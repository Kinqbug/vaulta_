"""Crypto withdrawals. The Flask application never holds a private key: it asks the WalletProvider (custody system) to build and submit a
transfer. Lifecycle:

  request  -> authenticated customer, KYC, mandatory 2FA, balance verified, destination validated for the network and screened, funds RESERVED
              in the ledger in the same transaction (so two simultaneous requests cannot both spend the same balance)
  approve  -> a different staff member (pending_approval -> approved); reject/cancel releases the reservation by reversal
  process  -> approved -> signing -> broadcast (custody signs; a failure whose outcome is unknown is NEVER auto-retried: it stays 'signing' for staff)
  advance  -> broadcast/confirming -> completed once the chain says final; posts Dr clearing / Cr custody (+ network fee)
Reservation happens at request time (earlier than the stylised flow in the spec) because reserving after approval would leave a window in which
the same balance could be requested twice.
"""
import json
from decimal import Decimal
from . import crypto_convert as conv, util, ledger, accounting as acc, crypto_ledger as cl, crypto_wallets as cw, crypto_deposits as cd, crypto_catalog as cat, chain_addresses as ca, assets as reg
from .util import ApiError
from .db import one, all_, tx
from .notify import notify
from .mailer import events as mail
from .audit import audit
from .rbac import setting

OPEN = ('pending_approval', 'approved', 'signing', 'broadcast', 'confirming')

def fee_for(info, amount):
    """Flat fee + percentage (basis points, rounded UP so the platform never under-collects), both set by the administrator per asset-network."""
    pol = json.loads(info['withdrawal_fee_policy'] or '{}'); bps = int(pol.get('bps', 0))
    return int(pol.get('flat_minor', 0)) + (-(-int(amount) * bps // 10000) if bps else 0)

def limits(info):
    pol = json.loads(info['withdrawal_fee_policy'] or '{}')
    mn = int(info['min_withdrawal']) if info['min_withdrawal'] not in (None, '') else int(pol.get('min_minor', 0))
    return mn, (int(info['max_withdrawal']) if info['max_withdrawal'] not in (None, '') else None)

def fee_parts(info, amount):
    """(fixed part, percentage part) of the withdrawal fee. fee_for() is their sum, so the breakdown can never disagree with what is charged."""
    pol = json.loads(info['withdrawal_fee_policy'] or '{}'); bps = int(pol.get('bps', 0))
    return int(pol.get('flat_minor', 0)), (-(-int(amount) * bps // 10000) if bps else 0)

def max_amount(info, balance):
    """Largest amount a such that a + fee(a) <= balance (and <= the configured maximum). Used by 'Withdraw all' so the customer never has to guess the fee."""
    pol = json.loads(info['withdrawal_fee_policy'] or '{}'); flat = int(pol.get('flat_minor', 0)); bps = int(pol.get('bps', 0)); balance = int(balance)
    if balance <= flat: return 0
    a = (balance - flat) * 10000 // (10000 + bps)
    while a > 0 and a + fee_for(info, a) > balance: a -= 1
    mx = limits(info)[1]
    return min(a, mx) if mx else a

def _available(info):
    return info['an_status'] == 'active' and info['withdrawal_enabled'] and info['asset_status'] == 'active' and info['network_status'] == 'active' and bool(info['asset_visible'])

def check_address(db, info, destination):
    """Format validation for the network plus 'not a Vaulta address'. A valid format does NOT prove the address is the customer's or that it is safe."""
    destination = (destination or '').strip(); fmt = info['address_format']
    if not destination: raise ApiError(422, 'invalid_address', 'Enter the destination wallet address.')
    try: ca.validate(fmt, destination, testnet=bool(info['is_testnet']))
    except ca.InvalidAddress as e:
        guess = ca.family_of(destination); hint = f" That looks like {cw.NAMES[guess]} address." if guess and guess != fmt else ''
        raise ApiError(422, 'invalid_address', f"Not a valid {info['network_name']} address ({e}).{hint}")
    if one(db, "SELECT 1 FROM company_crypto_wallets w JOIN asset_networks an ON an.id=w.asset_network_id WHERE an.network_id=? AND lower(w.public_address)=lower(?)", info['network_id'], destination): raise ApiError(422, 'company_address', 'You cannot withdraw to a Vaulta address.')
    return destination

def _cash_available(db, user_id): return ledger.balance(db, ledger.account_for(db, user_id, 'cash')['id'])['available_cents']

def max_for_cash(db, info, cash_cents):
    """Largest amount (asset units) the customer can withdraw so that amount + fee, converted at the current rate, fits inside their Account Balance
    (and the configured maximum). Binary search over the same conversion function the server charges with, so 'Withdraw all' is exact."""
    code = info['ledger_code']; p, bps = conv.price(db, code), conv.spread_bps(db); dec = one(db, 'SELECT decimals FROM ledger_assets WHERE code=?', code)['decimals']
    eff = p * (1 + Decimal(bps) / 10000); hi = int((Decimal(int(cash_cents)) / 100 / eff).scaleb(dec)) + 1; mx = limits(info)[1]
    if mx: hi = min(hi, mx)
    cost = lambda a: conv.usd_cents(db, code, a + fee_for(info, a), 'debit')[0]
    lo = 0
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if cost(mid) <= cash_cents: lo = mid
        else: hi = mid - 1
    return lo

def withdrawal_info(db, user, ledger_code):
    """Everything the Withdraw page needs for one asset-network, from the account and the registry: Account Balance, limits, fee schedule, the current rate and the 'withdraw all' amount."""
    info = cw.an_info(db, ledger_code=ledger_code); mn, mx = limits(info); cash = _cash_available(db, user['id']); f = lambda m: reg.from_minor(db, ledger_code, m)
    pol = json.loads(info['withdrawal_fee_policy'] or '{}'); rate = top = None; why = None
    try: rate = conv.price(db, ledger_code); top = max_for_cash(db, info, cash)
    except ApiError as e: why = e.message
    return {'ledger_code': ledger_code, 'asset': info['symbol'], 'available_usd': util.cstr(cash), 'rate': str(rate) if rate is not None else None, 'spread_bps': conv.spread_bps(db), 'price_available': rate is not None, 'price_message': why,
            'min': f(mn), 'max': f(mx) if mx else None, 'fee_fixed': f(int(pol.get('flat_minor', 0))), 'fee_percent': str(Decimal(int(pol.get('bps', 0))) / 100),
            'max_amount': f(top or 0), 'can_withdraw': bool(_available(info) and top is not None and top >= max(mn, 1)), 'available_for_withdrawal': _available(info)}

def quote(db, user, ledger_code, amount):
    info = cw.an_info(db, ledger_code=ledger_code); amt = reg.to_minor(db, ledger_code, amount); fee = fee_for(info, amt); mn, mx = limits(info); cash = _cash_available(db, user['id']); fixed, pct = fee_parts(info, amt)
    f = lambda m: reg.from_minor(db, ledger_code, m); nice = lambda m: (lambda t: t.rstrip('0').rstrip('.') if '.' in t else t)(f(m))
    problems = []                                            # advisory only: request_withdrawal() re-checks every one of these on the server
    if not _available(info): problems.append({'code': 'asset_unavailable', 'message': f"{info['symbol']} withdrawals are not available right now."})
    if amt < mn: problems.append({'code': 'below_minimum', 'message': f"The minimum {info['symbol']} withdrawal is {nice(mn)}."})
    if mx and amt > mx: problems.append({'code': 'above_maximum', 'message': f"The maximum {info['symbol']} withdrawal is {nice(mx)}."})
    usd = rate = None
    try:
        usd, rate, _bps = conv.usd_cents(db, ledger_code, amt + fee, 'debit')
        if usd > cash: problems.append({'code': 'insufficient_funds', 'message': 'Your Account Balance does not cover this amount plus the fee.'})
    except ApiError as e: problems.append({'code': e.code, 'message': e.message})
    return {'amount': f(amt), 'fee': f(fee), 'fee_fixed': f(fixed), 'fee_percent_amount': f(pct), 'total_debit': f(amt + fee), 'receives': f(amt), 'usd_debit': util.cstr(usd) if usd is not None else None, 'rate': str(rate) if rate is not None else None,
            'available_usd': util.cstr(cash), 'min': f(mn), 'max': f(mx) if mx else None, 'problems': problems}

def source_wallet(db, an_id):
    for t in ('hot', 'deposit'):
        w = cw.live_wallet(db, an_id, t)
        if cw.is_configured(w) and w['status'] == 'active': return w
    return None

def view(db, w, admin=False):
    info = cw.an_info(db, w['asset_network_id']); c = info['ledger_code']; a = one(db, 'SELECT * FROM crypto_assets WHERE id=?', info['asset_id'])
    v = {'ref': w['ref'], 'kind': 'withdrawal', 'asset': info['symbol'], 'asset_name': info['asset_name'], 'asset_id': a['id'], 'logo': cat.logo_url(a), 'ledger_code': c, 'network': info['network_name'], 'network_code': info['network_code'],
         'destination': w['destination'], 'amount': reg.from_minor(db, c, w['amount']), 'fee': reg.from_minor(db, c, w['fee']), 'total_debit': reg.from_minor(db, c, int(w['amount']) + int(w['fee'])),
         'status': w['status'], 'status_reason': w['status_reason'], 'tx_hash': w['tx_hash'], 'confirmations': w['confirmations'], 'created_at': w['created_at'], 'id': w['id'], 'user_id': w['user_id'],
         'network_fee': reg.from_minor(db, c, w['network_fee']) if int(w['network_fee']) else '0', 'processing_mode': w['processing_mode'], 'usd_debit': util.cstr(w['usd_debit_cents']) if w['usd_debit_cents'] else None, 'rate': w['rate']}
    if admin:
        u = one(db, 'SELECT u.public_id,u.email,u.kyc_status FROM users u WHERE u.id=?', w['user_id'])
        v.update({'user': {'public_id': u['public_id'], 'email': u['email'], 'kyc_status': u['kyc_status']}, 'approved_by': w['approved_by'], 'approved_at': w['approved_at'], 'processed_by': w['processed_by'], 'processed_at': w['processed_at'],
                  'admin_note': w['admin_note'], 'manual_attested': bool(w['manual_attested']), 'source_wallet': cw.get_wallet(db, w['company_wallet_id'])['public_address'] if w['company_wallet_id'] else None})
    return v

def request_withdrawal(db, providers, user, ledger_code, amount, destination, code=None, idem=None):
    cd.crypto_gate(providers); u = cd._can_use(db, user)
    if not u['totp_enabled']: raise ApiError(403, 'two_factor_setup_required', 'Enable two-factor authentication in Security before withdrawing crypto.')
    from . import finance as fin
    replay = idem and one(db, "SELECT 1 FROM idempotency_keys WHERE user_id=? AND key=? AND scope='crypto_withdrawal'", u['id'], idem)
    if not replay: fin.require_2fa(db, u, code, 'crypto_withdrawal')     # a replayed request returns the stored result; a changed payload fails the fingerprint check
    info = cw.an_info(db, ledger_code=ledger_code)
    if info['an_status'] != 'active' or not info['withdrawal_enabled'] or info['asset_status'] != 'active' or info['network_status'] != 'active' or not info['asset_visible']: raise ApiError(409, 'asset_unavailable', f"{info['symbol']} withdrawals are not available right now.")
    cw.env_guard(providers.env, info); amt = reg.to_minor(db, ledger_code, amount); fee = fee_for(info, amt); mn, mx = limits(info); fmt = info['address_format']; destination = (destination or '').strip()
    if amt <= 0: raise ApiError(422, 'invalid_amount', 'Enter an amount greater than zero.')
    if amt < mn: raise ApiError(422, 'below_minimum', f"The minimum {info['symbol']} withdrawal is {reg.from_minor(db, ledger_code, mn)}.")
    if mx and amt > mx: raise ApiError(422, 'above_maximum', f"The maximum {info['symbol']} withdrawal is {reg.from_minor(db, ledger_code, mx)}.")
    destination = check_address(db, info, destination)
    clear, why = providers.compliance.screen(db, fmt, ca.address_key(fmt, destination))
    if not clear: raise ApiError(422, 'destination_blocked', 'This destination cannot be used.')
    src = source_wallet(db, info['an_id'])
    if not src: raise ApiError(409, 'asset_unavailable', 'No funded withdrawal wallet is configured for this asset.')
    def run():
        ref = util.new_ref('CWD')
        wid = db.execute('INSERT INTO crypto_withdrawals(ref,user_id,asset_network_id,company_wallet_id,destination,amount,fee,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                         (ref, u['id'], info['an_id'], src['id'], destination, str(amt), str(fee), 'pending_approval', util.now(), util.now())).lastrowid
        cv = conv.convert_to_crypto(db, u['id'], info, amt + fee, wid, ref)          # Account Balance -> crypto at the market rate (rate locked now); raises insufficient_funds if the cash is not there
        eid = cl.post_withdrawal_reserve(db, wid, u['id'], ledger_code, amt, fee, ref)
        db.execute('UPDATE crypto_withdrawals SET reserve_entry_id=?,conversion_id=?,usd_debit_cents=?,rate=? WHERE id=?', (eid, cv['id'], cv['usd_cents'], cv['price'], wid))
        notify(db, u['id'], 'withdrawal', 'Crypto withdrawal requested', f"{reg.from_minor(db, ledger_code, amt)} {info['symbol']} withdrawal {ref} is awaiting approval.", '#/withdraw')
        mail.emit(db, 'withdrawal.requested', u['id'], ref)
        mail.emit(db, 'security.withdrawal_alert', u['id'], ref)
        return {'withdrawal': view(db, dict(one(db, 'SELECT * FROM crypto_withdrawals WHERE id=?', wid))), 'message': 'Withdrawal requested. It is reviewed by our team before it is processed.'}
    with tx(db): return ledger.idempotent(db, u['id'], idem, 'crypto_withdrawal', {'c': ledger_code, 'a': str(amt), 'd': destination}, run)

def _release(db, w, reason):
    rid = acc.reverse_entry(db, w['reserve_entry_id'], reason); db.execute('UPDATE crypto_withdrawals SET release_entry_id=? WHERE id=?', (rid, w['id']))
    if w.get('conversion_id'): conv.reverse(db, w['conversion_id'], reason)          # the customer gets back exactly the dollars that were debited (rate was locked at request time)

def _get(db, wid):
    w = one(db, 'SELECT * FROM crypto_withdrawals WHERE id=?', wid)
    if not w: raise ApiError(404, 'not_found', 'Withdrawal not found')
    return dict(w)

def cancel(db, user, ref):
    with tx(db):
        w = one(db, 'SELECT * FROM crypto_withdrawals WHERE ref=? AND user_id=?', ref, user['id'])
        if not w: raise ApiError(404, 'not_found', 'Withdrawal not found')
        if w['status'] != 'pending_approval': raise ApiError(409, 'invalid_state', 'This withdrawal can no longer be cancelled.')
        _release(db, dict(w), 'cancelled by customer'); db.execute("UPDATE crypto_withdrawals SET status='cancelled',updated_at=? WHERE id=?", (util.now(), w['id']))
        mail.emit(db, 'withdrawal.cancelled', w['user_id'], w['ref'])

def approve(db, actor, wid, reason=None):
    with tx(db):
        w = _get(db, wid)
        if w['status'] != 'pending_approval': raise ApiError(409, 'invalid_state', f"Cannot approve a withdrawal that is {w['status']}.")
        if w['user_id'] == actor['id']: raise ApiError(403, 'separation_of_duties', 'You cannot approve your own withdrawal.')
        db.execute("UPDATE crypto_withdrawals SET status='approved',approved_by=?,approved_at=?,updated_at=? WHERE id=?", (actor['id'], util.now(), util.now(), wid))
        audit(db, actor['id'], 'crypto.withdrawal_approved', 'crypto_withdrawal', wid, {'status': 'pending_approval'}, {'status': 'approved'}, reason)
        mail.emit(db, 'withdrawal.approved', w['user_id'], w['ref'])

def reject(db, actor, wid, reason):
    reason = cw._reason(reason, 10)
    with tx(db):
        w = _get(db, wid)
        if w['status'] not in ('pending_approval', 'approved'): raise ApiError(409, 'invalid_state', f"Cannot reject a withdrawal that is {w['status']}.")
        _release(db, w, 'rejected: ' + reason); db.execute("UPDATE crypto_withdrawals SET status='rejected',status_reason=?,updated_at=? WHERE id=?", (reason, util.now(), wid))
        audit(db, actor['id'], 'crypto.withdrawal_rejected', 'crypto_withdrawal', wid, {'status': w['status']}, {'status': 'rejected'}, reason)
        notify(db, w['user_id'], 'withdrawal', 'Crypto withdrawal rejected', f"Withdrawal {w['ref']} was rejected and your funds were released.", '#/withdraw')
        mail.emit(db, 'withdrawal.rejected', w['user_id'], w['ref'])

def process(db, providers, actor, wid):
    """approved -> signing -> broadcast. External calls happen OUTSIDE any database transaction, behind a compare-and-set so nothing can submit twice."""
    with tx(db):
        n = db.execute("UPDATE crypto_withdrawals SET status='signing',updated_at=? WHERE id=? AND status='approved'", (util.now(), wid)).rowcount
        if n != 1: raise ApiError(409, 'invalid_state', 'Only approved withdrawals can be processed.')
        w = _get(db, wid)
    info = cw.an_info(db, w['asset_network_id']); wallet = cw.get_wallet(db, w['company_wallet_id'])
    try:
        prepared = providers.wallets.create_transaction(info['network_code'], wallet['public_address'], w['destination'], int(w['amount']), info['contract_address'], client_ref=w['ref'])
    except ApiError as e:      # nothing was signed or sent: safe to return to the approved queue
        with tx(db): db.execute("UPDATE crypto_withdrawals SET status='approved',status_reason=?,updated_at=? WHERE id=?", (f'could not prepare: {e.code}', util.now(), wid))
        raise
    try: res = providers.wallets.submit_transaction(prepared)
    except ApiError as e:
        with tx(db):
            if e.code == 'custody_rejected': db.execute("UPDATE crypto_withdrawals SET status='approved',status_reason=?,updated_at=? WHERE id=?", ('custody provider rejected the transfer', util.now(), wid))
            else: db.execute("UPDATE crypto_withdrawals SET status_reason=?,updated_at=? WHERE id=?", (f'submission outcome unknown ({e.code}): verify on-chain before any retry', util.now(), wid))
        raise
    with tx(db):
        db.execute("UPDATE crypto_withdrawals SET status='broadcast',tx_hash=?,provider_ref=?,network_fee=?,status_reason=NULL,updated_at=? WHERE id=?", (res['tx_hash'], res.get('provider_ref'), str(int(res.get('network_fee', 0))), util.now(), wid))
        audit(db, actor['id'] if actor else None, 'crypto.withdrawal_broadcast', 'crypto_withdrawal', wid, {'status': 'signing'}, {'status': 'broadcast', 'tx_hash': res['tx_hash']}, None)
        mail.emit(db, 'withdrawal.processing', w['user_id'], w['ref'])
    return view(db, _get(db, wid))

def fail_unknown(db, actor, wid, reason):
    """Staff verified on-chain that a 'signing' withdrawal never went out: release the reservation."""
    reason = cw._reason(reason, 10)
    with tx(db):
        w = _get(db, wid)
        if w['status'] != 'signing' or w['tx_hash']: raise ApiError(409, 'invalid_state', 'Only a withdrawal stuck in signing without a transaction can be failed.')
        _release(db, w, 'failed: ' + reason); db.execute("UPDATE crypto_withdrawals SET status='failed',status_reason=?,updated_at=? WHERE id=?", (reason, util.now(), wid))
        audit(db, actor['id'], 'crypto.withdrawal_failed', 'crypto_withdrawal', wid, {'status': 'signing'}, {'status': 'failed'}, reason)
        mail.emit(db, 'withdrawal.failed', w['user_id'], w['ref'])

def advance(db, providers, wid):
    with tx(db):
        w = _get(db, wid)
        if w['status'] not in ('broadcast', 'confirming'): return w
        info = cw.an_info(db, w['asset_network_id']); evs = providers.chain.get_transaction(info['network_code'], w['tx_hash'])
        if not evs: return w
        ev = evs[0]
        if not ev.success:
            _release(db, w, 'transaction failed on-chain'); db.execute("UPDATE crypto_withdrawals SET status='failed',status_reason='transaction failed on-chain',updated_at=? WHERE id=?", (util.now(), wid)); mail.emit(db, 'withdrawal.failed', w['user_id'], w['ref']); return _get(db, wid)
        eff = cw.effective(db, cw.get_wallet(db, w['company_wallet_id']))
        fin = ev.confirmations >= eff['confirmations'] and (ev.commitment == 'finalized' if json.loads(info['finality_policy'] or '{}').get('mode') == 'commitment' else True)
        if not fin:
            db.execute("UPDATE crypto_withdrawals SET status=?,confirmations=?,block_number=?,block_hash=?,updated_at=? WHERE id=?", ('confirming' if ev.confirmations else 'broadcast', ev.confirmations, ev.block_number, ev.block_hash, util.now(), wid)); return _get(db, wid)
        eid = cl.post_withdrawal_completed(db, wid, info['ledger_code'], int(w['amount']), int(w['network_fee']), w['ref'])
        db.execute("UPDATE crypto_withdrawals SET status='completed',confirmations=?,block_number=?,block_hash=?,completion_entry_id=?,updated_at=? WHERE id=?", (ev.confirmations, ev.block_number, ev.block_hash, eid, util.now(), wid))
        notify(db, w['user_id'], 'withdrawal', 'Crypto withdrawal completed', f"Withdrawal {w['ref']} is confirmed on-chain.", '#/withdraw')
        mail.emit(db, 'withdrawal.completed', w['user_id'], w['ref'])      # only once the chain reports the required confirmations
        return _get(db, wid)

def advance_all(db, providers):
    n = 0
    for r in all_(db, "SELECT id FROM crypto_withdrawals WHERE status IN('broadcast','confirming')"):
        try: advance(db, providers, r['id']); n += 1
        except ApiError: pass
    return n

def history(db, user_id, limit=100): return [view(db, dict(w)) for w in db.execute('SELECT * FROM crypto_withdrawals WHERE user_id=? ORDER BY id DESC LIMIT ?', (user_id, limit))]
def list_admin(db, status=None, limit=200):
    return [view(db, dict(w), admin=True) for w in db.execute('SELECT * FROM crypto_withdrawals' + (' WHERE status=?' if status else '') + ' ORDER BY id DESC LIMIT ?', (*([status] if status else []), limit))]


# ---------------------------------------------------------------- manual processing (when an authorised administrator sends the funds outside the custody integration)
def manual_process(db, actor, wid, tx_hash, network_fee='0', note=None):
    """An approved withdrawal that staff sent by other means: record the resulting transaction. This is NOT completion: completion needs chain confirmation or an attested verification."""
    note = (note or '').strip()
    with tx(db):
        w = _get(db, wid); info = cw.an_info(db, w['asset_network_id']); fmt = info['address_format']; h = (tx_hash or '').strip()
        if not cd._HASH[fmt].match(h): raise ApiError(422, 'invalid_tx_hash', f"That is not a valid {info['network_name']} transaction hash.")
        key = cd._norm_hash(fmt, h); nf = cat._dec(db, info['ledger_code'], network_fee if network_fee not in (None, '') else '0', 'network fee') or 0
        if one(db, 'SELECT 1 FROM crypto_withdrawals WHERE tx_hash=? AND id<>?', key, wid): raise ApiError(409, 'duplicate_transaction', 'This transaction hash is already recorded against another withdrawal.')
        if db.execute("UPDATE crypto_withdrawals SET status='broadcast',tx_hash=?,processing_mode='manual',processed_by=?,processed_at=?,admin_note=?,network_fee=?,status_reason=NULL,updated_at=? WHERE id=? AND status='approved'",
                      (key, actor['id'], util.now(), note or None, str(nf), util.now(), wid)).rowcount != 1: raise ApiError(409, 'invalid_state', f"Only approved withdrawals can be processed (this one is {w['status']}).")
        audit(db, actor['id'], 'crypto.withdrawal_manual_processed', 'crypto_withdrawal', wid, {'status': 'approved'}, {'status': 'broadcast', 'tx_hash': key, 'network_fee': str(nf)}, note or None)
    return view(db, _get(db, wid), admin=True)

def manual_complete(db, providers, actor, wid, note=None, attest=False):
    """Complete a manually processed withdrawal only on evidence: the chain confirms it, or (when no provider can) the administrator attests they verified it."""
    note = (note or '').strip(); w = _get(db, wid)
    if w['status'] not in ('broadcast', 'confirming') or not w['tx_hash']: raise ApiError(409, 'invalid_state', 'Only a processed withdrawal with a transaction hash can be completed.')
    info = cw.an_info(db, w['asset_network_id'])
    try: evs = providers.chain.get_transaction(info['network_code'], w['tx_hash']); available = True
    except ApiError as e:
        if e.code != 'provider_not_configured': raise
        evs, available = None, False
    if available:
        if not evs: raise ApiError(409, 'transaction_not_found', 'The transaction is not visible on the network yet.')
        r = advance(db, providers, wid)
        if r['status'] not in ('completed', 'failed'): raise ApiError(409, 'insufficient_confirmations', 'The transaction does not have the required confirmations yet.')
        return view(db, r, admin=True)
    if not attest or len(note) < 20: raise ApiError(409, 'verification_required', 'No blockchain provider is connected. Confirm you verified the transaction independently and describe how (at least 20 characters).')
    with tx(db):
        w = _get(db, wid)
        if w['status'] not in ('broadcast', 'confirming'): raise ApiError(409, 'invalid_state', 'This withdrawal was already finalised.')
        eid = cl.post_withdrawal_completed(db, wid, info['ledger_code'], int(w['amount']), int(w['network_fee']), w['ref'])
        db.execute("UPDATE crypto_withdrawals SET status='completed',completion_entry_id=?,manual_attested=1,admin_note=COALESCE(admin_note||' | ','')||?,updated_at=? WHERE id=? AND status IN('broadcast','confirming')", (eid, note, util.now(), wid))
        audit(db, actor['id'], 'crypto.withdrawal_manual_completed', 'crypto_withdrawal', wid, {'status': w['status']}, {'status': 'completed', 'attested': True}, note)
        notify(db, w['user_id'], 'withdrawal', 'Crypto withdrawal completed', f"Withdrawal {w['ref']} has been sent. Transaction: {w['tx_hash']}", '#/withdraw')
        mail.emit(db, 'withdrawal.completed', w['user_id'], w['ref'])      # administrator attested independent verification (recorded + audited)
    return view(db, _get(db, wid), admin=True)
