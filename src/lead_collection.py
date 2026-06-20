import random
import re
import time
from datetime import datetime, timedelta
from html import unescape
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from loguru import logger
from sqlalchemy import or_

from config import Config
from src.database import (
    Lead,
    add_lead,
    can_use_api,
    db,
    ensure_search_tasks,
    get_next_search_tasks,
    mark_search_task_result,
    normalize_bd_phone,
    record_api_usage,
    refresh_lead_contact_fields,
)

logger.add("logs/lead_collection.log", rotation="500 MB")


class LeadCollector:
    """Autonomous lead collection and enrichment for Bangladesh institutes."""

    CLOSED_STATUSES = {'CLOSED_PERMANENTLY', 'CLOSED_TEMPORARILY'}
    CLOSED_NAME_PATTERN = re.compile(
        r'\b(permanently\s+closed|temporarily\s+closed|closed)\b|বন্ধ',
        re.IGNORECASE
    )

    def __init__(self):
        self.google_maps_api_key = Config.GOOGLE_MAPS_API_KEY
        self.hunter_api_key = Config.HUNTER_API_KEY

    def run_autonomous_cycle(self):
        """Run one safe, repeatable lead-only cycle."""
        logger.info("=" * 50)
        logger.info("Autonomous lead generation cycle started")
        logger.info("=" * 50)

        maps_count = self.collect_from_google_maps()
        contact_updates = self.enrich_missing_contact_info(
            limit=Config.CONTACT_ENRICH_LIMIT,
            find_email=False
        )
        email_updates = self.enrich_missing_emails(limit=Config.EMAIL_ENRICH_LIMIT)
        self.clean_and_score_leads()

        sheet_sync = {'synced': 0, 'worksheet': None}
        try:
            from src.reporting import reporting_manager
            sheet_sync = reporting_manager.sync_leads_to_sheet()
        except Exception as e:
            logger.warning(f"Lead sheet sync skipped during cycle: {e}")

        result = {
            'maps_upserts': maps_count,
            'contact_updates': contact_updates,
            'email_updates': email_updates,
            'sheet_sync': sheet_sync
        }
        logger.info(f"Autonomous lead generation cycle finished: {result}")
        return result

    def collect_from_google_maps(self, districts=None, keywords=None, max_queries=None, results_per_query=None):
        """Collect operational institute leads from Google Places."""
        if not self.google_maps_api_key:
            logger.error("GOOGLE_MAPS_API_KEY missing; Google Maps collection skipped")
            return 0

        districts = districts or Config.LEAD_COLLECTION_DISTRICTS
        keywords = keywords or Config.LEAD_COLLECTION_KEYWORDS
        max_queries = max_queries or Config.LEAD_COLLECTION_QUERIES_PER_RUN
        results_per_query = results_per_query or Config.LEAD_COLLECTION_RESULTS_PER_QUERY

        ensure_search_tasks(districts, keywords)
        tasks = get_next_search_tasks(max_queries, reset_days=Config.SEARCH_TASK_RESET_DAYS)
        if tasks:
            query_plan = [(task.district, task.keyword, task) for task in tasks]
        else:
            query_plan = [(district, keyword, None) for district in districts for keyword in keywords]
            random.shuffle(query_plan)
            query_plan = query_plan[:max_queries]

        total_upserts = 0
        for district, keyword, task in query_plan:
            try:
                upserts = self._collect_google_maps_query(keyword, district, results_per_query)
                total_upserts += upserts
                if task:
                    mark_search_task_result(task, upserts)
            except Exception as e:
                logger.error(f"Google Maps query failed for {keyword} in {district}: {e}")
                if task:
                    mark_search_task_result(task, 0)

        logger.info(f"Google Maps collection complete: {total_upserts} leads upserted")
        return total_upserts

    def _collect_google_maps_query(self, keyword, district, results_per_query):
        search_query = f"{keyword} in {district}, Bangladesh"
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {'query': search_query, 'key': self.google_maps_api_key}
        page_token = None
        seen_results = 0
        upserts = 0

        logger.info(f"Collecting Google Maps leads: {search_query}")
        while seen_results < results_per_query:
            if page_token:
                params = {'pagetoken': page_token, 'key': self.google_maps_api_key}

            data = self._get_google_places_page(url, params, bool(page_token))
            status = data.get('status')
            if status == 'ZERO_RESULTS':
                break
            if status not in ('OK', None):
                logger.info(f"Google Places status={status} query={search_query} error={data.get('error_message')}")
                break

            results = data.get('results', [])
            if not results:
                break

            for place in results:
                if seen_results >= results_per_query:
                    break
                seen_results += 1
                lead = self._process_google_place(place, district, keyword)
                if lead:
                    upserts += 1
                time.sleep(random.uniform(0.2, 0.6))

            page_token = data.get('next_page_token')
            if not page_token:
                break

        return upserts

    def _get_google_places_page(self, url, params, is_page_token_request=False):
        if not is_page_token_request:
            return self._get_json(url, params)

        for attempt in range(4):
            time.sleep(2.5 + attempt)
            data = self._get_json(url, params)
            if data.get('status') != 'INVALID_REQUEST':
                return data
            logger.debug(f"Google Places page token not ready yet; retry={attempt + 1}")
        return data

    def _process_google_place(self, place, district, keyword):
        place_id = place.get('place_id')
        name = (place.get('name') or '').strip()
        if not place_id or not name or self._looks_closed(name):
            return None

        business_status = place.get('business_status')
        if business_status in self.CLOSED_STATUSES:
            return None

        details = self._fetch_place_details(place_id)
        details_status = details.get('business_status') or business_status
        if details_status in self.CLOSED_STATUSES:
            return None

        address = details.get('formatted_address') or place.get('formatted_address') or ''
        phone = self._clean_phone(
            details.get('formatted_phone_number') or details.get('international_phone_number')
        )
        website = self._clean_website_url(details.get('website') or '')
        if not phone and website:
            phone = self._find_phone_from_website(website)
        rating = details.get('rating') or place.get('rating')
        user_ratings_total = details.get('user_ratings_total') or place.get('user_ratings_total')

        if not (address or phone or website):
            return None

        active_status = self._active_status_from_business_status(details_status)
        return add_lead(
            school_name=name,
            phone=phone,
            email=None,
            district=district,
            type=self._identify_type(keyword),
            source='google_maps',
            website=website,
            address=address,
            place_id=place_id,
            business_status=details_status,
            active_status=active_status,
            rating=rating,
            user_ratings_total=user_ratings_total,
            last_checked_at=datetime.utcnow()
        )

    def _fetch_place_details(self, place_id):
        details_url = "https://maps.googleapis.com/maps/api/place/details/json"
        details_params = {
            'place_id': place_id,
            'fields': (
                'name,formatted_phone_number,international_phone_number,website,'
                'formatted_address,business_status,rating,user_ratings_total'
            ),
            'key': self.google_maps_api_key
        }
        data = self._get_json(details_url, details_params)
        return data.get('result', {})

    def _get_json(self, url, params):
        if 'maps.googleapis.com' in url and not can_use_api('google_places', Config.GOOGLE_PLACES_DAILY_CALL_LIMIT):
            raise RuntimeError("Google Places daily API call limit reached")
        response = requests.get(url, params=params, timeout=15)
        if 'maps.googleapis.com' in url:
            record_api_usage(
                'google_places',
                endpoint=urlparse(url).path.rsplit('/', 1)[-1],
                status_code=response.status_code,
                success=response.status_code < 400
            )
        response.raise_for_status()
        return response.json()

    def collect_from_facebook_groups(self):
        logger.info("Facebook collection skipped: use official Meta APIs after developer access is ready")
        return 0

    def collect_from_linkedin(self):
        logger.info("LinkedIn collection skipped: use official API-approved sources only")
        return 0

    def enrich_missing_contact_info(self, limit=500, find_email=False, commit_every=25):
        """Refresh Google details for leads missing phone, website, status, or place_id."""
        cutoff = datetime.utcnow() - timedelta(days=7)
        leads = Lead.query.filter(
            Lead.source == 'google_maps',
            or_(Lead.active_status == None, Lead.active_status != 'closed'),
            or_(
                Lead.phone == None,
                Lead.phone == '',
                Lead.website == None,
                Lead.website == '',
                Lead.place_id == None,
                Lead.business_status == None,
                Lead.last_enriched_at == None,
                Lead.last_enriched_at < cutoff
            )
        ).limit(limit).all()

        updated = 0
        for lead in leads:
            try:
                details = self._lookup_place_details(lead.school_name, lead.district, lead.place_id)
                lead.last_enriched_at = datetime.utcnow()
                if not details:
                    continue

                changed = self._apply_place_details_to_lead(lead, details)
                if find_email and lead.website and not lead.email:
                    email = self.find_emails(lead.website, lead.school_name)
                    lead.email_checked_at = datetime.utcnow()
                    if email:
                        lead.email = email
                        changed = True

                if changed:
                    updated += 1
                if updated and updated % commit_every == 0:
                    db.session.commit()

                time.sleep(random.uniform(0.2, 0.7))
            except Exception as e:
                db.session.rollback()
                logger.warning(f"Contact enrichment failed for {lead.school_name}: {e}")

        db.session.commit()
        logger.info(f"Contact enrichment updated {updated} leads")
        return updated

    def _lookup_place_details(self, school_name, district=None, place_id=None):
        if place_id:
            details = self._fetch_place_details(place_id)
            details['place_id'] = place_id
            return details

        if not school_name:
            return None

        query = f"{school_name}, {district}, Bangladesh" if district else f"{school_name}, Bangladesh"
        search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        data = self._get_json(search_url, {'query': query, 'key': self.google_maps_api_key})
        results = data.get('results', [])
        if not results:
            return None

        place = results[0]
        place_id = place.get('place_id')
        if not place_id:
            return None

        details = self._fetch_place_details(place_id)
        details['place_id'] = place_id
        details['business_status'] = details.get('business_status') or place.get('business_status')
        details['rating'] = details.get('rating') or place.get('rating')
        details['user_ratings_total'] = details.get('user_ratings_total') or place.get('user_ratings_total')
        return details

    def _apply_place_details_to_lead(self, lead, details):
        changed = False
        business_status = details.get('business_status')
        active_status = self._active_status_from_business_status(business_status)

        phone = self._clean_phone(details.get('formatted_phone_number') or details.get('international_phone_number'))
        website = self._clean_website_url(details.get('website') or '')
        if not phone and website:
            phone = self._find_phone_from_website(website)

        updates = {
            'place_id': details.get('place_id'),
            'business_status': business_status,
            'active_status': active_status,
            'phone': phone,
            'website': website,
            'address': details.get('formatted_address') or '',
            'rating': details.get('rating'),
            'user_ratings_total': details.get('user_ratings_total'),
            'last_checked_at': datetime.utcnow(),
        }

        for field, value in updates.items():
            if value in (None, ''):
                continue
            if field in {'business_status', 'active_status', 'rating', 'user_ratings_total', 'last_checked_at'}:
                if getattr(lead, field) != value:
                    setattr(lead, field, value)
                    changed = True
            elif getattr(lead, field) in (None, ''):
                setattr(lead, field, value)
                changed = True

        if self._looks_closed(lead.school_name) or business_status in self.CLOSED_STATUSES:
            lead.active_status = 'closed'
            changed = True

        refresh_lead_contact_fields(lead)

        return changed

    def find_emails(self, domain, school_name=None):
        """Find one likely email using Hunter first when configured, then public website pages."""
        email, _ = self._find_email_candidate(domain, school_name, allow_hunter=True)
        return email

    def _find_email_candidate(self, domain, school_name=None, allow_hunter=True):
        try:
            if not domain or not domain.startswith('http'):
                return None, False

            domain_name = urlparse(domain).netloc.lower().replace('www.', '')
            if self.hunter_api_key and Config.EMAIL_FINDER_PROVIDER == 'hunter' and allow_hunter:
                hunter_email = self._find_email_with_hunter(domain_name)
                if hunter_email:
                    return hunter_email, True

            website_email = self._find_email_from_website(domain, school_name)
            if website_email:
                return website_email, False

            if self.hunter_api_key and Config.EMAIL_FINDER_PROVIDER == 'auto' and allow_hunter:
                hunter_email = self._find_email_with_hunter(domain_name)
                if hunter_email:
                    return hunter_email, True
                return None, True

            return None, False
        except Exception as e:
            logger.warning(f"Email finder error: {e}")
            return None, False

    def _find_email_with_hunter(self, domain_name):
        url = "https://api.hunter.io/v2/domain-search"
        if not can_use_api('hunter', Config.HUNTER_DAILY_CALL_LIMIT):
            logger.info(f"Hunter daily API call limit reached; skipping {domain_name}")
            return None
        params = {'domain': domain_name, 'limit': 10, 'api_key': self.hunter_api_key}
        response = requests.get(url, params=params, timeout=10)
        record_api_usage(
            'hunter',
            endpoint='domain-search',
            status_code=response.status_code,
            success=response.status_code < 400
        )
        if response.status_code in (401, 403, 429):
            logger.warning(f"Hunter lookup skipped for {domain_name}: status={response.status_code}")
            return None
        response.raise_for_status()
        data = response.json().get('data') or {}
        emails = data.get('emails') or []
        if not emails:
            return None

        preferred_prefixes = ('info', 'contact', 'admin', 'admission', 'admissions', 'support', 'principal')
        emails = sorted(
            emails,
            key=lambda item: (
                0 if item.get('value', '').split('@')[0].lower() in preferred_prefixes else 1,
                -(item.get('confidence') or 0)
            )
        )
        return emails[0].get('value')

    def _find_email_from_website(self, website, school_name=None):
        candidates = self._build_email_candidate_urls(website)
        checked = set()
        found_emails = []

        for url in candidates:
            if url in checked or len(checked) >= Config.WEBSITE_EMAIL_MAX_PAGES:
                continue
            checked.add(url)
            try:
                response = requests.get(
                    url,
                    timeout=8,
                    allow_redirects=True,
                    headers={'User-Agent': 'Mozilla/5.0 (compatible; PathshalaPro lead email enrichment)'}
                )
                if response.status_code >= 400:
                    continue
                content_type = response.headers.get('Content-Type', '').lower()
                if content_type and not any(token in content_type for token in ('html', 'text', 'json')):
                    continue

                soup = BeautifulSoup(response.text, 'html.parser')
                found_emails.extend(self._extract_emails_from_html(response.text, soup))

                if len(checked) < Config.WEBSITE_EMAIL_MAX_PAGES:
                    for link in self._discover_email_candidate_links(url, soup):
                        if link not in checked and link not in candidates:
                            candidates.append(link)
            except Exception as e:
                logger.debug(f"Website email lookup failed for {url}: {e}")

        return self._pick_best_email(found_emails, website, school_name)

    def _build_email_candidate_urls(self, website):
        base = website.rstrip('/')
        parsed = urlparse(base)
        urls = [base]
        paths = (
            'contact', 'contact-us', 'contacts', 'about', 'about-us', 'admission',
            'admissions', 'apply', 'enquiry', 'inquiry', 'support', 'privacy-policy'
        )

        for path in paths:
            urls.append(f"{base}/{path}")

        if 'facebook.com' in parsed.netloc.lower():
            path = parsed.path.strip('/')
            if path:
                urls.extend([
                    f"https://www.facebook.com/{path}/about",
                    f"https://m.facebook.com/{path}/about",
                    f"https://m.facebook.com/{path}"
                ])

        return list(dict.fromkeys(urls))

    def _discover_email_candidate_links(self, current_url, soup):
        wanted = re.compile(
            r'(contact|about|admission|enquiry|inquiry|support|privacy|যোগাযোগ|ভর্তি)',
            re.IGNORECASE
        )
        links = []
        current_host = urlparse(current_url).netloc.lower().replace('www.', '')

        for link in soup.select('a[href]'):
            href = link.get('href', '').strip()
            label = link.get_text(' ', strip=True)
            if not href or href.startswith(('#', 'javascript:', 'tel:', 'mailto:')):
                continue
            if not wanted.search(f"{href} {label}"):
                continue

            target = urljoin(current_url, href).split('#', 1)[0].rstrip('/')
            parsed = urlparse(target)
            target_host = parsed.netloc.lower().replace('www.', '')
            if target_host and target_host != current_host:
                continue
            links.append(target)

        return list(dict.fromkeys(links))[:8]

    def _extract_emails_from_html(self, html, soup):
        values = []
        for link in soup.select('a[href^="mailto:"]'):
            values.append(unquote(link.get('href', '')))
            values.append(link.get_text(' ', strip=True))

        for script in soup(['script', 'style']):
            script.decompose()
        values.append(soup.get_text(' ', strip=True))
        values.append(html)

        emails = []
        for value in values:
            normalized = self._normalize_obfuscated_email_text(value)
            emails.extend(self._EMAIL_PATTERN.findall(normalized))
        return emails

    def _normalize_obfuscated_email_text(self, value):
        text = unescape(value or '')
        replacements = [
            (r'\s*(?:\[|\()\s*at\s*(?:\]|\))\s*', '@'),
            (r'\s+at\s+', '@'),
            (r'\s*(?:\[|\()\s*dot\s*(?:\]|\))\s*', '.'),
            (r'\s+dot\s+', '.'),
            (r'\s*\[email\s+protected\]\s*', '@'),
        ]
        for pattern, replacement in replacements:
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
        return text

    _EMAIL_PATTERN = re.compile(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}', re.IGNORECASE)

    def _pick_best_email(self, emails, website, school_name=None):
        clean_emails = []
        blocked_domains = {
            'example.com', 'domain.com', 'email.com', 'sentry.io', 'wixpress.com',
            'facebook.com', 'facebookmail.com'
        }
        blocked_prefixes = ('noreply', 'no-reply', 'donotreply', 'example', 'test')
        preferred_prefixes = (
            'info', 'contact', 'admin', 'admission', 'admissions', 'support',
            'principal', 'office', 'hello', 'mail'
        )
        website_domain = urlparse(website).netloc.lower().replace('www.', '')

        for raw_email in emails:
            email = raw_email.strip().strip('.,;:()[]{}<>').lower()
            if not email or '@' not in email:
                continue
            if email.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.css', '.js')):
                continue
            local, domain = email.rsplit('@', 1)
            if domain in blocked_domains or any(local.startswith(prefix) for prefix in blocked_prefixes):
                continue
            if len(local) > 64 or len(email) > 254:
                continue
            clean_emails.append(email)

        if not clean_emails:
            return None

        def score(email):
            local, domain = email.rsplit('@', 1)
            value = 0
            if website_domain and domain == website_domain:
                value += 40
            elif website_domain and website_domain.endswith(domain):
                value += 20
            if local in preferred_prefixes:
                value += 25
            if any(word in local for word in ('admission', 'info', 'contact', 'office')):
                value += 10
            if school_name:
                normalized_name = re.sub(r'[^a-z0-9]+', '', school_name.lower())
                if normalized_name and normalized_name[:8] in re.sub(r'[^a-z0-9]+', '', email):
                    value += 5
            return value

        return sorted(set(clean_emails), key=lambda email: (-score(email), email))[0]

    def _find_phone_from_website(self, website):
        candidates = [website.rstrip('/')]
        for path in ('contact', 'contact-us', 'about', 'about-us', 'admission'):
            candidates.append(f"{website.rstrip('/')}/{path}")

        for url in candidates:
            try:
                response = requests.get(
                    url,
                    timeout=3,
                    headers={'User-Agent': 'Mozilla/5.0 PathshalaPro lead phone enrichment'}
                )
                if response.status_code >= 400:
                    continue

                soup = BeautifulSoup(response.text, 'html.parser')
                values = []
                for link in soup.select('a[href^="tel:"], a[href^="https://wa.me/"], a[href*="whatsapp"]'):
                    values.append(link.get('href', ''))
                    values.append(link.get_text(' ', strip=True))
                values.append(soup.get_text(' ', strip=True))

                for value in values:
                    phone_info = normalize_bd_phone(value)
                    if phone_info['phone_valid']:
                        return phone_info['phone']
            except Exception as e:
                logger.debug(f"Website phone lookup failed for {url}: {e}")

        return None

    def enrich_missing_emails(self, limit=50, commit_every=10, force=False):
        """Enrich a small quota-aware batch of active leads with websites."""
        cutoff = datetime.utcnow() - timedelta(days=14)
        filters = [
            or_(Lead.email == None, Lead.email == ''),
            Lead.website != None,
            Lead.website != '',
            or_(Lead.active_status == None, Lead.active_status != 'closed'),
        ]
        if not force:
            filters.append(or_(Lead.email_checked_at == None, Lead.email_checked_at < cutoff))

        leads = Lead.query.filter(*filters).order_by(Lead.email_checked_at.asc().nullsfirst()).limit(limit).all()

        updated = 0
        checked = 0
        hunter_searches = 0
        for lead in leads:
            allow_hunter = hunter_searches < Config.HUNTER_SEARCHES_PER_RUN
            email, hunter_used = self._find_email_candidate(lead.website, lead.school_name, allow_hunter=allow_hunter)
            if hunter_used:
                hunter_searches += 1
            lead.email_checked_at = datetime.utcnow()
            checked += 1
            if email:
                lead.email = email
                refresh_lead_contact_fields(lead)
                updated += 1
            if checked % commit_every == 0:
                db.session.commit()

        db.session.commit()
        logger.info(
            f"Email enrichment checked {checked}, updated {updated} leads, "
            f"hunter_searches={hunter_searches}, force={force}"
        )
        return updated

    def clean_and_score_leads(self):
        """Deduplicate, mark inactive leads, and score usable records."""
        leads = Lead.query.order_by(Lead.updated_at.desc()).all()
        seen_place_ids = set()
        seen_phones = set()
        seen_emails = set()
        seen_duplicate_keys = set()
        seen_keys = set()
        duplicates_removed = 0

        for lead in leads:
            refresh_lead_contact_fields(lead)
            duplicate = False
            if lead.place_id:
                duplicate = lead.place_id in seen_place_ids
                seen_place_ids.add(lead.place_id)
            if not duplicate and lead.phone_e164:
                duplicate = lead.phone_e164 in seen_phones
                seen_phones.add(lead.phone_e164)
            if not duplicate and lead.email:
                duplicate = lead.email in seen_emails
                seen_emails.add(lead.email)
            if not duplicate and lead.duplicate_key:
                duplicate = lead.duplicate_key in seen_duplicate_keys
                seen_duplicate_keys.add(lead.duplicate_key)
            if not duplicate and lead.canonical_key:
                duplicate = lead.canonical_key in seen_keys
                seen_keys.add(lead.canonical_key)

            if duplicate:
                db.session.delete(lead)
                duplicates_removed += 1

        db.session.commit()

        leads = Lead.query.all()
        for lead in leads:
            if self._looks_closed(lead.school_name) or lead.business_status in self.CLOSED_STATUSES:
                lead.active_status = 'closed'
                lead.score = 0
                lead.segment = 'Inactive'
                continue

            if not lead.active_status:
                lead.active_status = self._active_status_from_business_status(lead.business_status)

            score = 0
            if lead.active_status == 'active':
                score += 2
            refresh_lead_contact_fields(lead)

            if lead.qualification_status == 'unusable':
                lead.segment = 'Unusable'
                lead.score = 0
                continue

            if lead.email:
                score += 3
            if lead.phone_valid:
                score += 2
            if lead.website:
                score += 2
            if lead.address:
                score += 1
            if (lead.user_ratings_total or 0) >= 5:
                score += 1

            if score >= 7:
                segment = 'Hot'
            elif score >= 4:
                segment = 'Warm'
            else:
                segment = 'Cold'

            lead.score = score
            lead.segment = segment

        db.session.commit()
        logger.info(f"Lead cleaning complete: duplicates_removed={duplicates_removed}")

    def _clean_website_url(self, url):
        if not url:
            return ''

        url = str(url).strip()
        parsed = urlparse(url)
        if parsed.netloc.lower() in {'l.facebook.com', 'lm.facebook.com'}:
            target = parse_qs(parsed.query).get('u', [''])[0]
            if target:
                url = unquote(target).strip()
                parsed = urlparse(url)

        if not parsed.scheme or not parsed.netloc:
            return ''
        if len(url) > 255:
            logger.debug(f"Skipping overlong website URL for storage: {url[:120]}...")
            return ''
        return url

    def _clean_phone(self, phone):
        return normalize_bd_phone(phone)['phone']

    def _looks_closed(self, name):
        return bool(name and self.CLOSED_NAME_PATTERN.search(name))

    def _active_status_from_business_status(self, business_status):
        if business_status in self.CLOSED_STATUSES:
            return 'closed'
        if business_status == 'OPERATIONAL':
            return 'active'
        return 'unknown'

    def _identify_type(self, keyword):
        keyword_lower = keyword.lower()
        if 'coaching' in keyword_lower or 'tuition' in keyword_lower:
            return 'Coaching'
        if 'madrasa' in keyword_lower or 'madrasah' in keyword_lower:
            return 'Madrasa'
        if 'kindergarten' in keyword_lower or 'kinder' in keyword_lower:
            return 'Kindergarten'
        if 'college' in keyword_lower:
            return 'College'
        if 'polytechnic' in keyword_lower or 'technical' in keyword_lower or 'training' in keyword_lower:
            return 'Technical'
        if 'academy' in keyword_lower or 'institute' in keyword_lower:
            return 'Institute'
        return 'School'

    def run_all(self):
        return self.run_autonomous_cycle()


lead_collector = LeadCollector()


if __name__ == '__main__':
    collector = LeadCollector()
    collector.run_autonomous_cycle()
