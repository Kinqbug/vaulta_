"""Crypto deposits: intents -> on-chain detection -> independent verification -> confirmations -> attribution -> risk -> ledger credit.

Principles (each one is covered by tests/test_crypto.py):
  * A transaction hash supplied by a customer is only a hint to go and look. Every field that matters is re-read from the chain provider.
  * Money arriving at a company address is NOT thereby a customer credit. A deposit is credited only when it is on the right network, in the
    right asset, at an active wallet, final under that asset's policy, reliably attributed to ONE customer, and cleared by the risk step.
  * Attribution never uses the amount. It uses (a) a memo carrying the intent's reference code, or (b) a sender address the customer proved
    they control, held by exactly one customer, together with that customer's open deposit intent. Anything else stays in manual_review.
  * The identity of a transfer EVENT is (network, tx hash, event index). It exists once; it can be credited once.
"""
import re, json, secrets, sqlite3, hashlib
from . import crypto_convert as conv, util, security, uploads, ledger, chain_addresses as ca, crypto_wallets as cw, crypto_ledger as cl, crypto_catalog as cat, assets as reg
from .util import ApiError
from .db import one, all_, val, tx
from .rbac import setting
from .notify import notify
from .mailer import events as mail
from .audit import audit

ACTIVE = ('detected', 'validating', 'unconfirmed', 'confirming', 'confirmed', 'risk_review')
TERMINAL = ('credited', 'rejected', 'duplicate', 'wrong_network', 'unsupported_asset', 'failed')
ALLOWED = {
    'detected': {'validating', 'rejected', 'failed', 'manual_review'},
    'validating': {'unconfirmed', 'confirming', 'confirmed', 'rejected', 'failed', 'manual_review', 'reorged'},
    'unconfirmed': {'confirming', 'confirmed', 'reorged', 'failed'},
    'confirming': {'unconfirmed', 'confirmed', 'reorged', 'failed'},
    'confirmed': {'risk_review', 'manual_review', 'reorged'},
    'risk_review': {'credited', 'manual_review', 'reorged'},
    'manual_review': {'validating', 'risk_review', 'rejected', 'duplicate', 'reorged'},
    'reorged': {'detected'},
}
LABELS = {'detected': 'Detected', 'validating': 'Verifying', 'unconfirmed': 'Awaiting confirmation', 'confirming': 'Confirming', 'confirmed': 'Under review', 'risk_review': 'Under review',
          'credited': 'Credited', 'manual_review': 'Under review', 'rejected': 'Rejected', 'duplicate': 'Duplicate', 'wrong_network': 'Wrong network', 'unsupported_asset': 'Unsupported asset',
          'reorged': 'Re-organised', 'failed': 'Failed'}
REORG_RESCAN = 100            # blocks re-read on every scan so shallow reorgs and late indexing cannot hide a transfer
_HASH = {'btc': re.compile(r'^[0-9a-fA-F]{64}$'), 'evm': re.compile(r'^0x[0-9a-fA-F]{64}$'), 'solana': re.compile(r'^[1-9A-HJ-NP-Za-km-z]{64,90}$')}

def _norm_hash(fmt, h): return h.lower() if fmt in ('evm', 'btc') else h
def _grace(db): return setting(db, 'crypto_intent_grace_hours')

def crypto_gate(providers):
    """Live chain infrastructure moves real value: it stays disabled until REAL_FUNDS_ENABLED=1 (set only after licensing/custody/compliance sign-off)."""
    if providers.crypto_mode == 'live' and not providers.crypto_real_funds:
        raise ApiError(403, 'funding_not_authorized', 'Crypto deposits and withdrawals are not yet available. Nothing was created.')

def _user(db, uid): return dict(one(db, 'SELECT * FROM users WHERE id=?', uid))
def _can_use(db, user):
    from . import finance as fin
    u = fin.can_transact(db, user)
    if setting(db, 'crypto_requires_kyc') and u['kyc_status'] != 'verified': raise ApiError(403, 'kyc_required', 'Complete identity verification before using crypto funding.')
    return u

# =====================================================================================================
#  Customer: supported assets, intents, source addresses
# =====================================================================================================
def page_copy(db, an_id, asset_row, confirmations, side):
    """Customer-facing copy for the Deposit/Withdraw pages. Administrator-configured text wins; otherwise a generic, non-promissory default is derived from the real configuration.
    Arrival text is always an ESTIMATE: nothing here promises a time."""
    an = one(db, 'SELECT * FROM asset_networks WHERE id=?', an_id); n = int(confirmations or 1)
    default_arrival = f"About {n} network confirmation{'s' if n != 1 else ''}"
    out = {'logo_alt': asset_row['logo_alt'] or f"{asset_row['name']} logo", 'notice': asset_row['notice'] or '', 'network_notice': an['network_notice'] or '',
           'estimated_arrival': an['estimated_arrival'] or default_arrival}
    if side == 'deposit': out['instructions'] = an['deposit_instructions'] or ''
    else: out.update(instructions=an['withdrawal_instructions'] or '', processing_time=an['processing_time'] or 'Withdrawals are reviewed by our team before they are sent. Times vary.')
    return out

def supported_assets(db):
    """Assets a customer may deposit right now (never exposes unconfigured/inactive wallets)."""
    out = []
    logos = {r['id']: r for r in db.execute('SELECT * FROM crypto_assets')}
    for r in all_(db, "SELECT an.id FROM asset_networks an WHERE an.status='active' AND an.deposit_enabled=1 ORDER BY an.id"):
        info = cw.an_info(db, r['id']); ca_ = logos[info['asset_id']]
        if info['asset_status'] != 'active' or info['network_status'] != 'active' or not ca_['visible']: continue
        w = cw.live_wallet(db, r['id'])
        if not (cw.is_configured(w) and w['status'] == 'active' and w['is_deposit_enabled']): continue
        e = cw.effective(db, w)
        out.append({'ledger_code': info['ledger_code'], 'symbol': info['symbol'], 'name': info['asset_name'], 'network': info['network_code'], 'network_name': info['network_name'],
                    'is_testnet': bool(info['is_testnet']), 'minimum_deposit': reg.from_minor(db, info['ledger_code'], e['minimum_deposit']), 'required_confirmations': e['confirmations'],
                    'decimals': info['decimals'], 'memo_supported': info['attribution_mode'] == 'memo_or_source', 'contract_address': info['contract_address'],
                    'asset_id': info['asset_id'], 'logo': cat.logo_url(ca_), 'description': ca_['description'] or '', 'ledger_code_network': info['network_code'],
                    'max_deposit': reg.from_minor(db, info['ledger_code'], one(db, 'SELECT max_deposit FROM asset_networks WHERE id=?', r['id'])['max_deposit']) if one(db, 'SELECT max_deposit FROM asset_networks WHERE id=?', r['id'])['max_deposit'] else None,
                    **page_copy(db, r['id'], ca_, e['confirmations'], 'deposit')})
    return out

def withdrawal_assets(db):
    """Assets a customer may withdraw right now, with network, limits and the fee schedule (everything the UI shows comes from the registry)."""
    out = []
    for r in all_(db, "SELECT an.id FROM asset_networks an WHERE an.status='active' AND an.withdrawal_enabled=1 ORDER BY an.id"):
        a = one(db, 'SELECT * FROM crypto_assets WHERE id=(SELECT asset_id FROM asset_networks WHERE id=?)', r['id']); n = cat.network_view(db, one(db, 'SELECT * FROM asset_networks WHERE id=?', r['id']))
        if a['status'] != 'active' or not a['visible'] or n['network_status'] != 'active': continue
        out.append({'asset_id': a['id'], 'symbol': a['symbol'], 'name': a['name'], 'logo': cat.logo_url(a), 'ledger_code': n['ledger_code'], 'network': n['network_code'], 'network_name': n['network_name'], 'is_testnet': n['is_testnet'],
                    'min_withdrawal': n['min_withdrawal'], 'max_withdrawal': n['max_withdrawal'], 'fee_flat': n['fee_flat'], 'fee_percent': n['fee_percent'], 'decimals': n['decimals'],
                    'contract_address': n['contract_address'], 'address_hint': cat.ADDRESS_HINTS.get(n['address_format'], ''), 'required_confirmations': n['confirmations_effective'],
                    **page_copy(db, r['id'], a, n['confirmations_effective'], 'withdrawal')})
    return out

def _deposit_wallet(db, providers, ledger_code):
    info = cw.an_info(db, ledger_code=ledger_code)
    if info['an_status'] != 'active' or not info['deposit_enabled'] or info['asset_status'] != 'active' or info['network_status'] != 'active' or not one(db, 'SELECT visible FROM crypto_assets WHERE id=?', info['asset_id'])['visible']:
        raise ApiError(409, 'asset_unavailable', f"{info['symbol']} deposits on {info['network_name']} are not available right now.")
    cw.env_guard(providers.env, info)
    w = cw.live_wallet(db, info['an_id'])
    if not cw.is_configured(w): raise ApiError(409, 'wallet_not_configured', f"No {info['symbol']} deposit wallet is configured yet. Nothing was created.")
    if w['status'] != 'active' or not w['is_deposit_enabled']: raise ApiError(409, 'asset_unavailable', f"{info['symbol']} deposits are temporarily unavailable.")
    cw.validate_wallet_address(info, w['public_address'])           # never show an address that does not validate
    return info, w

def intent_view(db, i, with_instructions=True):
    info = cw.an_info(db, i['asset_network_id']); w = cw.get_wallet(db, i['company_wallet_id'])
    out = {'ref': i['ref'], 'status': i['status'], 'ledger_code': info['ledger_code'], 'asset': info['symbol'], 'network': info['network_code'], 'network_name': info['network_name'],
           'expected_amount': reg.from_minor(db, info['ledger_code'], i['expected_amount']) if i['expected_amount'] else None, 'created_at': i['created_at'], 'expires_at': i['expires_at']}
    if with_instructions and i['status'] == 'open':
        e = cw.effective(db, w)
        out.update({'deposit_address': w['public_address'], 'minimum_deposit': reg.from_minor(db, info['ledger_code'], e['minimum_deposit']), 'required_confirmations': e['confirmations'],
                    'memo': i['reference_code'] if info['attribution_mode'] == 'memo_or_source' else None, 'contract_address': info['contract_address'], 'is_testnet': bool(info['is_testnet']),
                    'warning': f"Only send {info['symbol']} using the {info['network_name']} network to this address. Sending any other asset, or using a different network, may result in permanent loss of funds.",
                    'attribution': attribution_hint(db, i['user_id'], info)})
    return out

def attribution_hint(db, user_id, info):
    verified = bool(one(db, "SELECT 1 FROM customer_source_addresses WHERE user_id=? AND network_id=? AND status='verified'", user_id, info['network_id']))
    memo = info['attribution_mode'] == 'memo_or_source'
    return {'memo_supported': memo, 'has_verified_source_address': verified, 'will_auto_credit': memo or verified,
            'message': ('Include the reference in the transaction memo, or send from a verified address.' if memo else 'Send only from an address you have verified in Fund account > Crypto > My addresses.') if (memo or not verified) else 'Deposits from your verified addresses are matched to you automatically.'}

def create_intent(db, providers, user, ledger_code, expected_amount=None):
    crypto_gate(providers); u = _can_use(db, user)
    with tx(db):
        info, w = _deposit_wallet(db, providers, ledger_code)
        exp = None
        if expected_amount not in (None, ''):
            exp = reg.to_minor(db, ledger_code, expected_amount); mn = cw.effective(db, w)['minimum_deposit']
            if exp < mn: raise ApiError(422, 'below_minimum', f"The minimum {info['symbol']} deposit is {reg.from_minor(db, ledger_code, mn)}.")
        cur = one(db, "SELECT * FROM deposit_intents WHERE user_id=? AND asset_network_id=? AND status='open'", u['id'], info['an_id'])
        if cur:
            if cur['expires_at'] > util.now() and cur['company_wallet_id'] == w['id']: return intent_view(db, dict(cur))
            db.execute("UPDATE deposit_intents SET status=CASE WHEN expires_at<=? THEN 'expired' ELSE 'cancelled' END,closed_at=? WHERE id=?", (util.now(), util.now(), cur['id']))
        ref = util.new_ref('DIN'); code = 'VLT-' + secrets.token_hex(4).upper()
        iid = db.execute('INSERT INTO deposit_intents(ref,user_id,asset_network_id,company_wallet_id,expected_amount,reference_code,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)',
                         (ref, u['id'], info['an_id'], w['id'], str(exp) if exp else None, code, util.now(), util.ts_after(hours=setting(db, 'crypto_intent_ttl_hours')))).lastrowid
        return intent_view(db, dict(one(db, 'SELECT * FROM deposit_intents WHERE id=?', iid)))

def cancel_intent(db, user, ref):
    with tx(db):
        i = one(db, 'SELECT * FROM deposit_intents WHERE ref=? AND user_id=?', ref, user['id'])
        if not i: raise ApiError(404, 'not_found', 'Intent not found')
        if i['status'] != 'open': raise ApiError(409, 'invalid_state', 'This intent is already closed.')
        db.execute("UPDATE deposit_intents SET status='cancelled',closed_at=? WHERE id=?", (util.now(), i['id']))

def list_intents(db, user_id):
    return [intent_view(db, dict(i), with_instructions=False) for i in db.execute('SELECT * FROM deposit_intents WHERE user_id=? ORDER BY id DESC LIMIT 50', (user_id,))]

def get_open_intent(db, user, ref):
    i = one(db, 'SELECT * FROM deposit_intents WHERE ref=? AND user_id=?', ref, user['id'])
    if not i: raise ApiError(404, 'not_found', 'Intent not found')
    return dict(i)

# ---- source addresses (the only non-memo attribution basis) ----
def _net(db, code):
    n = one(db, 'SELECT * FROM blockchain_networks WHERE network_code=?', code)
    if not n or not n['address_format']: raise ApiError(404, 'unknown_network', 'Unknown network')
    return dict(n)

def register_source(db, user, network_code, address):
    u = _can_use(db, user); n = _net(db, network_code); address = (address or '').strip()
    try: ca.validate(n['address_format'], address, testnet=bool(n['is_testnet']))
    except ca.InvalidAddress as e: raise ApiError(422, 'invalid_address', f"Not a valid {n['name']} address ({e}).")
    key = ca.address_key(n['address_format'], address)
    if one(db, "SELECT 1 FROM company_crypto_wallets w JOIN asset_networks an ON an.id=w.asset_network_id WHERE an.network_id=? AND lower(w.public_address)=lower(?)", n['id'], address):
        raise ApiError(422, 'company_address', 'That is a Vaulta address, not your own wallet.')
    with tx(db):
        e = one(db, "SELECT * FROM customer_source_addresses WHERE user_id=? AND network_id=? AND address_key=? AND status IN('pending','verified')", u['id'], n['id'], key)
        if e: return source_view(dict(e))
        challenge = f"VAULTA-OWNERSHIP:{u['public_id']}:{n['network_code']}:{secrets.token_hex(8)}"
        sid = db.execute('INSERT INTO customer_source_addresses(user_id,network_id,address,address_key,challenge,created_at) VALUES(?,?,?,?,?,?)', (u['id'], n['id'], address, key, challenge, util.now())).lastrowid
        mail.emit(db, 'security.wallet_changed', u['id'], f'{sid}:added', address_id=sid, action='Address added (pending verification)', at=util.now())
        return source_view(dict(one(db, 'SELECT * FROM customer_source_addresses WHERE id=?', sid)))

def source_view(s):
    return {'id': s['id'], 'address': s['address'], 'status': s['status'], 'challenge': s['challenge'] if s['status'] == 'pending' else None, 'verified_at': s['verified_at'], 'proof_method': s['proof_method']}

def _verify_source(db, s, method, actor_id=None, note=None):
    with tx(db):
        cur = one(db, 'SELECT * FROM customer_source_addresses WHERE id=?', s['id'])
        if cur['status'] != 'pending': raise ApiError(409, 'invalid_state', f"This address is {cur['status']}.")
        other = one(db, "SELECT user_id FROM customer_source_addresses WHERE network_id=? AND address_key=? AND status='verified'", cur['network_id'], cur['address_key'])
        if other:
            db.execute("UPDATE customer_source_addresses SET status='rejected',note=? WHERE id=?", ('already verified by another customer', cur['id']))
            audit(db, actor_id, 'crypto.source_address_conflict', 'customer_source_address', cur['id'], None, {'network_id': cur['network_id']}, 'address already verified by another customer')
            raise ApiError(409, 'address_in_use', 'That address is already verified on another account. It cannot be used for attribution.')
        db.execute("UPDATE customer_source_addresses SET status='verified',proof_method=?,verified_by=?,verified_at=?,note=? WHERE id=?", (method, actor_id, util.now(), note, cur['id']))
        audit(db, actor_id or cur['user_id'], 'crypto.source_address_verified', 'customer_source_address', cur['id'], None, {'method': method, 'user_id': cur['user_id']}, note)
        mail.emit(db, 'security.wallet_changed', cur['user_id'], f"{cur['id']}:verified", address_id=cur['id'], action='Address verified', at=util.now())

def verify_source_with_proof(db, providers, user, sid, signature):
    s = one(db, 'SELECT * FROM customer_source_addresses WHERE id=? AND user_id=?', sid, user['id'])
    if not s: raise ApiError(404, 'not_found', 'Address not found')
    n = dict(one(db, 'SELECT * FROM blockchain_networks WHERE id=?', s['network_id']))
    if not providers.chain.verify_ownership(n['network_code'], s['address'], s['challenge'], signature or ''): raise ApiError(422, 'proof_invalid', 'That signature does not prove control of this address.')
    _verify_source(db, dict(s), 'signed_message')
    return source_view(dict(one(db, 'SELECT * FROM customer_source_addresses WHERE id=?', sid)))

def admin_verify_source(db, actor, sid, reason):
    reason = cw._reason(reason, 10)
    s = one(db, 'SELECT * FROM customer_source_addresses WHERE id=?', sid)
    if not s: raise ApiError(404, 'not_found', 'Address not found')
    if s['user_id'] == actor['id']: raise ApiError(403, 'separation_of_duties', 'You cannot verify your own address.')
    _verify_source(db, dict(s), 'staff_review', actor['id'], reason)

def revoke_source(db, actor_id, user_id, sid, reason=None):
    with tx(db):
        s = one(db, 'SELECT * FROM customer_source_addresses WHERE id=? AND user_id=?', sid, user_id)
        if not s: raise ApiError(404, 'not_found', 'Address not found')
        db.execute("UPDATE customer_source_addresses SET status='revoked',note=? WHERE id=?", (reason, sid)); audit(db, actor_id, 'crypto.source_address_revoked', 'customer_source_address', sid, {'status': s['status']}, {'status': 'revoked'}, reason)
        mail.emit(db, 'security.wallet_changed', user_id, f'{sid}:removed', address_id=sid, action='Address removed', at=util.now())

def list_sources(db, user_id):
    return [dict(source_view(dict(s)), network=s['network_code']) for s in db.execute(
        "SELECT s.*,n.network_code FROM customer_source_addresses s JOIN blockchain_networks n ON n.id=s.network_id WHERE s.user_id=? AND s.status IN('pending','verified') ORDER BY s.id DESC", (user_id,))]

# =====================================================================================================
#  Attribution
# =====================================================================================================
def attribute(db, dep, info, wallet):
    """-> {'user_id','intent_id','method','note'} on success, or {'user_id': None, 'reason': ...}. Never guesses; never looks at the amount."""
    if dep.get('attribution_method') == 'manual' and dep.get('user_id'): return {'user_id': dep['user_id'], 'intent_id': dep.get('intent_id'), 'method': 'manual', 'note': dep.get('attribution_note')}
    if info['attribution_mode'] == 'none': return {'user_id': None, 'reason': 'unmatched: this network is configured for manual attribution only'}
    if wallet['status'] == 'retired': return {'user_id': None, 'reason': 'unmatched: deposit was sent to a retired wallet address'}
    limit = util.ts_after(hours=-_grace(db)); now = util.now()
    def usable(i):   # open (or lapsed within the grace window), same asset-network and wallet, created before the transfer was detected
        return i and i['asset_network_id'] == info['an_id'] and i['company_wallet_id'] == wallet['id'] and i['status'] in ('open', 'expired') and i['created_at'] <= dep['detected_at'] and i['expires_at'] >= limit
    by_memo = by_src = None; memo = (dep.get('memo') or '').strip().upper(); note = []
    if memo and info['attribution_mode'] == 'memo_or_source':
        i = one(db, 'SELECT * FROM deposit_intents WHERE reference_code=?', memo)
        if i:
            if not usable(dict(i)): return {'user_id': None, 'reason': f"unmatched: memo references intent {i['ref']} which is {i['status']}, on another asset/wallet, or outside its time window"}
            by_memo = dict(i)
        else: note.append('memo did not match any intent')
    if dep.get('sender'):
        s = one(db, "SELECT * FROM customer_source_addresses WHERE network_id=? AND address_key=? AND status='verified'", dep['network_id'], ca.address_key(info['address_format'], dep['sender']))
        if s:
            i = one(db, "SELECT * FROM deposit_intents WHERE user_id=? AND asset_network_id=? AND company_wallet_id=? AND status IN('open','expired') ORDER BY id DESC LIMIT 1", s['user_id'], info['an_id'], wallet['id'])
            if i and usable(dict(i)): by_src = dict(i)
            elif not by_memo: return {'user_id': None, 'reason': 'unmatched: sender is a verified address but the customer has no open deposit intent for this asset'}
    if by_memo and by_src and by_memo['user_id'] != by_src['user_id']: return {'user_id': None, 'reason': 'attribution_conflict: memo and verified sender point to different customers'}
    hit = by_memo or by_src
    if not hit: return {'user_id': None, 'reason': 'unmatched: no memo reference and the sender is not a verified address of any customer' + (f" ({'; '.join(note)})" if note else '')}
    return {'user_id': hit['user_id'], 'intent_id': hit['id'], 'method': 'memo' if by_memo else 'verified_source', 'note': '; '.join(note) or None}

# =====================================================================================================
#  Ingestion
# =====================================================================================================
def _ev(db, dep_id, frm, to, note=None, actor=None):
    db.execute('INSERT INTO crypto_deposit_events(deposit_id,from_status,to_status,note,actor_id,created_at) VALUES(?,?,?,?,?,?)', (dep_id, frm, to, note, actor, util.now()))

def _set(db, dep, new, reason=None, actor=None, **cols):
    old = dep['status']
    if new != old and new not in ALLOWED.get(old, set()): raise ApiError(409, 'invalid_transition', f'Deposit cannot move from {old} to {new}.')
    sets = {'status': new, 'status_reason': reason, 'updated_at': util.now(), **cols}
    db.execute('UPDATE crypto_deposits SET ' + ','.join(f'{k}=?' for k in sets) + ' WHERE id=?', (*sets.values(), dep['id']))
    dep.update(sets)
    if new != old or reason: _ev(db, dep['id'], old, new, reason, actor)
    if new != old and dep.get('user_id'):
        ev = {'unconfirmed': 'deposit.detected', 'confirming': 'deposit.detected', 'credited': 'deposit.confirmed', 'risk_review': 'deposit.review', 'manual_review': 'deposit.review',
              'rejected': 'deposit.failed', 'failed': 'deposit.failed'}.get(new)
        if ev: mail.emit(db, ev, dep['user_id'], dep['ref'])      # internal status_reason is deliberately NOT passed: it can describe risk rules

def _wallets_at(db, fmt, address):
    rows = db.execute("SELECT w.*,an.network_id,an.contract_address,an.id an_id FROM company_crypto_wallets w JOIN asset_networks an ON an.id=w.asset_network_id WHERE lower(w.public_address)=lower(?) AND w.public_address<>'NOT_CONFIGURED' AND w.wallet_type IN('deposit','customer_deposit')", (address,))
    return [dict(w) for w in rows if ca.address_key(fmt, w['public_address']) == ca.address_key(fmt, address)]

def _is_company_address(db, net, address):
    k = ca.address_key(net['address_format'], address)
    return any(ca.address_key(net['address_format'], r['public_address']) == k for r in db.execute("SELECT w.public_address FROM company_crypto_wallets w JOIN asset_networks an ON an.id=w.asset_network_id WHERE an.network_id=? AND lower(w.public_address)=lower(?)", (net['id'], address)))

def _record_movement(db, net, tr, h):
    """Record an on-chain Vaulta-to-Vaulta transfer in wallet_movements (separate from customer deposits). Idempotent per tx hash + event."""
    ref = f'{h}:{tr.event_index}'
    if one(db, 'SELECT 1 FROM wallet_movements WHERE tx_hash=?', ref): return
    f = one(db, "SELECT w.id,w.asset_network_id FROM company_crypto_wallets w JOIN asset_networks an ON an.id=w.asset_network_id WHERE an.network_id=? AND lower(w.public_address)=lower(?) ORDER BY w.id DESC LIMIT 1", net['id'], tr.sender)
    t = one(db, "SELECT w.id,w.asset_network_id FROM company_crypto_wallets w JOIN asset_networks an ON an.id=w.asset_network_id WHERE an.network_id=? AND lower(w.public_address)=lower(?) ORDER BY w.id DESC LIMIT 1", net['id'], tr.recipient)
    if not f or not t or f['id'] == t['id']: return
    db.execute('INSERT INTO wallet_movements(ref,asset_network_id,from_wallet_id,to_wallet_id,amount,tx_hash,status,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
               (util.new_ref('WMV'), t['asset_network_id'], f['id'], t['id'], str(int(tr.amount)), ref, 'confirmed' if tr.confirmations else 'broadcast', 'detected on-chain between Vaulta wallets', util.now()))

def ingest_transfer(db, providers, net, tr):
    """Record one on-chain transfer event exactly once and classify it. Returns (deposit_id, created)."""
    fmt = net['address_format']; h = _norm_hash(fmt, tr.tx_hash)
    if tr.sender and _is_company_address(db, net, tr.sender):          # Vaulta -> Vaulta (sweep, treasury top-up): a wallet movement, never a customer deposit
        _record_movement(db, net, tr, h); return None, False
    ex = one(db, 'SELECT id,status FROM crypto_deposits WHERE network_id=? AND tx_hash=? AND event_index=?', net['id'], h, tr.event_index)
    if ex:
        if ex['status'] == 'reorged':      # same event re-included after a reorg: revive the existing row, never create a second
            with tx(db):
                d = dict(one(db, 'SELECT * FROM crypto_deposits WHERE id=?', ex['id'])); _set(db, d, 'detected', 'transfer re-included after reorganisation', block_number=tr.block_number, block_hash=tr.block_hash, confirmations=tr.confirmations)
        return ex['id'], False
    at = _wallets_at(db, fmt, tr.recipient)
    here = [w for w in at if w['network_id'] == net['id']]
    contract = (tr.contract or '').lower() if fmt == 'evm' else tr.contract
    status, reason, wallet, an = 'detected', None, None, None
    if not here:
        status, reason, wallet = 'wrong_network', f"received on {net['name']}, which is not a supported deposit network for this address", (at[0] if at else None)
    else:
        for w in here:
            cand = one(db, 'SELECT * FROM asset_networks WHERE id=?', w['asset_network_id'])
            if ((cand['contract_address'] or '').lower() or None) == (contract or None): an, wallet = dict(cand), w; break
        if not an:
            status, wallet = 'unsupported_asset', here[0]
            reason = ('token contract ' + str(tr.contract) + ' is not a configured asset on this network') if tr.contract else 'native asset is not configured for this wallet address'
        elif not tr.success: status, reason = 'failed', 'transaction failed on-chain'
        elif net['status'] != 'active': status, reason = 'manual_review', f"network {net['network_code']} is {net['status']}"
    with tx(db):
        try:
            did = db.execute('INSERT INTO crypto_deposits(ref,network_id,asset_network_id,company_wallet_id,tx_hash,event_index,sender,recipient,contract,memo,amount,block_number,block_hash,confirmations,status,status_reason,detected_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                             (util.new_ref('CDP'), net['id'], an['id'] if an else None, wallet['id'] if wallet else None, h, tr.event_index, tr.sender, tr.recipient, contract, tr.memo, str(int(tr.amount)),
                              tr.block_number, tr.block_hash, tr.confirmations, status, reason, util.now(), util.now())).lastrowid
        except sqlite3.IntegrityError:
            r = one(db, 'SELECT id FROM crypto_deposits WHERE network_id=? AND tx_hash=? AND event_index=?', net['id'], h, tr.event_index); return r['id'], False
        _ev(db, did, None, status, reason or 'detected on-chain')
        if status in ('unsupported_asset', 'wrong_network'):
            audit(db, None, f'crypto.deposit_{status}', 'crypto_deposit', did, None, {'network': net['network_code'], 'tx': h, 'contract': contract, 'amount': str(tr.amount)}, reason)
        return did, True

# =====================================================================================================
#  State machine
# =====================================================================================================
def _ctx(db, dep):
    wallet = cw.get_wallet(db, dep['company_wallet_id']); info = cw.an_info(db, dep['asset_network_id']); eff = cw.effective(db, wallet)
    return wallet, info, eff

def _finality(info, eff_conf, tr):
    pol = json.loads(info['finality_policy'] or '{}')
    if tr.confirmations < eff_conf: return False
    return tr.commitment == pol.get('commitment', 'finalized') if pol.get('mode') == 'commitment' else True

def _fresh(providers, net_code, dep):
    """Re-read the transfer from the chain. -> (transfer|None, problem|None)"""
    evs = providers.chain.get_transaction(net_code, dep['tx_hash'])
    if not evs: return None, 'not_found'
    ev = next((e for e in evs if e.event_index == dep['event_index']), None)
    return (ev, None) if ev else (None, 'not_found')

def _drop_received(db, dep):
    if dep.get('received_entry_id') and not dep.get('credit_entry_id'):
        from . import accounting as acc
        acc.reverse_entry(db, dep['received_entry_id'], f"deposit {dep['ref']} no longer on chain (reorg)"); dep['received_entry_id'] = None
        db.execute('UPDATE crypto_deposits SET received_entry_id=NULL WHERE id=?', (dep['id'],))

def advance(db, providers, dep_id, max_steps=10):
    """Drive one deposit as far as it can go right now. Idempotent and safe to call repeatedly or concurrently."""
    for _ in range(max_steps):
        with tx(db):
            dep = dict(one(db, 'SELECT * FROM crypto_deposits WHERE id=?', dep_id))
            if not _step(db, providers, dep): return dep
    return dict(one(db, 'SELECT * FROM crypto_deposits WHERE id=?', dep_id))

def _step(db, providers, dep):
    st = dep['status']
    if st in TERMINAL or dep['asset_network_id'] is None: return False
    net = dict(one(db, 'SELECT * FROM blockchain_networks WHERE id=?', dep['network_id']))
    wallet, info, eff = _ctx(db, dep)
    if st == 'detected': _set(db, dep, 'validating'); return True
    if st == 'validating':
        res = providers.chain.verify_transaction(net['network_code'], dep['tx_hash'], dep['event_index'], recipient=dep['recipient'], contract=dep['contract'], amount=int(dep['amount']))
        if not res.ok:
            r = ','.join(res.reasons)
            if 'transaction_failed_on_chain' in res.reasons: _set(db, dep, 'failed', r)
            elif res.reasons in (['transaction_not_found'], ['event_not_found']): _set(db, dep, 'reorged', r)
            else: _set(db, dep, 'rejected', 'independent verification failed: ' + r)
            return False
        tr = res.transfer
        if int(dep['amount']) < eff['minimum_deposit'] and not dep['override_minimum']:
            _set(db, dep, 'manual_review', f"below_minimum: {reg.from_minor(db, info['ledger_code'], dep['amount'])} < {reg.from_minor(db, info['ledger_code'], eff['minimum_deposit'])}", required_confirmations=eff['confirmations']); return True
        if wallet['status'] == 'retired': _set(db, dep, 'manual_review', 'retired_wallet: sent to a retired address', required_confirmations=eff['confirmations']); return True
        if wallet['status'] != 'active' or not wallet['is_deposit_enabled']:
            _set(db, dep, 'manual_review', f"wallet_not_accepting: wallet is {wallet['status']}", required_confirmations=eff['confirmations']); return True
        if one(db, "SELECT 1 FROM crypto_deposits WHERE network_id=? AND tx_hash=? AND id<>? AND origin='manual_request' AND status='credited'", dep['network_id'], dep['tx_hash'], dep['id']):
            _set(db, dep, 'manual_review', 'duplicate_of_manual_credit: this transaction was already credited through an approved deposit request', required_confirmations=eff['confirmations']); return True
        a = attribute(db, dep, info, wallet)
        if not a['user_id']: _set(db, dep, 'manual_review', a['reason'], required_confirmations=eff['confirmations']); return True
        cols = dict(user_id=a['user_id'], intent_id=a.get('intent_id'), attribution_method=a['method'], attribution_note=a.get('note'), required_confirmations=eff['confirmations'],
                    block_number=tr.block_number, block_hash=tr.block_hash, confirmations=tr.confirmations)
        if _finality(info, eff['confirmations'], tr): _set(db, dep, 'confirmed', None, confirmed_at=util.now(), **cols)
        else: _set(db, dep, 'confirming' if tr.confirmations else 'unconfirmed', None, **cols)
        return True
    if st in ('unconfirmed', 'confirming'):
        tr, prob = _fresh(providers, net['network_code'], dep)
        if not tr: _set(db, dep, 'reorged', 'transaction no longer visible on-chain'); return False
        if not tr.success: _set(db, dep, 'failed', 'transaction failed on-chain'); return False
        if int(tr.amount) != int(dep['amount']) or ca.address_key(net['address_format'], tr.recipient) != ca.address_key(net['address_format'], dep['recipient']): _set(db, dep, 'rejected', 'on-chain data changed after detection'); return False
        if dep['block_hash'] and tr.block_hash != dep['block_hash']:
            _ev(db, dep['id'], st, st, f"block changed: {dep['block_hash'][:12]} -> {(tr.block_hash or 'mempool')[:12]}")
            if tr.block_hash is None: _set(db, dep, 'reorged', 'transaction left its block (chain reorganisation); confirmations reset', confirmations=0, block_number=None, block_hash=None); return False
        cols = dict(confirmations=tr.confirmations, block_number=tr.block_number, block_hash=tr.block_hash)
        if _finality(info, eff['confirmations'], tr): _set(db, dep, 'confirmed', None, confirmed_at=util.now(), **cols); return True
        new = 'confirming' if tr.confirmations else 'unconfirmed'; _set(db, dep, new, None, **cols); return False
    if st == 'confirmed':
        if not dep['received_entry_id']: _post_received(db, dep, info)
        if not dep['user_id']: _set(db, dep, 'manual_review', 'unmatched'); return False
        _set(db, dep, 'risk_review'); return True
    if st == 'risk_review':
        tr, prob = _fresh(providers, net['network_code'], dep)
        if not tr or (dep['block_hash'] and tr.block_hash != dep['block_hash']) or not _finality(info, eff['confirmations'], tr):
            _drop_received(db, dep); _set(db, dep, 'reorged', 'chain reorganisation before credit; confirmation reset'); return False
        verdict, why = _risk(db, providers, dep, info, wallet)
        if verdict == 'manual': _set(db, dep, 'manual_review', why); return False
        if verdict == 'hold':
            if why != dep['status_reason']: _set(db, dep, 'risk_review', why)
            return False
        _credit(db, dep, info); return False
    if st == 'manual_review':     # funds still become ours on-chain: recognise them in suspense once final, but never credit a customer
        if dep['received_entry_id']: return False
        tr, prob = _fresh(providers, net['network_code'], dep)
        if tr and tr.success and _finality(info, eff['confirmations'], tr) and int(tr.amount) == int(dep['amount']):
            db.execute('UPDATE crypto_deposits SET confirmations=?,block_number=?,block_hash=?,confirmed_at=COALESCE(confirmed_at,?) WHERE id=?', (tr.confirmations, tr.block_number, tr.block_hash, util.now(), dep['id']))
            dep.update(confirmations=tr.confirmations, block_hash=tr.block_hash); _post_received(db, dep, info)
        return False
    if st == 'reorged':
        tr, prob = _fresh(providers, net['network_code'], dep)
        if tr and tr.block_number: _set(db, dep, 'detected', 'transfer visible again', confirmations=tr.confirmations, block_number=tr.block_number, block_hash=tr.block_hash); return True
        return False
    return False

def _post_received(db, dep, info):
    eid = cl.post_received(db, dep['id'], info['ledger_code'], int(dep['amount']), dep['ref'])
    db.execute('UPDATE crypto_deposits SET received_entry_id=? WHERE id=?', (eid, dep['id'])); dep['received_entry_id'] = eid
    _ev(db, dep['id'], dep['status'], dep['status'], 'funds recognised in custody (suspense)')

def _risk(db, providers, dep, info, wallet):
    """-> ('ok'|'hold'|'manual', reason). Fails closed: if a check cannot run, the deposit waits."""
    try: crypto_gate(providers)
    except ApiError: return 'hold', 'funding_not_authorized: live crypto funding is not enabled'
    u = _user(db, dep['user_id'])
    try: conv.usd_cents(db, info['ledger_code'], int(dep['amount']), 'credit')       # no usable price: nothing is credited and nothing is guessed; the deposit waits
    except ApiError as e: return 'hold', f'{e.code}: {e.message}'
    if setting(db, 'maintenance_mode'): return 'hold', 'maintenance: money movement is temporarily disabled'
    if u['status'] != 'active': return 'hold', f"account_{u['status']}: customer account is not active"
    if setting(db, 'crypto_requires_kyc') and u['kyc_status'] != 'verified': return 'hold', 'kyc_required: customer identity is not verified'
    if dep['sender']:
        try: clear, why = providers.compliance.screen(db, info['address_format'], ca.address_key(info['address_format'], dep['sender']))
        except ApiError as e: return 'hold', f'screening_unavailable: {e.message}'
        if not clear: return 'manual', 'blocked_sender: ' + why
    th = wallet.get('risk_review_threshold')
    if (dep['risk_approval_required'] or (th and int(dep['amount']) >= int(th))) and not dep['risk_approved_by']: return 'hold', 'second_approval_required: awaiting approval by another administrator'
    return 'ok', None

def _credit(db, dep, info, notify_user=True):
    eid = cl.post_credit(db, dep['id'], dep['user_id'], info['ledger_code'], int(dep['amount']), dep['ref'])
    cv = conv.convert_deposit(db, dep, info)               # crypto -> Account Balance, same database transaction: both happen or neither
    _set(db, dep, 'credited', None, credit_entry_id=eid, credited_at=util.now())
    if dep['intent_id']: db.execute("UPDATE deposit_intents SET status='fulfilled',closed_at=? WHERE id=? AND status IN('open','expired')", (util.now(), dep['intent_id']))
    if notify_user: notify(db, dep['user_id'], 'deposit', 'Crypto deposit credited', f"{reg.from_minor(db, info['ledger_code'], dep['amount'])} {info['symbol']} ({dep['ref']}) was converted and ${util.cstr(cv['usd_cents'])} was added to your Account Balance.", '#/deposit')

# =====================================================================================================
#  Monitoring
# =====================================================================================================
def scan_network(db, providers, net):
    """Read incoming transfers for every company address on this network (including retired ones) and ingest them."""
    chain = providers.chain; chain.prepare_network(net); fmt = net['address_format']; seen = created = 0
    addrs = {}
    for w in all_(db, "SELECT w.public_address FROM company_crypto_wallets w JOIN asset_networks an ON an.id=w.asset_network_id WHERE an.network_id=? AND w.public_address<>'NOT_CONFIGURED' AND w.wallet_type IN('deposit','customer_deposit')", net['id']): addrs[ca.address_key(fmt, w['public_address'])] = w['public_address']
    if net['status'] != 'active':      # watch-only networks: look for company addresses (of the same family) used on a network we do NOT support
        for w in all_(db, "SELECT w.public_address FROM company_crypto_wallets w JOIN asset_networks an ON an.id=w.asset_network_id JOIN blockchain_networks n ON n.id=an.network_id WHERE n.address_format=? AND n.id<>? AND n.status='active' AND w.public_address<>'NOT_CONFIGURED' AND w.wallet_type IN('deposit','customer_deposit')", fmt, net['id']):
            addrs.setdefault(ca.address_key(fmt, w['public_address']), w['public_address'])
    latest = chain.latest_block(net['network_code'])
    for k, addr in addrs.items():
        cur = one(db, 'SELECT last_block FROM chain_scan_cursors WHERE network_id=? AND address_key=?', net['id'], k)
        for tr in chain.list_incoming(net['network_code'], addr, max((cur['last_block'] if cur else 0) - REORG_RESCAN, 0)):
            did, new = ingest_transfer(db, providers, net, tr); seen += 1; created += new
        db.execute('INSERT INTO chain_scan_cursors(network_id,address_key,last_block,updated_at) VALUES(?,?,?,?) ON CONFLICT(network_id,address_key) DO UPDATE SET last_block=excluded.last_block,updated_at=excluded.updated_at', (net['id'], k, latest, util.now()))
    return {'network': net['network_code'], 'seen': seen, 'new': created}

def watch_credited(db, providers, days=7):
    """A credited deposit that later disappears from the chain is a critical incident: raise a reconciliation exception, never auto-debit."""
    n = 0
    for d in all_(db, "SELECT d.*,n.network_code FROM crypto_deposits d JOIN blockchain_networks n ON n.id=d.network_id WHERE d.status='credited' AND d.credited_at>=?", util.ts_after(days=-days)):
        tr, prob = _fresh(providers, d['network_code'], d)
        if not tr or (d['block_hash'] and tr.block_hash != d['block_hash']):
            from . import crypto_recon
            crypto_recon.raise_exception(db, f"post_credit_reorg:{d['id']}", 'post_credit_reorg', 'critical', cw.an_info(db, d['asset_network_id'])['ledger_code'],
                                         {'deposit': d['ref'], 'tx_hash': d['tx_hash'], 'user_id': d['user_id'], 'amount': d['amount'], 'note': 'credited deposit is no longer on the chain; do not release funds'}); n += 1
    return n

def run_once(db, providers):
    """One monitoring pass: scan every network, advance every open deposit, watch recent credits. Each failure is contained and reported."""
    out = {'scanned': [], 'advanced': 0, 'errors': []}
    for net in all_(db, 'SELECT * FROM blockchain_networks WHERE address_format IS NOT NULL'):
        try: out['scanned'].append(scan_network(db, providers, net))
        except ApiError as e: out['errors'].append({'network': net['network_code'], 'error': e.code})
    for d in all_(db, "SELECT id FROM crypto_deposits WHERE status IN ('detected','validating','unconfirmed','confirming','confirmed','risk_review','manual_review','reorged') AND asset_network_id IS NOT NULL ORDER BY id"):
        try: advance(db, providers, d['id']); out['advanced'] += 1
        except ApiError as e: out['errors'].append({'deposit_id': d['id'], 'error': e.code})
    try: out['post_credit_incidents'] = watch_credited(db, providers)
    except ApiError as e: out['errors'].append({'watch': e.code})
    return out

def submit_tx(db, providers, user, intent_ref, tx_hash):
    """Customer-supplied hash: ONLY a hint to look sooner. We read the chain ourselves and attribution still follows the normal rules."""
    crypto_gate(providers); u = _can_use(db, user); i = get_open_intent(db, u, intent_ref)
    info = cw.an_info(db, i['asset_network_id']); net = _net(db, info['network_code']); tx_hash = (tx_hash or '').strip()
    if not _HASH[net['address_format']].match(tx_hash): raise ApiError(422, 'invalid_tx_hash', f"That is not a valid {net['name']} transaction hash.")
    evs = providers.chain.get_transaction(net['network_code'], tx_hash)
    if not evs: raise ApiError(404, 'transaction_not_found', 'We cannot see that transaction on the network yet. It will be picked up automatically once it appears.')
    w = cw.get_wallet(db, i['company_wallet_id']); ids = []
    for ev in evs:
        if ca.address_key(net['address_format'], ev.recipient) != ca.address_key(net['address_format'], w['public_address']): continue
        did, new = ingest_transfer(db, providers, net, ev)
        if not new:
            with tx(db): _ev(db, did, None, None, f"duplicate submission of this transaction by user {u['id']}", u['id'])
        advance(db, providers, did); ids.append(did)
    if not ids: raise ApiError(422, 'not_a_deposit', 'That transaction did not pay a Vaulta deposit address for this asset.')
    return [customer_view(db, one(db, 'SELECT * FROM crypto_deposits WHERE id=?', d)) for d in ids]

# =====================================================================================================
#  Views and admin resolution
# =====================================================================================================
def customer_view(db, d):
    d = dict(d); info = cw.an_info(db, d['asset_network_id']) if d['asset_network_id'] else None
    net = dict(one(db, 'SELECT * FROM blockchain_networks WHERE id=?', d['network_id']))
    return {'ref': d['ref'], 'asset': info['symbol'] if info else 'Unsupported', 'ledger_code': info['ledger_code'] if info else None, 'amount': reg.from_minor(db, info['ledger_code'], d['amount']) if info else None,
            'network': net['name'], 'tx_hash': d['tx_hash'], 'status': d['status'], 'status_label': LABELS[d['status']], 'confirmations': d['confirmations'], 'required_confirmations': d['required_confirmations'],
            'detected_at': d['detected_at'], 'credited_at': d['credited_at'],
            'note': 'We are matching this deposit to your account. Our team will contact you if we need anything.' if d['status'] in ('manual_review', 'confirmed', 'risk_review') else None}

def history(db, user_id, limit=100):
    return [customer_view(db, d) for d in db.execute('SELECT * FROM crypto_deposits WHERE user_id=? AND asset_network_id IS NOT NULL ORDER BY id DESC LIMIT ?', (user_id, limit))]

def admin_view(db, d):
    d = dict(d); v = customer_view(db, d) if d['asset_network_id'] else {'ref': d['ref'], 'asset': 'Unsupported', 'amount': None, 'network': dict(one(db, 'SELECT * FROM blockchain_networks WHERE id=?', d['network_id']))['name'], 'tx_hash': d['tx_hash'],
                                                                            'status': d['status'], 'status_label': LABELS[d['status']], 'confirmations': d['confirmations'], 'detected_at': d['detected_at']}
    v.update({'id': d['id'], 'user_id': d['user_id'], 'sender': d['sender'], 'recipient': d['recipient'], 'contract': d['contract'], 'memo': d['memo'], 'event_index': d['event_index'], 'amount_minor': d['amount'],
              'status_reason': d['status_reason'], 'attribution_method': d['attribution_method'], 'attribution_note': d['attribution_note'], 'risk_approval_required': bool(d['risk_approval_required']), 'risk_approved_by': d['risk_approved_by'],
              'received_in_ledger': bool(d['received_entry_id']), 'block_number': d['block_number'], 'company_wallet_id': d['company_wallet_id']})
    return v

def list_admin(db, status=None, user_id=None, limit=200):
    w, a = [], []
    if status: w.append('status=?'); a.append(status)
    if user_id: w.append('user_id=?'); a.append(user_id)
    return [admin_view(db, d) for d in db.execute('SELECT * FROM crypto_deposits' + (' WHERE ' + ' AND '.join(w) if w else '') + ' ORDER BY id DESC LIMIT ?', (*a, limit))]

def events(db, dep_id): return all_(db, 'SELECT * FROM crypto_deposit_events WHERE deposit_id=? ORDER BY id', dep_id)

def resolve(db, providers, actor, dep_id, action, reason, user_ref=None, duplicate_of=None):
    """Staff resolution of a manual_review / risk_review deposit. Every action needs a reason and is audited. Crediting still passes the normal pipeline."""
    reason = cw._reason(reason, 10)
    with tx(db):
        d = dict(one(db, 'SELECT * FROM crypto_deposits WHERE id=?', dep_id) or {})
        if not d: raise ApiError(404, 'not_found', 'Deposit not found')
        before = {'status': d['status'], 'user_id': d['user_id']}
        if action == 'attribute':
            if d['status'] != 'manual_review': raise ApiError(409, 'invalid_state', 'Only deposits in manual review can be attributed.')
            u = one(db, 'SELECT * FROM users WHERE public_id=? OR email=?', user_ref, str(user_ref).lower())
            if not u: raise ApiError(404, 'not_found', 'Customer not found')
            if u['id'] == actor['id']: raise ApiError(403, 'separation_of_duties', 'You cannot attribute a deposit to yourself.')
            db.execute("UPDATE crypto_deposits SET user_id=?,attribution_method='manual',attribution_note=?,risk_approval_required=1,risk_approved_by=NULL,override_minimum=? WHERE id=?",
                       (u['id'], reason, 1 if (d['status_reason'] or '').startswith('below_minimum') else 0, dep_id))
            d.update(user_id=u['id'], attribution_method='manual'); _set(db, d, 'validating', 'manually attributed by staff; a second administrator must approve before credit', actor['id'])
        elif action == 'retry':
            if d['status'] != 'manual_review': raise ApiError(409, 'invalid_state', 'Only deposits in manual review can be retried.')
            db.execute('UPDATE crypto_deposits SET user_id=NULL,attribution_method=NULL,intent_id=NULL WHERE id=?', (dep_id,)); d['user_id'] = None; d['attribution_method'] = None
            _set(db, d, 'validating', 'attribution re-evaluated by staff', actor['id'])
        elif action == 'accept_below_minimum':
            if d['status'] != 'manual_review' or not (d['status_reason'] or '').startswith('below_minimum'): raise ApiError(409, 'invalid_state', 'This deposit is not below the minimum.')
            db.execute('UPDATE crypto_deposits SET override_minimum=1 WHERE id=?', (dep_id,)); _set(db, d, 'validating', 'minimum waived by staff', actor['id'])
        elif action == 'approve_risk':
            if d['status'] != 'risk_review': raise ApiError(409, 'invalid_state', 'Only deposits awaiting risk approval can be approved.')
            if d['user_id'] == actor['id']: raise ApiError(403, 'separation_of_duties', 'You cannot approve a deposit to your own account.')
            if d['attribution_method'] == 'manual' and (one(db, 'SELECT 1 FROM crypto_deposit_events WHERE deposit_id=? AND actor_id=? AND note LIKE ?', dep_id, actor['id'], 'manually attributed%')): raise ApiError(403, 'separation_of_duties', 'The administrator who attributed a deposit cannot also approve it.')
            db.execute('UPDATE crypto_deposits SET risk_approved_by=? WHERE id=?', (actor['id'], dep_id)); _ev(db, dep_id, d['status'], d['status'], 'risk approved: ' + reason, actor['id'])
        elif action == 'reject':
            if d['status'] not in ('manual_review', 'risk_review'): raise ApiError(409, 'invalid_state', 'Only deposits under review can be rejected.')
            if d['status'] == 'risk_review': _set(db, d, 'manual_review', 'staff rejection', actor['id']); d = dict(one(db, 'SELECT * FROM crypto_deposits WHERE id=?', dep_id))
            _set(db, d, 'rejected', reason, actor['id'])
        elif action == 'mark_duplicate':
            o = one(db, 'SELECT id FROM crypto_deposits WHERE id=?', duplicate_of)
            if not o or o['id'] == dep_id: raise ApiError(422, 'invalid_duplicate_of', 'duplicate_of must reference a different deposit')
            if d['status'] != 'manual_review': raise ApiError(409, 'invalid_state', 'Only deposits in manual review can be marked duplicate.')
            _set(db, d, 'duplicate', f'duplicate of deposit {o["id"]}: {reason}', actor['id'])
        else: raise ApiError(422, 'invalid_action', 'Unknown action')
        audit(db, actor['id'], f'crypto.deposit_{action}', 'crypto_deposit', dep_id, before, {'status': dict(one(db, 'SELECT status,user_id FROM crypto_deposits WHERE id=?', dep_id))}, reason)
    return admin_view(db, advance(db, providers, dep_id))


# =====================================================================================================
#  Deposit requests: the customer proves a payment with a screenshot (+ TXID and amount); staff verify and approve.
#  A screenshot is SUPPORTING EVIDENCE ONLY. Approval needs either an independent chain verification, or an explicit,
#  audited attestation by the administrator when no chain provider can verify. Credit goes through the same ledger postings
#  as on-chain deposits, so there is still exactly one funding system, one ledger and one set of duplicate-credit guards.
# =====================================================================================================
EVIDENCE_DIR = 'deposit-evidence'
REQ_STAGES = ['Submitted', 'In review', 'Decision', 'Credited']

def _rev(db, rid, event, actor=None, note=None):
    db.execute('INSERT INTO deposit_request_events(request_id,event,actor_id,note,created_at) VALUES(?,?,?,?,?)', (rid, event, actor, note, util.now()))

def _req(db, rid=None, ref=None):
    r = one(db, 'SELECT * FROM deposit_requests WHERE ' + ('id=?' if rid is not None else 'ref=?'), rid if rid is not None else ref)
    if not r: raise ApiError(404, 'not_found', 'Deposit request not found')
    return dict(r)

def request_view(db, r, admin=False):
    info = cw.an_info(db, r['asset_network_id']); a = one(db, 'SELECT * FROM crypto_assets WHERE id=?', info['asset_id']); c = info['ledger_code']
    stage = {'pending': 1, 'approved': 3, 'rejected': 2, 'cancelled': 2}[r['status']]
    v = {'ref': r['ref'], 'kind': 'deposit', 'status': r['status'], 'asset': info['symbol'], 'asset_name': info['asset_name'], 'asset_id': a['id'], 'logo': cat.logo_url(a), 'ledger_code': c, 'network': info['network_name'], 'network_code': info['network_code'],
         'amount': reg.from_minor(db, c, r['claimed_amount']), 'verified_amount': reg.from_minor(db, c, r['verified_amount']) if r['verified_amount'] else None, 'tx_hash': r['claimed_txid'], 'destination_address': r['destination_address'],
         'submitted_at': r['submitted_at'], 'reviewed_at': r['reviewed_at'], 'rejection_reason': r['rejection_reason'], 'has_screenshot': bool(r['screenshot_name']), 'can_cancel': r['status'] == 'pending',
         'stage': stage, 'stages': REQ_STAGES, 'stage_state': {'pending': 'active', 'approved': 'done', 'rejected': 'failed', 'cancelled': 'failed'}[r['status']]}
    if admin:
        u = one(db, 'SELECT u.id,u.public_id,u.email,u.status,u.kyc_status,p.first_name,p.last_name FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE u.id=?', r['user_id'])
        v.update({'id': r['id'], 'user': {'id': u['id'], 'public_id': u['public_id'], 'email': u['email'], 'name': ' '.join(x for x in (u['first_name'], u['last_name']) if x), 'status': u['status'], 'kyc_status': u['kyc_status']},
                  'sender_address': r['sender_address'], 'user_note': r['user_note'], 'admin_notes': r['admin_notes'], 'verification_method': r['verification_method'], 'reviewed_by': r['reviewed_by'],
                  'screenshot': {'mime': r['screenshot_mime'], 'size': r['screenshot_size'], 'sha256': r['screenshot_sha256'], 'original_name': r['screenshot_original']} if r['screenshot_name'] else None,
                  'evidence_duplicate_of': one(db, 'SELECT ref FROM deposit_requests WHERE id=?', r['evidence_duplicate_of'])['ref'] if r['evidence_duplicate_of'] else None, 'deposit_ref': one(db, 'SELECT ref FROM crypto_deposits WHERE id=?', r['deposit_id'])['ref'] if r['deposit_id'] else None})
    return v

def submit_request(db, providers, user, root, *, intent_ref, network_code, amount, txid=None, sender=None, note=None, screenshot=None, original_name=None, idem=None):
    crypto_gate(providers); u = _can_use(db, user); i = get_open_intent(db, u, intent_ref)
    if i['status'] != 'open': raise ApiError(409, 'intent_closed', 'This deposit session has ended. Choose the asset again to get a fresh deposit address.')
    info = cw.an_info(db, i['asset_network_id']); c = info['ledger_code']
    if (network_code or '').strip() != info['network_code']: raise ApiError(422, 'network_mismatch', f"This deposit is for the {info['network_name']} network. Select the network you actually sent on.")
    net = _net(db, info['network_code']); info2, w = _deposit_wallet(db, providers, c)
    if w['id'] != i['company_wallet_id']: raise ApiError(409, 'wallet_changed', 'The deposit address changed. Start again to get the current address before sending.')
    amt = reg.to_minor(db, c, amount)
    if amt <= 0: raise ApiError(422, 'invalid_amount', 'Enter the amount you sent.')
    mn = cw.effective(db, w)['minimum_deposit']; mx = one(db, 'SELECT max_deposit FROM asset_networks WHERE id=?', info['an_id'])['max_deposit']
    if amt < mn: raise ApiError(422, 'below_minimum', f"The minimum {info['symbol']} deposit is {reg.from_minor(db, c, mn)}.")
    if mx and amt > int(mx): raise ApiError(422, 'above_maximum', f"The maximum {info['symbol']} deposit is {reg.from_minor(db, c, mx)}.")
    txid = (txid or '').strip() or None; key = None
    if txid:
        if not _HASH[net['address_format']].match(txid): raise ApiError(422, 'invalid_tx_hash', f"That is not a valid {net['name']} transaction hash.")
        key = _norm_hash(net['address_format'], txid)
    sender = (sender or '').strip() or None
    if sender:
        try: ca.validate(net['address_format'], sender, testnet=bool(net['is_testnet']))
        except ca.InvalidAddress as e: raise ApiError(422, 'invalid_address', f"The sending address is not a valid {net['name']} address ({e}).")
    need_shot = bool(setting(db, 'crypto_screenshot_required'))
    if screenshot is None and need_shot: raise ApiError(422, 'screenshot_required', 'Upload a screenshot of your transaction.')
    mime = digest = None
    if screenshot is not None: mime, _, _ = uploads.validate_screenshot(screenshot); digest = hashlib.sha256(screenshot).hexdigest()
    note = util.text(note, 'note', 500) if (note or '').strip() else None
    replay = idem and one(db, "SELECT 1 FROM idempotency_keys WHERE user_id=? AND key=? AND scope='deposit_request'", u['id'], idem)
    name = None
    if screenshot is not None and not replay: name, _ = uploads.store(root, EVIDENCE_DIR, screenshot, mime)
    def run():
        if val(db, "SELECT COUNT(*) FROM deposit_requests WHERE user_id=? AND status='pending'", u['id']) >= setting(db, 'crypto_max_pending_requests'):
            raise ApiError(429, 'too_many_pending', 'You already have several deposits awaiting review. Wait for a decision before submitting more.')
        if key:
            if one(db, "SELECT 1 FROM deposit_requests WHERE network_id=? AND txid_key=? AND status IN('pending','approved')", net['id'], key): raise ApiError(409, 'duplicate_transaction', 'A deposit request for this transaction already exists.')
            if one(db, "SELECT 1 FROM crypto_deposits WHERE network_id=? AND tx_hash=? AND status='credited'", net['id'], key): raise ApiError(409, 'already_credited', 'This transaction has already been credited.')
        dup = one(db, 'SELECT id FROM deposit_requests WHERE screenshot_sha256=? ORDER BY id LIMIT 1', digest) if digest else None
        ref = util.new_ref('DRQ')
        rid = db.execute('INSERT INTO deposit_requests(ref,user_id,intent_id,asset_network_id,network_id,company_wallet_id,destination_address,claimed_amount,claimed_txid,txid_key,sender_address,user_note,screenshot_name,screenshot_mime,screenshot_size,screenshot_sha256,screenshot_original,evidence_duplicate_of,submitted_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                         (ref, u['id'], i['id'], info['an_id'], net['id'], w['id'], w['public_address'], str(amt), txid, key, sender, note, name, mime, len(screenshot) if screenshot else None, digest,
                          (original_name or '')[:120] or None, dup['id'] if dup else None, util.now(), util.now())).lastrowid
        _rev(db, rid, 'submitted', u['id'], None)
        mail.emit(db, 'deposit.pending', u['id'], ref)
        return {'request': request_view(db, _req(db, rid))}
    try:
        with tx(db): return ledger.idempotent(db, u['id'], idem, 'deposit_request', {'i': intent_ref, 'a': str(amt), 't': key, 's': digest}, run)
    except BaseException:
        if name: uploads.remove(root, EVIDENCE_DIR, name)
        raise

def cancel_request(db, user, ref):
    with tx(db):
        r = _req(db, ref=ref)
        if r['user_id'] != user['id']: raise ApiError(404, 'not_found', 'Deposit request not found')
        if db.execute("UPDATE deposit_requests SET status='cancelled',updated_at=? WHERE id=? AND status='pending'", (util.now(), r['id'])).rowcount != 1: raise ApiError(409, 'invalid_state', 'This request can no longer be cancelled.')
        _rev(db, r['id'], 'cancelled', user['id'], 'withdrawn by customer')

def user_requests(db, user_id, limit=100): return [request_view(db, dict(r)) for r in db.execute('SELECT * FROM deposit_requests WHERE user_id=? ORDER BY id DESC LIMIT ?', (user_id, limit))]

def user_request(db, user_id, ref):
    r = _req(db, ref=ref)
    if r['user_id'] != user_id: raise ApiError(404, 'not_found', 'Deposit request not found')       # 404, not 403: do not reveal other customers' references
    return r

def evidence(db, root, r):
    if not r['screenshot_name']: raise ApiError(404, 'not_found', 'No screenshot attached')
    return uploads.load(root, EVIDENCE_DIR, r['screenshot_name']), r['screenshot_mime']

# ---------------------------------------------------------------- independent chain verification
def chain_check(db, providers, r):
    """Look the transaction up ourselves. status: no_txid | unavailable | not_found | mismatch | unconfirmed | verified | already_credited"""
    info = cw.an_info(db, r['asset_network_id']); net = _net(db, info['network_code']); w = cw.get_wallet(db, r['company_wallet_id']); eff = cw.effective(db, w); fmt = net['address_format']
    out = {'status': 'no_txid', 'network': net['name'], 'expected_address': r['destination_address'], 'expected_contract': info['contract_address'], 'required_confirmations': eff['confirmations'], 'reasons': [],
           'claimed_amount': reg.from_minor(db, info['ledger_code'], r['claimed_amount']), 'current_wallet_address': w['public_address'], 'wallet_status': w['status']}
    if not r['txid_key']: out['reasons'].append('No transaction hash was supplied, so the blockchain cannot be checked.'); return out
    try: evs = providers.chain.get_transaction(net['network_code'], r['txid_key'])
    except ApiError as e:
        if e.code == 'provider_not_configured': out.update(status='unavailable'); out['reasons'].append('No blockchain provider is connected, so this transaction cannot be verified automatically.'); return out
        raise
    if not evs: out.update(status='not_found'); out['reasons'].append('The transaction is not visible on the network (yet).'); return out
    ours = [e for e in evs if ca.address_key(fmt, e.recipient) == ca.address_key(fmt, r['destination_address']) and (e.contract or '').lower() == (info['contract_address'] or '').lower()]
    if not ours:
        out.update(status='mismatch'); out['reasons'].append('The transaction exists but does not pay this Vaulta address in this asset on this network (wrong address, asset or network).'); return out
    ev = next((e for e in ours if int(e.amount) == int(r['claimed_amount'])), ours[0] if len(ours) == 1 else None)
    if ev is None: out.update(status='mismatch'); out['reasons'].append('Several transfers to Vaulta in this transaction and none matches the claimed amount.'); return out
    if cd_row := one(db, "SELECT status FROM crypto_deposits WHERE network_id=? AND tx_hash=? AND event_index=?", net['id'], _norm_hash(fmt, ev.tx_hash), ev.event_index):
        if cd_row['status'] == 'credited': out.update(status='already_credited'); out['reasons'].append('This transfer has already been credited.'); return out
    out.update(event_index=ev.event_index, amount=str(int(ev.amount)), amount_display=reg.from_minor(db, info['ledger_code'], ev.amount), confirmations=ev.confirmations, block_number=ev.block_number, sender=ev.sender,
               amount_matches_claim=int(ev.amount) == int(r['claimed_amount']))
    if not ev.success: out.update(status='mismatch'); out['reasons'].append('The transaction failed on-chain.'); return out
    out['status'] = 'verified' if _finality(info, eff['confirmations'], ev) else 'unconfirmed'
    if out['status'] == 'unconfirmed': out['reasons'].append(f"{ev.confirmations} of {eff['confirmations']} required confirmations.")
    return out

def admin_detail(db, providers, rid):
    r = _req(db, rid); v = request_view(db, r, admin=True)
    try: chk = chain_check(db, providers, r)
    except ApiError as e: chk = {'status': 'unavailable', 'reasons': [e.message]}
    other = [x['ref'] for x in db.execute('SELECT ref FROM deposit_requests WHERE id<>? AND (screenshot_sha256=? OR (txid_key IS NOT NULL AND txid_key=? AND network_id=?)) ORDER BY id', (r['id'], r['screenshot_sha256'], r['txid_key'], r['network_id']))]
    return {'request': v, 'chain_check': chk, 'events': all_(db, 'SELECT event,actor_id,note,created_at FROM deposit_request_events WHERE request_id=? ORDER BY id', r['id']), 'related_requests': other,
            'destination_matches_current_wallet': ca.address_key('evm', r['destination_address']) == ca.address_key('evm', chk.get('current_wallet_address', '')) if chk.get('current_wallet_address') else None}

def list_requests(db, status=None, q=None, symbol=None, page=1, size=25):
    w, a = [], []
    if status: w.append('r.status=?'); a.append(status)
    if symbol: w.append('r.asset_network_id IN(SELECT an.id FROM asset_networks an JOIN crypto_assets c ON c.id=an.asset_id WHERE c.symbol=?)'); a.append(symbol.upper())
    if q:
        w.append('(r.ref LIKE ? OR r.claimed_txid LIKE ? OR u.email LIKE ? OR u.public_id LIKE ? OR r.destination_address LIKE ?)'); a += [f'%{q}%'] * 5
    wh = (' WHERE ' + ' AND '.join(w)) if w else ''; base = 'FROM deposit_requests r JOIN users u ON u.id=r.user_id' + wh
    total = val(db, 'SELECT COUNT(*) ' + base, *a)
    rows = [request_view(db, dict(x), admin=True) for x in db.execute('SELECT r.* ' + base + ' ORDER BY r.id DESC LIMIT ? OFFSET ?', (*a, size, (page - 1) * size))]
    counts = {r['status']: r['n'] for r in db.execute('SELECT status,COUNT(*) n FROM deposit_requests GROUP BY status')}
    return {'requests': rows, 'total': total, 'page': page, 'size': size, 'counts': counts, 'assets': [r['symbol'] for r in db.execute('SELECT symbol FROM crypto_assets ORDER BY id')]}

# ---------------------------------------------------------------- decisions
def approve_request(db, providers, actor, rid, note=None, verified_amount=None, attest=False):
    """Approve exactly once. Serialised by BEGIN IMMEDIATE and guarded by a compare-and-set on status='pending', so a double-click, a retry or two
    administrators acting at once can never credit twice. Everything (ledger entries, deposit row, request decision, intent) commits or rolls back together."""
    crypto_gate(providers); note = (note or '').strip(); r0 = _req(db, rid)
    if r0['status'] != 'pending': raise ApiError(409, 'invalid_state', f"This request is already {r0['status']}; it cannot be approved again.")
    if r0['user_id'] == actor['id']: raise ApiError(403, 'separation_of_duties', 'You cannot approve your own deposit.')
    chk = chain_check(db, providers, r0); st = chk['status']
    if st == 'already_credited': raise ApiError(409, 'already_credited', 'This transaction has already been credited.')
    if st == 'mismatch': raise ApiError(409, 'transaction_mismatch', ' '.join(chk['reasons']) + ' Reject the request or ask the customer for the correct details.')
    if st == 'not_found': raise ApiError(409, 'transaction_not_found', 'The transaction is not visible on the network yet. Wait for it to appear, or reject the request.')
    if st == 'unconfirmed': raise ApiError(409, 'insufficient_confirmations', ' '.join(chk['reasons']) + ' Approve once it is final.')
    info = cw.an_info(db, r0['asset_network_id']); c = info['ledger_code']
    if info['an_status'] != 'active' or not info['deposit_enabled'] or info['asset_status'] != 'active': raise ApiError(409, 'asset_unavailable', f"{info['symbol']} deposits are disabled. Re-enable the asset or reject the request.")
    if st == 'verified':
        method = 'chain'; amt = int(chk['amount'])
        if amt != int(r0['claimed_amount']) and len(note) < 10: raise ApiError(422, 'note_required', f"The on-chain amount ({chk['amount_display']}) differs from the claimed amount. Add a note explaining the decision.")
    else:
        method = 'manual_attestation'
        if not attest: raise ApiError(409, 'verification_required', ' '.join(chk['reasons']) + ' To approve anyway you must confirm you verified the transfer independently (for example on a block explorer) and enter the verified amount.')
        if len(note) < 20: raise ApiError(422, 'note_required', 'Describe how you verified the transfer (at least 20 characters).')
        if verified_amount in (None, ''): raise ApiError(422, 'verified_amount_required', 'Enter the amount you verified.')
        amt = reg.to_minor(db, c, verified_amount)
        if amt <= 0: raise ApiError(422, 'invalid_amount', 'The verified amount must be positive.')
    with tx(db):
        r = _req(db, rid)
        if db.execute("UPDATE deposit_requests SET updated_at=? WHERE id=? AND status='pending'", (util.now(), rid)).rowcount != 1:
            raise ApiError(409, 'invalid_state', f"This request is already {r['status']}; it cannot be approved again.")
        net = _net(db, info['network_code']); w = cw.get_wallet(db, r['company_wallet_id'])
        if method == 'chain':
            ev = next(e for e in providers.chain.get_transaction(net['network_code'], r['txid_key']) if e.event_index == chk['event_index'])
            did, created = ingest_transfer(db, providers, net, ev)
            if did is None: raise ApiError(409, 'transaction_mismatch', 'This is an internal Vaulta wallet movement, not a customer deposit.')
            dep = dict(one(db, 'SELECT * FROM crypto_deposits WHERE id=?', did))
            if dep['status'] == 'credited': raise ApiError(409, 'already_credited', 'This transaction has already been credited.')
            if dep['user_id'] and dep['user_id'] != r['user_id']: raise ApiError(409, 'attributed_elsewhere', 'This transaction is already attributed to a different customer.')
            if dep['asset_network_id'] is None or dep['status'] in ('wrong_network', 'unsupported_asset', 'failed', 'rejected', 'duplicate', 'reorged'): raise ApiError(409, 'transaction_mismatch', f"The transfer is classified as {dep['status']} and cannot be credited.")
            db.execute("UPDATE crypto_deposits SET user_id=?,intent_id=?,attribution_method='manual',attribution_note=?,risk_approved_by=?,override_minimum=1,origin='manual_request' WHERE id=?", (r['user_id'], r['intent_id'], f"approved via deposit request {r['ref']}", actor['id'], did))
            dep.update(user_id=r['user_id'], attribution_method='manual')
            if dep['status'] == 'manual_review': _set(db, dep, 'validating', f"approved via deposit request {r['ref']}", actor['id'])
            dep = advance(db, providers, did)
            if dep['status'] != 'credited': raise ApiError(409, 'cannot_credit', f"The deposit could not be credited yet ({dep['status']}: {dep['status_reason'] or 'pending checks'}).")
        else:
            h = r['txid_key'] or f"manual:{r['ref']}"
            if one(db, 'SELECT 1 FROM crypto_deposits WHERE network_id=? AND tx_hash=?', net['id'], h): raise ApiError(409, 'already_credited', 'This transaction is already recorded in the on-chain deposit queue. Resolve it there instead.')
            did = db.execute("INSERT INTO crypto_deposits(ref,network_id,asset_network_id,company_wallet_id,user_id,intent_id,tx_hash,event_index,sender,recipient,contract,amount,confirmations,required_confirmations,status,status_reason,attribution_method,attribution_note,override_minimum,risk_approved_by,detected_at,confirmed_at,updated_at,origin) VALUES(?,?,?,?,?,?,?,0,?,?,?,?,0,?,'risk_review',?, 'manual',?,1,?,?,?,?,'manual_request')",
                             (util.new_ref('CDP'), net['id'], info['an_id'], w['id'], r['user_id'], r['intent_id'], h, r['sender_address'], w['public_address'], (info['contract_address'] or '').lower() or None, str(amt), cw.effective(db, w)['confirmations'],
                              'manually attested by administrator', f"approved via deposit request {r['ref']} (manual attestation)", actor['id'], util.now(), util.now(), util.now())).lastrowid
            _ev(db, did, None, 'risk_review', f"manual attestation for request {r['ref']}", actor['id'])
            dep = dict(one(db, 'SELECT * FROM crypto_deposits WHERE id=?', did)); _post_received(db, dep, info)
            verdict, why = _risk(db, providers, dep, info, w)
            if verdict != 'ok': raise ApiError(409, 'cannot_credit', f'The deposit cannot be credited yet: {why}')
            _credit(db, dep, info, notify_user=False)
        detail = {k: chk.get(k) for k in ('status', 'confirmations', 'block_number', 'sender', 'amount_display', 'amount_matches_claim', 'reasons')}
        if db.execute("UPDATE deposit_requests SET status='approved',verified_amount=?,verification_method=?,verification_detail=?,admin_notes=?,reviewed_by=?,reviewed_at=?,deposit_id=?,updated_at=? WHERE id=? AND status='pending'",
                      (str(amt), method, util.jdump(detail), note or None, actor['id'], util.now(), did, util.now(), rid)).rowcount != 1: raise ApiError(409, 'invalid_state', 'This request was decided by someone else.')
        _rev(db, rid, 'approved', actor['id'], note or None)
        audit(db, actor['id'], 'crypto.deposit_request_approved', 'deposit_request', rid, {'status': 'pending'}, {'status': 'approved', 'amount': str(amt), 'method': method, 'deposit': did, 'user_id': r['user_id']}, note or None)
        notify(db, r['user_id'], 'deposit', 'Deposit approved', f"Your {reg.from_minor(db, c, amt)} {info['symbol']} deposit {r['ref']} was approved and credited to your account.", '#/deposit')
    return admin_detail(db, providers, rid)['request']

def reject_request(db, actor, rid, reason, note=None):
    reason = (reason or '').strip()
    if len(reason) < 10: raise ApiError(422, 'reason_required', 'A rejection reason of at least 10 characters is required; the customer will see it.')
    with tx(db):
        r = _req(db, rid)
        if r['user_id'] == actor['id']: raise ApiError(403, 'separation_of_duties', 'You cannot review your own deposit.')
        if db.execute("UPDATE deposit_requests SET status='rejected',rejection_reason=?,admin_notes=?,reviewed_by=?,reviewed_at=?,updated_at=? WHERE id=? AND status='pending'", (util.text(reason, 'reason', 500), (note or '').strip() or None, actor['id'], util.now(), util.now(), rid)).rowcount != 1:
            raise ApiError(409, 'invalid_state', f"This request is already {r['status']}; it cannot be rejected.")
        _rev(db, rid, 'rejected', actor['id'], reason)
        audit(db, actor['id'], 'crypto.deposit_request_rejected', 'deposit_request', rid, {'status': 'pending'}, {'status': 'rejected', 'user_id': r['user_id']}, reason)
        info = cw.an_info(db, r['asset_network_id'])
        notify(db, r['user_id'], 'deposit', 'Deposit not approved', f"Your {info['symbol']} deposit request {r['ref']} was not approved. Reason: {reason} You may submit a new request if the details were wrong.", '#/deposit')
        mail.emit(db, 'deposit.failed', r['user_id'], r['ref'], reason=reason)
    return request_view(db, _req(db, rid), admin=True)

def note_evidence_view(db, actor, rid, download=False):
    audit(db, actor['id'], 'crypto.deposit_evidence_downloaded' if download else 'crypto.deposit_evidence_viewed', 'deposit_request', rid, None, None, None)


# =====================================================================================================
#  One funding history for the customer (requests + on-chain detections + withdrawals), also merged into the Transactions page.
#  Deliberately NOT written into the legacy `transactions` table: that table is USD-cents and is mirrored into the USD cash ledger by accounting.backfill.
# =====================================================================================================
_DISPLAY = {'pending': 'pending', 'approved': 'completed', 'rejected': 'failed', 'cancelled': 'cancelled'}
_CHAIN_DISPLAY = {'credited': 'completed', 'rejected': 'failed', 'failed': 'failed', 'wrong_network': 'failed', 'unsupported_asset': 'failed', 'duplicate': 'failed'}
_WD_DISPLAY = {'pending_approval': 'pending', 'approved': 'pending', 'signing': 'processing', 'broadcast': 'processing', 'confirming': 'processing', 'completed': 'completed', 'rejected': 'failed', 'failed': 'failed', 'cancelled': 'cancelled'}

def _usd(db, deposit_id):
    """Dollars credited to the customer's Account Balance for a credited deposit, and the rate used."""
    c = conv.for_source(db, 'crypto_deposit', deposit_id) if deposit_id else None
    return {'usd_credit': util.cstr(c['usd_cents']), 'rate': c['price']} if c else {'usd_credit': None, 'rate': None}

def funding_items(db, user_id):
    from . import crypto_withdrawals as cwd
    out = []
    for r in db.execute('SELECT * FROM deposit_requests WHERE user_id=?', (user_id,)):
        v = request_view(db, dict(r)); v.update(source='request', display_status=_DISPLAY[r['status']], created_at=r['submitted_at'], direction='credit', currency=v['asset'])
        v.update(_usd(db, r['deposit_id']))
        out.append(v)
    for d in db.execute('SELECT * FROM crypto_deposits WHERE user_id=? AND asset_network_id IS NOT NULL AND id NOT IN(SELECT deposit_id FROM deposit_requests WHERE deposit_id IS NOT NULL)', (user_id,)):
        v = customer_view(db, d); a = one(db, 'SELECT ca.* FROM crypto_assets ca JOIN asset_networks an ON an.asset_id=ca.id WHERE an.id=?', d['asset_network_id'])
        v.update(kind='deposit', source='chain', display_status=_CHAIN_DISPLAY.get(d['status'], 'pending'), created_at=d['detected_at'], direction='credit', currency=v['asset'], logo=cat.logo_url(a), asset_id=a['id'], network_code=None)
        v.update(_usd(db, d['id']))
        out.append(v)
    for w in db.execute('SELECT * FROM crypto_withdrawals WHERE user_id=?', (user_id,)):
        v = cwd.view(db, dict(w)); v.update(source='withdrawal', display_status=_WD_DISPLAY[w['status']], direction='debit', currency=v['asset']); out.append(v)
    out.sort(key=lambda x: (x['created_at'], x['ref']), reverse=True)
    return out

def as_transaction(it):
    """Shape a funding item like a legacy transaction row so the existing Transactions page can render it."""
    return {'ref': it['ref'], 'type': it['kind'], 'direction': it['direction'], 'amount': it['amount'] or '0', 'currency': it['currency'], 'status': it['display_status'], 'created_at': it['created_at'],
            'completed_at': it.get('credited_at') or it.get('reviewed_at'), 'fee': it.get('fee', '0'), 'metadata': {}, 'non_live': False, 'crypto': True, 'asset': it['asset'], 'logo': it.get('logo'),
            'network': it['network'], 'tx_hash': it.get('tx_hash'), 'raw_status': it['status'], 'reference': it.get('tx_hash'), 'usd': it.get('usd_credit') or it.get('usd_debit'), 'rate': it.get('rate')}
