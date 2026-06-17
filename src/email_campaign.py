import sendgrid
from sendgrid.helpers.mail import Mail, Email, Content, HtmlContent
from jinja2 import Template
from datetime import datetime
from loguru import logger
from config import Config
from src.database import (
    Lead, get_leads_by_segment, update_lead_status, 
    log_email_event, db
)
import os

logger.add("logs/email_campaign.log", rotation="500 MB")


class EmailCampaign:
    """ইমেইল ক্যাম্পেইন ম্যানেজার"""
    
    def __init__(self):
        self.sg = sendgrid.SendGridAPIClient(Config.SENDGRID_API_KEY)
        self.from_email = Config.FROM_EMAIL
        self.from_name = Config.FROM_NAME
        self.templates_path = 'templates/email/'
        
        # টেমপ্লেট লোড করা
        self.templates = self._load_templates()
    
    
    def _load_templates(self):
        """ইমেইল টেমপ্লেট লোড করা"""
        templates = {
            'hot_first': '''
প্রিয় [SCHOOL_NAME] এর অধ্যক্ষ/ম্যানেজার,

নমস্কার! আমরা PathshalaPro - সম্পূর্ণ বাংলা স্কুল ম্যানেজমেন্ট সফটওয়্যার।

আপনার [SCHOOL_NAME] কে আরও ভালোভাবে পরিচালনা করার জন্য আমরা এখানে আছি।

[DISTRICT] এ ইতিমধ্যে ১৫+ প্রতিষ্ঠান PathshalaPro ব্যবহার করছে।

✓ ডিজিটাল হাজিরা - ৫ মিনিটে শেষ
✓ অনলাইন ফি সংগ্রহ - সরাসরি আপনার বিকাশে
✓ রেজাল্ট কার্ড - এক ক্লিকে প্রিন্ট করুন
✓ অভিভাবকদের স্বয়ংক্রিয় SMS

১৪ দিন সম্পূর্ণ ফ্রি ট্রায়াল করুন:
https://pathshalapro.net/trial?ref=[LEAD_ID]

যেকোনো প্রশ্নের জন্য: support@pathshalapro.net

ধন্যবাদ,
PathshalaPro টিম
            ''',
            
            'warm_first': '''
প্রিয় বন্ধু,

আমরা PathshalaPro - স্কুল/কোচিং ম্যানেজমেন্ট সফটওয়্যার নিয়ে এসেছি।

আপনার প্রতিষ্ঠান কে ডিজিটাল করতে আমরা সাহায্য করতে পারি।

কোন খরচ নেই, শুধু ১৪ দিন ফ্রি ট্রায়াল।

আগ্রহী হলে এখানে ক্লিক করুন:
https://pathshalapro.net/trial?ref=[LEAD_ID]

ধন্যবাদ,
PathshalaPro টিম
            ''',
            
            'cold_first': '''
প্রিয় [SCHOOL_NAME],

নমস্কার!

PathshalaPro একটি সম্পূর্ণ বাংলা স্কুল ম্যানেজমেন্ট সফটওয়্যার।

[DISTRICT] তে ইতিমধ্যে অনেক প্রতিষ্ঠান ডিজিটালাইজড হয়েছে।

আপনিও এই সুবিধা পেতে পারেন। একটি ছোট্ট ডেমো দেখান যায়?

ডেমো বুক করুন: https://pathshalapro.net/demo?ref=[LEAD_ID]

ধন্যবাদ,
PathshalaPro
            ''',
            
            'followup_second': '''
প্রিয় [SCHOOL_NAME],

আমরা আপনার আগের ইমেইল পাঠিয়েছিলাম। আপনি দেখেছেন?

আপনার যদি কোন প্রশ্ন থাকে আমরা এখানে আছি।

PathshalaPro এর বৈশিষ্ট্য সম্পর্কে আরও জানতে:
https://pathshalapro.net/features?ref=[LEAD_ID]

ধন্যবাদ,
PathshalaPro
            ''',
            
            'followup_video': '''
প্রিয় [SCHOOL_NAME],

একটি ৫ মিনিটের ভিডিও দেমো দেখতে আগ্রহী?

এখানে ক্লিক করুন: https://pathshalapro.net/video-demo?ref=[LEAD_ID]

এই ভিডিওতে আপনি দেখতে পাবেন:
- কিভাবে হাজিরা নেওয়া যায় ৫ মিনিটে
- অনলাইন ফি কিভাবে কালেক্ট করতে হয়
- রেজাল্ট কার্ড কিভাবে প্রিন্ট করতে হয়

ধন্যবাদ,
PathshalaPro
            ''',
            
            'objection_price': '''
প্রিয় [SCHOOL_NAME],

আমরা বুঝি আপনি দাম নিয়ে চিন্তিত।

তাই এখানে একটি তুলনা দিচ্ছি:

PathshalaPro: ৳৯৯৯/মাস (Standard Plan)
অন্যান্য সফটওয়্যার: ৳২,০००-৳৫,০००/মাস

এবং আমরা দিচ্ছি সব ফিচার একসাথে!

আরও বিস্তারিত: https://pathshalapro.net/pricing?ref=[LEAD_ID]

ধন্যবাদ,
PathshalaPro
            ''',
            
            'final_offer': '''
প্রিয় [SCHOOL_NAME],

এটি আমাদের শেষ অফার!

প্রথম মাস ৫০% ছাড়ে PathshalaPro ব্যবহার করুন।

এই অফার শুধুমাত্র আজকের জন্য।

এখনই যোগাযোগ করুন:
- ইমেইল: support@pathshalapro.net
- হোয়াটসঅ্যাপ: +88015XXXXXXX

ধন্যবাদ,
PathshalaPro টিম
            '''
        }
        
        return templates
    
    
    def send_email(self, to_email, subject, html_content, lead_id=None, template_name=None):
        """একটি ইমেইল পাঠানো"""
        try:
            message = Mail(
                from_email=Email(self.from_email, self.from_name),
                to_emails=to_email,
                subject=subject,
                html_content=html_content
            )
            
            # ট্র্যাকিং লিঙ্ক যোগ করা
            message.template_id = None  # SendGrid টেমপ্লেট ব্যবহার করছি না
            
            response = self.sg.send(message)
            
            logger.info(f"✓ ইমেইল পাঠানো: {to_email} (Status: {response.status_code})")
            
            # লগ রেকর্ড করা
            if lead_id:
                log_email_event(
                    lead_id=lead_id,
                    subject=subject,
                    status='sent',
                    template=template_name
                )
            
            return True
        
        except Exception as e:
            logger.error(f"Email sending error ({to_email}): {e}")
            return False
    
    
    def _personalize_template(self, template_text, lead):
        """টেমপ্লেট পার্সোনালাইজ করা"""
        replacements = {
            '[SCHOOL_NAME]': lead.school_name or 'বন্ধু',
            '[DISTRICT]': lead.district or 'বাংলাদেশ',
            '[LEAD_ID]': str(lead.id),
            '[TYPE]': lead.type or 'প্রতিষ্ঠান'
        }
        
        result = template_text
        for key, value in replacements.items():
            result = result.replace(key, value)
        
        return result
    
    
    def run_campaign(self):
        """সম্পূর্ণ ইমেইল ক্যাম্পেইন চালানো"""
        logger.info("=" * 50)
        logger.info("ইমেইল ক্যাম্পেইন শুরু")
        logger.info("=" * 50)
        
        try:
            # লিড স্কোর অনুযায়ী সংগ্রহ করা
            hot_leads = get_leads_by_segment('Hot')
            warm_leads = get_leads_by_segment('Warm')
            cold_leads = get_leads_by_segment('Cold')
            
            total_sent = 0
            
            # Hot লিডদের প্রথম ইমেইল পাঠানো
            logger.info(f"Hot লিড পাঠাচ্ছি: {len(hot_leads)}")
            for lead in hot_leads:
                if lead.email and not lead.email_sent:
                    html_content = self._personalize_template(
                        self.templates['hot_first'], 
                        lead
                    )
                    
                    subject = f"{lead.school_name} এর জন্য বিশেষ অফার 🎁"
                    
                    if self.send_email(
                        lead.email, 
                        subject, 
                        html_content, 
                        lead.id,
                        'hot_first'
                    ):
                        update_lead_status(
                            lead.id,
                            email_sent=True,
                            email_sent_date=datetime.utcnow(),
                            email_send_count=1
                        )
                        total_sent += 1
            
            # Warm লিডদের মধ্যম ইমেইল
            logger.info(f"Warm লিড পাঠাচ্ছি: {len(warm_leads)}")
            for lead in warm_leads:
                if lead.email and not lead.email_sent:
                    html_content = self._personalize_template(
                        self.templates['warm_first'], 
                        lead
                    )
                    
                    subject = f"{lead.school_name} - স্কুল ম্যানেজমেন্ট সলিউশন"
                    
                    if self.send_email(
                        lead.email, 
                        subject, 
                        html_content, 
                        lead.id,
                        'warm_first'
                    ):
                        update_lead_status(
                            lead.id,
                            email_sent=True,
                            email_sent_date=datetime.utcnow(),
                            email_send_count=1
                        )
                        total_sent += 1
            
            # Cold লিডদের সফট ইমেইল
            logger.info(f"Cold লিড পাঠাচ্ছি: {len(cold_leads)}")
            for lead in cold_leads:
                if lead.email and not lead.email_sent:
                    html_content = self._personalize_template(
                        self.templates['cold_first'], 
                        lead
                    )
                    
                    subject = f"{lead.school_name} - বিনামূল্যে ডেমো"
                    
                    if self.send_email(
                        lead.email, 
                        subject, 
                        html_content, 
                        lead.id,
                        'cold_first'
                    ):
                        update_lead_status(
                            lead.id,
                            email_sent=True,
                            email_sent_date=datetime.utcnow(),
                            email_send_count=1
                        )
                        total_sent += 1
            
            logger.info(f"✓ মোট ইমেইল পাঠানো: {total_sent}")
            return total_sent
        
        except Exception as e:
            logger.error(f"Campaign error: {e}")
            return 0


# সিঙ্গেল ইনস্ট্যান্স
email_campaign = EmailCampaign()


if __name__ == '__main__':
    campaign = EmailCampaign()
    campaign.run_campaign()
