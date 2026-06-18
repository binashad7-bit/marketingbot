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
    
    # WhatsApp Settings
    WHATSAPP_PROVIDER = os.getenv('WHATSAPP_PROVIDER', 'twilio').lower()
    WHATSAPP_FROM_NUMBER = os.getenv('WHATSAPP_FROM_NUMBER')
    
    # General Settings
    ENVIRONMENT = os.getenv('ENVIRONMENT', 'development')
    DEBUG = os.getenv('DEBUG', 'False').lower() == 'true'
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
    PORT = int(os.getenv('PORT', 5000))
    
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
            'facebook_posting': '14:00'
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
