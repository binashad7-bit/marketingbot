import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
from loguru import logger
from src.database import Lead, get_stats
from config import Config
import json
import os

logger.add("logs/reporting.log", rotation="500 MB")


class ReportGenerator:
    """রিপোর্ট জেনারেটর - Google Sheets এ ড্যাশবোর্ড আপডেট করা"""
    
    def __init__(self):
        self.sheet_id = Config.GOOGLE_SHEET_ID
        self.spreadsheet = None
        self._authenticate()
    
    
    def _authenticate(self):
        """Google Sheets এ সংযোগ করা"""
        try:
            # নোট: আপনাকে Google Sheets API ক্রেডেনশিয়াল সেটআপ করতে হবে
            # এর জন্য Google Cloud Console থেকে JSON ফাইল ডাউনলোড করুন
            
            # বর্তমানে এটি একটি ডামি সংযোগ
            # আপনার ক্রেডেনশিয়াল সেট করার পর সক্রিয় করুন
            
            logger.info("Google Sheets ক্রেডেনশিয়াল সেটআপ প্রয়োজন")
            return False
        
        except Exception as e:
            logger.warning(f"Google Sheets authentication error: {e}")
            return False
    
    
    def generate_daily_report(self):
        """দৈনিক রিপোর্ট তৈরি করা"""
        logger.info("=" * 50)
        logger.info("দৈনিক রিপোর্ট জেনারেশন শুরু")
        logger.info("=" * 50)
        
        try:
            # পরিসংখ্যান পাওয়া
            stats = get_stats()
            
            # রিপোর্ট ফরম্যাট করা
            report = self._format_report(stats)
            
            # ড্যাশবোর্ড আপডেট করা
            self._update_dashboard(report)
            
            # কনসোলে প্রিন্ট করা
            self._print_report(report)
            
            logger.info("✓ দৈনিক রিপোর্ট সফল")
            return report
        
        except Exception as e:
            logger.error(f"Report generation error: {e}")
            return None
    
    
    def _format_report(self, stats):
        """রিপোর্ট ফরম্যাট করা"""
        report = {
            'date': datetime.now().strftime("%Y-%m-%d"),
            'time': datetime.now().strftime("%H:%M:%S"),
            'total_leads': stats.get('total_leads', 0),
            'hot_leads': stats.get('hot_leads', 0),
            'warm_leads': stats.get('warm_leads', 0),
            'cold_leads': stats.get('cold_leads', 0),
            'emails_sent': stats.get('emails_sent', 0),
            'emails_opened': stats.get('emails_opened', 0),
            'email_open_rate': stats.get('email_open_rate', '0%'),
            'converted': stats.get('converted', 0),
            'conversion_rate': stats.get('conversion_rate', '0%')
        }
        return report
    
    
    def _update_dashboard(self, report):
        """Google Sheets ড্যাশবোর্ড আপডেট করা"""
        try:
            # যদি Google Sheets সংযোগ সফল হয় তাহলে এটি চালানো হবে
            # বর্তমানে এটি লোকাল JSON ফাইলে সেভ করছি
            
            self._save_to_json(report)
            logger.info("✓ ড্যাশবোর্ড আপডেট করা হয়েছে")
            return True
        
        except Exception as e:
            logger.error(f"Dashboard update error: {e}")
            return False
    
    
    def _save_to_json(self, report):
        """রিপোর্ট JSON ফাইলে সেভ করা"""
        try:
            # লোকাল রিপোর্ট ফাইল
            report_file = 'reports/daily_report.json'
            
            # ফোল্ডার তৈরি করা যদি না থাকে
            os.makedirs(os.path.dirname(report_file), exist_ok=True)
            
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            
            logger.info(f"✓ রিপোর্ট সেভ করা হয়েছে: {report_file}")
            return True
        
        except Exception as e:
            logger.error(f"JSON save error: {e}")
            return False
    
    
    def _print_report(self, report):
        """রিপোর্ট প্রিন্ট করা"""
        print("\n")
        print("=" * 60)
        print(f"  দৈনিক রিপোর্ট - {report['date']} {report['time']}")
        print("=" * 60)
        print(f"  মোট লিড: {report['total_leads']}")
        print(f"    └─ Hot: {report['hot_leads']}")
        print(f"    └─ Warm: {report['warm_leads']}")
        print(f"    └─ Cold: {report['cold_leads']}")
        print(f"\n  ইমেইল ক্যাম্পেইন:")
        print(f"    └─ পাঠানো: {report['emails_sent']}")
        print(f"    └─ খোলা: {report['emails_opened']}")
        print(f"    └─ ওপেন রেট: {report['email_open_rate']}")
        print(f"\n  রূপান্তর:")
        print(f"    └─ পেইড কাস্টমার: {report['converted']}")
        print(f"    └─ রূপান্তর হার: {report['conversion_rate']}")
        print("=" * 60)
        print("\n")
    
    
    def generate_weekly_report(self):
        """সাপ্তাহিক রিপোর্ট তৈরি করা"""
        logger.info("সাপ্তাহিক রিপোর্ট জেনারেশন শুরু")
        
        try:
            from datetime import datetime, timedelta
            
            # গত ৭ দিনের ডেটা
            week_start = datetime.now() - timedelta(days=7)
            
            # গত সপ্তাহের লিড পাওয়া
            weekly_leads = Lead.query.filter(
                Lead.created_at >= week_start
            ).all()
            
            weekly_report = {
                'week_of': week_start.strftime("%Y-%m-%d"),
                'leads_collected': len(weekly_leads),
                'avg_daily_leads': len(weekly_leads) / 7,
                'conversions': len([l for l in weekly_leads if l.paid_customer]),
                'best_source': self._get_best_source(weekly_leads)
            }
            
            logger.info(f"✓ সাপ্তাহিক রিপোর্ট: {weekly_report}")
            return weekly_report
        
        except Exception as e:
            logger.error(f"Weekly report error: {e}")
            return None
    
    
    def _get_best_source(self, leads):
        """সেরা লিড সোর্স পাওয়া"""
        sources = {}
        for lead in leads:
            sources[lead.source] = sources.get(lead.source, 0) + 1
        
        if sources:
            return max(sources, key=sources.get)
        return 'unknown'
    
    
    def generate_monthly_report(self):
        """মাসিক রিপোর্ট তৈরি করা"""
        logger.info("মাসিক রিপোর্ট জেনারেশন শুরু")
        
        try:
            from datetime import datetime, timedelta
            
            # গত ৩০ দিনের ডেটা
            month_start = datetime.now() - timedelta(days=30)
            
            monthly_leads = Lead.query.filter(
                Lead.created_at >= month_start
            ).all()
            
            converted_leads = [l for l in monthly_leads if l.paid_customer]
            
            monthly_report = {
                'month': datetime.now().strftime("%B %Y"),
                'total_leads': len(monthly_leads),
                'conversions': len(converted_leads),
                'conversion_rate': (len(converted_leads) / len(monthly_leads) * 100) if monthly_leads else 0,
                'total_revenue': sum([l.subscription_amount or 0 for l in converted_leads]),
                'average_customer_value': (sum([l.subscription_amount or 0 for l in converted_leads]) / len(converted_leads)) if converted_leads else 0
            }
            
            logger.info(f"✓ মাসিক রিপোর্ট: {monthly_report}")
            return monthly_report
        
        except Exception as e:
            logger.error(f"Monthly report error: {e}")
            return None


class ReportingManager:
    """রিপোর্টিং ম্যানেজার"""
    
    def __init__(self):
        self.generator = ReportGenerator()
    
    
    def run_daily(self):
        """দৈনিক রিপোর্টিং চালানো"""
        logger.info("দৈনিক রিপোর্টিং শুরু")
        
        daily = self.generator.generate_daily_report()
        weekly = self.generator.generate_weekly_report()
        monthly = self.generator.generate_monthly_report()
        
        return {
            'daily': daily,
            'weekly': weekly,
            'monthly': monthly
        }


# সিঙ্গেল ইনস্ট্যান্স
reporting_manager = ReportingManager()


if __name__ == '__main__':
    reporting_manager.run_daily()
