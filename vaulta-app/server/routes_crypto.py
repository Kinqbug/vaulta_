"""Crypto funding API.
  /api/crypto/*            customer: supported assets, deposit intents + instructions + QR, deposit history, source addresses, balances, withdrawals
  /api/admin/crypto/*      staff: company wallets (two-person change workflow), deposits & manual review, withdrawals, reconciliation
  /api/dev/crypto/*        development ONLY (mock chain): simulate a customer's on-chain payment so the UI can be exercised
Every admin route is guarded server-side by require_perm; hiding UI is cosmetic only."""
from flask import Blueprint, request, g, current_app, Response
from . import uploads, crypto_catalog as cat, util, security as sec, crypto_deposits as cd, crypto_withdrawals as cwd, crypto_wallets as cw, crypto_recon as rc, crypto_ledger as cl, assets as reg, qr, chain_providers as cp, chain_addresses as ca, crypto_setup, wallet_console as wc
from .util import ApiError, page_args
from .db import get_db, one, all_, val
from .routes_auth import body
from .rbac import setting

bp = Blueprint('crypto', __name__, url_prefix='/api/crypto')
admin = Blueprint('crypto_admin', __name__, url_prefix='/api/admin/crypto')
dev = Blueprint('crypto_dev', __name__, url_prefix='/api/dev/crypto')
U = sec.login_required; P = sec.require_perm
def PR(): return current_app.providers
def me(): return g.user
def A(): return g.user
def idem(): return request.headers.get('Idempotency-Key')
def _body():
    b = body(); cw.reject_secret_fields(b); return b

# ================= customer =================
@bp.get('/config')
@U
def config():
    p = PR(); test = p.crypto_mode != 'live' or p.env != 'production'
    db = get_db()
    return {'environment': p.env, 'provider_mode': p.crypto_mode, 'is_test_environment': test, 'real_funds_enabled': p.crypto_real_funds,
            'proof': {'required': bool(setting(db, 'crypto_screenshot_required')), 'max_bytes': uploads.SCREENSHOT_MAX_BYTES, 'types': ['image/png', 'image/jpeg', 'image/webp']},
            'banner': 'TEST ENVIRONMENT: no real cryptocurrency can be sent or received here. Balances are simulated.' if test else None}

@bp.get('/assets')
@U
def assets(): db = get_db(); return {'assets': cd.supported_assets(db), 'withdrawal_assets': cd.withdrawal_assets(db)}

def _root(): return current_app.config['UPLOAD_DIR']

@bp.get('/logos/<int:asset_id>')
@U
def logo(asset_id):
    """Asset logo straight from the registry. A hidden asset's logo is served only to staff who can read the registry, or to a customer who still holds that asset."""
    db = get_db(); a, data = cat.read_logo(db, _root(), asset_id)
    if not a['visible']:
        staff = one(db, "SELECT 1 FROM user_roles ur JOIN role_permissions rp ON rp.role_id=ur.role_id JOIN permissions p ON p.id=rp.permission_id WHERE ur.user_id=? AND p.code='crypto.registry.read'", g.user['id'])
        holds = a['symbol'] in {c.split('.')[0] for c in cl.balances(db, g.user['id'])}
        if not staff and not holds: raise ApiError(404, 'not_found', 'No logo')
    return uploads.file_response(data, a['logo_mime'], download=False, etag=a['logo_sha256'][:16])

@bp.get('/withdrawal-info')
@U
def wd_info():
    """Balance, limits, fee schedule and the 'withdraw all' amount for one asset-network (all computed server-side from the customer's own ledger balance)."""
    return cwd.withdrawal_info(get_db(), me(), request.args.get('ledger_code', ''))

@bp.get('/validate-address')
@U
def wd_validate():
    """Format check of a destination for the selected network. 'valid' means well-formed for that network only: it says nothing about who controls the address."""
    db = get_db(); info = cw.an_info(db, ledger_code=request.args.get('ledger_code', ''))
    try: cwd.check_address(db, info, request.args.get('address', '')); return {'valid': True, 'message': 'The format is valid for this network. Make sure the address is yours and correct: transfers cannot be reversed.'}
    except ApiError as e: return {'valid': False, 'code': e.code, 'message': e.message}

@bp.get('/withdrawal-quote')
@U
def wd_quote():
    return cwd.quote(get_db(), me(), request.args.get('ledger_code', ''), request.args.get('amount', '0'))

# ---- deposit requests (screenshot + TXID), reviewed by staff ----
def _upload():
    f = request.files.get('screenshot')
    if not f: return None, None
    data = f.read(uploads.SCREENSHOT_MAX_BYTES + 1)
    if len(data) > uploads.SCREENSHOT_MAX_BYTES: raise ApiError(413, 'file_too_large', 'Screenshots must be 5 MB or smaller.')
    return data, f.filename

@bp.post('/deposit-requests')
@U
def req_create():
    cw.reject_secret_fields(dict(request.form)); data, fname = _upload(); f = request.form
    for k in ('intent_ref', 'network', 'amount'):
        if not (f.get(k) or '').strip(): raise ApiError(422, 'missing_field', f'{k} is required')
    r = cd.submit_request(get_db(), PR(), me(), _root(), intent_ref=f['intent_ref'], network_code=f['network'], amount=f['amount'], txid=f.get('tx_hash'), sender=f.get('sender_address'), note=f.get('note'),
                          screenshot=data, original_name=fname, idem=idem())
    return r, 201
@bp.get('/deposit-requests')
@U
def req_list(): return {'requests': cd.user_requests(get_db(), me()['id'])}
@bp.get('/deposit-requests/<ref>')
@U
def req_get(ref): db = get_db(); r = cd.user_request(db, me()['id'], ref); return {'request': cd.request_view(db, r), 'events': all_(db, 'SELECT event,created_at FROM deposit_request_events WHERE request_id=? ORDER BY id', r['id'])}
@bp.post('/deposit-requests/<ref>/cancel')
@U
def req_cancel(ref): cd.cancel_request(get_db(), me(), ref); return {'ok': True}
@bp.get('/deposit-requests/<ref>/evidence')
@U
def req_evidence(ref):
    """Only the customer who submitted it. Other customers get 404 so references cannot be probed."""
    db = get_db(); r = cd.user_request(db, me()['id'], ref); data, mime = cd.evidence(db, _root(), r)
    return uploads.file_response(data, mime, r['ref'] + '.' + uploads.EXT[mime], download=request.args.get('download') == '1')

@bp.get('/funding-history')
@U
def funding_history(): return {'items': cd.funding_items(get_db(), me()['id'])}

@bp.post('/deposit-intents')
@U
def intent_create():
    b = _body(); util.need(b, 'ledger_code')
    return {'intent': cd.create_intent(get_db(), PR(), me(), b['ledger_code'], b.get('expected_amount'))}, 201
@bp.get('/deposit-intents')
@U
def intent_list(): return {'intents': cd.list_intents(get_db(), me()['id'])}
@bp.post('/deposit-intents/<ref>/cancel')
@U
def intent_cancel(ref): cd.cancel_intent(get_db(), me(), ref); return {'ok': True}

@bp.get('/deposit-intents/<ref>/qr.svg')
@U
def intent_qr(ref):
    """QR of the wallet address for the customer's OWN open intent only; the address is re-validated before it is encoded."""
    db = get_db(); i = cd.get_open_intent(db, me(), ref)
    if i['status'] != 'open': raise ApiError(409, 'invalid_state', 'This intent is closed.')
    w = cw.get_wallet(db, i['company_wallet_id']); info = cw.an_info(db, i['asset_network_id']); cw.validate_wallet_address(info, w['public_address'])
    return Response(qr.svg(w['public_address']), mimetype='image/svg+xml', headers={'Cache-Control': 'no-store'})

@bp.post('/deposits/submit-tx')
@U
def submit_tx():
    b = _body(); util.need(b, 'intent_ref', 'tx_hash'); return {'deposits': cd.submit_tx(get_db(), PR(), me(), b['intent_ref'], b['tx_hash'])}
@bp.get('/deposits')
@U
def deposits(): return {'deposits': cd.history(get_db(), me()['id'])}

@bp.get('/source-addresses')
@U
def src_list(): return {'addresses': cd.list_sources(get_db(), me()['id'])}
@bp.post('/source-addresses')
@U
def src_add():
    b = _body(); util.need(b, 'network', 'address'); return {'address': cd.register_source(get_db(), me(), b['network'], b['address'])}, 201
@bp.post('/source-addresses/<int:sid>/verify')
@U
def src_verify(sid):
    b = _body(); return {'address': cd.verify_source_with_proof(get_db(), PR(), me(), sid, b.get('signature'))}
@bp.post('/source-addresses/<int:sid>/revoke')
@U
def src_revoke(sid): cd.revoke_source(get_db(), me()['id'], me()['id'], sid, 'revoked by customer'); return {'ok': True}

@bp.get('/balances')
@U
def balances():
    from . import portfolio as pf
    db = get_db(); rows = []
    for r in pf._crypto_rows(db, me()['id']):
        info = cw.an_info(db, ledger_code=r['ledger_code'])
        rows.append({'ledger_code': r['ledger_code'], 'asset': r['symbol'], 'asset_name': info['asset_name'], 'logo': cat.logo_url(one(db, 'SELECT * FROM crypto_assets WHERE id=?', info['asset_id'])), 'network': info['network_name'], 'amount': reg.from_minor(db, r['ledger_code'], cl.balance(db, me()['id'], r['ledger_code'])),
                     'price': r['price'] if r['priced'] else None, 'value': util.cstr(r['value_cents']) if r['priced'] else None, 'currency': 'USD'})
    return {'balances': rows}

@bp.get('/withdrawals')
@U
def wd_list(): return {'withdrawals': cwd.history(get_db(), me()['id'])}
@bp.post('/withdrawals')
@U
def wd_create():
    b = _body(); util.need(b, 'ledger_code', 'amount', 'destination'); r = cwd.request_withdrawal(get_db(), PR(), me(), b['ledger_code'], b['amount'], b['destination'], b.get('code'), idem()); return r, 201
@bp.post('/withdrawals/<ref>/cancel')
@U
def wd_cancel(ref): cwd.cancel(get_db(), me(), ref); return {'ok': True}

# ================= development simulator (mock chain only) =================
def _dev_only():
    p = PR()
    if p.crypto_mode != 'mock' or p.env != 'development': raise ApiError(404, 'not_found', 'Not found')
@dev.post('/simulate-deposit')
@U
def dev_simulate():
    """Pays the caller's open intent on the mock chain, from their verified address (or an unknown one), mines the required blocks and runs the monitor."""
    _dev_only(); b = _body(); util.need(b, 'ledger_code', 'amount'); db = get_db(); p = PR(); code = b['ledger_code']
    info = cw.an_info(db, ledger_code=code); i = one(db, "SELECT * FROM deposit_intents WHERE user_id=? AND asset_network_id=? AND status='open'", me()['id'], info['an_id'])
    if not i: raise ApiError(409, 'no_open_intent', 'Create a deposit intent first.')
    w = cw.get_wallet(db, i['company_wallet_id']); minor = reg.to_minor(db, code, b['amount'])
    s = one(db, "SELECT address FROM customer_source_addresses WHERE user_id=? AND network_id=? AND status='verified' ORDER BY id DESC LIMIT 1", me()['id'], info['network_id'])
    sender = s['address'] if (s and b.get('from') != 'unknown') else crypto_setup.demo_address(info['address_format'], 'unknown-' + util.new_ref('S'), True)
    memo = i['reference_code'] if (info['attribution_mode'] == 'memo_or_source' and b.get('memo', True)) else None
    h = p.chain.chain.fund(info['network_code'], w['public_address'], minor, sender=sender, contract=info['contract_address'], memo=memo)
    p.chain.chain.mine(info['network_code'], int(b.get('confirmations', 1))); out = cd.run_once(db, p)
    return {'tx_hash': h, 'sender': sender, 'monitor': {'advanced': out['advanced']}}

@dev.post('/mine')
@U
def dev_mine():
    _dev_only(); b = _body(); util.need(b, 'network'); p = PR(); p.chain.chain.mine(b['network'], int(b.get('blocks', 1))); return {'monitor': {'advanced': cd.run_once(get_db(), p)['advanced']}}

@dev.post('/sign-challenge')
@U
def dev_sign():
    _dev_only(); b = _body(); util.need(b, 'address', 'challenge'); return {'signature': cp.mock_sign(b['address'], b['challenge'])}

# ================= admin: wallets =================
@admin.get('/wallets')
@P('crypto.wallets.read')
def a_wallets(): return {'wallets': cw.list_wallets(get_db(), request.args.get('retired') == '1'), 'environment': PR().env, 'provider_mode': PR().crypto_mode,
                         'asset_networks': [{'id': r['id'], 'ledger_code': r['ledger_code'], 'symbol': r['symbol'], 'network': r['network_code']} for r in reg.list_registry(get_db())]}
@admin.get('/wallets/history')
@P('crypto.wallets.read')
def a_wallet_history(): return {'events': cw.history(get_db(), request.args.get('wallet_id', type=int))}
@admin.get('/wallets/change-requests')
@P('crypto.wallets.read')
def a_requests(): return {'requests': cw.list_requests(get_db(), request.args.get('status'))}
@admin.post('/wallets/change-requests')
@P('crypto.wallets.manage')
def a_propose():
    b = _body(); util.need(b, 'asset_network_id', 'address', 'label', 'reason', 'code')
    return {'ref': cw.propose_change(get_db(), PR(), A(), b['asset_network_id'], b['address'], b['label'], b['reason'], b['code'], b.get('wallet_type', 'deposit'))}, 201
@admin.post('/wallets/change-requests/<ref>/approve')
@P('crypto.wallets.approve')
def a_approve(ref):
    b = _body(); util.need(b, 'code'); return {'wallet_id': cw.approve_change(get_db(), PR(), A(), ref, b['code'], b.get('note'))}
@admin.post('/wallets/change-requests/<ref>/reject')
@P('crypto.wallets.manage')
def a_reject(ref): b = _body(); cw.reject_change(get_db(), A(), ref, b.get('note')); return {'ok': True}
@admin.post('/wallets/<int:wid>/settings')
@P('crypto.wallets.manage')
def a_settings(wid):
    b = _body(); util.need(b, 'code', 'reason'); ch = {k: v for k, v in b.items() if k not in ('code', 'reason')}
    return {'wallet': cw.update_settings(get_db(), PR(), A(), wid, b['code'], b['reason'], ch)}

# ================= admin: deposit requests (screenshot review) =================
@admin.get('/deposit-requests')
@P('crypto.deposits.read')
def a_reqs():
    a = request.args; return cd.list_requests(get_db(), a.get('status') or None, (a.get('q') or '').strip() or None, a.get('asset') or None, max(1, a.get('page', 1, type=int)), min(100, max(1, a.get('size', 25, type=int))))
@admin.get('/deposit-requests/<int:rid>')
@P('crypto.deposits.read')
def a_req(rid): return cd.admin_detail(get_db(), PR(), rid)
@admin.get('/deposit-requests/<int:rid>/evidence')
@P('crypto.deposits.read')
def a_req_evidence(rid):
    db = get_db(); r = cd._req(db, rid); data, mime = cd.evidence(db, _root(), r); dl = request.args.get('download') == '1'
    cd.note_evidence_view(db, A(), rid, dl)
    return uploads.file_response(data, mime, r['ref'] + '.' + uploads.EXT[mime], download=dl)
@admin.post('/deposit-requests/<int:rid>/approve')
@P('crypto.deposits.manage')
def a_req_approve(rid):
    b = _body(); return {'request': cd.approve_request(get_db(), PR(), A(), rid, b.get('note'), b.get('verified_amount'), bool(b.get('attest')))}
@admin.post('/deposit-requests/<int:rid>/reject')
@P('crypto.deposits.manage')
def a_req_reject(rid): b = _body(); return {'request': cd.reject_request(get_db(), A(), rid, b.get('reason'), b.get('note'))}

# ================= admin: deposits (on-chain detections) =================
@admin.get('/deposits')
@P('crypto.deposits.read')
def a_deposits(): return {'deposits': cd.list_admin(get_db(), request.args.get('status'), request.args.get('user_id', type=int))}
@admin.get('/deposits/<int:did>')
@P('crypto.deposits.read')
def a_deposit(did):
    db = get_db(); d = one(db, 'SELECT * FROM crypto_deposits WHERE id=?', did)
    if not d: raise ApiError(404, 'not_found', 'Deposit not found')
    return {'deposit': cd.admin_view(db, d), 'events': cd.events(db, did)}
@admin.post('/deposits/<int:did>/resolve')
@P('crypto.deposits.manage')
def a_resolve(did):
    b = _body(); util.need(b, 'action', 'reason'); return {'deposit': cd.resolve(get_db(), PR(), A(), did, b['action'], b['reason'], b.get('user'), b.get('duplicate_of'))}
@admin.post('/monitor/run')
@P('crypto.deposits.manage')
def a_monitor(): return cd.run_once(get_db(), PR())
@admin.get('/source-addresses')
@P('crypto.deposits.read')
def a_sources():
    return {'addresses': all_(get_db(), "SELECT s.id,s.user_id,u.email,n.network_code,s.address,s.status,s.proof_method,s.created_at FROM customer_source_addresses s JOIN users u ON u.id=s.user_id JOIN blockchain_networks n ON n.id=s.network_id WHERE s.status=? ORDER BY s.id DESC LIMIT 200", request.args.get('status', 'pending'))}
@admin.post('/source-addresses/<int:sid>/verify')
@P('crypto.deposits.manage')
def a_src_verify(sid): b = _body(); cd.admin_verify_source(get_db(), A(), sid, b.get('reason')); return {'ok': True}

# ================= admin: withdrawals =================
@admin.get('/withdrawals')
@P('crypto.withdrawals.read')
def a_wds(): return {'withdrawals': cwd.list_admin(get_db(), request.args.get('status'))}
@admin.post('/withdrawals/<int:wid>/<action>')
@P('crypto.withdrawals.manage')
def a_wd_action(wid, action):
    b = _body(); db = get_db()
    if action == 'approve': cwd.approve(db, A(), wid, b.get('reason'))
    elif action == 'reject': cwd.reject(db, A(), wid, b.get('reason'))
    elif action == 'process': cwd.process(db, PR(), A(), wid)
    elif action == 'fail': cwd.fail_unknown(db, A(), wid, b.get('reason'))
    elif action == 'advance': cwd.advance(db, PR(), wid)
    elif action == 'manual_process': cwd.manual_process(db, A(), wid, b.get('tx_hash'), b.get('network_fee') or '0', b.get('note'))
    elif action == 'manual_complete': cwd.manual_complete(db, PR(), A(), wid, b.get('note'), bool(b.get('attest')))
    else: raise ApiError(422, 'invalid_action', 'Unknown action')
    return {'withdrawal': cwd.view(db, cwd._get(db, wid), admin=True)}

# ================= admin: reconciliation =================
@admin.get('/reconciliation')
@P('crypto.recon.read')
def a_recon(): db = get_db(); return {'runs': rc.list_runs(db), 'exceptions': rc.list_exceptions(db, request.args.get('status') or None)}
@admin.post('/reconciliation/run')
@P('crypto.recon.manage')
def a_recon_run(): return rc.reconcile(get_db(), PR(), A()['id'])
@admin.post('/reconciliation/exceptions/<int:eid>/resolve')
@P('crypto.recon.manage')
def a_exc_resolve(eid): b = _body(); util.need(b, 'status', 'note'); rc.resolve_exception(get_db(), A(), eid, b['status'], b['note']); return {'ok': True}


# ================= admin: crypto asset catalog (logos, networks, limits, fees): part of the existing Assets management =================
def _form(): return request.form if (request.files or request.mimetype == 'multipart/form-data') else (request.get_json(silent=True) or {})
def _logo_bytes():
    f = request.files.get('logo')
    if not f: return None
    d = f.read(uploads.LOGO_MAX_BYTES + 1)
    if len(d) > uploads.LOGO_MAX_BYTES: raise ApiError(413, 'file_too_large', 'Logos must be 1 MB or smaller.')
    if not d and f.filename: raise ApiError(422, 'invalid_image', 'The file is empty.')      # a chosen-but-empty file is an error, never a silent no-op
    return d or None
def _b(v): return v if isinstance(v, bool) else str(v).lower() in ('1', 'true', 'on', 'yes')

@admin.get('/assets')
@P('crypto.registry.read')
def a_assets(): db = get_db(); return {'assets': cat.list_assets(db), 'networks': cat.available_networks(db)}
@admin.post('/assets')
@P('crypto.registry.manage')
def a_asset_create():
    f = _form(); cw.reject_secret_fields(dict(f)); net = None
    if f.get('network_code'): net = {k: f.get(k) for k in ('network_code', 'decimals', 'contract_address', 'min_deposit', 'max_deposit', 'min_withdrawal', 'max_withdrawal', 'fee_flat', 'fee_percent', 'confirmations', *cat.TEXT_FIELDS) if f.get(k) not in (None, '')}
    a = cat.create_asset(get_db(), A()['id'], _root(), symbol=f.get('symbol'), name=f.get('name'), typ=f.get('type') or 'native', description=f.get('description'), visible=_b(f.get('visible', True)),
                                      logo=_logo_bytes(), activate=_b(f.get('activate', False)), network=net, logo_alt=f.get('logo_alt'), notice=f.get('notice'))
    return {'id': a['id'], 'asset': a}, 201
@admin.post('/assets/<int:aid>')
@P('crypto.registry.manage')
def a_asset_update(aid):
    f = _form(); cw.reject_secret_fields(dict(f))
    return {'asset': cat.update_asset(get_db(), A()['id'], _root(), aid, name=f.get('name'), description=f.get('description'), visible=_b(f['visible']) if 'visible' in f else None, logo=_logo_bytes(),
                                      remove_logo=_b(f.get('remove_logo', False)), status=f.get('status') or None, reason=f.get('reason'),
                                      logo_alt=f.get('logo_alt') if 'logo_alt' in f else None, notice=f.get('notice') if 'notice' in f else None)}
@admin.delete('/assets/<int:aid>')
@P('crypto.registry.manage')
def a_asset_delete(aid): cat.delete_asset(get_db(), A()['id'], _root(), aid); return {'ok': True}
@admin.post('/assets/<int:aid>/networks')
@P('crypto.registry.manage')
def a_asset_network_add(aid): b = _body(); return {'network': cat.add_network(get_db(), A()['id'], aid, b)}, 201
@admin.post('/asset-networks/<int:anid>/config')
@P('crypto.registry.manage')
def a_an_config(anid):
    b = _body(); reason = b.pop('reason', None)
    for k in ('deposit_enabled', 'withdrawal_enabled'):
        if k in b: b[k] = _b(b[k])
    return {'network': cat.configure_network(get_db(), A()['id'], anid, b, reason)}


# ================= admin: blockchain networks (validated: an address format is mandatory; the older POST /api/admin/crypto/networks remains for compatibility) =================
@admin.get('/blockchain-networks')
@P('crypto.registry.read')
def a_networks(): return {'networks': cat.list_networks(get_db())}
@admin.post('/blockchain-networks')
@P('crypto.registry.manage')
def a_network_create(): return {'network': cat.create_network(get_db(), A()['id'], _body())}, 201
@admin.post('/blockchain-networks/<int:nid>')
@P('crypto.registry.manage')
def a_network_update(nid):
    b = _body(); return {'network': cat.update_network(get_db(), A()['id'], nid, name=b.get('name'), confirmations_required=b.get('confirmations_required'), status=b.get('status') or None, reason=b.get('reason'))}


# ================= admin: Wallets & Reconciliation / Asset Settings pages (read models + QR + explorer URL) =================
@admin.get('/wallets/overview')
@P('crypto.wallets.read')
def a_wallets_overview(): return wc.overview(get_db(), PR())
@admin.get('/wallets/<int:wid>/qr.svg')
@P('crypto.wallets.read')
def a_wallet_qr(wid):
    """Generated (never uploaded) QR of the validated company address. Encodes the bare public address, the same payload customers receive."""
    db = get_db(); addr, w = wc.wallet_qr_payload(db, PR(), wid)
    h = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
    if request.args.get('download') == '1':
        h['Content-Disposition'] = f"attachment; filename=\"vaulta-{w['id']}-deposit-qr.svg\""
        audit_qr(db, w)
    return Response(qr.svg(addr), mimetype='image/svg+xml', headers=h)
def audit_qr(db, w):
    from .audit import audit
    audit(db, A()['id'], 'crypto.wallet_qr_downloaded', 'company_crypto_wallet', w['id'], None, {'address': w['public_address']}, None)
@admin.get('/reconciliation/records')
@P('crypto.recon.read')
def a_recon_records():
    a = request.args
    return wc.records(get_db(), asset=a.get('asset') or None, network=a.get('network') or None, typ=a.get('type') or None, status=a.get('status') or None,
                      date_from=a.get('from') or None, date_to=a.get('to') or None, q=a.get('q') or None, page=a.get('page', 1, type=int), size=a.get('size', 20, type=int))
@admin.get('/reconciliation/summary')
@P('crypto.recon.read')
def a_recon_summary(): return wc.reconciliation_summary(get_db())
@admin.get('/assets/<int:aid>/settings')
@P('crypto.registry.read')
def a_asset_settings(aid): return wc.asset_settings(get_db(), PR(), aid)
@admin.post('/blockchain-networks/<int:nid>/explorer')
@P('crypto.registry.manage')
def a_network_explorer(nid):
    b = _body(); return {'explorer_url': wc.set_explorer(get_db(), A()['id'], nid, b.get('explorer_url'), b.get('reason'))}
