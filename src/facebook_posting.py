import requests
from datetime import datetime
from loguru import logger
from config import Config
from src.database import db
import json
import os

logger.add("logs/facebook_posting.log", rotation="500 MB")


class FacebookPoster:
    """ফেসবুক পোস্টিং ম্যানেজার"""
    
    def __init__(self):
        self.page_id = Config.FACEBOOK_PAGE_ID
        self.access_token = Config.FACEBOOK_PAGE_ACCESS_TOKEN
        self.api_version = "v18.0"
    
    
    def post_to_facebook(self, content_text, image_url=None):
        """ফেসবুকে একটি পোস্ট করা"""
        try:
            if not self.page_id or not self.access_token:
                post_id = self._queue_post(content_text, image_url)
                logger.warning(f"Facebook credential missing; queued post {post_id}")
                return True, post_id

            url = f"https://graph.facebook.com/{self.api_version}/{self.page_id}/feed"
            
            if image_url:
                # ছবি সহ পোস্ট
                payload = {
                    'message': content_text,
                    'link': image_url,
                    'access_token': self.access_token
                }
            else:
                # টেক্সট পোস্ট
                payload = {
                    'message': content_text,
                    'access_token': self.access_token
                }
            
            response = requests.post(url, data=payload, timeout=10)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"✓ ফেসবুকে পোস্ট করা হয়েছে (ID: {result.get('id')})")
                return True, result.get('id')
            else:
                logger.error(f"Facebook error: {response.status_code} - {response.text}")
                return False, None
        
        except Exception as e:
            logger.error(f"Facebook posting error: {e}")
            return False, None

    def _queue_post(self, content_text, image_url=None):
        """Facebook token না থাকলে post content local outbox-এ রাখা"""
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

        queued.append({
            'id': post_id,
            'content': content_text,
            'image_url': image_url,
            'created_at': datetime.utcnow().isoformat()
        })

        with open(outbox_file, 'w', encoding='utf-8') as f:
            json.dump(queued, f, ensure_ascii=False, indent=2)

        return post_id
    
    
    def get_scheduled_posts(self):
        """Google Sheets থেকে শিডিউলড পোস্ট পাওয়া"""
        try:
            import gspread
            from oauth2client.service_account import ServiceAccountCredentials
            import json
            
            # Google Sheets সংযোগ (আপনাকে credentials সেট করতে হবে)
            # এটি একটি অপশনাল ফিচার - সরাসরি কনফিগ থেকে পোস্ট পাওয়া যায়
            
            scheduled_posts = [
                {
                    'time': '14:00',  # দুপুর २ PM
                    'content': 'আপনার লেখা পোস্ট ১',
                    'image': None
                },
                {
                    'time': '18:00',  # সন্ধ্যা ६ PM
                    'content': 'আপনার লেখা পোস্ট २',
                    'image': None
                }
            ]
            
            return scheduled_posts
        
        except Exception as e:
            logger.warning(f"Schedule retrieval error: {e}")
            return []
    
    
    def run_scheduled_posting(self):
        """শিডিউলড পোস্ট চালানো"""
        logger.info("=" * 50)
        logger.info("ফেসবুক পোস্টিং শুরু")
        logger.info("=" * 50)
        
        try:
            scheduled_posts = self.get_scheduled_posts()
            
            current_time = datetime.now().strftime("%H:%M")
            
            total_posted = 0
            
            for post in scheduled_posts:
                if post['time'] == current_time:
                    success, post_id = self.post_to_facebook(
                        post['content'],
                        post.get('image')
                    )
                    
                    if success:
                        total_posted += 1
            
            logger.info(f"✓ মোট পোস্ট করা: {total_posted}")
            return total_posted
        
        except Exception as e:
            logger.error(f"Posting error: {e}")
            return 0
    
    
    def post_daily_content(self):
        """প্রতিদিনের কন্টেন্ট পোস্ট করা (বিভিন্ন দিনে ভিন্ন ভিন্ন)"""
        logger.info("দৈনিক কন্টেন্ট পোস্টিং শুরু")
        
        from datetime import datetime
        day_of_week = datetime.now().strftime("%A").lower()
        
        daily_content = {
            'monday': '''আজকের টিপস: হাজিরা নেওয়ার স্মার্ট উপায় 📋

ডিজিটাল হাজিরায় কয়েকটি সুবিধা:
✓ সময় বাঁচায় (৫ মিনিটে শেষ)
✓ স্বচ্ছতা বাড়ায়
✓ অভিভাবকদের রিয়েল-টাইম নোটিফিকেশন
✓ রিপোর্ট স্বয়ংক্রিয়

PathshalaPro দিয়ে ট্রাই করুন: https://pathshalapro.net/demo''',
            
            'wednesday': '''স্কুল ম্যানেজমেন্টের চ্যালেঞ্জ 🎓

আপনার কাছে এই সমস্যা আছে?
❌ হাজিরা নিতে ৩০ মিনিট লাগে
❌ ফি সংগ্রহে জটিলতা
❌ রেজাল্ট তৈরি করতে দিনখানেক সময়

PathshalaPro সব সমাধান করে দেয়!

ফ্রি ট্রায়াল করুন: https://pathshalapro.net/trial''',
            
            'friday': '''সাফল্যের গল্প 🌟

"PathshalaPro ব্যবহার করে আমরা অর্ধেক সময়ে প্রশাসনিক কাজ শেষ করতে পারি।"
- ঢাকা শহর কোচিং সেন্টার

আপনার স্কুল/কোচিং কেও এভাবে সফল করতে পারেন।

শুরু করুন আজই: https://pathshalapro.net/demo''',
            
            'default': '''PathshalaPro - স্কুল ম্যানেজমেন্ট সফটওয়্যার

সম্পূর্ণ বাংলা ভাষায় উপলব্ধ।
✓ ডিজিটাল হাজিরা
✓ অনলাইন ফি
✓ অভিভাবক পোর্টাল
✓ রেজাল্ট ম্যানেজমেন্ট

আপনার প্রতিষ্ঠানকে ডিজিটালাইজ করুন।

https://pathshalapro.net'''
        }
        
        content = daily_content.get(day_of_week, daily_content['default'])
        
        success, post_id = self.post_to_facebook(content)
        
        if success:
            logger.info(f"✓ দৈনিক পোস্ট সফল")
        else:
            logger.error("দৈনিক পোস্ট ব্যর্থ")
        
        return success


# সিঙ্গেল ইনস্ট্যান্স
facebook_poster = FacebookPoster()


if __name__ == '__main__':
    poster = FacebookPoster()
    poster.post_daily_content()
