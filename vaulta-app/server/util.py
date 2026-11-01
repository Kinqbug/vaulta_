import re, secrets, json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime, timezone, timedelta

class ApiError(Exception):
    def __init__(self, status, code, message, details=None):
        super().__init__(message); self.status, self.code, self.message, self.details = status, code, message, details

# ---- clock (overridable so seed/tests can backdate through the real services) ----
_clock = None
def set_clock(fn): 
    global _clock; _clock = fn
def utcnow():
    return _clock() if _clock else datetime.now(timezone.utc)
def now():
    return utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
def ts_after(minutes=0, hours=0, days=0, seconds=0):
    return (utcnow() + timedelta(minutes=minutes, hours=hours, days=days, seconds=seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')
def parse_ts(s):
    return datetime.strptime(s[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)

def new_ref(prefix): return f'{prefix}-{secrets.token_hex(5).upper()}'

# ---- money: integer minor units (cents) are authoritative. Floats are never accepted. ----
CENT = Decimal('0.01')
def dec(v, field='value'):
    if isinstance(v, float): raise ApiError(422, 'invalid_number', f'{field}: send numbers as strings, e.g. "10.50"')
    try:
        d = Decimal(str(v).strip())
    except (InvalidOperation, ValueError):
        raise ApiError(422, 'invalid_number', f'{field} is not a valid number')
    if not d.is_finite(): raise ApiError(422, 'invalid_number', f'{field} is not a valid number')
    return d
def parse_amount(v, field='amount', maximum_cents=10**11):
    d = dec(v, field)
    if d != d.quantize(CENT): raise ApiError(422, 'invalid_amount', f'{field} can have at most 2 decimal places')
    c = int(d * 100)
    if c <= 0: raise ApiError(422, 'invalid_amount', f'{field} must be greater than zero')
    if c > maximum_cents: raise ApiError(422, 'invalid_amount', f'{field} is too large')
    return c
def parse_qty(v, field='quantity'):
    d = dec(v, field)
    if d <= 0: raise ApiError(422, 'invalid_quantity', f'{field} must be greater than zero')
    if d != d.quantize(Decimal('0.00000001')): raise ApiError(422, 'invalid_quantity', f'{field} has too many decimals')
    if d > Decimal('1e9'): raise ApiError(422, 'invalid_quantity', f'{field} is too large')
    return d
def cstr(c): return str((Decimal(int(c)) / 100).quantize(CENT))
def to_cents(d): return int((Decimal(d) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
def qstr(d): return format(Decimal(d).quantize(Decimal('0.00000001')), 'f')

EMAIL_RE = re.compile(r'^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$')
def need(body, *keys):
    if not isinstance(body, dict): raise ApiError(400, 'bad_request', 'JSON object expected')
    miss = [k for k in keys if body.get(k) in (None, '')]
    if miss: raise ApiError(422, 'missing_fields', 'Missing: ' + ', '.join(miss), {'fields': miss})
def text(v, field, maxlen=200, required=True):
    v = ('' if v is None else str(v)).strip()
    if required and not v: raise ApiError(422, 'missing_fields', f'{field} is required')
    if len(v) > maxlen: raise ApiError(422, 'too_long', f'{field} is too long (max {maxlen})')
    return v
def jdump(o): return json.dumps(o, separators=(',', ':'), default=str, sort_keys=True)
def rows(cur): return [dict(r) for r in cur.fetchall()]
def page_args(args, default=25, maximum=100):
    try: p = max(1, int(args.get('page', 1))); n = min(maximum, max(1, int(args.get('size', default))))
    except ValueError: raise ApiError(422, 'bad_paging', 'page and size must be integers')
    return p, n
