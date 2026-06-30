import json
import mimetypes
import os
import tempfile
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import requests
from loguru import logger

from config import Config
from src.personalization import GeminiClient, outreach_personalizer

logger.add("logs/facebook_posting.log", rotation="500 MB")

CONTENT_STRATEGY_VERSION = 'creatifybd-autonomous-v4'
MIN_QUALITY_SCORE = 9
TEST_POST_UID = 'fb-autonomous-test-v2'


FACEBOOK_POST_HEADERS = [
    'uid',
    'strategy_version',
    'scheduled_date',
    'scheduled_time',
    'scheduled_at',
    'status',
    'approval_note',
    'quality_score',
    'audience_stage',
    'marketing_goal',
    'hook',
    'takeaway',
    'pillar',
    'format',
    'caption',
    'image_prompt',
    'image_status',
    'posted_at',
    'post_id',
    'replacement_for',
    'created_at',
    'updated_at',
]


class FacebookPoster:
    """Approval-gated social media manager for the CreatifyBD Facebook page."""

    APPROVED = 'Approved'
    DENIED = 'Denied'
    PENDING = 'Pending'
    POSTED = 'Posted'
    REPLACED = 'Replaced'
    FAILED = 'Failed'

    def __init__(self):
        self.page_id = Config.FACEBOOK_PAGE_ID
        self.access_token = Config.FACEBOOK_PAGE_ACCESS_TOKEN or Config.FACEBOOK_ACCESS_TOKEN
        self.api_version = "v25.0"
        social_keys = Config.GEMINI_API_KEYS[:max(1, Config.FACEBOOK_AI_MAX_KEYS_PER_BATCH)]
        self.gemini = GeminiClient(
            api_keys=social_keys,
            timeout=Config.FACEBOOK_AI_GENERATION_TIMEOUT_SECONDS
        )
        self.timezone = ZoneInfo(Config.SCHEDULER_TIMEZONE)
        self._last_facebook_error = ''

    def _now(self):
        return datetime.now(self.timezone)

    def _spreadsheet(self):
        try:
            from src.reporting import reporting_manager

            return reporting_manager.generator.spreadsheet
        except Exception as exc:
            logger.warning(f"Google Sheets unavailable for Facebook calendar: {exc}")
            return None

    def _worksheet(self):
        spreadsheet = self._spreadsheet()
        if not spreadsheet:
            return None

        title = Config.FACEBOOK_POSTS_WORKSHEET
        try:
            worksheet = spreadsheet.worksheet(title)
        except Exception:
            worksheet = spreadsheet.add_worksheet(title=title, rows=2000, cols=len(FACEBOOK_POST_HEADERS))
            worksheet.append_row(FACEBOOK_POST_HEADERS)

        values = worksheet.get_all_values()
        if not values:
            worksheet.append_row(FACEBOOK_POST_HEADERS)
        elif values[0] != FACEBOOK_POST_HEADERS:
            worksheet.clear()
            worksheet.append_row(FACEBOOK_POST_HEADERS)
        return worksheet

    def _rows(self, worksheet):
        records = worksheet.get_all_records()
        rows = []
        for index, record in enumerate(records, start=2):
            rows.append({'row_number': index, **record})
        return rows

    def ensure_content_calendar(self, horizon_days=None):
        """Keep a 30-day autonomous content calendar in Google Sheets."""
        worksheet = self._worksheet()
        if not worksheet:
            return {'created': 0, 'worksheet': None}

        horizon_days = horizon_days or Config.FACEBOOK_CONTENT_HORIZON_DAYS
        now = self._now()
        end_date = now.date() + timedelta(days=horizon_days - 1)
        existing_rows = self._rows(worksheet)
        if self._should_reset_calendar(existing_rows):
            preserved_rows = [
                self._row_values(row)
                for row in existing_rows
                if self._status(row) in {self.POSTED, self.FAILED, self.REPLACED}
            ]
            worksheet.clear()
            worksheet.append_row(FACEBOOK_POST_HEADERS)
            if preserved_rows:
                worksheet.append_rows(preserved_rows, value_input_option='USER_ENTERED')
            existing_rows = self._rows(worksheet)

        existing = {
            str(row.get('scheduled_at', '')).strip()
            for row in existing_rows
            if str(row.get('scheduled_at', '')).strip()
        }

        slots = [
            scheduled_at for scheduled_at in self._planned_slots(now.date(), end_date)
            if scheduled_at.isoformat(timespec='minutes') not in existing
        ]

        if not slots:
            return {'created': 0, 'worksheet': worksheet.title}

        created = 0
        rows_to_append = []
        batch_size = max(1, Config.FACEBOOK_CONTENT_BATCH_DAYS * Config.FACEBOOK_POSTS_PER_DAY)
        for start in range(0, len(slots), batch_size):
            batch = slots[start:start + batch_size]
            posts = self._generate_posts_for_slots(batch)
            for scheduled_at, post in zip(batch, posts):
                rows_to_append.append(self._sheet_row(scheduled_at, post))
                created += 1

        if rows_to_append:
            worksheet.append_rows(rows_to_append, value_input_option='USER_ENTERED')

        logger.info(f"Facebook content calendar updated: created={created}")
        return {'created': created, 'worksheet': worksheet.title}

    def calendar_status(self):
        """Return public-safe metadata about the connected Facebook calendar sheet."""
        worksheet = self._worksheet()
        if not worksheet:
            return {'connected': False, 'worksheet': None}

        values = worksheet.get_all_values()
        headers = values[0] if values else []
        rows = self._rows(worksheet)
        status_counts = {}
        version_counts = {}
        scheduled = []

        for row in rows:
            status = self._status(row)
            status_counts[status] = status_counts.get(status, 0) + 1
            version = str(row.get('strategy_version') or 'legacy').strip() or 'legacy'
            version_counts[version] = version_counts.get(version, 0) + 1
            scheduled_at = str(row.get('scheduled_at') or '').strip()
            if scheduled_at:
                scheduled.append(scheduled_at)

        return {
            'connected': True,
            'worksheet': worksheet.title,
            'row_count': len(rows),
            'header_count': len(headers),
            'headers': headers,
            'status_counts': status_counts,
            'strategy_version_counts': version_counts,
            'autonomous_mode': Config.FACEBOOK_AUTONOMOUS_MODE,
            'approval_required': Config.FACEBOOK_REQUIRE_APPROVAL and not Config.FACEBOOK_AUTONOMOUS_MODE,
            'first_scheduled_at': min(scheduled) if scheduled else None,
            'last_scheduled_at': max(scheduled) if scheduled else None,
            'failed_rows': [
                {
                    'uid': str(row.get('uid') or ''),
                    'approval_note': str(row.get('approval_note') or '')[:260],
                    'image_status': str(row.get('image_status') or '')[:120],
                    'post_id': str(row.get('post_id') or ''),
                }
                for row in rows
                if self._status(row) == self.FAILED
            ][:5],
        }

    def post_next_approved(self):
        """Publish the next due post, approval-gated or autonomous depending on config."""
        worksheet = self._worksheet()
        if not worksheet:
            return False

        self.ensure_content_calendar()
        rows = self._rows(worksheet)
        now = self._now()

        candidate = self._first_publishable_due(rows, now)
        replacement_for = ''
        if not Config.FACEBOOK_AUTONOMOUS_MODE and not candidate and self._has_blocked_due_slot(rows, now):
            candidate = self._first_future_approved(rows, now)
            if candidate:
                replacement_for = self._blocked_due_uid(rows, now)

        if not candidate:
            logger.info("No approved Facebook post is ready to publish")
            return False

        return self._publish_sheet_row(worksheet, candidate, replacement_for=replacement_for)

    def post_autonomous_test_once(self):
        """Publish one owner-approved test post and record it in the calendar sheet."""
        worksheet = self._worksheet()
        if not worksheet:
            return {'posted': False, 'reason': 'sheet unavailable'}

        rows = self._rows(worksheet)
        existing = next((row for row in rows if str(row.get('uid') or '') == TEST_POST_UID), None)
        if existing:
            if self._status(existing) == self.FAILED:
                success = self._publish_sheet_row(worksheet, existing)
                return {'posted': bool(success), 'uid': TEST_POST_UID, 'status': self._status(existing)}
            return {'posted': False, 'reason': 'test already recorded', 'status': self._status(existing)}

        now = self._now()
        post = self._test_post()
        row_values = self._sheet_row(now, post)
        row_values[0] = TEST_POST_UID
        row_values[4] = now.isoformat(timespec='minutes')
        row_values[5] = self.APPROVED if not Config.FACEBOOK_AUTONOMOUS_MODE else self.PENDING
        worksheet.append_row(row_values, value_input_option='USER_ENTERED')
        row = {'row_number': len(self._rows(worksheet)) + 1}
        row.update(dict(zip(FACEBOOK_POST_HEADERS, row_values)))
        success = self._publish_sheet_row(worksheet, row)
        return {'posted': bool(success), 'uid': TEST_POST_UID}

    def post_daily_content(self):
        """Backward-compatible entrypoint used by older scheduler/manual triggers."""
        return self.post_next_approved()

    def post_to_facebook(self, content_text, image_url=None, image_bytes=None, mime_type='image/jpeg'):
        """Post text or an image+caption to Facebook Graph API; queue locally if credentials are missing."""
        try:
            if not self.page_id or not self.access_token:
                post_id = self._queue_post(content_text, image_url=image_url, image_bytes=image_bytes)
                logger.warning(f"Facebook credential missing; queued post {post_id}")
                return True, post_id

            if image_bytes:
                success, post_id = self._post_photo(content_text, image_bytes, mime_type)
                if success:
                    return success, post_id
                logger.warning("Facebook photo post failed; retrying as text-only feed post")

            url = f"https://graph.facebook.com/{self.api_version}/{self.page_id}/feed"
            payload = {'message': content_text, 'access_token': self.access_token}
            if image_url:
                payload['link'] = image_url

            response = requests.post(url, data=payload, timeout=20)
            if response.status_code >= 400:
                self._last_facebook_error = self._safe_error(response.status_code, response.text)
                logger.error(f"Facebook error: {self._last_facebook_error}")
                return False, None
            result = response.json()
            self._last_facebook_error = ''
            return True, result.get('id')
        except Exception as exc:
            self._last_facebook_error = f'{type(exc).__name__}: {str(exc)[:160]}'
            logger.error(f"Facebook posting error: {exc}")
            return False, None

    def _post_photo(self, caption, image_bytes, mime_type):
        url = f"https://graph.facebook.com/{self.api_version}/{self.page_id}/photos"
        ext = mimetypes.guess_extension(mime_type) or '.jpg'
        files = {'source': (f'creatifybd-social{ext}', image_bytes, mime_type)}
        data = {'caption': caption, 'access_token': self.access_token}
        response = requests.post(url, data=data, files=files, timeout=60)
        if response.status_code >= 400:
            self._last_facebook_error = self._safe_error(response.status_code, response.text)
            logger.error(f"Facebook photo error: {self._last_facebook_error}")
            return False, None
        result = response.json()
        self._last_facebook_error = ''
        return True, result.get('post_id') or result.get('id')

    @staticmethod
    def _safe_error(status_code, text):
        cleaned = str(text or '').replace('\n', ' ')[:260]
        return f'HTTP {status_code}: {cleaned}'

    def _publish_sheet_row(self, worksheet, row, replacement_for=''):
        caption = str(row.get('caption') or '').strip()
        if not caption:
            self._update_row_status(worksheet, row['row_number'], self.FAILED, 'Blank caption')
            return False

        image_bytes = None
        mime_type = 'image/jpeg'
        image_prompt = str(row.get('image_prompt') or '').strip()
        image_status = 'not_requested'
        if Config.FACEBOOK_GENERATE_IMAGES and image_prompt:
            try:
                image_bytes, mime_type = self.gemini.generate_image(self._image_prompt(image_prompt))
                image_status = 'generated'
            except Exception as exc:
                image_status = f'failed: {type(exc).__name__}'
                logger.warning(f"Facebook image generation skipped: {exc}")

        success, post_id = self.post_to_facebook(
            caption,
            image_bytes=image_bytes,
            mime_type=mime_type
        )
        now = self._now().isoformat(timespec='seconds')
        if success:
            updates = {
                'status': self.POSTED,
                'posted_at': now,
                'post_id': post_id or '',
                'replacement_for': replacement_for,
                'image_status': image_status,
                'updated_at': now,
            }
            self._update_row(worksheet, row['row_number'], updates)
            return True

        self._update_row_status(
            worksheet,
            row['row_number'],
            self.FAILED,
            self._last_facebook_error or 'Facebook API failed'
        )
        return False

    def _generate_posts_for_slots(self, slots):
        if outreach_personalizer.enabled:
            try:
                result = self.gemini.generate_json(self._calendar_prompt(slots), max_output_tokens=12000)
                posts = result.get('posts') or []
                cleaned = [self._clean_generated_post(post) for post in posts]
                if len(cleaned) >= len(slots):
                    for index, post in enumerate(cleaned[:len(slots)]):
                        if int(post.get('quality_score') or 0) < MIN_QUALITY_SCORE:
                            cleaned[index] = self._fallback_post(slots[index], index)
                    return cleaned[:len(slots)]
            except Exception as exc:
                logger.warning(f"AI Facebook calendar fallback: {exc}")

        return [self._fallback_post(slot, index) for index, slot in enumerate(slots)]

    def _calendar_prompt(self, slots):
        slot_lines = '\n'.join(
            f"- {slot.isoformat(timespec='minutes')} ({slot.strftime('%A')})"
            for slot in slots
        )
        schema = {
            'posts': [
                {
                    'audience_stage': 'unaware | problem_aware | solution_aware | comparison | ready',
                    'marketing_goal': 'educate | trust | demand_creation | engagement | lead_intent',
                    'pillar': 'website_conversion | SEO | social_growth | paid_ads | branding | client_hunting | trend | meme | founder_pov',
                    'format': 'text | meme | carousel_idea | short_story | checklist | audit_prompt',
                    'hook': 'specific opening idea, not clickbait',
                    'takeaway': 'the practical lesson the audience can apply',
                    'caption': '130-220 words, human, insightful, useful, no fake claims',
                    'image_prompt': '90-140 word premium art direction prompt, no text/logos',
                    'quality_score': '1-10'
                }
            ]
        }
        return (
            f'Agency: {Config.AGENCY_NAME}\n'
            f'Website: {Config.AGENCY_WEBSITE}\n'
            f'Services: {Config.AGENCY_SERVICES}\n'
            'Audience: founders, local business owners, service businesses, ecommerce owners, '
            'and growing Bangladeshi/English-speaking SMEs who need better websites, SEO, '
            'content, social media, branding, and paid acquisition.\n\n'
            'Role: act as a top-tier creative agency team in one person: brand strategist, '
            'SEO strategist, performance marketer, social media manager, creative director, '
            'copy chief, and client hunter. You are not making filler posts. You are building '
            'trust, authority, and organic demand for CreatifyBD before the website launch is complete.\n\n'
            'Brand direction: smart, practical, premium, founder-led, quietly confident. '
            'Never sound like a generic AI marketing page. Write like a strategist who has '
            'sat with real business owners and understands conversion friction, buyer psychology, '
            'local trust, content distribution, SEO intent, creative testing, and follow-up systems.\n\n'
            'Strategic content pillars:\n'
            '1. Website conversion audits: clarity, trust, mobile speed, CTA, offer-match.\n'
            '2. SEO and helpful content: customer questions, service pages, local intent, proof.\n'
            '3. Organic social growth: audience problems, useful lessons, shareable POV, comments.\n'
            '4. Paid ads readiness: landing page, tracking, offer, follow-up, creative testing.\n'
            '5. Branding and creative direction: consistency, positioning, visual trust.\n'
            '6. Client-hunting education: how businesses can attract better-fit clients.\n'
            '7. Tasteful trend/meme posts: World Cup/teamwork/tactics analogies are allowed, '
            'but no team logos, no match scores, no fake breaking news, no cheap jokes.\n\n'
            'Quality bar for every post:\n'
            '- Open with a concrete, non-generic hook that names a real business tension.\n'
            '- Teach one useful idea with depth; avoid obvious advice like "post consistently".\n'
            '- Include a practical mental model, mini-framework, mistake, or audit prompt.\n'
            '- Sound like a smart human founder/strategist, not an AI assistant or motivational page.\n'
            '- No fabricated results, case studies, clients, awards, numbers, or guarantees.\n'
            '- No hard selling while creatifybd.com is unfinished. Soft CTA only.\n'
            '- Use polished Bangla-English only when natural; otherwise use crisp English.\n'
            '- End with a comment-worthy question or low-friction reflection.\n'
            '- Score your own post. Only output posts that deserve 9/10 or higher.\n\n'
            'Image prompt quality bar:\n'
            '- Describe a premium designer/photographer-level square visual, not a generic stock image.\n'
            '- Mention exact scene, subject, composition, camera/lighting style, material details, mood, '
            'color palette, and why it matches the post idea.\n'
            '- Prefer realistic editorial photography, high-end 3D editorial, or tasteful campaign art.\n'
            '- No readable words, no letters, no numbers, no UI text, no logos, no distorted charts, no clutter.\n'
            '- Avoid cheap clipart, flat generic icons, random laptops, fake brand marks, and messy small objects.\n\n'
            f'Slots:\n{slot_lines}\n\n'
            f'Return valid JSON exactly like this shape: {json.dumps(schema)}'
        )

    def _clean_generated_post(self, post):
        cleaned = {
            'audience_stage': str(post.get('audience_stage') or 'problem_aware').strip()[:80],
            'marketing_goal': str(post.get('marketing_goal') or 'educate').strip()[:80],
            'pillar': str(post.get('pillar') or 'education').strip()[:80],
            'format': str(post.get('format') or 'text').strip()[:80],
            'hook': str(post.get('hook') or '').strip()[:220],
            'takeaway': str(post.get('takeaway') or '').strip()[:260],
            'caption': str(post.get('caption') or '').strip()[:2200],
            'image_prompt': str(post.get('image_prompt') or '').strip()[:1400],
        }
        cleaned['quality_score'] = max(
            self._quality_score(cleaned),
            self._coerce_score(post.get('quality_score'))
        )
        return cleaned

    def _should_reset_calendar(self, rows):
        return any(
            self._status(row) in {self.PENDING, self.APPROVED, self.DENIED}
            and str(row.get('strategy_version') or '') != CONTENT_STRATEGY_VERSION
            for row in rows
        )

    def _planned_slots(self, start_date, end_date):
        slots = []
        current = start_date
        while current <= end_date:
            for post_time in self._autonomous_post_times(current):
                slots.append(self._combine(current, post_time))
            current += timedelta(days=1)
        return slots

    def _autonomous_post_times(self, day):
        if not Config.FACEBOOK_AUTONOMOUS_MODE:
            return Config.FACEBOOK_POST_TIMES[:Config.FACEBOOK_POSTS_PER_DAY]

        # Owner-level rhythm: publish more on high-attention weekdays, stay lighter
        # on Friday/weekends, and keep posts away from cramped back-to-back windows.
        weekday = day.weekday()
        if weekday in (1, 3):  # Tuesday, Thursday
            return ['09:40', '13:15', '17:40', '21:05']
        if weekday in (0, 2):  # Monday, Wednesday
            return ['10:10', '15:10', '20:35']
        if weekday == 4:  # Friday
            return ['11:00', '20:45']
        if weekday == 5:  # Saturday
            return ['12:15', '19:45']
        return ['10:45', '16:30', '21:00']

    def _test_post(self):
        caption = (
            "A strong digital presence does not start with posting more. It starts with making "
            "the business easier to understand, trust, and contact.\n\n"
            "Before any campaign, look at the full path: what the customer sees first, what they "
            "believe, what question stays unanswered, and how easy the next step feels on mobile.\n\n"
            "CreatifyBD is being built around that kind of practical growth thinking: websites, "
            "SEO, content, branding, social media, and paid acquisition working as one system.\n\n"
            "If you had to improve only one part of your digital presence this week, what would it be?"
        )
        return {
            'audience_stage': 'problem_aware',
            'marketing_goal': 'trust',
            'pillar': 'founder_pov',
            'format': 'text',
            'hook': 'A strong digital presence does not start with posting more',
            'takeaway': 'Fix clarity, trust, and next-step friction before scaling promotion.',
            'caption': caption,
            'image_prompt': '',
            'quality_score': 9,
        }

    @staticmethod
    def _coerce_score(value):
        try:
            return max(0, min(10, int(float(value))))
        except (TypeError, ValueError):
            return 0

    def _quality_score(self, post):
        caption = str(post.get('caption') or '')
        lower = caption.lower()
        score = 0
        if len(caption.split()) >= 95:
            score += 2
        if any(mark in caption for mark in ('?', 'Why', 'How', 'Before', 'If ')):
            score += 1
        if any(word in lower for word in ('website', 'seo', 'content', 'brand', 'ads', 'landing page', 'customer', 'trust')):
            score += 2
        if any(word in lower for word in ('check', 'review', 'ask', 'start', 'before', 'track', 'rewrite', 'remove')):
            score += 2
        if str(post.get('hook') or '').strip():
            score += 1
        if str(post.get('takeaway') or '').strip():
            score += 1
        if any(bad in lower for bad in ('guaranteed', 'best agency', 'limited offer', 'buy now', 'we are the #1')):
            score -= 3
        return max(0, min(10, score))

    def _fallback_post(self, slot, index):
        ideas = [
            (
                'education',
                'checklist',
                'A website should answer three questions fast: what you do, who it is for, and what the visitor should do next. If those answers are hidden behind pretty design, conversion drops. Before spending more on ads, review your homepage like a first-time visitor. Is the offer clear above the fold? Is there proof? Is the enquiry path simple on mobile? Small clarity fixes often create more value than another campaign. What is the one thing your homepage should make obvious in five seconds?',
                'Clean square editorial image of a business owner reviewing a website checklist on a laptop, modern workspace, natural light, no logos, no small text.'
            ),
            (
                'trend',
                'meme',
                'World Cup season is a useful reminder for marketing: talent matters, but systems win. A good campaign is not only a creative post. It is positioning, content, landing page, follow-up, and measurement working together. If one part is weak, the whole move breaks down. Before blaming reach or budget, check the full play: message, audience, page speed, trust signals, and response time. Which part of your marketing system needs better teamwork right now?',
                'Tasteful football-tactics inspired marketing visual, strategy board with website, content, ads, and follow-up icons, professional editorial style, no team logos, no text.'
            ),
            (
                'owner_pov',
                'short_story',
                'Many businesses do not need louder marketing first. They need clearer marketing. Clear service pages, useful posts, visible proof, and fast follow-up can make the same audience respond differently. Growth often starts when a visitor stops guessing and starts trusting. This week, pick one service you sell and rewrite the page from the customer question: "Why should I choose you now?" What would you remove to make that answer clearer?',
                'Square editorial illustration of a founder simplifying a messy marketing board into a clear customer journey, premium agency style, no text.'
            ),
            (
                'checklist',
                'checklist',
                'A good service page is not a brochure. It is a decision helper. Start with the problem, show the outcome, explain the process, add trust signals, then make the next step easy. If visitors need to message you just to understand what you offer, the page is doing extra damage quietly. Pick one service today and check: does the page answer price range, timeline, proof, and next step clearly enough?',
                'Premium square editorial visual of a service page audit checklist beside a laptop and coffee, muted agency colors, clean composition, no readable text.'
            ),
            (
                'seo',
                'education',
                'SEO is not only keywords. For local and service businesses, it is clarity plus consistency: clear service pages, useful answers, fast loading, structured information, and proof that real people can trust. The easiest place to start is not a technical hack. Write down the five questions customers ask before buying, then turn each one into a useful page or post. Which customer question have you not answered publicly yet?',
                'Square editorial image of search results, customer questions, and content blocks arranged on a strategy desk, modern professional style, no text.'
            ),
            (
                'social',
                'owner_pov',
                'Most business pages post when they have something to announce. Strong pages post when the audience has something to learn, decide, compare, or feel understood about. That shift changes everything. Instead of asking "What should we post today?", ask "What is our customer trying to figure out this week?" Good content starts there. What is one confusion your customers often have before they contact you?',
                'Clean square visual of a social media calendar transforming from random posts into customer questions and useful content, no text, editorial style.'
            ),
            (
                'branding',
                'short_story',
                'Branding is not just a logo. It is the feeling of consistency across every touchpoint: your website, page posts, replies, offers, visuals, and follow-up. When those pieces feel disconnected, customers hesitate even if the service is good. The fix often starts small: one clear message, one visual direction, one consistent tone. If someone saw your website and Facebook page side by side, would they feel the same brand?',
                'Square editorial brand board with website, social post, color swatches, and customer message cards, refined agency aesthetic, no logos.'
            ),
            (
                'conversion',
                'education',
                'Traffic is expensive. Confusion is more expensive. If people visit your website but do not enquire, check the basics before blaming ads: mobile speed, headline clarity, proof, offer match, and a simple contact path. A beautiful page that makes people think too hard is still leaking opportunity. What is the one action your website should make easiest for a serious buyer?',
                'Modern square visual showing a leaky funnel being repaired with clarity, proof, speed, and CTA elements, professional and minimal, no text.'
            ),
            (
                'trend',
                'meme',
                'World Cup tactics and marketing have one thing in common: random energy rarely beats a clear system. A post can get attention, but the full play matters: who you target, what you promise, where you send them, and how fast you follow up. Before chasing the next trend, check your formation. Are your website, content, ads, and inbox working as one team?',
                'Tasteful football strategy board blended with marketing channels, website and content icons, no team branding, premium square editorial look.'
            ),
            (
                'meme',
                'meme',
                'That moment when a business boosts a post, gets clicks, and then sends people to a page that does not explain the offer clearly. The ad did its job. The landing page did not. Growth is usually a chain, not a single action. Before spending more, strengthen the weakest link. Is your landing page ready for the attention you are paying for?',
                'Smart business meme-style square image: ad clicks flowing toward a messy landing page being organized, tasteful humor, no captions or small text.'
            ),
            (
                'paid_ads',
                'checklist',
                'Before running ads, ask five questions: Is the offer specific? Is the audience narrow enough? Does the landing page match the promise? Is there proof? Who follows up and how quickly? Skipping these makes ad spend look like the problem when the real issue is the system around it. Paid ads can scale clarity. They cannot rescue confusion.',
                'Square editorial visual of a paid ads launch checklist with campaign, landing page, proof, and follow-up cards, clean professional style, no text.'
            ),
            (
                'content',
                'education',
                'Helpful content does not need to be complicated. Teach one useful thing. Share one mistake to avoid. Explain one decision. Show one behind-the-scenes process. The goal is not to sound big. The goal is to become easier to trust before the customer speaks to you. What is one small lesson from your work that your audience would genuinely benefit from?',
                'Warm square editorial image of a founder turning expertise into simple content cards, modern workspace, natural light, no readable text.'
            ),
            (
                'website',
                'owner_pov',
                'Your website is often your first salesperson. It works silently, all day, for people who may never message if the first impression feels unclear. That is why copy, structure, speed, and trust matter as much as design. A strong site does not just look professional. It reduces hesitation. What would a first-time visitor trust more after spending 30 seconds on your homepage?',
                'Square visual of a website acting like a calm professional salesperson, laptop with structured page sections, polished editorial style, no text.'
            ),
            (
                'analytics',
                'checklist',
                'Marketing without measurement becomes guesswork. You do not need a complex dashboard to start. Track where leads come from, which posts create conversations, which pages get visits, and how many enquiries become real opportunities. Once you see the pattern, better decisions become easier. What is one number your business should check every week but currently ignores?',
                'Minimal square analytics desk scene with simple charts, leads, content, and enquiry cards, sophisticated agency visual, no small text.'
            ),
        ]
        day_offset = (slot.date() - date.today()).days
        pillar, format_name, caption, image_prompt = ideas[(index + day_offset) % len(ideas)]
        first_sentence = caption.split('.')[0].strip()
        return {
            'audience_stage': self._fallback_stage(index + day_offset),
            'marketing_goal': self._fallback_goal(pillar),
            'pillar': pillar,
            'format': format_name,
            'hook': first_sentence,
            'takeaway': self._fallback_takeaway(pillar),
            'caption': caption,
            'image_prompt': image_prompt,
            'quality_score': max(MIN_QUALITY_SCORE, self._quality_score({
                'caption': caption,
                'hook': first_sentence,
                'takeaway': self._fallback_takeaway(pillar),
            })),
        }

    @staticmethod
    def _fallback_stage(index):
        stages = ['problem_aware', 'solution_aware', 'unaware', 'comparison', 'ready']
        return stages[index % len(stages)]

    @staticmethod
    def _fallback_goal(pillar):
        if pillar in {'meme', 'trend'}:
            return 'engagement'
        if pillar in {'paid_ads', 'conversion', 'website'}:
            return 'lead_intent'
        if pillar in {'branding', 'analytics'}:
            return 'trust'
        return 'educate'

    @staticmethod
    def _fallback_takeaway(pillar):
        takeaways = {
            'education': 'Make the offer, audience, and next step clear before spending more on promotion.',
            'trend': 'Use timely analogies to teach a real business lesson without lowering brand taste.',
            'owner_pov': 'Clarity and trust usually create more growth than louder posting.',
            'checklist': 'Turn vague marketing problems into a quick owner-level audit.',
            'seo': 'Answer real buyer questions publicly before expecting search traffic to convert.',
            'social': 'Build posts around customer decisions, not only company announcements.',
            'branding': 'Consistency across touchpoints reduces hesitation.',
            'conversion': 'Fix the customer journey before blaming traffic quality.',
            'meme': 'Use humor to reveal a real marketing mistake.',
            'paid_ads': 'Ads scale a working system; they do not fix a confusing offer.',
            'content': 'Useful content makes trust easier before a sales conversation.',
            'website': 'A website should reduce hesitation, not just look good.',
            'analytics': 'Simple weekly metrics make marketing less dependent on guesswork.',
        }
        return takeaways.get(pillar, 'Teach one useful, specific marketing idea.')

    def _sheet_row(self, scheduled_at, post):
        now = self._now().isoformat(timespec='seconds')
        uid = f"fb-{scheduled_at.strftime('%Y%m%d-%H%M')}"
        return [
            uid,
            CONTENT_STRATEGY_VERSION,
            scheduled_at.date().isoformat(),
            scheduled_at.strftime('%H:%M'),
            scheduled_at.isoformat(timespec='minutes'),
            self.PENDING,
            '',
            post.get('quality_score', ''),
            post.get('audience_stage', ''),
            post.get('marketing_goal', ''),
            post.get('hook', ''),
            post.get('takeaway', ''),
            post['pillar'],
            post['format'],
            post['caption'],
            post['image_prompt'],
            '',
            '',
            '',
            '',
            now,
            now,
        ]

    @staticmethod
    def _row_values(row):
        return [row.get(header, '') for header in FACEBOOK_POST_HEADERS]

    def _image_prompt(self, prompt):
        return (
            f"{prompt}\n\n"
            "Create a premium square Facebook visual for CreatifyBD's business audience. "
            "The result must look like it was art-directed by a senior creative director, "
            "professional designer, or commercial photographer, not a quick AI image. "
            "Use a clean, intentional composition with strong focal hierarchy, realistic material detail, "
            "controlled lighting, refined color palette, and enough negative space for a social feed crop. "
            "Make the image directly relevant to the marketing lesson, using visual metaphor only when it is clear. "
            "No readable text, no letters, no numbers, no fake logos, no copyrighted marks, no tiny UI copy, "
            "no distorted charts, no clutter, no cheap stock-photo smiles, no generic clipart. "
            "Prefer premium editorial photography or high-end campaign-style 3D editorial."
        )

    def _combine(self, day, hhmm):
        try:
            hour, minute = [int(part) for part in str(hhmm).split(':', 1)]
        except ValueError:
            hour, minute = 14, 0
        return datetime.combine(day, time(hour, minute), tzinfo=self.timezone)

    def _parse_scheduled_at(self, row):
        raw = str(row.get('scheduled_at') or '').strip()
        if raw:
            try:
                value = datetime.fromisoformat(raw)
                return value if value.tzinfo else value.replace(tzinfo=self.timezone)
            except ValueError:
                pass
        try:
            day = date.fromisoformat(str(row.get('scheduled_date')).strip())
            return self._combine(day, str(row.get('scheduled_time') or '14:00'))
        except ValueError:
            return self._now() + timedelta(days=3650)

    def _first_approved_due(self, rows, now):
        candidates = [
            row for row in rows
            if self._status(row) == self.APPROVED and self._parse_scheduled_at(row) <= now
        ]
        return sorted(candidates, key=self._parse_scheduled_at)[0] if candidates else None

    def _first_publishable_due(self, rows, now):
        if not Config.FACEBOOK_AUTONOMOUS_MODE:
            return self._first_approved_due(rows, now)
        candidates = [
            row for row in rows
            if self._status(row) in {self.PENDING, self.APPROVED}
            and self._parse_scheduled_at(row) <= now
            and int(row.get('quality_score') or 0) >= MIN_QUALITY_SCORE
        ]
        return sorted(candidates, key=self._parse_scheduled_at)[0] if candidates else None

    def _first_future_approved(self, rows, now):
        candidates = [
            row for row in rows
            if self._status(row) == self.APPROVED and self._parse_scheduled_at(row) > now
        ]
        return sorted(candidates, key=self._parse_scheduled_at)[0] if candidates else None

    def _has_blocked_due_slot(self, rows, now):
        blocked = {self.PENDING, self.DENIED}
        return any(self._status(row) in blocked and self._parse_scheduled_at(row) <= now for row in rows)

    def _blocked_due_uid(self, rows, now):
        blocked = [
            row for row in rows
            if self._status(row) in {self.PENDING, self.DENIED} and self._parse_scheduled_at(row) <= now
        ]
        if not blocked:
            return ''
        return str(sorted(blocked, key=self._parse_scheduled_at)[0].get('uid') or '')

    def _status(self, row):
        status = str(row.get('status') or '').strip().lower()
        mapping = {
            'approved': self.APPROVED,
            'approve': self.APPROVED,
            'yes': self.APPROVED,
            'y': self.APPROVED,
            'denied': self.DENIED,
            'deny': self.DENIED,
            'rejected': self.DENIED,
            'no': self.DENIED,
            'n': self.DENIED,
            'posted': self.POSTED,
            'done': self.POSTED,
            'failed': self.FAILED,
            'replaced': self.REPLACED,
            'pending': self.PENDING,
            '': self.PENDING,
        }
        return mapping.get(status, self.PENDING)

    def _update_row_status(self, worksheet, row_number, status, note=''):
        updates = {'status': status, 'approval_note': note, 'updated_at': self._now().isoformat(timespec='seconds')}
        self._update_row(worksheet, row_number, updates)

    def _update_row(self, worksheet, row_number, updates):
        values = []
        for header, value in updates.items():
            if header not in FACEBOOK_POST_HEADERS:
                continue
            col = FACEBOOK_POST_HEADERS.index(header) + 1
            values.append({'range': f'{self._col(col)}{row_number}', 'values': [[value]]})
        if values:
            worksheet.batch_update(values, value_input_option='USER_ENTERED')

    @staticmethod
    def _col(index):
        result = ''
        while index:
            index, remainder = divmod(index - 1, 26)
            result = chr(65 + remainder) + result
        return result

    def _queue_post(self, content_text, image_url=None, image_bytes=None):
        os.makedirs('reports', exist_ok=True)
        outbox_file = 'reports/facebook_post_outbox.json'
        post_id = f"queued-facebook-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        queued = []

        if os.path.exists(outbox_file):
            try:
                with open(outbox_file, 'r', encoding='utf-8') as f:
                    queued = json.load(f)
            except Exception:
                queued = []

        image_path = None
        if image_bytes:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg', prefix='creatifybd-', dir='reports') as tmp:
                tmp.write(image_bytes)
                image_path = tmp.name

        queued.append({
            'id': post_id,
            'content': content_text,
            'image_url': image_url,
            'image_path': image_path,
            'created_at': datetime.utcnow().isoformat()
        })

        with open(outbox_file, 'w', encoding='utf-8') as f:
            json.dump(queued, f, ensure_ascii=False, indent=2)

        return post_id


facebook_poster = FacebookPoster()


if __name__ == '__main__':
    poster = FacebookPoster()
    poster.ensure_content_calendar()
