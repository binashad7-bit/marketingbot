import os
from dotenv import load_dotenv
from datetime import datetime

# .env ফাইল লোড করা
load_dotenv()


def normalize_database_url(url):
    """SQLAlchemy-এর জন্য PostgreSQL URL driver ঠিক করা"""
    if url.startswith('postgres://'):
        url = 'postgresql://' + url[len('postgres://'):]
    if url.startswith('postgresql://'):
        return 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


def _csv_list_env(name, default_values):
    """Read comma-separated env values while keeping a sane Python default."""
    raw = os.getenv(name)
    if not raw:
        return default_values
    return [item.strip() for item in raw.split(',') if item.strip()]


def _gemini_api_keys():
    """Collect Gemini keys from common names without logging secret values."""
    candidates = _csv_list_env('GEMINI_API_KEYS', [])
    candidates.extend([
        os.getenv('GEMINI_API_KEY'),
        os.getenv('GOOGLE_API_KEY'),
    ])
    candidates.extend(os.getenv(f'GOOGLE_API_KEY{index}') for index in range(1, 21))
    return list(dict.fromkeys(key.strip() for key in candidates if key and key.strip()))


BANGLADESH_DISTRICTS = [
    'Dhaka', 'Faridpur', 'Gazipur', 'Gopalganj', 'Kishoreganj', 'Madaripur',
    'Manikganj', 'Munshiganj', 'Narayanganj', 'Narsingdi', 'Rajbari',
    'Shariatpur', 'Tangail', 'Chattogram', "Cox's Bazar", 'Cumilla',
    'Brahmanbaria', 'Chandpur', 'Feni', 'Khagrachhari', 'Lakshmipur',
    'Noakhali', 'Rangamati', 'Bandarban', 'Rajshahi', 'Bogura', 'Joypurhat',
    'Naogaon', 'Natore', 'Chapainawabganj', 'Pabna', 'Sirajganj', 'Khulna',
    'Bagerhat', 'Chuadanga', 'Jashore', 'Jhenaidah', 'Kushtia', 'Magura',
    'Meherpur', 'Narail', 'Satkhira', 'Barishal', 'Barguna', 'Bhola',
    'Jhalokati', 'Patuakhali', 'Pirojpur', 'Sylhet', 'Habiganj',
    'Moulvibazar', 'Sunamganj', 'Rangpur', 'Dinajpur', 'Gaibandha',
    'Kurigram', 'Lalmonirhat', 'Nilphamari', 'Panchagarh', 'Thakurgaon',
    'Mymensingh', 'Jamalpur', 'Netrokona', 'Sherpur'
]


INSTITUTE_KEYWORDS = [
    'school', 'high school', 'primary school', 'college', 'kindergarten',
    'madrasa', 'madrasah', 'coaching center', 'academy', 'institute',
    'polytechnic institute', 'technical school', 'training center'
]

USA_LOCAL_BUSINESS_NICHES = [
    'Med Spa', 'Real Estate', 'Restaurant/Cafe', 'Dental Clinic',
    'Salon/Barbershop', 'Gym/Fitness', 'Chiropractor', 'Auto Repair',
    'Home Services', 'Law Firm', 'Accounting/Tax', 'Veterinary Clinic'
]


class Config:
    """বেস কনফিগারেশন"""
    
    # Database
    SQLALCHEMY_DATABASE_URI = normalize_database_url(
        os.getenv('DATABASE_URL', 'postgresql://localhost/pathshalapro_bot')
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # API Keys
    GOOGLE_MAPS_API_KEY = os.getenv('GOOGLE_MAPS_API_KEY')
    SENDGRID_API_KEY = os.getenv('SENDGRID_API_KEY')
    BREVO_API_KEY = os.getenv('BREVO_API_KEY')
    TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
    TWILIO_WHATSAPP_NUMBER = os.getenv('TWILIO_WHATSAPP_NUMBER')
    HUNTER_API_KEY = os.getenv('HUNTER_API_KEY')
    EMAIL_FINDER_PROVIDER = os.getenv('EMAIL_FINDER_PROVIDER', 'website').lower()

    # AI personalization. Multiple keys are rotated automatically on quota errors.
    GEMINI_API_KEYS = _gemini_api_keys()
    GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
    GEMINI_TIMEOUT_SECONDS = int(os.getenv('GEMINI_TIMEOUT_SECONDS', 30))
    ENABLE_AI_PERSONALIZATION = os.getenv(
        'ENABLE_AI_PERSONALIZATION',
        'true' if GEMINI_API_KEYS else 'false'
    ).lower() == 'true'
    LEAD_RESEARCH_MAX_PAGES = int(os.getenv('LEAD_RESEARCH_MAX_PAGES', 3))
    LEAD_RESEARCH_MAX_CHARS = int(os.getenv('LEAD_RESEARCH_MAX_CHARS', 12000))
    AGENCY_NAME = os.getenv('AGENCY_NAME', 'CreatifyBD')
    AGENCY_WEBSITE = os.getenv('AGENCY_WEBSITE', 'https://creatifybd.com')
    AGENCY_SERVICES = os.getenv(
        'AGENCY_SERVICES',
        'website design and development, SEO, social media marketing, branding and creative design, content, paid advertising'
    )
    
    # Facebook
    FACEBOOK_PAGE_ID = os.getenv('FACEBOOK_PAGE_ID')
    FACEBOOK_ACCESS_TOKEN = os.getenv('FACEBOOK_ACCESS_TOKEN')
    FACEBOOK_PAGE_ACCESS_TOKEN = os.getenv('FACEBOOK_PAGE_ACCESS_TOKEN')
    
    # Google Sheets
    GOOGLE_SHEETS_CREDENTIALS = os.getenv('GOOGLE_SHEETS_CREDENTIALS')
    GOOGLE_SHEETS_AUTH_METHOD = os.getenv('GOOGLE_SHEETS_AUTH_METHOD', 'auto').lower()
    GOOGLE_SHEETS_OAUTH_TOKEN = os.getenv('GOOGLE_SHEETS_OAUTH_TOKEN')
    GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
    
    # Email Settings
    EMAIL_PROVIDER = os.getenv('EMAIL_PROVIDER', 'brevo').lower()
    FROM_EMAIL = os.getenv('FROM_EMAIL', 'marketing@pathshalapro.net')
    FROM_NAME = os.getenv('FROM_NAME', 'PathshalaPro Marketing Team')
    EMAIL_DAILY_LIMIT = int(os.getenv('EMAIL_DAILY_LIMIT', 300))
    
    # WhatsApp Settings
    WHATSAPP_PROVIDER = os.getenv('WHATSAPP_PROVIDER', 'twilio').lower()
    WHATSAPP_FROM_NUMBER = os.getenv('WHATSAPP_FROM_NUMBER')
    
    # General Settings
    ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    PORT = int(os.getenv('PORT', 5000))
    ADMIN_API_TOKEN = os.getenv('ADMIN_API_TOKEN')
    # Public base URL of the deployed app, used for email open-tracking pixels.
    PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL')

    # WhatsApp campaign targeting
    # When false, WhatsApp messages go to every qualified WhatsApp-ready lead
    # (the realistic primary channel) instead of requiring a prior email open.
    WHATSAPP_REQUIRE_EMAIL_OPEN = os.getenv('WHATSAPP_REQUIRE_EMAIL_OPEN', 'false').lower() == 'true'
    WHATSAPP_DAILY_LIMIT = int(os.getenv('WHATSAPP_DAILY_LIMIT', 100))

    # Scheduler Settings
    SCHEDULER_TIMEZONE = os.getenv('SCHEDULER_TIMEZONE', 'UTC')
    SCHEDULER_MODE = os.getenv('SCHEDULER_MODE', 'all').lower()
    ENABLE_LEAD_COLLECTION = os.getenv('ENABLE_LEAD_COLLECTION', 'true').lower() == 'true'
    ENABLE_BD_EDUCATION_COLLECTION = os.getenv(
        'ENABLE_BD_EDUCATION_COLLECTION', 'false'
    ).lower() == 'true'
    ENABLE_MARKETING_JOBS = os.getenv('ENABLE_MARKETING_JOBS', 'true').lower() == 'true'
    ENABLE_REPORTING_JOBS = os.getenv('ENABLE_REPORTING_JOBS', 'true').lower() == 'true'
    LEAD_COLLECTION_DISTRICTS = _csv_list_env('LEAD_COLLECTION_DISTRICTS', BANGLADESH_DISTRICTS)
    LEAD_COLLECTION_KEYWORDS = _csv_list_env('LEAD_COLLECTION_KEYWORDS', INSTITUTE_KEYWORDS)
    LEAD_COLLECTION_QUERIES_PER_RUN = int(os.getenv('LEAD_COLLECTION_QUERIES_PER_RUN', 16))
    LEAD_COLLECTION_RESULTS_PER_QUERY = int(os.getenv('LEAD_COLLECTION_RESULTS_PER_QUERY', 60))
    LEAD_COLLECTION_INTERVAL_MINUTES = int(os.getenv('LEAD_COLLECTION_INTERVAL_MINUTES', 120))
    ENABLE_OPENSTREETMAP_COLLECTION = os.getenv('ENABLE_OPENSTREETMAP_COLLECTION', 'true').lower() == 'true'
    OPENSTREETMAP_DISTRICTS_PER_RUN = int(os.getenv('OPENSTREETMAP_DISTRICTS_PER_RUN', 6))
    OPENSTREETMAP_RESULTS_PER_DISTRICT = int(os.getenv('OPENSTREETMAP_RESULTS_PER_DISTRICT', 80))
    OPENSTREETMAP_INTERVAL_MINUTES = int(os.getenv('OPENSTREETMAP_INTERVAL_MINUTES', 360))
    OVERPASS_API_URLS = _csv_list_env(
        'OVERPASS_API_URLS',
        [
            'https://overpass-api.de/api/interpreter',
            'https://overpass.private.coffee/api/interpreter',
            'https://overpass.kumi.systems/api/interpreter'
        ]
    )
    OVERPASS_TIMEOUT_SECONDS = int(os.getenv('OVERPASS_TIMEOUT_SECONDS', 15))
    ENABLE_PUBLIC_DATASET_COLLECTION = os.getenv('ENABLE_PUBLIC_DATASET_COLLECTION', 'true').lower() == 'true'
    PUBLIC_DATASET_BATCH_SIZE = int(os.getenv('PUBLIC_DATASET_BATCH_SIZE', 1000))
    PUBLIC_DATASET_INTERVAL_MINUTES = int(os.getenv('PUBLIC_DATASET_INTERVAL_MINUTES', 180))
    PUBLIC_DATASET_CONTACT_URL = os.getenv(
        'PUBLIC_DATASET_CONTACT_URL',
        'https://data.gov.bd/api/download/?id=76f80f8e-536c-42b3-8a6d-5ae932aa401b'
    )
    CONTACT_ENRICH_INTERVAL_MINUTES = int(os.getenv('CONTACT_ENRICH_INTERVAL_MINUTES', 180))
    EMAIL_ENRICH_INTERVAL_MINUTES = int(os.getenv('EMAIL_ENRICH_INTERVAL_MINUTES', 30))
    SHEET_SYNC_INTERVAL_MINUTES = int(os.getenv('SHEET_SYNC_INTERVAL_MINUTES', 60))
    CONTACT_ENRICH_LIMIT = int(os.getenv('CONTACT_ENRICH_LIMIT', 100))
    EMAIL_ENRICH_LIMIT = int(os.getenv('EMAIL_ENRICH_LIMIT', 80))
    HUNTER_SEARCHES_PER_RUN = int(os.getenv('HUNTER_SEARCHES_PER_RUN', 10))
    WEBSITE_EMAIL_MAX_PAGES = int(os.getenv('WEBSITE_EMAIL_MAX_PAGES', 12))
    WEBSITE_EMAIL_MAX_LINKS_PER_SITE = int(os.getenv('WEBSITE_EMAIL_MAX_LINKS_PER_SITE', 8))
    WEBSITE_EMAIL_TIMEOUT_SECONDS = int(os.getenv('WEBSITE_EMAIL_TIMEOUT_SECONDS', 6))
    WEBSITE_EMAIL_MAX_SECONDS_PER_SITE = int(os.getenv('WEBSITE_EMAIL_MAX_SECONDS_PER_SITE', 35))
    GOOGLE_PLACES_DAILY_CALL_LIMIT = int(os.getenv('GOOGLE_PLACES_DAILY_CALL_LIMIT', 6000))
    HUNTER_DAILY_CALL_LIMIT = int(os.getenv('HUNTER_DAILY_CALL_LIMIT', 50))
    SEARCH_TASK_RESET_DAYS = int(os.getenv('SEARCH_TASK_RESET_DAYS', 14))
    ENABLE_USA_LOCAL_BUSINESS_COLLECTION = os.getenv(
        'ENABLE_USA_LOCAL_BUSINESS_COLLECTION', 'true'
    ).lower() == 'true'
    USA_LOCAL_BUSINESS_NICHES = _csv_list_env(
        'USA_LOCAL_BUSINESS_NICHES', USA_LOCAL_BUSINESS_NICHES
    )
    USA_LOCAL_BUSINESS_LOCATIONS_PER_RUN = int(os.getenv('USA_LOCAL_BUSINESS_LOCATIONS_PER_RUN', 6))
    USA_LOCAL_BUSINESS_RESULTS_PER_LOCATION = int(os.getenv('USA_LOCAL_BUSINESS_RESULTS_PER_LOCATION', 200))
    USA_LOCAL_BUSINESS_INTERVAL_MINUTES = int(os.getenv('USA_LOCAL_BUSINESS_INTERVAL_MINUTES', 60))
    
    # Schedule Configuration
    SCHEDULE_CONFIG = {
        'lead_collection': {
            'google_maps': '01:00',
            'facebook_groups': '01:30',
            'email_finder': '02:00',
            'linkedin': '02:30',
            'data_cleaning': '03:00',
            'score_calculation': '04:00'
        },
        'engagement': {
            'lead_scoring': '09:00',
            'email_campaign': '10:30',
            'whatsapp_campaign': '12:30',
            'facebook_posting': '14:00',
            'email_followups': '16:00'
        },
        'tracking': {
            'email_tracking': '20:00',
            'whatsapp_tracking': '21:00'
        },
        'reporting': {
            'daily_report': '21:30'
        }
    }
    
    # Batch Settings
    BATCH_SIZE = 50  # একবারে কত লিড প্রসেস করবো
    MAX_RETRIES = 3  # API ফেইল হলে কতবার রিট্রাই করবো
    
    # Lead Scoring
    LEAD_SCORES = {
        'has_email': 3,
        'has_phone': 2,
        'has_website': 2,
        'has_facebook': 1,
        'large_institution': 2  # ১০০+ শিক্ষার্থী
    }
    
    # Email Templates Path
    TEMPLATES_PATH = 'templates/email/'
    
    # API Rate Limits
    RATE_LIMITS = {
        'google_maps': {'calls': 25000, 'period': 'day'},
        'sendgrid': {'calls': 12000, 'period': 'day'},
        'hunter': {'calls': 100, 'period': 'day'}
    }


class DevelopmentConfig(Config):
    """ডেভেলপমেন্ট কনফিগারেশন"""
    DEBUG = True
    TESTING = False


class ProductionConfig(Config):
    """প্রোডাকশন কনফিগারেশন"""
    DEBUG = False
    TESTING = False


class TestingConfig(Config):
    """টেস্টিং কনফিগারেশন"""
    TESTING = True
    SQLALCHEMY_DATABASE_URI = 'sqlite:///test.db'


# এনভায়রনমেন্ট অনুযায়ী কনফিগ সিলেক্ট করা
config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'testing': TestingConfig,
    'default': DevelopmentConfig
}

def get_config():
    """বর্তমান এনভায়রনমেন্ট অনুযায়ী কনফিগ রিটার্ন করা"""
    env = os.getenv('ENVIRONMENT', 'development')
    return config.get(env, config['default'])
