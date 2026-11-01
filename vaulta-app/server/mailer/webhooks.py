"""Provider delivery webhooks. Nothing here is trusted until the signature verifies; with no secret configured the endpoint refuses everything (503),
so an unauthenticated caller can never mark a message delivered."""
import hmac, hashlib, base64, json, time

TOLERANCE_SECONDS = 300

class WebhookError(Exception):
    def __init__(self, status, code): super().__init__(code); self.status, self.code = status, code

def verify_svix(secret, headers, body, now=None):
    """Svix scheme used by Resend: signature = base64(HMAC-SHA256(base64decode(secret w/o whsec_ prefix), f"{id}.{timestamp}.{body}")); header holds one or more "v1,<sig>"."""
    if not secret: raise WebhookError(503, 'webhook_secret_not_configured')
    mid, ts, sigs = headers.get('svix-id'), headers.get('svix-timestamp'), headers.get('svix-signature')
    if not (mid and ts and sigs): raise WebhookError(400, 'missing_signature_headers')
    try: tsi = int(ts)
    except ValueError: raise WebhookError(400, 'bad_timestamp')
    if abs((now if now is not None else time.time()) - tsi) > TOLERANCE_SECONDS: raise WebhookError(400, 'timestamp_outside_tolerance')
    raw = secret.split('_', 1)[1] if secret.startswith('whsec_') else secret
    try: key = base64.b64decode(raw)
    except Exception: raise WebhookError(503, 'webhook_secret_malformed')
    expected = base64.b64encode(hmac.new(key, f'{mid}.{ts}.'.encode() + body, hashlib.sha256).digest()).decode()
    for part in sigs.split():
        ver, _, sig = part.partition(',')
        if ver == 'v1' and hmac.compare_digest(sig, expected): return mid
    raise WebhookError(401, 'invalid_signature')

def handle_resend(mailer, db, secret, headers, body, tracking_enabled=False, now=None):
    mid = verify_svix(secret, headers, body, now)
    try: ev = json.loads(body)
    except ValueError: raise WebhookError(400, 'invalid_json')
    etype = ev.get('type') or ''; data = ev.get('data') or {}
    if etype in ('email.opened', 'email.clicked') and not tracking_enabled: return {'result': 'ignored_tracking_disabled'}
    msg_id = data.get('email_id') or data.get('id')
    bounce = data.get('bounce') or {}
    detail = {'bounce_type': bounce.get('type'), 'bounce_message': (bounce.get('message') or '')[:300], 'reason': (data.get('reason') or '')[:300]}
    return {'result': mailer.record_event(db, 'resend', mid, etype, msg_id, ev.get('created_at'), detail)}
