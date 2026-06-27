import csv
import io
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
    get_source_cursor,
    mark_search_task_result,
    normalize_bd_phone,
    normalize_phone,
    record_api_usage,
    refresh_lead_contact_fields,
    update_source_cursor,
)

logger.add("logs/lead_collection.log", rotation="500 MB")


class LeadCollector:
    """Autonomous lead collection and enrichment for Bangladesh institutes."""

    CLOSED_STATUSES = {'CLOSED_PERMANENTLY', 'CLOSED_TEMPORARILY'}
    CLOSED_NAME_PATTERN = re.compile(
        r'\b(permanently\s+closed|temporarily\s+closed|closed)\b|বন্ধ',
        re.IGNORECASE
    )
    USA_LOCATIONS = [
        {'city': 'New York', 'state': 'NY', 'bbox': (40.4774, -74.2591, 40.9176, -73.7004)},
        {'city': 'Los Angeles', 'state': 'CA', 'bbox': (33.7037, -118.6682, 34.3373, -118.1553)},
        {'city': 'Chicago', 'state': 'IL', 'bbox': (41.6445, -87.9401, 42.0230, -87.5241)},
        {'city': 'Houston', 'state': 'TX', 'bbox': (29.5236, -95.9097, 30.1107, -95.0145)},
        {'city': 'Phoenix', 'state': 'AZ', 'bbox': (33.2903, -112.3240, 33.9206, -111.9261)},
        {'city': 'Philadelphia', 'state': 'PA', 'bbox': (39.8670, -75.2803, 40.1379, -74.9558)},
        {'city': 'San Antonio', 'state': 'TX', 'bbox': (29.1872, -98.8041, 29.7180, -98.2229)},
        {'city': 'San Diego', 'state': 'CA', 'bbox': (32.5343, -117.2825, 33.1142, -116.9057)},
        {'city': 'Dallas', 'state': 'TX', 'bbox': (32.6175, -97.0005, 33.0238, -96.4636)},
        {'city': 'Austin', 'state': 'TX', 'bbox': (30.0987, -97.9384, 30.5169, -97.5615)},
        {'city': 'Miami', 'state': 'FL', 'bbox': (25.7090, -80.3198, 25.8558, -80.1392)},
        {'city': 'Atlanta', 'state': 'GA', 'bbox': (33.6478, -84.5511, 33.8868, -84.2896)},
    ]

    def __init__(self):
        self.google_maps_api_key = Config.GOOGLE_MAPS_API_KEY
        self.hunter_api_key = Config.HUNTER_API_KEY
        self.google_places_available = True

    def run_autonomous_cycle(self):
        """Run one safe, repeatable lead-only cycle."""
        logger.info("=" * 50)
        logger.info("Autonomous lead generation cycle started")
        logger.info("=" * 50)

        public_dataset_count = self.collect_from_public_datasets()
        maps_count = self.collect_from_google_maps()
        osm_count = self.collect_from_openstreetmap()
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
            'public_dataset': public_dataset_count,
            'google_maps': maps_count,
            'openstreetmap': osm_count,
            'contact_updates': contact_updates,
            'email_updates': email_updates,
            'sheet_sync': sheet_sync
        }
        logger.info(f"Autonomous lead generation cycle finished: {result}")
        return result

    def _source_stats(self):
        return {
            'processed': 0,
            'created': 0,
            'updated': 0,
            'unchanged': 0,
            'skipped': 0
        }

    def _record_source_result(self, stats, lead):
        if not lead:
            stats['skipped'] += 1
            return stats

        action = getattr(lead, '_upsert_action', 'updated')
        if action not in ('created', 'updated', 'unchanged'):
            action = 'updated'
        stats[action] += 1
        return stats

    def _actionable_count(self, stats):
        if isinstance(stats, dict):
            return int(stats.get('created', 0) or 0) + int(stats.get('updated', 0) or 0)
        return int(stats or 0)

    def _merge_stats(self, target, source):
        for key in target:
            target[key] += int(source.get(key, 0) or 0)
        return target

    def collect_from_google_maps(self, districts=None, keywords=None, max_queries=None, results_per_query=None):
        """Collect operational institute leads from Google Places."""
        stats = self._source_stats()
        if not self.google_maps_api_key:
            logger.error("GOOGLE_MAPS_API_KEY missing; Google Maps collection skipped")
            stats['skipped'] = max_queries or 0
            return stats

        if not can_use_api('google_places', Config.GOOGLE_PLACES_DAILY_CALL_LIMIT):
            logger.info("Google Places daily API call limit reached; collection skipped until quota resets")
            return stats

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

        for district, keyword, task in query_plan:
            if not self.google_places_available:
                logger.info("Google Places collection stopped after provider denied the request")
                break
            try:
                query_stats = self._collect_google_maps_query(keyword, district, results_per_query)
                self._merge_stats(stats, query_stats)
                if task:
                    mark_search_task_result(task, self._actionable_count(query_stats))
            except Exception as e:
                logger.error(f"Google Maps query failed for {keyword} in {district}: {e}")
                if task:
                    mark_search_task_result(task, 0)

        logger.info(f"Google Maps collection complete: {stats}")
        return stats

    def _collect_google_maps_query(self, keyword, district, results_per_query):
        search_query = f"{keyword} in {district}, Bangladesh"
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {'query': search_query, 'key': self.google_maps_api_key}
        page_token = None
        seen_results = 0
        stats = self._source_stats()

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
                if status in {'REQUEST_DENIED', 'OVER_DAILY_LIMIT'}:
                    self.google_places_available = False
                break

            results = data.get('results', [])
            if not results:
                break

            for place in results:
                if seen_results >= results_per_query:
                    break
                seen_results += 1
                stats['processed'] += 1
                lead = self._process_google_place(place, district, keyword)
                self._record_source_result(stats, lead)
                time.sleep(random.uniform(0.2, 0.6))

            page_token = data.get('next_page_token')
            if not page_token:
                break

        return stats

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
            source_record_id=place_id,
            source_confidence=90,
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
        data = response.json()
        if 'maps.googleapis.com' in url and data.get('status') in {'REQUEST_DENIED', 'OVER_DAILY_LIMIT'}:
            self.google_places_available = False
        return data

    def collect_from_facebook_groups(self):
        logger.info("Facebook collection skipped: use official Meta APIs after developer access is ready")
        return 0

    def collect_from_public_datasets(self, limit=None):
        """Collect high-volume free leads from public Bangladesh open-data datasets."""
        stats = self._source_stats()
        if not Config.ENABLE_PUBLIC_DATASET_COLLECTION:
            return stats

        limit = limit or Config.PUBLIC_DATASET_BATCH_SIZE
        rows = self._fetch_data_gov_contact_rows()
        if not rows:
            return stats

        cursor = get_source_cursor('data_gov_bd_contact')
        start = cursor.cursor % len(rows)
        selected = rows[start:] + rows[:start]

        processed = 0
        for row in selected:
            if processed >= limit:
                break
            processed += 1
            stats['processed'] += 1
            lead = self._process_data_gov_contact_row(row)
            self._record_source_result(stats, lead)

        next_cursor = (start + processed) % len(rows)
        update_source_cursor(
            'data_gov_bd_contact',
            next_cursor,
            total_seen=len(rows),
            meta={'last_batch_size': processed, 'last_stats': stats}
        )
        logger.info(f"Public dataset collection complete: {stats}")
        return stats

    def _fetch_data_gov_contact_rows(self):
        url = Config.PUBLIC_DATASET_CONTACT_URL
        try:
            response = requests.get(
                url,
                timeout=60,
                headers={'User-Agent': 'PathshalaPro lead collection (contact: info@pathshalapro.net)'}
            )
            response.raise_for_status()
        except Exception as e:
            logger.warning(f"Public dataset download failed: {url}: {e}")
            return []

        text = response.content.decode('utf-8-sig', errors='replace')
        header_index = text.upper().find('DIVISION_NAME,')
        if header_index > 0:
            text = text[header_index:]
        if 'INSTITUTE_NAME' not in text[:500].upper():
            logger.warning("Public dataset skipped: CSV header not found")
            return []

        try:
            return list(csv.DictReader(io.StringIO(text)))
        except Exception as e:
            logger.warning(f"Public dataset CSV parse failed: {e}")
            return []

    def _process_data_gov_contact_row(self, row):
        name = (row.get('INSTITUTE_NAME_NEW') or row.get('INSTITUTE_NAME') or '').strip()
        if not name or self._looks_closed(name):
            return None

        phone = self._clean_phone(row.get('MOBPHONE') or row.get('MOBILE') or row.get('PHONE'))
        email = self._normalize_email_value(row.get('EMAIL') or row.get('MAIL'))
        website = self._clean_website_url(row.get('WEBSITE') or row.get('URL') or '')
        if not (phone or email or website):
            return None

        district = (row.get('DISTRICT_NAME') or row.get('DISTRICT') or '').strip().title() or 'Bangladesh'
        thana = (row.get('THANA_NAME') or row.get('THANA') or row.get('UPAZILA') or '').strip().title()
        post_office = (row.get('POST_OFFICE') or '').strip().title()
        location = (row.get('LOCATION') or '').strip().title()
        address = ', '.join(part for part in (location, post_office, thana, district) if part)
        eiin = str(row.get('EIIN') or '').strip()
        source_id = f"data_gov_bd:contact:{eiin or self._slugify(name)}"

        return add_lead(
            school_name=name.title(),
            phone=phone,
            email=email,
            district=district,
            type=self._identify_public_dataset_type(row),
            source='data_gov_bd',
            source_record_id=source_id,
            source_confidence=80,
            eiin=eiin,
            upazila=thana,
            website=website,
            address=address,
            place_id=source_id,
            business_status='OPERATIONAL',
            active_status='active',
            last_checked_at=datetime.utcnow()
        )

    def _identify_public_dataset_type(self, row):
        text = ' '.join(str(row.get(key, '')) for key in ('TYP', 'LVL', 'INSTITUTE_NAME_NEW', 'INSTITUTE_NAME')).lower()
        if 'madrasa' in text or 'madrasah' in text:
            return 'Madrasa'
        if 'college' in text:
            return 'College'
        if 'kindergarten' in text or 'kg' in text:
            return 'Kindergarten'
        if 'technical' in text or 'vocational' in text:
            return 'Technical Institute'
        return 'School'

    def collect_usa_local_businesses(self, locations_per_run=None, results_per_location=None):
        """Collect the requested USA local-business niches from free OSM data."""
        stats = self._source_stats()
        if not Config.ENABLE_USA_LOCAL_BUSINESS_COLLECTION:
            return stats

        niches = list(Config.USA_LOCAL_BUSINESS_NICHES or [])
        tasks = [(location, niche) for location in self.USA_LOCATIONS for niche in niches]
        if not tasks:
            return stats

        batch_size = locations_per_run or Config.USA_LOCAL_BUSINESS_LOCATIONS_PER_RUN
        result_limit = results_per_location or Config.USA_LOCAL_BUSINESS_RESULTS_PER_LOCATION
        cursor = get_source_cursor('usa_local_business_osm')
        start = cursor.cursor % len(tasks)
        selected = (tasks[start:] + tasks[:start])[:batch_size]
        processed_tasks = 0

        for location, niche in selected:
            label = f"{niche} in {location['city']}, {location['state']}"
            try:
                query = self._build_usa_overpass_query(location['bbox'], niche, result_limit)
                elements = self._fetch_overpass_elements(query, label)
                for element in elements:
                    stats['processed'] += 1
                    lead = self._process_usa_osm_business(element, location, niche)
                    self._record_source_result(stats, lead)
                processed_tasks += 1
                time.sleep(random.uniform(1.0, 2.0))
            except Exception as e:
                logger.warning(f"USA local-business collection failed for {label}: {e}")

        update_source_cursor(
            'usa_local_business_osm',
            (start + max(processed_tasks, 1)) % len(tasks),
            total_seen=len(tasks),
            meta={
                'last_tasks': [f"{niche}:{location['city']}" for location, niche in selected],
                'last_stats': stats,
            }
        )
        logger.info(f"USA local-business collection complete: {stats}")
        return stats

    def _build_usa_overpass_query(self, bbox, niche, limit):
        south, west, north, east = bbox
        limit = max(10, min(int(limit), 250))
        niche_key = niche.lower()
        if 'med spa' in niche_key:
            selectors = (
                f'nwr["name"~"med(ical)? spa|aesthetic|medspa",i]({south},{west},{north},{east});\n'
                f'nwr["beauty"="spa"]({south},{west},{north},{east});'
            )
        elif 'real estate' in niche_key:
            selectors = (
                f'nwr["office"~"estate_agent|property_management"]({south},{west},{north},{east});\n'
                f'nwr["name"~"real estate|realtor|realty",i]({south},{west},{north},{east});'
            )
        else:
            selectors = f'nwr["amenity"~"^(restaurant|cafe)$"]({south},{west},{north},{east});'
        return f"""
[out:json][timeout:45];
(
  {selectors}
);
out tags center qt {limit};
"""

    def _process_usa_osm_business(self, element, location, niche):
        tags = element.get('tags') or {}
        name = (tags.get('name') or tags.get('official_name') or '').strip()
        if not name or self._looks_closed(name):
            return None

        raw_phone = tags.get('contact:phone') or tags.get('phone') or tags.get('contact:mobile')
        email = self._normalize_email_value(tags.get('contact:email') or tags.get('email'))
        website = self._clean_website_url(tags.get('contact:website') or tags.get('website') or '')
        facebook = self._social_url(tags.get('contact:facebook') or tags.get('facebook'), 'facebook.com')
        instagram = self._social_url(tags.get('contact:instagram') or tags.get('instagram'), 'instagram.com')
        if not (raw_phone or email or website):
            return None

        address = self._osm_address(tags)
        if not address:
            address = f"{location['city']}, {location['state']}, USA"
        osm_id = f"osm:US:{element.get('type')}:{element.get('id')}"
        missing = []
        if not instagram and not facebook:
            missing.append('No social profiles linked in public listing')
        if not email:
            missing.append('Email not publicly listed')
        if not website:
            missing.append('No official website linked')

        return add_lead(
            school_name=name,
            phone=raw_phone,
            email=email,
            district=f"{location['city']}, {location['state']}",
            type=niche,
            source='openstreetmap_usa',
            source_record_id=osm_id,
            source_confidence=65,
            website=website,
            address=address,
            place_id=osm_id,
            business_status='OPERATIONAL',
            active_status='active',
            market='usa_local_business',
            country_code='US',
            state=location['state'],
            city=location['city'],
            facebook_url=facebook,
            instagram_url=instagram,
            prospect_problem='; '.join(missing),
            last_checked_at=datetime.utcnow(),
        )

    def _social_url(self, value, domain):
        if not value:
            return None
        value = str(value).strip()
        if value.startswith('http://') or value.startswith('https://'):
            return value[:255]
        return f"https://{domain}/{value.lstrip('@/')}"[:255]

    def _slugify(self, value):
        return re.sub(r'[^a-z0-9]+', '-', str(value).lower()).strip('-')[:120]

    def collect_from_openstreetmap(self, districts=None, districts_per_run=None, results_per_district=None):
        """Collect institute leads from free OpenStreetMap/Overpass public data."""
        stats = self._source_stats()
        if not Config.ENABLE_OPENSTREETMAP_COLLECTION:
            return stats

        cells = self._osm_grid_cells()
        districts_per_run = districts_per_run or Config.OPENSTREETMAP_DISTRICTS_PER_RUN
        results_per_district = results_per_district or Config.OPENSTREETMAP_RESULTS_PER_DISTRICT
        cursor = get_source_cursor('openstreetmap_grid')
        start = cursor.cursor % len(cells)
        selected_cells = (cells[start:] + cells[:start])[:districts_per_run]

        processed_cells = 0
        for cell in selected_cells:
            try:
                cell_stats = self._collect_openstreetmap_cell(cell, results_per_district)
                self._merge_stats(stats, cell_stats)
                processed_cells += 1
                time.sleep(random.uniform(1.0, 2.0))
            except Exception as e:
                logger.warning(f"OpenStreetMap collection failed for {cell['label']}: {e}")

        update_source_cursor(
            'openstreetmap_grid',
            (start + processed_cells) % len(cells),
            total_seen=len(cells),
            meta={'last_cells': [cell['label'] for cell in selected_cells], 'last_stats': stats}
        )
        logger.info(f"OpenStreetMap collection complete: {stats}")
        return stats

    def _collect_openstreetmap_cell(self, cell, limit):
        query = self._build_overpass_query(cell, limit)
        elements = self._fetch_overpass_elements(query, cell['label'])

        stats = self._source_stats()
        for element in elements:
            stats['processed'] += 1
            lead = self._process_osm_element(element, cell['label'])
            self._record_source_result(stats, lead)
        return stats

    def _fetch_overpass_elements(self, query, cell_label):
        endpoints = list(Config.OVERPASS_API_URLS or [])
        random.shuffle(endpoints)
        last_error = None

        for endpoint in endpoints:
            try:
                response = requests.post(
                    endpoint,
                    data={'data': query},
                    timeout=Config.OVERPASS_TIMEOUT_SECONDS,
                    headers={'User-Agent': 'PathshalaPro lead collection (contact: info@pathshalapro.net)'}
                )
                response.raise_for_status()
                return response.json().get('elements') or []
            except requests.RequestException as e:
                last_error = e
                logger.warning(f"OpenStreetMap endpoint failed for {cell_label}: {endpoint}: {e}")
                time.sleep(random.uniform(1.0, 2.0))

        if last_error:
            raise last_error
        return []

    def _osm_grid_cells(self):
        cells = []
        lat_min, lat_max = 20.6, 26.8
        lon_min, lon_max = 88.0, 92.8
        step = 0.6
        lat = lat_min
        row = 1
        while lat < lat_max:
            lon = lon_min
            col = 1
            while lon < lon_max:
                cells.append({
                    'label': f'BD-OSM-R{row:02d}C{col:02d}',
                    'bbox': (round(lat, 2), round(lon, 2), round(min(lat + step, lat_max), 2), round(min(lon + step, lon_max), 2))
                })
                lon += step
                col += 1
            lat += step
            row += 1
        return cells

    def _build_overpass_query(self, cell, limit):
        south, west, north, east = cell['bbox']
        limit = max(10, min(int(limit), 200))
        return f"""
[out:json][timeout:45];
(
  nwr["amenity"~"^(school|college|university|kindergarten|language_school|prep_school)$"]({south},{west},{north},{east});
  nwr["office"="educational_institution"]({south},{west},{north},{east});
  nwr["training"]({south},{west},{north},{east});
);
out tags center qt {limit};
"""

    def _process_osm_element(self, element, district):
        tags = element.get('tags') or {}
        name = (tags.get('name') or tags.get('name:en') or tags.get('official_name') or '').strip()
        if not name or self._looks_closed(name):
            return None
        if not self._osm_tags_look_bangladesh(tags):
            return None

        phone = self._clean_phone(
            tags.get('contact:phone') or tags.get('phone') or tags.get('mobile') or tags.get('contact:mobile')
        )
        email = self._normalize_email_value(tags.get('contact:email') or tags.get('email'))
        website = self._clean_website_url(
            tags.get('contact:website') or tags.get('website') or tags.get('url') or tags.get('contact:facebook')
        )
        address = self._osm_address(tags)
        district = tags.get('addr:district') or tags.get('addr:city') or tags.get('addr:town') or tags.get('addr:county') or district
        if not (phone or email or website or address):
            return None

        osm_id = f"osm:{element.get('type')}:{element.get('id')}"
        return add_lead(
            school_name=name,
            phone=phone,
            email=email,
            district=district,
            type=self._identify_osm_type(tags),
            source='openstreetmap',
            source_record_id=osm_id,
            source_confidence=60,
            upazila=tags.get('addr:subdistrict') or tags.get('addr:upazila'),
            website=website,
            address=address,
            place_id=osm_id,
            business_status='OPERATIONAL',
            active_status='active',
            last_checked_at=datetime.utcnow()
        )

    def _osm_tags_look_bangladesh(self, tags):
        country = (tags.get('addr:country') or tags.get('is_in:country') or '').strip().lower()
        if country and country not in {'bd', 'bangladesh'}:
            return False

        location_text = ' '.join(
            str(tags.get(key, ''))
            for key in (
                'addr:state', 'addr:province', 'addr:district', 'addr:city',
                'addr:town', 'addr:county', 'is_in', 'is_in:state'
            )
        ).lower()
        blocked_border_regions = (
            'assam', 'tripura', 'meghalaya', 'mizoram', 'west bengal', 'india'
        )
        return not any(region in location_text for region in blocked_border_regions)

    def _osm_address(self, tags):
        parts = [
            tags.get('addr:housenumber'),
            tags.get('addr:street'),
            tags.get('addr:suburb'),
            tags.get('addr:city') or tags.get('addr:town') or tags.get('addr:village'),
            tags.get('addr:district'),
        ]
        return ', '.join(part.strip() for part in parts if part and str(part).strip())

    def _identify_osm_type(self, tags):
        amenity = (tags.get('amenity') or '').lower()
        name = ' '.join(str(tags.get(key, '')) for key in ('name', 'name:en', 'official_name')).lower()
        if 'madrasa' in name or 'madrasah' in name:
            return 'Madrasa'
        if amenity == 'university':
            return 'University'
        if amenity == 'college':
            return 'College'
        if amenity == 'kindergarten':
            return 'Kindergarten'
        if tags.get('training'):
            return 'Training Center'
        return 'School'

    def _normalize_email_value(self, email):
        if not email:
            return None
        matches = self._EMAIL_PATTERN.findall(str(email))
        return matches[0].lower() if matches else None

    def collect_from_linkedin(self):
        logger.info("LinkedIn collection skipped: use official API-approved sources only")
        return 0

    def enrich_missing_contact_info(self, limit=500, find_email=False, commit_every=25):
        """Refresh Google details for leads missing phone, website, status, or place_id."""
        stats = {
            'checked': 0,
            'updated': 0,
            'skipped': 0,
            'quota_available': True
        }
        if not self.google_maps_api_key:
            logger.info("Contact enrichment skipped: GOOGLE_MAPS_API_KEY missing")
            stats['quota_available'] = False
            return stats
        if not can_use_api('google_places', Config.GOOGLE_PLACES_DAILY_CALL_LIMIT):
            logger.info("Contact enrichment skipped: Google Places daily API call limit reached")
            stats['quota_available'] = False
            return stats

        cutoff = datetime.utcnow() - timedelta(days=7)
        leads = Lead.query.filter(
            Lead.source == 'google_maps',
            or_(Lead.active_status == None, Lead.active_status != 'closed'),
            or_(Lead.last_enriched_at == None, Lead.last_enriched_at < cutoff),
            or_(
                Lead.phone == None,
                Lead.phone == '',
                Lead.website == None,
                Lead.website == '',
                Lead.place_id == None,
                Lead.business_status == None
            )
        ).limit(min(limit, 100)).all()

        for lead in leads:
            try:
                stats['checked'] += 1
                if not self.google_places_available or not can_use_api('google_places', Config.GOOGLE_PLACES_DAILY_CALL_LIMIT):
                    logger.info("Contact enrichment paused: Google Places quota exhausted mid-run")
                    stats['quota_available'] = False
                    stats['skipped'] += len(leads) - stats['checked'] + 1
                    break
                details = self._lookup_place_details(lead.school_name, lead.district, lead.place_id)
                lead.last_enriched_at = datetime.utcnow()
                if not details:
                    stats['skipped'] += 1
                    continue

                changed = self._apply_place_details_to_lead(lead, details)
                if find_email and lead.website and not lead.email:
                    email = self.find_emails(lead.website, lead.school_name)
                    lead.email_checked_at = datetime.utcnow()
                    if email and not self._email_used_by_other_lead(email, lead.id):
                        lead.email = email.lower()
                        refresh_lead_contact_fields(lead)
                        changed = True

                if changed:
                    stats['updated'] += 1
                if stats['checked'] and stats['checked'] % commit_every == 0:
                    db.session.commit()

                time.sleep(random.uniform(0.2, 0.7))
            except Exception as e:
                db.session.rollback()
                stats['skipped'] += 1
                logger.warning(f"Contact enrichment failed for {lead.school_name}: {e}")

        db.session.commit()
        logger.info(f"Contact enrichment complete: {stats}")
        return stats

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
            'admissions', 'apply', 'enquiry', 'inquiry', 'support', 'privacy-policy',
            'contact.php', 'contact-us.php', 'about.php', 'about-us.php',
            'admission.php', 'admissions.php', 'page/contact-us', 'pages/contact',
            'contact/index.php', 'wp/contact', 'wp/contact-us'
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

            target = urljoin(current_url, href).split('#', 1)[0].rstrip('/')
            parsed = urlparse(target)
            target_host = parsed.netloc.lower().replace('www.', '')
            is_facebook_page = target_host.endswith('facebook.com') or target_host.endswith('fb.com')
            if is_facebook_page and any(blocked in target for blocked in ('/share', '/sharer', '/plugins')):
                continue
            if not wanted.search(f"{href} {label}") and not is_facebook_page:
                continue
            if target_host and target_host != current_host and not is_facebook_page:
                continue
            links.append(target)

        return list(dict.fromkeys(links))[:8]

    def _extract_emails_from_html(self, html, soup):
        values = []
        for link in soup.select('a[href^="mailto:"]'):
            values.append(unquote(link.get('href', '')))
            values.append(link.get_text(' ', strip=True))
        for protected in soup.select('[data-cfemail]'):
            decoded = self._decode_cloudflare_email(protected.get('data-cfemail', ''))
            if decoded:
                values.append(decoded)

        for script in soup(['script', 'style']):
            script.decompose()
        values.append(soup.get_text(' ', strip=True))
        values.append(html)

        emails = []
        for value in values:
            normalized = self._normalize_obfuscated_email_text(value)
            emails.extend(self._EMAIL_PATTERN.findall(normalized))
        return emails

    def _decode_cloudflare_email(self, encoded):
        try:
            if not encoded or len(encoded) < 4:
                return None
            key = int(encoded[:2], 16)
            return ''.join(
                chr(int(encoded[i:i + 2], 16) ^ key)
                for i in range(2, len(encoded), 2)
            )
        except Exception:
            return None

    def _normalize_obfuscated_email_text(self, value):
        text = unescape(value or '')
        replacements = [
            (r'\s*(?:\[|\()\s*at\s*(?:\]|\))\s*', '@'),
            (r'\s+at\s+', '@'),
            (r'\s*(?:\[|\()\s*dot\s*(?:\]|\))\s*', '.'),
            (r'\s+dot\s+', '.'),
            (r'\s*(?:\[|\()\s*email\s*(?:\]|\))\s*', '@'),
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

    def _email_used_by_other_lead(self, email, lead_id):
        if not email:
            return False
        return db.session.query(Lead.id).filter(
            Lead.id != lead_id,
            db.func.lower(Lead.email) == email.lower()
        ).first() is not None

    def _find_phone_from_website(self, website, country_code='BD'):
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
                    phone_info = normalize_phone(value, country_code)
                    if phone_info['phone_valid']:
                        return phone_info['phone']
            except Exception as e:
                logger.debug(f"Website phone lookup failed for {url}: {e}")

        return None

    def _find_social_profiles_from_website(self, website):
        profiles = {'facebook_url': None, 'instagram_url': None}
        try:
            response = requests.get(
                website,
                timeout=8,
                allow_redirects=True,
                headers={'User-Agent': 'Mozilla/5.0 (compatible; PathshalaPro prospect enrichment)'},
            )
            if response.status_code >= 400:
                return profiles
            soup = BeautifulSoup(response.text, 'html.parser')
            for link in soup.select('a[href]'):
                href = urljoin(response.url, link.get('href', '')).split('?', 1)[0]
                host = urlparse(href).netloc.lower()
                if not profiles['facebook_url'] and ('facebook.com' in host or host == 'fb.com'):
                    if not any(path in href.lower() for path in ('/share', '/sharer', '/plugins')):
                        profiles['facebook_url'] = href[:255]
                if not profiles['instagram_url'] and 'instagram.com' in host:
                    profiles['instagram_url'] = href[:255]
                if all(profiles.values()):
                    break
        except Exception as e:
            logger.debug(f"Website social profile lookup failed for {website}: {e}")
        return profiles

    def enrich_missing_emails(self, limit=50, commit_every=10, force=False):
        """Enrich a small quota-aware batch of active leads with websites."""
        stats = {
            'checked': 0,
            'updated': 0,
            'phones_updated': 0,
            'social_profiles_updated': 0,
            'skipped_no_website': 0,
            'hunter_searches': 0,
            'force': force
        }
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

        hunter_searches = 0
        for lead in leads:
            allow_hunter = hunter_searches < Config.HUNTER_SEARCHES_PER_RUN
            email, hunter_used = self._find_email_candidate(lead.website, lead.school_name, allow_hunter=allow_hunter)
            if hunter_used:
                hunter_searches += 1
                stats['hunter_searches'] = hunter_searches
            lead.email_checked_at = datetime.utcnow()
            stats['checked'] += 1
            changed = False
            if email and not self._email_used_by_other_lead(email, lead.id):
                lead.email = email.lower()
                changed = True
            if lead.market == 'usa_local_business':
                if not lead.phone_valid:
                    phone = self._find_phone_from_website(lead.website, lead.country_code or 'US')
                    if phone:
                        lead.phone = phone
                        stats['phones_updated'] += 1
                        changed = True
                if not lead.facebook_url or not lead.instagram_url:
                    profiles = self._find_social_profiles_from_website(lead.website)
                    for field, value in profiles.items():
                        if value and not getattr(lead, field):
                            setattr(lead, field, value)
                            stats['social_profiles_updated'] += 1
                            changed = True
            if changed:
                refresh_lead_contact_fields(lead)
                stats['updated'] += 1
            if stats['checked'] % commit_every == 0:
                db.session.commit()

        db.session.commit()
        logger.info(f"Email enrichment complete: {stats}")
        return stats

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
