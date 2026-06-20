from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import json
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

db = SQLAlchemy()
Base = declarative_base()


class Lead(db.Model):
    """লিড মডেল"""
    __tablename__ = 'leads'
    
    id = db.Column(db.Integer, primary_key=True)
    school_name = db.Column(db.String(255))
    phone = db.Column(db.String(20))
    email = db.Column(db.String(255))
    address = db.Column(db.Text)
    district = db.Column(db.String(100))
    type = db.Column(db.String(50))  # School/Coaching/Madrasa
    source = db.Column(db.String(100))  # google_maps/facebook/linkedin
    website = db.Column(db.String(255), nullable=True)
    place_id = db.Column(db.String(255), nullable=True, index=True)
    business_status = db.Column(db.String(50), nullable=True)
    active_status = db.Column(db.String(30), default='unknown', index=True)
    rating = db.Column(db.Float, nullable=True)
    user_ratings_total = db.Column(db.Integer, nullable=True)
    canonical_key = db.Column(db.String(255), nullable=True, index=True)
    last_checked_at = db.Column(db.DateTime, nullable=True)
    last_enriched_at = db.Column(db.DateTime, nullable=True)
    email_checked_at = db.Column(db.DateTime, nullable=True)
    
    # Lead Scoring
    score = db.Column(db.Integer, default=0)
    segment = db.Column(db.String(20))  # Hot/Warm/Cold
    
    # Email Tracking
    email_sent = db.Column(db.Boolean, default=False)
    email_sent_date = db.Column(db.DateTime)
    email_opened = db.Column(db.Boolean, default=False)
    email_opened_time = db.Column(db.DateTime)
    email_clicked = db.Column(db.Boolean, default=False)
    clicked_link = db.Column(db.String(255), nullable=True)
    email_send_count = db.Column(db.Integer, default=0)
    last_email_sent = db.Column(db.DateTime)
    
    # WhatsApp Tracking
    whatsapp_sent = db.Column(db.Boolean, default=False)
    whatsapp_sent_date = db.Column(db.DateTime)
    whatsapp_delivered = db.Column(db.Boolean, default=False)
    whatsapp_read = db.Column(db.Boolean, default=False)
    whatsapp_sid = db.Column(db.String(255), nullable=True)
    whatsapp_send_count = db.Column(db.Integer, default=0)
    
    # Status
    status = db.Column(db.String(50), default='pending')  # pending/engaged/converted/objection
    objection_type = db.Column(db.String(100), nullable=True)  # price/features/competitor
    notes = db.Column(db.Text)
    
    # Conversion
    conversion_date = db.Column(db.DateTime)
    demo_booked = db.Column(db.Boolean, default=False)
    demo_date = db.Column(db.DateTime)
    trial_signed = db.Column(db.Boolean, default=False)
    paid_customer = db.Column(db.Boolean, default=False)
    subscription_plan = db.Column(db.String(50), nullable=True)  # Starter/Standard/Pro
    subscription_amount = db.Column(db.Float, nullable=True)
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    def __repr__(self):
        return f"<Lead {self.school_name}>"


class EmailLog(db.Model):
    """ইমেইল লগ ট্র্যাকিং"""
    __tablename__ = 'email_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'))
    subject = db.Column(db.String(255))
    template = db.Column(db.String(100))
    status = db.Column(db.String(20))  # sent/failed/opened/clicked
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    opened_at = db.Column(db.DateTime, nullable=True)
    clicked_at = db.Column(db.DateTime, nullable=True)
    clicked_link = db.Column(db.String(255), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    
    lead = db.relationship('Lead', backref='email_logs')


class WhatsAppLog(db.Model):
    """হোয়াটসঅ্যাপ লগ ট্র্যাকিং"""
    __tablename__ = 'whatsapp_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'))
    message = db.Column(db.Text)
    status = db.Column(db.String(20))  # sent/delivered/read/failed
    message_sid = db.Column(db.String(255))
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    delivered_at = db.Column(db.DateTime, nullable=True)
    read_at = db.Column(db.DateTime, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    
    lead = db.relationship('Lead', backref='whatsapp_logs')


class FollowUp(db.Model):
    """ফলো-আপ ট্র্যাক করা"""
    __tablename__ = 'followups'
    
    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey('leads.id'))
    followup_type = db.Column(db.String(50))  # email/whatsapp/call
    scheduled_for = db.Column(db.DateTime)
    status = db.Column(db.String(20))  # pending/completed/skipped
    completed_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text)
    
    lead = db.relationship('Lead', backref='followups')


class WorkflowLog(db.Model):
    """Operational log for lead-only automation jobs."""
    __tablename__ = 'workflow_logs'

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.String(100), index=True)
    level = db.Column(db.String(20), default='info', index=True)
    status = db.Column(db.String(30), index=True)
    message = db.Column(db.String(500))
    payload = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


# ডাটাবেস ফাংশনস

def init_db(app):
    """ডাটাবেস ইনিশিয়ালাইজ করা"""
    with app.app_context():
        db.create_all()
        ensure_schema()
        print("Database tables ensured")


def ensure_schema():
    """Keep older production databases compatible with new lead columns."""
    inspector = inspect(db.engine)
    if 'leads' not in inspector.get_table_names():
        return

    existing_columns = {column['name'] for column in inspector.get_columns('leads')}
    column_definitions = {
        'place_id': 'VARCHAR(255)',
        'business_status': 'VARCHAR(50)',
        'active_status': "VARCHAR(30) DEFAULT 'unknown'",
        'rating': 'FLOAT',
        'user_ratings_total': 'INTEGER',
        'canonical_key': 'VARCHAR(255)',
        'last_checked_at': 'TIMESTAMP',
        'last_enriched_at': 'TIMESTAMP',
        'email_checked_at': 'TIMESTAMP',
    }

    dialect = db.engine.dialect.name
    for column_name, column_type in column_definitions.items():
        if column_name in existing_columns:
            continue
        if dialect == 'postgresql':
            statement = f'ALTER TABLE leads ADD COLUMN IF NOT EXISTS {column_name} {column_type}'
        else:
            statement = f'ALTER TABLE leads ADD COLUMN {column_name} {column_type}'
        db.session.execute(text(statement))

    for statement in (
        'CREATE INDEX IF NOT EXISTS ix_leads_place_id ON leads (place_id)',
        'CREATE INDEX IF NOT EXISTS ix_leads_active_status ON leads (active_status)',
        'CREATE INDEX IF NOT EXISTS ix_leads_canonical_key ON leads (canonical_key)',
    ):
        db.session.execute(text(statement))

    db.session.commit()


def add_lead(school_name, phone, email, district, type, source, **kwargs):
    """Create or update a lead using stable identifiers to avoid duplicates."""
    try:
        phone = _normalize_phone(phone)
        kwargs['canonical_key'] = kwargs.get('canonical_key') or _build_canonical_key(
            school_name,
            district,
            kwargs.get('address')
        )

        lead = None
        if kwargs.get('place_id'):
            lead = Lead.query.filter_by(place_id=kwargs['place_id']).first()
        if not lead and phone:
            lead = Lead.query.filter_by(phone=phone).first()
        if not lead and kwargs.get('canonical_key'):
            lead = Lead.query.filter_by(canonical_key=kwargs['canonical_key']).first()

        fields = {
            'school_name': school_name,
            'phone': phone,
            'email': email,
            'district': district,
            'type': type,
            'source': source,
            **kwargs
        }

        if lead:
            _merge_lead_fields(lead, fields)
        else:
            lead = Lead(**fields)
            db.session.add(lead)

        db.session.commit()
        return lead
    except Exception as e:
        db.session.rollback()
        print(f"Error adding lead: {e}")
        return None


def _merge_lead_fields(lead, fields):
    always_refresh = {
        'business_status', 'active_status', 'rating', 'user_ratings_total',
        'last_checked_at', 'last_enriched_at', 'email_checked_at'
    }
    for key, value in fields.items():
        if not hasattr(lead, key) or value in (None, ''):
            continue
        current = getattr(lead, key)
        if current in (None, '') or key in always_refresh:
            setattr(lead, key, value)
    lead.updated_at = datetime.utcnow()


def _normalize_phone(phone):
    if not phone:
        return None
    phone = str(phone).strip().replace(' ', '').replace('-', '')
    if phone.startswith('+880'):
        phone = '0' + phone[4:]
    return phone[:20]


def _build_canonical_key(school_name, district, address=None):
    values = [school_name or '', district or '', address or '']
    normalized = '|'.join(' '.join(value.lower().split()) for value in values if value)
    return normalized[:255] if normalized else None


def log_workflow_event(job_id, status, message, level='info', payload=None):
    """Persist compact workflow events for the monitoring dashboard."""
    try:
        log = WorkflowLog(
            job_id=job_id,
            level=level,
            status=status,
            message=message[:500] if message else '',
            payload=json.dumps(payload, default=str) if payload is not None else None
        )
        db.session.add(log)
        db.session.commit()
        return log
    except Exception as e:
        db.session.rollback()
        print(f"Error logging workflow event: {e}")
        return None


def get_recent_workflow_logs(limit=100):
    logs = WorkflowLog.query.order_by(WorkflowLog.created_at.desc()).limit(limit).all()
    return [
        {
            'id': log.id,
            'job_id': log.job_id,
            'level': log.level,
            'status': log.status,
            'message': log.message,
            'payload': json.loads(log.payload) if log.payload else None,
            'created_at': log.created_at.isoformat() if log.created_at else None
        }
        for log in logs
    ]


def get_leads_by_segment(segment):
    """সেগমেন্ট অনুযায়ী লিড পাওয়া"""
    return Lead.query.filter_by(segment=segment).all()


def get_unsent_leads(limit=50):
    """যাদের ইমেইল পাঠানো হয়নি এমন লিড পাওয়া"""
    return Lead.query.filter_by(email_sent=False).limit(limit).all()


def update_lead_status(lead_id, **kwargs):
    """লিড স্ট্যাটাস আপডেট করা"""
    try:
        lead = Lead.query.get(lead_id)
        for key, value in kwargs.items():
            if hasattr(lead, key):
                setattr(lead, key, value)
        lead.updated_at = datetime.utcnow()
        db.session.commit()
        return lead
    except Exception as e:
        db.session.rollback()
        print(f"Error updating lead: {e}")
        return None


def log_email_event(lead_id, subject, status, **kwargs):
    """ইমেইল ইভেন্ট লগ করা"""
    try:
        log = EmailLog(
            lead_id=lead_id,
            subject=subject,
            status=status,
            **kwargs
        )
        db.session.add(log)
        db.session.commit()
        return log
    except Exception as e:
        db.session.rollback()
        print(f"Error logging email: {e}")
        return None


def log_whatsapp_event(lead_id, message, status, message_sid, **kwargs):
    """হোয়াটসঅ্যাপ ইভেন্ট লগ করা"""
    try:
        log = WhatsAppLog(
            lead_id=lead_id,
            message=message,
            status=status,
            message_sid=message_sid,
            **kwargs
        )
        db.session.add(log)
        db.session.commit()
        return log
    except Exception as e:
        db.session.rollback()
        print(f"Error logging whatsapp: {e}")
        return None


def get_stats():
    """সামগ্রিক পরিসংখ্যান পাওয়া"""
    total_leads = Lead.query.count()
    hot_leads = Lead.query.filter_by(segment='Hot').count()
    warm_leads = Lead.query.filter_by(segment='Warm').count()
    cold_leads = Lead.query.filter_by(segment='Cold').count()
    emails_sent = Lead.query.filter_by(email_sent=True).count()
    emails_opened = Lead.query.filter_by(email_opened=True).count()
    converted = Lead.query.filter_by(paid_customer=True).count()
    active_leads = Lead.query.filter_by(active_status='active').count()
    closed_leads = Lead.query.filter_by(active_status='closed').count()
    
    return {
        'total_leads': total_leads,
        'active_leads': active_leads,
        'closed_leads': closed_leads,
        'hot_leads': hot_leads,
        'warm_leads': warm_leads,
        'cold_leads': cold_leads,
        'emails_sent': emails_sent,
        'emails_opened': emails_opened,
        'email_open_rate': f"{(emails_opened/emails_sent*100) if emails_sent > 0 else 0:.1f}%",
        'converted': converted,
        'conversion_rate': f"{(converted/total_leads*100) if total_leads > 0 else 0:.1f}%"
    }
