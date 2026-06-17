# PathshalaPro মার্কেটিং বট - সম্পূর্ণ সেটআপ গাইড

## ধাপ ১: GitHub থেকে রিপোজিটরি ক্লোন করা

```bash
git clone https://github.com/YOUR_USERNAME/pathshalapro-marketing-bot.git
cd pathshalapro-marketing-bot
```

## ধাপ २: Python Environment সেটআপ করা

### Windows এ:
```bash
python -m venv venv
venv\Scripts\activate
```

### Mac/Linux এ:
```bash
python3 -m venv venv
source venv/bin/activate
```

## ধাপ ३: Dependencies ইনস্টল করা

```bash
pip install -r requirements.txt
```

## ধাপ ४: API Keys সংগ্রহ করা

### A. Google Maps API Key

1. https://console.cloud.google.com এ যান
2. নতুন প্রজেক্ট তৈরি করুন
3. "Maps API" সার্চ করুন এবং enable করুন
4. Credentials → Create Credentials → API Key
5. Copy করুন

### B. SendGrid API Key

1. https://sendgrid.com সাইন আপ করুন (ফ্রি অ্যাকাউন্ট)
2. Settings → API Keys এ যান
3. Create API Key ক্লিক করুন
4. Full Access দিয়ে create করুন
5. Copy করুন

### C. Twilio Account (WhatsApp এর জন্য)

1. https://www.twilio.com সাইন আপ করুন
2. Console Dashboard থেকে Account SID ও Auth Token কপি করুন
3. Messaging → Try it out → Messaging Service তৈরি করুন
4. WhatsApp সাথে সংযোগ করুন

### D. Hunter.io API Key

1. https://hunter.io সাইন আপ করুন (ফ্রি অ্যাকাউন্ট)
2. Dashboard এ API মেনু খান
3. API Key কপি করুন

### E. Facebook Page এবং Access Token

1. https://www.facebook.com/business/tools/meta-business-suite এ যান
2. আপনার পেজ সিলেক্ট করুন
3. Settings → Apps and Websites
4. Page ID এবং Page Access Token পান

### F. PostgreSQL Database (Railway.app তে ফ্রি)

1. https://railway.app সাইন আপ করুন
2. নতুন প্রজেক্ট তৈরি করুন
3. PostgreSQL Database add করুন
4. Database URL কপি করুন

## ধাপ ५: .env ফাইল তৈরি করা

```bash
cp .env.example .env
```

এবার `.env` ফাইল খুলে সব keys পূরণ করুন:

```
DATABASE_URL=postgresql://user:password@host:port/dbname
GOOGLE_MAPS_API_KEY=your_key_here
SENDGRID_API_KEY=your_key_here
TWILIO_ACCOUNT_SID=your_sid_here
TWILIO_AUTH_TOKEN=your_token_here
TWILIO_WHATSAPP_NUMBER=+1234567890
HUNTER_API_KEY=your_key_here
FACEBOOK_PAGE_ID=your_page_id
FACEBOOK_PAGE_ACCESS_TOKEN=your_token_here
FROM_EMAIL=marketing@pathshalapro.net
WHATSAPP_FROM_NUMBER=+88015XXXXXXX
ENVIRONMENT=development
DEBUG=False
```

## ধাপ ६: ডাটাবেস ইনিশিয়ালাইজ করা

```bash
python -c "from main import app, initialize_app; initialize_app()"
```

## ধাপ ७: লোকালে টেস্ট করা

```bash
python main.py
```

ব্রাউজারে খুলুন: http://localhost:5000

## ধাপ ८: Heroku এ ডিপ্লয় করা (লাইভ সার্ভারের জন্য)

### Heroku সেটআপ:

```bash
# Heroku CLI ডাউনলোড করুন: https://devcenter.heroku.com/articles/heroku-cli

# Heroku এ লগইন করুন
heroku login

# নতুন অ্যাপ তৈরি করুন
heroku create pathshalapro-bot

# PostgreSQL ডাটাবেস add করুন
heroku addons:create heroku-postgresql:hobby-dev

# Environment variables সেট করুন
heroku config:set GOOGLE_MAPS_API_KEY=your_key
heroku config:set SENDGRID_API_KEY=your_key
heroku config:set TWILIO_ACCOUNT_SID=your_sid
heroku config:set TWILIO_AUTH_TOKEN=your_token
heroku config:set TWILIO_WHATSAPP_NUMBER=your_number
heroku config:set HUNTER_API_KEY=your_key
heroku config:set FACEBOOK_PAGE_ID=your_page_id
heroku config:set FACEBOOK_PAGE_ACCESS_TOKEN=your_token
heroku config:set FROM_EMAIL=marketing@pathshalapro.net
heroku config:set WHATSAPP_FROM_NUMBER=your_whatsapp_number
heroku config:set ENVIRONMENT=production

# ডিপ্লয় করুন
git push heroku main

# লগ দেখুন
heroku logs --tail
```

## ধাপ ९: Webhook সেটআপ করা (SendGrid এবং Twilio এর জন্য)

### SendGrid Webhook:

1. SendGrid Dashboard এ যান
2. Settings → Mail Send (প্রতিটি event সিলেক্ট করুন)
3. HTTP Post URL এ এটি লাগান:
   ```
   https://your-app-name.herokuapp.com/webhooks/sendgrid
   ```

### Twilio Webhook:

1. Twilio Console এ যান
2. Messaging → Services → আপনার service সিলেক্ট করুন
3. Integration → Webhooks
4. URL এ এটি লাগান:
   ```
   https://your-app-name.herokuapp.com/webhooks/twilio
   ```

## ধাপ १०: ম্যানুয়াল টেস্টিং

লোকালে কমান্ড দিয়ে টেস্ট করুন:

```bash
# লিড সংগ্রহ টেস্ট করা
curl -X POST http://localhost:5000/trigger/lead-collection

# ইমেইল ক্যাম্পেইন টেস্ট করা
curl -X POST http://localhost:5000/trigger/email-campaign

# হোয়াটসঅ্যাপ ক্যাম্পেইন টেস্ট করা
curl -X POST http://localhost:5000/trigger/whatsapp-campaign

# স্ট্যাটিস্টিক্স দেখা
curl http://localhost:5000/stats
```

## ধাপ ११: শিডিউলড জবস দেখা

```bash
curl http://localhost:5000/scheduler/jobs
```

## ট্রাবলশুটিং

### ইমেইল পাঠানো হচ্ছে না
- SendGrid API Key চেক করুন
- `DEBUG=True` সেট করে লগ দেখুন

### হোয়াটসঅ্যাপ মেসেজ না আসা
- Twilio credentials চেক করুন
- Trial account তাহলে verified numbers এ পাঠাতে পারবেন শুধু
- সিমুলেটর নাম্বার দিয়ে টেস্ট করুন

### ডাটাবেস কানেকশন এরর
- DATABASE_URL চেক করুন
- Railway থেকে নতুন URL কপি করুন

### Google Maps এরর
- API quota চেক করুন
- Billing enable করা আছে কিনা দেখুন

## প্রথম রান করার পরে কি হবে

**রাত ১ AM:**
- Bot শুরু হবে Google Maps থেকে লিড খুঁজতে
- প্রতিদিন রাত ১-৫ AM এ নতুন লিড সংগ্রহ করবে

**সকাল ১০:३० AM:**
- সংগৃহীত লিডদের কাছে পার্সোনালাইজড ইমেইল যাবে

**দুপুর १२:३० PM:**
- যারা ইমেইল খুলেছে তাদের কাছে হোয়াটসঅ্যাপ মেসেজ যাবে

**দুপুর २ PM:**
- আপনার লেখা কনটেন্ট ফেসবুকে পোস্ট হবে

**রাত ९:३० PM:**
- দৈনিক রিপোর্ট তৈরি হবে এবং Google Sheets এ আপডেট হবে

---

## ফাইল স্ট্রাকচার সম্পর্কে

```
pathshalapro-marketing-bot/
├── main.py                 # মেইন অ্যাপ (সার্ভার এবং শিডিউলার)
├── config.py               # কনফিগারেশন সেটিংস
├── requirements.txt        # Python dependencies
├── .env.example           # Environment variables উদাহরণ
├── Procfile               # Heroku এর জন্য
├── README.md              # এই ফাইল
└── src/
    ├── __init__.py
    ├── database.py         # ডাটাবেস মডেলস
    ├── lead_collection.py  # লিড সংগ্রহ
    ├── email_campaign.py   # ইমেইল পাঠানো
    ├── whatsapp_campaign.py # হোয়াটসঅ্যাপ মেসেজ
    ├── facebook_posting.py  # ফেসবুক পোস্ট
    ├── tracking.py         # ট্র্যাকিং
    └── reporting.py        # রিপোর্ট জেনারেশন
```

## সাপোর্ট

কোন সমস্যা হলে:
1. লগ ফাইল দেখুন (`logs/` ফোল্ডার)
2. `.env` ফাইল সঠিক কিনা চেক করুন
3. API keys valid কিনা verify করুন
4. GitHub Issues এ রিপোর্ট করুন

---

**উপভোগ করুন সম্পূর্ণ অটোমেটেড মার্কেটিং! 🚀**
