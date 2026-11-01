"""Company-controlled deposit wallets: PUBLIC data only.

  * Addresses are validated against the asset's network (and the token contract, for tokens) before they are saved.
  * Changing an address is a high-risk operation: it needs RBAC, a fresh 2FA code from the requester, a SECOND person with the
    approve permission and their own 2FA code, a reason, validation, and confirmation by the custody system that it controls the address.
  * Wallet rows are never edited in place or deleted. A change retires the old row and creates a new one.
  * Every step writes an immutable wallet_change_events row AND the hash-chained audit log.
  * No private key, seed phrase or signing credential is accepted, stored or logged anywhere in this module.
"""
import re
from . import util, security, chain_addresses as ca
from .util import ApiError
from .db import one, all_, val, tx
from .audit import audit

NAMES = {'btc': 'a Bitcoin', 'evm': 'an Ethereum-style (0x…)', 'solana': 'a Solana'}
WALLET_TYPES = ('deposit', 'hot', 'cold', 'sweep', 'operational', 'treasury', 'customer_deposit')
STATUSES = ('active', 'inactive', 'maintenance', 'manual_review', 'retired')
LIVE = ('active', 'maintenance', 'manual_review')
FORBIDDEN_FIELDS = ('private_key', 'seed_phrase', 'mnemonic', 'secret_recovery_phrase', 'wallet_password', 'signing_key', 'secret')

_INFO_SQL = '''SELECT an.id an_id, an.ledger_code, an.contract_address, an.decimals, an.min_deposit, an.confirmations_override, an.deposit_enabled, an.withdrawal_enabled,
  an.status an_status, an.withdrawal_fee_policy, an.max_deposit, an.min_withdrawal, an.max_withdrawal, a.visible asset_visible, a.id asset_id, a.symbol, a.name asset_name, a.type asset_type, a.status asset_status,
  n.id network_id, n.network_code, n.name network_name, n.address_format, n.is_testnet, n.attribution_mode, n.confirmations_required, n.finality_policy, n.status network_status, n.chain_type
  FROM asset_networks an JOIN crypto_assets a ON a.id=an.asset_id JOIN blockchain_networks n ON n.id=an.network_id'''

def an_info(db, an_id=None, ledger_code=None):
    r = one(db, _INFO_SQL + (' WHERE an.id=?' if an_id is not None else ' WHERE an.ledger_code=?'), an_id if an_id is not None else ledger_code)
    if not r: raise ApiError(404, 'not_found', 'Unknown asset network')
    return dict(r)

def reject_secret_fields(payload):
    """Defence in depth: refuse any request that even tries to carry key material."""
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if any(w in str(k).lower() for w in FORBIDDEN_FIELDS): raise ApiError(400, 'secret_material_refused', 'Private keys, seed phrases and wallet passwords must never be sent to Vaulta.')
                walk(v)
        elif isinstance(o, list): [walk(x) for x in o]
    walk(payload)

# ---------------- validation ----------------
def validate_wallet_address(info, address):
    fmt = info['address_format']
    if not fmt: raise ApiError(422, 'network_not_supported', f"{info['network_name']} has no address format configured, so wallets cannot be validated.")
    address = address if isinstance(address, str) else ''
    try: ca.validate(fmt, address, testnet=bool(info['is_testnet']), strict=True)
    except ca.InvalidAddress as e:
        guess = ca.family_of(address); hint = f' That looks like {NAMES[guess]} address.' if guess and guess != fmt else ''
        raise ApiError(422, 'invalid_address', f"Not a valid {info['network_name']} address ({e}).{hint}")
    if info['contract_address']:                                 # token: the contract must itself be valid and the wallet must not BE the contract
        try: ca.validate(fmt, info['contract_address'], testnet=bool(info['is_testnet']), strict=True)
        except ca.InvalidAddress as e: raise ApiError(409, 'invalid_contract', f"The configured {info['symbol']} contract is not valid for {info['network_name']} ({e}). Fix the asset registry first.")
        if ca.address_key(fmt, address) == ca.address_key(fmt, info['contract_address']): raise ApiError(422, 'address_is_contract', 'A wallet address cannot be the token contract address.')
    return True

def env_guard(env, info):
    if env == 'production' and info['is_testnet']: raise ApiError(409, 'testnet_in_production', 'A test network cannot be used for company wallets in production.')
    if env == 'staging' and not info['is_testnet']: raise ApiError(409, 'mainnet_in_staging', 'Staging may only use test networks.')

def elevated(db, actor, code, why):
    """High-risk operations require a fresh TOTP code from the acting administrator. Fails closed if 2FA is not set up."""
    u = dict(one(db, 'SELECT * FROM users WHERE id=?', actor['id']))
    if not u['totp_enabled']: raise ApiError(403, 'two_factor_setup_required', 'Enable two-factor authentication on your account before changing company wallets.')
    if not security.verify_totp(db, u, code):
        security.log_event(db, u['id'], '2fa_challenge_failed', why); raise ApiError(401, 'two_factor_required', 'Enter a valid 2FA code to continue.')

def _reason(reason, n=10):
    r = (reason or '').strip()
    if len(r) < n: raise ApiError(422, 'reason_required', f'A reason of at least {n} characters is required.')
    return util.text(r, 'reason', 500)

def _event(db, etype, info, wallet_id, old, new, actor, approver, req_ref, reason):
    db.execute('INSERT INTO wallet_change_events(event_type,asset_symbol,ledger_code,network_code,wallet_id,old_address,new_address,actor_id,approver_id,request_ref,reason,ip,user_agent,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
               (etype, info['symbol'], info['ledger_code'], info['network_code'], wallet_id, old, new, actor, approver, req_ref, reason, security.client_ip(), security.ua(), util.now()))

# ---------------- reads ----------------
def effective(db, w):
    """Resolve a wallet's real policy: wallet override -> asset-network override -> network default. Nothing is hard-coded per chain."""
    info = an_info(db, w['asset_network_id'])
    mn = int(w['minimum_deposit']) if w.get('minimum_deposit') not in (None, '') else int(info['min_deposit'])
    conf = w.get('confirmation_requirement') or info['confirmations_override'] or info['confirmations_required']
    return {'minimum_deposit': mn, 'confirmations': int(conf), 'info': info}

def get_wallet(db, wid):
    w = one(db, 'SELECT * FROM company_crypto_wallets WHERE id=?', wid)
    if not w: raise ApiError(404, 'not_found', 'Wallet not found')
    return dict(w)

def live_wallet(db, an_id, wallet_type='deposit'):
    r = one(db, "SELECT * FROM company_crypto_wallets WHERE asset_network_id=? AND wallet_type=? AND owner_user_id IS NULL AND status IN('active','maintenance','manual_review')", an_id, wallet_type)
    return dict(r) if r else None

def is_configured(w): return bool(w) and w['public_address'] != ca.PLACEHOLDER

def view(db, w, show_policy=True):
    info = an_info(db, w['asset_network_id']); e = effective(db, w) if show_policy else None
    from . import assets as reg
    out = {'id': w['id'], 'asset': info['symbol'], 'asset_name': info['asset_name'], 'ledger_code': info['ledger_code'], 'network': info['network_code'], 'network_name': info['network_name'],
           'is_testnet': bool(info['is_testnet']), 'label': w['wallet_label'], 'public_address': w['public_address'], 'configured': is_configured(w), 'wallet_type': w['wallet_type'],
           'provider': w['provider'], 'status': w['status'], 'is_deposit_enabled': bool(w['is_deposit_enabled']), 'contract_address': info['contract_address'],
           'created_at': w['created_at'], 'retired_at': w['retired_at'], 'replaced_by_wallet_id': w['replaced_by_wallet_id'], 'parent_wallet_id': w['parent_wallet_id']}
    if e:
        out.update({'minimum_deposit': reg.from_minor(db, info['ledger_code'], e['minimum_deposit']), 'required_confirmations': e['confirmations'],
                    'risk_review_threshold': reg.from_minor(db, info['ledger_code'], w['risk_review_threshold']) if w.get('risk_review_threshold') else None})
    return out

def list_wallets(db, include_retired=False):
    rows = all_(db, "SELECT * FROM company_crypto_wallets" + ('' if include_retired else " WHERE status<>'retired'") + ' ORDER BY asset_network_id, wallet_type, id')
    return [view(db, w) for w in rows]

def history(db, wallet_id=None, limit=200):
    return all_(db, 'SELECT * FROM wallet_change_events' + (' WHERE wallet_id=?' if wallet_id else '') + ' ORDER BY id DESC LIMIT ?', *([wallet_id] if wallet_id else []), limit)

# ---------------- installing (shared by bootstrap and approvals) ----------------
def install_wallet(db, *, an_id, address, label, wallet_type='deposit', actor_id=None, approver_id=None, request_ref=None, reason='', event='wallet_installed',
                   provider='external_custody', allow_placeholder=False, minimum_deposit=None, confirmation_requirement=None):
    """Create a wallet row (inactive, deposits disabled) and retire whatever it replaces. Validation happens here, always."""
    wallet_type = wallet_type if wallet_type in WALLET_TYPES else None
    if not wallet_type: raise ApiError(422, 'invalid_wallet_type', 'Unknown wallet type')
    with tx(db):
        info = an_info(db, an_id)
        if address == ca.PLACEHOLDER:
            if not allow_placeholder: raise ApiError(422, 'invalid_address', 'NOT_CONFIGURED is a placeholder, not an address.')
        else: validate_wallet_address(info, address)
        olds = all_(db, "SELECT * FROM company_crypto_wallets WHERE asset_network_id=? AND wallet_type=? AND owner_user_id IS NULL AND status<>'retired'", an_id, wallet_type)
        if any(ca.address_key(info['address_format'] or 'evm', o['public_address']) == ca.address_key(info['address_format'] or 'evm', address) and address != ca.PLACEHOLDER for o in olds):
            raise ApiError(409, 'same_address', 'That address is already configured for this wallet.')
        wid = db.execute('INSERT INTO company_crypto_wallets(asset_network_id,wallet_label,public_address,wallet_type,provider,status,is_deposit_enabled,minimum_deposit,confirmation_requirement,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (an_id, util.text(label, 'label', 80), address, wallet_type, provider, 'inactive', 0, minimum_deposit, confirmation_requirement, actor_id, util.now())).lastrowid
        old_addr = None
        for o in olds:
            db.execute("UPDATE company_crypto_wallets SET status='retired',is_deposit_enabled=0,retired_at=?,replaced_by_wallet_id=?,updated_at=? WHERE id=?", (util.now(), wid, util.now(), o['id']))
            db.execute("UPDATE deposit_intents SET status='cancelled',closed_at=? WHERE company_wallet_id=? AND status='open'", (util.now(), o['id']))
            _event(db, 'wallet_retired', info, o['id'], o['public_address'], address, actor_id, approver_id, request_ref, 'replaced by wallet %s' % wid)
            old_addr = o['public_address'] if o['public_address'] != ca.PLACEHOLDER else old_addr
        _event(db, event, info, wid, old_addr, address, actor_id, approver_id, request_ref, reason)
        audit(db, actor_id, 'crypto.wallet_' + event.replace('wallet_', ''), 'company_crypto_wallet', wid, {'address': old_addr}, {'address': address, 'ledger_code': info['ledger_code'], 'type': wallet_type, 'approver': approver_id}, reason or None)
        return wid

# ---------------- change workflow ----------------
def propose_change(db, providers, actor, an_id, address, label, reason, code, wallet_type='deposit'):
    reject_secret_fields({'address': address, 'label': label})
    elevated(db, actor, code, 'wallet_change_propose'); reason = _reason(reason)
    info = an_info(db, an_id); env_guard(providers.env, info); validate_wallet_address(info, address)
    if wallet_type not in WALLET_TYPES: raise ApiError(422, 'invalid_wallet_type', 'Unknown wallet type')
    with tx(db):
        cur = one(db, "SELECT * FROM company_crypto_wallets WHERE asset_network_id=? AND wallet_type=? AND owner_user_id IS NULL AND status<>'retired' ORDER BY id DESC LIMIT 1", an_id, wallet_type)
        if cur and ca.address_key(info['address_format'], cur['public_address']) == ca.address_key(info['address_format'], address): raise ApiError(409, 'same_address', 'That address is already configured.')
        if one(db, "SELECT 1 FROM wallet_change_requests WHERE asset_network_id=? AND wallet_type=? AND status='pending'", an_id, wallet_type): raise ApiError(409, 'change_pending', 'A change for this wallet is already awaiting approval.')
        ref = util.new_ref('WCR')
        db.execute('INSERT INTO wallet_change_requests(ref,asset_network_id,wallet_type,current_wallet_id,old_address,new_address,new_label,reason,requested_by,requested_at,requested_ip,requested_ua,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (ref, an_id, wallet_type, cur['id'] if cur else None, cur['public_address'] if cur and cur['public_address'] != ca.PLACEHOLDER else None, address, util.text(label, 'label', 80), reason,
                    actor['id'], util.now(), security.client_ip(), security.ua(), util.ts_after(hours=48)))
        _event(db, 'change_requested', info, cur['id'] if cur else None, cur['public_address'] if cur else None, address, actor['id'], None, ref, reason)
        audit(db, actor['id'], 'crypto.wallet_change_requested', 'wallet_change_request', ref, {'address': cur['public_address'] if cur else None}, {'address': address, 'ledger_code': info['ledger_code']}, reason)
        return ref

def _request(db, ref):
    r = one(db, 'SELECT * FROM wallet_change_requests WHERE ref=?', ref)
    if not r: raise ApiError(404, 'not_found', 'Change request not found')
    r = dict(r)
    if r['status'] == 'pending' and r['expires_at'] < util.now():
        db.execute("UPDATE wallet_change_requests SET status='expired' WHERE id=?", (r['id'],)); r['status'] = 'expired'
    return r

def approve_change(db, providers, approver, ref, code, note=None):
    elevated(db, approver, code, 'wallet_change_approve')
    r = _request(db, ref)
    if r['status'] != 'pending': raise ApiError(409, 'invalid_state', f"This request is {r['status']}.")
    if r['requested_by'] == approver['id']: raise ApiError(403, 'separation_of_duties', 'A second administrator must approve this change; you cannot approve your own request.')
    info = an_info(db, r['asset_network_id']); env_guard(providers.env, info); validate_wallet_address(info, r['new_address'])
    # fail closed: the custody system itself must confirm it controls this address (catches typos and addresses we do not hold keys for)
    if not providers.wallets.controls_address(info['network_code'], r['new_address']):
        raise ApiError(409, 'custody_does_not_control_address', 'The custody provider does not confirm control of this address. Nothing was changed.')
    with tx(db):
        r2 = _request(db, ref)
        if r2['status'] != 'pending': raise ApiError(409, 'invalid_state', f"This request is {r2['status']}.")
        cur = one(db, "SELECT id FROM company_crypto_wallets WHERE asset_network_id=? AND wallet_type=? AND owner_user_id IS NULL AND status<>'retired' ORDER BY id DESC LIMIT 1", r['asset_network_id'], r['wallet_type'])
        if (cur['id'] if cur else None) != r['current_wallet_id']: raise ApiError(409, 'stale_request', 'The wallet changed since this request was made. Submit a new request.')
        wid = install_wallet(db, an_id=r['asset_network_id'], address=r['new_address'], label=r['new_label'], wallet_type=r['wallet_type'], actor_id=r['requested_by'], approver_id=approver['id'],
                             request_ref=ref, reason=r['reason'], event='wallet_address_changed' if cur else 'wallet_address_set')
        db.execute("UPDATE wallet_change_requests SET status='approved',decided_by=?,decided_at=?,decision_reason=?,decided_ip=?,resulting_wallet_id=? WHERE id=?", (approver['id'], util.now(), note, security.client_ip(), wid, r['id']))
        _event(db, 'change_approved', info, wid, r['old_address'], r['new_address'], r['requested_by'], approver['id'], ref, note or r['reason'])
        audit(db, approver['id'], 'crypto.wallet_change_approved', 'wallet_change_request', ref, {'address': r['old_address']}, {'address': r['new_address'], 'requested_by': r['requested_by']}, note or r['reason'])
        return wid

def reject_change(db, actor, ref, note):
    note = _reason(note, 5)
    with tx(db):
        r = _request(db, ref)
        if r['status'] != 'pending': raise ApiError(409, 'invalid_state', f"This request is {r['status']}.")
        db.execute("UPDATE wallet_change_requests SET status=?,decided_by=?,decided_at=?,decision_reason=?,decided_ip=? WHERE id=?",
                   ('cancelled' if r['requested_by'] == actor['id'] else 'rejected', actor['id'], util.now(), note, security.client_ip(), r['id']))
        _event(db, 'change_rejected', an_info(db, r['asset_network_id']), r['current_wallet_id'], r['old_address'], r['new_address'], r['requested_by'], actor['id'], ref, note)
        audit(db, actor['id'], 'crypto.wallet_change_rejected', 'wallet_change_request', ref, None, {'address': r['new_address']}, note)

def list_requests(db, status=None):
    rows = all_(db, 'SELECT r.*,an.ledger_code FROM wallet_change_requests r JOIN asset_networks an ON an.id=r.asset_network_id' + (' WHERE r.status=?' if status else '') + ' ORDER BY r.id DESC LIMIT 100', *([status] if status else []))
    return [dict(x, status='expired' if x['status'] == 'pending' and x['expires_at'] < util.now() else x['status']) for x in rows]

# ---------------- settings (never the address) ----------------
def update_settings(db, providers, actor, wallet_id, code, reason, changes):
    """Edit confirmation policy, minimum, risk threshold, label, status, deposit switch. The address itself can only change via the approval workflow."""
    reject_secret_fields(changes)
    if 'public_address' in changes or 'address' in changes: raise ApiError(422, 'use_change_workflow', 'Wallet addresses can only be changed through the two-person approval workflow.')
    elevated(db, actor, code, 'wallet_settings'); reason = _reason(reason)
    from . import assets as reg
    with tx(db):
        w = get_wallet(db, wallet_id)
        if w['status'] == 'retired': raise ApiError(409, 'retired', 'Retired wallets are historical records and cannot be changed.')
        info = an_info(db, w['asset_network_id']); upd = {}
        if 'wallet_label' in changes: upd['wallet_label'] = util.text(changes['wallet_label'], 'label', 80)
        if 'minimum_deposit' in changes:
            v = changes['minimum_deposit']; upd['minimum_deposit'] = None if v in (None, '') else str(reg.to_minor(db, info['ledger_code'], v))
        if 'risk_review_threshold' in changes:
            v = changes['risk_review_threshold']; upd['risk_review_threshold'] = None if v in (None, '') else str(reg.to_minor(db, info['ledger_code'], v))
        if 'confirmation_requirement' in changes:
            v = changes['confirmation_requirement']
            if v is not None and (not isinstance(v, int) or isinstance(v, bool) or not 1 <= v <= 100000): raise ApiError(422, 'invalid_confirmations', 'confirmation_requirement must be a positive integer')
            upd['confirmation_requirement'] = v
        new_status = changes.get('status', w['status']); new_dep = int(bool(changes['is_deposit_enabled'])) if 'is_deposit_enabled' in changes else w['is_deposit_enabled']
        if new_status not in STATUSES or new_status == 'retired': raise ApiError(422, 'invalid_status', 'status must be active, inactive, maintenance or manual_review (retire by replacing the address)')
        if new_status in LIVE or new_dep:
            if not is_configured(w): raise ApiError(409, 'not_configured', 'Set the wallet address first (NOT_CONFIGURED is a placeholder).')
            validate_wallet_address(info, w['public_address']); env_guard(providers.env, info)
            if (new_status != w['status'] and new_status == 'active') or (new_dep and not w['is_deposit_enabled']):
                if not providers.wallets.controls_address(info['network_code'], w['public_address']): raise ApiError(409, 'custody_does_not_control_address', 'The custody provider does not confirm control of this address.')
        if new_dep and new_status != 'active': raise ApiError(409, 'not_active', 'Deposits can only be enabled on an active wallet.')
        if new_status != 'active': new_dep = 0
        upd['status'] = new_status; upd['is_deposit_enabled'] = new_dep
        before = {k: w[k] for k in upd}
        if before == upd: return view(db, w)
        db.execute('UPDATE company_crypto_wallets SET ' + ','.join(f'{k}=?' for k in upd) + ',updated_at=? WHERE id=?', (*upd.values(), util.now(), wallet_id))
        _event(db, 'wallet_settings_changed', info, wallet_id, None, None, actor['id'], None, None, reason + ' | ' + util.jdump({k: v for k, v in upd.items() if before[k] != v}))
        audit(db, actor['id'], 'crypto.wallet_settings', 'company_crypto_wallet', wallet_id, before, upd, reason)
        return view(db, get_wallet(db, wallet_id))
