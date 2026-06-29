import requests
import html as html_lib
import re
from datetime import datetime, timedelta
from loguru import logger
from config import Config
from src.database import (
    EmailLog, Lead, get_leads_by_segment, update_lead_status,
    log_email_event, log_followup_event, db
)
from src.personalization import outreach_personalizer
import os

logger.add("logs/email_campaign.log", rotation="500 MB")

_URL_RE = re.compile(r'(https?://[^\s<]+)')


def _text_to_html(text):
    """Convert a plain-text email body into safe HTML with clickable links."""
    escaped = html_lib.escape((text or '').strip())

    def _linkify(match):
        url = match.group(1)
        return f'<a href="{url}" style="color:#176a35;">{url}</a>'

    linked = _URL_RE.sub(_linkify, escaped)
    return linked.replace('\n', '<br>\n')


class EmailCampaign:
    """ইমেইল ক্যাম্পেইন ম্যানেজার"""
    
    def __init__(self):
        self.email_provider = Config.EMAIL_PROVIDER
        self.brevo_api_key = Config.BREVO_API_KEY
        self.sendgrid_api_key = Config.SENDGRID_API_KEY
        self.sg = None
        if self.email_provider == 'sendgrid':
            import sendgrid
            self.sg = sendgrid.SendGridAPIClient(self.sendgrid_api_key)
        self.from_email = Config.FROM_EMAIL
        self.from_name = Config.FROM_NAME
        self.templates_path = 'templates/email/'
        
        # টেমপ্লেট লোড করা
        self.templates = self._load_templates()

        # ফলো-আপ ড্রিপ সিকোয়েন্স: {current_send_count: (days_gap, template, subject)}
        self.followup_sequence = {
            1: (3, 'followup_second', 'A quick follow-up for {school}'),
            2: (5, 'followup_value', 'One practical growth idea for {school}'),
            3: (7, 'final_followup', 'Should I close the loop, {school}?'),
        }
    
    
    def _load_templates(self):
        """ইমেইল টেমপ্লেট লোড করা"""
        first_touch = '''Hi [SCHOOL_NAME] team,

I am reaching out from CreatifyBD. We help businesses improve their websites, search visibility, social media, creative content, and paid acquisition.

Your business looked relevant to the work we do. If growth or a stronger digital presence is a priority, I would be happy to prepare a short, no-obligation review with a few practical ideas specific to [SCHOOL_NAME].

Would that be useful?

Best,
CreatifyBD
https://creatifybd.com'''
        return {
            'hot_first': first_touch,
            'warm_first': first_touch,
            'cold_first': first_touch,
            'followup_second': '''Hi [SCHOOL_NAME] team,

Just following up on my earlier note. I can send a concise review focused on the most useful digital growth opportunity I can identify for your business.

Would you like me to prepare it?

Best,
CreatifyBD''',
            'followup_value': '''Hi [SCHOOL_NAME] team,

One practical place we often find growth opportunities is the path from discovery to enquiry: search visibility, landing-page clarity, trust signals, and follow-up.

I can review that path for [SCHOOL_NAME] and send the clearest improvement opportunities. Interested?

Best,
CreatifyBD''',
            'final_followup': '''Hi [SCHOOL_NAME] team,

I do not want to crowd your inbox, so this will be my last note. If a short digital growth review would be useful later, you can reach us at marketing@creatifybd.com.

Best,
CreatifyBD'''
        }

        # Legacy templates are retained below only for migration history.
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
    
    
    def _build_html_email(self, body_text, lead_id=None):
        """Wrap a plain-text body in a responsive HTML shell + open-tracking pixel."""
        inner = _text_to_html(body_text)
        pixel = ''
        base_url = (Config.PUBLIC_BASE_URL or '').rstrip('/')
        if base_url and lead_id:
            pixel = (
                f'<img src="{base_url}/track/open/{lead_id}" '
                'width="1" height="1" alt="" style="display:none">'
            )
        return (
            '<!doctype html><html lang="bn"><head>'
            '<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            '</head>'
            '<body style="margin:0;padding:0;background:#f4f7f6;">'
            '<div style="max-width:600px;margin:0 auto;padding:24px;'
            "font-family:Arial,'Hind Siliguri',sans-serif;font-size:15px;"
            'line-height:1.7;color:#17211d;background:#ffffff;">'
            f'{inner}'
            '</div>'
            f'{pixel}'
            '</body></html>'
        )

    def send_email(self, to_email, subject, body_text, lead_id=None, template_name=None):
        """একটি ইমেইল পাঠানো (plain text → HTML + plaintext উভয়ই)"""
        try:
            html_content = self._build_html_email(body_text, lead_id)
            text_content = (body_text or '').strip()
            if self.email_provider == 'brevo':
                response_status = self._send_with_brevo(to_email, subject, html_content, text_content, lead_id, template_name)
            elif self.email_provider == 'sendgrid':
                response_status = self._send_with_sendgrid(to_email, subject, html_content, text_content)
            else:
                raise ValueError(f"Unsupported EMAIL_PROVIDER: {self.email_provider}")

            logger.info(f"✓ ইমেইল পাঠানো: {to_email} (Status: {response_status})")
            
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
            if lead_id:
                log_email_event(
                    lead_id=lead_id,
                    subject=subject,
                    status='failed',
                    template=template_name,
                    error_message=str(e)
                )
            return False

    def _send_with_brevo(self, to_email, subject, html_content, text_content=None, lead_id=None, template_name=None):
        """Brevo transactional email API দিয়ে পাঠানো"""
        if not self.brevo_api_key:
            raise ValueError("BREVO_API_KEY is not configured")

        payload = {
            'sender': {
                'email': self.from_email,
                'name': self.from_name
            },
            'to': [{'email': to_email}],
            'subject': subject,
            'htmlContent': html_content
        }
        if text_content:
            payload['textContent'] = text_content
        tags = [tag for tag in [template_name, f'lead-{lead_id}' if lead_id else None] if tag]
        if tags:
            payload['tags'] = tags

        response = requests.post(
            'https://api.brevo.com/v3/smtp/email',
            headers={
                'accept': 'application/json',
                'api-key': self.brevo_api_key,
                'content-type': 'application/json'
            },
            json=payload,
            timeout=20
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Brevo API error {response.status_code}: {response.text}")
        return response.status_code

    def _send_with_sendgrid(self, to_email, subject, html_content, text_content=None):
        """পুরোনো SendGrid path রাখতে optional fallback"""
        if not self.sendgrid_api_key:
            raise ValueError("SENDGRID_API_KEY is not configured")

        from sendgrid.helpers.mail import Mail, Email

        message = Mail(
            from_email=Email(self.from_email, self.from_name),
            to_emails=to_email,
            subject=subject,
            plain_text_content=text_content or None,
            html_content=html_content
        )
        response = self.sg.send(message)
        return response.status_code
    
    
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
            day_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            sent_today = EmailLog.query.filter(
                EmailLog.status == 'sent',
                EmailLog.sent_at >= day_start
            ).count()
            daily_remaining = max(0, Config.EMAIL_DAILY_LIMIT - sent_today)
            if daily_remaining == 0:
                logger.info(f"Daily email limit reached: {Config.EMAIL_DAILY_LIMIT}")
                return 0

            # লিড স্কোর অনুযায়ী সংগ্রহ করা
            hot_leads = get_leads_by_segment('Hot')
            warm_leads = get_leads_by_segment('Warm')
            cold_leads = get_leads_by_segment('Cold')
            
            total_sent = 0
            
            # Hot লিডদের প্রথম ইমেইল পাঠানো
            logger.info(f"Hot লিড পাঠাচ্ছি: {len(hot_leads)}")
            for lead in hot_leads:
                if total_sent >= daily_remaining:
                    break
                if lead.email and not lead.email_sent:
                    ai_copy = outreach_personalizer.create(lead)
                    html_content = ai_copy['email_body'] if ai_copy else self._personalize_template(
                        self.templates['hot_first'], lead
                    )
                    subject = ai_copy['subject'] if ai_copy else f"A growth idea for {lead.school_name}"
                    
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
                            last_email_sent=datetime.utcnow(),
                            email_send_count=1
                        )
                        total_sent += 1
            
            # Warm লিডদের মধ্যম ইমেইল
            logger.info(f"Warm লিড পাঠাচ্ছি: {len(warm_leads)}")
            for lead in warm_leads:
                if total_sent >= daily_remaining:
                    break
                if lead.email and not lead.email_sent:
                    ai_copy = outreach_personalizer.create(lead)
                    html_content = ai_copy['email_body'] if ai_copy else self._personalize_template(
                        self.templates['warm_first'], lead
                    )
                    subject = ai_copy['subject'] if ai_copy else f"A digital growth review for {lead.school_name}"
                    
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
                            last_email_sent=datetime.utcnow(),
                            email_send_count=1
                        )
                        total_sent += 1
            
            # Cold লিডদের সফট ইমেইল
            logger.info(f"Cold লিড পাঠাচ্ছি: {len(cold_leads)}")
            for lead in cold_leads:
                if total_sent >= daily_remaining:
                    break
                if lead.email and not lead.email_sent:
                    ai_copy = outreach_personalizer.create(lead)
                    html_content = ai_copy['email_body'] if ai_copy else self._personalize_template(
                        self.templates['cold_first'], lead
                    )
                    subject = ai_copy['subject'] if ai_copy else f"A quick idea for {lead.school_name}"
                    
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
                            last_email_sent=datetime.utcnow(),
                            email_send_count=1
                        )
                        total_sent += 1
            
            logger.info(f"✓ মোট ইমেইল পাঠানো: {total_sent}")
            return total_sent
        
        except Exception as e:
            logger.error(f"Campaign error: {e}")
            return 0

    def run_followups(self):
        """Send the next drip email to leads already in the sequence."""
        logger.info("=" * 50)
        logger.info("ফলো-আপ ইমেইল সিকোয়েন্স শুরু")
        logger.info("=" * 50)

        try:
            now = datetime.utcnow()
            leads = Lead.query.filter(
                Lead.email_sent == True,
                Lead.email.isnot(None),
                Lead.email != '',
                Lead.paid_customer == False,
                Lead.status != 'converted',
                Lead.email_send_count >= 1,
                Lead.email_send_count <= 4
            ).all()

            total_sent = 0
            for lead in leads:
                step = self.followup_sequence.get(lead.email_send_count or 0)
                if not step:
                    continue
                days_gap, template_key, subject_tpl = step
                last_sent = lead.last_email_sent or lead.email_sent_date
                if not last_sent or (now - last_sent) < timedelta(days=days_gap):
                    continue

                body_text = self._personalize_template(self.templates[template_key], lead)
                subject = subject_tpl.format(school=lead.school_name or 'আপনার প্রতিষ্ঠান')

                if self.send_email(lead.email, subject, body_text, lead.id, template_key):
                    new_count = (lead.email_send_count or 0) + 1
                    update_lead_status(
                        lead.id,
                        email_send_count=new_count,
                        email_sent_date=now,
                        last_email_sent=now
                    )
                    log_followup_event(lead.id, 'email', status='completed', notes=template_key)
                    total_sent += 1

            logger.info(f"✓ মোট ফলো-আপ ইমেইল পাঠানো: {total_sent}")
            return total_sent

        except Exception as e:
            logger.error(f"Follow-up campaign error: {e}")
            return 0


# সিঙ্গেল ইনস্ট্যান্স
email_campaign = EmailCampaign()


if __name__ == '__main__':
    campaign = EmailCampaign()
    campaign.run_campaign()
