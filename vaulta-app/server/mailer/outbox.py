"""Persistent email queue and delivery.

Status model (what each value is evidence of):
  queued       committed with the business transaction; waiting for a worker (next_attempt_at says when)
  sending      claimed by a worker (lease in locked_until)
  retry        provider/network gave a retryable error; will be tried again after exponential backoff
  accepted     the provider API accepted the message. NOT proof of inbox delivery
  delivered    a verified provider webhook said the recipient's mail server accepted it
  delayed      provider reported a temporary delivery problem
  bounced      verified provider webhook: permanent/undetermined bounce
  complained   verified provider webhook: recipient marked it as spam
  failed       permanent error, retries exhausted, render error, or unknown outcome we refuse to resend
  suppressed   skipped at send time: unsubscribed / suppressed address / closed account / no consent
  blocked      skipped by the sandbox guard (non-production environment or simulated money)
  cancelled    cancelled by an administrator or its campaign
  dev_captured the DEV transport stored it locally. Nothing was sent
"""
import json, random, re
from .. import util
from ..db import tx
from . import catalog as cat, render as rnd, prefs, templates_db as tdb
from .transports import Msg

SECRET_FREE = ('accepted', 'delivered', 'bounced', 'complained', 'failed', 'suppressed', 'blocked', 'cancelled', 'dev_captured')
RANK = {'queued': 0, 'retry': 0, 'sending': 1, 'accepted': 2, 'delayed': 3, 'delivered': 4, 'failed': 5, 'bounced': 6, 'complained': 7}
LEASE_SECONDS = 300

def backoff_seconds(attempt):
    """60s, 2m, 4m, 8m, ... capped at 6h, ±10% jitter."""
    base = min(21600, 60 * 2 ** max(0, attempt - 1)); return int(base * random.uniform(0.9, 1.1))

def enqueue(db, *, user_id, to_email, template_id, version, subject, event_type, entity_ref, idem, mail_class, ctx, secret=None, overrides=None,
            run_at=None, non_live=0, campaign_id=None, created_by=None, max_attempts=6, status='queued', error=None):
    """Idempotent: the same `idem` key never creates a second message. Returns (id, created)."""
    n = util.now()
    cur = db.execute(
        'INSERT OR IGNORE INTO email_outbox(user_id,to_email,subject,body,non_live,created_at,status,mail_class,template_id,template_version,event_type,entity_ref,idempotency_key,'
        'context_json,secret_json,overrides_json,next_attempt_at,attempts,max_attempts,created_by,campaign_id,updated_at,last_error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?)',
        (user_id, to_email, subject, '', int(bool(non_live)), n, status, mail_class, template_id, version, event_type, entity_ref, idem, json.dumps(ctx or {}, sort_keys=True),
         json.dumps(secret) if secret else None, json.dumps(overrides) if overrides else None, run_at or n, max_attempts, created_by, campaign_id, n, error))
    if cur.rowcount: return cur.lastrowid, True
    return db.execute('SELECT id FROM email_outbox WHERE idempotency_key=?', (idem,)).fetchone()['id'], False


class Mailer:
    def __init__(self, cfg, transport):
        self.cfg, self.transport = cfg, transport
    def _c(self, k, d=''): return self.cfg.get(k, d) if isinstance(self.cfg, dict) else getattr(self.cfg, k, d)

    def identity(self):
        """Sender identity: SMTP settings saved by an administrator override the environment defaults."""
        i = {'from_address': self._c('EMAIL_FROM_ADDRESS'), 'from_name': self._c('EMAIL_FROM_NAME') or 'VAULTA', 'reply_to': self._c('EMAIL_REPLY_TO'), 'public_base_url': self._c('PUBLIC_BASE_URL')}
        f = getattr(self.transport, 'identity', None)
        if f:
            for k, v in f().items():
                if v: i[k] = v
        return i

    @property
    def base_url(self):
        b = self.identity()['public_base_url']
        if b: return b
        return 'http://127.0.0.1:5000' if self._c('ENV', 'development') == 'development' else ''   # dev-only fallback; production config requires https PUBLIC_BASE_URL

    @property
    def configured(self): return bool(self.transport.live)

    def status(self):
        """Safe summary for the admin UI: booleans and names only, never credentials."""
        c, t = self._c, self.transport; idn = self.identity()
        en = getattr(t, 'enabled', None); disabled = bool(en) and not en()
        return {'provider': t.name, 'live_delivery': bool(t.live) and not disabled, 'sending_enabled': not disabled, 'environment': c('ENV'), 'from_address': idn['from_address'] or None, 'from_name': idn['from_name'],
                'public_base_url': self.base_url or None, 'webhook_secret_configured': bool(c('EMAIL_WEBHOOK_SECRET')), 'sandbox_allowlist_configured': bool(c('EMAIL_SANDBOX_ALLOWLIST')),
                'delivery_confirmation': t.name == 'resend' and bool(c('EMAIL_WEBHOOK_SECRET')),
                'warning': ('Email sending is switched off or SMTP is not configured. Messages stay queued and are not sent.' if disabled else None) if t.live else 'Real email delivery is NOT configured. Messages are captured locally and are never sent to recipients.'}

    def legal(self, db):
        return {r['key']: r['value'] for r in db.execute("SELECT key,value FROM legal_config WHERE key IN ('legal_entity_name','registered_address','website_url','support_email') AND value<>''")}

    def sandbox_ok(self, addr):
        a = (addr or '').lower()
        return any(a == e or (e.startswith('@') and a.endswith(e)) for e in (x.strip().lower() for x in (self._c('EMAIL_SANDBOX_ALLOWLIST') or '').split(',')) if e)

    def unsub_urls(self, user_id):
        tok = prefs.make_unsub_token(self._c('AUTH_SECRET') or self._c('SECRET_KEY'), user_id)
        return f'{self.base_url}/unsubscribe/?t={tok}', f'{self.base_url}/api/email/unsubscribe/one-click?t={tok}'

    # ------------------------------------------------------------ rendering (shared by delivery, preview and admin views)
    def render_row(self, db, row, *, banner=None, inline_logo=False, show_secret=True):
        got = tdb.get(db, row['template_id'], row['template_version'])
        if not got: raise rnd.RenderError('template_missing', f"template {row['template_id']} v{row['template_version']} not found")
        t, v, spec, _ = got
        ctx = json.loads(row['context_json'] or '{}')
        secret = json.loads(row['secret_json']) if (row['secret_json'] and show_secret) else ({'action_url': 'https://link.removed.invalid/'} if not show_secret or 'action_url' in cat.referenced_vars(v['subject'], spec) else {})
        unsub = self.unsub_urls(row['user_id'])[0] if row['mail_class'] == 'marketing' and row['user_id'] else None
        return rnd.render(v['subject'], spec, ctx, secret=secret, optional=tdb.optional_for(db, row['template_id']), mail_class=row['mail_class'], base_url=self.base_url,
                          legal=self.legal(db), unsubscribe_url=unsub, banner=banner, overrides=json.loads(row['overrides_json'] or '{}'),
                          inline_logo_root=self._c('WEB_ROOT') if inline_logo else None, extra_hosts=(self._c('EMAIL_LINK_ALLOWED_HOSTS') or '').split(','))

    # ------------------------------------------------------------ delivery
    def deliver_due(self, db, limit=25, now=None):
        now = now or util.now()
        stats = {'recovered': self.recover_stuck(db, now), 'processed': 0}
        en = getattr(self.transport, 'enabled', None)
        if en and not en(): stats['paused'] = True; return stats      # SMTP switched off / unconfigured: leave the queue untouched
        ids = [r['id'] for r in db.execute("SELECT id FROM email_outbox WHERE status IN ('queued','retry') AND next_attempt_at<=? ORDER BY next_attempt_at,id LIMIT ?", (now, limit))]
        for i in ids:
            if self.deliver_one(db, i, now): stats['processed'] += 1
        return stats

    def recover_stuck(self, db, now):
        """A worker died mid-send: the outcome is unknown. Re-send only when the provider honours our idempotency key; otherwise fail rather than risk a duplicate."""
        n = 0
        for r in db.execute("SELECT * FROM email_outbox WHERE status='sending' AND locked_until<?", (now,)).fetchall():
            with tx(db):
                db.execute("INSERT INTO email_attempts(email_id,attempt_no,transport,started_at,finished_at,outcome,error_code,error_message) VALUES(?,?,?,?,?,'unknown','lease_expired','worker stopped before recording the outcome')", (r['id'], r['attempts'], self.transport.name, r['updated_at'] or now, now))
                if self.transport.supports_idempotency and r['attempts'] < r['max_attempts']:
                    db.execute("UPDATE email_outbox SET status='retry', next_attempt_at=?, locked_until=NULL, updated_at=? WHERE id=? AND status='sending'", (now, now, r['id']))
                else:
                    db.execute("UPDATE email_outbox SET status='failed', secret_json=NULL, locked_until=NULL, last_error='outcome unknown after worker interruption; not re-sent to avoid a duplicate', updated_at=? WHERE id=? AND status='sending'", (now, r['id']))
            n += 1
        return n

    def deliver_one(self, db, email_id, now=None):
        now = now or util.now()
        lease = util.ts_after(seconds=LEASE_SECONDS)
        with tx(db):
            claimed = db.execute("UPDATE email_outbox SET status='sending', attempts=attempts+1, locked_until=?, updated_at=? WHERE id=? AND status IN ('queued','retry') AND next_attempt_at<=?", (lease, now, email_id, now)).rowcount
        if not claimed: return False
        row = db.execute('SELECT * FROM email_outbox WHERE id=?', (email_id,)).fetchone()
        started = util.now(); no = row['attempts']
        decision = self._prepare(db, row)
        if decision[0] == 'final':
            _, status, outcome, why = decision
            self._finish(db, row, status, outcome, started, error=why)
            return True
        _, msg = decision
        try: res = self.transport.send(msg)
        except Exception as e:                                       # a transport bug must not wedge the queue
            from .transports import Result
            res = Result(False, retryable=True, code='transport_exception', error=f'{type(e).__name__}')
        self._apply(db, row, res, started)
        return True

    def _prepare(self, db, row):
        """-> ('send', Msg) or ('final', status, attempt_outcome, reason)"""
        addr, cls = row['to_email'], row['mail_class']
        if not prefs.valid_address(addr): return ('final', 'failed', 'permanent_error', 'invalid recipient address')
        if row['user_id']:
            u = db.execute('SELECT status FROM users WHERE id=?', (row['user_id'],)).fetchone()
            if u and u['status'] == 'closed': return ('final', 'suppressed', 'suppressed', 'account closed')
        why = prefs.is_suppressed(db, addr, 'marketing' if cls == 'marketing' else 'all')
        if why: return ('final', 'suppressed', 'suppressed', f'address suppressed ({why})')
        if cls == 'marketing' and not (row['user_id'] and prefs.marketing_opted_in(db, row['user_id'])): return ('final', 'suppressed', 'suppressed', 'no marketing consent')
        if self.transport.enforce_sandbox and (self._c('ENV') != 'production' or row['non_live']) and not self.sandbox_ok(addr):
            return ('final', 'blocked', 'blocked', 'sandbox guard: this environment/message may not be sent to this recipient')
        idn = self.identity()
        if self.transport.live and not idn['from_address']: return ('final', 'failed', 'permanent_error', 'EMAIL_FROM_ADDRESS is not configured')
        is_test = row['event_type'] == 'admin.test_send'
        banner = 'TEST EMAIL. Sample data only; this is not a real transaction.' if is_test else ('DEVELOPMENT EMAIL. Captured locally; not delivered to the recipient.' if self.transport.name in ('dev', 'mock') else None)
        try: r = self.render_row(db, row, banner=banner)
        except rnd.RenderError as e: return ('final', 'failed', 'permanent_error', f'render: {e.code}' + (f" ({', '.join(e.variables)})" if e.variables else ''))
        headers = {}
        if cls == 'marketing':
            page, one_click = self.unsub_urls(row['user_id'])
            headers = {'List-Unsubscribe': f'<{one_click}>', 'List-Unsubscribe-Post': 'List-Unsubscribe=One-Click'}
        prefix = '[TEST] ' if is_test else ('[DEV] ' if self.transport.name in ('dev', 'mock') else '')
        return ('send', Msg(addr, idn['from_address'] or 'dev@vaulta.invalid', idn['from_name'] or 'VAULTA', prefix + r.subject, r.html, r.text,
                            reply_to=idn['reply_to'], headers=headers, idem_key=row['idempotency_key'] or f"outbox-{row['id']}", tags={'email_id': row['id'], 'template': row['template_id'] or ''}))

    def _finish(self, db, row, status, outcome, started, error=None, message_id=None):
        n = util.now()
        with tx(db):
            db.execute('INSERT INTO email_attempts(email_id,attempt_no,transport,started_at,finished_at,outcome,error_message,provider_message_id) VALUES(?,?,?,?,?,?,?,?)', (row['id'], row['attempts'], self.transport.name, started, n, outcome, error, message_id))
            db.execute('UPDATE email_outbox SET status=?, secret_json=NULL, locked_until=NULL, last_error=?, updated_at=? WHERE id=?', (status, error, n, row['id']))

    def _apply(self, db, row, res, started):
        n = util.now(); no = row['attempts']
        with tx(db):
            if res.ok:
                status = 'dev_captured' if res.captured else 'accepted'
                db.execute('INSERT INTO email_attempts(email_id,attempt_no,transport,started_at,finished_at,outcome,http_status,provider_message_id) VALUES(?,?,?,?,?,?,?,?)',
                           (row['id'], no, self.transport.name, started, n, 'captured' if res.captured else 'accepted', res.http_status, res.message_id))
                db.execute('UPDATE email_outbox SET status=?, provider=?, provider_message_id=?, accepted_at=?, secret_json=NULL, locked_until=NULL, last_error=NULL, updated_at=? WHERE id=?',
                           (status, self.transport.name, res.message_id, n, n, row['id']))
                if res.captured and self._c('ENV') == 'development' and self.transport.name == 'dev': self._capture(db, row)
                if res.message_id: self._reconcile_orphans(db, row['id'], res.message_id)
                return
            retry = res.retryable and no < row['max_attempts']
            db.execute('INSERT INTO email_attempts(email_id,attempt_no,transport,started_at,finished_at,outcome,http_status,error_code,error_message) VALUES(?,?,?,?,?,?,?,?,?)',
                       (row['id'], no, self.transport.name, started, n, 'retryable_error' if res.retryable else 'permanent_error', res.http_status, res.code, res.error))
            if retry:
                db.execute("UPDATE email_outbox SET status='retry', next_attempt_at=?, locked_until=NULL, last_error=?, updated_at=? WHERE id=?", (util.ts_after(seconds=backoff_seconds(no)), f'{res.code}: {res.error}'[:300], n, row['id']))
            else:
                why = ('retries exhausted: ' if res.retryable else 'permanent failure: ') + f'{res.code}: {res.error}'
                db.execute("UPDATE email_outbox SET status='failed', secret_json=NULL, locked_until=NULL, last_error=?, updated_at=? WHERE id=?", (why[:300], n, row['id']))

    def _capture(self, db, row):
        try:
            r = self.render_row(db, row, banner='DEVELOPMENT EMAIL. Captured locally; not delivered to the recipient.', inline_logo=True)
            db.execute('INSERT OR REPLACE INTO email_dev_capture(email_id,subject,html,text,created_at) VALUES(?,?,?,?,?)', (row['id'], '[DEV] ' + r.subject, r.html, r.text, util.now()))
        except rnd.RenderError: pass

    # ------------------------------------------------------------ provider events (webhooks)
    def _reconcile_orphans(self, db, email_id, message_id):
        for ev in db.execute("SELECT * FROM email_events WHERE email_id IS NULL AND applied=0 AND json_extract(detail,'$.message_id')=? ORDER BY id", (message_id,)).fetchall():
            db.execute('UPDATE email_events SET email_id=? WHERE id=?', (email_id, ev['id']))
            self._apply_event(db, email_id, ev['id'], ev['event_type'], json.loads(ev['detail'] or '{}'), ev['occurred_at'])

    def record_event(self, db, provider, event_id, etype, message_id, occurred_at, detail):
        """Returns 'duplicate' | 'unmatched' | 'applied' | 'recorded'. Webhook replays and out-of-order events are safe."""
        detail = {**(detail or {}), 'message_id': message_id}
        with tx(db):
            row = db.execute('SELECT id FROM email_outbox WHERE provider_message_id=?', (message_id,)).fetchone() if message_id else None
            cur = db.execute('INSERT OR IGNORE INTO email_events(email_id,provider,provider_event_id,event_type,occurred_at,received_at,detail) VALUES(?,?,?,?,?,?,?)',
                             (row['id'] if row else None, provider, event_id, etype, occurred_at, util.now(), json.dumps(detail)))
            if not cur.rowcount: return 'duplicate'
            if not row: return 'unmatched'
            return 'applied' if self._apply_event(db, row['id'], cur.lastrowid, etype, detail, occurred_at) else 'recorded'

    def _apply_event(self, db, email_id, event_row_id, etype, detail, occurred_at):
        row = db.execute('SELECT * FROM email_outbox WHERE id=?', (email_id,)).fetchone()
        cur = row['status']; n = util.now(); new = None
        if etype == 'email.delivered': new = 'delivered'
        elif etype == 'email.delivery_delayed': new = 'delayed'
        elif etype == 'email.bounced':
            btype = (detail.get('bounce_type') or '').lower()
            new = 'delayed' if btype == 'transient' else 'bounced'
            if btype == 'permanent': prefs.add_suppression(db, row['to_email'], 'all', 'hard_bounce', 'provider_webhook', email_id)
        elif etype == 'email.complained':
            new = 'complained'
            if row['mail_class'] == 'marketing':
                prefs.add_suppression(db, row['to_email'], 'marketing', 'complaint', 'provider_webhook', email_id)
                if row['user_id']: prefs.set_marketing(db, row['user_id'], False, 'spam_complaint')
        elif etype == 'email.failed': new = 'failed'
        if not new or cur in ('legacy_unsent', 'dev_captured', 'cancelled', 'suppressed', 'blocked'): return False
        if RANK.get(new, 0) < RANK.get(cur, 0): return False        # never move backwards (e.g. a late "delayed" after "delivered")
        sets, args = 'status=?, updated_at=?, secret_json=NULL', [new, n]
        if new == 'delivered': sets += ', delivered_at=?'; args.append(occurred_at or n)
        if new in ('bounced', 'failed'): sets += ', last_error=?'; args.append((detail.get('bounce_message') or detail.get('reason') or etype)[:300])
        db.execute(f'UPDATE email_outbox SET {sets} WHERE id=?', (*args, email_id))
        db.execute('UPDATE email_events SET applied=1 WHERE id=?', (event_row_id,))
        return True


# ---------------------------------------------------------------- administrative actions
def requeue_failed(db, email_id):
    r = db.execute('SELECT * FROM email_outbox WHERE id=?', (email_id,)).fetchone()
    if not r or r['status'] != 'failed': raise ValueError('only failed messages can be retried')
    if 'action_url' in cat.referenced_vars(*_sub_spec(db, r)): raise ValueError('this message carried a one-time link that was removed after sending failed; ask the customer to request a new link')
    n = util.now()
    db.execute("UPDATE email_outbox SET status='queued', attempts=0, next_attempt_at=?, last_error=NULL, updated_at=? WHERE id=?", (n, n, email_id))

def _sub_spec(db, r):
    got = tdb.get(db, r['template_id'], r['template_version'])
    return (got[1]['subject'], got[2]) if got else ('', {})

def cancel(db, email_id):
    n = util.now()
    return db.execute("UPDATE email_outbox SET status='cancelled', secret_json=NULL, locked_until=NULL, updated_at=? WHERE id=? AND status IN ('queued','retry')", (n, email_id)).rowcount == 1
