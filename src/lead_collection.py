import requests
import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
from datetime import datetime
from loguru import logger
from config import Config
from src.database import Lead, add_lead, db
import json
import random
import re

logger.add("logs/lead_collection.log", rotation="500 MB")


class LeadCollector:
    """লিড সংগ্রহের মূল ক্লাস"""
    
    def __init__(self):
        self.google_maps_api_key = Config.GOOGLE_MAPS_API_KEY
        self.hunter_api_key = Config.HUNTER_API_KEY
        
    def collect_from_google_maps(self):
        """Google Maps API থেকে স্কুল খুঁজে পাওয়া"""
        logger.info("শুরু: Google Maps থেকে লিড সংগ্রহ...")
        
        districts = ['Dhaka', 'Chittagong', 'Sylhet', 'Khulna', 'Rajshahi']
        keywords = ['school', 'coaching center', 'madrasa', 'kindergarten']
        
        total_collected = 0
        
        for district in districts:
            for keyword in keywords:
                try:
                    # Google Maps সার্চ কোয়েরি
                    search_query = f"{keyword} in {district}, Bangladesh"
                    
                    url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
                    params = {
                        'query': search_query,
                        'key': self.google_maps_api_key
                    }
                    
                    response = requests.get(url, params=params, timeout=10)
                    results = response.json().get('results', [])
                    
                    for place in results:
                        try:
                            # প্লেস ডিটেইলস পাওয়া
                            place_id = place['place_id']
                            name = place.get('name', '')
                            address = place.get('formatted_address', '')
                            
                            # ফোন নম্বর পাওয়ার জন্য place details API কল করা
                            details_url = "https://maps.googleapis.com/maps/api/place/details/json"
                            details_params = {
                                'place_id': place_id,
                                'fields': 'phone_number,website,formatted_address',
                                'key': self.google_maps_api_key
                            }
                            
                            details_response = requests.get(details_url, params=details_params, timeout=10)
                            place_details = details_response.json().get('result', {})
                            
                            phone = place_details.get('phone_number', '')
                            website = place_details.get('website', '')
                            email = self.find_emails(website, name) if website else None
                            
                            # লিড ডাটাবেসে যোগ করা
                            if name and (phone or address):
                                lead = add_lead(
                                    school_name=name,
                                    phone=phone.replace('+880', '0') if phone else None,
                                    email=email,
                                    district=district,
                                    type=self._identify_type(keyword),
                                    source='google_maps',
                                    website=website,
                                    address=address
                                )
                                
                                if lead:
                                    total_collected += 1
                                    logger.info(f"✓ সংগৃহীত: {name} ({district})")
                            
                            # Google Maps API রেট লিমিট এড়ানোর জন্য
                            time.sleep(random.uniform(0.5, 1.5))
                        
                        except Exception as e:
                            logger.warning(f"Place error: {e}")
                            continue
                    
                except Exception as e:
                    logger.error(f"Google Maps error: {e}")
                    continue
        
        logger.info(f"✓ Google Maps সংগ্রহ সম্পূর্ণ: {total_collected} লিড")
        return total_collected
    
    
    def collect_from_facebook_groups(self):
        """Facebook গ্রুপ থেকে লিড সংগ্রহ"""
        logger.info("শুরু: Facebook গ্রুপ থেকে লিড সংগ্রহ...")
        
        groups = [
            'bd-school-principals',
            'coaching-centers-bd',
            'teachers-community-bd',
            'bangladesh-education'
        ]
        
        total_collected = 0
        
        # নোট: Facebook সরাসরি স্ক্র্যাপিং এ সীমাবদ্ধতা আছে
        # এই অংশটি ম্যানুয়াল বা ফেসবুক অ্যাপি দিয়ে করা উচিত
        logger.warning("Facebook স্ক্র্যাপিং: Facebook API ব্যবহার করা সুপারিশ করা হয়")
        
        return total_collected
    
    
    def find_emails(self, domain, school_name=None):
        """Hunter.io বা public website scrape করে ইমেইল খুঁজে পাওয়া"""
        try:
            if not domain or not domain.startswith('http'):
                return None
            
            # URL থেকে ডোমেইন এক্সট্র্যাক্ট করা
            from urllib.parse import urlparse
            domain_name = urlparse(domain).netloc
            
            if self.hunter_api_key and Config.EMAIL_FINDER_PROVIDER in ('hunter', 'auto'):
                hunter_email = self._find_email_with_hunter(domain_name)
                if hunter_email:
                    return hunter_email

            return self._find_email_from_website(domain)
        
        except Exception as e:
            logger.warning(f"Email finder error: {e}")
            return None

    def _find_email_with_hunter(self, domain_name):
        """Hunter API থাকলে domain search করা"""
        url = "https://api.hunter.io/v2/domain-search"
        params = {
            'domain': domain_name,
            'limit': 10,
            'api_key': self.hunter_api_key
        }

        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if data.get('data'):
            emails = data['data'].get('emails', [])
            if emails:
                return emails[0]['value']
        return None

    def _find_email_from_website(self, website):
        """Public website pages থেকে mailto/text email খোঁজা"""
        candidates = [website.rstrip('/')]
        for path in ('contact', 'contact-us', 'about', 'about-us'):
            candidates.append(f"{website.rstrip('/')}/{path}")

        email_pattern = re.compile(r'[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}', re.IGNORECASE)
        blocked_domains = {'example.com', 'domain.com'}

        for url in candidates:
            try:
                response = requests.get(
                    url,
                    timeout=10,
                    headers={'User-Agent': 'Mozilla/5.0 PathshalaPro lead enrichment'}
                )
                if response.status_code >= 400:
                    continue

                emails = []
                soup = BeautifulSoup(response.text, 'html.parser')
                for link in soup.select('a[href^="mailto:"]'):
                    emails.extend(email_pattern.findall(link.get('href', '')))
                emails.extend(email_pattern.findall(response.text))

                for email in emails:
                    email_domain = email.split('@')[-1].lower()
                    if email_domain not in blocked_domains and not email.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        return email
            except Exception as e:
                logger.debug(f"Website email lookup failed for {url}: {e}")

        return None

    def enrich_missing_emails(self, limit=50):
        """Website আছে কিন্তু email নেই এমন leads enrich করা"""
        leads = Lead.query.filter(
            Lead.email == None,
            Lead.website != None
        ).limit(limit).all()

        updated = 0
        for lead in leads:
            email = self.find_emails(lead.website, lead.school_name)
            if email:
                lead.email = email
                updated += 1

        db.session.commit()
        logger.info(f"Email enrichment updated {updated} leads")
        return updated
    
    
    def collect_from_linkedin(self):
        """LinkedIn থেকে স্কুল প্রিন্সিপাল খুঁজে পাওয়া"""
        logger.info("শুরু: LinkedIn থেকে লিড সংগ্রহ...")
        
        # নোট: LinkedIn সরাসরি স্ক্র্যাপিং প্রতিবন্ধী
        # LinkedIn API ব্যবহার করা উচিত (সীমিত অ্যাক্সেস)
        logger.warning("LinkedIn: Official API ব্যবহার করা উচিত")
        
        return 0
    
    
    def clean_and_score_leads(self):
        """লিড ক্লিন এবং স্কোর করা"""
        logger.info("শুরু: লিড ক্লিনিং এবং স্কোরিং...")
        
        from src.database import Lead
        
        # Duplicate রিমুভ করা (একই ফোন নম্বর)
        leads = Lead.query.all()
        processed_phones = set()
        duplicates_removed = 0
        
        for lead in leads:
            if lead.phone:
                if lead.phone in processed_phones:
                    db.session.delete(lead)
                    duplicates_removed += 1
                else:
                    processed_phones.add(lead.phone)
        
        db.session.commit()
        logger.info(f"✓ Duplicates রিমুভ করা: {duplicates_removed}")
        
        # লিড স্কোর ক্যালকুলেট করা
        leads = Lead.query.all()
        
        for lead in leads:
            score = 0
            
            # ডেটা উপস্থিতি
            if lead.email:
                score += 3
            if lead.phone:
                score += 2
            if lead.website:
                score += 2
            if lead.address:
                score += 1
            
            # সেগমেন্ট নির্ধারণ
            if score >= 5:
                segment = 'Hot'
            elif score >= 3:
                segment = 'Warm'
            else:
                segment = 'Cold'
            
            lead.score = score
            lead.segment = segment
        
        db.session.commit()
        logger.info("✓ লিড স্কোরিং সম্পূর্ণ")
        
        # স্ট্যাটিস্টিক্স
        hot = Lead.query.filter_by(segment='Hot').count()
        warm = Lead.query.filter_by(segment='Warm').count()
        cold = Lead.query.filter_by(segment='Cold').count()
        
        logger.info(f"স্কোর সারমর্ম: Hot={hot}, Warm={warm}, Cold={cold}")
    
    
    def _identify_type(self, keyword):
        """কীওয়ার্ড অনুযায়ী টাইপ নির্ধারণ করা"""
        keyword_lower = keyword.lower()
        
        if 'coaching' in keyword_lower or 'tuition' in keyword_lower:
            return 'Coaching'
        elif 'madrasa' in keyword_lower or 'madrasah' in keyword_lower:
            return 'Madrasa'
        elif 'kindergarten' in keyword_lower or 'kinder' in keyword_lower:
            return 'Kindergarten'
        else:
            return 'School'
    
    
    def run_all(self):
        """সব কালেকশন চালানো"""
        logger.info("=" * 50)
        logger.info("লিড কালেকশন সাইকেল শুরু")
        logger.info("=" * 50)
        
        try:
            # Google Maps থেকে সংগ্রহ
            maps_count = self.collect_from_google_maps()
            
            # Facebook থেকে সংগ্রহ (যদি সম্ভব)
            fb_count = self.collect_from_facebook_groups()
            
            # LinkedIn থেকে সংগ্রহ (যদি সম্ভব)
            linkedin_count = self.collect_from_linkedin()
            
            # ক্লিন এবং স্কোর করা
            self.clean_and_score_leads()
            
            total = maps_count + fb_count + linkedin_count
            logger.info(f"✓ মোট সংগৃহীত লিড: {total}")
            
            return total
        
        except Exception as e:
            logger.error(f"Lead collection error: {e}")
            return 0


# একটি সিঙ্গেল ইনস্ট্যান্স তৈরি করা
lead_collector = LeadCollector()


if __name__ == '__main__':
    # টেস্টিংয়ের জন্য
    collector = LeadCollector()
    collector.run_all()
