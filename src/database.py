from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timedelta
import json
import os
import re
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
    phone_e164 = db.Column(db.String(20), nullable=True, index=True)
    whatsapp_url = db.Column(db.String(255), nullable=True)
    phone_valid = db.Column(db.Boolean, default=False, index=True)
    phone_type = db.Column(db.String(20), nullable=True)
    email = db.Column(db.String(255))
    address = db.Column(db.Text)
    district = db.Column(db.String(100))
    type = db.Column(db.String(50))  # School/Coaching/Madrasa
    source = db.Column(db.String(100))  # google_maps/facebook/linkedin
    source_record_id = db.Column(db.String(255), nullable=True, index=True)
    source_confidence = db.Column(db.Integer, default=50)
    eiin = db.Column(db.String(50), nullable=True, index=True)
    upazila = db.Column(db.String(100), nullable=True, index=True)
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
    qualification_status = db.Column(db.String(30), default='needs_enrichment', index=True)
    contact_quality = db.Column(db.String(30), default='no_contact')
    duplicate_key = db.Column(db.String(255), nullable=True, index=True)
    last_verified_at = db.Column(db.DateTime, nullable=True)
    
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


class ApiUsageLog(db.Model):
    """Daily API usage ledger for quota/cost guardrails."""
    __tablename__ = 'api_usage_logs'

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(50), index=True)
    endpoint = db.Column(db.String(100), nullable=True)
    status_code = db.Column(db.Integer, nullable=True)
    success = db.Column(db.Boolean, default=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class SearchTask(db.Model):
    """Persistent coverage tracker for district + keyword searches."""
    __tablename__ = 'search_tasks'

    id = db.Column(db.Integer, primary_key=True)
    district = db.Column(db.String(100), index=True)
    keyword = db.Column(db.String(100), index=True)
    status = db.Column(db.String(30), default='active', index=True)
    priority = db.Column(db.Integer, default=0)
    total_runs = db.Column(db.Integer, default=0)
    total_upserts = db.Column(db.Integer, default=0)
    last_result_count = db.Column(db.Integer, default=0)
    consecutive_empty = db.Column(db.Integer, default=0)
    last_run_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SourceCursor(db.Model):
    """Progress cursor for finite or grid-based public data sources."""
    __tablename__ = 'source_cursors'

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(100), unique=True, index=True)
    cursor = db.Column(db.Integer, default=0)
    total_seen = db.Column(db.Integer, default=0)
    meta = db.Column(db.Text, nullable=True)
    last_run_at = db.Column(db.DateTime, nullable=True, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
        'phone_e164': 'VARCHAR(20)',
        'whatsapp_url': 'VARCHAR(255)',
        'phone_valid': 'BOOLEAN DEFAULT FALSE',
        'phone_type': 'VARCHAR(20)',
        'source_record_id': 'VARCHAR(255)',
        'source_confidence': 'INTEGER DEFAULT 50',
        'eiin': 'VARCHAR(50)',
        'upazila': 'VARCHAR(100)',
        'business_status': 'VARCHAR(50)',
        'active_status': "VARCHAR(30) DEFAULT 'unknown'",
        'rating': 'FLOAT',
        'user_ratings_total': 'INTEGER',
        'canonical_key': 'VARCHAR(255)',
        'last_checked_at': 'TIMESTAMP',
        'last_enriched_at': 'TIMESTAMP',
        'email_checked_at': 'TIMESTAMP',
        'qualification_status': "VARCHAR(30) DEFAULT 'needs_enrichment'",
        'contact_quality': "VARCHAR(30) DEFAULT 'no_contact'",
        'duplicate_key': 'VARCHAR(255)',
        'last_verified_at': 'TIMESTAMP',
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

    index_statements = [
        'CREATE INDEX IF NOT EXISTS ix_leads_place_id ON leads (place_id)',
        'CREATE INDEX IF NOT EXISTS ix_leads_phone_e164 ON leads (phone_e164)',
        'CREATE INDEX IF NOT EXISTS ix_leads_source_record_id ON leads (source_record_id)',
        'CREATE INDEX IF NOT EXISTS ix_leads_eiin ON leads (eiin)',
        'CREATE INDEX IF NOT EXISTS ix_leads_upazila ON leads (upazila)',
        'CREATE INDEX IF NOT EXISTS ix_leads_phone_valid ON leads (phone_valid)',
        'CREATE INDEX IF NOT EXISTS ix_leads_active_status ON leads (active_status)',
        'CREATE INDEX IF NOT EXISTS ix_leads_canonical_key ON leads (canonical_key)',
        'CREATE INDEX IF NOT EXISTS ix_leads_qualification_status ON leads (qualification_status)',
        'CREATE INDEX IF NOT EXISTS ix_leads_duplicate_key ON leads (duplicate_key)',
        'CREATE UNIQUE INDEX IF NOT EXISTS ux_search_tasks_district_keyword ON search_tasks (district, keyword)',
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_leads_place_id_not_null ON leads (place_id) WHERE place_id IS NOT NULL AND place_id <> ''",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_leads_phone_e164_not_null ON leads (phone_e164) WHERE phone_e164 IS NOT NULL AND phone_e164 <> ''",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_leads_email_not_null ON leads (email) WHERE email IS NOT NULL AND email <> ''",
    ]

    for statement in index_statements:
        try:
            db.session.execute(text(statement))
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Schema index skipped: {e}")

    _backfill_lead_source_metadata(dialect)
    db.session.commit()


def _backfill_lead_source_metadata(dialect):
    """Fill source metadata for leads collected before these columns existed."""
    statements = [
        """
        UPDATE leads
        SET source_record_id = place_id
        WHERE (source_record_id IS NULL OR source_record_id = '')
          AND place_id IS NOT NULL
          AND place_id <> ''
        """,
        """
        UPDATE leads
        SET source_confidence = CASE
            WHEN source = 'google_maps' THEN 90
            WHEN source = 'data_gov_bd' THEN 80
            WHEN source = 'openstreetmap' THEN 60
            ELSE COALESCE(source_confidence, 50)
        END
        WHERE source_confidence IS NULL OR source_confidence = 50
        """
    ]
    if dialect == 'postgresql':
        statements.append(
            """
            UPDATE leads
            SET eiin = split_part(place_id, ':', 3)
            WHERE source = 'data_gov_bd'
              AND (eiin IS NULL OR eiin = '')
              AND place_id LIKE 'data_gov_bd:contact:%'
              AND split_part(place_id, ':', 3) ~ '^[0-9]+$'
            """
        )

    for statement in statements:
        try:
            db.session.execute(text(statement))
        except Exception as e:
            db.session.rollback()
            print(f"Schema backfill skipped: {e}")


def add_lead(school_name, phone, email, district, type, source, **kwargs):
    """Create or update a lead using stable identifiers to avoid duplicates."""
    try:
        phone_info = normalize_bd_phone(phone)
        phone = phone_info['phone']
        email = _normalize_email(email)
        kwargs.setdefault('phone_e164', phone_info['phone_e164'])
        kwargs.setdefault('whatsapp_url', phone_info['whatsapp_url'])
        kwargs.setdefault('phone_valid', phone_info['phone_valid'])
        kwargs.setdefault('phone_type', phone_info['phone_type'])
        kwargs['canonical_key'] = kwargs.get('canonical_key') or _build_canonical_key(
            school_name,
            district,
            kwargs.get('address')
        )
        kwargs['duplicate_key'] = kwargs.get('duplicate_key') or _build_duplicate_key(
            kwargs.get('place_id'),
            phone_info['phone_e164'],
            email,
            kwargs['canonical_key']
        )
        kwargs.update(_qualification_fields(
            email,
            phone_info['phone_valid'],
            kwargs.get('active_status'),
            bool(kwargs.get('website'))
        ))

        lead = None
        if kwargs.get('place_id'):
            lead = Lead.query.filter_by(place_id=kwargs['place_id']).first()
        if not lead and kwargs.get('phone_e164'):
            lead = Lead.query.filter_by(phone_e164=kwargs['phone_e164']).first()
        if not lead and email:
            lead = Lead.query.filter_by(email=email).first()
        if not lead and kwargs.get('duplicate_key'):
            lead = Lead.query.filter_by(duplicate_key=kwargs['duplicate_key']).first()
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

        created = False
        if lead:
            changed = _merge_lead_fields(lead, fields)
        else:
            lead = Lead(**fields)
            db.session.add(lead)
            created = True
            changed = True

        db.session.commit()
        lead._upsert_action = 'created' if created else ('updated' if changed else 'unchanged')
        return lead
    except Exception as e:
        db.session.rollback()
        print(f"Error adding lead: {e}")
        return None


def _merge_lead_fields(lead, fields):
    always_refresh = {
        'business_status', 'active_status', 'rating', 'user_ratings_total',
        'last_checked_at', 'last_enriched_at', 'email_checked_at',
        'phone_e164', 'whatsapp_url', 'phone_valid', 'phone_type',
        'qualification_status', 'contact_quality', 'duplicate_key',
        'last_verified_at', 'source_confidence'
    }
    changed = False
    for key, value in fields.items():
        if not hasattr(lead, key) or value in (None, ''):
            continue
        current = getattr(lead, key)
        if (current in (None, '') or key in always_refresh) and current != value:
            setattr(lead, key, value)
            changed = True
    if changed:
        lead.updated_at = datetime.utcnow()
    return changed


def normalize_bd_phone(phone):
    """Normalize Bangladeshi numbers into local and WhatsApp-ready formats."""
    if not phone:
        return _phone_result()

    text = str(phone)
    candidates = _extract_phone_candidates(text)
    if not candidates:
        candidates = [text]

    parsed = [_parse_bd_phone_candidate(candidate) for candidate in candidates]
    parsed = [item for item in parsed if item['phone']]
    if not parsed:
        return _phone_result()

    parsed.sort(key=lambda item: (0 if item['phone_type'] == 'mobile' else 1, item['phone']))
    return parsed[0]


def _extract_phone_candidates(text):
    patterns = [
        r'\+?880[\s().-]*1[3-9](?:[\s().-]*\d){8}',
        r'01[3-9](?:[\s().-]*\d){8}',
        r'\b1[3-9](?:[\s().-]*\d){8}\b',
        r'\+?880[\s().-]*[2-9](?:[\s().-]*\d){6,10}',
        r'\b0[2-9](?:[\s().-]*\d){6,10}\b',
    ]
    candidates = []
    for pattern in patterns:
        candidates.extend(match.group(0) for match in re.finditer(pattern, text))
    return candidates


def _parse_bd_phone_candidate(candidate):
    digits = re.sub(r'\D+', '', str(candidate))
    if not digits:
        return _phone_result()

    if digits.startswith('00880'):
        digits = digits[2:]

    local = None
    phone_type = 'unknown'
    if digits.startswith('8801') and len(digits) >= 13:
        local = '0' + digits[3:13]
        phone_type = 'mobile'
    elif digits.startswith('01') and len(digits) >= 11:
        local = digits[:11]
        phone_type = 'mobile'
    elif digits.startswith('1') and len(digits) >= 10:
        local = '0' + digits[:10]
        phone_type = 'mobile'
    elif digits.startswith('880') and len(digits) >= 10:
        local = '0' + digits[3:14]
        phone_type = 'landline'
    elif digits.startswith('0') and len(digits) >= 8:
        local = digits[:12]
        phone_type = 'landline'

    if not local:
        return _phone_result()

    is_mobile = bool(re.fullmatch(r'01[3-9]\d{8}', local))
    phone_valid = is_mobile
    phone_e164 = '+880' + local[1:] if is_mobile else None
    whatsapp_url = f"https://wa.me/{phone_e164[1:]}" if phone_e164 else None
    return _phone_result(local, phone_e164, whatsapp_url, phone_valid, 'mobile' if is_mobile else phone_type)


def _phone_result(phone=None, phone_e164=None, whatsapp_url=None, phone_valid=False, phone_type=None):
    return {
        'phone': phone,
        'phone_e164': phone_e164,
        'whatsapp_url': whatsapp_url,
        'phone_valid': phone_valid,
        'phone_type': phone_type
    }


def _normalize_email(email):
    if not email:
        return None
    email = str(email).strip().lower()
    return email[:255] if '@' in email else None


def _build_duplicate_key(place_id, phone_e164, email, canonical_key):
    for prefix, value in (
        ('place', place_id),
        ('phone', phone_e164),
        ('email', email),
        ('identity', canonical_key),
    ):
        if value:
            return f'{prefix}:{value}'[:255]
    return None


def _qualification_fields(email, phone_valid, active_status=None, has_website=False):
    has_email = bool(_normalize_email(email))
    has_valid_phone = bool(phone_valid)
    if active_status == 'closed':
        qualification_status = 'closed'
    elif has_valid_phone or has_email:
        qualification_status = 'qualified'
    elif has_website:
        qualification_status = 'needs_enrichment'
    else:
        qualification_status = 'unusable'

    if has_valid_phone and has_email:
        contact_quality = 'phone_email'
    elif has_valid_phone:
        contact_quality = 'phone'
    elif has_email:
        contact_quality = 'email'
    else:
        contact_quality = 'no_contact'

    return {
        'qualification_status': qualification_status,
        'contact_quality': contact_quality,
        'last_verified_at': datetime.utcnow() if qualification_status == 'qualified' else None
    }


def refresh_lead_contact_fields(lead):
    """Recalculate normalized contact and qualification fields for an existing lead."""
    phone_info = normalize_bd_phone(lead.phone)
    lead.phone = phone_info['phone']
    lead.phone_e164 = phone_info['phone_e164']
    lead.whatsapp_url = phone_info['whatsapp_url']
    lead.phone_valid = phone_info['phone_valid']
    lead.phone_type = phone_info['phone_type']
    lead.email = _normalize_email(lead.email)
    fields = _qualification_fields(
        lead.email,
        lead.phone_valid,
        lead.active_status,
        bool(lead.website)
    )
    lead.qualification_status = fields['qualification_status']
    lead.contact_quality = fields['contact_quality']
    if fields['last_verified_at']:
        lead.last_verified_at = fields['last_verified_at']
    lead.duplicate_key = _build_duplicate_key(
        lead.place_id,
        lead.phone_e164,
        lead.email,
        lead.canonical_key
    )
    return lead


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


def record_api_usage(provider, endpoint=None, status_code=None, success=True):
    try:
        log = ApiUsageLog(
            provider=provider,
            endpoint=endpoint,
            status_code=status_code,
            success=success
        )
        db.session.add(log)
        db.session.commit()
        return log
    except Exception as e:
        db.session.rollback()
        print(f"Error logging API usage: {e}")
        return None


def get_api_usage_count(provider, hours=24):
    since = datetime.utcnow() - timedelta(hours=hours)
    return ApiUsageLog.query.filter(
        ApiUsageLog.provider == provider,
        ApiUsageLog.created_at >= since
    ).count()


def can_use_api(provider, daily_limit):
    if not daily_limit:
        return True
    return get_api_usage_count(provider, hours=24) < daily_limit


def get_source_cursor(source):
    cursor = SourceCursor.query.filter_by(source=source).first()
    if cursor:
        return cursor

    cursor = SourceCursor(source=source, cursor=0, total_seen=0)
    db.session.add(cursor)
    db.session.commit()
    return cursor


def update_source_cursor(source, cursor_position, total_seen=None, meta=None):
    cursor = get_source_cursor(source)
    cursor.cursor = int(cursor_position or 0)
    if total_seen is not None:
        cursor.total_seen = int(total_seen or 0)
    if meta is not None:
        cursor.meta = json.dumps(meta, default=str)
    cursor.last_run_at = datetime.utcnow()
    cursor.updated_at = datetime.utcnow()
    db.session.commit()
    return cursor


def duplicate_group_counts():
    """Return duplicate identity groups that should remain at zero in production."""
    return {
        'phone_e164': db.session.query(Lead.phone_e164, db.func.count(Lead.id))
        .filter(Lead.phone_e164 != None, Lead.phone_e164 != '')
        .group_by(Lead.phone_e164)
        .having(db.func.count(Lead.id) > 1)
        .count(),
        'email': db.session.query(db.func.lower(Lead.email), db.func.count(Lead.id))
        .filter(Lead.email != None, Lead.email != '')
        .group_by(db.func.lower(Lead.email))
        .having(db.func.count(Lead.id) > 1)
        .count(),
        'place_id': db.session.query(Lead.place_id, db.func.count(Lead.id))
        .filter(Lead.place_id != None, Lead.place_id != '')
        .group_by(Lead.place_id)
        .having(db.func.count(Lead.id) > 1)
        .count(),
        'duplicate_key': db.session.query(Lead.duplicate_key, db.func.count(Lead.id))
        .filter(Lead.duplicate_key != None, Lead.duplicate_key != '')
        .group_by(Lead.duplicate_key)
        .having(db.func.count(Lead.id) > 1)
        .count()
    }


def ensure_search_tasks(districts, keywords):
    created = 0
    for district in districts:
        for keyword in keywords:
            exists = SearchTask.query.filter_by(district=district, keyword=keyword).first()
            if exists:
                continue
            db.session.add(SearchTask(district=district, keyword=keyword))
            created += 1
    if created:
        db.session.commit()
    return created


def get_next_search_tasks(limit, reset_days=14):
    stale_before = datetime.utcnow() - timedelta(days=reset_days)
    tasks = SearchTask.query.filter(
        SearchTask.status == 'active'
    ).order_by(
        SearchTask.last_run_at.asc().nullsfirst(),
        SearchTask.priority.desc(),
        SearchTask.consecutive_empty.asc()
    ).limit(limit).all()

    if len(tasks) < limit:
        stale_tasks = SearchTask.query.filter(
            SearchTask.status == 'exhausted',
            SearchTask.last_run_at < stale_before
        ).limit(limit - len(tasks)).all()
        for task in stale_tasks:
            task.status = 'active'
            task.consecutive_empty = 0
        if stale_tasks:
            db.session.commit()
        tasks.extend(stale_tasks)

    return tasks


def mark_search_task_result(task, upserts):
    task.total_runs = (task.total_runs or 0) + 1
    task.total_upserts = (task.total_upserts or 0) + upserts
    task.last_result_count = upserts
    task.last_run_at = datetime.utcnow()
    task.updated_at = datetime.utcnow()
    if upserts:
        task.consecutive_empty = 0
        task.status = 'active'
    else:
        task.consecutive_empty = (task.consecutive_empty or 0) + 1
        if task.consecutive_empty >= 3:
            task.status = 'exhausted'
    db.session.commit()
    return task


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
        if not lead:
            print(f"update_lead_status: lead {lead_id} not found")
            return None
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


def log_followup_event(lead_id, followup_type='email', status='completed', notes=None, scheduled_for=None):
    """ফলো-আপ ইভেন্ট রেকর্ড করা"""
    try:
        followup = FollowUp(
            lead_id=lead_id,
            followup_type=followup_type,
            status=status,
            notes=notes,
            scheduled_for=scheduled_for,
            completed_at=datetime.utcnow() if status == 'completed' else None
        )
        db.session.add(followup)
        db.session.commit()
        return followup
    except Exception as e:
        db.session.rollback()
        print(f"Error logging followup: {e}")
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
    qualified_leads = Lead.query.filter_by(qualification_status='qualified').count()
    whatsapp_ready = Lead.query.filter_by(phone_valid=True).count()
    leads_with_email = Lead.query.filter(Lead.email != None, Lead.email != '').count()
    leads_with_website = Lead.query.filter(Lead.website != None, Lead.website != '').count()
    
    return {
        'total_leads': total_leads,
        'active_leads': active_leads,
        'closed_leads': closed_leads,
        'qualified_leads': qualified_leads,
        'whatsapp_ready_leads': whatsapp_ready,
        'leads_with_email': leads_with_email,
        'leads_with_website': leads_with_website,
        'qualified_rate': f"{(qualified_leads/total_leads*100) if total_leads > 0 else 0:.1f}%",
        'email_rate': f"{(leads_with_email/total_leads*100) if total_leads > 0 else 0:.1f}%",
        'website_rate': f"{(leads_with_website/total_leads*100) if total_leads > 0 else 0:.1f}%",
        'hot_leads': hot_leads,
        'warm_leads': warm_leads,
        'cold_leads': cold_leads,
        'emails_sent': emails_sent,
        'emails_opened': emails_opened,
        'email_open_rate': f"{(emails_opened/emails_sent*100) if emails_sent > 0 else 0:.1f}%",
        'converted': converted,
        'conversion_rate': f"{(converted/total_leads*100) if total_leads > 0 else 0:.1f}%"
    }
