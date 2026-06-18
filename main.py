import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger
import atexit
from datetime import datetime

from config import Config, get_config
from src.database import db, init_db, get_stats
from src.lead_collection import lead_collector
from src.email_campaign import email_campaign
from src.whatsapp_campaign import whatsapp_campaign
from src.facebook_posting import facebook_poster
from src.tracking import tracking_manager
from src.reporting import reporting_manager

# লগিং সেটআপ
logger.add("logs/main.log", rotation="500 MB")
logger.add("logs/error.log", level="ERROR", rotation="500 MB")

# Flask অ্যাপ সেটআপ
app = Flask(__name__)
app.config.from_object(get_config())

# ডাটাবেস সংযোগ
db.init_app(app)

# Scheduler সেটআপ
scheduler = BackgroundScheduler()

# পোর্ট সেটআপ
PORT = os.getenv('PORT', 5000)


def init_scheduler():
    """স্বয়ংক্রিয় টাস্ক সময়সূচী সেটআপ করা"""
    logger.info("শিডিউলার সেটআপ করছি...")
    
    # ======== রাত্রিকালীন লিড সংগ্রহ ========
    
    # Google Maps থেকে লিড সংগ্রহ (রাত ১ AM)
    scheduler.add_job(
        func=lead_collector.collect_from_google_maps,
        trigger="cron",
        hour=1,
        minute=0,
        id='collect_google_maps',
        name='Google Maps থেকে লিড সংগ্রহ'
    )
    
    # Facebook গ্রুপ থেকে লিড সংগ্রহ (রাত ১:३० AM)
    scheduler.add_job(
        func=lead_collector.collect_from_facebook_groups,
        trigger="cron",
        hour=1,
        minute=30,
        id='collect_facebook',
        name='Facebook গ্রুপ থেকে লিড সংগ্রহ'
    )
    
    # Email খুঁজে পাওয়া (রাত २:००AM)
    scheduler.add_job(
        func=lead_collector.enrich_missing_emails,
        trigger="cron",
        hour=2,
        minute=0,
        id='find_emails',
        name='Email খুঁজে পাওয়া'
    )
    
    # Data Cleaning (রাত ३:००AM)
    scheduler.add_job(
        func=lead_collector.clean_and_score_leads,
        trigger="cron",
        hour=3,
        minute=0,
        id='clean_leads',
        name='লিড ক্লিনিং এবং স্কোরিং'
    )
    
    # ======== সকালের এনগেজমেন্ট ========
    
    # ইমেইল ক্যাম্পেইন (সকাল १०:३०AM)
    scheduler.add_job(
        func=email_campaign.run_campaign,
        trigger="cron",
        hour=10,
        minute=30,
        id='email_campaign',
        name='ইমেইল ক্যাম্পেইন'
    )
    
    # হোয়াটসঅ্যাপ ক্যাম্পেইন (দুপুর १२:३०PM)
    scheduler.add_job(
        func=whatsapp_campaign.send_campaign,
        trigger="cron",
        hour=12,
        minute=30,
        id='whatsapp_campaign',
        name='হোয়াটসঅ্যাপ মেসেজিং'
    )
    
    # ফেসবুক পোস্টিং (দুপুর २:००PM)
    scheduler.add_job(
        func=facebook_poster.post_daily_content,
        trigger="cron",
        hour=14,
        minute=0,
        id='facebook_posting',
        name='ফেসবুক পোস্টিং'
    )
    
    # ======== ট্র্যাকিং ========
    
    # ইমেইল ট্র্যাকিং (রাত ८:००PM)
    scheduler.add_job(
        func=tracking_manager.run_all,
        trigger="cron",
        hour=20,
        minute=0,
        id='tracking',
        name='ট্র্যাকিং এবং মেট্রিক্স'
    )
    
    # ======== রিপোর্টিং ========
    
    # দৈনিক রিপোর্ট (রাত ९:३०PM)
    scheduler.add_job(
        func=reporting_manager.run_daily,
        trigger="cron",
        hour=21,
        minute=30,
        id='daily_report',
        name='দৈনিক রিপোর্ট'
    )
    
    scheduler.start()
    logger.info("✓ শিডিউলার সফলভাবে সেটআপ করা হয়েছে")
    
    # প্রোগ্রাম শেষ হলে শিডিউলার বন্ধ করা
    atexit.register(lambda: scheduler.shutdown())


# ======== FLASK ROUTES ========

@app.route('/health', methods=['GET'])
def health_check():
    """হেলথ চেক এন্ডপয়েন্ট"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'scheduler': 'running' if scheduler.running else 'stopped'
    }), 200


@app.route('/stats', methods=['GET'])
def get_statistics():
    """বর্তমান পরিসংখ্যান পাওয়া"""
    try:
        stats = get_stats()
        return jsonify({
            'status': 'success',
            'data': stats,
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Stats error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/lead-collection', methods=['POST'])
def trigger_lead_collection():
    """ম্যানুয়ালি লিড সংগ্রহ ট্রিগার করা"""
    try:
        result = lead_collector.run_all()
        return jsonify({
            'status': 'success',
            'message': f'{result} লিড সংগ্রহ করা হয়েছে',
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/email-campaign', methods=['POST'])
def trigger_email_campaign():
    """ম্যানুয়ালি ইমেইল ক্যাম্পেইন ট্রিগার করা"""
    try:
        result = email_campaign.run_campaign()
        return jsonify({
            'status': 'success',
            'message': f'{result} ইমেইল পাঠানো হয়েছে',
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Email trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/whatsapp-campaign', methods=['POST'])
def trigger_whatsapp_campaign():
    """ম্যানুয়ালি হোয়াটসঅ্যাপ ক্যাম্পেইন ট্রিগার করা"""
    try:
        result = whatsapp_campaign.send_campaign()
        return jsonify({
            'status': 'success',
            'message': f'{result} হোয়াটসঅ্যাপ মেসেজ পাঠানো হয়েছে',
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"WhatsApp trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/facebook-posting', methods=['POST'])
def trigger_facebook_posting():
    """ম্যানুয়ালি ফেসবুক পোস্টিং ট্রিগার করা"""
    try:
        result = facebook_poster.post_daily_content()
        return jsonify({
            'status': 'success',
            'message': 'ফেসবুকে পোস্ট করা হয়েছে' if result else 'পোস্টিং ব্যর্থ',
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Facebook trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/tracking', methods=['POST'])
def trigger_tracking():
    """ম্যানুয়ালি ট্র্যাকিং ট্রিগার করা"""
    try:
        metrics = tracking_manager.run_all()
        return jsonify({
            'status': 'success',
            'data': metrics,
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Tracking trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/report', methods=['POST'])
def trigger_report():
    """ম্যানুয়ালি রিপোর্ট জেনারেশন ট্রিগার করা"""
    try:
        result = reporting_manager.run_daily()
        return jsonify({
            'status': 'success',
            'data': result,
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Report trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/webhooks/sendgrid', methods=['POST'])
def sendgrid_webhook():
    """SendGrid ইভেন্ট ওয়েবহুক"""
    try:
        from src.database import Lead
        
        events = request.json
        
        for event in events:
            if event.get('event') == 'open':
                lead_id = event.get('lead_id')
                lead = Lead.query.get(lead_id)
                if lead:
                    lead.email_opened = True
                    lead.email_opened_time = datetime.utcnow()
            
            elif event.get('event') == 'click':
                lead_id = event.get('lead_id')
                lead = Lead.query.get(lead_id)
                if lead:
                    lead.email_clicked = True
        
        db.session.commit()
        return jsonify({'status': 'success'}), 200
    
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/webhooks/twilio', methods=['POST'])
def twilio_webhook():
    """Twilio ইভেন্ট ওয়েবহুক"""
    try:
        from src.database import WhatsAppLog
        
        status = request.form.get('MessageStatus')
        message_sid = request.form.get('MessageSid')
        
        log = WhatsAppLog.query.filter_by(message_sid=message_sid).first()
        
        if log:
            if status == 'delivered':
                log.status = 'delivered'
                log.delivered_at = datetime.utcnow()
            elif status == 'read':
                log.status = 'read'
                log.read_at = datetime.utcnow()
            
            db.session.commit()
        
        return jsonify({'status': 'success'}), 200
    
    except Exception as e:
        logger.error(f"Twilio webhook error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/scheduler/jobs', methods=['GET'])
def list_jobs():
    """সব শিডিউলড জবস দেখা"""
    try:
        jobs = []
        for job in scheduler.get_jobs():
            jobs.append({
                'id': job.id,
                'name': job.name,
                'next_run_time': str(job.next_run_time) if job.next_run_time else None,
                'trigger': str(job.trigger)
            })
        
        return jsonify({
            'status': 'success',
            'total_jobs': len(jobs),
            'jobs': jobs
        }), 200
    
    except Exception as e:
        logger.error(f"Jobs listing error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.errorhandler(404)
def not_found(error):
    """404 ত্রুটি হ্যান্ডলার"""
    return jsonify({
        'status': 'error',
        'message': 'এন্ডপয়েন্ট খুঁজে পাওয়া যায়নি'
    }), 404


@app.errorhandler(500)
def internal_error(error):
    """500 ত্রুটি হ্যান্ডলার"""
    logger.error(f"Internal server error: {error}")
    return jsonify({
        'status': 'error',
        'message': 'অভ্যন্তরীণ সার্ভার ত্রুটি'
    }), 500


# ======== INITIALIZATION ========

def initialize_app():
    """অ্যাপ ইনিশিয়ালাইজ করা"""
    with app.app_context():
        logger.info("অ্যাপ ইনিশিয়ালাইজ করছি...")
        
        # ডাটাবেস সেটআপ
        init_db(app)
        
        # শিডিউলার সেটআপ
        init_scheduler()
        
        logger.info("✓ অ্যাপ সফলভাবে ইনিশিয়ালাইজ করা হয়েছে")


# ======== MAIN ========

if __name__ == '__main__':
    # অ্যাপ ইনিশিয়ালাইজ করা
    initialize_app()
    
    # সার্ভার চালানো
    logger.info(f"সার্ভার শুরু হচ্ছে পোর্ট {PORT} এ...")
    
    app.run(
        host='0.0.0.0',
        port=PORT,
        debug=app.config['DEBUG'],
        use_reloader=False  # Heroku এ reloader দিয়ে সমস্যা হয়
    )
