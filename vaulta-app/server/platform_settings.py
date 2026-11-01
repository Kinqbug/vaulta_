"""Typed platform settings behind System Settings (admin API). One source of truth: the existing `system_settings` table.

Text values live under `platform.<name>`; numeric values and the four switches keep their existing keys (see rbac.SETTINGS), so the
legacy settings API, the audit log and every reader (rbac.setting) keep working. Nothing here is stored in the browser.
Each entry says what actually consumes it ('used_by'); controls with no real backing are listed in `unavailable`, never faked.
"""
import os, re, time, zoneinfo
from decimal import Decimal, InvalidOperation
from flask import Blueprint, current_app, g, request
from . import security as sec, rbac, util, platform_gate
from .util import ApiError, text
from .db import get_db, one, tx
from .audit import audit

bp = Blueprint('platform_admin', __name__, url_prefix='/api/admin/platform')
STARTED = time.time()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_NAME = re.compile(r'^[^<>\x00-\x1f]{1,60}$')
_ADDR = re.compile(r'^[^@\s<>]{1,64}@[^@\s<>]+\.[^@\s<>]{2,}$')
_TZ = None
def timezones():
    global _TZ
    if _TZ is None: _TZ = sorted(zoneinfo.available_timezones() | {'UTC'})
    return _TZ
def languages():
    from .routes_settings import LANGUAGES
    return LANGUAGES

# ---- validators: return the string to store, or raise ValueError(message) ----
def v_name(v):
    v = str(v if v is not None else '').strip()
    if not _NAME.match(v): raise ValueError('Enter a platform name of 1 to 60 characters without < or >.')
    return v
def v_email(v):
    v = str(v if v is not None else '').strip().lower()
    if not v or len(v) > 254 or not _ADDR.match(v): raise ValueError('Enter a valid email address.')
    return v
def v_lang(v):
    if v not in {o['value'] for o in languages()}: raise ValueError('That language is not supported yet.')
    return v
def v_tz(v):
    if v not in timezones(): raise ValueError('Choose a time zone from the list.')
    return v
def v_flag(v):
    if v in (True, 1, '1', 'true'): return '1'
    if v in (False, 0, '0', 'false'): return '0'
    raise ValueError('Must be on or off.')
def v_rate(v):
    try: n = int(str(v).strip())
    except ValueError: raise ValueError('Enter a whole number.')
    if not 0 <= n <= 1_000_000: raise ValueError('Enter a number from 0 (no limit) to 1,000,000.')
    return str(n)
def v_money(v):
    if not re.fullmatch(r'\d{1,9}(\.\d{1,2})?', str(v).strip()): raise ValueError('Enter an amount such as 10.00.')
    try: d = Decimal(str(v).strip())
    except InvalidOperation: raise ValueError('Enter an amount such as 10.00.')
    if not d.is_finite() or d < 0 or d != d.quantize(Decimal('0.01')) or d > Decimal('100000000'): raise ValueError('Enter an amount of 0 or more with at most 2 decimal places.')
    return str(int(d * 100))
def v_pct(v):
    if not re.fullmatch(r'\d{1,2}(\.\d{1,2})?', str(v).strip()): raise ValueError('Enter a percentage such as 0.10.')
    try: d = Decimal(str(v).strip())
    except InvalidOperation: raise ValueError('Enter a percentage such as 0.10.')
    if not d.is_finite() or d < 0 or d > 10 or d != d.quantize(Decimal('0.01')): raise ValueError('Enter a percentage from 0 to 10 with at most 2 decimal places.')
    return str(int(d * 100))

def v_int(lo, hi, what):
    def f(v):
        if not re.fullmatch(r'\d{1,7}', str(v).strip()): raise ValueError('Enter a whole number.')
        n = int(str(v).strip())
        if not lo <= n <= hi: raise ValueError(f'{what}: enter a number from {lo} to {hi}.')
        return str(n)
    return f
def v_pct_max(mx):
    def f(v):
        r = v_pct(v)
        if int(r) > mx * 100: raise ValueError(f'Enter a percentage from 0 to {mx}.')
        return r
    return f

# api name -> dict(store key, validator, group, label, used_by, unit, min, max). Order is the order the UI shows. 'used_by' names the code that really reads it.
def E(store, v, grp, label, used, unit=None, **m): return dict(store=store, v=v, group=grp, label=label, used=used, unit=unit, **m)
REG = {
 'name': E('platform.name', v_name, 'general', 'Platform name', 'Returned by the public status endpoint for page titles and footers. Email sender names still come from EMAIL_FROM_NAME.'),
 'support_email': E('platform.support_email', v_email, 'general', 'Support email', 'Returned by the public status endpoint as the customer contact address.'),
 'language': E('platform.language', v_lang, 'general', 'Default language', 'Default for new visitors. Only languages with translations are offered.'),
 'timezone': E('platform.timezone', v_tz, 'general', 'Time zone', 'Reference zone shown in the admin console. Stored timestamps stay in UTC.'),
 'maintenance_mode': E('maintenance_mode', v_flag, 'switches', 'Maintenance mode', 'The whole API answers 503 except to staff; money movement is blocked.'),
 'live_prices_enabled': E('live_prices_enabled', v_flag, 'switches', 'Show live prices', 'When off, the market-data provider is never called; cached data is served marked stale.'),
 'registration_enabled': E('registration_enabled', v_flag, 'switches', 'Enable registration', 'When off, the registration endpoint refuses new sign-ups (403).'),
 'require_2fa_all': E('require_2fa_all', v_flag, 'switches', 'Require two-factor authentication', 'Accounts without 2FA can only enrol until they enable it.'),
 'api_rate_limit_per_hour': E('api_rate_limit_per_hour', v_int(0, 1_000_000, 'API rate limit'), 'limits', 'API rate limit (requests per hour, 0 = no limit)', 'Per signed-in customer (or IP when signed out); staff are exempt. Counted per server process.', 'requests/hour', min=0, max=1_000_000),
 'min_deposit': E('min_deposit_cents', v_money, 'financial', 'Minimum deposit', 'Legacy bank/card deposits only (needs fiat funding). Crypto minimums are set per asset network in Assets & Markets.', 'USD'),
 'trade_fee_percent': E('trade_fee_bps', v_pct, 'financial', 'Trading fee', 'Charged on every executed order.', '%'),
 # ---- Security Settings
 'twofa_required_above': E('twofa_required_above_cents', v_money, 'security', 'Investments needing a 2FA code', 'Investments at or above this amount need a 2FA code from customers who have 2FA on.', 'USD'),
 'withdrawal_review_threshold': E('withdrawal_review_threshold_cents', v_money, 'security', 'Withdrawal compliance-review threshold', 'Withdrawals at or above this amount are held for compliance review.', 'USD'),
 'risk_ack_required': E('risk_ack_required', v_flag, 'security', 'Require risk disclosure acknowledgement', 'Customers must accept the Risk Disclosure before investing or trading.'),
 'crypto_requires_kyc': E('crypto_requires_kyc', v_flag, 'security', 'Require verified KYC for crypto', 'Verified identity is required before crypto deposits are credited and for crypto withdrawals.'),
 # ---- Payment Settings
 'fiat_funding_enabled': E('fiat_funding_enabled', v_flag, 'payments', 'Legacy bank/card funding', 'Funding is crypto-only by default. On re-enables legacy bank/card deposits and withdrawals.'),
 'max_deposit': E('max_deposit_cents', v_money, 'payments', 'Maximum single deposit', 'Upper limit for one legacy deposit.', 'USD'),
 'daily_withdrawal_limit': E('daily_withdrawal_limit_cents', v_money, 'payments', 'Daily withdrawal limit per customer', 'Total a customer may withdraw per day.', 'USD'),
 'card_fee_percent': E('card_fee_bps', v_pct_max(10), 'payments', 'Card deposit fee', 'Charged on legacy card deposits.', '%'),
 'bank_fee_percent': E('bank_fee_bps', v_pct_max(10), 'payments', 'Bank transfer deposit fee', 'Charged on legacy bank deposits.', '%'),
 'savings_apy_percent': E('savings_apy_bps', v_pct_max(25), 'payments', 'Default savings APY', 'Variable rate applied by the savings interest job; not guaranteed.', '%'),
 'crypto_screenshot_required': E('crypto_screenshot_required', v_flag, 'payments', 'Require screenshot on crypto deposits', 'Every crypto deposit request must include a transaction screenshot.'),
 'crypto_spread_percent': E('crypto_conversion_spread_bps', v_pct_max(10), 'payments', 'Crypto conversion spread', 'Applied when crypto is converted to or from Account Balance.', '%'),
 'crypto_price_max_age_minutes': E('crypto_price_max_age_minutes', v_int(0, 10080, 'Price age'), 'payments', 'Maximum price age for conversions (0 = no limit)', 'Conversions are refused when the asset price is older than this.', 'minutes', min=0, max=10080),
 'crypto_max_pending_requests': E('crypto_max_pending_requests', v_int(1, 100, 'Pending requests'), 'payments', 'Maximum pending crypto deposit requests per customer', 'Customers cannot open more requests than this while others await review.', 'requests', min=1, max=100),
 'crypto_intent_ttl_hours': E('crypto_intent_ttl_hours', v_int(1, 720, 'Intent lifetime'), 'payments', 'Crypto deposit intent lifetime', 'How long a deposit intent stays open.', 'hours', min=1, max=720),
 'crypto_intent_grace_hours': E('crypto_intent_grace_hours', v_int(0, 720, 'Grace window'), 'payments', 'Late-arrival grace window', 'After an intent expires, late arrivals are still accepted for manual review for this long.', 'hours', min=0, max=720),
}
BPS = {'trade_fee_percent', 'card_fee_percent', 'bank_fee_percent', 'crypto_spread_percent', 'savings_apy_percent'}; CENTS = {'min_deposit', 'max_deposit', 'twofa_required_above', 'withdrawal_review_threshold', 'daily_withdrawal_limit'}
GROUPS = ('general', 'switches', 'limits', 'financial', 'security', 'payments')

def _raw(db, store, default=''):
    r = one(db, 'SELECT value FROM system_settings WHERE key=?', store)
    return r['value'] if r else default
DEFAULTS = {'name': 'VAULTA', 'support_email': '', 'language': 'en', 'timezone': 'UTC'}
def kind(name):
    v = REG[name]['v']
    return 'flag' if v is v_flag else 'money' if name in CENTS else 'percent' if name in BPS else 'text' if name in DEFAULTS else 'int'
def display(db, name):
    e = REG[name]; raw = _raw(db, e['store'], DEFAULTS.get(name, rbac.SETTINGS.get(e['store'], ('',))[0])); k = kind(name)
    if k == 'money': return util.cstr(int(raw))
    if k == 'percent': return str((Decimal(int(raw)) / 100).quantize(Decimal('0.01')))
    if k == 'flag': return bool(int(raw))
    if k == 'int': return int(raw)
    return raw

def get_all(db):
    out = {g_: {} for g_ in GROUPS}
    for name, e in REG.items():
        r = one(db, 'SELECT updated_at FROM system_settings WHERE key=?', e['store'])
        f = {'value': display(db, name), 'kind': kind(name), 'label': e['label'], 'used_by': e['used'], 'updated_at': r['updated_at'] if r else None}
        if e['unit']: f['unit'] = e['unit']
        for m in ('min', 'max'):
            if m in e: f[m] = e[m]
        out[e['group']][name] = f
    out['general']['site_url'] = {'value': current_app.config.get('PUBLIC_BASE_URL') or None, 'kind': 'text', 'label': 'Site URL', 'editable': False,
                                  'used_by': 'Set with the PUBLIC_BASE_URL environment variable: it is the only source of links in emails, so it cannot be edited here.'}
    out['options'] = {'language': languages(), 'timezone': timezones()}
    return out

def apply(db, actor_id, changes, reason):
    if not isinstance(changes, dict) or not changes: raise ApiError(422, 'nothing_to_update', 'No changes supplied.')
    unknown = [k for k in changes if k not in REG]
    if unknown: raise ApiError(422, 'unknown_setting', 'Unknown or read-only setting: ' + ', '.join(sorted(unknown)), {'fields': {k: 'Unknown or read-only setting.' for k in unknown}})
    new, errs = {}, {}
    for k, v in changes.items():
        try: new[k] = REG[k]['v'](v)
        except ValueError as e: errs[k] = str(e)
    cur = lambda k: int(new[k]) if k in new else int(_raw(db, REG[k]['store'], rbac.SETTINGS[REG[k]['store']][0]))
    if not errs and ('min_deposit' in new or 'max_deposit' in new):
        if cur('min_deposit') > cur('max_deposit'): errs['max_deposit' if 'max_deposit' in new else 'min_deposit'] = 'The minimum deposit cannot exceed the maximum deposit (%s).' % util.cstr(cur('max_deposit'))
    if errs: raise ApiError(422, 'validation_failed', 'Some values are not valid.', {'fields': errs})
    if new.get('require_2fa_all') == '1' and not g.user['totp_enabled']: raise ApiError(409, 'enable_own_2fa_first', 'Turn on two-factor authentication for your own account before requiring it for everyone.')
    changed = []
    with tx(db):
        for k, v in new.items():
            e = REG[k]; store = e['store']; old = one(db, 'SELECT value FROM system_settings WHERE key=?', store)
            c0 = old['value'] if old else DEFAULTS.get(k, rbac.SETTINGS.get(store, ('',))[0])
            if c0 == v: continue
            db.execute('INSERT INTO system_settings(key,value,description,updated_by,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_by=excluded.updated_by,updated_at=excluded.updated_at',
                       (store, v, e['label'], actor_id, util.now()))
            audit(db, actor_id, 'setting.changed', 'setting', store, {'value': old['value'] if old else None}, {'value': v}, reason); changed.append(k)
    return changed

def system_info():
    c = current_app.config
    ver = os.getenv('VAULTA_VERSION', '').strip()
    if not ver:
        try: ver = open(os.path.join(ROOT, 'VERSION'), encoding='utf-8').read().strip()
        except OSError: ver = ''
    bt = os.getenv('VAULTA_BUILD_TIME', '').strip(); src = 'environment'
    if not bt:
        d = os.path.join(ROOT, 'server'); m = max((os.path.getmtime(os.path.join(d, f)) for f in os.listdir(d) if f.endswith('.py')), default=None); src = 'code_modified'
        bt = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(m)) if m else None
    return {'version': ver or None, 'version_note': None if ver else 'No version is set. Set VAULTA_VERSION (or add a VERSION file) when deploying.',
            'last_updated': bt, 'last_updated_source': src, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(STARTED)),
            'uptime_seconds': int(time.time() - STARTED), 'uptime_note': 'Time since this server process started. Availability percentages are not tracked.',
            'environment': c.get('ENV', 'development'), 'payment_mode': current_app.providers.mode, 'crypto_mode': current_app.providers.crypto_mode}

def policy():
    """Read-only facts the admin console shows on the Security tab. They come from server configuration (environment variables) and code, never from this API."""
    c = current_app.config
    return [{'key': 'session_idle_minutes', 'label': 'Session idle timeout', 'value': c['SESSION_IDLE_MINUTES'], 'unit': 'minutes', 'source': 'SESSION_IDLE_MINUTES'},
            {'key': 'session_absolute_hours', 'label': 'Maximum session length', 'value': c['SESSION_ABSOLUTE_HOURS'], 'unit': 'hours', 'source': 'SESSION_ABSOLUTE_HOURS'},
            {'key': 'max_failed_logins', 'label': 'Failed sign-ins before lockout', 'value': c['MAX_FAILED_LOGINS'], 'unit': 'attempts', 'source': 'MAX_FAILED_LOGINS'},
            {'key': 'lockout_minutes', 'label': 'Lockout duration', 'value': c['LOCKOUT_MINUTES'], 'unit': 'minutes', 'source': 'LOCKOUT_MINUTES'},
            {'key': 'password_min_length', 'label': 'Minimum password length', 'value': sec.MIN_PASSWORD, 'unit': 'characters', 'source': 'Fixed in the server code'}]

def integrations(db):
    from . import marketdata
    c, p = current_app.config, current_app.providers
    md = marketdata.status(); em = current_app.mailer.status()
    def row(k, label, state, detail): return {'key': k, 'label': label, 'state': state, 'detail': detail}
    return [row('payments', 'Payment provider', 'configured' if p.mode == 'live' and c.get('PAYMENT_PROVIDER_KEY') else 'not_configured', 'Internal ledger only: no external payment provider is connected.' if p.mode != 'live' else 'Live provider mode.'),
            row('crypto', 'Crypto payments', 'configured', f"Provider mode: {p.crypto_mode}. Managed in Assets & Markets and Crypto Operations."),
            row('market_data', 'Market data (CoinGecko)', 'configured' if md['api_key_configured'] else 'not_configured', md['note'] or 'API key set.' + (f" Last error: {md['last_error_kind']}." if md['last_error_kind'] else '')),
            row('email', 'Email delivery', 'configured' if em['live_delivery'] else 'not_configured', em['warning'] or f"Provider: {em['provider']}."),
            row('kyc', 'Identity verification provider', 'configured' if c.get('KYC_PROVIDER_KEY') else 'not_configured', 'Set KYC_PROVIDER_KEY on the server.' if not c.get('KYC_PROVIDER_KEY') else 'Key set.'),
            row('chat', 'Community chat', 'configured', 'Built-in Trade community chat (moderated under Communications).'),
            row('analytics', 'Analytics provider', 'unavailable', 'No analytics integration exists in this project.'),
            row('google_analytics', 'Google Analytics', 'unavailable', 'No Google Analytics integration exists in this project.'),
            row('livechat', 'LiveChat', 'unavailable', 'No LiveChat integration exists in this project.')]

UNAVAILABLE = [
 {'key': 'admin_email', 'label': 'Admin email', 'reason': 'No feature sends mail to an admin address yet, so a stored address would do nothing.'},
 {'key': 'notification_email', 'label': 'Notification email', 'reason': 'Customer mail uses the sender set by EMAIL_FROM_ADDRESS; there is no separate notification address.'},
 {'key': 'email_alerts', 'label': 'Enable email alerts', 'reason': 'There is no admin alert system to switch on.'},
 {'key': 'daily_reports', 'label': 'Send daily reports', 'reason': 'There is no scheduler or daily report job.'},
 {'key': 'min_withdrawal', 'label': 'Minimum withdrawal', 'reason': 'Withdrawal minimums are set per asset network (Assets & Markets > Configure).'},
 {'key': 'withdrawal_processing_time', 'label': 'Withdrawal processing time', 'reason': 'Withdrawals are approved and processed manually; no processing-time rule exists.'}]

P = sec.require_perm('settings.manage')
@bp.get('/settings')
@P
def settings_get():
    db = get_db(); r = get_all(db); r.update({'policy': policy(), 'integrations': integrations(db), 'email': current_app.mailer.status(), 'unavailable': UNAVAILABLE, 'manage_templates': '#/email'}); return r
@bp.put('/settings')
@P
def settings_put():
    from .routes_auth import body
    b, db = body(), get_db(); reason = text(b.get('reason'), 'reason', 500)
    if len(reason) < 5: raise ApiError(422, 'validation_failed', 'Please give a reason.', {'fields': {'reason': 'Please give a reason (at least 5 characters).'}})
    changed = apply(db, g.user['id'], b.get('changes'), reason)
    return {'ok': True, 'changed': changed, **get_all(db)}
@bp.get('/system-info')
@P
def info_get(): return system_info()
