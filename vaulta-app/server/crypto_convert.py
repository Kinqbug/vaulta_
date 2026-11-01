"""Crypto <-> Account Balance (USD cash) conversion.

Funding a Vaulta account with crypto lands in the customer's Account Balance (the USD cash account), not in a crypto holding:

  deposit     the on-chain deposit is verified and credited exactly as before (customer crypto liability), then IMMEDIATELY converted:
                crypto entry   Dr customer crypto liability   Cr CRYPTO_CONVERSION          (units of the ledger asset)
                cash entry     Dr CRYPTO_CONVERSION           Cr customer cash liability    (USD cents, via the customer subledger)
  withdrawal  the customer asks for N units; USD is debited and converted FIRST, then the normal reserve entry runs:
                cash entry     Dr customer cash liability     Cr CRYPTO_CONVERSION
                crypto entry   Dr CRYPTO_CONVERSION           Cr customer crypto liability
  reversal    a cancelled / rejected / failed withdrawal reverses the reserve and then BOTH conversion entries, so the customer gets back
              exactly the dollars that were debited (the rate is locked when the request is made).

Every conversion is one row in crypto_conversions (rate, spread, units, dollars, both ledger references). Prices come from the same asset table as the rest
of the app; if an asset has no usable price nothing is converted and the deposit waits for review rather than guessing. The optional spread is an
administrator setting (default 0 = market price). Rounding always favours the house by at most one cent (floor on credits, ceiling on debits).
Customer crypto liabilities are therefore transient: they exist for the length of one database transaction, and legacy balances are converted once."""
import json
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from . import util, accounting as acc, ledger, crypto_ledger as cl, assets as reg
from .db import one, all_, tx
from .rbac import setting
from .util import ApiError, cstr

MAX_SPREAD_BPS = 2000

def spread_bps(db): return max(0, min(MAX_SPREAD_BPS, setting(db, 'crypto_conversion_spread_bps')))

def price(db, ledger_code):
    """Current USD price of one whole unit of the asset behind this ledger code. Raises price_unavailable rather than guess."""
    sym = ledger_code.split('.')[0]; a = one(db, 'SELECT * FROM assets WHERE symbol=?', sym)
    def no(why): raise ApiError(409, 'price_unavailable', f'{sym} cannot be converted to your Account Balance right now ({why}).')
    if not a: no('no price is configured')
    if a['status'] != 'active': no('trading in this asset is paused')
    try: p = Decimal(a['last_price'])
    except Exception: no('the price is invalid')
    if p <= 0: no('the price is invalid')
    max_age = setting(db, 'crypto_price_max_age_minutes')
    if max_age:
        if not a['price_updated_at'] or (util.utcnow() - util.parse_ts(a['price_updated_at'])).total_seconds() > max_age * 60: no('the price is out of date')
    return p

def _major(db, ledger_code, units): return Decimal(int(units)).scaleb(-one(db, 'SELECT decimals FROM ledger_assets WHERE code=?', ledger_code)['decimals'])

def usd_cents(db, ledger_code, units, side):
    """-> (cents, market price, spread bps). side='credit': what the customer receives (rounded down). side='debit': what the customer pays (rounded up)."""
    p = price(db, ledger_code); bps = spread_bps(db); s = Decimal(bps) / 10000
    eff = p * (1 - s) if side == 'credit' else p * (1 + s)
    cents = int((_major(db, ledger_code, units) * eff * 100).to_integral_value(ROUND_FLOOR if side == 'credit' else ROUND_CEILING))
    if cents <= 0: raise ApiError(409, 'amount_too_small', 'This amount is too small to convert to your Account Balance.')
    return cents, p, bps

def _record(db, uid, direction, code, units, p, bps, cents, st, sid, eid, tid, ref):
    return db.execute('INSERT INTO crypto_conversions(ref,user_id,direction,ledger_code,units,price,spread_bps,usd_cents,source_type,source_id,crypto_entry_id,cash_txn_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (ref, uid, direction, code, str(int(units)), str(p), bps, cents, st, sid, eid, tid, util.now())).lastrowid

def _to_cash(db, uid, code, units, direction, st, sid, source_ref, non_live, extra=None):
    """crypto liability -> Account Balance. Idempotent on (direction, source)."""
    ex = one(db, 'SELECT * FROM crypto_conversions WHERE direction=? AND source_type=? AND source_id=? AND ledger_code=?', direction, st, sid, code)
    if ex: return dict(ex)
    cents, p, bps = usd_cents(db, code, units, 'credit'); ref = util.new_ref('CNV'); key = f'cconv:{direction}:{st}:{sid}:{code}'; sym = code.split('.')[0]
    eid = acc.post_entry(db, entry_type='crypto.conversion_to_cash', idem=key, source_type='crypto_conversion', source_ref=ref, description=f'{source_ref}: crypto converted to Account Balance',
                         lines=[(cl.customer_account(db, uid), 'debit', code, int(units)), (acc.account_id(db, 'CRYPTO_CONVERSION'), 'credit', code, int(units))])
    cash = ledger.account_for(db, uid, 'cash')
    tid = ledger.post(db, user_id=uid, account_id=cash['id'], type='deposit', direction='credit', amount_cents=cents, status='completed', reference=source_ref, idem=key, non_live=non_live,
                      metadata={'crypto_conversion': ref, 'asset': sym, 'ledger_code': code, 'units': format(_major(db, code, units), 'f'), 'price': str(p), 'spread_bps': bps, 'source': source_ref, **(extra or {})})
    cid = _record(db, uid, direction, code, units, p, bps, cents, st, sid, eid, tid, ref)
    return dict(one(db, 'SELECT * FROM crypto_conversions WHERE id=?', cid))

def convert_deposit(db, dep, info):
    """Called right after a deposit's normal crypto credit, inside the same database transaction."""
    with tx(db): return _to_cash(db, dep['user_id'], info['ledger_code'], int(dep['amount']), 'deposit', 'crypto_deposit', dep['id'], dep['ref'], bool(info['is_testnet']))

def convert_to_crypto(db, user_id, info, units, wid, wref):
    """Account Balance -> crypto liability, for a withdrawal request. Fails with insufficient_funds if the cash is not available."""
    code = info['ledger_code']
    with tx(db):
        cents, p, bps = usd_cents(db, code, units, 'debit'); ref = util.new_ref('CNV'); key = f'cconv:withdrawal:crypto_withdrawal:{wid}:{code}'; sym = code.split('.')[0]
        cash = ledger.account_for(db, user_id, 'cash')
        tid = ledger.post(db, user_id=user_id, account_id=cash['id'], type='withdrawal', direction='debit', amount_cents=cents, status='completed', reference=wref, idem=key, non_live=bool(info['is_testnet']),
                          metadata={'crypto_conversion': ref, 'asset': sym, 'ledger_code': code, 'units': format(_major(db, code, units), 'f'), 'price': str(p), 'spread_bps': bps, 'source': wref})
        eid = acc.post_entry(db, entry_type='crypto.conversion_from_cash', idem=key, source_type='crypto_conversion', source_ref=ref, description=f'{wref}: Account Balance converted to {sym} for withdrawal',
                             lines=[(acc.account_id(db, 'CRYPTO_CONVERSION'), 'debit', code, int(units)), (cl.customer_account(db, user_id), 'credit', code, int(units))])
        cid = _record(db, user_id, 'withdrawal', code, units, p, bps, cents, 'crypto_withdrawal', wid, eid, tid, ref)
        return dict(one(db, 'SELECT * FROM crypto_conversions WHERE id=?', cid))

def reverse(db, conversion_id, reason):
    """Undo a conversion exactly (used when a withdrawal is cancelled, rejected or fails). Idempotent."""
    with tx(db):
        c = one(db, 'SELECT * FROM crypto_conversions WHERE id=?', conversion_id)
        if not c or c['reversed_at']: return
        acc.reverse_entry(db, c['crypto_entry_id'], reason)
        ledger.transition(db, c['cash_txn_id'], 'reversed')
        db.execute('UPDATE crypto_conversions SET reversed_at=? WHERE id=?', (util.now(), conversion_id))

def for_source(db, source_type, source_id):
    r = one(db, "SELECT * FROM crypto_conversions WHERE source_type=? AND source_id=? AND reversed_at IS NULL ORDER BY id DESC", source_type, source_id)
    return dict(r) if r else None

def view(c):
    """Customer-facing facts about one conversion."""
    if not c: return None
    return {'usd': cstr(c['usd_cents']), 'rate': c['price'], 'spread_bps': c['spread_bps'], 'ref': c['ref']}

def convert_legacy(db):
    """One-off: convert crypto balances that customers held before crypto funding went to Account Balance. Idempotent. Unpriced balances are left
    untouched (still shown as holdings) and reported, never guessed."""
    out = {'converted': 0, 'skipped': []}
    for g in all_(db, "SELECT owner_user_id FROM gl_accounts WHERE code LIKE 'CRYPTO-U%' AND owner_user_id IS NOT NULL"):
        uid = g['owner_user_id']
        for code, units in cl.balances(db, uid).items():
            if units <= 0: continue
            try: _to_cash_legacy(db, uid, code, units); out['converted'] += 1
            except ApiError as e: out['skipped'].append({'user_id': uid, 'ledger_code': code, 'reason': e.code})
    return out

def _to_cash_legacy(db, uid, code, units):
    info_net = one(db, 'SELECT n.is_testnet FROM asset_networks an JOIN blockchain_networks n ON n.id=an.network_id WHERE an.ledger_code=?', code)
    with tx(db): return _to_cash(db, uid, code, units, 'legacy', f'balance:{int(units)}', uid, f'LEGACY-U{uid}', bool(info_net['is_testnet']) if info_net else True, {'legacy': True})
