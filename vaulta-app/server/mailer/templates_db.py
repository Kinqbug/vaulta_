"""Template storage: seeding from the catalog, immutable versions, drafts, publishing, duplication, automation rules."""
import json
from .. import util
from . import catalog as cat

REMINDERS = {   # event -> (delay_minutes, enabled, params)
    'reminder.verify_email': (1440, 1, {'max_attempts': 1, 'interval_hours': 0, 'respect_quiet_hours': True}),
    'reminder.verify_email_final': (4320, 1, {'max_attempts': 1, 'interval_hours': 0, 'respect_quiet_hours': True}),
    'reminder.kyc_incomplete': (2880, 1, {'max_attempts': 3, 'interval_hours': 168, 'respect_quiet_hours': True}),
    'reminder.kyc_info_outstanding': (4320, 1, {'max_attempts': 2, 'interval_hours': 168, 'respect_quiet_hours': True}),
    'reminder.reengagement': (43200, 0, {'max_attempts': 2, 'interval_hours': 720, 'respect_quiet_hours': True}),       # delay = days of inactivity (30d); off until an admin enables it
    'reminder.retirement_review': (525600, 0, {'max_attempts': 1, 'interval_hours': 0, 'respect_quiet_hours': True}),   # 365d since the plan was last saved
}

def ensure_seed(db):
    """Idempotent, run at startup. System metadata (class, trigger, flags) is owned by code. Content: a new system version is published only
    when the catalog text changed AND no administrator has edited the template since, so admin work is never overwritten."""
    n = util.now()
    with db_tx(db):
        for t in cat.CATALOG:
            h = cat.content_hash(t['subject'], t['spec']); spec = json.dumps(t['spec'], sort_keys=True)
            row = db.execute('SELECT * FROM email_templates WHERE id=?', (t['id'],)).fetchone()
            if not row:
                db.execute('INSERT INTO email_templates(id,category,name,description,mail_class,trigger_event,manual_send,pref_gated,is_system,status,current_version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,1,?,1,?,?)',
                           (t['id'], t['category'], t['name'], t['description'], t['mail_class'], t['trigger_event'], int(t['manual_send']), int(t['pref_gated']), 'active', n, n))
                db.execute("INSERT INTO email_template_versions(template_id,version,state,subject,spec,content_hash,note,created_at,published_at) VALUES(?,?,'published',?,?,?,?,?,?)", (t['id'], 1, t['subject'], spec, h, 'system default', n, n))
            else:
                db.execute('UPDATE email_templates SET category=?, name=?, description=?, mail_class=?, trigger_event=?, manual_send=?, pref_gated=? WHERE id=? AND is_system=1',
                           (t['category'], t['name'], t['description'], t['mail_class'], t['trigger_event'], int(t['manual_send']), int(t['pref_gated']), t['id']))
                cur = db.execute('SELECT * FROM email_template_versions WHERE template_id=? AND state=\'published\' ORDER BY version DESC LIMIT 1', (t['id'],)).fetchone()
                if cur and cur['created_by'] is None and cur['content_hash'] != h and row['current_version'] == cur['version']:
                    v = db.execute('SELECT MAX(version) m FROM email_template_versions WHERE template_id=?', (t['id'],)).fetchone()['m'] + 1
                    db.execute("UPDATE email_template_versions SET state='superseded' WHERE id=?", (cur['id'],))
                    db.execute("INSERT INTO email_template_versions(template_id,version,state,subject,spec,content_hash,note,created_at,published_at) VALUES(?,?,'published',?,?,?,?,?,?)", (t['id'], v, t['subject'], spec, h, 'system update', n, n))
                    db.execute('UPDATE email_templates SET current_version=?, updated_at=? WHERE id=?', (v, n, t['id']))
            if t['trigger_event']:
                kind = 'reminder' if t['trigger_event'].startswith('reminder.') else 'event'
                delay, enabled, params = REMINDERS.get(t['trigger_event'], (0, 1, {}))
                db.execute('INSERT OR IGNORE INTO email_automation_rules(code,kind,event_type,template_id,name,description,enabled,delay_minutes,params,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                           ('evt:' + t['trigger_event'], kind, t['trigger_event'], t['id'], t['name'], cat.EVENTS[t['trigger_event']], enabled, delay, json.dumps(params), n))

def db_tx(db):
    from ..db import tx
    return tx(db)

def get(db, template_id, version=None):
    """-> (template_row, version_row, spec dict, optional list). version=None means the current published version."""
    t = db.execute('SELECT * FROM email_templates WHERE id=?', (template_id,)).fetchone()
    if not t: return None
    v = db.execute('SELECT * FROM email_template_versions WHERE template_id=? AND version=?', (template_id, version or t['current_version'])).fetchone()
    if not v: return None
    return t, v, json.loads(v['spec']), (cat.BY_ID.get(template_id) or {}).get('optional', [])

def optional_for(db, template_id):
    c = cat.BY_ID.get(template_id)
    if c: return c['optional']
    r = db.execute('SELECT spec FROM email_template_versions WHERE template_id=? ORDER BY version DESC LIMIT 1', (template_id,)).fetchone()
    return json.loads(r['spec']).get('_optional', []) if r else []

def save_draft(db, template_id, subject, spec, admin_id, note=''):
    t = db.execute('SELECT * FROM email_templates WHERE id=?', (template_id,)).fetchone()
    if not t: raise ValueError('template not found')
    spec = {k: v for k, v in spec.items() if k in ('preheader', 'headline', 'intro', 'badge', 'details', 'summary_title', 'summary', 'cta', 'after', 'notice', '_optional')}
    spec['intro'] = [str(x) for x in spec.get('intro', [])]
    problems = cat.spec_problems(subject, spec)
    allowed = set(_allowed_vars(db, t))
    extra = [v for v in cat.referenced_vars(subject, spec) if v not in allowed]
    if extra: problems.append('Variables not available for this template: ' + ', '.join(extra))
    if problems: raise ValueError('; '.join(problems))
    n = util.now(); spec_s = json.dumps(spec, sort_keys=True); h = cat.content_hash(subject, spec)
    d = db.execute("SELECT id FROM email_template_versions WHERE template_id=? AND state='draft'", (template_id,)).fetchone()
    if d: db.execute('UPDATE email_template_versions SET subject=?, spec=?, content_hash=?, note=?, created_by=?, created_at=? WHERE id=?', (subject, spec_s, h, note, admin_id, n, d['id']))
    else:
        v = db.execute('SELECT MAX(version) m FROM email_template_versions WHERE template_id=?', (template_id,)).fetchone()['m'] + 1
        db.execute("INSERT INTO email_template_versions(template_id,version,state,subject,spec,content_hash,note,created_by,created_at) VALUES(?,?,'draft',?,?,?,?,?,?)", (template_id, v, subject, spec_s, h, note, admin_id, n))
    return db.execute("SELECT version FROM email_template_versions WHERE template_id=? AND state='draft'", (template_id,)).fetchone()['version']

def _allowed_vars(db, t):
    """A template may only use variables its own backend event (or its administrator inputs) can supply: the ones its default version already references."""
    c = cat.BY_ID.get(t['id'])
    base = cat.referenced_vars(c['subject'], c['spec']) if c else []
    common = ['first_name', 'email', 'customer_id', 'dashboard_url', 'support_url', 'security_url', 'kyc_url', 'transaction_url', 'portfolio_url', 'trade_url', 'goal_url', 'event_timestamp']
    return list(dict.fromkeys(base + common + (c['admin_vars'] if c else ['message_body'])))

def publish(db, template_id, admin_id, note=''):
    t = db.execute('SELECT * FROM email_templates WHERE id=?', (template_id,)).fetchone()
    d = db.execute("SELECT * FROM email_template_versions WHERE template_id=? AND state='draft'", (template_id,)).fetchone()
    if not d: raise ValueError('there is no draft to publish')
    if t['mail_class'] in ('security', 'transactional') and d['created_by'] == admin_id:
        raise PermissionError('separation of duties: a security/transactional template must be published by someone other than its author')
    n = util.now()
    db.execute("UPDATE email_template_versions SET state='superseded' WHERE template_id=? AND state='published'", (template_id,))
    db.execute("UPDATE email_template_versions SET state='published', published_by=?, published_at=?, note=COALESCE(NULLIF(?,''),note) WHERE id=?", (admin_id, n, note, d['id']))
    db.execute('UPDATE email_templates SET current_version=?, updated_at=?, updated_by=? WHERE id=?', (d['version'], n, admin_id, template_id))
    return d['version']

def discard_draft(db, template_id): db.execute("DELETE FROM email_template_versions WHERE template_id=? AND state='draft'", (template_id,))

def duplicate(db, template_id, new_id, name, admin_id):
    got = get(db, template_id)
    if not got: raise ValueError('template not found')
    t, v, spec, _ = got
    if not new_id.replace('_', '').isalnum() or not new_id.isupper() or len(new_id) > 48 or db.execute('SELECT 1 FROM email_templates WHERE id=?', (new_id,)).fetchone():
        raise ValueError('template ID must be unique, upper-case letters/digits/underscores')
    n = util.now(); spec = {**spec, '_optional': optional_for(db, template_id)}
    db.execute('INSERT INTO email_templates(id,category,name,description,mail_class,trigger_event,manual_send,pref_gated,is_system,status,current_version,created_at,updated_at,updated_by) VALUES(?,?,?,?,?,NULL,?,?,0,?,1,?,?,?)',
               (new_id, t['category'], name, f'Copy of {template_id}', t['mail_class'], 1 if t['mail_class'] in ('admin', 'marketing', 'service') else 0, t['pref_gated'], 'inactive', n, n, admin_id))
    db.execute("INSERT INTO email_template_versions(template_id,version,state,subject,spec,content_hash,note,created_by,created_at,published_by,published_at) VALUES(?,?,'published',?,?,?,?,?,?,?,?)",
               (new_id, 1, v['subject'], json.dumps(spec, sort_keys=True), cat.content_hash(v['subject'], spec), f'duplicated from {template_id}', admin_id, n, admin_id, n))
