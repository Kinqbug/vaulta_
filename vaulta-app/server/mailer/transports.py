"""Delivery transports behind one interface:  transport.send(Msg) -> Result.

  dev     writes nothing to the network. The outbox records the message as `dev_captured`, which is explicitly NOT "delivered".
  mock    in-memory, scriptable results. Automated tests only (refused outside development by config).
  resend  Resend REST API (https://resend.com/docs/api-reference/emails/send-email) with an Idempotency-Key, plus Svix-signed webhooks (webhooks.py).
  ses     Amazon SES v2 SendEmail over HTTPS with SigV4 (no SDK dependency). Delivery events arrive through SNS, which this project does not verify,
          so SES messages stay `accepted` and are never reported as delivered.

A transport reports only what the provider said. `ok=True` means the provider API accepted the message for delivery: never that it reached an inbox.
"""
import json, hmac, hashlib, urllib.request, urllib.error, socket, re
from datetime import datetime, timezone
from urllib.parse import urlparse, quote

class Msg:
    def __init__(self, to, from_addr, from_name, subject, html, text, reply_to='', headers=None, idem_key='', tags=None):
        for f in (to, from_addr, reply_to, from_name, subject):
            if re.search(r'[\r\n]', f or ''): raise ValueError('header injection attempt')
        self.to, self.from_addr, self.from_name, self.subject, self.html, self.text = to, from_addr, from_name, subject, html, text
        self.reply_to, self.headers, self.idem_key, self.tags = reply_to, headers or {}, idem_key, tags or {}
    @property
    def from_header(self): return f'{self.from_name} <{self.from_addr}>' if self.from_name else self.from_addr

class Result:
    def __init__(self, ok, *, message_id=None, retryable=False, code='', error='', http_status=None, captured=False):
        self.ok, self.message_id, self.retryable, self.code, self.error, self.http_status, self.captured = ok, message_id, retryable, code, (error or '')[:500], http_status, captured

class DevTransport:
    name, live, enforce_sandbox, supports_idempotency = 'dev', False, False, True
    def send(self, m):
        print(f'[email:dev] captured (NOT delivered) to={m.to} subject={m.subject!r}')
        return Result(True, captured=True)

class MockTransport:
    """Scriptable transport for tests: push Result objects to `plan` to simulate provider outcomes; otherwise every send is accepted."""
    name, live, supports_idempotency = 'mock', True, True
    def __init__(self, enforce_sandbox=False): self.sent, self.plan, self.enforce_sandbox, self._n = [], [], enforce_sandbox, 0
    def send(self, m):
        self.sent.append(m)
        if self.plan: return self.plan.pop(0)
        self._n += 1; return Result(True, message_id=f'mock_{self._n}')

def _http(url, body, headers, timeout):
    req = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as e: return None, str(e).encode()

def _json(b):
    try: return json.loads(b or b'{}')
    except ValueError: return {}

class ResendTransport:
    name, live, enforce_sandbox, supports_idempotency = 'resend', True, True, True
    def __init__(self, api_key, base_url='https://api.resend.com', timeout=15): self.key, self.base, self.timeout = api_key, base_url.rstrip('/'), timeout
    def send(self, m):
        body = {'from': m.from_header, 'to': [m.to], 'subject': m.subject, 'html': m.html, 'text': m.text}
        if m.reply_to: body['reply_to'] = m.reply_to
        if m.headers: body['headers'] = m.headers
        if m.tags: body['tags'] = [{'name': k, 'value': re.sub(r'[^A-Za-z0-9_-]', '_', str(v))[:256]} for k, v in m.tags.items()]
        h = {'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json', 'User-Agent': 'vaulta-mailer/1.0'}
        if m.idem_key: h['Idempotency-Key'] = m.idem_key[:256]
        status, raw = _http(self.base + '/emails', json.dumps(body).encode(), h, self.timeout)
        data = _json(raw)
        if status is None: return Result(False, retryable=True, code='network', error=raw.decode('utf-8', 'replace'))
        if 200 <= status < 300:
            if data.get('id'): return Result(True, message_id=data['id'], http_status=status)
            return Result(False, retryable=True, code='bad_response', error='provider returned success without a message id', http_status=status)
        msg = data.get('message') or data.get('error') or raw[:200].decode('utf-8', 'replace')
        code = data.get('name') or data.get('code') or str(status)
        if status in (429, 500, 502, 503, 504, 409): return Result(False, retryable=True, code=code, error=msg, http_status=status)
        return Result(False, retryable=False, code=code, error=msg, http_status=status)       # 400/401/403/404/422: retrying cannot help

# ---------------------------------------------------------------- AWS SigV4 (stdlib only)
def _hm(k, s): return hmac.new(k, s.encode(), hashlib.sha256).digest()
def sigv4_authorization(method, host, path, query, headers, payload, region, service, access_key, secret_key, amz_date):
    """headers: lower-case name -> value, MUST include host and x-amz-date. Returns (Authorization value, signed header names)."""
    signed = ';'.join(sorted(headers))
    canon_h = ''.join(f'{k}:{" ".join(headers[k].split())}\n' for k in sorted(headers))
    canon = '\n'.join([method, path or '/', query, canon_h, signed, hashlib.sha256(payload).hexdigest()])
    day = amz_date[:8]; scope = f'{day}/{region}/{service}/aws4_request'
    sts = '\n'.join(['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canon.encode()).hexdigest()])
    k = _hm(_hm(_hm(_hm(('AWS4' + secret_key).encode(), day), region), service), 'aws4_request')
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    return f'AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}', signed

class SesTransport:
    name, live, enforce_sandbox, supports_idempotency = 'ses', True, True, False      # SendEmail has no idempotency key: never auto-resend an unknown outcome
    def __init__(self, region, access_key, secret_key, config_set='', endpoint='', timeout=15):
        self.region, self.ak, self.sk, self.cfgset, self.timeout = region, access_key, secret_key, config_set, timeout
        self.endpoint = endpoint.rstrip('/') or f'https://email.{region}.amazonaws.com'
    def send(self, m):
        simple = {'Subject': {'Data': m.subject, 'Charset': 'UTF-8'}, 'Body': {'Html': {'Data': m.html, 'Charset': 'UTF-8'}, 'Text': {'Data': m.text, 'Charset': 'UTF-8'}}}
        if m.headers: simple['Headers'] = [{'Name': k, 'Value': v} for k, v in m.headers.items()]
        body = {'FromEmailAddress': m.from_header, 'Destination': {'ToAddresses': [m.to]}, 'Content': {'Simple': simple}}
        if m.reply_to: body['ReplyToAddresses'] = [m.reply_to]
        if self.cfgset: body['ConfigurationSetName'] = self.cfgset
        payload = json.dumps(body).encode()
        u = urlparse(self.endpoint); path = '/v2/email/outbound-emails'
        amz = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        hdr = {'content-type': 'application/json', 'host': u.netloc, 'x-amz-date': amz}
        auth, _ = sigv4_authorization('POST', u.netloc, quote(path), '', hdr, payload, self.region, 'ses', self.ak, self.sk, amz)
        status, raw = _http(self.endpoint + path, payload, {'Content-Type': 'application/json', 'X-Amz-Date': amz, 'Authorization': auth, 'User-Agent': 'vaulta-mailer/1.0'}, self.timeout)
        data = _json(raw)
        if status is None: return Result(False, retryable=True, code='network', error=raw.decode('utf-8', 'replace'))
        if 200 <= status < 300 and data.get('MessageId'): return Result(True, message_id=data['MessageId'], http_status=status)
        code = str(data.get('__type') or data.get('code') or status).split('#')[-1]; msg = data.get('message') or data.get('Message') or ''
        if status in (429, 500, 502, 503, 504) or code in ('ThrottlingException', 'TooManyRequestsException'): return Result(False, retryable=True, code=code, error=msg, http_status=status)
        return Result(False, retryable=False, code=code, error=msg, http_status=status)

def build_transport(cfg):
    """cfg is a mapping/obj with the EMAIL_* settings. Raises ValueError on an unusable combination (config.validate normally catches it first)."""
    g = (lambda k, d='': cfg.get(k, d)) if isinstance(cfg, dict) else (lambda k, d='': getattr(cfg, k, d))
    p = (g('EMAIL_PROVIDER') or 'dev').lower()
    if p == 'dev': return DevTransport()
    if p == 'mock':
        if g('ENV', 'development') != 'development': raise ValueError('mock email transport is development-only')
        return MockTransport()
    if p == 'smtp':
        from .smtp import SmtpTransport
        return SmtpTransport(g('DATABASE_PATH'))
    if p == 'resend':
        if not g('EMAIL_PROVIDER_KEY'): raise ValueError('EMAIL_PROVIDER_KEY is not set')
        return ResendTransport(g('EMAIL_PROVIDER_KEY'), g('EMAIL_RESEND_BASE_URL', 'https://api.resend.com'))
    if p == 'ses':
        if not (g('AWS_ACCESS_KEY_ID') and g('AWS_SECRET_ACCESS_KEY') and g('EMAIL_SES_REGION')): raise ValueError('SES credentials/region are not set')
        return SesTransport(g('EMAIL_SES_REGION'), g('AWS_ACCESS_KEY_ID'), g('AWS_SECRET_ACCESS_KEY'), g('EMAIL_SES_CONFIGURATION_SET'), g('EMAIL_SES_ENDPOINT'))
    raise ValueError(f'unknown EMAIL_PROVIDER {p!r}')
