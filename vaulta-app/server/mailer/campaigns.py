"""Individual, selected-customer and broadcast sends. Every send is a campaign record (who created it, who approved it, exactly which template version and audience),
expanded into ONE outbox message per recipient: no recipient ever sees another recipient's address, and nothing uses CC/BCC."""
import json, re
from .. import util
from ..db import tx
from . import catalog as cat, render as rnd, segments, outbox, prefs, templates_db as tdb, events

AUTO_VARS = {'first_name', 'email', 'customer_id', 'event_timestamp'} | (cat.LINK_VARS - {'action_url'})

PERSONAL = ('first_name', 'customer_id', 'dashboard_url', 'support_url', 'security_url', 'kyc_url', 'portfolio_url', 'trade_url', 'goal_url', 'transaction_url')   # the only variables an administrator may write inside a message

def check_personalisation(text_):
    bad = [v for v in cat.PLACEHOLDER.findall(text_ or '') if v not in PERSONAL]
    if bad: raise CampaignError('unknown_variable', 'Unsupported variable in the message: ' + ', '.join('{{%s}}' % v for v in dict.fromkeys(bad)) + '. Allowed: ' + ', '.join('{{%s}}' % v for v in PERSONAL))
    if re.search(r'\{\{|\}\}', cat.PLACEHOLDER.sub('', text_ or '')): raise CampaignError('malformed_variable', 'A {{variable}} in the message is malformed.')

def expand(text_, ctx, base):
    vals = {**rnd.build_links(base), **{k: v for k, v in ctx.items() if k in PERSONAL}}
    return cat.PLACEHOLDER.sub(lambda m: vals.get(m.group(1), ''), text_ or '')

class CampaignError(Exception):
    def __init__(self, code, message, status=422): super().__init__(message); self.code, self.message, self.status = code, message, status

def setting(db, key, default):
    r = db.execute('SELECT value FROM system_settings WHERE key=?', (key,)).fetchone()
    try: return int(r[0]) if r else default
    except ValueError: return default

def check_template(db, template_id, mode):
    got = tdb.get(db, template_id)
    if not got: raise CampaignError('template_not_found', 'Template not found', 404)
    t, v, spec, optional = got
    if t['status'] != 'active': raise CampaignError('template_inactive', 'This template is disabled.')
    if not t['manual_send']: raise CampaignError('template_not_sendable', 'This template describes a specific transaction or event and is sent only by the system when that event happens. It cannot be sent by hand.')
    if mode == 'broadcast' and t['mail_class'] not in ('service', 'marketing'): raise CampaignError('template_not_for_broadcast', 'Administrative and security templates are one-to-one only; a broadcast needs a service or marketing template.')
    return t, v, spec, optional

def clean_inputs(db, t, optional_vars, variables, overrides, base_url, extra_hosts):
    allowed = set(cat.BY_ID[t['id']]['admin_vars']) if t['id'] in cat.BY_ID else {'message_body'}
    vars_ = {}
    for k, v in (variables or {}).items():
        if k not in allowed: raise CampaignError('variable_not_allowed', f'"{k}" cannot be set for this template.')
        vars_[k] = rnd.clean(v, multiline=cat.VARS[k][1] == 'multiline'); check_personalisation(vars_[k])
    ov = {}
    for k in ('subject', 'headline', 'cta_label', 'cta_url'):
        if overrides and overrides.get(k): ov[k] = rnd.clean(overrides[k], limit=200 if k != 'cta_url' else 2000)
    if ov.get('cta_url') and not rnd.link_ok(ov['cta_url'], base_url, extra_hosts): raise CampaignError('link_not_allowed', 'The button link must be https and on VAULTA or an approved host.')
    return vars_, ov

def dry_run(db, mailer, t, v, spec, optional, vars_, ov, auto_extra=None):
    """Render once with labelled sample recipient values so missing variables are reported before anything is queued."""
    ctx = {**{k: rnd.SAMPLE[k] for k in ('first_name', 'email', 'customer_id', 'event_timestamp')}, **{k: expand(x, rnd.SAMPLE, mailer.base_url) for k, x in vars_.items()}, **(auto_extra or {})}
    for k in cat.required_vars(v['subject'], spec, optional):             # transaction-specific values are derived per recipient later; sample them here only for the dry run
        if k not in ctx and k not in AUTO_VARS and k not in cat.LINK_VARS: ctx[k] = rnd.SAMPLE.get(k, '') if k in ('transaction_status',) else ''
    try: return rnd.render(v['subject'], spec, ctx, optional=optional, mail_class=t['mail_class'], base_url=mailer.base_url, legal=mailer.legal(db), unsubscribe_url='https://example.invalid/u', overrides=ov, secret={'action_url': 'https://example.invalid/'}, extra_hosts=(mailer._c('EMAIL_LINK_ALLOWED_HOSTS') or '').split(','))
    except rnd.RenderError as e: raise CampaignError(e.code, e.message)

def _doc_url(template_id, base):
    return base + ('/legal/privacy/' if 'PRIVACY' in template_id else '/legal/terms/')

def create(db, mailer, admin_id, *, name, mode, template_id, audience, overrides=None, variables=None, scheduled_at=None):
    if mode not in ('individual', 'selected', 'broadcast'): raise CampaignError('invalid_mode', 'Unknown send mode.')
    t, v, spec, optional = check_template(db, template_id, mode)
    cls = t['mail_class']
    vars_, ov = clean_inputs(db, t, optional, variables, overrides, mailer.base_url, (mailer._c('EMAIL_LINK_ALLOWED_HOSTS') or '').split(','))
    auto = {'document_url': _doc_url(template_id, mailer.base_url)} if 'document_url' in cat.referenced_vars(v['subject'], spec) else {}
    dry_run(db, mailer, t, v, spec, optional, vars_, ov, auto)
    aud = {'segment': audience.get('segment'), 'user_ids': list(audience.get('user_ids') or [])}
    if mode == 'broadcast':
        if aud['segment'] not in segments.SEGMENTS: raise CampaignError('invalid_audience', 'Choose an audience.')
        est = segments.count(db, aud['segment'], cls)
    else:
        n = len(aud['user_ids'])
        if mode == 'individual' and n != 1: raise CampaignError('invalid_recipients', 'Choose exactly one customer.')
        if mode == 'selected' and not (1 <= n <= 200): raise CampaignError('invalid_recipients', 'Select between 1 and 200 customers.')
        ok, _ = segments.resolve_ids(db, aud['user_ids'], cls); est = len(ok)
    if scheduled_at:
        try: s = util.parse_ts(scheduled_at)
        except Exception: raise CampaignError('invalid_schedule', 'Use a UTC timestamp such as 2026-11-02T09:00:00Z.')
        if s <= util.utcnow() or (s - util.utcnow()).days > 90: raise CampaignError('invalid_schedule', 'Schedule between now and 90 days ahead.')
        scheduled_at = s.strftime('%Y-%m-%dT%H:%M:%SZ')
    n = util.now(); ref = util.new_ref('CMP')
    cur = db.execute('INSERT INTO email_campaigns(ref,name,mode,template_id,mail_class,audience,overrides,variables,status,estimated_count,scheduled_at,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (ref, (name or t['name'])[:120], mode, template_id, cls, json.dumps(aud), json.dumps(ov), json.dumps(vars_), 'draft', est, scheduled_at, admin_id, n, n))
    return cur.lastrowid, ref, est

def get(db, cid):
    c = db.execute('SELECT * FROM email_campaigns WHERE id=?', (cid,)).fetchone()
    if not c: raise CampaignError('not_found', 'Campaign not found', 404)
    return c

def submit(db, mailer, cid, admin_id):
    """The explicit 'confirm send' step. Large campaigns stop at pending_approval until a different administrator approves."""
    c = get(db, cid)
    if c['created_by'] != admin_id: raise CampaignError('forbidden', 'Only the creator can submit a campaign.', 403)
    if c['status'] != 'draft': raise CampaignError('invalid_state', f"This campaign is {c['status']}.", 409)
    aud = json.loads(c['audience']); cls = c['mail_class']
    est = segments.count(db, aud['segment'], cls) if c['mode'] == 'broadcast' else len(segments.resolve_ids(db, aud['user_ids'], cls)[0])
    if est == 0: raise CampaignError('no_recipients', 'No eligible recipients.')
    if est > setting(db, 'email_campaign_max_recipients', 5000): raise CampaignError('too_many_recipients', f"This audience has {est} eligible recipients; the configured limit is {setting(db, 'email_campaign_max_recipients', 5000)}.")
    n = util.now()
    needs_approval = est > setting(db, 'email_bulk_approval_threshold', 50)
    db.execute('UPDATE email_campaigns SET estimated_count=?, submitted_at=?, status=?, updated_at=? WHERE id=?', (est, n, 'pending_approval' if needs_approval else 'approved', n, cid))
    if not needs_approval: launch(db, mailer, cid)
    return 'pending_approval' if needs_approval else get(db, cid)['status']

def approve(db, mailer, cid, admin_id):
    c = get(db, cid)
    if c['status'] != 'pending_approval': raise CampaignError('invalid_state', f"This campaign is {c['status']}.", 409)
    if c['created_by'] == admin_id: raise CampaignError('separation_of_duties', 'A campaign must be approved by someone other than its creator.', 403)
    db.execute("UPDATE email_campaigns SET status='approved', approved_by=?, approved_at=?, updated_at=? WHERE id=?", (admin_id, util.now(), util.now(), cid))
    launch(db, mailer, cid)

def _resolve_reference(db, uid, ref):
    """transaction_reference supplied by an administrator must belong to THIS recipient; the status comes from our records, not from the administrator."""
    try:
        for fn in (events.find_withdrawal, events.find_deposit):
            try:
                d = fn(db, ref)
                if d['user_id'] == uid: return d['status']
            except events.Skip: pass
    except Exception: pass
    r = db.execute('SELECT status FROM investments WHERE ref=? AND user_id=?', (ref, uid)).fetchone() or db.execute('SELECT status FROM orders WHERE ref=? AND user_id=?', (ref, uid)).fetchone() \
        or db.execute('SELECT status FROM transactions WHERE ref=? AND user_id=?', (ref, uid)).fetchone()
    return events.label(r['status']) if r else None

def launch(db, mailer, cid):
    """Expand to one queued message per eligible recipient, pinned to the template version published right now."""
    c = get(db, cid); t = db.execute('SELECT * FROM email_templates WHERE id=?', (c['template_id'],)).fetchone()
    v = tdb.get(db, c['template_id'])[1]
    aud, vars_, ov = json.loads(c['audience']), json.loads(c['variables']), json.loads(c['overrides'])
    cap = setting(db, 'email_campaign_max_recipients', 5000)
    rows = list(segments.select(db, aud['segment'], c['mail_class'], cap)) if c['mode'] == 'broadcast' else segments.resolve_ids(db, aud['user_ids'], c['mail_class'])[0]
    skipped = [] if c['mode'] == 'broadcast' else segments.resolve_ids(db, aud['user_ids'], c['mail_class'])[1]
    run_at = c['scheduled_at'] or util.now(); n = util.now(); queued = 0
    spec = json.loads(v['spec']); needs_ref = 'transaction_reference' in cat.referenced_vars(v['subject'], spec)
    event = 'admin.individual' if c['mode'] == 'individual' else ('admin.selected' if c['mode'] == 'selected' else 'campaign.broadcast')
    for r in rows:
        person = {'first_name': r['first_name'] or 'there', 'customer_id': r['public_id']}
        ctx = {**person, 'email': r['email'], 'event_timestamp': events.fmt_ts(n), **{k: expand(x, person, mailer.base_url) for k, x in vars_.items()}}
        if 'document_url' in cat.referenced_vars(v['subject'], spec): ctx['document_url'] = _doc_url(c['template_id'], mailer.base_url)
        if needs_ref:
            st = _resolve_reference(db, r['id'], vars_.get('transaction_reference', ''))
            if not st: skipped.append((r['public_id'], 'reference does not belong to this customer')); continue
            ctx['transaction_status'] = st
        eid, created = outbox.enqueue(db, user_id=r['id'], to_email=r['email'], template_id=c['template_id'], version=v['version'], subject=v['subject'][:200], event_type=event, entity_ref=c['ref'],
                                      idem=f"campaign:{cid}:{r['id']}", mail_class=c['mail_class'], ctx=ctx, overrides=ov, run_at=run_at, campaign_id=cid, created_by=c['created_by'],
                                      max_attempts=setting(db, 'email_max_attempts', 6))
        db.execute('INSERT OR IGNORE INTO email_campaign_recipients(campaign_id,user_id,email_id,status) VALUES(?,?,?,?)', (cid, r['id'], eid, 'queued')); queued += 1
    for pid, why in skipped:
        u = db.execute('SELECT id FROM users WHERE public_id=?', (pid,)).fetchone()
        if u: db.execute('INSERT OR IGNORE INTO email_campaign_recipients(campaign_id,user_id,status,skip_reason) VALUES(?,?,?,?)', (cid, u['id'], 'skipped', why))
    future = c['scheduled_at'] and c['scheduled_at'] > n
    db.execute('UPDATE email_campaigns SET status=?, final_count=?, launched_at=?, updated_at=? WHERE id=?', ('scheduled' if future else 'sending', queued, n, n, cid))
    if queued == 0: db.execute("UPDATE email_campaigns SET status='failed' WHERE id=?", (cid,))
    return queued

def cancel(db, cid, admin_id):
    c = get(db, cid)
    if c['status'] not in ('draft', 'pending_approval', 'approved', 'scheduled', 'sending'): raise CampaignError('invalid_state', f"This campaign is {c['status']}.", 409)
    n = 0
    for r in db.execute("SELECT id FROM email_outbox WHERE campaign_id=? AND status IN ('queued','retry')", (cid,)).fetchall(): n += int(outbox.cancel(db, r['id']))
    db.execute("UPDATE email_campaigns SET status='cancelled', cancelled_by=?, updated_at=? WHERE id=?", (admin_id, util.now(), cid))
    return n

def refresh_status(db):
    """sending/scheduled -> completed once nothing is left to attempt."""
    for c in db.execute("SELECT id FROM email_campaigns WHERE status IN ('sending','scheduled')").fetchall():
        left = db.execute("SELECT COUNT(*) FROM email_outbox WHERE campaign_id=? AND status IN ('queued','retry','sending')", (c['id'],)).fetchone()[0]
        if left == 0: db.execute("UPDATE email_campaigns SET status='completed', completed_at=?, updated_at=? WHERE id=?", (util.now(), util.now(), c['id']))
        elif db.execute("SELECT 1 FROM email_campaigns WHERE id=? AND status='scheduled' AND scheduled_at<=?", (c['id'], util.now())).fetchone(): db.execute("UPDATE email_campaigns SET status='sending' WHERE id=?", (c['id'],))

def stats(db, cid):
    return {r['status']: r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM email_outbox WHERE campaign_id=? GROUP BY status', (cid,))}
