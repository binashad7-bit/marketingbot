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


FACEBOOK_POST_HEADERS = [
    'uid',
    'scheduled_date',
    'scheduled_time',
    'scheduled_at',
    'status',
    'approval_note',
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
        self.gemini = GeminiClient()
        self.timezone = ZoneInfo(Config.SCHEDULER_TIMEZONE)

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
        """Keep a 30-day, three-post-per-day approval calendar in Google Sheets."""
        worksheet = self._worksheet()
        if not worksheet:
            return {'created': 0, 'worksheet': None}

        horizon_days = horizon_days or Config.FACEBOOK_CONTENT_HORIZON_DAYS
        now = self._now()
        end_date = now.date() + timedelta(days=horizon_days - 1)
        existing = {
            str(row.get('scheduled_at', '')).strip()
            for row in self._rows(worksheet)
            if str(row.get('scheduled_at', '')).strip()
        }

        slots = []
        current = now.date()
        while current <= end_date:
            for post_time in Config.FACEBOOK_POST_TIMES[:Config.FACEBOOK_POSTS_PER_DAY]:
                scheduled_at = self._combine(current, post_time)
                if scheduled_at.isoformat(timespec='minutes') not in existing:
                    slots.append(scheduled_at)
            current += timedelta(days=1)

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

    def post_next_approved(self):
        """Publish the next approved due post, pulling forward an approved replacement if needed."""
        worksheet = self._worksheet()
        if not worksheet:
            return False

        self.ensure_content_calendar()
        rows = self._rows(worksheet)
        now = self._now()

        candidate = self._first_approved_due(rows, now)
        replacement_for = ''
        if not candidate and self._has_blocked_due_slot(rows, now):
            candidate = self._first_future_approved(rows, now)
            if candidate:
                replacement_for = self._blocked_due_uid(rows, now)

        if not candidate:
            logger.info("No approved Facebook post is ready to publish")
            return False

        return self._publish_sheet_row(worksheet, candidate, replacement_for=replacement_for)

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
                return self._post_photo(content_text, image_bytes, mime_type)

            url = f"https://graph.facebook.com/{self.api_version}/{self.page_id}/feed"
            payload = {'message': content_text, 'access_token': self.access_token}
            if image_url:
                payload['link'] = image_url

            response = requests.post(url, data=payload, timeout=20)
            if response.status_code >= 400:
                logger.error(f"Facebook error: {response.status_code} - {response.text}")
                return False, None
            result = response.json()
            return True, result.get('id')
        except Exception as exc:
            logger.error(f"Facebook posting error: {exc}")
            return False, None

    def _post_photo(self, caption, image_bytes, mime_type):
        url = f"https://graph.facebook.com/{self.api_version}/{self.page_id}/photos"
        ext = mimetypes.guess_extension(mime_type) or '.jpg'
        files = {'source': (f'creatifybd-social{ext}', image_bytes, mime_type)}
        data = {'caption': caption, 'access_token': self.access_token}
        response = requests.post(url, data=data, files=files, timeout=60)
        if response.status_code >= 400:
            logger.error(f"Facebook photo error: {response.status_code} - {response.text}")
            return False, None
        result = response.json()
        return True, result.get('post_id') or result.get('id')

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

        self._update_row_status(worksheet, row['row_number'], self.FAILED, 'Facebook API failed')
        return False

    def _generate_posts_for_slots(self, slots):
        if outreach_personalizer.enabled:
            try:
                result = self.gemini.generate_json(self._calendar_prompt(slots), max_output_tokens=12000)
                posts = result.get('posts') or []
                cleaned = [self._clean_generated_post(post) for post in posts]
                if len(cleaned) >= len(slots):
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
                    'pillar': 'education | owner_pov | checklist | trend | meme | case_style',
                    'format': 'text | meme | carousel_idea | short_story | checklist',
                    'caption': '90-170 words, human, useful, no fake claims',
                    'image_prompt': 'square editorial visual prompt, no small text, no logos'
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
            'Create a one-month-style Facebook content batch for a serious agency page. '
            'The page must feel like a thoughtful human social media manager, not generic AI. '
            'Every post must teach something useful, spark comments, or make the audience feel '
            'seen. Keep a balanced mix: practical marketing education, website conversion tips, '
            'SEO/social lessons, founder POV, soft memes, and trend-aware posts. It is World Cup '
            'season, so some trend/meme posts may use football/teamwork/tactics analogies, but '
            'do not mention specific match results, teams, scores, or breaking news. Maintain '
            'business-standard taste: smart, warm, no cheap jokes, no fake case studies, no '
            'fabricated results, no hard selling, no exaggerated claims, no emojis-heavy style. '
            'Use natural Bangla-English where it helps the Bangladeshi audience, otherwise clean '
            'professional English. End most posts with a thoughtful question or soft CTA.\n\n'
            f'Slots:\n{slot_lines}\n\n'
            f'Return valid JSON exactly like this shape: {json.dumps(schema)}'
        )

    def _clean_generated_post(self, post):
        return {
            'pillar': str(post.get('pillar') or 'education').strip()[:80],
            'format': str(post.get('format') or 'text').strip()[:80],
            'caption': str(post.get('caption') or '').strip()[:2200],
            'image_prompt': str(post.get('image_prompt') or '').strip()[:1400],
        }

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
        ]
        pillar, format_name, caption, image_prompt = ideas[index % len(ideas)]
        return {
            'pillar': pillar,
            'format': format_name,
            'caption': caption,
            'image_prompt': image_prompt,
        }

    def _sheet_row(self, scheduled_at, post):
        now = self._now().isoformat(timespec='seconds')
        uid = f"fb-{scheduled_at.strftime('%Y%m%d-%H%M')}"
        return [
            uid,
            scheduled_at.date().isoformat(),
            scheduled_at.strftime('%H:%M'),
            scheduled_at.isoformat(timespec='minutes'),
            self.PENDING,
            '',
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

    def _image_prompt(self, prompt):
        return (
            f"{prompt}\n\n"
            "Create a polished square Facebook visual for CreatifyBD's business audience. "
            "No tiny text, no fake logos, no copyrighted team marks, no clutter. "
            "Use a professional agency editorial style that can accompany a marketing lesson."
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
