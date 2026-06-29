from twilio.rest import Client
from datetime import datetime
from loguru import logger
from config import Config
from src.personalization import outreach_personalizer
from src.database import (
    Lead, update_lead_status, log_whatsapp_event, db, normalize_bd_phone
)
import csv
import os

logger.add("logs/whatsapp_campaign.log", rotation="500 MB")


class WhatsAppCampaign:
    """হোয়াটসঅ্যাপ মেসেজিং ক্যাম্পেইন"""
    
    def __init__(self):
        self.provider = Config.WHATSAPP_PROVIDER
        self.account_sid = Config.TWILIO_ACCOUNT_SID
        self.auth_token = Config.TWILIO_AUTH_TOKEN
        self.whatsapp_from = Config.TWILIO_WHATSAPP_NUMBER
        self.client = None
        if self.provider == 'twilio' and self.account_sid and self.auth_token:
            self.client = Client(self.account_sid, self.auth_token)
    
    
    def send_message(self, phone_number, message_text, lead_id=None):
        """একটি হোয়াটসঅ্যাপ মেসেজ পাঠানো"""
        try:
            if not self.client or not self.whatsapp_from:
                sid = self._queue_message(phone_number, message_text, lead_id)
                logger.warning(f"WhatsApp credential missing; queued message for {phone_number}")
                return True, sid

            # ফোন নম্বর ফরম্যাট করা (Bangladesh format)
            phone_info = normalize_bd_phone(phone_number)
            if not phone_info['phone_valid']:
                raise ValueError(f"Invalid WhatsApp-ready Bangladesh mobile number: {phone_number}")
            phone_number = phone_info['phone_e164']
            
            message = self.client.messages.create(
                from_=f'whatsapp:{self.whatsapp_from}',
                body=message_text,
                to=f'whatsapp:{phone_number}'
            )
            
            logger.info(f"✓ হোয়াটসঅ্যাপ পাঠানো: {phone_number} (SID: {message.sid})")
            
            # লগ রেকর্ড করা
            if lead_id:
                log_whatsapp_event(
                    lead_id=lead_id,
                    message=message_text,
                    status='sent',
                    message_sid=message.sid
                )
            
            return True, message.sid
        
        except Exception as e:
            logger.error(f"WhatsApp error ({phone_number}): {e}")
            
            if lead_id:
                log_whatsapp_event(
                    lead_id=lead_id,
                    message=message_text,
                    status='failed',
                    message_sid='',
                    error_message=str(e)
                )
            
            return False, None

    def _queue_message(self, phone_number, message_text, lead_id=None):
        """Twilio/Meta credentials না থাকলে মেসেজ local outbox-এ রাখা"""
        os.makedirs('reports', exist_ok=True)
        outbox_file = 'reports/whatsapp_outbox.csv'
        file_exists = os.path.exists(outbox_file)
        queued_id = f"queued-{lead_id or 'unknown'}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"

        with open(outbox_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'queued_id', 'lead_id', 'phone_number', 'message', 'created_at'
            ])
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                'queued_id': queued_id,
                'lead_id': lead_id or '',
                'phone_number': phone_number,
                'message': message_text,
                'created_at': datetime.utcnow().isoformat()
            })

        if lead_id:
            log_whatsapp_event(
                lead_id=lead_id,
                message=message_text,
                status='queued',
                message_sid=queued_id
            )
        return queued_id
    
    
    def send_campaign(self):
        """সম্পূর্ণ হোয়াটসঅ্যাপ ক্যাম্পেইন চালানো"""
        logger.info("=" * 50)
        logger.info("হোয়াটসঅ্যাপ ক্যাম্পেইন শুরু")
        logger.info("=" * 50)
        
        try:
            # WhatsApp-ready qualified leads যাদের এখনো মেসেজ পাঠানো হয়নি।
            # ডিফল্টে email-open এর উপর নির্ভরশীল নয় (WhatsApp প্রধান চ্যানেল);
            # WHATSAPP_REQUIRE_EMAIL_OPEN=true করলে পুরোনো আচরণ ফিরে আসে।
            query = Lead.query.filter(
                Lead.phone_valid == True,
                Lead.whatsapp_sent == False,
                Lead.qualification_status == 'qualified'
            )
            if Config.WHATSAPP_REQUIRE_EMAIL_OPEN:
                query = query.filter(Lead.email_opened == True)

            target_leads = query.order_by(Lead.score.desc()).limit(Config.WHATSAPP_DAILY_LIMIT).all()

            logger.info(f"WhatsApp টার্গেট লিড: {len(target_leads)}")
            
            total_sent = 0
            
            for lead in target_leads:
                message_text = self._create_message(lead)
                
                success, sid = self.send_message(
                    lead.phone_e164 or lead.phone,
                    message_text,
                    lead.id
                )
                
                if success:
                    update_lead_status(
                        lead.id,
                        whatsapp_sent=True,
                        whatsapp_sent_date=datetime.utcnow(),
                        whatsapp_sid=sid,
                        whatsapp_send_count=1
                    )
                    total_sent += 1
            
            logger.info(f"✓ মোট হোয়াটসঅ্যাপ পাঠানো: {total_sent}")
            return total_sent
        
        except Exception as e:
            logger.error(f"Campaign error: {e}")
            return 0
    
    
    def _create_message(self, lead):
        """লিডের ধরন অনুযায়ী মেসেজ তৈরি করা"""
        ai_copy = outreach_personalizer.create(lead)
        if ai_copy:
            return ai_copy['whatsapp_message']

        return (
            f"Hi, this is CreatifyBD. I came across {lead.school_name or 'your business'} "
            "and thought there may be a useful digital growth opportunity around your website, "
            "search visibility, or social presence. May I send a short, no-obligation review? "
            "If this is not relevant, just let me know and I will not follow up."
        )
        
        messages = {
            'hot': f"""নমস্কার! 👋

আপনার {lead.school_name} এর জন্য PathshalaPro নিয়ে এসেছি।

সম্পূর্ণ বাংলা স্কুল ম্যানেজমেন্ট সফটওয়্যার।

✅ ডিজিটাল হাজিরা - ৫ মিনিটে শেষ
✅ অনলাইন ফি সংগ্রহ
✅ অভিভাবক এসএমএস
✅ রেজাল্ট কার্ড

১৪ দিন সম্পূর্ণ ফ্রি!

ডেমো দেখতে: https://pathshalapro.net/demo?ref={lead.id}

কোন প্রশ্ন? এখানে উত্তর দিচ্ছি।""",
            
            'warm': f"""নমস্কার! 👋

PathshalaPro - স্কুল ম্যানেজমেন্ট সফটওয়্যার।

আপনার প্রতিষ্ঠান কে ডিজিটাল করতে আমরা প্রস্তুত।

বৈশিষ্ট্য:
✅ ডিজিটাল হাজিরা
✅ অনলাইন ফি
✅ অভিভাবক SMS

১৪ দিন ফ্রি ট্রায়াল: https://pathshalapro.net/trial?ref={lead.id}

আগ্রহী? আমাদের সাথে যোগাযোগ করুন।""",
            
            'cold': f"""নমস্কার! 

PathshalaPro একটি স্কুল ম্যানেজমেন্ট সফটওয়্যার।

আপনার স্কুল/কোচিং কে ডিজিটাল করতে আমরা সাহায্য করি।

ফ্রি ডেমো: https://pathshalapro.net/demo?ref={lead.id}

ধন্যবাদ! ☺️"""
        }
        
        # সেগমেন্ট অনুযায়ী মেসেজ নির্বাচন করা
        if lead.segment == 'Hot':
            return messages['hot']
        elif lead.segment == 'Warm':
            return messages['warm']
        else:
            return messages['cold']


# সিঙ্গেল ইনস্ট্যান্স
whatsapp_campaign = WhatsAppCampaign()


if __name__ == '__main__':
    campaign = WhatsAppCampaign()
    campaign.send_campaign()
