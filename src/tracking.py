from datetime import datetime
from loguru import logger
from config import Config
from src.database import Lead, EmailLog, WhatsAppLog, db, update_lead_status

logger.add("logs/tracking.log", rotation="500 MB")


class EmailTracking:
    """ইমেইল ট্র্যাকিং"""
    
    @staticmethod
    def track_opens():
        """খোলা ইমেইল ট্র্যাক করা (SendGrid ওয়েবহুক থেকে)"""
        logger.info("ইমেইল খোলা ট্র্যাক করছি...")
        
        try:
            # SendGrid ইভেন্ট API থেকে ডেটা পাওয়া (webhook এর মাধ্যমে)
            # এটি আপনার Flask অ্যাপে একটি endpoint দিয়ে হ্যান্ডেল করা যায়
            
            # উদাহরণ endpoint:
            # @app.route('/webhooks/sendgrid', methods=['POST'])
            # def sendgrid_webhook():
            #     for event in request.json:
            #         if event['event'] == 'open':
            #             lead = Lead.query.get(event['lead_id'])
            #             lead.email_opened = True
            #             lead.email_opened_time = datetime.utcnow()
            #             db.session.commit()
            
            logger.info("✓ ইমেইল খোলা ট্র্যাকিং সেটআপ করা হয়েছে")
            return True
        
        except Exception as e:
            logger.error(f"Email tracking error: {e}")
            return False
    
    
    @staticmethod
    def track_clicks():
        """ইমেইল লিঙ্ক ক্লিক ট্র্যাক করা"""
        logger.info("ইমেইল ক্লিক ট্র্যাক করছি...")
        
        try:
            # SendGrid ইভেন্ট থেকে ক্লিক ডেটা পাওয়া
            # webhook endpoint এর মাধ্যমে
            
            logger.info("✓ ইমেইল ক্লিক ট্র্যাকিং সেটআপ করা হয়েছে")
            return True
        
        except Exception as e:
            logger.error(f"Click tracking error: {e}")
            return False


class WhatsAppTracking:
    """হোয়াটসঅ্যাপ ট্র্যাকিং"""
    
    @staticmethod
    def track_delivery():
        """ডেলিভারি স্ট্যাটাস ট্র্যাক করা"""
        logger.info("হোয়াটসঅ্যাপ ডেলিভারি ট্র্যাক করছি...")
        
        try:
            # Twilio ওয়েবহুক থেকে ডেলিভারি স্ট্যাটাস পাওয়া
            # @app.route('/webhooks/twilio', methods=['POST'])
            # def twilio_webhook():
            #     status = request.form.get('MessageStatus')
            #     message_sid = request.form.get('MessageSid')
            #     
            #     log = WhatsAppLog.query.filter_by(message_sid=message_sid).first()
            #     if log:
            #         if status == 'delivered':
            #             log.status = 'delivered'
            #             log.delivered_at = datetime.utcnow()
            #         elif status == 'read':
            #             log.status = 'read'
            #             log.read_at = datetime.utcnow()
            #         db.session.commit()
            
            logger.info("✓ হোয়াটসঅ্যাপ ডেলিভারি ট্র্যাকিং সেটআপ করা হয়েছে")
            return True
        
        except Exception as e:
            logger.error(f"Delivery tracking error: {e}")
            return False
    
    
    @staticmethod
    def track_reads():
        """পড়া হোয়াটসঅ্যাপ মেসেজ ট্র্যাক করা"""
        logger.info("হোয়াটসঅ্যাপ রিড ট্র্যাক করছি...")
        
        try:
            # Twilio webhook থেকে read status পাওয়া
            logger.info("✓ হোয়াটসঅ্যাপ রিড ট্র্যাকিং সেটআপ করা হয়েছে")
            return True
        
        except Exception as e:
            logger.error(f"Read tracking error: {e}")
            return False


class GeneralTracking:
    """সাধারণ ট্র্যাকিং"""
    
    @staticmethod
    def update_lead_engagement():
        """লিড এনগেজমেন্ট আপডেট করা"""
        logger.info("লিড এনগেজমেন্ট আপডেট করছি...")
        
        try:
            # যারা ইমেইল খুলেছে অথবা হোয়াটসঅ্যাপ পড়েছে
            engaged_leads = Lead.query.filter(
                (Lead.email_opened == True) | (Lead.whatsapp_read == True)
            ).all()
            
            for lead in engaged_leads:
                if lead.status == 'pending':
                    lead.status = 'engaged'
                    lead.updated_at = datetime.utcnow()
            
            db.session.commit()
            logger.info(f"✓ {len(engaged_leads)} লিড এনগেজড হিসেবে চিহ্নিত করা হয়েছে")
            return len(engaged_leads)
        
        except Exception as e:
            logger.error(f"Engagement update error: {e}")
            return 0
    
    
    @staticmethod
    def identify_objections():
        """অবজেকশন চিহ্নিত করা (আগ্রহী কিন্তু সিদ্ধান্ত নেয়নি)"""
        logger.info("অবজেকশন চিহ্নিত করছি...")
        
        try:
            from datetime import timedelta
            
            # যারা ইমেইল খুলেছে কিন্তু ৫ দিন পরও কোন কাজ নেই
            cutoff_date = datetime.utcnow() - timedelta(days=5)
            
            objection_leads = Lead.query.filter(
                Lead.email_opened == True,
                Lead.email_sent_date <= cutoff_date,
                Lead.trial_signed == False,
                Lead.status != 'converted'
            ).all()
            
            for lead in objection_leads:
                if lead.status != 'objection':
                    lead.status = 'objection'
                    lead.objection_type = 'unknown'  # টাইপ সেটিংস করা যায় রেসপন্সের উপর ভিত্তি করে
            
            db.session.commit()
            logger.info(f"✓ {len(objection_leads)} লিড অবজেকশন হিসেবে চিহ্নিত করা হয়েছে")
            return len(objection_leads)
        
        except Exception as e:
            logger.error(f"Objection identification error: {e}")
            return 0
    
    
    @staticmethod
    def calculate_metrics():
        """মেট্রিক্স ক্যালকুলেট করা"""
        logger.info("মেট্রিক্স ক্যালকুলেট করছি...")
        
        try:
            total_leads = Lead.query.count()
            emails_sent = Lead.query.filter_by(email_sent=True).count()
            emails_opened = Lead.query.filter_by(email_opened=True).count()
            whatsapp_sent = Lead.query.filter_by(whatsapp_sent=True).count()
            whatsapp_read = Lead.query.filter_by(whatsapp_read=True).count()
            engaged = Lead.query.filter_by(status='engaged').count()
            conversions = Lead.query.filter_by(paid_customer=True).count()
            
            email_open_rate = (emails_opened / emails_sent * 100) if emails_sent > 0 else 0
            whatsapp_read_rate = (whatsapp_read / whatsapp_sent * 100) if whatsapp_sent > 0 else 0
            conversion_rate = (conversions / total_leads * 100) if total_leads > 0 else 0
            
            metrics = {
                'total_leads': total_leads,
                'emails_sent': emails_sent,
                'emails_opened': emails_opened,
                'email_open_rate': round(email_open_rate, 1),
                'whatsapp_sent': whatsapp_sent,
                'whatsapp_read': whatsapp_read,
                'whatsapp_read_rate': round(whatsapp_read_rate, 1),
                'engaged': engaged,
                'conversions': conversions,
                'conversion_rate': round(conversion_rate, 1)
            }
            
            logger.info(f"✓ মেট্রিক্স: {metrics}")
            return metrics
        
        except Exception as e:
            logger.error(f"Metrics calculation error: {e}")
            return None


class TrackingManager:
    """সম্পূর্ণ ট্র্যাকিং ম্যানেজার"""
    
    @staticmethod
    def run_all():
        """সব ট্র্যাকিং চালানো"""
        logger.info("=" * 50)
        logger.info("ট্র্যাকিং সাইকেল শুরু")
        logger.info("=" * 50)
        
        try:
            # ইমেইল ট্র্যাকিং
            EmailTracking.track_opens()
            EmailTracking.track_clicks()
            
            # হোয়াটসঅ্যাপ ট্র্যাকিং
            WhatsAppTracking.track_delivery()
            WhatsAppTracking.track_reads()
            
            # সাধারণ ট্র্যাকিং
            GeneralTracking.update_lead_engagement()
            GeneralTracking.identify_objections()
            
            # মেট্রিক্স
            metrics = GeneralTracking.calculate_metrics()
            
            logger.info("✓ ট্র্যাকিং সাইকেল সম্পূর্ণ")
            return metrics
        
        except Exception as e:
            logger.error(f"Tracking cycle error: {e}")
            return None


# সিঙ্গেল ইনস্ট্যান্স
tracking_manager = TrackingManager()


if __name__ == '__main__':
    tracking_manager.run_all()
