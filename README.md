# PathshalaPro Marketing Automation Bot

সম্পূর্ণ স্বয়ংক্রিয় মার্কেটিং বট যা ২৪/৭ লিড সংগ্রহ এবং এনগেজমেন্ট করে।

## বৈশিষ্ট্য

✓ **রাত্রিকালীন লিড সংগ্রহ (১-৫ AM)**
  - Google Maps থেকে স্কুল খুঁজে পাওয়া
  - Email খুঁজে পাওয়া (Hunter.io + ওয়েবসাইট স্ক্র্যাপিং)
  - Facebook গ্রুপ / LinkedIn সংগ্রহ — _অফিসিয়াল API access প্রয়োজন, এখনো implement করা হয়নি (stub)_

✓ **স্বয়ংক্রিয় ইমেইল ক্যাম্পেইন (সকাল ১০:३० AM)**
  - পার্সোনালাইজড HTML ইমেইল (clickable লিঙ্ক সহ)
  - লিড স্কোরিং এবং সেগমেন্টেশন
  - ইমেইল ওপেন ট্র্যাকিং (tracking pixel + Brevo webhook)
  - ফলো-আপ ড্রিপ সিকোয়েন্স (বিকেল ৪:০০ PM)

✓ **হোয়াটসঅ্যাপ মেসেজিং (দুপুর १२:३० PM)**
  - সব qualified WhatsApp-ready লিডে মেসেজ (ডিফল্ট)
  - ডেলিভারি ট্র্যাকিং

✓ **Facebook পোস্টিং (দুপুর २ PM)**
  - Google Sheet ('FacebookPosts' worksheet) থেকে কন্টেন্ট, না থাকলে ডিফল্ট

✓ **রিয়েল-টাইম ড্যাশবোর্ড**
  - প্রতিদিন মেট্রিক্স ট্র্যাকিং
  - Google Sheets এ আপডেট

## প্রয়োজনীয়তা

- Python 3.8+
- PostgreSQL (Railway.app এ ফ্রি)
- API Keys:
  - Google Maps API
  - SendGrid
  - Twilio
  - Hunter.io
  - Facebook Graph API

## ইনস্টলেশন

### লোকাল সেটআপ

```bash
# রিপোজিটরি ক্লোন করা
git clone https://github.com/YOUR_USERNAME/pathshalapro-marketing-bot.git
cd pathshalapro-marketing-bot

# Virtual environment তৈরি করা
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# ডিপেন্ডেন্সি ইনস্টল করা
pip install -r requirements.txt

# .env ফাইল তৈরি করা
cp .env.example .env

# .env এ আপনার API keys পূরণ করা
nano .env  # বা আপনার প্রিয় এডিটর দিয়ে খুলুন
```

### API Keys সংগ্রহ করা

**Google Maps API:**
1. https://console.cloud.google.com এ যান
2. নতুন প্রজেক্ট তৈরি করুন
3. Maps API enable করুন
4. API Key তৈরি করুন

**SendGrid:**
1. https://sendgrid.com এ সাইন আপ করুন
2. Settings → API Keys এ API Key তৈরি করুন

**Twilio:**
1. https://twilio.com এ সাইন আপ করুন
2. Account SID এবং Auth Token কপি করুন

**Hunter.io:**
1. https://hunter.io এ সাইন আপ করুন
2. Dashboard থেকে API Key পান

**Facebook:**
1. https://developers.facebook.com এ যান
2. Facebook App তৈরি করুন
3. Page Access Token জেনারেট করুন

**PostgreSQL (Railway):**
1. https://railway.app এ সাইন আপ করুন
2. নতুন প্রজেক্ট → Add Service → PostgreSQL
3. Database URL কপি করুন

## ব্যবহার

### লোকালে রান করা

```bash
python main.py
```

### Heroku তে ডিপ্লয় করা

```bash
# Heroku CLI ইনস্টল করুন: https://devcenter.heroku.com/articles/heroku-cli

# Heroku এ সাইন ইন করুন
heroku login

# নতুন Heroku অ্যাপ তৈরি করুন
heroku create pathshalapro-bot

# Environment variables সেট করুন
heroku config:set GOOGLE_MAPS_API_KEY=your_key
heroku config:set SENDGRID_API_KEY=your_key
# ... ইত্যাদি সব keys

# PostgreSQL অ্যাড করুন
heroku addons:create heroku-postgresql:hobby-dev

# ডিপ্লয় করুন
git push heroku main
```

## ফাইল স্ট্রাকচার

```
pathshalapro-marketing-bot/
├── main.py                    # মেইন স্ক্রিপ্ট
├── config.py                  # কনফিগারেশন
├── requirements.txt           # ডিপেন্ডেন্সি
├── .env.example              # এনভায়রনমেন্ট ভেরিয়েবল উদাহরণ
├── .gitignore
├── Procfile                   # Heroku কনফিগ
├── README.md
└── src/
    ├── database.py           # ডাটাবেস অপারেশনস
    ├── lead_collection.py    # লিড সংগ্রহ
    ├── email_campaign.py     # ইমেইল ক্যাম্পেইন
    ├── whatsapp_campaign.py  # WhatsApp মেসেজিং
    ├── facebook_posting.py   # Facebook পোস্টিং
    ├── tracking.py           # ট্র্যাকিং
    └── reporting.py          # রিপোর্টিং
```

## ডেটাবেস স্ট্রাকচার

### Leads টেবিল

```sql
CREATE TABLE leads (
    id SERIAL PRIMARY KEY,
    school_name VARCHAR(255),
    phone VARCHAR(20),
    email VARCHAR(255),
    address TEXT,
    district VARCHAR(100),
    type VARCHAR(50),  -- School/Coaching/Madrasa
    source VARCHAR(100),  -- google_maps/facebook/linkedin
    score INTEGER DEFAULT 0,
    segment VARCHAR(20),  -- Hot/Warm/Cold
    email_sent BOOLEAN DEFAULT FALSE,
    email_sent_date TIMESTAMP,
    email_opened BOOLEAN DEFAULT FALSE,
    email_opened_time TIMESTAMP,
    email_clicked BOOLEAN DEFAULT FALSE,
    whatsapp_sent BOOLEAN DEFAULT FALSE,
    whatsapp_sent_date TIMESTAMP,
    whatsapp_delivered BOOLEAN DEFAULT FALSE,
    whatsapp_read BOOLEAN DEFAULT FALSE,
    status VARCHAR(50),  -- pending/engaged/converted/objection
    conversion_date TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## শিডিউল

- **রাত ১:০০ AM** - Google Maps Scraping
- **রাত ১:३० AM** - Facebook Group Scraping
- **রাত २:०० AM** - Email খুঁজে পাওয়া
- **রাত २:३० AM** - LinkedIn Search
- **রাত ३:००AM** - Data Cleaning
- **রাত ४:००AM** - Score Calculation
- **সকাল ९:००AM** - Lead Scoring & Segmentation
- **সকাল १०:३० AM** - Email Campaign
- **দুপুর १२:३० PM** - WhatsApp Messages
- **দুপুর २:००PM** - Facebook Posting
- **রাত ८:००PM** - Email Tracking
- **রাত ९:३०PM** - Daily Report & Dashboard Update

## খরচ

মাসিক খরচ: ২০০-१,२००টাকা (মূলত WhatsApp মেসেজিং এর জন্য)

## ট্রাবলশুটিং

**ইমেইল পাঠানো হচ্ছে না:**
```bash
# SendGrid API key চেক করুন
python -c "from src.email_campaign import test_sendgrid; test_sendgrid()"
```

**ডাটাবেস সংযোগ সমস্যা:**
```bash
# DATABASE_URL চেক করুন
echo $DATABASE_URL
```

**হোয়াটসঅ্যাপ মেসেজ না আসা:**
- Twilio Account SID এবং Auth Token সঠিক কিনা চেক করুন
- ট্রায়াল অ্যাকাউন্ট থাকলে verified numbers এ পাঠানো যায়

## সাপোর্ট

যেকোনো সমস্যার জন্য Issues এ রিপোর্ট করুন।

## লাইসেন্স

MIT

## লেখক

PathshalaPro Team

---

**প্রথম সময় রান করার আগে .env ফাইল সেট করুন!**
