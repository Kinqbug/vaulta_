"""Shared helpers for the email tests. A MockTransport replaces the real provider: NO network, NO real email is ever sent."""
import json, hmac, hashlib, base64, time
import test_vaulta as tv
from server.mailer.outbox import Mailer
from server.mailer import events
from server.mailer.transports import MockTransport, Result

BASE = 'https://app.vaulta.example'
WH_SECRET = 'whsec_' + base64.b64encode(b'unit-test-signing-key-0123456789').decode()

def sign(body: bytes, msg_id='msg_1', ts=None, secret=WH_SECRET):
    ts = str(int(ts if ts is not None else time.time()))
    key = base64.b64decode(secret.split('_', 1)[1])
    sig = base64.b64encode(hmac.new(key, f'{msg_id}.{ts}.'.encode() + body, hashlib.sha256).digest()).decode()
    return {'svix-id': msg_id, 'svix-timestamp': ts, 'svix-signature': 'v1,' + sig, 'Content-Type': 'application/json'}

class EmailBase(tv.Base):
    def setUp(self):
        super().setUp()
        self.app.config.update(PUBLIC_BASE_URL=BASE, EMAIL_FROM_ADDRESS='no-reply@mail.vaulta.example', EMAIL_FROM_NAME='VAULTA', EMAIL_WEBHOOK_SECRET=WH_SECRET)
        self.mock = MockTransport(); self.app.mailer = Mailer(self.app.config, self.mock); self.m = self.app.mailer

    def emit(self, event, uid, ref, **kw):
        with self.app.app_context(): return events.emit(self.db, event, uid, ref, **kw)

    def settle(self, d, **override):
        """Deliver the provider's settlement event for a deposit, as the webhook route would (inside an app context)."""
        from server import finance as fin
        with self.app.app_context(): return fin.handle_payment_event(self.db, {**self.P.payments.internal_event(d['external_ref']), **override})

    def drain(self, n=500, now=None): return self.m.deliver_due(self.db, limit=n, now=now)

    def rows(self, event=None, template=None, to=None, status=None):
        q, a = "SELECT * FROM email_outbox WHERE status<>'legacy_unsent'", []
        for col, v in (('event_type', event), ('template_id', template), ('to_email', to), ('status', status)):
            if v: q += f' AND {col}=?'; a.append(v)
        return self.db.execute(q + ' ORDER BY id', a).fetchall()

    def webhook(self, c, payload, **kw):
        body = json.dumps(payload).encode(); h = sign(body, **kw)
        return c.post('/api/webhooks/email/resend', data=body, headers=h)

    def accepted_id(self, **kw):
        """Queue + send one message so webhook tests have a provider_message_id to talk about."""
        self.signup(**kw); self.drain()
        r = self.rows(template='EMAIL_VERIFY_001')[0]; return r['id'], r['provider_message_id']
