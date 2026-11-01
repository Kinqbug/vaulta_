"""HTTP surface of the Email Center.

  /api/admin/email/*                 administrators; every endpoint is guarded SERVER-side by a named permission (the UI hiding a button is never the control)
  /api/email/unsubscribe*            public, token-authenticated (HMAC), no login: one-click unsubscribe must work from the inbox
  /api/me/communication-preferences  the signed-in customer's own consent
  /api/webhooks/email/resend         provider delivery events; refused unless the Svix signature verifies
"""
import json
from flask import Blueprint, request, jsonify, g, current_app
from .. import util, security as sec
from ..util import ApiError, text, page_args
from ..db import get_db, tx
from ..audit import audit
from ..routes_auth import body
from . import catalog as cat, render as rnd, templates_db as tdb, outbox, prefs, segments, campaigns as camp, reminders, webhooks, events

admin = Blueprint('email_admin', __name__, url_prefix='/api/admin/email')
public = Blueprint('email_public', __name__, url_prefix='/api')
P = sec.require_perm
def A(): return g.user['id']
def M(): return current_app.mailer

def mask(addr):
    a, _, d = (addr or '').partition('@'); return (a[:1] + '***@' + d) if d else '***'

def _wrap(fn, *a, **k):
    try: return fn(*a, **k)
    except camp.CampaignError as e: raise ApiError(e.status, e.code, e.message)
    except PermissionError as e: raise ApiError(403, 'separation_of_duties', str(e))
    except ValueError as e: raise ApiError(422, 'invalid_request', str(e))

def _template_dict(db, t, with_spec=False):
    v = db.execute('SELECT * FROM email_template_versions WHERE template_id=? AND version=?', (t['id'], t['current_version'])).fetchone()
    by = db.execute('SELECT email FROM users WHERE id=?', (t['updated_by'],)).fetchone() if t['updated_by'] else None
    d = {'id': t['id'], 'name': t['name'], 'category': t['category'], 'category_label': cat.CATEGORIES.get(t['category'], t['category']), 'description': t['description'], 'mail_class': t['mail_class'],
         'subject': v['subject'], 'trigger_event': t['trigger_event'], 'trigger': cat.EVENTS.get(t['trigger_event']) if t['trigger_event'] else 'No automated trigger (manual send or not applicable)',
         'status': t['status'], 'version': t['current_version'], 'updated_at': t['updated_at'], 'updated_by': by['email'] if by else 'system', 'manual_send': bool(t['manual_send']), 'is_system': bool(t['is_system']),
         'has_draft': bool(db.execute("SELECT 1 FROM email_template_versions WHERE template_id=? AND state='draft'", (t['id'],)).fetchone())}
    if with_spec: d['spec'] = json.loads(v['spec'])
    return d

# ================================================================ overview / status
@admin.get('/status')
@P('email.logs.read')
def status(): return {'provider': M().status(), 'categories': cat.CATEGORIES, 'classes': ['transactional', 'security', 'service', 'marketing', 'admin']}

@admin.get('/overview')
@P('email.logs.read')
def overview():
    db = get_db(); days = max(1, min(90, int(request.args.get('days', 7)))); since = util.ts_after(days=-days)
    c = {r['status']: r['n'] for r in db.execute("SELECT status,COUNT(*) n FROM email_outbox WHERE created_at>=? AND status<>'legacy_unsent' GROUP BY status", (since,))}
    g_ = lambda *k: sum(c.get(x, 0) for x in k)
    trend = {}
    for r in db.execute("SELECT substr(created_at,1,10) d,status,COUNT(*) n FROM email_outbox WHERE created_at>=? AND status<>'legacy_unsent' GROUP BY d,status", (since,)):
        t = trend.setdefault(r['d'], {'queued': 0, 'accepted': 0, 'delivered': 0, 'bounced': 0, 'failed': 0, 'other': 0})
        k = {'queued': 'queued', 'retry': 'queued', 'sending': 'queued', 'accepted': 'accepted', 'delayed': 'accepted', 'delivered': 'delivered', 'bounced': 'bounced', 'complained': 'bounced', 'failed': 'failed'}.get(r['status'], 'other'); t[k] += r['n']
    st = M().status()
    return {'period_days': days, 'provider': st,
            'counts': {'queued': g_('queued', 'retry'), 'processing': g_('sending'), 'accepted_by_provider': g_('accepted', 'delayed', 'delivered', 'bounced', 'complained'), 'confirmed_delivered': g_('delivered'),
                       'bounced': g_('bounced'), 'complained': g_('complained'), 'failed': g_('failed'), 'suppressed': g_('suppressed'), 'blocked': g_('blocked'), 'dev_captured': g_('dev_captured'), 'cancelled': g_('cancelled')},
            'note': 'Accepted by provider means the provider API took the message. Only messages with a verified provider delivery event are counted as confirmed delivered.' if st['live_delivery'] else st['warning'],
            'legacy_unsent': db.execute("SELECT COUNT(*) FROM email_outbox WHERE status='legacy_unsent'").fetchone()[0],
            'active_automations': db.execute('SELECT COUNT(*) FROM email_automation_rules WHERE enabled=1').fetchone()[0], 'total_automations': db.execute('SELECT COUNT(*) FROM email_automation_rules').fetchone()[0],
            'templates': {'total': db.execute('SELECT COUNT(*) FROM email_templates').fetchone()[0], 'active': db.execute("SELECT COUNT(*) FROM email_templates WHERE status='active'").fetchone()[0]},
            'campaigns': {r['status']: r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM email_campaigns GROUP BY status')},
            'trend': [{'date': d, **v} for d, v in sorted(trend.items())]}

# ================================================================ templates
@admin.get('/templates')
@P('email.templates.read')
def templates():
    db = get_db(); w, a = [], []
    if request.args.get('category'): w.append('category=?'); a.append(request.args['category'])
    if request.args.get('status') in ('active', 'inactive'): w.append('status=?'); a.append(request.args['status'])
    if request.args.get('mail_class'): w.append('mail_class=?'); a.append(request.args['mail_class'])
    if request.args.get('q'): w.append('(id LIKE ? OR name LIKE ? OR description LIKE ?)'); a += ['%' + request.args['q'].replace('%', '') + '%'] * 3
    rows = db.execute('SELECT * FROM email_templates' + (' WHERE ' + ' AND '.join(w) if w else '') + ' ORDER BY category,id', a).fetchall()
    return {'items': [_template_dict(db, t) for t in rows]}

@admin.get('/templates/<tid>')
@P('email.templates.read')
def template(tid):
    db = get_db(); t = db.execute('SELECT * FROM email_templates WHERE id=?', (tid,)).fetchone()
    if not t: raise ApiError(404, 'not_found', 'Template not found')
    d = _template_dict(db, t, True)
    dr = db.execute("SELECT * FROM email_template_versions WHERE template_id=? AND state='draft'", (tid,)).fetchone()
    d['draft'] = {'version': dr['version'], 'subject': dr['subject'], 'spec': json.loads(dr['spec']), 'created_by': dr['created_by'], 'note': dr['note']} if dr else None
    d['versions'] = [dict(r) for r in db.execute('SELECT v.version,v.state,v.note,v.created_at,v.published_at,u.email author FROM email_template_versions v LEFT JOIN users u ON u.id=v.created_by WHERE template_id=? ORDER BY version DESC', (tid,))]
    c = cat.BY_ID.get(tid)
    allowed = tdb._allowed_vars(db, t)
    d['variables'] = [{'name': k, 'description': cat.VARS[k][0], 'kind': cat.VARS[k][1]} for k in allowed if k in cat.VARS]
    d['optional_variables'] = tdb.optional_for(db, tid); d['admin_variables'] = c['admin_vars'] if c else ['message_body']
    d['stats'] = {r['status']: r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM email_outbox WHERE template_id=? GROUP BY status', (tid,))}
    return d

@admin.post('/templates/<tid>/draft')
@P('email.templates.edit')
def template_draft(tid):
    b, db = body(), get_db()
    with tx(db):
        v = _wrap(tdb.save_draft, db, tid, str(b.get('subject', '')), b.get('spec') or {}, A(), str(b.get('note', ''))[:300])
        audit(db, A(), 'email.template_draft_saved', 'email_template', tid, None, {'version': v})
    return {'draft_version': v}

@admin.delete('/templates/<tid>/draft')
@P('email.templates.edit')
def template_discard(tid):
    db = get_db()
    with tx(db): tdb.discard_draft(db, tid); audit(db, A(), 'email.template_draft_discarded', 'email_template', tid)
    return {'ok': True}

@admin.post('/templates/<tid>/publish')
@P('email.templates.publish')
def template_publish(tid):
    b, db = body(), get_db()
    with tx(db):
        v = _wrap(tdb.publish, db, tid, A(), str(b.get('note', ''))[:300])
        audit(db, A(), 'email.template_published', 'email_template', tid, None, {'version': v}, str(b.get('note', ''))[:300] or None)
    return {'version': v}

@admin.post('/templates/<tid>/status')
@P('email.templates.publish')
def template_status(tid):
    b, db = body(), get_db(); st = b.get('status')
    if st not in ('active', 'inactive'): raise ApiError(422, 'invalid_status', 'status must be active or inactive')
    reason = text(b.get('reason'), 'reason', 300)
    with tx(db):
        t = db.execute('SELECT * FROM email_templates WHERE id=?', (tid,)).fetchone()
        if not t: raise ApiError(404, 'not_found', 'Template not found')
        db.execute('UPDATE email_templates SET status=?, updated_at=?, updated_by=? WHERE id=?', (st, util.now(), A(), tid))
        audit(db, A(), 'email.template_status_changed', 'email_template', tid, {'status': t['status']}, {'status': st}, reason)
    return {'status': st}

@admin.post('/templates/<tid>/duplicate')
@P('email.templates.edit')
def template_duplicate(tid):
    b, db = body(), get_db()
    with tx(db):
        _wrap(tdb.duplicate, db, tid, str(b.get('new_id', '')).strip().upper(), text(b.get('name'), 'name', 120), A())
        audit(db, A(), 'email.template_duplicated', 'email_template', tid, None, {'new_id': b.get('new_id')})
    return {'id': str(b.get('new_id')).strip().upper()}

def _preview_ctx(db, b, tpl_id):
    """Sample values (clearly labelled) by default; a real customer's personal fields when one is chosen. Never secrets."""
    ctx = {k: v for k, v in rnd.SAMPLE.items() if k != 'action_url'}
    if b.get('user_id'):
        u = db.execute('SELECT u.id,u.public_id,u.email,p.first_name FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.public_id=?', (b['user_id'],)).fetchone()
        if not u: raise ApiError(404, 'not_found', 'Customer not found')
        ctx.update(first_name=u['first_name'] or 'there', email=u['email'], customer_id=u['public_id'])
    for k, v in (b.get('variables') or {}).items():
        if k in cat.VARS and k not in cat.SECRET_VARS: ctx[k] = v
    return ctx

@admin.post('/templates/<tid>/preview')
@P('email.templates.read')
def template_preview(tid):
    b, db = body(), get_db(); m = M()
    got = tdb.get(db, tid, b.get('version'))
    if not got: raise ApiError(404, 'not_found', 'Template not found')
    t, v, spec, optional = got
    subject, spec = v['subject'], spec
    if b.get('spec') is not None:                       # live preview of unsaved editor content
        subject, spec = str(b.get('subject', subject)), b['spec']
        problems = cat.spec_problems(subject, spec)
        if problems: raise ApiError(422, 'invalid_content', '; '.join(problems))
    ctx = _preview_ctx(db, b, tid)
    try:
        r = rnd.render(subject, spec, ctx, secret={'action_url': m.base_url + '/preview-link-not-real/'}, optional=optional, mail_class=t['mail_class'], base_url=m.base_url, legal=m.legal(db),
                       unsubscribe_url=m.base_url + '/unsubscribe/?t=preview', banner='PREVIEW. Sample data; nothing has been sent.', inline_logo_root=m._c('WEB_ROOT'), overrides=b.get('overrides') or {},
                       extra_hosts=(m._c('EMAIL_LINK_ALLOWED_HOSTS') or '').split(','))
    except rnd.RenderError as e: raise ApiError(422, e.code, e.message)
    return {'subject': r.subject, 'html': r.html, 'text': r.text, 'preheader': r.preheader, 'sample_data': not b.get('user_id')}

@admin.post('/templates/<tid>/test-send')
@P('email.send')
@sec.rate_limit('email_test', 20, 3600)
def template_test(tid):
    """Sends sample content to the ADMINISTRATOR'S OWN address only (never an arbitrary one: this is not a relay)."""
    db, m = get_db(), M()
    got = tdb.get(db, tid)
    if not got: raise ApiError(404, 'not_found', 'Template not found')
    t, v, spec, optional = got; me = db.execute('SELECT email FROM users WHERE id=?', (A(),)).fetchone()['email']
    ctx = {k: x for k, x in rnd.SAMPLE.items() if k != 'action_url'}; ctx.update(email=me)
    with tx(db):
        eid, _ = outbox.enqueue(db, user_id=A(), to_email=me, template_id=tid, version=v['version'], subject=v['subject'][:200], event_type='admin.test_send', entity_ref=tid, idem=f'test:{tid}:{A()}:{util.now()}:{util.new_ref("T")}',
                                mail_class='admin', ctx=ctx, secret={'action_url': m.base_url + '/'}, non_live=0, created_by=A(), max_attempts=1)
        audit(db, A(), 'email.test_sent', 'email_template', tid, None, {'email_id': eid})
    m.deliver_one(db, eid)
    r = db.execute('SELECT status,last_error FROM email_outbox WHERE id=?', (eid,)).fetchone()
    return {'email_id': eid, 'status': r['status'], 'error': r['last_error'], 'sent_to': mask(me), 'note': m.status()['warning'] or 'Provider accepted the test message. Check your inbox; delivery is confirmed separately.'}

# ================================================================ SMTP host configuration (secret-handling: its own permission)
from . import smtp as smtpmod
from .. import secretbox

def _smtp_state(db):
    return {'settings': smtpmod.public_view(db), 'provider': M().status(), 'provider_env': current_app.config.get('EMAIL_PROVIDER'), 'environment': current_app.config.get('ENV'),
            'smtp_active': M().transport.name == 'smtp',
            'notice': None if M().transport.name == 'smtp' else f"The server is running EMAIL_PROVIDER={current_app.config.get('EMAIL_PROVIDER')}. These SMTP settings are stored but only used when EMAIL_PROVIDER=smtp is set in the server environment and the app is restarted."}

@admin.get('/smtp')
@P('email.smtp.manage')
def smtp_get(): return _smtp_state(get_db())

@admin.put('/smtp')
@P('email.smtp.manage')
@sec.rate_limit('smtp_save', 30, 3600)
def smtp_put():
    db, b = get_db(), body()
    if 'password' in b and b['password'] is not None and not isinstance(b['password'], str): raise ApiError(422, 'invalid_request', 'Password must be text.')
    try:
        with tx(db):
            before, after = smtpmod.save(db, b, A())
            audit(db, A(), 'email.smtp_updated', 'email_smtp_settings', 1, before, after, (text(b.get('reason'), 'reason', 300, False) or None))
    except smtpmod.SmtpConfigError as e: raise ApiError(422, 'invalid_smtp_settings', str(e))
    return _smtp_state(db)

@admin.post('/smtp/test-connection')
@P('email.smtp.manage')
@sec.rate_limit('smtp_test_conn', 20, 3600)
def smtp_test_connection():
    """Connects, negotiates TLS and authenticates against the SAVED settings. Sends no email."""
    db = get_db()
    ok, msg, det = smtpmod.test_connection(db)
    audit(db, A(), 'email.smtp_connection_tested', 'email_smtp_settings', 1, None, {'ok': ok})
    return {'ok': ok, 'message': msg, **det}

@admin.post('/smtp/send-test')
@P('email.smtp.manage')
@sec.rate_limit('smtp_send_test', 10, 3600)
def smtp_send_test():
    """One genuine test message to ONE explicitly supplied address. Goes through the normal outbox so it appears in the logs."""
    db, m, b = get_db(), M(), body()
    to = (b.get('to') or '').strip()
    if not prefs.valid_address(to): raise ApiError(422, 'invalid_recipient', 'Enter exactly one valid recipient email address.')
    if m.transport.name != 'smtp': raise ApiError(409, 'smtp_not_active', 'The server is not running the SMTP transport (EMAIL_PROVIDER=smtp), so no test message was sent.')
    if not m.transport.enabled(): raise ApiError(409, 'smtp_disabled', 'SMTP sending is switched off or not fully configured. Save and enable the settings first.')
    t = db.execute("SELECT id FROM email_templates ORDER BY (mail_class='admin') DESC, id LIMIT 1").fetchone()
    got = tdb.get(db, t['id']) if t else None
    if not got: raise ApiError(409, 'no_template', 'No email template is available to render a test message.')
    _, v, spec, optional = got
    ctx = {k: x for k, x in rnd.SAMPLE.items() if k != 'action_url'}; ctx.update(email=to)
    with tx(db):
        eid, _ = outbox.enqueue(db, user_id=A(), to_email=to, template_id=t['id'], version=v['version'], subject=v['subject'][:200], event_type='admin.test_send', entity_ref='smtp-test',
                                idem=f'smtp-test:{A()}:{util.new_ref("T")}', mail_class='admin', ctx=ctx, secret={'action_url': m.base_url + '/'}, non_live=0, created_by=A(), max_attempts=1)
        audit(db, A(), 'email.smtp_test_sent', 'email_outbox', eid, None, {'to': mask(to)})
    m.deliver_one(db, eid)
    r = db.execute('SELECT status,last_error,provider_message_id FROM email_outbox WHERE id=?', (eid,)).fetchone()
    ok = r['status'] == 'accepted'
    return {'ok': ok, 'email_id': eid, 'status': r['status'], 'error': r['last_error'], 'sent_to': mask(to),
            'message': ('The SMTP server accepted the message. That is not proof of inbox delivery: check the recipient mailbox.' if ok else 'The test message was NOT sent: ' + (r['last_error'] or r['status']))}

# ================================================================ compose / campaigns
@admin.get('/customers/search')
@P('email.send')
def customers():
    db = get_db(); q = (request.args.get('q') or '').strip()
    if len(q) < 2: return {'items': []}
    like = '%' + q.replace('%', '').replace('_', '') + '%'
    rows = db.execute("SELECT u.public_id id,u.email,u.status,u.kyc_status,u.email_verified,p.first_name,p.last_name,"
                      "COALESCE((SELECT marketing_opt_in FROM communication_preferences c WHERE c.user_id=u.id),0) marketing_opt_in,"
                      "EXISTS(SELECT 1 FROM email_suppressions s WHERE s.email_key=lower(u.email) AND s.released_at IS NULL) suppressed "
                      "FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.id NOT IN (SELECT user_id FROM user_roles) AND u.status<>'closed' "
                      "AND (u.email LIKE ? OR u.public_id LIKE ? OR p.first_name LIKE ? OR p.last_name LIKE ?) ORDER BY u.id DESC LIMIT 20", (like,) * 4).fetchall()
    return {'items': [dict(r) for r in rows]}

@admin.get('/audiences')
@P('email.send')
def audiences():
    db = get_db(); cls = request.args.get('mail_class', 'service')
    if cls not in ('service', 'marketing'): raise ApiError(422, 'invalid_class', 'mail_class must be service or marketing')
    return {'mail_class': cls, 'items': [{'key': k, 'label': lbl, 'eligible': segments.count(db, k, cls)} for k, (lbl, _) in segments.SEGMENTS.items()],
            'note': 'Counts include only customers who may lawfully receive this class of email: marketing needs opt-in consent and a verified address; suppressed addresses are always excluded.'}

def _need_send_perm(mode):
    code = 'email.send_bulk' if mode == 'broadcast' else 'email.send'
    if 'perms' not in g.__dict__: g.perms = sec.user_perms(get_db(), A())
    if code not in g.perms: raise ApiError(403, 'forbidden', 'You do not have permission for this action.')

@admin.post('/compose/preview')
@P('email.send')
def compose_preview():
    b, db = body(), get_db(); m = M()
    t, v, spec, optional = _wrap(camp.check_template, db, b.get('template_id'), b.get('mode', 'individual'))
    vars_, ov = _wrap(camp.clean_inputs, db, t, optional, b.get('variables'), b.get('overrides'), m.base_url, (m._c('EMAIL_LINK_ALLOWED_HOSTS') or '').split(','))
    ctx = {**{k: x for k, x in rnd.SAMPLE.items() if k not in ('action_url',)}, **{k: camp.expand(x, rnd.SAMPLE, m.base_url) for k, x in vars_.items()}}
    if b.get('user_id'):
        u = db.execute('SELECT u.id,u.public_id,u.email,p.first_name FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.public_id=?', (b['user_id'],)).fetchone()
        if not u: raise ApiError(404, 'not_found', 'Customer not found')
        person = {'first_name': u['first_name'] or 'there', 'customer_id': u['public_id']}
        ctx.update(**person, email=u['email'], **{k: camp.expand(x, person, m.base_url) for k, x in vars_.items()})
        if 'transaction_reference' in vars_:
            st = camp._resolve_reference(db, u['id'], vars_['transaction_reference'])
            if not st: raise ApiError(422, 'reference_mismatch', 'That reference does not belong to this customer.')
            ctx['transaction_status'] = st
    if 'document_url' in cat.referenced_vars(v['subject'], spec): ctx['document_url'] = camp._doc_url(t['id'], m.base_url)
    try:
        r = rnd.render(v['subject'], spec, ctx, optional=optional, mail_class=t['mail_class'], base_url=m.base_url, legal=m.legal(db), unsubscribe_url=m.base_url + '/unsubscribe/?t=preview',
                       banner='PREVIEW. Nothing has been sent.', inline_logo_root=m._c('WEB_ROOT'), overrides=ov, extra_hosts=(m._c('EMAIL_LINK_ALLOWED_HOSTS') or '').split(','))
    except rnd.RenderError as e: raise ApiError(422, e.code, e.message)
    return {'subject': r.subject, 'html': r.html, 'text': r.text, 'mail_class': t['mail_class']}

@admin.post('/compose/test-send')
@P('email.send')
@sec.rate_limit('email_test', 20, 3600)
def compose_test_send():
    """Sends the COMPOSED message (your wording, your template) to the administrator's own address only, with the administrator's own name."""
    b, db = body(), get_db(); m = M()
    t, v, spec, optional = _wrap(camp.check_template, db, b.get('template_id'), 'individual')
    vars_, ov = _wrap(camp.clean_inputs, db, t, optional, b.get('variables'), b.get('overrides'), m.base_url, (m._c('EMAIL_LINK_ALLOWED_HOSTS') or '').split(','))
    me = db.execute('SELECT u.public_id,u.email,p.first_name FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.id=?', (A(),)).fetchone()
    person = {'first_name': me['first_name'] or 'there', 'customer_id': me['public_id']}
    ctx = {**{k: x for k, x in rnd.SAMPLE.items() if k != 'action_url'}, **person, 'email': me['email'], **{k: camp.expand(x, person, m.base_url) for k, x in vars_.items()}}
    if 'document_url' in cat.referenced_vars(v['subject'], spec): ctx['document_url'] = camp._doc_url(t['id'], m.base_url)
    with tx(db):
        eid, _ = outbox.enqueue(db, user_id=A(), to_email=me['email'], template_id=t['id'], version=v['version'], subject=v['subject'][:200], event_type='admin.test_send', entity_ref=t['id'], overrides=ov,
                                idem=f'test:{t["id"]}:{A()}:{util.new_ref("T")}', mail_class='admin', ctx=ctx, created_by=A(), max_attempts=1)
        audit(db, A(), 'email.test_sent', 'email_template', t['id'], None, {'email_id': eid, 'composed': True})
    m.deliver_one(db, eid); r = db.execute('SELECT status,last_error FROM email_outbox WHERE id=?', (eid,)).fetchone()
    return {'email_id': eid, 'status': r['status'], 'error': r['last_error'], 'sent_to': mask(me['email']), 'note': m.status()['warning'] or 'Provider accepted the test message. Check your inbox; delivery is confirmed separately.'}

@admin.post('/campaigns')
@P('email.send')
def campaign_create():
    b, db = body(), get_db(); mode = b.get('mode'); _need_send_perm(mode)
    with tx(db):
        cid, ref, est = _wrap(camp.create, db, M(), A(), name=str(b.get('name', ''))[:120], mode=mode, template_id=b.get('template_id'), audience=b.get('audience') or {}, overrides=b.get('overrides'),
                              variables=b.get('variables'), scheduled_at=b.get('schedule_at'))
        audit(db, A(), 'email.campaign_created', 'email_campaign', ref, None, {'mode': mode, 'template': b.get('template_id'), 'estimated': est})
    return jsonify({'id': cid, 'ref': ref, 'estimated_count': est, 'needs_approval': est > camp.setting(db, 'email_bulk_approval_threshold', 50)}), 201

@admin.post('/campaigns/<int:cid>/submit')
@P('email.send')
def campaign_submit(cid):
    b, db = body(), get_db(); c = _wrap(camp.get, db, cid); _need_send_perm(c['mode'])
    with tx(db):
        if c['mode'] != 'individual':          # an explicit, informed confirmation of the audience size
            aud = json.loads(c['audience']); cls = c['mail_class']
            now_n = segments.count(db, aud['segment'], cls) if c['mode'] == 'broadcast' else len(segments.resolve_ids(db, aud['user_ids'], cls)[0])
            if b.get('confirm_count') != now_n: raise ApiError(409, 'audience_changed', f'The eligible audience is now {now_n} recipients. Review and confirm that number.', {'eligible': now_n})
        status = _wrap(camp.submit, db, M(), cid, A())
        audit(db, A(), 'email.campaign_submitted', 'email_campaign', c['ref'], None, {'status': status, 'recipients': camp.get(db, cid)['estimated_count']})
    return {'status': status}

@admin.post('/campaigns/<int:cid>/approve')
@P('email.bulk_approve')
def campaign_approve(cid):
    db = get_db(); c = _wrap(camp.get, db, cid)
    with tx(db):
        _wrap(camp.approve, db, M(), cid, A()); audit(db, A(), 'email.campaign_approved', 'email_campaign', c['ref'], {'status': 'pending_approval'}, {'status': 'approved'})
    return {'status': camp.get(db, cid)['status']}

@admin.post('/campaigns/<int:cid>/cancel')
@P('email.send')
def campaign_cancel(cid):
    db = get_db(); c = _wrap(camp.get, db, cid)
    if c['created_by'] != A() and 'email.bulk_approve' not in sec.user_perms(db, A()): raise ApiError(403, 'forbidden', 'Only the creator or an approver can cancel a campaign.')
    with tx(db):
        n = _wrap(camp.cancel, db, cid, A()); audit(db, A(), 'email.campaign_cancelled', 'email_campaign', c['ref'], None, {'messages_cancelled': n})
    return {'cancelled_messages': n}

@admin.get('/campaigns')
@P('email.logs.read')
def campaigns_list():
    db = get_db(); camp.refresh_status(db)
    rows = db.execute('SELECT c.*,u.email creator FROM email_campaigns c JOIN users u ON u.id=c.created_by ORDER BY c.id DESC LIMIT 100').fetchall()
    return {'items': [{**{k: r[k] for k in ('id', 'ref', 'name', 'mode', 'template_id', 'mail_class', 'status', 'estimated_count', 'final_count', 'scheduled_at', 'created_at', 'approved_at')}, 'creator': r['creator'], 'delivery': camp.stats(db, r['id'])} for r in rows]}

@admin.get('/campaigns/<int:cid>')
@P('email.logs.read')
def campaign_get(cid):
    db = get_db(); c = _wrap(camp.get, db, cid); camp.refresh_status(db)
    rec = [{'customer': r['public_id'], 'email': mask(r['email']), 'status': r['mstatus'] or r['status'], 'skip_reason': r['skip_reason']} for r in db.execute(
        'SELECT u.public_id,u.email,r.status,r.skip_reason,o.status mstatus FROM email_campaign_recipients r JOIN users u ON u.id=r.user_id LEFT JOIN email_outbox o ON o.id=r.email_id WHERE r.campaign_id=? ORDER BY r.id LIMIT 200', (cid,))]
    return {'campaign': {k: c[k] for k in c.keys() if k not in ('audience',)}, 'audience': {'segment': json.loads(c['audience']).get('segment'), 'selected': len(json.loads(c['audience']).get('user_ids') or [])}, 'delivery': camp.stats(db, cid), 'recipients': rec}

# ================================================================ automations
@admin.get('/automations')
@P('email.logs.read')
def automations():
    db = get_db(); since = util.ts_after(days=-30); out = []
    for r in db.execute('SELECT r.*,t.name tname,t.mail_class FROM email_automation_rules r JOIN email_templates t ON t.id=r.template_id ORDER BY r.kind DESC,r.event_type').fetchall():
        sent = {x['status']: x['n'] for x in db.execute('SELECT status,COUNT(*) n FROM email_outbox WHERE event_type=? AND created_at>=? GROUP BY status', (r['event_type'], since))}
        d = {'id': r['id'], 'code': r['code'], 'kind': r['kind'], 'event_type': r['event_type'], 'event': cat.EVENTS.get(r['event_type']), 'template_id': r['template_id'], 'template': r['tname'], 'mail_class': r['mail_class'],
             'enabled': bool(r['enabled']), 'delay_minutes': r['delay_minutes'], 'params': json.loads(r['params'] or '{}'), 'last_30_days': sent, 'updated_at': r['updated_at']}
        if r['kind'] == 'reminder': d['reminder_state'] = {'in_progress': db.execute('SELECT COUNT(*) FROM email_reminder_state WHERE rule_code=? AND stopped_at IS NULL', (r['code'],)).fetchone()[0],
                                                           'stopped': db.execute('SELECT COUNT(*) FROM email_reminder_state WHERE rule_code=? AND stopped_at IS NOT NULL', (r['code'],)).fetchone()[0]}
        out.append(d)
    gaps = [{'template': t['id'], 'name': t['name'], 'reason': t['description']} for t in db.execute("SELECT id,name,description FROM email_templates WHERE trigger_event IS NULL AND is_system=1 AND manual_send=0 ORDER BY id")]
    return {'items': out, 'no_backend_trigger': gaps}

@admin.patch('/automations/<int:rid>')
@P('email.automations.manage')
def automation_update(rid):
    b, db = body(), get_db()
    with tx(db):
        r = db.execute('SELECT r.*,t.mail_class FROM email_automation_rules r JOIN email_templates t ON t.id=r.template_id WHERE r.id=?', (rid,)).fetchone()
        if not r: raise ApiError(404, 'not_found', 'Automation not found')
        enabled, delay, params = r['enabled'], r['delay_minutes'], json.loads(r['params'] or '{}')
        if 'enabled' in b:
            enabled = 1 if b['enabled'] else 0
            if not enabled and r['kind'] == 'event' and r['mail_class'] in ('security', 'transactional'): text(b.get('reason'), 'reason', 300)        # switching off a security/transactional ALERT needs a recorded reason (reminders are optional nudges)
        if 'delay_minutes' in b:
            if r['kind'] == 'event' and r['mail_class'] in ('security', 'transactional') and int(b['delay_minutes']) != 0: raise ApiError(422, 'not_allowed', 'Security and transactional emails are always sent immediately.')
            delay = int(b['delay_minutes'])
            if not (0 <= delay <= 60 * 24 * 400): raise ApiError(422, 'out_of_range', 'Delay is out of range.')
        for k, lo, hi in (('max_attempts', 1, 10), ('interval_hours', 0, 24 * 365)):
            if k in (b.get('params') or {}):
                if r['kind'] != 'reminder': raise ApiError(422, 'not_allowed', 'Only reminders have attempt limits.')
                params[k] = int(b['params'][k]);
                if not (lo <= params[k] <= hi): raise ApiError(422, 'out_of_range', f'{k} is out of range.')
        if 'respect_quiet_hours' in (b.get('params') or {}): params['respect_quiet_hours'] = bool(b['params']['respect_quiet_hours'])
        db.execute('UPDATE email_automation_rules SET enabled=?, delay_minutes=?, params=?, updated_at=?, updated_by=? WHERE id=?', (enabled, delay, json.dumps(params), util.now(), A(), rid))
        audit(db, A(), 'email.automation_updated', 'email_automation', r['code'], {'enabled': r['enabled'], 'delay_minutes': r['delay_minutes']}, {'enabled': enabled, 'delay_minutes': delay, 'params': params}, b.get('reason'))
    return {'ok': True}

@admin.post('/automations/run-reminders')
@P('email.automations.manage')
def run_reminders():
    db = get_db(); b = body()
    res = reminders.run(db, M(), only=b.get('event_type'))
    audit(db, A(), 'email.reminders_run', 'email_automation', None, None, {k: v for k, v in res.items()})
    return {'result': res}

# ================================================================ outbox / delivery logs
@admin.get('/outbox')
@P('email.logs.read')
def outbox_list():
    db = get_db(); p, n = page_args(request.args); w, a = ["o.status<>'legacy_unsent'"], []
    for k, col in (('status', 'o.status'), ('template', 'o.template_id'), ('mail_class', 'o.mail_class'), ('event', 'o.event_type')):
        if request.args.get(k): w.append(f'{col}=?'); a.append(request.args[k])
    if request.args.get('campaign'): w.append('o.campaign_id=?'); a.append(int(request.args['campaign']))
    if request.args.get('q'): w.append('(u.public_id LIKE ? OR o.entity_ref LIKE ? OR o.provider_message_id LIKE ? OR o.to_email LIKE ?)'); a += ['%' + request.args['q'].replace('%', '') + '%'] * 4
    where = ' AND '.join(w); total = db.execute(f'SELECT COUNT(*) FROM email_outbox o LEFT JOIN users u ON u.id=o.user_id WHERE {where}', a).fetchone()[0]
    rows = db.execute(f'SELECT o.*,u.public_id upid FROM email_outbox o LEFT JOIN users u ON u.id=o.user_id WHERE {where} ORDER BY o.id DESC LIMIT ? OFFSET ?', (*a, n, (p - 1) * n)).fetchall()
    items = [{'id': r['id'], 'created_at': r['created_at'], 'customer': r['upid'], 'to': mask(r['to_email']), 'template_id': r['template_id'], 'version': r['template_version'], 'mail_class': r['mail_class'], 'event': r['event_type'],
              'entity_ref': r['entity_ref'], 'status': r['status'], 'attempts': r['attempts'], 'provider': r['provider'], 'provider_message_id': r['provider_message_id'], 'last_error': r['last_error'],
              'next_attempt_at': r['next_attempt_at'] if r['status'] in ('queued', 'retry') else None, 'delivered_at': r['delivered_at']} for r in rows]
    return {'items': items, 'total': total, 'page': p, 'size': n}

@admin.get('/outbox/<int:eid>')
@P('email.logs.read')
def outbox_get(eid):
    db = get_db(); r = db.execute('SELECT o.*,u.public_id upid FROM email_outbox o LEFT JOIN users u ON u.id=o.user_id WHERE o.id=?', (eid,)).fetchone()
    if not r: raise ApiError(404, 'not_found', 'Message not found')
    d = {'id': r['id'], 'created_at': r['created_at'], 'customer': r['upid'], 'to': mask(r['to_email']), 'template_id': r['template_id'], 'version': r['template_version'], 'mail_class': r['mail_class'], 'event': r['event_type'],
         'entity_ref': r['entity_ref'], 'status': r['status'], 'attempts': r['attempts'], 'max_attempts': r['max_attempts'], 'provider': r['provider'], 'provider_message_id': r['provider_message_id'], 'last_error': r['last_error'],
         'accepted_at': r['accepted_at'], 'delivered_at': r['delivered_at'], 'next_attempt_at': r['next_attempt_at'], 'non_live': bool(r['non_live']), 'campaign_id': r['campaign_id']}
    d['attempts_log'] = [dict(x) for x in db.execute('SELECT attempt_no,transport,started_at,finished_at,outcome,http_status,error_code,error_message FROM email_attempts WHERE email_id=? ORDER BY id', (eid,))]
    d['provider_events'] = [dict(x) for x in db.execute('SELECT provider,event_type,occurred_at,received_at,applied FROM email_events WHERE email_id=? ORDER BY id', (eid,))]
    d['status_explained'] = {'accepted': 'The provider accepted the message. Inbox delivery is not confirmed.', 'delivered': 'A verified provider event confirmed delivery to the recipient mail server.', 'dev_captured': 'Captured locally by the development transport. Nothing was sent.',
                             'blocked': 'Blocked by the sandbox guard so test data cannot reach real recipients.', 'suppressed': 'Skipped at send time (unsubscribed, suppressed address, closed account or no consent).'}.get(r['status'])
    return d

@admin.get('/outbox/<int:eid>/preview')
@P('email.logs.read')
def outbox_preview(eid):
    """The message as it was/will be rendered, with one-time links removed. Viewing customer message content is itself audited."""
    db, m = get_db(), M(); r = db.execute('SELECT * FROM email_outbox WHERE id=?', (eid,)).fetchone()
    if not r or not r['template_id']: raise ApiError(404, 'not_found', 'Message not found')
    audit(db, A(), 'email.message_viewed', 'email_outbox', eid)
    try: x = m.render_row(db, r, banner='ARCHIVE VIEW. One-time links are removed.', inline_logo=True, show_secret=False)
    except rnd.RenderError as e: raise ApiError(422, e.code, e.message)
    return {'subject': x.subject, 'html': x.html, 'text': x.text}

@admin.post('/outbox/<int:eid>/retry')
@P('email.settings.manage')
def outbox_retry(eid):
    db = get_db()
    with tx(db): _wrap(outbox.requeue_failed, db, eid); audit(db, A(), 'email.message_retried', 'email_outbox', eid)
    return {'ok': True}

@admin.post('/outbox/<int:eid>/cancel')
@P('email.settings.manage')
def outbox_cancel(eid):
    db = get_db()
    with tx(db):
        if not outbox.cancel(db, eid): raise ApiError(409, 'invalid_state', 'Only queued messages can be cancelled.')
        audit(db, A(), 'email.message_cancelled', 'email_outbox', eid)
    return {'ok': True}

@admin.get('/delivery-logs')
@P('email.logs.read')
def delivery_logs():
    db = get_db(); p, n = page_args(request.args); kind = request.args.get('kind', 'events')
    if kind == 'attempts':
        total = db.execute('SELECT COUNT(*) FROM email_attempts').fetchone()[0]
        rows = [dict(x) for x in db.execute('SELECT a.email_id,a.attempt_no,a.transport,a.started_at,a.outcome,a.http_status,a.error_code,a.error_message,o.template_id FROM email_attempts a JOIN email_outbox o ON o.id=a.email_id ORDER BY a.id DESC LIMIT ? OFFSET ?', (n, (p - 1) * n))]
    else:
        total = db.execute('SELECT COUNT(*) FROM email_events').fetchone()[0]
        rows = [dict(x) for x in db.execute('SELECT e.email_id,e.provider,e.event_type,e.occurred_at,e.received_at,e.applied,o.template_id FROM email_events e LEFT JOIN email_outbox o ON o.id=e.email_id ORDER BY e.id DESC LIMIT ? OFFSET ?', (n, (p - 1) * n))]
    return {'kind': kind, 'items': rows, 'total': total, 'page': p, 'size': n}

@admin.post('/queue/process')
@P('email.settings.manage')
def queue_process():
    db, m = get_db(), M(); res = m.deliver_due(db, limit=int(body().get('limit', 25)) if request.data else 25); camp.refresh_status(db)
    return {**res, 'note': m.status()['warning']}

# ================================================================ suppression / preferences
@admin.get('/suppressions')
@P('email.logs.read')
def suppressions():
    db = get_db()
    rows = db.execute('SELECT id,email_key,scope,reason,source,created_at,released_at,release_reason FROM email_suppressions ORDER BY id DESC LIMIT 200').fetchall()
    return {'items': [{**dict(r), 'email_key': mask(r['email_key'])} for r in rows], 'marketing_opted_in': db.execute('SELECT COUNT(*) FROM communication_preferences WHERE marketing_opt_in=1').fetchone()[0],
            'consent_events': db.execute('SELECT COUNT(*) FROM communication_consent_log').fetchone()[0]}

@admin.post('/suppressions')
@P('email.suppression.manage')
def suppression_add():
    b, db = body(), get_db(); addr = str(b.get('email', '')).strip().lower(); scope = b.get('scope', 'all')
    if not prefs.valid_address(addr) or scope not in ('all', 'marketing'): raise ApiError(422, 'invalid_request', 'Enter a valid address and scope.')
    with tx(db):
        created = prefs.add_suppression(db, addr, scope, 'manual', 'admin', None, A()); audit(db, A(), 'email.suppression_added', 'email_suppression', prefs.email_key(addr)[:3] + '***', None, {'scope': scope}, text(b.get('reason'), 'reason', 300))
    return {'created': created}

@admin.post('/suppressions/<int:sid>/release')
@P('email.suppression.manage')
def suppression_release(sid):
    b, db = body(), get_db(); why = text(b.get('reason'), 'reason', 300)
    with tx(db):
        s = db.execute('SELECT * FROM email_suppressions WHERE id=?', (sid,)).fetchone()
        if not s or not prefs.release_suppression(db, sid, A(), why): raise ApiError(409, 'invalid_state', 'Not found or already released.')
        audit(db, A(), 'email.suppression_released', 'email_suppression', sid, {'reason': s['reason'], 'scope': s['scope']}, {'released': True}, why)
    return {'ok': True}

KEYS = ('email_bulk_approval_threshold', 'email_campaign_max_recipients', 'email_quiet_start_hour', 'email_quiet_end_hour', 'email_tracking_enabled', 'email_max_attempts')
@admin.get('/settings')
@P('email.settings.manage')
def settings_get():
    db = get_db()
    return {'provider': M().status(), 'settings': {r['key']: {'value': r['value'], 'description': r['description']} for r in db.execute(f"SELECT key,value,description FROM system_settings WHERE key IN ({','.join('?' * len(KEYS))})", KEYS)},
            'env': {'EMAIL_PROVIDER': 'resend | ses | dev', 'EMAIL_PROVIDER_KEY': 'Resend API key (secret)', 'EMAIL_FROM_ADDRESS': 'verified sender', 'EMAIL_FROM_NAME': '', 'PUBLIC_BASE_URL': 'https origin used for ALL email links',
                    'EMAIL_WEBHOOK_SECRET': 'Resend webhook signing secret (secret)', 'EMAIL_SANDBOX_ALLOWLIST': 'non-production allow-list', 'note': 'Provider credentials are environment variables and are never shown or editable here.'}}

@admin.put('/settings')
@P('email.settings.manage')
def settings_put():
    b, db = body(), get_db(); changed = {}
    with tx(db):
        for k, v in (b.get('settings') or {}).items():
            if k not in KEYS: raise ApiError(422, 'invalid_setting', f'Unknown setting {k}')
            try: iv = int(v)
            except (TypeError, ValueError): raise ApiError(422, 'invalid_value', f'{k} must be a whole number')
            lim = {'email_quiet_start_hour': (0, 23), 'email_quiet_end_hour': (0, 23), 'email_tracking_enabled': (0, 1), 'email_max_attempts': (1, 10), 'email_bulk_approval_threshold': (1, 100000), 'email_campaign_max_recipients': (1, 100000)}[k]
            if not (lim[0] <= iv <= lim[1]): raise ApiError(422, 'out_of_range', f'{k} must be between {lim[0]} and {lim[1]}')
            old = db.execute('SELECT value FROM system_settings WHERE key=?', (k,)).fetchone()['value']
            db.execute('UPDATE system_settings SET value=?, updated_at=? WHERE key=?', (str(iv), util.now(), k)); changed[k] = {'from': old, 'to': iv}
        audit(db, A(), 'email.settings_changed', 'email_settings', None, None, changed, b.get('reason'))
    return {'changed': changed}

# ================================================================ public: unsubscribe, customer preferences, provider webhook
def _secret(): return current_app.config.get('AUTH_SECRET') or current_app.config.get('SECRET_KEY') or ''

@public.get('/email/unsubscribe/info')
@sec.rate_limit('unsub_info', 60, 3600)
def unsub_info():
    uid = prefs.parse_unsub_token(_secret(), request.args.get('t'))
    if not uid: raise ApiError(400, 'invalid_token', 'This unsubscribe link is not valid.')
    db = get_db(); u = db.execute('SELECT email FROM users WHERE id=?', (uid,)).fetchone()
    return {'email': mask(u['email']) if u else None, 'subscribed': prefs.marketing_opted_in(db, uid)}

@public.post('/email/unsubscribe')
@sec.rate_limit('unsub', 30, 3600)
def unsubscribe():
    b = body(); uid = prefs.parse_unsub_token(_secret(), b.get('token')); db = get_db()
    if not uid: raise ApiError(400, 'invalid_token', 'This unsubscribe link is not valid.')
    with tx(db): prefs.set_marketing(db, uid, False, 'unsubscribe_link', sec.client_ip())
    return {'ok': True, 'subscribed': False}

@public.post('/email/unsubscribe/one-click')
def unsubscribe_one_click():
    """RFC 8058: mail clients POST `List-Unsubscribe=One-Click` to the URL from the List-Unsubscribe header."""
    uid = prefs.parse_unsub_token(_secret(), request.args.get('t')); db = get_db()
    if not uid: raise ApiError(400, 'invalid_token', 'Invalid link.')
    with tx(db): prefs.set_marketing(db, uid, False, 'one_click_unsubscribe', sec.client_ip())
    return {'ok': True}

@public.get('/me/communication-preferences')
@sec.login_required
def my_prefs():
    db = get_db(); return {'marketing_opt_in': prefs.marketing_opted_in(db, g.user['id']), 'timezone': prefs.get_timezone(db, g.user['id'], current_app.config.get('EMAIL_DEFAULT_TIMEZONE', 'UTC')),
                           'note': 'Security alerts and messages about your transactions are always sent: they cannot be switched off.'}

@public.put('/me/communication-preferences')
@sec.login_required
def my_prefs_put():
    b, db = body(), get_db()
    with tx(db):
        if 'marketing_opt_in' in b: prefs.set_marketing(db, g.user['id'], b['marketing_opt_in'] is True, 'settings', sec.client_ip())
        if b.get('timezone'):
            try: prefs.set_timezone(db, g.user['id'], str(b['timezone']))
            except ValueError as e: raise ApiError(422, 'invalid_timezone', str(e))
    return my_prefs()

@public.post('/webhooks/email/resend')
def resend_webhook():
    m = M(); db = get_db(); raw = request.get_data()
    try:
        res = webhooks.handle_resend(m, db, current_app.config.get('EMAIL_WEBHOOK_SECRET', ''), {k.lower(): v for k, v in request.headers.items()}, raw,
                                     tracking_enabled=bool(camp.setting(db, 'email_tracking_enabled', 0)))
    except webhooks.WebhookError as e:
        return jsonify({'error': {'code': e.code}}), e.status
    return jsonify(res), 200
