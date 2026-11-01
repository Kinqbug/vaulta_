import json, csv, io, html
from decimal import Decimal
from flask import Blueprint, request, jsonify, g, current_app, Response, send_file
from . import util, security as sec, ledger, portfolio as pf, finance as fin, legal
from .util import ApiError, need, text, parse_amount, parse_qty, cstr, page_args
from .db import get_db, one, all_, tx, val
from .notify import notify, TYPES
from .mailer import events as mail, prefs as mailprefs
from .routes_auth import body, public_user

bp = Blueprint('user', __name__, url_prefix='/api')
def me(): return g.user
def idem(): return request.headers.get('Idempotency-Key')
def U(f):
    return sec.login_required(f)

def _logo(symbol):
    """Admin-managed logo for a crypto symbol (one lookup per request); None falls back to a generated badge in the UI."""
    from . import crypto_catalog as cat
    if g.get('_logos') is None: g._logos = cat.logo_map(get_db())
    return g._logos.get(symbol)

def asset_row(a, wl=None):
    p, c = Decimal(a['last_price']), Decimal(a['prev_close'])
    return {'logo': _logo(a['symbol']) if a['asset_class'] == 'Crypto' else None, 'symbol': a['symbol'], 'name': a['name'], 'asset_class': a['asset_class'], 'price': a['last_price'], 'change_pct': str(((p - c) * 100 / c).quantize(Decimal('0.01'))) if c else '0.00',
            'status': a['status'], 'tradable': bool(a['tradable']), 'watched': (a['id'] in wl) if wl is not None else None}
def watch_ids(db, uid): return {r[0] for r in db.execute('SELECT asset_id FROM watchlist_items WHERE user_id=?', (uid,))}

# ---------------- dashboard ----------------
@bp.get('/dashboard')
@U
def dashboard():
    db, uid = get_db(), g.user['id']; wl = watch_ids(db, uid)
    goals = [fin.goal_view(x) for x in db.execute("SELECT * FROM savings_goals WHERE user_id=? AND status IN('active','completed') ORDER BY id LIMIT 6", (uid,))]
    recent = [ledger.txn_view(r) for r in db.execute('SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 8', (uid,))]
    watch = [asset_row(dict(a), wl) for a in db.execute('SELECT a.* FROM watchlist_items w JOIN assets a ON a.id=w.asset_id WHERE w.user_id=? ORDER BY w.added_at DESC LIMIT 6', (uid,))]
    return {'summary': pf.summary(db, uid), 'allocation': pf.allocation(db, uid), 'goals': goals, 'recent': recent, 'watchlist': watch,
            'unread': val(db, 'SELECT COUNT(*) FROM notifications WHERE user_id=? AND read_at IS NULL AND deleted_at IS NULL', uid), 'kyc_status': g.user['kyc_status'], 'mode': current_app.providers.mode}

@bp.get('/portfolio')
@U
def portfolio():
    db, uid = get_db(), g.user['id']; wl = watch_ids(db, uid)
    watch = [asset_row(dict(a), wl) for a in db.execute('SELECT a.* FROM watchlist_items w JOIN assets a ON a.id=w.asset_id WHERE w.user_id=? ORDER BY w.added_at DESC LIMIT 6', (uid,))]
    return {'summary': pf.summary(db, uid), 'holdings': pf.holdings(db, uid), 'allocation': pf.allocation(db, uid), 'watchlist': watch, 'recent_activity': pf.activity(db, uid, 6)}
def _csv_safe(v):
    v = str(v); return "'" + v if v[:1] in ('=', '+', '-', '@', '\t', '\r') and not v.lstrip('+-').replace('.', '', 1).isdigit() else v
@bp.get('/portfolio/export')
@U
def portfolio_export():
    db, uid = get_db(), g.user['id']; s_ = io.StringIO(); w = csv.writer(s_)
    w.writerow(['symbol', 'name', 'asset_class', 'quantity', 'avg_cost', 'price', 'value', 'cost_basis', 'unrealized_pl', 'realized_pl', 'allocation_pct'])
    for h in pf.holdings(db, uid): w.writerow([_csv_safe(h['symbol']), _csv_safe(h['name']), h['asset_class'], h['quantity'], h['avg_cost'], h['price'], h['value'], h['cost'], h['unrealized'], h['realized'], h['allocation_pct']])
    return Response(s_.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=vaulta-holdings.csv', 'Cache-Control': 'no-store'})
@bp.get('/portfolio/history')
@U
def portfolio_history(): return pf.history(get_db(), g.user['id'], request.args.get('range', '1M').upper())

# ---------------- profile ----------------
@bp.get('/profile')
@U
def profile():
    db = get_db(); p = dict(one(db, 'SELECT * FROM user_profiles WHERE user_id=?', g.user['id'])); p.pop('user_id')
    return {'profile': p, 'user': public_user(db, g.user, False), 'withdrawal_2fa': bool(g.user['withdrawal_2fa'])}
@bp.put('/profile')
@U
def put_profile():
    b, db = body(), get_db(); f = {}
    for k, n in (('first_name', 60), ('last_name', 60), ('country', 60), ('employment', 60), ('income_range', 40), ('investment_goal', 80), ('investment_horizon', 30), ('risk_tolerance', 30)):
        if k in b: f[k] = text(b[k], k, n, k in ('first_name', 'last_name'))
    if f.get('risk_tolerance') and f['risk_tolerance'] not in ('Conservative', 'Moderate', 'Aggressive'): raise ApiError(422, 'invalid_value', 'risk_tolerance must be Conservative, Moderate or Aggressive')
    with tx(db):
        before = dict(one(db, 'SELECT * FROM user_profiles WHERE user_id=?', g.user['id']))
        if f: db.execute('UPDATE user_profiles SET ' + ','.join(f'{k}=?' for k in f) + ' WHERE user_id=?', (*f.values(), g.user['id']))
        if 'phone' in b:
            new_phone = text(b['phone'], 'phone', 30, False) or None
            db.execute('UPDATE users SET phone=? WHERE id=?', (new_phone, g.user['id']))
            if (g.user['phone'] or None) != new_phone: mail.emit(db, 'account.phone_changed', g.user['id'], f"{g.user['id']}:{util.now()}", at=util.now())      # the number itself is never put in the email
        changed = [k.replace('_', ' ') for k, v in f.items() if (before.get(k) or '') != (v or '')]
        if changed: mail.emit(db, 'account.profile_updated', g.user['id'], f"{g.user['id']}:{util.now()}", changed=changed, at=util.now())
        if f.get('risk_tolerance'): db.execute('UPDATE users SET risk_profile=? WHERE id=?', (f['risk_tolerance'], g.user['id']))
    return profile()

# ---------------- products, assets, watchlist ----------------
def product_view(p):
    p = dict(p); p['min_investment'] = cstr(p.pop('min_investment_cents')); p['fee_pct'] = str(Decimal(p['fee_bps']) / 100); p['documents'] = json.loads(p.get('documents') or '[]'); return p
@bp.get('/assets')
def assets():
    db = get_db(); wl = watch_ids(db, g.user['id']) if g.user else None
    q = request.args.get('q', '').strip().lower(); cls = request.args.get('class')
    rows = [asset_row(dict(a), wl) for a in db.execute("SELECT * FROM assets WHERE status!='archived' ORDER BY asset_class,symbol")]
    return {'assets': [r for r in rows if (not q or q in r['symbol'].lower() or q in r['name'].lower()) and (not cls or r['asset_class'] == cls)]}
@bp.get('/assets/<symbol>/prices')
def asset_prices(symbol):
    from datetime import timedelta
    db = get_db(); a = fin._asset(db, symbol); span = {'1D': 1, '1W': 7, '1M': 30, '3M': 91, '6M': 182, '1Y': 365, '5Y': 1826}.get(request.args.get('range', '1M').upper())
    since = (util.utcnow() - timedelta(days=span)).strftime('%Y-%m-%dT%H:%M:%SZ') if span else '0'
    rows = all_(db, 'SELECT ts,price FROM asset_prices WHERE asset_id=? AND ts>=? ORDER BY ts', a['id'], since)
    if len(rows) > 120: rows = rows[::len(rows) // 120 + 1] + [rows[-1]]
    return {'symbol': a['symbol'], 'points': [{'t': r['ts'], 'price': r['price']} for r in rows]}
@bp.get('/watchlist')
@U
def watchlist():
    db = get_db(); wl = watch_ids(db, g.user['id'])
    return {'items': [asset_row(dict(a), wl) for a in db.execute('SELECT a.* FROM watchlist_items w JOIN assets a ON a.id=w.asset_id WHERE w.user_id=? ORDER BY w.added_at DESC', (g.user['id'],))]}
@bp.post('/watchlist')
@U
def watch_add():
    db = get_db(); a = fin._asset(db, body().get('symbol'))
    db.execute('INSERT OR IGNORE INTO watchlist_items(user_id,asset_id,added_at) VALUES(?,?,?)', (g.user['id'], a['id'], util.now())); return {'ok': True}
@bp.delete('/watchlist/<symbol>')
@U
def watch_remove(symbol):
    db = get_db(); a = fin._asset(db, symbol); db.execute('DELETE FROM watchlist_items WHERE user_id=? AND asset_id=?', (g.user['id'], a['id'])); return {'ok': True}

# ---------------- investing ----------------
@bp.post('/investments')
@U
def invest():
    legal.require_risk_ack(get_db(), g.user)
    b = body(); need(b, 'product_id', 'amount')
    return jsonify(fin.create_investment(get_db(), current_app.providers, g.user, int(b['product_id']), parse_amount(b['amount']), b.get('accepted_disclosures'), b.get('code'), idem())), 201
@bp.get('/investments')
@U
def my_investments():
    rows = all_(get_db(), 'SELECT i.ref,i.status,i.amount_cents,i.fee_cents,i.quantity,i.unit_price,i.created_at,i.completed_at,i.non_live,p.name product FROM investments i JOIN investment_products p ON p.id=i.product_id WHERE i.user_id=? ORDER BY i.id DESC LIMIT 100', g.user['id'])
    for r in rows: r['amount'] = cstr(r.pop('amount_cents')); r['fee'] = cstr(r.pop('fee_cents')); r['non_live'] = bool(r['non_live'])
    return {'investments': rows}

# ---------------- trading ----------------
@bp.post('/orders')
@U
def order():
    legal.require_risk_ack(get_db(), g.user)
    b = body(); need(b, 'symbol', 'side', 'order_type', 'quantity')
    return jsonify(fin.place_order(get_db(), current_app.providers, g.user, b['symbol'], b['side'], b['order_type'], parse_qty(b['quantity']), b.get('limit_price'), b.get('stop_price'), b.get('code'), idem())), 201
@bp.get('/orders')
@U
def orders():
    db = get_db(); st = request.args.get('status')
    return {'orders': [fin.order_view(db, r) for r in db.execute('SELECT * FROM orders WHERE user_id=?' + (' AND status=?' if st else '') + ' ORDER BY id DESC LIMIT 100', (g.user['id'], st) if st else (g.user['id'],))]}
@bp.post('/orders/<int:oid>/cancel')
@U
def order_cancel(oid): fin.cancel_order(get_db(), g.user['id'], oid); return {'ok': True}

# ---------------- deposits / withdrawals ----------------
def _crypto_only():
    """Deposits and withdrawals are cryptocurrency-only (see /api/crypto/*). The legacy bank/card endpoints stay off unless an admin enables them."""
    from .rbac import setting
    if not setting(get_db(), 'fiat_funding_enabled'): raise ApiError(409, 'crypto_only', 'Deposits and withdrawals are made in cryptocurrency. Choose a crypto asset on the Deposit & withdraw page.')
@bp.post('/deposits')
@U
def deposit():
    _crypto_only()
    b = body(); need(b, 'amount', 'method'); return jsonify(fin.create_deposit(get_db(), current_app.providers, g.user, parse_amount(b['amount']), b['method'], idem())), 201
@bp.get('/deposits')
@U
def deposits():
    db = get_db(); return {'deposits': [fin.deposit_view(db, r['ref']) for r in db.execute('SELECT ref FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 100', (g.user['id'],))]}
@bp.post('/deposits/<ref>/settle')
@U
def settle_deposit(ref):
    """Internal mode only: stands in for the provider's confirmation webhook when no payment provider is connected."""
    if current_app.providers.mode != 'non_live': raise ApiError(404, 'not_found', 'Not found')
    db = get_db(); d = one(db, 'SELECT * FROM deposits WHERE ref=? AND user_id=?', ref, g.user['id'])
    if not d: raise ApiError(404, 'not_found', 'Deposit not found')
    return fin.handle_payment_event(db, current_app.providers.payments.internal_event(d['external_ref']))
@bp.post('/withdrawals')
@U
def withdraw():
    _crypto_only()
    b = body(); need(b, 'amount', 'method', 'destination')
    return jsonify(fin.request_withdrawal(get_db(), current_app.providers, g.user, parse_amount(b['amount']), b['method'], b['destination'], b.get('code'), idem())), 201
@bp.get('/withdrawals')
@U
def withdrawals():
    db = get_db(); return {'withdrawals': [fin.withdrawal_view(db, r['ref']) for r in db.execute('SELECT ref FROM withdrawals WHERE user_id=? ORDER BY id DESC LIMIT 100', (g.user['id'],))]}
@bp.post('/withdrawals/<int:wid>/cancel')
@U
def withdrawal_cancel(wid): fin.cancel_withdrawal(get_db(), g.user, wid); return {'ok': True}
@bp.get('/accounts')
@U
def accounts():
    db = get_db(); out = []
    for a in db.execute("SELECT * FROM accounts WHERE user_id=? AND status='active'", (g.user['id'],)):
        b = ledger.balance(db, a['id']); out.append({'id': a['id'], 'kind': a['kind'], 'name': a['name'], 'currency': a['currency'], 'apy_bps': a['apy_bps'], **{k.replace('_cents', ''): cstr(v) for k, v in b.items()}})
    return {'accounts': out, 'limits': {'min_deposit': cstr(__import__('server.rbac', fromlist=['x']).setting(db, 'min_deposit_cents')), 'review_threshold': cstr(__import__('server.rbac', fromlist=['x']).setting(db, 'withdrawal_review_threshold_cents'))}}

# ---------------- transactions ----------------
@bp.get('/transactions')
@U
def transactions():
    """Cash transactions plus crypto funding (deposit requests, on-chain deposits, withdrawals), merged read-only and newest first. The cash side of a crypto conversion is hidden here because the crypto item already represents it (and shows its dollar value); it still counts in balances, statements and exports."""
    from . import crypto_deposits as cd
    db = get_db(); p, n = page_args(request.args); where, args = ['t.user_id=?', 't.metadata NOT LIKE \'%"crypto_conversion"%\''], [g.user['id']]
    typ, st, q = request.args.get('type'), request.args.get('status'), (request.args.get('q') or '').strip()
    for k in ('type', 'status'):
        if request.args.get(k): where.append(f't.{k}=?'); args.append(request.args[k])
    if q: where.append('(t.ref LIKE ? OR t.reference LIKE ?)'); args += [f'%{q}%'] * 2
    w = ' AND '.join(where); total = val(db, f'SELECT COUNT(*) FROM transactions t WHERE {w}', *args)
    rows = [ledger.txn_view(r) for r in db.execute(f'SELECT t.*,(SELECT COALESCE(SUM(amount_cents),0) FROM transaction_fees f WHERE f.transaction_id=t.id) fee_cents FROM transactions t WHERE {w} ORDER BY t.id DESC LIMIT ? OFFSET 0', (*args, p * n))]
    for r in rows: r['fee'] = cstr(r.pop('fee_cents'))
    crypto = []
    if typ in (None, '', 'deposit', 'withdrawal'):
        crypto = [cd.as_transaction(i) for i in cd.funding_items(db, g.user['id'])]
        crypto = [c for c in crypto if (not typ or c['type'] == typ) and (not st or c['status'] == st) and (not q or q.lower() in (c['ref'] + ' ' + (c['tx_hash'] or '')).lower())]
    merged = sorted(rows + crypto, key=lambda r: (r['created_at'], r['ref']), reverse=True)[(p - 1) * n:p * n]
    return {'transactions': merged, 'total': total + len(crypto), 'page': p, 'size': n}
@bp.get('/transactions/<ref>')
@U
def transaction(ref):
    from . import crypto_deposits as cd
    db = get_db(); t = one(db, 'SELECT * FROM transactions WHERE ref=? AND user_id=?', ref, g.user['id'])
    if not t:
        it = next((i for i in cd.funding_items(db, g.user['id']) if i['ref'] == ref), None)
        if not it: raise ApiError(404, 'not_found', 'Transaction not found')
        return {'transaction': cd.as_transaction(it), 'fees': [{'kind': 'withdrawal fee', 'amount': it['fee']}] if it.get('fee') not in (None, '0', '0.000000') else [], 'funding': it}
    fees = all_(db, 'SELECT kind,amount_cents FROM transaction_fees WHERE transaction_id=?', t['id'])
    return {'transaction': ledger.txn_view(t), 'fees': [{'kind': f['kind'], 'amount': cstr(f['amount_cents'])} for f in fees]}

# ---------------- savings & goals ----------------
@bp.get('/savings')
@U
def savings():
    db, uid = get_db(), g.user['id']; a = ledger.account_for(db, uid, 'savings')
    interest = val(db, "SELECT COALESCE(SUM(amount_cents),0) FROM transactions WHERE account_id=? AND type='interest' AND status='completed'", a['id'])
    recent = [ledger.txn_view(r) for r in db.execute('SELECT * FROM transactions WHERE account_id=? ORDER BY id DESC LIMIT 10', (a['id'],))]
    return {'account': {'name': a['name'], 'apy_pct': str(Decimal(a['apy_bps']) / 100), **{k.replace('_cents', ''): cstr(v) for k, v in ledger.balance(db, a['id']).items()}}, 'accrued_interest': cstr(interest), 'recent': recent,
            'note': 'APY is variable and not guaranteed. Interest accrues daily on the closing balance.'}
@bp.post('/savings/transfer')
@U
def sav_transfer():
    b = body(); need(b, 'direction', 'amount'); return jsonify(fin.savings_transfer(get_db(), g.user, b['direction'], parse_amount(b['amount']), idem())), 201
@bp.get('/goals')
@U
def goals():
    db = get_db(); rows = [fin.goal_view(r) for r in db.execute("SELECT * FROM savings_goals WHERE user_id=? AND status!='cancelled' ORDER BY id", (g.user['id'],))]
    for r in rows:
        a = one(db, 'SELECT next_run,last_result FROM automatic_contributions WHERE goal_id=? AND active=1', r['id']); r['auto'] = dict(a) if a else None
    return {'goals': rows}
@bp.post('/goals')
@U
def goal_create():
    b = body(); need(b, 'name', 'target_amount')
    c = parse_amount(b['contribution_amount']) if b.get('contribution_amount') else None
    return jsonify({'goal': fin.create_goal(get_db(), g.user, text(b['name'], 'name', 80), text(b.get('category', 'Custom'), 'category', 40), parse_amount(b['target_amount']), b.get('target_date') or None, b.get('frequency') or None, c)}), 201
@bp.post('/goals/<int:gid>/contribute')
@U
def goal_contribute(gid): b = body(); need(b, 'amount'); return jsonify(fin.contribute_goal(get_db(), g.user, gid, parse_amount(b['amount']), idem()))
@bp.post('/goals/<int:gid>/status')
@U
def goal_status(gid):
    st = body().get('status'); db = get_db()
    if st not in ('active', 'paused', 'cancelled'): raise ApiError(422, 'invalid_status', 'Invalid status')
    with tx(db):
        if db.execute('UPDATE savings_goals SET status=? WHERE id=? AND user_id=? AND status!="completed"', (st, gid, g.user['id'])).rowcount == 0: raise ApiError(404, 'not_found', 'Goal not found')
        db.execute('UPDATE automatic_contributions SET active=? WHERE goal_id=?', (1 if st == 'active' else 0, gid))
    return {'ok': True}

# ---------------- retirement ----------------
@bp.get('/retirement')
@U
def retirement(): return {'retirement': fin.get_retirement(get_db(), g.user['id'])}
@bp.put('/retirement')
@U
def retirement_save(): return {'retirement': fin.save_retirement(get_db(), g.user['id'], body())}
@bp.post('/retirement/project')
@U
def retirement_project():
    db = get_db(); b = body()
    class Tmp:  # compute without persisting: validate through a savepoint-less dry run
        pass
    from . import finance
    try:
        db.execute('BEGIN'); r = finance.save_retirement(db, g.user['id'], b)
    finally:
        db.execute('ROLLBACK') if db.in_transaction else None
    return {'retirement': r}

# ---------------- notifications ----------------
@bp.get('/notifications')
@U
def notifications():
    db = get_db(); rows = all_(db, 'SELECT id,type,title,body,link,read_at,created_at FROM notifications WHERE user_id=? AND deleted_at IS NULL ORDER BY id DESC LIMIT 100', g.user['id'])
    return {'notifications': rows, 'unread': sum(1 for r in rows if not r['read_at'])}
@bp.post('/notifications/read')
@U
def notif_read():
    b, db = body(), get_db(); ids = b.get('ids')
    if b.get('all'): db.execute('UPDATE notifications SET read_at=? WHERE user_id=? AND read_at IS NULL', (util.now(), g.user['id']))
    elif isinstance(ids, list): db.executemany('UPDATE notifications SET read_at=? WHERE id=? AND user_id=?', [(util.now(), int(i), g.user['id']) for i in ids[:100]])
    return {'ok': True}
@bp.post('/notifications/<int:nid>/unread')
@U
def notif_unread(nid): get_db().execute('UPDATE notifications SET read_at=NULL WHERE id=? AND user_id=?', (nid, g.user['id'])); return {'ok': True}
@bp.delete('/notifications/<int:nid>')
@U
def notif_delete(nid): get_db().execute('UPDATE notifications SET deleted_at=? WHERE id=? AND user_id=?', (util.now(), nid, g.user['id'])); return {'ok': True}
@bp.get('/notification-preferences')
@U
def prefs_get():
    db = get_db(); have = {r['type']: r for r in db.execute('SELECT * FROM notification_preferences WHERE user_id=?', (g.user['id'],))}
    return {'preferences': [{'type': t, 'in_app': bool(have[t]['in_app']) if t in have else True, 'email': (mailprefs.marketing_opted_in(db, g.user['id']) if t == 'promotional' else (bool(have[t]['email']) if t in have else False)), 'locked': t == 'security'} for t in TYPES]}
@bp.put('/notification-preferences')
@U
def prefs_put():
    db = get_db()
    with tx(db):
        for p in body().get('preferences', []):
            if p.get('type') in TYPES:
                db.execute('INSERT OR REPLACE INTO notification_preferences(user_id,type,in_app,email) VALUES(?,?,?,?)', (g.user['id'], p['type'], 1 if (p.get('in_app') or p['type'] == 'security') else 0, 1 if p.get('email') else 0))
                if p['type'] == 'promotional': mailprefs.set_marketing(db, g.user['id'], bool(p.get('email')), 'settings', sec.client_ip())      # recorded in the consent log
    return {'ok': True}

# ---------------- support ----------------
CATS = ('account', 'deposit', 'withdrawal', 'investment', 'trading', 'kyc', 'security', 'technical', 'transaction', 'wallet', 'email', 'general')
@bp.get('/support/tickets')
@U
def tickets(): return {'tickets': all_(get_db(), 'SELECT ref,category,subject,status,priority,created_at,updated_at FROM support_tickets WHERE user_id=? ORDER BY updated_at DESC', g.user['id'])}
@bp.post('/support/tickets')
@U
@sec.rate_limit('ticket', 10, 3600)
def ticket_create():
    b, db = body(), get_db(); need(b, 'category', 'subject', 'message')
    if b['category'] not in CATS: raise ApiError(422, 'invalid_category', 'Invalid category')
    with tx(db):
        ref = util.new_ref('TKT'); t = util.now()
        cur = db.execute('INSERT INTO support_tickets(ref,user_id,category,subject,created_at,updated_at) VALUES(?,?,?,?,?,?)', (ref, g.user['id'], b['category'], text(b['subject'], 'subject', 140), t, t))
        db.execute('INSERT INTO support_messages(ticket_id,author_id,body,created_at) VALUES(?,?,?,?)', (cur.lastrowid, g.user['id'], text(b['message'], 'message', 4000), t))
        mail.emit(db, 'support.ticket_created', g.user['id'], ref)
        from . import support_chat as sc
        sc.hist(db, cur.lastrowid, g.user['id'], None, 'open')
        for sid in sc.staff_ids(db): notify(db, sid, 'system', 'New support conversation', f"{ref}: {b['subject'][:80]}", '#/support')
    return jsonify({'ref': ref}), 201
@bp.get('/support/tickets/<ref>')
@U
def ticket_get(ref):
    db = get_db(); t = one(db, 'SELECT * FROM support_tickets WHERE ref=? AND user_id=?', ref, g.user['id'])
    if not t: raise ApiError(404, 'not_found', 'Ticket not found')
    return {'ticket': dict(t), 'messages': all_(db, 'SELECT is_staff,body,created_at FROM support_messages WHERE ticket_id=? AND internal=0 ORDER BY id', t['id'])}
@bp.post('/support/tickets/<ref>/messages')
@U
@sec.rate_limit('ticket_msg', 30, 600)
def ticket_reply(ref):
    db = get_db(); t = one(db, 'SELECT * FROM support_tickets WHERE ref=? AND user_id=?', ref, g.user['id'])
    if not t: raise ApiError(404, 'not_found', 'Ticket not found')
    if t['status'] == 'closed': raise ApiError(409, 'closed', 'This ticket is closed.')
    with tx(db):
        db.execute('INSERT INTO support_messages(ticket_id,author_id,body,created_at) VALUES(?,?,?,?)', (t['id'], g.user['id'], text(body().get('message'), 'message', 4000), util.now()))
        db.execute("UPDATE support_tickets SET status='open',updated_at=? WHERE id=?", (util.now(), t['id']))
    return {'ok': True}
@bp.post('/support/tickets/<ref>/close')
@U
def ticket_close(ref):
    db = get_db()
    with tx(db):
        t = one(db, 'SELECT id,status FROM support_tickets WHERE ref=? AND user_id=?', ref, g.user['id'])
        if not t: raise ApiError(404, 'not_found', 'Ticket not found')
        from . import support_chat as sc; sc.hist(db, t['id'], g.user['id'], t['status'], 'closed')
        db.execute("UPDATE support_tickets SET status='closed',closed_at=?,updated_at=? WHERE id=?", (util.now(), util.now(), t['id']))
    return {'ok': True}

# ---------------- KYC ----------------
def kyc_state(db, uid):
    k = dict(one(db, 'SELECT * FROM kyc_profiles WHERE user_id=?', uid))
    docs = all_(db, 'SELECT id,doc_type,original_name,size,created_at FROM kyc_documents WHERE user_id=? ORDER BY id', uid)
    hist = all_(db, 'SELECT decision,reason,created_at FROM kyc_reviews WHERE user_id=? ORDER BY id DESC', uid)
    return {'kyc': {k2: k[k2] for k2 in ('status', 'legal_name', 'date_of_birth', 'country', 'address', 'submitted_at', 'reviewed_at', 'rejection_reason', 'info_request')}, 'documents': docs, 'history': hist,
            'required': ['Legal name, date of birth, country and address', 'One government-issued photo ID (JPG, PNG or PDF, max 5 MB)']}
@bp.get('/kyc')
@U
def kyc_get(): return kyc_state(get_db(), g.user['id'])
@bp.put('/kyc')
@U
def kyc_put():
    b, db = body(), get_db(); k = one(db, 'SELECT status FROM kyc_profiles WHERE user_id=?', g.user['id'])
    if k['status'] in ('under_review', 'verified'): raise ApiError(409, 'locked', 'Your verification is already under review or complete.')
    vals = [text(b.get(f), f, 200) for f in ('legal_name', 'date_of_birth', 'country', 'address')]
    try: import datetime; datetime.date.fromisoformat(vals[1])
    except ValueError: raise ApiError(422, 'invalid_date', 'Date of birth must be YYYY-MM-DD')
    with tx(db): db.execute("UPDATE kyc_profiles SET legal_name=?,date_of_birth=?,country=?,address=?,status=CASE WHEN status='not_started' THEN 'pending' ELSE status END WHERE user_id=?", (*vals, g.user['id'])); db.execute("UPDATE users SET kyc_status=(SELECT status FROM kyc_profiles WHERE user_id=?) WHERE id=?", (g.user['id'], g.user['id']))
    return kyc_state(db, g.user['id'])
@bp.post('/kyc/documents')
@U
@sec.rate_limit('kycdoc', 15, 3600)
def kyc_upload():
    db = get_db(); f = request.files.get('file'); dt = request.form.get('doc_type', 'government_id')
    if dt not in ('government_id', 'proof_of_address', 'selfie'): raise ApiError(422, 'invalid_type', 'Invalid document type')
    if not f: raise ApiError(422, 'missing_file', 'Attach a file')
    if val(db, 'SELECT COUNT(*) FROM kyc_documents WHERE user_id=?', g.user['id']) >= 6: raise ApiError(409, 'limit', 'Document limit reached.')
    name, mime, digest = current_app.providers.storage.save(f.read())
    with tx(db): db.execute('INSERT INTO kyc_documents(user_id,doc_type,stored_name,original_name,mime,size,sha256,created_at) VALUES(?,?,?,?,?,?,?,?)', (g.user['id'], dt, name, text(f.filename, 'filename', 120, False)[:120], mime, request.content_length, digest, util.now()))
    return kyc_state(db, g.user['id']), 201
@bp.post('/kyc/submit')
@U
def kyc_submit():
    db = get_db(); k = dict(one(db, 'SELECT * FROM kyc_profiles WHERE user_id=?', g.user['id']))
    if k['status'] not in ('pending', 'requires_info', 'rejected'): raise ApiError(409, 'invalid_state', 'Nothing to submit.')
    if not k['legal_name']: raise ApiError(422, 'incomplete', 'Save your details first.')
    if not val(db, "SELECT COUNT(*) FROM kyc_documents WHERE user_id=? AND doc_type='government_id'", g.user['id']): raise ApiError(422, 'incomplete', 'Upload a government ID first.')
    with tx(db):
        current_app.providers.kyc.submit(g.user['id'], {})
        db.execute("UPDATE kyc_profiles SET status='under_review',submitted_at=?,info_request=NULL,rejection_reason=NULL WHERE user_id=?", (util.now(), g.user['id']))
        db.execute("UPDATE users SET kyc_status='under_review' WHERE id=?", (g.user['id'],)); sec.log_event(db, g.user['id'], 'kyc_submitted'); mail.emit(db, 'kyc.submitted', g.user['id'], f"{g.user['id']}:{util.now()}", at=util.now())
        notify(db, g.user['id'], 'kyc', 'Verification submitted', 'Our compliance team is reviewing your details.', '#/settings')
    return kyc_state(db, g.user['id'])

# ---------------- documents & statements ----------------
def _html(title, rows_html, note=''):
    return Response(f"<!doctype html><meta charset=utf-8><title>{html.escape(title)}</title><style>body{{font:15px/1.5 system-ui;max-width:760px;margin:40px auto;padding:0 20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:8px;border-bottom:1px solid #ddd;text-align:left}}.n{{background:#f7f7f5;padding:10px;border-radius:8px;font-size:13px}}</style><h1>VAULTA — {html.escape(title)}</h1>{sandbox_note}{rows_html}<p class=n>Demonstration platform. Figures are produced by VAULTA's ledger. Investing involves risk, including possible loss of principal; past performance does not guarantee future results.</p>", mimetype='text/html')
@bp.get('/documents')
@U
def documents():
    db = get_db(); months = [r[0] for r in db.execute("SELECT DISTINCT substr(created_at,1,7) m FROM transactions WHERE user_id=? ORDER BY m DESC LIMIT 12", (g.user['id'],))]
    conf = all_(db, "SELECT ref,type,created_at FROM transactions WHERE user_id=? AND status='completed' AND type IN('investment','trade','deposit','withdrawal') ORDER BY id DESC LIMIT 30", g.user['id'])
    return {'statements': [{'period': m, 'title': f'Account statement {m}'} for m in months], 'receipts': conf, 'disclosures': [{'title': 'Risk disclosure', 'url': '/legal/risk-disclosure/'}, {'title': 'Terms', 'url': '/legal/terms/'}],
            'tax_documents': [], 'tax_note': 'Tax documents are not generated in this demo.'}
@bp.get('/documents/statement/<period>')
@U
def statement(period):
    import re
    if not re.fullmatch(r'\d{4}-\d{2}', period): raise ApiError(422, 'bad_period', 'Use YYYY-MM')
    db = get_db(); rows = all_(db, "SELECT ref,type,direction,amount_cents,status,created_at FROM transactions WHERE user_id=? AND substr(created_at,1,7)=? ORDER BY id", g.user['id'], period)
    if request.args.get('format') == 'csv':
        s = io.StringIO(); w = csv.writer(s); w.writerow(['ref', 'type', 'direction', 'amount', 'status', 'created_at'])
        for r in rows: w.writerow([r['ref'], r['type'], r['direction'], cstr(r['amount_cents']), r['status'], r['created_at']])
        return Response(s.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename=vaulta-statement-{period}.csv'})
    body_ = '<table><tr><th>Ref<th>Type<th>Dir<th>Amount<th>Status<th>Date</tr>' + ''.join(f"<tr><td>{r['ref']}<td>{r['type']}<td>{r['direction']}<td>${cstr(r['amount_cents'])}<td>{r['status']}<td>{r['created_at']}</tr>" for r in rows) + '</table>'
    return _html(f'Statement {period}', body_)
@bp.get('/documents/receipt/<ref>')
@U
def receipt(ref):
    db = get_db(); t = one(db, 'SELECT * FROM transactions WHERE ref=? AND user_id=?', ref, g.user['id'])
    if not t: raise ApiError(404, 'not_found', 'Not found')
    fees = val(db, 'SELECT COALESCE(SUM(amount_cents),0) FROM transaction_fees WHERE transaction_id=?', t['id'])
    note = ''
    return _html(f"{t['type'].title()} confirmation", f"<table><tr><th>Reference<td>{t['ref']}<tr><th>Type<td>{t['type']}<tr><th>Amount<td>${cstr(t['amount_cents'])} ({t['direction']})<tr><th>Fees<td>${cstr(fees)}<tr><th>Status<td>{t['status']}<tr><th>Date<td>{t['created_at']}</table>", note)

