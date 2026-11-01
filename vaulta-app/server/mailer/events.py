"""Domain events -> queued emails.

`emit(db, event, user_id, ref, **kw)` is called by the real workflows (finance, crypto, auth, support, admin) right beside the state transition it describes,
INSIDE the same database transaction. That gives the properties the project needs:

  * atomic: if the business transaction rolls back, no email is queued; if it commits, the email job is committed with it
  * truthful: every value in the email is read back from the database row that now holds the verified state, never from the request
  * idempotent: the queue key is <rule>:<entity ref>[:qualifier], so replays/retries of the same event cannot create a second email
  * non-fatal: emit() never raises into the business flow. A bug or missing data becomes a visible `failed` outbox row with a safe error
"""
import logging, json
from decimal import Decimal
from .. import util
from . import catalog as cat, templates_db as tdb, outbox, prefs, render as rnd

log = logging.getLogger('vaulta.mailer')
_fallback = None
def configure(mailer):
    """Only for code running outside a Flask app (scripts, the worker). Inside a request the current app's mailer is always used."""
    global _fallback; _fallback = mailer
def mailer():
    try:
        from flask import current_app
        return current_app.mailer
    except (RuntimeError, AttributeError): return _fallback

class Skip(Exception):
    """The event does not call for an email in this particular case (not an error)."""

PREF_TYPE = {'deposit': 'deposit', 'withdrawal': 'withdrawal', 'trade': 'trade', 'savings': 'savings', 'retirement': 'retirement', 'investment': 'investment', 'kyc': 'kyc', 'account': 'security'}
BUILDERS = {}
def builder(*events):
    def deco(fn):
        for e in events: BUILDERS[e] = fn
        return fn
    return deco

# ---------------------------------------------------------------- helpers
def fmt_ts(s):
    try: return util.parse_ts(s).strftime('%Y-%m-%d %H:%M UTC')
    except Exception: return s or util.utcnow().strftime('%Y-%m-%d %H:%M UTC')
def mask(s, keep=6): s = str(s or ''); return ('…' + s[-keep:]) if len(s) > keep else s
def usd(cents): return '$' + format(Decimal(int(cents)) / 100, ',.2f')
def label(s): return (s or '').replace('_', ' ').capitalize()
def row(db, sql, *a): return db.execute(sql, a).fetchone()

def _user(db, uid):
    return row(db, 'SELECT u.id,u.public_id,u.email,u.status,u.phone,u.kyc_status,u.email_verified,p.first_name FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.id=?', uid)

def _crypto_asset(db, asset_network_id):
    r = row(db, 'SELECT an.ledger_code,an.decimals,ca.symbol,bn.name network FROM asset_networks an JOIN crypto_assets ca ON ca.id=an.asset_id JOIN blockchain_networks bn ON bn.id=an.network_id WHERE an.id=?', asset_network_id)
    if not r: raise Skip('unknown crypto asset')
    return r
def _minor(v, decimals): return format(Decimal(int(v)).scaleb(-decimals), 'f')

WD_STATUS = {'pending': 'Pending', 'held_for_review': 'Under review', 'approved': 'Approved', 'processing': 'Processing', 'completed': 'Completed', 'rejected': 'Rejected', 'failed': 'Unsuccessful', 'cancelled': 'Cancelled',
             'pending_approval': 'Pending approval', 'signing': 'Processing', 'broadcast': 'Processing', 'confirming': 'Processing'}
DEP_STATUS = {'pending': 'Pending', 'processing': 'Processing', 'completed': 'Completed', 'failed': 'Unsuccessful', 'reversed': 'Reversed'}

def find_deposit(db, ref):
    """Fiat deposit, crypto deposit or crypto deposit request -> normalised dict. The ref is the system-generated reference."""
    r = row(db, 'SELECT * FROM deposits WHERE ref=?', ref)
    if r: return dict(kind='fiat', user_id=r['user_id'], ref=r['ref'], amount=util.cstr(r['amount_cents']), symbol=r['currency'] or 'USD', network='', status=DEP_STATUS.get(r['status'], label(r['status'])), when=r['created_at'], non_live=r['non_live'])
    r = row(db, 'SELECT * FROM crypto_deposits WHERE ref=?', ref)
    if r:
        a = _crypto_asset(db, r['asset_network_id']) if r['asset_network_id'] else None
        if not a or not r['user_id']: raise Skip('crypto deposit is not attributed to a customer/asset')
        return dict(kind='crypto', user_id=r['user_id'], ref=r['ref'], amount=_minor(r['amount'], a['decimals']), symbol=a['symbol'], network=a['network'], status=label(r['status']), when=r['created_at'] if 'created_at' in r.keys() else None, non_live=0)
    r = row(db, 'SELECT * FROM deposit_requests WHERE ref=?', ref)
    if r:
        a = _crypto_asset(db, r['asset_network_id'])
        return dict(kind='request', user_id=r['user_id'], ref=r['ref'], amount=_minor(r['claimed_amount'], a['decimals']), symbol=a['symbol'], network=a['network'], status=label(r['status']), when=r['created_at'], non_live=0)
    raise Skip('deposit not found')

def find_withdrawal(db, ref):
    r = row(db, 'SELECT * FROM withdrawals WHERE ref=?', ref)
    if r: return dict(kind='fiat', id=r['id'], user_id=r['user_id'], ref=r['ref'], amount=util.cstr(r['amount_cents']), symbol=r['currency'] or 'USD', destination=mask(r['destination'], 4), raw_destination=r['destination'],
                      status=WD_STATUS.get(r['status'], label(r['status'])), raw=r['status'], when=r['created_at'], non_live=r['non_live'], network='')
    r = row(db, 'SELECT * FROM crypto_withdrawals WHERE ref=?', ref)
    if r:
        a = _crypto_asset(db, r['asset_network_id'])
        return dict(kind='crypto', id=r['id'], user_id=r['user_id'], ref=r['ref'], amount=_minor(r['amount'], a['decimals']), symbol=a['symbol'], destination=mask(r['destination'], 6), raw_destination=r['destination'],
                    status=WD_STATUS.get(r['status'], label(r['status'])), raw=r['status'], when=r['created_at'] if 'created_at' in r.keys() else None, non_live=0, network=a['network'])
    raise Skip('withdrawal not found')

# ---------------------------------------------------------------- builders: each returns {'ctx':{}, 'secret':{}, 'non_live':0}
def _tx_ctx(d, **extra): return {'transaction_reference': d['ref'], 'transaction_status': d['status'], 'amount': d['amount'], 'asset_symbol': d['symbol'], 'network': d['network'], 'transaction_date': fmt_ts(d['when']), **extra}

@builder('user.registered', 'auth.email_verified', 'security.2fa_enabled', 'security.2fa_disabled', 'security.password_changed', 'account.access_restored', 'account.phone_changed', 'kyc.submitted', 'kyc.approved')
def _simple(db, u, ref, **kw): return {'ctx': {'event_timestamp': fmt_ts(kw.get('at'))}}

@builder('kyc.rejected', 'kyc.info_required')
def _kyc_reason(db, u, ref, **kw):
    if not (kw.get('reason') or '').strip(): raise Skip('no approved reason recorded')
    return {'ctx': {'reason': kw['reason'], 'event_timestamp': fmt_ts(kw.get('at'))}}

@builder('auth.verification_requested', 'reminder.verify_email', 'reminder.verify_email_final', 'security.password_reset_requested')
def _token_mail(db, u, ref, **kw):
    if not kw.get('token'): raise Skip('no token')
    path = '/reset-password/?token=' if kw.get('purpose') == 'reset' else '/verify-email/?token='
    return {'ctx': {'expires_in': kw.get('expires_in', '48 hours')}, 'secret': {'action_url': mailer().base_url + path + kw['token']}}

@builder('reminder.kyc_incomplete')
def _kyc_rem(db, u, ref, **kw): return {'ctx': {}}

@builder('reminder.kyc_info_outstanding')
def _kyc_info_rem(db, u, ref, **kw): return {'ctx': {'reason': kw.get('reason', '')}}

@builder('reminder.reengagement', 'reminder.retirement_review')
def _plain(db, u, ref, **kw): return {'ctx': {}}

@builder('deposit.requested', 'deposit.pending', 'deposit.detected', 'deposit.confirmed', 'deposit.failed', 'deposit.review', 'deposit.reversed')
def _deposit(db, u, ref, **kw):
    d = find_deposit(db, ref)
    if d['user_id'] != u['id']: raise Skip('deposit belongs to another customer')
    ev = kw['_event']
    status = {'deposit.confirmed': 'Credited', 'deposit.detected': 'Awaiting confirmations', 'deposit.review': 'Under review', 'deposit.pending': 'Pending'}.get(ev, d['status'])
    return {'ctx': _tx_ctx(d, transaction_status=status, reason=kw.get('reason', '')), 'non_live': d['non_live']}

@builder('withdrawal.requested', 'withdrawal.review', 'withdrawal.approved', 'withdrawal.processing', 'withdrawal.completed', 'withdrawal.failed', 'withdrawal.rejected', 'withdrawal.cancelled')
def _withdrawal(db, u, ref, **kw):
    w = find_withdrawal(db, ref)
    if w['user_id'] != u['id']: raise Skip('withdrawal belongs to another customer')
    return {'ctx': _tx_ctx(w, destination=w['destination'], reason=kw.get('reason', '')), 'non_live': w['non_live']}

@builder('security.withdrawal_alert')
def _wd_alert(db, u, ref, **kw):
    w = find_withdrawal(db, ref)
    if w['user_id'] != u['id']: raise Skip('withdrawal belongs to another customer')
    held = w['raw'] in ('held_for_review',)
    prior = row(db, "SELECT 1 FROM withdrawals WHERE user_id=? AND destination=? AND id<>? AND status='completed'", u['id'], w['raw_destination'], w['id'] if w['kind'] == 'fiat' else -1) \
        or row(db, "SELECT 1 FROM crypto_withdrawals WHERE user_id=? AND destination=? AND id<>? AND status='completed'", u['id'], w['raw_destination'], w['id'] if w['kind'] == 'crypto' else -1)
    if prior and not held: raise Skip('destination already used and not held for review')
    return {'ctx': _tx_ctx(w, destination=w['destination']), 'non_live': w['non_live']}

@builder('investment.received', 'investment.confirmed')
def _investment(db, u, ref, **kw):
    r = row(db, 'SELECT i.*,p.name pname FROM investments i JOIN investment_products p ON p.id=i.product_id WHERE i.ref=?', ref)
    if not r or r['user_id'] != u['id']: raise Skip('investment not found')
    if kw['_event'] == 'investment.confirmed' and r['status'] != 'completed': raise Skip('investment is not in the completed state')
    if kw['_event'] == 'investment.received' and r['status'] not in ('pending', 'processing'): raise Skip('investment is not awaiting confirmation')
    return {'ctx': {'investment_reference': r['ref'], 'investment_name': r['pname'], 'investment_amount': usd(r['amount_cents']), 'transaction_date': fmt_ts(r['completed_at'] or r['created_at'])}, 'non_live': r['non_live']}

@builder('trade.order_placed', 'trade.order_filled', 'trade.order_cancelled', 'trade.order_rejected')
def _order(db, u, ref, **kw):
    o = row(db, 'SELECT o.*,a.symbol FROM orders o JOIN assets a ON a.id=o.asset_id WHERE o.ref=?', ref)
    if not o or o['user_id'] != u['id']: raise Skip('order not found')
    ev, want = kw['_event'], {'trade.order_placed': ('open', 'pending'), 'trade.order_filled': ('filled',), 'trade.order_cancelled': ('cancelled',), 'trade.order_rejected': ('rejected', 'failed')}[kw['_event']]
    if o['status'] not in want: raise Skip(f"order is '{o['status']}', not {'/'.join(want)}")          # an email may only describe the state the order is really in
    ctx = {'order_reference': o['ref'], 'trade_side': label(o['side']), 'order_type': label(o['order_type']).replace('_', ' '), 'asset_symbol': o['symbol'], 'order_quantity': o['quantity'],
           'limit_price': ('$' + o['limit_price']) if o['limit_price'] else '', 'reason': (kw.get('reason') or o['reason'] or '')}
    if ev == 'trade.order_filled':
        ctx.update(executed_quantity=o['filled_qty'], execution_price=('$' + o['execution_price']) if o['execution_price'] else '', trading_fee=usd(o['fee_cents']), transaction_date=fmt_ts(o['executed_at']))
        if not o['execution_price']: raise Skip('filled order has no recorded execution price')
    return {'ctx': ctx, 'non_live': o['non_live']}

@builder('savings.goal_created', 'savings.goal_achieved', 'savings.milestone', 'savings.contribution_recorded', 'savings.contribution_failed')
def _savings(db, u, ref, **kw):
    gid = kw.get('goal_id')
    g = row(db, 'SELECT * FROM savings_goals WHERE id=? AND user_id=?', gid, u['id']) if gid else None
    ev = kw['_event']
    if ev != 'savings.contribution_recorded' and not g: raise Skip('goal not found')
    ctx = {'transaction_date': fmt_ts(kw.get('at'))}
    if g:
        pct = int(min(100, (g['current_cents'] * 100) // g['target_cents'])) if g['target_cents'] else 0
        ctx.update(goal_name=g['name'], target_amount=usd(g['target_cents']), current_amount=usd(g['current_cents']), progress_percentage=f'{pct}%')
        if ev == 'savings.goal_achieved' and g['current_cents'] < g['target_cents']: raise Skip('goal target not reached')
    if ev == 'savings.contribution_recorded': ctx['contribution_amount'] = usd(kw['amount_cents'])
    if ev == 'savings.contribution_failed': ctx['reason'] = kw.get('reason', '')
    return {'ctx': ctx}

@builder('retirement.updated')
def _retirement(db, u, ref, **kw):
    r = row(db, 'SELECT * FROM retirement_plans WHERE user_id=?', u['id'])
    if not r: raise Skip('no plan')
    return {'ctx': {'retirement_age': str(r['retirement_age']), 'contribution_amount': usd(r['monthly_contribution_cents'] or 0), 'target_amount': usd(r['target_cents']) if r['target_cents'] else ''}}

@builder('security.new_login', 'security.new_device', 'security.suspicious_activity')
def _login(db, u, ref, **kw):
    return {'ctx': {'event_timestamp': fmt_ts(kw.get('at')), 'ip_address': kw.get('ip') or '', 'device': (kw.get('user_agent') or 'Unknown browser')[:120]}}

@builder('security.wallet_changed')
def _wallet(db, u, ref, **kw):
    r = row(db, 'SELECT s.*,n.name nname FROM customer_source_addresses s JOIN blockchain_networks n ON n.id=s.network_id WHERE s.id=? AND s.user_id=?', kw.get('address_id'), u['id'])
    if not r: raise Skip('address not found')
    return {'ctx': {'wallet_action': kw['action'], 'network': r['nname'], 'wallet_address': mask(r['address'], 6), 'event_timestamp': fmt_ts(kw.get('at'))}}

@builder('security.account_restricted')
def _restricted(db, u, ref, **kw):
    if u['status'] not in ('restricted', 'suspended'): raise Skip('account is not restricted')
    return {'ctx': {'account_status': label(u['status']), 'event_timestamp': fmt_ts(kw.get('at'))}}

@builder('account.profile_updated')
def _profile(db, u, ref, **kw):
    if not kw.get('changed'): raise Skip('nothing changed')
    return {'ctx': {'changed_fields': ', '.join(kw['changed']), 'event_timestamp': fmt_ts(kw.get('at'))}}

@builder('support.ticket_created', 'support.reply', 'support.info_required', 'support.resolved', 'support.closed')
def _support(db, u, ref, **kw):
    t = row(db, 'SELECT * FROM support_tickets WHERE ref=? AND user_id=?', ref, u['id'])
    if not t: raise Skip('ticket not found')
    return {'ctx': {'ticket_reference': t['ref'], 'ticket_subject': t['subject'], 'event_timestamp': fmt_ts(t['created_at'] if kw['_event'] == 'support.ticket_created' else t['updated_at'])}}

# ---------------------------------------------------------------- the engine
def emit(db, event, user_id, ref, *, qualifier='', **kw):
    """Queue the email for `event`. Returns the outbox id, or None when nothing was queued. Never raises."""
    if mailer() is None:
        log.error('email event DROPPED (no mailer: call events.configure(mailer) when running outside a Flask app): event=%s ref=%s', event, ref)
        return None
    try: return _emit(db, event, user_id, str(ref), qualifier, kw)
    except Exception as e:                                       # noqa: a bug here must never break a deposit, a login or a trade
        log.exception('email emit failed event=%s ref=%s (%s)', event, ref, type(e).__name__)
        return None

def _emit(db, event, user_id, ref, qualifier, kw):
    rule = row(db, 'SELECT * FROM email_automation_rules WHERE event_type=?', event)
    if not rule or not rule['enabled']: return None
    got = tdb.get(db, rule['template_id'])
    if not got or got[0]['status'] != 'active': return None
    t, v, spec, optional = got
    u = _user(db, user_id)
    if not u or not u['email']: return None
    ptype = PREF_TYPE.get(event.split('.')[1 if event.startswith('reminder.') else 0])
    if t['pref_gated'] and ptype and not prefs.notification_pref_on(db, user_id, ptype): return None       # optional notices are opt-in
    idem = f"{rule['code']}:{ref}" + (f':{qualifier}' if qualifier else '')
    try:
        b = BUILDERS[event](db, u, ref, _event=event, **kw)
    except Skip as s:
        log.info('email skipped event=%s ref=%s: %s', event, ref, s); return None
    ctx = {'first_name': u['first_name'] or 'there', 'email': u['email'], 'customer_id': u['public_id'], 'event_timestamp': fmt_ts(util.now()), **b.get('ctx', {})}
    secret = b.get('secret') or {}
    have = {k for k, x in {**ctx, **secret}.items() if x not in (None, '')} | set(rnd.build_links(mailer().base_url))
    missing = [x for x in cat.required_vars(v['subject'], spec, optional) if x not in have]
    run_at = util.ts_after(minutes=rule['delay_minutes']) if rule['delay_minutes'] and rule['kind'] == 'event' else None
    common = dict(user_id=user_id, to_email=u['email'], template_id=t['id'], version=v['version'], subject=v['subject'][:200], event_type=event, entity_ref=ref, idem=idem, mail_class=t['mail_class'],
                  non_live=b.get('non_live', 0))
    if missing:
        log.warning('email not queued: event=%s ref=%s missing=%s', event, ref, ','.join(missing))
        eid, _ = outbox.enqueue(db, **common, ctx={}, status='failed', error='missing required values: ' + ', '.join(missing))
        return eid
    eid, created = outbox.enqueue(db, **common, ctx=ctx, secret=secret, run_at=run_at, max_attempts=int(_setting(db, 'email_max_attempts', 6)))
    return eid if created else None

def recent(db, user_id, event, minutes):
    """How many emails for this event were queued for the user in the last N minutes (abuse / cooldown checks)."""
    return db.execute('SELECT COUNT(*) FROM email_outbox WHERE user_id=? AND event_type=? AND created_at>=?', (user_id, event, util.ts_after(minutes=-minutes))).fetchone()[0]

def _setting(db, key, default):
    r = db.execute('SELECT value FROM system_settings WHERE key=?', (key,)).fetchone()
    try: return int(r[0]) if r else default
    except ValueError: return default
