import os
import re
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from apscheduler.schedulers.background import BackgroundScheduler
from loguru import logger
import atexit
from datetime import datetime, timedelta
from functools import wraps

from config import Config, get_config
from src.database import (
    Lead,
    SearchTask,
    db,
    duplicate_group_counts,
    get_api_usage_count,
    get_recent_workflow_logs,
    get_stats,
    init_db,
    log_workflow_event,
)
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
scheduler = BackgroundScheduler(timezone=Config.SCHEDULER_TIMEZONE)

# পোর্ট সেটআপ
PORT = os.getenv('PORT', 5000)

LEAD_JOB_IDS = {
    'lead_generation_cycle',
    'public_dataset_collection',
    'openstreetmap_collection',
    'enrich_contact_info',
    'find_emails',
    'clean_leads',
    'sync_leads_to_sheets',
    'usa_local_business_collection'
}


def _request_admin_token():
    auth_header = request.headers.get('Authorization', '')
    if auth_header.lower().startswith('bearer '):
        return auth_header.split(' ', 1)[1].strip()
    return request.headers.get('X-Admin-Token') or request.args.get('token')


def require_admin(func):
    """Protect operational endpoints that can spend credits or send messages."""
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not Config.ADMIN_API_TOKEN:
            if Config.ENVIRONMENT == 'production':
                return jsonify({
                    'status': 'error',
                    'message': 'Admin API token is not configured'
                }), 503
            return func(*args, **kwargs)

        if _request_admin_token() != Config.ADMIN_API_TOKEN:
            return jsonify({
                'status': 'error',
                'message': 'Unauthorized'
            }), 401

        return func(*args, **kwargs)

    return wrapper


def run_scheduled_job(job_id, func, *args, **kwargs):
    """Run scheduler jobs inside Flask app context and log failures."""
    try:
        logger.info(f"Scheduled job started: {job_id}")
        with app.app_context():
            log_workflow_event(job_id, 'started', f'{job_id} started')
            result = func(*args, **kwargs)
            log_workflow_event(job_id, 'success', f'{job_id} finished', payload=result)
        logger.info(f"Scheduled job finished: {job_id} result={result}")
        return result
    except Exception as e:
        logger.exception(f"Scheduled job failed: {job_id}: {e}")
        with app.app_context():
            log_workflow_event(job_id, 'failed', str(e), level='error')
        raise


def add_interval_job(job_id, name, func, minutes, job_kwargs=None, first_run_delay_minutes=2):
    first_run_at = datetime.now(scheduler.timezone) + timedelta(minutes=first_run_delay_minutes)
    scheduler.add_job(
        func=run_scheduled_job,
        args=[job_id, func],
        kwargs=job_kwargs or {},
        trigger="interval",
        minutes=minutes,
        id=job_id,
        name=name,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=900,
        next_run_time=first_run_at
    )


def _parse_hhmm(value, default_hour, default_minute):
    """Parse an 'HH:MM' schedule string, falling back to defaults."""
    try:
        hour_str, minute_str = str(value).split(':')
        return int(hour_str), int(minute_str)
    except (ValueError, AttributeError):
        return default_hour, default_minute


def add_cron_job(job_id, name, func, hour, minute, job_kwargs=None):
    scheduler.add_job(
        func=run_scheduled_job,
        args=[job_id, func],
        kwargs=job_kwargs or {},
        trigger="cron",
        hour=hour,
        minute=minute,
        id=job_id,
        name=name,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=900
    )


def init_scheduler():
    """Set up scheduled jobs based on the configured scheduler mode."""
    if scheduler.running:
        logger.info("Scheduler is already running")
        return

    logger.info(
        f"Setting up scheduler: mode={Config.SCHEDULER_MODE}, "
        f"timezone={Config.SCHEDULER_TIMEZONE}"
    )

    lead_collection_enabled = (
        Config.ENABLE_LEAD_COLLECTION
        and Config.SCHEDULER_MODE in ('all', 'lead_collection')
    )
    marketing_enabled = (
        Config.ENABLE_MARKETING_JOBS
        and Config.SCHEDULER_MODE in ('all', 'marketing')
    )
    social_enabled = (
        Config.ENABLE_FACEBOOK_POSTING
        and Config.SCHEDULER_MODE in ('all', 'marketing', 'social')
    )
    reporting_enabled = (
        Config.ENABLE_REPORTING_JOBS
        and Config.SCHEDULER_MODE in ('all', 'reporting')
    )

    if lead_collection_enabled:
        if Config.ENABLE_BD_EDUCATION_COLLECTION:
            add_interval_job(
                'lead_generation_cycle',
                'Autonomous Bangladesh education lead generation cycle',
                lead_collector.run_autonomous_cycle,
                Config.LEAD_COLLECTION_INTERVAL_MINUTES
            )
        if Config.ENABLE_BD_EDUCATION_COLLECTION:
            add_interval_job(
                'enrich_contact_info',
                'Enrich lead phone, website, and active status',
                lead_collector.enrich_missing_contact_info,
                Config.CONTACT_ENRICH_INTERVAL_MINUTES,
                job_kwargs={'limit': Config.CONTACT_ENRICH_LIMIT, 'find_email': False},
                first_run_delay_minutes=5
            )
        if Config.ENABLE_BD_EDUCATION_COLLECTION and Config.ENABLE_PUBLIC_DATASET_COLLECTION:
            add_interval_job(
                'public_dataset_collection',
                'Collect leads from public open-data contact datasets',
                lead_collector.collect_from_public_datasets,
                Config.PUBLIC_DATASET_INTERVAL_MINUTES,
                job_kwargs={'limit': Config.PUBLIC_DATASET_BATCH_SIZE},
                first_run_delay_minutes=6
            )
        if Config.ENABLE_BD_EDUCATION_COLLECTION and Config.ENABLE_OPENSTREETMAP_COLLECTION:
            add_interval_job(
                'openstreetmap_collection',
                'Collect leads from OpenStreetMap public data',
                lead_collector.collect_from_openstreetmap,
                Config.OPENSTREETMAP_INTERVAL_MINUTES,
                first_run_delay_minutes=7
            )
        if Config.ENABLE_USA_LOCAL_BUSINESS_COLLECTION:
            add_interval_job(
                'usa_local_business_collection',
                'Collect USA local-business prospects',
                lead_collector.collect_usa_local_businesses,
                Config.USA_LOCAL_BUSINESS_INTERVAL_MINUTES,
                first_run_delay_minutes=3
            )
        add_interval_job(
            'find_emails',
            'Find lead emails',
            lead_collector.enrich_missing_emails,
            Config.EMAIL_ENRICH_INTERVAL_MINUTES,
            job_kwargs={'limit': Config.EMAIL_ENRICH_LIMIT},
            first_run_delay_minutes=10
        )
        add_interval_job(
            'clean_leads',
            'Clean and score leads',
            lead_collector.clean_and_score_leads,
            360,
            first_run_delay_minutes=15
        )
        add_interval_job(
            'sync_leads_to_sheets',
            'Sync active leads to Google Sheets',
            reporting_manager.sync_leads_to_sheet,
            Config.SHEET_SYNC_INTERVAL_MINUTES,
            first_run_delay_minutes=20
        )

    if marketing_enabled:
        engagement = Config.SCHEDULE_CONFIG.get('engagement', {})
        tracking_cfg = Config.SCHEDULE_CONFIG.get('tracking', {})

        if Config.ENABLE_EMAIL_CAMPAIGN:
            hour, minute = _parse_hhmm(engagement.get('email_campaign'), 10, 30)
            add_cron_job('email_campaign', 'Email campaign', email_campaign.run_campaign, hour, minute)

        if Config.ENABLE_WHATSAPP_CAMPAIGN:
            hour, minute = _parse_hhmm(engagement.get('whatsapp_campaign'), 12, 30)
            add_cron_job('whatsapp_campaign', 'WhatsApp campaign', whatsapp_campaign.send_campaign, hour, minute)

        if Config.ENABLE_EMAIL_FOLLOWUPS:
            hour, minute = _parse_hhmm(engagement.get('email_followups'), 16, 0)
            add_cron_job('email_followups', 'Email follow-up drip', email_campaign.run_followups, hour, minute)

        hour, minute = _parse_hhmm(tracking_cfg.get('email_tracking'), 20, 0)
        add_cron_job('tracking', 'Tracking and metrics', tracking_manager.run_all, hour, minute)

    if social_enabled:
        add_interval_job(
            'facebook_schedule_generation',
            'Generate approval-gated Facebook content calendar',
            facebook_poster.ensure_content_calendar,
            360,
            first_run_delay_minutes=2
        )
        post_times = Config.FACEBOOK_POST_TIMES[:Config.FACEBOOK_POSTS_PER_DAY]
        for index, post_time in enumerate(post_times, start=1):
            hour, minute = _parse_hhmm(post_time, 14, 0)
            add_cron_job(
                f'facebook_posting_{index}',
                f'Facebook approved post slot {index}',
                facebook_poster.post_next_approved,
                hour,
                minute
            )
        if Config.FACEBOOK_TEST_POST_ON_DEPLOY:
            scheduler.add_job(
                func=run_scheduled_job,
                args=['facebook_test_post_once', facebook_poster.post_autonomous_test_once],
                trigger="date",
                run_date=datetime.now(scheduler.timezone) + timedelta(minutes=3),
                id='facebook_test_post_once',
                name='Post one autonomous Facebook test post',
                max_instances=1,
                misfire_grace_time=900
            )

    if reporting_enabled:
        reporting_cfg = Config.SCHEDULE_CONFIG.get('reporting', {})
        hour, minute = _parse_hhmm(reporting_cfg.get('daily_report'), 21, 30)
        add_cron_job('daily_report', 'Daily report', reporting_manager.run_daily, hour, minute)

    scheduler.start()
    logger.info(f"Scheduler started with {len(scheduler.get_jobs())} jobs")

    atexit.register(lambda: scheduler.shutdown())


# ======== FLASK ROUTES ========

@app.route('/', methods=['GET'])
def index():
    """Basic service overview with monitoring links."""
    return jsonify({
        'service': 'marketingbot',
        'status': 'running',
        'mode': Config.SCHEDULER_MODE,
        'timezone': Config.SCHEDULER_TIMEZONE,
        'endpoints': {
            'health': '/health',
            'lead_collection_status': '/lead-collection/status',
            'lead_collection_workflow': '/lead-collection/workflow',
            'lead_collection_dashboard': '/lead-collection/dashboard',
            'stats': '/stats',
            'scheduler_jobs': '/scheduler/jobs',
            'public_dataset_collection': 'POST /trigger/public-dataset-collection',
            'openstreetmap_collection': 'POST /trigger/openstreetmap-collection',
            'usa_local_business_collection': 'POST /trigger/usa-local-business-collection',
            'usa_local_business_bulk_collection': 'POST /trigger/usa-local-business-bulk-collection',
            'sync_leads_to_sheets': 'POST /trigger/sync-leads-to-sheets',
            'enrich_contact_info': 'POST /trigger/enrich-contact-info',
            'find_emails': 'POST /trigger/find-emails',
            'facebook_calendar_status': '/facebook-calendar/status',
            'generate_facebook_schedule': 'POST /trigger/generate-facebook-schedule',
            'post_next_approved_facebook': 'POST /trigger/facebook-posting'
        }
    }), 200


@app.route('/health', methods=['GET'])
def health_check():
    """হেলথ চেক এন্ডপয়েন্ট"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'scheduler': 'running' if scheduler.running else 'stopped',
        'jobs': [job.id for job in scheduler.get_jobs()]
    }), 200


@app.route('/privacy-policy', methods=['GET'])
def privacy_policy():
    """Public privacy policy for Meta app review and page publishing."""
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CreatifyBD MarketingBot Privacy Policy</title>
  <style>
    body { margin: 0; font-family: Arial, sans-serif; color: #1f2a25; background: #f7f8f6; line-height: 1.6; }
    main { max-width: 840px; margin: 0 auto; padding: 40px 20px 64px; }
    h1 { font-size: 32px; line-height: 1.2; margin: 0 0 8px; }
    h2 { font-size: 20px; margin-top: 28px; }
    p, li { font-size: 16px; }
    .updated { color: #5c6b63; margin-bottom: 28px; }
  </style>
</head>
<body>
  <main>
    <h1>CreatifyBD MarketingBot Privacy Policy</h1>
    <p class="updated">Last updated: June 30, 2026</p>
    <p>CreatifyBD MarketingBot is an internal automation tool used by CreatifyBD to manage public Facebook Page publishing, lead organization, reporting, and marketing workflow operations.</p>
    <h2>Information We Use</h2>
    <p>The tool may process business contact details, public business profile information, website information, social media page metadata, campaign performance data, and Facebook Page publishing data that CreatifyBD is authorized to access.</p>
    <h2>How We Use Information</h2>
    <ul>
      <li>To prepare, schedule, and publish content on CreatifyBD-owned social media pages.</li>
      <li>To maintain marketing calendars, workflow logs, and operational reports.</li>
      <li>To research public business information for lawful B2B marketing operations.</li>
      <li>To improve content quality, relevance, and campaign performance.</li>
    </ul>
    <h2>Facebook Platform Data</h2>
    <p>The tool uses Facebook permissions only for CreatifyBD-owned assets and authorized Page management tasks. We do not sell Facebook Platform data. We do not use Facebook Platform data for unrelated third-party advertising networks.</p>
    <h2>Data Sharing</h2>
    <p>We do not sell personal data. Data may be processed by service providers used to operate the tool, including hosting, spreadsheets, email systems, analytics, and AI providers, only for CreatifyBD business operations.</p>
    <h2>Retention and Deletion</h2>
    <p>Operational records are retained only as long as needed for business, legal, security, and reporting purposes. To request deletion of data associated with CreatifyBD MarketingBot, contact us using the email below.</p>
    <h2>Contact</h2>
    <p>For privacy questions or data deletion requests, contact: creatifybd@gmail.com</p>
  </main>
</body>
</html>
"""
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


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


@app.route('/lead-collection/status', methods=['GET'])
def lead_collection_status():
    """Public-safe lead collection monitoring without exposing lead contact data."""
    try:
        now = datetime.utcnow()
        last_24h = now - timedelta(hours=24)
        last_7d = now - timedelta(days=7)

        latest_lead = Lead.query.order_by(Lead.created_at.desc()).first()
        sources = {}
        districts = {}
        contact_quality = {}
        markets = {}

        for source, count in db.session.query(Lead.source, db.func.count(Lead.id)).group_by(Lead.source):
            sources[source or 'unknown'] = count

        for district, count in db.session.query(Lead.district, db.func.count(Lead.id)).group_by(Lead.district):
            districts[district or 'unknown'] = count

        for quality, count in db.session.query(Lead.contact_quality, db.func.count(Lead.id)).group_by(Lead.contact_quality):
            contact_quality[quality or 'unknown'] = count

        for market, count in db.session.query(Lead.market, db.func.count(Lead.id)).group_by(Lead.market):
            markets[market or 'unknown'] = count

        lead_jobs = []
        for job in scheduler.get_jobs():
            if job.id in LEAD_JOB_IDS:
                lead_jobs.append({
                    'id': job.id,
                    'name': job.name,
                    'next_run_time': str(job.next_run_time) if job.next_run_time else None,
                    'trigger': str(job.trigger)
                })

        total_leads = Lead.query.count()
        qualified_leads = Lead.query.filter(Lead.qualification_status == 'qualified').count()
        valid_whatsapp = Lead.query.filter(Lead.phone_valid == True).count()
        leads_with_email = Lead.query.filter(Lead.email != None, Lead.email != '').count()
        leads_with_website = Lead.query.filter(Lead.website != None, Lead.website != '').count()

        return jsonify({
            'status': 'success',
            'scheduler': 'running' if scheduler.running else 'stopped',
            'mode': Config.SCHEDULER_MODE,
            'timezone': Config.SCHEDULER_TIMEZONE,
            'quota_usage_last_24h': {
                'google_places': {
                    'used': get_api_usage_count('google_places', hours=24),
                    'limit': Config.GOOGLE_PLACES_DAILY_CALL_LIMIT
                },
                'hunter': {
                    'used': get_api_usage_count('hunter', hours=24),
                    'limit': Config.HUNTER_DAILY_CALL_LIMIT
                }
            },
            'search_coverage': {
                'total_tasks': SearchTask.query.count(),
                'active_tasks': SearchTask.query.filter(SearchTask.status == 'active').count(),
                'exhausted_tasks': SearchTask.query.filter(SearchTask.status == 'exhausted').count(),
                'never_run_tasks': SearchTask.query.filter(SearchTask.last_run_at == None).count()
            },
            'total_leads': total_leads,
            'active_leads': Lead.query.filter(Lead.active_status == 'active').count(),
            'closed_or_inactive_leads': Lead.query.filter(Lead.active_status == 'closed').count(),
            'unknown_active_status_leads': Lead.query.filter((Lead.active_status == None) | (Lead.active_status == 'unknown')).count(),
            'leads_with_place_id': Lead.query.filter(Lead.place_id != None, Lead.place_id != '').count(),
            'leads_with_phone': Lead.query.filter(Lead.phone != None, Lead.phone != '').count(),
            'leads_with_valid_whatsapp_phone': valid_whatsapp,
            'leads_with_email': leads_with_email,
            'leads_with_website': leads_with_website,
            'qualified_leads': qualified_leads,
            'unusable_no_contact_leads': Lead.query.filter(Lead.qualification_status == 'unusable').count(),
            'data_quality': {
                'qualified_rate': round((qualified_leads / total_leads * 100), 2) if total_leads else 0,
                'whatsapp_ready_rate': round((valid_whatsapp / total_leads * 100), 2) if total_leads else 0,
                'email_rate': round((leads_with_email / total_leads * 100), 2) if total_leads else 0,
                'website_rate': round((leads_with_website / total_leads * 100), 2) if total_leads else 0,
                'contact_quality_counts': contact_quality,
                'duplicate_groups': duplicate_group_counts()
            },
            'leads_last_24h': Lead.query.filter(Lead.created_at >= last_24h).count(),
            'leads_last_7d': Lead.query.filter(Lead.created_at >= last_7d).count(),
            'last_lead_at': latest_lead.created_at.isoformat() if latest_lead else None,
            'source_counts': sources,
            'market_counts': markets,
            'district_counts': districts,
            'lead_jobs': lead_jobs,
            'recent_workflow_logs': get_recent_workflow_logs(limit=20),
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Lead collection status error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/lead-collection/workflow', methods=['GET'])
def lead_collection_workflow():
    """Clean workflow log feed for lead-generation monitoring."""
    try:
        limit = min(int(request.args.get('limit', 100)), 300)
        return jsonify({
            'status': 'success',
            'scheduler': 'running' if scheduler.running else 'stopped',
            'total_leads': Lead.query.count(),
            'active_leads': Lead.query.filter(Lead.active_status == 'active').count(),
            'leads_with_phone': Lead.query.filter(Lead.phone != None, Lead.phone != '').count(),
            'leads_with_valid_whatsapp_phone': Lead.query.filter(Lead.phone_valid == True).count(),
            'leads_with_email': Lead.query.filter(Lead.email != None, Lead.email != '').count(),
            'leads_with_website': Lead.query.filter(Lead.website != None, Lead.website != '').count(),
            'qualified_leads': Lead.query.filter(Lead.qualification_status == 'qualified').count(),
            'unusable_no_contact_leads': Lead.query.filter(Lead.qualification_status == 'unusable').count(),
            'logs': get_recent_workflow_logs(limit=limit),
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Lead workflow log error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/lead-collection/dashboard', methods=['GET'])
def lead_collection_dashboard():
    """Small auto-refreshing dashboard for non-technical workflow monitoring."""
    html = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PathshalaPro Lead Monitor</title>
  <style>
    :root { color-scheme: light; font-family: Inter, Arial, sans-serif; }
    body { margin: 0; background: #f4f7f6; color: #17211d; }
    header { padding: 18px 24px; background: #173c33; color: white; display: flex; justify-content: space-between; gap: 16px; align-items: center; }
    h1 { margin: 0; font-size: 22px; letter-spacing: 0; }
    main { padding: 20px 24px 32px; max-width: 1200px; margin: 0 auto; }
    .status { font-size: 14px; color: #c8efe3; }
    .metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 18px; }
    .metric { background: white; border: 1px solid #d8e1dd; border-radius: 8px; padding: 14px; }
    .metric strong { display: block; font-size: 26px; margin-top: 6px; }
    .section { background: white; border: 1px solid #d8e1dd; border-radius: 8px; overflow: hidden; }
    .section h2 { margin: 0; padding: 14px 16px; font-size: 16px; border-bottom: 1px solid #d8e1dd; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
    th, td { padding: 10px 12px; border-bottom: 1px solid #edf1ef; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
    th { background: #f8faf9; color: #4b5c55; font-weight: 700; }
    .pill { display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; }
    .success { background: #dff5e7; color: #176a35; }
    .started { background: #e3efff; color: #2257a3; }
    .failed { background: #ffe3df; color: #a43122; }
    .empty { padding: 18px; color: #63736d; }
    @media (max-width: 640px) { header { align-items: flex-start; flex-direction: column; } main { padding: 14px; } th:nth-child(5), td:nth-child(5) { display: none; } }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>PathshalaPro Lead Monitor</h1>
      <div class="status" id="status">Loading workflow...</div>
    </div>
    <div class="status" id="updated"></div>
  </header>
  <main>
    <div class="metrics">
      <div class="metric">Total leads<strong id="total">0</strong></div>
      <div class="metric">Active leads<strong id="active">0</strong></div>
      <div class="metric">Qualified<strong id="qualified">0</strong></div>
      <div class="metric">WhatsApp-ready phones<strong id="phones">0</strong></div>
      <div class="metric">Websites<strong id="websites">0</strong></div>
      <div class="metric">Emails<strong id="emails">0</strong></div>
    </div>
    <div class="section">
      <h2>Recent Workflow Events</h2>
      <div id="logs"></div>
    </div>
  </main>
  <script>
    const fmt = (value) => value || '';
    const payloadText = (payload) => payload ? JSON.stringify(payload) : '';
    async function refresh() {
      const response = await fetch('/lead-collection/workflow?limit=80', { cache: 'no-store' });
      const data = await response.json();
      document.getElementById('status').textContent = `Scheduler: ${data.scheduler}`;
      document.getElementById('updated').textContent = `Updated: ${new Date().toLocaleString()}`;
      document.getElementById('total').textContent = data.total_leads;
      document.getElementById('active').textContent = data.active_leads;
      document.getElementById('qualified').textContent = data.qualified_leads;
      document.getElementById('phones').textContent = data.leads_with_valid_whatsapp_phone;
      document.getElementById('websites').textContent = data.leads_with_website;
      document.getElementById('emails').textContent = data.leads_with_email;
      const logs = data.logs || [];
      if (!logs.length) {
        document.getElementById('logs').innerHTML = '<div class="empty">No workflow events have been recorded yet. New events will appear after the next scheduled job.</div>';
        return;
      }
      const rows = logs.map((log) => `
        <tr>
          <td>${fmt(log.created_at)}</td>
          <td>${fmt(log.job_id)}</td>
          <td><span class="pill ${fmt(log.status)}">${fmt(log.status)}</span></td>
          <td>${fmt(log.message)}</td>
          <td>${payloadText(log.payload)}</td>
        </tr>`).join('');
      document.getElementById('logs').innerHTML = `
        <table>
          <thead><tr><th>Time</th><th>Job</th><th>Status</th><th>Message</th><th>Payload</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`;
    }
    refresh();
    setInterval(refresh, 15000);
  </script>
</body>
</html>
"""
    return html, 200, {'Content-Type': 'text/html; charset=utf-8'}


@app.route('/facebook-calendar/status', methods=['GET'])
def facebook_calendar_status():
    """Public-safe Facebook content calendar diagnostics without post copy or secrets."""
    try:
        return jsonify({
            'status': 'success',
            'data': facebook_poster.calendar_status(),
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Facebook calendar status error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/lead-collection', methods=['POST'])
@require_admin
def trigger_lead_collection():
    """ম্যানুয়ালি লিড সংগ্রহ ট্রিগার করা"""
    try:
        result = lead_collector.run_autonomous_cycle()
        return jsonify({
            'status': 'success',
            'data': result,
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/public-dataset-collection', methods=['POST'])
@require_admin
def trigger_public_dataset_collection():
    """Manually collect leads from public open-data contact datasets."""
    try:
        payload = request.json if request.is_json else {}
        limit = payload.get('limit', Config.PUBLIC_DATASET_BATCH_SIZE)
        result = lead_collector.collect_from_public_datasets(limit=limit)
        return jsonify({
            'status': 'success',
            'data': {'public_dataset': result},
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Public dataset trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/openstreetmap-collection', methods=['POST'])
@require_admin
def trigger_openstreetmap_collection():
    """Manually collect institute leads from OpenStreetMap public data."""
    try:
        payload = request.json if request.is_json else {}
        cells_per_run = payload.get('cells_per_run', payload.get('districts_per_run', Config.OPENSTREETMAP_DISTRICTS_PER_RUN))
        results_per_cell = payload.get('results_per_cell', payload.get('results_per_district', Config.OPENSTREETMAP_RESULTS_PER_DISTRICT))
        result = lead_collector.collect_from_openstreetmap(
            districts_per_run=cells_per_run,
            results_per_district=results_per_cell
        )
        return jsonify({
            'status': 'success',
            'data': {'openstreetmap': result},
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"OpenStreetMap trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/sync-leads-to-sheets', methods=['POST'])
@require_admin
def trigger_sync_leads_to_sheets():
    """Manually sync collected leads to the Leads worksheet."""
    try:
        result = reporting_manager.sync_leads_to_sheet()
        return jsonify({
            'status': 'success',
            'data': result,
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Lead sheet sync trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/usa-local-business-collection', methods=['POST'])
@require_admin
def trigger_usa_local_business_collection():
    """Manually collect USA local-business prospects from free public data."""
    try:
        payload = request.json if request.is_json else {}
        result = lead_collector.collect_usa_local_businesses(
            locations_per_run=payload.get(
                'locations_per_run', Config.USA_LOCAL_BUSINESS_LOCATIONS_PER_RUN
            ),
            results_per_location=payload.get(
                'results_per_location', Config.USA_LOCAL_BUSINESS_RESULTS_PER_LOCATION
            ),
        )
        return jsonify({
            'status': 'success',
            'data': {'usa_local_businesses': result},
            'timestamp': datetime.now().isoformat(),
        }), 200
    except Exception as e:
        logger.error(f"USA local-business trigger error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/trigger/usa-local-business-bulk-collection', methods=['POST'])
@require_admin
def trigger_usa_local_business_bulk_collection():
    """Run multiple USA local-business batches without touching BD sources."""
    try:
        payload = request.json if request.is_json else {}
        batches = max(1, min(int(payload.get('batches', 3)), 12))
        locations_per_run = payload.get(
            'locations_per_run', Config.USA_LOCAL_BUSINESS_LOCATIONS_PER_RUN
        )
        results_per_location = payload.get(
            'results_per_location', Config.USA_LOCAL_BUSINESS_RESULTS_PER_LOCATION
        )
        totals = {'processed': 0, 'created': 0, 'updated': 0, 'unchanged': 0, 'skipped': 0}
        batch_results = []
        for _ in range(batches):
            result = lead_collector.collect_usa_local_businesses(
                locations_per_run=locations_per_run,
                results_per_location=results_per_location,
            )
            batch_results.append(result)
            for key in totals:
                totals[key] += int(result.get(key, 0) or 0)
        sheet_result = reporting_manager.sync_leads_to_sheet()
        return jsonify({
            'status': 'success',
            'data': {
                'usa_local_businesses': totals,
                'batches': batch_results,
                'sheet_sync': sheet_result,
            },
            'timestamp': datetime.now().isoformat(),
        }), 200
    except Exception as e:
        logger.error(f"USA local-business bulk trigger error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/trigger/enrich-contact-info', methods=['POST'])
@require_admin
def trigger_enrich_contact_info():
    """Manually enrich existing leads with missing phone, website, and email."""
    try:
        limit = request.json.get('limit', Config.CONTACT_ENRICH_LIMIT) if request.is_json else Config.CONTACT_ENRICH_LIMIT
        find_email = request.json.get('find_email', False) if request.is_json else False
        updated = lead_collector.enrich_missing_contact_info(limit=limit, find_email=find_email)
        sheet_result = reporting_manager.sync_leads_to_sheet()
        return jsonify({
            'status': 'success',
            'updated': updated,
            'sheet_sync': sheet_result,
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Contact enrichment trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/email-campaign', methods=['POST'])
@require_admin
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


@app.route('/trigger/email-followups', methods=['POST'])
@require_admin
def trigger_email_followups():
    """ম্যানুয়ালি ফলো-আপ ইমেইল ড্রিপ ট্রিগার করা"""
    try:
        result = email_campaign.run_followups()
        return jsonify({
            'status': 'success',
            'message': f'{result} ফলো-আপ ইমেইল পাঠানো হয়েছে',
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Email follow-up trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/whatsapp-campaign', methods=['POST'])
@require_admin
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
@require_admin
def trigger_facebook_posting():
    """ম্যানুয়ালি ফেসবুক পোস্টিং ট্রিগার করা"""
    try:
        result = facebook_poster.post_next_approved()
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


@app.route('/trigger/generate-facebook-schedule', methods=['POST'])
@require_admin
def trigger_generate_facebook_schedule():
    """Generate or top up the approval-gated Facebook content calendar."""
    try:
        payload = request.json if request.is_json else {}
        result = facebook_poster.ensure_content_calendar(
            horizon_days=payload.get('horizon_days', Config.FACEBOOK_CONTENT_HORIZON_DAYS)
        )
        return jsonify({
            'status': 'success',
            'data': result,
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Facebook schedule generation error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


@app.route('/trigger/tracking', methods=['POST'])
@require_admin
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
@require_admin
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


@app.route('/trigger/find-emails', methods=['POST'])
@require_admin
def trigger_find_emails():
    """Manually run public website and quota-aware email enrichment."""
    try:
        limit = request.json.get('limit', Config.EMAIL_ENRICH_LIMIT) if request.is_json else Config.EMAIL_ENRICH_LIMIT
        force = bool(request.json.get('force', False)) if request.is_json else False
        updated = lead_collector.enrich_missing_emails(limit=limit, force=force)
        sheet_result = reporting_manager.sync_leads_to_sheet()
        return jsonify({
            'status': 'success',
            'updated': updated,
            'sheet_sync': sheet_result,
            'timestamp': datetime.now().isoformat()
        }), 200
    except Exception as e:
        logger.error(f"Email enrichment trigger error: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500


def _lead_id_from_brevo_tags(event):
    """Extract the lead id from Brevo event tags like 'lead-123'."""
    tags = event.get('tags') or event.get('tag') or []
    if isinstance(tags, str):
        tags = [tags]
    for tag in tags:
        match = re.match(r'lead-(\d+)', str(tag))
        if match:
            return int(match.group(1))
    return None


# 1x1 transparent GIF used as an email open-tracking pixel.
_TRACKING_PIXEL = (
    b'GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01'
    b'\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
)


@app.route('/track/open/<int:lead_id>', methods=['GET'])
def track_open(lead_id):
    """Email open-tracking pixel: stamps the lead as opened, returns a 1x1 GIF."""
    try:
        lead = Lead.query.get(lead_id)
        if lead and not lead.email_opened:
            lead.email_opened = True
            lead.email_opened_time = datetime.utcnow()
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.error(f"Open tracking error for lead {lead_id}: {e}")
    return app.response_class(
        _TRACKING_PIXEL,
        mimetype='image/gif',
        headers={'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0'}
    )


@app.route('/webhooks/brevo', methods=['POST'])
def brevo_webhook():
    """Brevo event webhook: marks email_opened / email_clicked on the lead."""
    try:
        payload = request.json or {}
        events = payload if isinstance(payload, list) else [payload]

        for event in events:
            event_type = (event.get('event') or '').lower()
            lead_id = _lead_id_from_brevo_tags(event)
            lead = Lead.query.get(lead_id) if lead_id else None
            if not lead and event.get('email'):
                lead = Lead.query.filter_by(email=str(event['email']).strip().lower()).first()
            if not lead:
                continue

            if event_type in ('opened', 'unique_opened', 'open'):
                lead.email_opened = True
                lead.email_opened_time = datetime.utcnow()
            elif event_type in ('click', 'clicks', 'unique_clicked'):
                lead.email_clicked = True
                if event.get('link'):
                    lead.clicked_link = str(event['link'])[:255]

        db.session.commit()
        return jsonify({'status': 'success'}), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f"Brevo webhook error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/webhooks/sendgrid', methods=['POST'])
def sendgrid_webhook():
    """SendGrid ইভেন্ট ওয়েবহুক"""
    try:
        events = request.json or []

        for event in events:
            lead_id = event.get('lead_id')
            if not lead_id:
                continue
            lead = Lead.query.get(lead_id)
            if not lead:
                continue
            if event.get('event') == 'open':
                lead.email_opened = True
                lead.email_opened_time = datetime.utcnow()
            elif event.get('event') == 'click':
                lead.email_clicked = True
        
        db.session.commit()
        return jsonify({'status': 'success'}), 200
    
    except Exception as e:
        db.session.rollback()
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
@require_admin
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
        log_workflow_event(
            'app',
            'success',
            'Application initialized and scheduler started',
            payload={'mode': Config.SCHEDULER_MODE, 'timezone': Config.SCHEDULER_TIMEZONE}
        )
        
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
