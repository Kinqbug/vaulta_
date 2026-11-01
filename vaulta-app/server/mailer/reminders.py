"""Scheduled reminders. Run by the worker (every few minutes) or by an administrator ("Run now").

For each enabled reminder rule:
  * candidates = customers whose CONDITION still holds and whose reference time is older than the rule's delay
  * a reminder stops permanently when the condition is resolved (email verified, KYC submitted/approved, consent withdrawn ...): recorded in email_reminder_state
  * maximum attempts and the interval between reminders are enforced per customer; the queue key (rule:user:attempt) makes duplicates impossible
  * quiet hours (system settings, in the CUSTOMER's time zone) postpone sending; they never cancel a reminder
  * marketing reminders need opt-in consent, and the outbox re-checks consent/suppression at the moment of sending
"""
import json, zoneinfo
from .. import util
from . import events, prefs

STAFF_DEMO = "u.is_demo=0 AND u.id NOT IN (SELECT user_id FROM user_roles)"
MKT = "EXISTS(SELECT 1 FROM communication_preferences cp WHERE cp.user_id=u.id AND cp.marketing_opt_in=1)"
# event -> (condition on users u, reference-time expression)
RULES = {
 'reminder.verify_email': ("u.email_verified=0 AND u.status='active'", 'u.created_at'),
 'reminder.verify_email_final': ("u.email_verified=0 AND u.status='active'", 'u.created_at'),
 'reminder.kyc_incomplete': ("u.email_verified=1 AND u.kyc_status IN ('not_started','pending') AND u.status='active'", 'u.created_at'),
 'reminder.kyc_info_outstanding': ("u.kyc_status='requires_info' AND u.status='active'", '(SELECT reviewed_at FROM kyc_profiles k WHERE k.user_id=u.id)'),
 'reminder.reengagement': (f"u.email_verified=1 AND u.status='active' AND {MKT}", 'COALESCE(u.last_login_at,u.created_at)'),
 'reminder.retirement_review': ("u.status='active' AND EXISTS(SELECT 1 FROM retirement_plans r WHERE r.user_id=u.id)", '(SELECT updated_at FROM retirement_plans r WHERE r.user_id=u.id)'),
}

def in_quiet_hours(db, user_id, cfg_tz, now_dt, start, end):
    try: tz = zoneinfo.ZoneInfo(prefs.get_timezone(db, user_id, cfg_tz) or 'UTC')
    except Exception: tz = zoneinfo.ZoneInfo('UTC')
    h = now_dt.astimezone(tz).hour
    return (h >= start or h < end) if start > end else (start <= h < end)

def run(db, mailer, now=None, only=None):
    """-> {rule_code: {'sent': n, 'postponed': n, 'stopped': n}}"""
    from ..db import tx
    from .campaigns import setting
    from .. import routes_auth
    from .. import security as sec
    now_s = now or util.now(); now_dt = util.parse_ts(now_s); out = {}
    qs, qe = setting(db, 'email_quiet_start_hour', 21), setting(db, 'email_quiet_end_hour', 8)
    for rule in db.execute("SELECT * FROM email_automation_rules WHERE kind='reminder' AND enabled=1").fetchall():
        if only and rule['event_type'] != only: continue
        spec = RULES.get(rule['event_type'])
        if not spec: continue
        cond, ref_time = spec; p = json.loads(rule['params'] or '{}'); mx = int(p.get('max_attempts', 1)); gap_h = int(p.get('interval_hours', 0))
        st = out[rule['code']] = {'sent': 0, 'postponed': 0, 'stopped': 0}
        with tx(db):
            # resolved -> stop for good
            st['stopped'] = db.execute(f"UPDATE email_reminder_state SET stopped_at=?, stop_reason='condition_resolved' WHERE rule_code=? AND stopped_at IS NULL AND user_id NOT IN (SELECT u.id FROM users u WHERE {cond})", (now_s, rule['code'])).rowcount
        cutoff = util.ts_after(minutes=-rule['delay_minutes']) if not now else (now_dt - __import__('datetime').timedelta(minutes=rule['delay_minutes'])).strftime('%Y-%m-%dT%H:%M:%SZ')
        cands = db.execute(f"SELECT u.id FROM users u WHERE {STAFF_DEMO} AND ({cond}) AND {ref_time} IS NOT NULL AND {ref_time}<=? ORDER BY u.id LIMIT 500", (cutoff,)).fetchall()
        for c in cands:
            uid = c['id']
            s = db.execute('SELECT * FROM email_reminder_state WHERE user_id=? AND rule_code=?', (uid, rule['code'])).fetchone()
            if s and (s['stopped_at'] or s['sent_count'] >= mx): continue
            if s and s['last_sent_at'] and gap_h and util.parse_ts(s['last_sent_at']) + __import__('datetime').timedelta(hours=gap_h) > now_dt: continue
            if p.get('respect_quiet_hours', True) and in_quiet_hours(db, uid, mailer._c('EMAIL_DEFAULT_TIMEZONE') or 'UTC', now_dt, qs, qe): st['postponed'] += 1; continue
            n_next = (s['sent_count'] if s else 0) + 1
            kw = {}
            with tx(db):
                if rule['event_type'] in ('reminder.verify_email', 'reminder.verify_email_final'):
                    kw = {'token': routes_auth.make_token(db, uid, 'verify_email', 48), 'expires_in': '48 hours'}      # a fresh single-use link each time
                if rule['event_type'] == 'reminder.kyc_info_outstanding':
                    kw = {'reason': (db.execute('SELECT info_request FROM kyc_profiles WHERE user_id=?', (uid,)).fetchone() or {'info_request': ''})['info_request'] or ''}
                    if not kw['reason']: continue
                events.emit(db, rule['event_type'], uid, str(uid), qualifier=str(n_next), **kw)
                db.execute('INSERT INTO email_reminder_state(user_id,rule_code,sent_count,last_sent_at) VALUES(?,?,1,?) ON CONFLICT(user_id,rule_code) DO UPDATE SET sent_count=sent_count+1, last_sent_at=excluded.last_sent_at', (uid, rule['code'], now_s))
            st['sent'] += 1
    return out
