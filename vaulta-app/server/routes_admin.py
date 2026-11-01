"""Admin API. Every route is guarded server-side by require_perm(...). UI hiding is cosmetic only."""
import json, csv, io, re
from decimal import Decimal
from datetime import timedelta
from flask import Blueprint, request, jsonify, g, current_app, send_file, Response
from . import util, security as sec, ledger, portfolio as pf, finance as fin, rbac, accounting, assets as asset_registry, platform_gate
from .util import ApiError, need, text, parse_amount, cstr, page_args, to_cents
from .db import get_db, one, all_, tx, val
from .audit import audit, verify_chain
from .notify import notify
from .mailer import events as mail
from .routes_auth import body, public_user, bp as _auth

bp = Blueprint('admin', __name__, url_prefix='/api/admin')
P = sec.require_perm
def A(): return g.user['id']

def page(sql_from, where, args, select, order, cols=None):
    p, n = page_args(request.args); w = (' WHERE ' + ' AND '.join(where)) if where else ''
    total = val(get_db(), f'SELECT COUNT(*) FROM {sql_from}{w}', *args)
    rows = all_(get_db(), f'SELECT {select} FROM {sql_from}{w} ORDER BY {order} LIMIT ? OFFSET ?', *args, n, (p - 1) * n)
    return rows, {'total': total, 'page': p, 'size': n}
def like(q): return f'%{q.replace("%", "").replace("_", "")}%'
def money(rows, *keys):
    for r in rows:
        for k in keys:
            if k + '_cents' in r:
                v = r.pop(k + '_cents'); r[k] = cstr(v) if v is not None else None
    return rows
def uid_from(db, ident):
    u = one(db, 'SELECT * FROM users WHERE public_id=? OR email=?', ident, str(ident).lower())
    if not u: raise ApiError(404, 'not_found', 'User not found')
    return dict(u)
def reason_req(b, n=5):
    r = text(b.get('reason'), 'reason', 500)
    if len(r) < n: raise ApiError(422, 'reason_required', 'Please give a reason.')
    return r

# ================= overview =================
@bp.get('/overview')
@sec.require_staff
def overview():
    db = get_db(); now = util.utcnow(); d30 = (now - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    cash = val(db, "SELECT COALESCE(SUM(CASE direction WHEN 'credit' THEN amount_cents ELSE -amount_cents END),0) FROM transactions WHERE status='completed'")
    hold = sum(to_cents(Decimal(r['quantity']) * Decimal(r['last_price'])) for r in db.execute('SELECT h.quantity,a.last_price FROM portfolio_holdings h JOIN assets a ON a.id=h.asset_id'))
    m = {'total_users': val(db, 'SELECT COUNT(*) FROM users WHERE id NOT IN(SELECT user_id FROM user_roles)'),
         'active_users': val(db, 'SELECT COUNT(*) FROM users WHERE last_login_at>=?', d30), 'verified_users': val(db, "SELECT COUNT(*) FROM users WHERE kyc_status='verified' AND id NOT IN(SELECT user_id FROM user_roles)"),
         'aum': cstr(cash + hold), 'total_deposits': cstr(val(db, "SELECT COALESCE(SUM(amount_cents),0) FROM deposits WHERE status='completed'")),
         'total_withdrawals': cstr(val(db, "SELECT COALESCE(SUM(amount_cents),0) FROM withdrawals WHERE status='completed'")),
         'pending_withdrawals': val(db, "SELECT COUNT(*) FROM withdrawals WHERE status IN('pending','held_for_review','approved','processing')") + val(db, "SELECT COUNT(*) FROM crypto_withdrawals WHERE status IN('pending_approval','approved','signing','broadcast','confirming')"),
         'pending_kyc': val(db, "SELECT COUNT(*) FROM kyc_profiles WHERE status='under_review'"), 'active_investments': val(db, "SELECT COUNT(*) FROM investments WHERE status='completed'"),
         'open_orders': val(db, "SELECT COUNT(*) FROM orders WHERE status IN('open','pending','partially_filled')"), 'open_tickets': val(db, "SELECT COUNT(*) FROM support_tickets WHERE status NOT IN('resolved','closed')"),
         'pending_deposits': val(db, "SELECT COUNT(*) FROM deposits WHERE status IN('pending','processing')") + val(db, "SELECT COUNT(*) FROM crypto_deposits WHERE status IN('manual_review','risk_review','unconfirmed','confirming')")}
    def daily(sql, *a): return {r[0]: r[1] for r in db.execute(sql, a)}
    days = [(now - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(29, -1, -1)]
    dep = daily("SELECT substr(completed_at,1,10) d,SUM(amount_cents) FROM deposits WHERE status='completed' AND completed_at>=? GROUP BY d", d30)
    wdr = daily("SELECT substr(completed_at,1,10) d,SUM(amount_cents) FROM withdrawals WHERE status='completed' AND completed_at>=? GROUP BY d", d30)
    usr = daily('SELECT substr(created_at,1,10) d,COUNT(*) FROM users WHERE created_at>=? GROUP BY d', d30)
    act = daily("SELECT substr(created_at,1,10) d,COUNT(DISTINCT user_id) FROM security_events WHERE type='login_success' AND created_at>=? GROUP BY d", d30)
    inv = daily("SELECT substr(created_at,1,10) d,SUM(amount_cents) FROM investments WHERE status='completed' AND created_at>=? GROUP BY d", d30)
    vol = daily("SELECT substr(created_at,1,10) d,SUM(amount_cents) FROM transactions WHERE status='completed' AND created_at>=? GROUP BY d", d30)
    c = lambda d: [cstr(d.get(x, 0)) for x in days]
    aum = all_(db, 'SELECT day,SUM(value_cents) v FROM portfolio_snapshots GROUP BY day ORDER BY day DESC LIMIT 90')[::-1]
    alloc = {}
    for r in db.execute('SELECT a.asset_class c,h.quantity q,a.last_price p FROM portfolio_holdings h JOIN assets a ON a.id=h.asset_id'): alloc[r['c']] = alloc.get(r['c'], 0) + to_cents(Decimal(r['q']) * Decimal(r['p']))
    alloc['Cash & savings'] = max(cash, 0)
    return {'metrics': m, 'series': {'days': days, 'deposits': c(dep), 'withdrawals': c(wdr), 'new_users': [usr.get(x, 0) for x in days], 'active_users': [act.get(x, 0) for x in days],
            'investment_volume': c(inv), 'transaction_volume': c(vol), 'aum': [{'day': r['day'], 'value': cstr(r['v'])} for r in aum]},
            'allocation': [{'label': k, 'value': cstr(v)} for k, v in alloc.items() if v > 0], 'mode': current_app.providers.mode}

# ================= users =================
def _cust_where():
    """Customers = accounts with no staff role. Shared by the list, the export and the summary so the three can never disagree."""
    return ['u.id NOT IN(SELECT user_id FROM user_roles)' if request.args.get('staff') != '1' else 'u.id IN(SELECT user_id FROM user_roles)'], []
def _avatar(r):
    """Admin-side photo URL: served by the permission-checked /users/<id>/avatar route, versioned for cache-busting. None -> the UI shows initials."""
    v = r.pop('avatar_version', None); n = r.pop('avatar_name', None)
    r['avatar_url'] = f"/api/admin/users/{r['id']}/avatar?v={v}" if n else None
    return r
@bp.get('/users')
@P('users.read')
def users():
    db = get_db(); where, args = _cust_where()
    q = request.args.get('q')
    if q: where.append('(u.email LIKE ? OR u.public_id LIKE ? OR p.last_name LIKE ? OR p.first_name LIKE ? OR (p.first_name||\' \'||p.last_name) LIKE ?)'); args += [like(q)] * 5
    for k, col in (('status', 'u.status'), ('kyc', 'u.kyc_status')):
        if request.args.get(k): where.append(f'{col}=?'); args.append(request.args[k])
    order = {'newest': 'u.id DESC', 'oldest': 'u.id ASC', 'last_login': 'u.last_login_at DESC'}.get(request.args.get('sort'), 'u.id DESC')
    rows, meta = page('users u LEFT JOIN user_profiles p ON p.user_id=u.id', where, args, 'u.id _pk,u.public_id id,u.email,u.status,u.kyc_status,u.email_verified,u.totp_enabled,u.is_demo,u.created_at,u.last_login_at,p.first_name,p.last_name,p.country,p.avatar_name,p.avatar_version', order)
    for r in rows:
        r['total_balance'] = pf.summary(db, r.pop('_pk'))['net_worth']      # the same ledger-derived figure the customer sees (cash + portfolio); never computed in the browser
        _avatar(r)
    return {'items': rows, **meta}
@bp.get('/users/export.csv')
@P('users.export')
def users_export():
    """CSV of the customers matching the current filters (same filters as the list). Capped, audited, and spreadsheet-formula safe."""
    db = get_db(); where, args = _cust_where(); q = request.args.get('q')
    if q: where.append("(u.email LIKE ? OR u.public_id LIKE ? OR p.last_name LIKE ? OR p.first_name LIKE ? OR (p.first_name||' '||p.last_name) LIKE ?)"); args += [like(q)] * 5
    for k, col in (('status', 'u.status'), ('kyc', 'u.kyc_status')):
        if request.args.get(k): where.append(f'{col}=?'); args.append(request.args[k])
    rows = db.execute('SELECT u.id pk,u.public_id,u.email,u.status,u.kyc_status,u.created_at,u.last_login_at,p.first_name,p.last_name,p.country FROM users u LEFT JOIN user_profiles p ON p.user_id=u.id WHERE ' + ' AND '.join(where) + ' ORDER BY u.id DESC LIMIT 5000', args).fetchall()
    safe = lambda v: ("'" + str(v)) if isinstance(v, str) and v[:1] in ('=', '+', '-', '@', '\t', '\r') else ('' if v is None else v)
    out = io.StringIO(); w = csv.writer(out); w.writerow(['customer_id', 'first_name', 'last_name', 'email', 'country', 'account_status', 'kyc_status', 'total_balance_usd', 'joined', 'last_sign_in'])
    for r in rows: w.writerow([safe(x) for x in (r['public_id'], r['first_name'], r['last_name'], r['email'], r['country'], r['status'], r['kyc_status'], pf.summary(db, r['pk'])['net_worth'], r['created_at'], r['last_login_at'])])
    audit(db, A(), 'users.exported', 'user', None, None, {'rows': len(rows), 'filters': {k: request.args.get(k) for k in ('q', 'status', 'kyc')}}, 'Customer list export')
    return Response(out.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=customers.csv', 'X-Content-Type-Options': 'nosniff'})
@bp.get('/users/summary')
@P('users.read')
def users_summary():
    """Real counts for the Customers dashboard (cards, segments, recent activity). Change figures compare with the same query run as of 7 days ago where history exists."""
    db = get_db(); now = util.utcnow(); d7 = (now - timedelta(days=7)).strftime('%Y-%m-%dT%H:%M:%SZ'); d30 = (now - timedelta(days=30)).strftime('%Y-%m-%dT%H:%M:%SZ')
    C = "u.id NOT IN(SELECT user_id FROM user_roles)"
    n = lambda extra='', *a: val(db, f'SELECT COUNT(*) FROM users u WHERE {C}{extra}', *a)
    total = n(); by_kyc = {r[0]: r[1] for r in db.execute(f'SELECT kyc_status,COUNT(*) FROM users u WHERE {C} GROUP BY kyc_status')}
    by_status = {r[0]: r[1] for r in db.execute(f'SELECT status,COUNT(*) FROM users u WHERE {C} GROUP BY status')}
    recent = []
    for r in db.execute("SELECT a.action,a.entity_id,a.created_at,a.reason FROM audit_logs a WHERE a.entity='user' AND (a.action LIKE 'user.%' OR a.action LIKE 'kyc.%') AND a.action NOT IN('kyc.viewed','kyc.document_viewed') ORDER BY a.id DESC LIMIT 8"):
        e = one(db, 'SELECT email FROM users WHERE public_id=?', r['entity_id'])
        recent.append({'kind': r['action'], 'who': e['email'] if e else r['entity_id'], 'at': r['created_at'], 'detail': r['reason']})
    for r in db.execute(f"SELECT email,created_at FROM users u WHERE {C} ORDER BY id DESC LIMIT 5"): recent.append({'kind': 'user.registered', 'who': r['email'], 'at': r['created_at'], 'detail': None})
    recent.sort(key=lambda x: x['at'] or '', reverse=True)
    return {'total': total, 'new_7d': n(' AND u.created_at>=?', d7), 'verified': by_kyc.get('verified', 0), 'pending_kyc': by_kyc.get('under_review', 0) + by_kyc.get('requires_info', 0),
            'restricted': by_status.get('restricted', 0) + by_status.get('suspended', 0), 'active_30d': n(' AND u.last_login_at>=?', d30), 'by_kyc': by_kyc, 'by_status': by_status, 'recent': recent[:8]}
@bp.get('/users/<pid>/avatar')
@P('users.read')
def user_avatar(pid):
    """A customer's profile photo for staff. Same file the customer uploaded in Profile Settings; 404 when they have none (the UI then shows initials)."""
    db = get_db(); u = uid_from(db, pid)
    p = one(db, 'SELECT avatar_name,avatar_sha256 FROM user_profiles WHERE user_id=?', u['id'])
    if not p or not p['avatar_name']: raise ApiError(404, 'not_found', 'No profile photo')
    from . import uploads
    data = uploads.load(current_app.config['UPLOAD_DIR'], 'avatars', p['avatar_name'])
    mime = {'png': 'image/png', 'jpg': 'image/jpeg', 'webp': 'image/webp'}[p['avatar_name'].rsplit('.', 1)[1]]
    return uploads.file_response(data, mime, etag=p['avatar_sha256']).make_conditional(request)
@bp.get('/users/<pid>')
@P('users.read')
def user_detail(pid):
    db = get_db(); u = uid_from(db, pid); uid = u['id']
    ph = u['phone']; out = public_user(db, u)
    out['phone'] = ('•••' + ph[-3:]) if ph else None
    av = one(db, 'SELECT avatar_name,avatar_version FROM user_profiles WHERE user_id=?', uid); out['avatar_url'] = f"/api/admin/users/{u['public_id']}/avatar?v={av['avatar_version']}" if av and av['avatar_name'] else None
    prof = dict(one(db, 'SELECT first_name,last_name,country,investment_goal,investment_horizon,risk_tolerance FROM user_profiles WHERE user_id=?', uid))
    accts = [{'kind': a['kind'], 'name': a['name'], **{k.replace('_cents', ''): cstr(v) for k, v in ledger.balance(db, a['id']).items()}} for a in db.execute('SELECT * FROM accounts WHERE user_id=?', (uid,))]
    tx_ = [ledger.txn_view(r) for r in db.execute('SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 10', (uid,))]
    dep = money(all_(db, 'SELECT ref,amount_cents,method,status,created_at FROM deposits WHERE user_id=? ORDER BY id DESC LIMIT 5', uid), 'amount')
    wdr = money(all_(db, 'SELECT id,ref,amount_cents,method,status,created_at FROM withdrawals WHERE user_id=? ORDER BY id DESC LIMIT 5', uid), 'amount')
    ev = all_(db, 'SELECT type,ip,detail,created_at FROM security_events WHERE user_id=? ORDER BY id DESC LIMIT 15', uid)
    return {'user': out, 'profile': prof, 'summary': pf.summary(db, uid), 'holdings': pf.holdings(db, uid), 'accounts': accts, 'transactions': tx_, 'deposits': dep, 'withdrawals': wdr, 'security_events': ev, 'failed_logins': u['failed_logins'], 'locked_until': u['locked_until']}
@bp.post('/users/<pid>/status')
@P('users.manage')
def user_status(pid):
    b, db = body(), get_db(); st = b.get('status'); reason = reason_req(b)
    if st not in ('active', 'restricted', 'suspended'): raise ApiError(422, 'invalid_status', 'Status must be active, restricted or suspended')
    with tx(db):
        u = uid_from(db, pid)
        if u['id'] == A(): raise ApiError(403, 'forbidden', 'You cannot change your own account status.')
        if sec.user_perms(db, u['id']) and not sec.has_perm(db, 'roles.manage'): raise ApiError(403, 'forbidden', 'Only role managers can change staff accounts.')
        db.execute('UPDATE users SET status=?,failed_logins=0,locked_until=NULL WHERE id=?', (st, u['id']))
        if st == 'suspended': sec.revoke_sessions(db, u['id'])
        sec.log_event(db, u['id'], f'account_{st}', reason); audit(db, A(), 'user.status_changed', 'user', u['public_id'], {'status': u['status']}, {'status': st}, reason)
        notify(db, u['id'], 'security', 'Account status updated', f'Your account is now {st}. Contact support if you have questions.')
        if st in ('restricted', 'suspended') and u['status'] != st: mail.emit(db, 'security.account_restricted', u['id'], f"{u['id']}:{st}:{util.now()}", at=util.now())
        elif st == 'active' and u['status'] in ('restricted', 'suspended'): mail.emit(db, 'account.access_restored', u['id'], f"{u['id']}:{util.now()}", at=util.now())
    return {'ok': True}
@bp.post('/users/<pid>/unlock')
@P('users.manage')
def user_unlock(pid):
    db = get_db(); b = body(); reason = reason_req(b)
    with tx(db):
        u = uid_from(db, pid); db.execute('UPDATE users SET failed_logins=0,locked_until=NULL WHERE id=?', (u['id'],)); audit(db, A(), 'user.unlocked', 'user', u['public_id'], {'locked_until': u['locked_until']}, {'locked_until': None}, reason)
    return {'ok': True}

# ================= KYC =================
@bp.get('/kyc')
@P('kyc.review')
def kyc_queue():
    st = request.args.get('status', 'under_review'); where, args = [], []
    if st != 'all': where.append('k.status=?'); args.append(st)
    rows, meta = page('kyc_profiles k JOIN users u ON u.id=k.user_id', where + ["k.status!='not_started'"], args, 'u.public_id user_id,u.email,k.status,k.legal_name,k.country,k.submitted_at,k.reviewed_at', 'COALESCE(k.submitted_at,k.reviewed_at) ASC')
    return {'items': rows, **meta}
@bp.get('/kyc/<pid>')
@P('kyc.review')
def kyc_detail(pid):
    db = get_db(); u = uid_from(db, pid); k = dict(one(db, 'SELECT * FROM kyc_profiles WHERE user_id=?', u['id'])); k.pop('user_id')
    audit(db, A(), 'kyc.viewed', 'user', u['public_id'])
    return {'user': {'id': u['public_id'], 'email': u['email']}, 'kyc': k, 'documents': all_(db, 'SELECT id,doc_type,original_name,mime,size,created_at FROM kyc_documents WHERE user_id=?', u['id']),
            'reviews': all_(db, 'SELECT r.decision,r.reason,r.created_at,x.email reviewer FROM kyc_reviews r LEFT JOIN users x ON x.id=r.reviewer_id WHERE r.user_id=? ORDER BY r.id DESC', u['id'])}
@bp.get('/kyc/documents/<int:did>')
@P('kyc.review')
def kyc_doc(did):
    db = get_db(); d = one(db, 'SELECT * FROM kyc_documents WHERE id=?', did)
    if not d: raise ApiError(404, 'not_found', 'Document not found')
    audit(db, A(), 'kyc.document_viewed', 'kyc_document', did)
    return send_file(current_app.providers.storage.path(d['stored_name']), mimetype=d['mime'], as_attachment=True, download_name=f"kyc-{did}.{d['stored_name'].rsplit('.',1)[-1]}")
@bp.post('/kyc/<pid>/decision')
@P('kyc.review')
def kyc_decide(pid):
    b, db = body(), get_db(); dec = b.get('decision')
    if dec not in ('approve', 'reject', 'request_info'): raise ApiError(422, 'invalid_decision', 'Invalid decision')
    reason = text(b.get('reason'), 'reason', 500, required=dec != 'approve')
    with tx(db):
        u = uid_from(db, pid); k = dict(one(db, 'SELECT * FROM kyc_profiles WHERE user_id=?', u['id']))
        if u['id'] == A(): raise ApiError(403, 'separation_of_duties', 'You cannot review your own verification.')
        if k['status'] != 'under_review': raise ApiError(409, 'invalid_state', 'This submission is not awaiting review.')
        new = {'approve': 'verified', 'reject': 'rejected', 'request_info': 'requires_info'}[dec]; t = util.now()
        db.execute('UPDATE kyc_profiles SET status=?,reviewed_by=?,reviewed_at=?,rejection_reason=?,info_request=? WHERE user_id=?', (new, A(), t, reason if dec == 'reject' else None, reason if dec == 'request_info' else None, u['id']))
        db.execute('UPDATE users SET kyc_status=? WHERE id=?', (new, u['id']))
        db.execute('INSERT INTO kyc_reviews(user_id,reviewer_id,decision,reason,created_at) VALUES(?,?,?,?,?)', (u['id'], A(), new, reason, t))
        audit(db, A(), f'kyc.{dec}', 'user', u['public_id'], {'status': k['status']}, {'status': new}, reason)
        notify(db, u['id'], 'kyc', {'verified': 'Identity verified', 'rejected': 'Verification unsuccessful', 'requires_info': 'More information needed'}[new], reason or 'You can now deposit, withdraw and trade.', '#/settings')
        mail.emit(db, {'approve': 'kyc.approved', 'reject': 'kyc.rejected', 'request_info': 'kyc.info_required'}[dec], u['id'], f"{u['id']}:{t}", reason=reason or '', at=t)
    return {'status': new}

# ================= accounts / savings / retirement =================
@bp.get('/accounts')
@P('accounts.read')
def accounts():
    db = get_db(); where, args = [], []
    if request.args.get('kind'): where.append('a.kind=?'); args.append(request.args['kind'])
    if request.args.get('q'): where.append('u.email LIKE ?'); args.append(like(request.args['q']))
    rows, meta = page('accounts a JOIN users u ON u.id=a.user_id', where, args, 'a.id,a.kind,a.name,a.status,a.apy_bps,u.public_id user_id,u.email', 'a.id DESC')
    for r in rows: r.update({k.replace('_cents', ''): cstr(v) for k, v in ledger.balance(db, r['id']).items()})
    return {'items': rows, **meta}
@bp.get('/savings')
@P('savings.read')
def savings():
    rows, meta = page('savings_goals g JOIN users u ON u.id=g.user_id', [], [], 'g.id,u.email,g.name,g.category,g.target_cents,g.current_cents,g.target_date,g.frequency,g.contribution_cents,g.status', 'g.id DESC')
    for r in rows: r['progress_pct'] = str(min(Decimal(100), Decimal(r['current_cents']) * 100 / r['target_cents']).quantize(Decimal('0.1')))
    tot = val(get_db(), "SELECT COALESCE(SUM(CASE direction WHEN 'credit' THEN amount_cents ELSE -amount_cents END),0) FROM transactions t JOIN accounts a ON a.id=t.account_id WHERE a.kind='savings' AND t.status='completed'")
    return {'items': money(rows, 'target', 'current', 'contribution'), **meta, 'total_savings': cstr(tot), 'accrued_interest': cstr(val(get_db(), "SELECT COALESCE(SUM(amount_cents),0) FROM transactions WHERE type='interest' AND status='completed'"))}
@bp.post('/jobs/interest')
@P('jobs.run')
def job_interest():
    db = get_db()
    with tx(db): r = fin.accrue_interest(db); audit(db, A(), 'job.interest_accrual', 'system', None, None, r)
    return r
@bp.post('/jobs/contributions')
@P('jobs.run')
def job_contrib():
    db = get_db(); r = fin.run_due_contributions(db); audit(db, A(), 'job.auto_contributions', 'system', None, None, r); return r
@bp.post('/jobs/snapshots')
@P('jobs.run')
def job_snap():
    db = get_db()
    with tx(db): pf.snapshot_all(db); db.execute('UPDATE assets SET prev_close=last_price'); audit(db, A(), 'job.snapshots', 'system')
    return {'ok': True}
@bp.get('/retirement')
@P('retirement.read')
def retirement():
    rows, meta = page('retirement_plans r JOIN users u ON u.id=r.user_id', [], [], 'u.email,r.user_id,r.current_age,r.retirement_age,r.current_savings_cents,r.monthly_contribution_cents,r.employer_match_pct,r.expected_return_pct,r.target_cents', 'r.updated_at DESC')
    for r in rows:
        r['projected'] = fin.project_retirement({'current_age': r['current_age'], 'retirement_age': r['retirement_age'], 'current_savings_cents': r['current_savings_cents'], 'monthly_contribution_cents': r['monthly_contribution_cents'], 'employer_match_pct': r['employer_match_pct'], 'expected_return_pct': r['expected_return_pct']})['final']; r.pop('user_id')
    return {'items': money(rows, 'current_savings', 'monthly_contribution', 'target'), **meta}

# ================= investment products & assets =================
PF = ('slug', 'name', 'category', 'kind', 'issuer', 'risk', 'horizon', 'description', 'score', 'illustrative_return_pct')
@bp.get('/investments')
@P('investments.manage')
def products():
    rows = all_(get_db(), 'SELECT p.*,a.symbol,(SELECT COUNT(*) FROM investments i WHERE i.product_id=p.id AND i.status="completed") investors FROM investment_products p JOIN assets a ON a.id=p.asset_id ORDER BY p.id')
    for r in rows: r['min_investment'] = cstr(r.pop('min_investment_cents')); r['fee_pct'] = str(Decimal(r['fee_bps']) / 100)
    recent = money(all_(get_db(), 'SELECT i.ref,u.email,p.name product,i.amount_cents,i.fee_cents,i.status,i.non_live,i.created_at FROM investments i JOIN users u ON u.id=i.user_id JOIN investment_products p ON p.id=i.product_id ORDER BY i.id DESC LIMIT 25'), 'amount', 'fee')
    return {'products': rows, 'recent': recent}
def _prod_vals(b, partial=False):
    f = {}
    for k in PF:
        if k in b: f[k] = text(b[k], k, 1000, k not in ('kind', 'issuer', 'description', 'illustrative_return_pct'))
    if 'risk' in f and f['risk'] not in ('Low', 'Moderate', 'High'): raise ApiError(422, 'invalid_value', 'risk must be Low, Moderate or High')
    if 'min_investment' in b: f['min_investment_cents'] = parse_amount(b['min_investment'], 'min_investment')
    if 'fee_pct' in b:
        d = util.dec(b['fee_pct'], 'fee_pct')
        if not (0 <= d <= 10): raise ApiError(422, 'out_of_range', 'fee_pct must be 0–10')
        f['fee_bps'] = int(d * 100)
    if 'status' in b:
        if b['status'] not in ('active', 'paused', 'archived'): raise ApiError(422, 'invalid_status', 'Invalid status')
        f['status'] = b['status']
    if 'score' in f: f['score'] = int(f['score'])
    return f
@bp.post('/investments')
@P('investments.manage')
def product_create():
    b, db = body(), get_db(); need(b, 'name', 'slug', 'symbol', 'category', 'risk', 'min_investment', 'fee_pct')
    f = _prod_vals(b)
    if not re.fullmatch(r'[a-z0-9-]{3,60}', f['slug']): raise ApiError(422, 'invalid_slug', 'slug: lowercase letters, numbers, hyphens')
    with tx(db):
        a = one(db, 'SELECT * FROM assets WHERE symbol=?', str(b['symbol']).upper())
        if not a: raise ApiError(404, 'unknown_asset', 'Create the underlying asset first (Assets).')
        f['asset_id'] = a['id']; f['created_at'] = f['updated_at'] = util.now()
        cur = db.execute(f"INSERT INTO investment_products({','.join(f)}) VALUES({','.join('?'*len(f))})", tuple(f.values()))
        audit(db, A(), 'product.created', 'investment_product', cur.lastrowid, None, f, b.get('reason'))
    return {'id': cur.lastrowid}, 201
@bp.patch('/investments/<int:pid>')
@P('investments.manage')
def product_update(pid):
    b, db = body(), get_db(); f = _prod_vals(b); f.pop('slug', None)
    if not f: raise ApiError(422, 'nothing_to_update', 'No changes supplied')
    with tx(db):
        old = one(db, 'SELECT * FROM investment_products WHERE id=?', pid)
        if not old: raise ApiError(404, 'not_found', 'Product not found')
        f['updated_at'] = util.now(); db.execute('UPDATE investment_products SET ' + ','.join(f'{k}=?' for k in f) + ' WHERE id=?', (*f.values(), pid))
        audit(db, A(), 'product.updated', 'investment_product', pid, {k: old[k] for k in f if k in old.keys()}, f, b.get('reason'))
    return {'ok': True}
@bp.get('/assets')
@P('assets.manage')
def assets():
    rows = all_(get_db(), "SELECT a.*,(SELECT COUNT(*) FROM portfolio_holdings h WHERE h.asset_id=a.id AND CAST(h.quantity AS REAL)>0) holders FROM assets a ORDER BY asset_class,symbol")
    return {'items': rows}
@bp.post('/assets')
@P('assets.manage')
def asset_create():
    b, db = body(), get_db(); need(b, 'symbol', 'name', 'asset_class', 'price'); price = util.dec(b['price'], 'price')
    if price <= 0: raise ApiError(422, 'invalid_price', 'Price must be positive')
    if b['asset_class'] not in pf.CLASSES: raise ApiError(422, 'invalid_class', 'Unknown asset class')
    sym = text(b['symbol'], 'symbol', 12).upper()
    with tx(db):
        if one(db, 'SELECT 1 FROM assets WHERE symbol=?', sym): raise ApiError(409, 'exists', 'Symbol already exists')
        cur = db.execute('INSERT INTO assets(symbol,name,asset_class,provider,external_id,last_price,prev_close,created_at,price_updated_at) VALUES(?,?,?,?,?,?,?,?,?)', (sym, text(b['name'], 'name', 100), b['asset_class'], text(b.get('provider', 'internal'), 'provider', 40), text(b.get('external_id'), 'external_id', 80, False) or None, str(price), str(price), util.now(), util.now()))
        db.execute('INSERT INTO asset_prices(asset_id,ts,price) VALUES(?,?,?)', (cur.lastrowid, util.now(), str(price))); audit(db, A(), 'asset.created', 'asset', cur.lastrowid, None, {'symbol': sym, 'price': str(price)})
    return {'id': cur.lastrowid}, 201
@bp.patch('/assets/<int:aid>')
@P('assets.manage')
def asset_update(aid):
    b, db = body(), get_db(); f = {}
    for k in ('name', 'provider', 'external_id'):
        if k in b: f[k] = text(b[k], k, 100, k == 'name')
    if 'status' in b:
        if b['status'] not in ('active', 'halted', 'archived'): raise ApiError(422, 'invalid_status', 'Invalid status')
        f['status'] = b['status']
    if 'tradable' in b: f['tradable'] = 1 if b['tradable'] else 0
    with tx(db):
        old = one(db, 'SELECT * FROM assets WHERE id=?', aid)
        if not old: raise ApiError(404, 'not_found', 'Asset not found')
        if f: db.execute('UPDATE assets SET ' + ','.join(f'{k}=?' for k in f) + ' WHERE id=?', (*f.values(), aid)); audit(db, A(), 'asset.updated', 'asset', aid, {k: old[k] for k in f}, f, b.get('reason'))
    return {'ok': True}
@bp.post('/assets/<int:aid>/price')
@P('assets.manage')
def asset_price(aid):
    b = body(); need(b, 'price'); n = fin.set_price(get_db(), current_app.providers, aid, util.dec(b['price'], 'price'), A(), reason_req(b)); return {'orders_filled': n}
@bp.post('/jobs/market-tick')
@P('jobs.run')
def tick():
    db = get_db(); n = fin.market_tick(db, current_app.providers); audit(db, A(), 'job.market_tick', 'system', None, None, {'assets': n}); return {'assets_updated': n, 'mode': current_app.providers.mode}

# ================= orders =================
@bp.get('/orders')
@P('orders.read')
def orders():
    db = get_db(); where, args = [], []
    for k in ('status', 'side', 'order_type'):
        if request.args.get(k): where.append(f'o.{k}=?'); args.append(request.args[k])
    if request.args.get('q'): where.append('(o.ref LIKE ? OR u.email LIKE ? OR a.symbol LIKE ?)'); args += [like(request.args['q'])] * 3
    rows, meta = page('orders o JOIN users u ON u.id=o.user_id JOIN assets a ON a.id=o.asset_id', where, args, 'o.id,o.ref,u.email,a.symbol,o.side,o.order_type,o.quantity,o.limit_price,o.stop_price,o.execution_price,o.fee_cents,o.status,o.non_live,o.created_at,o.executed_at,o.reason', 'o.id DESC')
    return {'items': money(rows, 'fee'), **meta}
@bp.post('/orders/<int:oid>/cancel')
@P('orders.manage')
def order_cancel(oid): fin.cancel_order(get_db(), None, oid, A(), reason_req(body())); return {'ok': True}
@bp.post('/jobs/match-orders')
@P('orders.manage')
def match():
    db = get_db(); n = fin.match_open_orders(db, current_app.providers); audit(db, A(), 'job.match_orders', 'system', None, None, {'filled': n}); return {'filled': n}

# ================= transactions =================
@bp.get('/transactions')
@P('transactions.read')
def transactions():
    where, args = [], []
    for k in ('type', 'status'):
        if request.args.get(k): where.append(f't.{k}=?'); args.append(request.args[k])
    if request.args.get('q'): where.append('(t.ref LIKE ? OR t.reference LIKE ? OR u.email LIKE ?)'); args += [like(request.args['q'])] * 3
    rows, meta = page('transactions t JOIN users u ON u.id=t.user_id', where, args, 't.id,t.ref,u.email,t.type,t.direction,t.amount_cents,t.status,t.reference,t.non_live,t.created_at', 't.id DESC')
    return {'items': money(rows, 'amount'), **meta}
@bp.get('/transactions/<ref>')
@P('transactions.read')
def transaction(ref):
    db = get_db(); t = one(db, 'SELECT t.*,u.email,u.public_id uid,a.kind account_kind,a.name account_name FROM transactions t JOIN users u ON u.id=t.user_id JOIN accounts a ON a.id=t.account_id WHERE t.ref=?', ref)
    if not t: raise ApiError(404, 'not_found', 'Transaction not found')
    v = ledger.txn_view(t); v.update({'email': t['email'], 'user_id': t['uid'], 'account': f"{t['account_kind']} · {t['account_name']}"})
    trail = all_(db, "SELECT a.action,a.reason,a.created_at,a.prev_value,a.new_value,x.email actor FROM audit_logs a LEFT JOIN users x ON x.id=a.actor_id WHERE (a.entity='transaction' AND a.entity_id=?) ORDER BY a.id", str(t['id']))
    return {'transaction': v, 'fees': money(all_(db, 'SELECT kind,amount_cents FROM transaction_fees WHERE transaction_id=?', t['id']), 'amount'), 'audit_trail': trail,
            'related': {'deposit': one(db, 'SELECT ref,status FROM deposits WHERE txn_id=?', t['id']) and dict(one(db, 'SELECT ref,status FROM deposits WHERE txn_id=?', t['id'])),
                        'withdrawal': one(db, 'SELECT ref,status FROM withdrawals WHERE txn_id=?', t['id']) and dict(one(db, 'SELECT ref,status FROM withdrawals WHERE txn_id=?', t['id']))}}
@bp.post('/transactions/adjustments')
@P('transactions.adjust')
def adjustment():
    b, db = body(), get_db(); need(b, 'user', 'direction', 'amount', 'reason')
    if b['direction'] not in ('credit', 'debit'): raise ApiError(422, 'invalid_direction', 'direction must be credit or debit')
    reason = reason_req(b, 10); amt = parse_amount(b['amount'])
    with tx(db):
        u = uid_from(db, b['user']); acct = ledger.account_for(db, u['id'], b.get('account', 'cash'))
        tid = ledger.post(db, user_id=u['id'], account_id=acct['id'], type='adjustment', direction=b['direction'], amount_cents=amt, status='completed', non_live=False, reference='ADJ:' + A().__str__(), metadata={'reason': reason, 'by': g.user['public_id']})
        audit(db, A(), 'ledger.adjustment', 'transaction', tid, None, {'user': u['public_id'], 'direction': b['direction'], 'amount': cstr(amt), 'account': acct['kind']}, reason)
        notify(db, u['id'], 'transaction', 'Account adjustment', f"A ${cstr(amt)} {b['direction']} adjustment was posted. Reason: {reason}", '#/transactions')
    return {'ok': True, 'transaction_id': tid}, 201

# ================= deposits / withdrawals =================
@bp.get('/deposits')
@P('deposits.read')
def deposits():
    where, args = [], []
    if request.args.get('status'): where.append('d.status=?'); args.append(request.args['status'])
    if request.args.get('q'): where.append('(d.ref LIKE ? OR u.email LIKE ? OR d.external_ref LIKE ?)'); args += [like(request.args['q'])] * 3
    rows, meta = page('deposits d JOIN users u ON u.id=d.user_id', where, args, 'd.id,d.ref,u.email,d.amount_cents,d.fee_cents,d.method,d.provider,d.external_ref,d.status,d.non_live,d.created_at,d.completed_at', 'd.id DESC')
    return {'items': money(rows, 'amount', 'fee'), **meta}
@bp.post('/deposits/<int:did>/action')
@P('deposits.manage')
def deposit_action(did):
    b, db = body(), get_db(); act = b.get('action'); d = one(db, 'SELECT * FROM deposits WHERE id=?', did)
    if not d: raise ApiError(404, 'not_found', 'Deposit not found')
    if act == 'reverse': fin.reverse_deposit(db, A(), did, reason_req(b)); return {'ok': True}
    if act in ('settle', 'fail'):
        if current_app.providers.mode != 'internal': raise ApiError(409, 'live_mode', 'Live deposits settle via the provider webhook only.')
        reason = reason_req(b) if act == 'fail' else 'manual settlement'
        with tx(db):
            ev = current_app.providers.payments.internal_event(d['external_ref'], 'succeeded' if act == 'settle' else 'failed'); r = fin.handle_payment_event(db, ev)
            audit(db, A(), f'deposit.{act}_manual', 'deposit', did, {'status': d['status']}, r, reason)
        return r
    raise ApiError(422, 'invalid_action', 'Unknown action')
@bp.get('/withdrawals')
@P('withdrawals.read')
def withdrawals():
    where, args = [], []
    if request.args.get('status'): where.append('w.status=?'); args.append(request.args['status'])
    if request.args.get('q'): where.append('(w.ref LIKE ? OR u.email LIKE ?)'); args += [like(request.args['q'])] * 2
    rows, meta = page('withdrawals w JOIN users u ON u.id=w.user_id', where, args, 'w.id,w.ref,u.email,w.amount_cents,w.method,w.destination,w.status,w.review_reason,w.non_live,w.created_at,w.completed_at', 'w.id DESC')
    return {'items': money(rows, 'amount'), **meta}
@bp.post('/withdrawals/<int:wid>/action')
@P('withdrawals.manage')
def withdrawal_action(wid):
    b = body(); return fin.withdrawal_action(get_db(), current_app.providers, A(), wid, b.get('action'), b.get('reason'))

# ================= reports =================
def safe(v): s = '' if v is None else str(v); return "'" + s if s[:1] in ('=', '+', '-', '@') else s
@bp.get('/reports/transactions.csv')
@P('reports.read')
def export_tx():
    db = get_db(); where, args = [], []
    if request.args.get('from'): where.append('t.created_at>=?'); args.append(request.args['from'])
    if request.args.get('to'): where.append('t.created_at<=?'); args.append(request.args['to'] + 'T23:59:59Z')
    w = (' WHERE ' + ' AND '.join(where)) if where else ''
    s = io.StringIO(); c = csv.writer(s); c.writerow(['ref', 'user', 'type', 'direction', 'amount', 'status', 'non_live', 'created_at'])
    for r in db.execute(f'SELECT t.*,u.public_id uid FROM transactions t JOIN users u ON u.id=t.user_id{w} ORDER BY t.id', args): c.writerow([safe(x) for x in (r['ref'], r['uid'], r['type'], r['direction'], cstr(r['amount_cents']), r['status'], r['non_live'], r['created_at'])])
    audit(db, A(), 'report.exported', 'report', 'transactions', None, {'from': request.args.get('from'), 'to': request.args.get('to')})
    return Response(s.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=vaulta-transactions.csv'})
@bp.get('/reports/summary')
@P('reports.read')
def report_summary():
    db = get_db()
    by_type = money(all_(db, "SELECT type,direction,COUNT(*) n,SUM(amount_cents) total_cents FROM transactions WHERE status='completed' GROUP BY type,direction ORDER BY type"), 'total')
    fees = cstr(val(db, "SELECT COALESCE(SUM(f.amount_cents),0) FROM transaction_fees f JOIN transactions t ON t.id=f.transaction_id WHERE t.status='completed'"))
    # reconciliation check: every account balance is derivable from the ledger; flag any account with negative posted balance
    neg = all_(db, "SELECT a.id,u.email FROM accounts a JOIN users u ON u.id=a.user_id WHERE (SELECT COALESCE(SUM(CASE direction WHEN 'credit' THEN amount_cents ELSE -amount_cents END),0) FROM transactions WHERE account_id=a.id AND status='completed')<0")
    return {'by_type': by_type, 'fees_collected': fees, 'negative_balance_accounts': neg, 'audit_chain': verify_chain(db)}

# ================= support =================
@bp.get('/support')
@P('support.manage')
def tickets():
    where, args = [], []
    for k in ('status', 'priority', 'category'):
        if request.args.get(k): where.append(f't.{k}=?'); args.append(request.args[k])
    if request.args.get('q'): where.append('(t.ref LIKE ? OR t.subject LIKE ? OR u.email LIKE ?)'); args += [like(request.args['q'])] * 3
    if request.args.get('assigned') == 'none': where.append('t.assigned_to IS NULL AND t.status NOT IN (\'resolved\',\'closed\')')
    if request.args.get('assigned') == 'me': where.append('t.assigned_to=?'); args.append(A())
    if request.args.get('unread') == '1': where.append('EXISTS (SELECT 1 FROM support_messages m WHERE m.ticket_id=t.id AND m.is_staff=0 AND m.read_by_staff_at IS NULL)')
    rows, meta = page('support_tickets t JOIN users u ON u.id=t.user_id LEFT JOIN users s ON s.id=t.assigned_to', where, args, 't.id,t.ref,u.email,t.category,t.subject,t.status,t.priority,s.email assignee,t.assigned_to assignee_id,t.created_at,t.updated_at,(SELECT COUNT(*) FROM support_messages m WHERE m.ticket_id=t.id AND m.is_staff=0 AND m.read_by_staff_at IS NULL) unread,(SELECT substr(m.body,1,120) FROM support_messages m WHERE m.ticket_id=t.id AND m.internal=0 ORDER BY m.id DESC LIMIT 1) last_message', "CASE t.status WHEN 'open' THEN 0 WHEN 'in_progress' THEN 1 ELSE 2 END,t.updated_at DESC")
    return {'items': rows, **meta}
@bp.get('/support/<ref>')
@P('support.manage')
def ticket(ref):
    db = get_db(); t = one(db, 'SELECT t.*,u.email,u.public_id uid,u.kyc_status FROM support_tickets t JOIN users u ON u.id=t.user_id WHERE t.ref=?', ref)
    if not t: raise ApiError(404, 'not_found', 'Ticket not found')
    return {'ticket': dict(t), 'messages': all_(db, 'SELECT m.is_staff,m.internal,m.body,m.created_at,u.email author FROM support_messages m JOIN users u ON u.id=m.author_id WHERE m.ticket_id=? ORDER BY m.id', t['id'])}
@bp.post('/support/<ref>/reply')
@P('support.manage')
def ticket_reply(ref):
    b, db = body(), get_db(); internal = bool(b.get('internal'))
    with tx(db):
        t = one(db, 'SELECT * FROM support_tickets WHERE ref=?', ref)
        if not t: raise ApiError(404, 'not_found', 'Ticket not found')
        mid = db.execute('INSERT INTO support_messages(ticket_id,author_id,is_staff,internal,body,created_at) VALUES(?,?,?,?,?,?)', (t['id'], A(), 1, 1 if internal else 0, text(b.get('message'), 'message', 4000), util.now())).lastrowid
        if not internal:
            db.execute("UPDATE support_tickets SET status=COALESCE(?,CASE WHEN status IN('open','in_progress') THEN 'waiting_for_user' ELSE status END),updated_at=?,assigned_to=COALESCE(assigned_to,?) WHERE id=?", (b.get('status') if b.get('status') in ('open', 'in_progress', 'waiting_for_user', 'resolved', 'closed') else None, util.now(), A(), t['id']))
            notify(db, t['user_id'], 'system', 'Support replied', f"New reply on {t['ref']}: {t['subject']}", '#/support')
            mail.emit(db, 'support.reply', t['user_id'], t['ref'], qualifier=str(mid))        # internal notes never email; the message text is never included
            if b.get('status') in ('resolved', 'closed'): mail.emit(db, 'support.' + b['status'], t['user_id'], t['ref'], qualifier=f"{b['status']}:{mid}")
    return {'ok': True}
@bp.patch('/support/<ref>')
@P('support.manage')
def ticket_update(ref):
    b, db = body(), get_db(); f = {}
    if 'status' in b:
        if b['status'] not in ('open', 'in_progress', 'waiting_for_user', 'resolved', 'closed'): raise ApiError(422, 'invalid_status', 'Invalid status')
        f['status'] = b['status']
    if 'priority' in b:
        if b['priority'] not in ('low', 'normal', 'high', 'urgent'): raise ApiError(422, 'invalid_priority', 'Invalid priority')
        f['priority'] = b['priority']
    if 'assign_to_me' in b: f['assigned_to'] = A() if b['assign_to_me'] else None
    if not f: raise ApiError(422, 'nothing_to_update', 'No changes')
    with tx(db):
        old = one(db, 'SELECT user_id,status FROM support_tickets WHERE ref=?', ref)
        if db.execute('UPDATE support_tickets SET ' + ','.join(f'{k}=?' for k in f) + ',updated_at=? WHERE ref=?', (*f.values(), util.now(), ref)).rowcount == 0: raise ApiError(404, 'not_found', 'Ticket not found')
        if f.get('status') and f['status'] != old['status'] and f['status'] in ('waiting_for_user', 'resolved', 'closed'):
            mail.emit(db, {'waiting_for_user': 'support.info_required', 'resolved': 'support.resolved', 'closed': 'support.closed'}[f['status']], old['user_id'], ref, qualifier=f"{f['status']}:{util.now()}")
        audit(db, A(), 'ticket.updated', 'ticket', ref, None, f)
    return {'ok': True}

# ================= notifications =================
@bp.post('/notifications/broadcast')
@P('notifications.send')
def broadcast():
    b, db = body(), get_db(); need(b, 'title', 'body'); typ = b.get('type', 'system')
    if typ not in ('system', 'promotional'): raise ApiError(422, 'invalid_type', 'type must be system or promotional')
    aud = b.get('audience', 'all'); sql = "SELECT id FROM users WHERE status='active' AND id NOT IN(SELECT user_id FROM user_roles)" + (" AND kyc_status='verified'" if aud == 'verified' else '')
    with tx(db):
        ids = [r[0] for r in db.execute(sql)]
        for i in ids: notify(db, i, typ, text(b['title'], 'title', 120), text(b['body'], 'body', 1000))
        audit(db, A(), 'notification.broadcast', 'notification', None, None, {'title': b['title'], 'audience': aud, 'recipients': len(ids)})
    return {'recipients': len(ids)}
@bp.get('/notifications/outbox')
@P('notifications.send')
def outbox(): rows, meta = page('email_outbox', [], [], 'id,to_email,subject,non_live,created_at', 'id DESC'); return {'items': rows, **meta, 'note': 'Email outbox (no email provider connected). Bodies are not shown here because they can contain one-time links.'}

# ================= content =================
CATS = ('article', 'guide', 'market_insight', 'faq', 'education', 'announcement')
@bp.get('/content')
@P('content.manage')
def content_list():
    where, args = [], []
    if request.args.get('status'): where.append('status=?'); args.append(request.args['status'])
    rows, meta = page('articles', where, args, 'id,slug,title,category,status,publish_at,updated_at', 'updated_at DESC'); return {'items': rows, **meta}
@bp.get('/content/<int:aid>')
@P('content.manage')
def content_get(aid):
    r = one(get_db(), 'SELECT * FROM articles WHERE id=?', aid)
    if not r: raise ApiError(404, 'not_found', 'Article not found')
    return {'article': dict(r)}
def _art(b, partial):
    f = {}
    if 'title' in b: f['title'] = text(b['title'], 'title', 160)
    if 'slug' in b:
        f['slug'] = text(b['slug'], 'slug', 80).lower()
        if not re.fullmatch(r'[a-z0-9-]{3,80}', f['slug']): raise ApiError(422, 'invalid_slug', 'slug: lowercase letters, numbers, hyphens')
    if 'category' in b:
        if b['category'] not in CATS: raise ApiError(422, 'invalid_category', 'Invalid category')
        f['category'] = b['category']
    if 'summary' in b: f['summary'] = text(b['summary'], 'summary', 300, False)
    if 'body' in b: f['body'] = text(b['body'], 'body', 50000)
    if 'status' in b:
        if b['status'] not in ('draft', 'published', 'scheduled', 'archived'): raise ApiError(422, 'invalid_status', 'Invalid status')
        f['status'] = b['status']
    if 'publish_at' in b: f['publish_at'] = b['publish_at'] or None
    if f.get('status') == 'scheduled' and not (f.get('publish_at') or b.get('_existing_publish')): raise ApiError(422, 'publish_at_required', 'Scheduled content needs a publish date (YYYY-MM-DDTHH:MM:SSZ).')
    if f.get('status') == 'published' and not f.get('publish_at'): f['publish_at'] = util.now()
    return f
@bp.post('/content')
@P('content.manage')
def content_create():
    b, db = body(), get_db(); need(b, 'title', 'slug', 'category', 'body'); f = _art(b, False); f.setdefault('status', 'draft'); f['author_id'] = A(); f['created_at'] = f['updated_at'] = util.now()
    with tx(db):
        try: cur = db.execute(f"INSERT INTO articles({','.join(f)}) VALUES({','.join('?'*len(f))})", tuple(f.values()))
        except Exception: raise ApiError(409, 'slug_taken', 'That slug is already used.')
        audit(db, A(), 'content.created', 'article', cur.lastrowid, None, {'title': f['title'], 'status': f['status']})
    return {'id': cur.lastrowid}, 201
@bp.patch('/content/<int:aid>')
@P('content.manage')
def content_update(aid):
    b, db = body(), get_db()
    with tx(db):
        old = one(db, 'SELECT * FROM articles WHERE id=?', aid)
        if not old: raise ApiError(404, 'not_found', 'Article not found')
        b['_existing_publish'] = old['publish_at']; f = _art(b, True); f['updated_at'] = util.now()
        db.execute('UPDATE articles SET ' + ','.join(f'{k}=?' for k in f) + ' WHERE id=?', (*f.values(), aid))
        audit(db, A(), 'content.updated', 'article', aid, {k: old[k] for k in f if k in old.keys() and k != 'body'}, {k: v for k, v in f.items() if k != 'body'})
    return {'ok': True}

# ================= audit, roles, settings =================
@bp.get('/audit-logs')
@P('audit.read')
def audit_logs():
    where, args = [], []
    if request.args.get('action'): where.append('a.action LIKE ?'); args.append(like(request.args['action']))
    if request.args.get('entity'): where.append('a.entity=?'); args.append(request.args['entity'])
    rows, meta = page('audit_logs a LEFT JOIN users u ON u.id=a.actor_id', where, args, 'a.id,u.email actor,a.action,a.entity,a.entity_id,a.prev_value,a.new_value,a.reason,a.ip,a.created_at,substr(a.hash,1,12) hash', 'a.id DESC')
    return {'items': rows, **meta}
@bp.get('/audit-logs/verify')
@P('audit.read')
def audit_verify(): return verify_chain(get_db())
@bp.get('/roles')
@P('roles.manage')
def roles():
    db = get_db(); out = []
    for r in db.execute('SELECT * FROM roles ORDER BY id'):
        out.append({'name': r['name'], 'description': r['description'], 'permissions': [p[0] for p in db.execute('SELECT p.code FROM role_permissions rp JOIN permissions p ON p.id=rp.permission_id WHERE rp.role_id=? ORDER BY p.code', (r['id'],))],
                    'members': [m[0] for m in db.execute('SELECT u.email FROM user_roles ur JOIN users u ON u.id=ur.user_id WHERE ur.role_id=?', (r['id'],))]})
    return {'roles': out, 'permissions': all_(db, 'SELECT code,description FROM permissions ORDER BY code')}
@bp.post('/roles/assign')
@P('roles.manage')
def role_assign():
    b, db = body(), get_db(); need(b, 'user', 'role', 'action'); reason = reason_req(b)
    with tx(db):
        u = uid_from(db, b['user']); r = one(db, 'SELECT * FROM roles WHERE name=?', b['role'])
        if not r: raise ApiError(404, 'not_found', 'Unknown role')
        if u['id'] == A(): raise ApiError(403, 'forbidden', 'You cannot change your own roles.')
        if b['action'] == 'grant':
            if u['status'] != 'active': raise ApiError(409, 'inactive', 'Only active accounts can receive roles.')
            db.execute('INSERT OR IGNORE INTO user_roles(user_id,role_id,granted_by,granted_at) VALUES(?,?,?,?)', (u['id'], r['id'], A(), util.now()))
        elif b['action'] == 'revoke':
            if r['name'] == 'super_admin' and val(db, 'SELECT COUNT(*) FROM user_roles WHERE role_id=?', r['id']) <= 1: raise ApiError(409, 'last_super_admin', 'Cannot remove the last super admin.')
            db.execute('DELETE FROM user_roles WHERE user_id=? AND role_id=?', (u['id'], r['id']))
        else: raise ApiError(422, 'invalid_action', 'action must be grant or revoke')
        audit(db, A(), f"role.{b['action']}", 'user', u['public_id'], None, {'role': r['name']}, reason); sec.revoke_sessions(db, u['id'])   # force re-login so permissions refresh
    return {'ok': True}
@bp.get('/settings')
@P('settings.manage')
def settings_get(): return {'settings': all_(get_db(), "SELECT key,value,description,updated_at FROM system_settings WHERE key NOT LIKE 'platform.%' ORDER BY key"), 'mode': current_app.providers.mode}
@bp.put('/settings')
@P('settings.manage')
def settings_put():
    b, db = body(), get_db(); reason = reason_req(b); changes = b.get('changes')
    if not isinstance(changes, dict) or not changes: raise ApiError(422, 'nothing_to_update', 'No changes supplied')
    with tx(db):
        for k, v in changes.items():
            old = one(db, 'SELECT value FROM system_settings WHERE key=?', k)
            if not old: raise ApiError(404, 'unknown_setting', f'Unknown setting {k}')
            if not re.fullmatch(r'\d{1,12}', str(v)): raise ApiError(422, 'invalid_value', f'{k} must be a non-negative whole number')
            if k in platform_gate.FLAGS and str(v) not in ('0', '1'): raise ApiError(422, 'invalid_value', f'{k} is a switch: use 0 (off) or 1 (on)')
            if k == 'require_2fa_all' and str(v) == '1' and not g.user['totp_enabled']: raise ApiError(409, 'enable_own_2fa_first', 'Turn on two-factor authentication for your own account before requiring it for everyone.')
            db.execute('UPDATE system_settings SET value=?,updated_by=?,updated_at=? WHERE key=?', (str(v), A(), util.now(), k)); audit(db, A(), 'setting.changed', 'setting', k, {'value': old['value']}, {'value': str(v)}, reason)
    return {'ok': True}

# ================= general ledger (M2) =================
@bp.get('/ledger/trial-balance')
@P('ledger.read')
def gl_trial_balance(): return accounting.trial_balance(get_db())

@bp.get('/ledger/integrity')
@P('ledger.read')
def gl_integrity():
    db = get_db(); return {'integrity': accounting.verify_integrity(db), 'subledger_variances': accounting.reconcile_subledger(db)}

@bp.get('/ledger/entries')
@P('ledger.read')
def gl_entries():
    where, args = [], []
    if request.args.get('type'): where.append('entry_type=?'); args.append(request.args['type'])
    if request.args.get('source_ref'): where.append('source_ref=?'); args.append(request.args['source_ref'])
    rows, meta = page('journal_entries', where, args, 'id,ref,entry_type,description,source_type,source_ref,reverses_entry_id,line_count,posted_at', 'id DESC')
    return {'items': rows, **meta}

@bp.get('/ledger/entries/<int:eid>')
@P('ledger.read')
def gl_entry(eid):
    db = get_db(); e = one(db, 'SELECT * FROM journal_entries WHERE id=?', eid)
    if not e: raise ApiError(404, 'not_found', 'Entry not found')
    lines = all_(db, 'SELECT l.line_no,a.code account,a.name,l.asset_code asset,l.direction,l.amount FROM journal_lines l JOIN gl_accounts a ON a.id=l.gl_account_id WHERE l.entry_id=? ORDER BY l.line_no', eid)
    return {'entry': dict(e), 'lines': lines}

@bp.get('/ledger/periods')
@P('ledger.read')
def gl_periods(): return {'items': all_(get_db(), 'SELECT code,starts_on,ends_on,status,closed_at FROM accounting_periods ORDER BY code DESC')}

@bp.post('/ledger/periods/<code>/close')
@P('ledger.manage')
def gl_close_period(code):
    b = body(); accounting.close_period(get_db(), code, A(), reason_req(b, 10)); return {'ok': True}

# ================= crypto asset / network registry (M3) =================
@bp.get('/crypto/registry')
@P('crypto.registry.read')
def crypto_registry():
    db = get_db(); return {'items': asset_registry.list_registry(db), 'assets': all_(db, 'SELECT id,symbol,name,type,status FROM crypto_assets ORDER BY id'),
                           'networks': all_(db, 'SELECT id,network_code,name,chain_type,is_testnet,confirmations_required,status FROM blockchain_networks ORDER BY id')}

# POST /crypto/assets (create, now with logo/description/limits) lives in routes_crypto.py as part of the single funding system.

@bp.post('/crypto/networks')
@P('crypto.registry.manage')
def crypto_network_create():
    b = body(); need(b, 'network_code', 'name', 'chain_type', 'confirmations_required'); db = get_db()
    return {'id': asset_registry.create_network(db, A(), b['network_code'], b['name'], b['chain_type'], b['confirmations_required'], b.get('chain_id'), bool(b.get('is_testnet')), b.get('finality_policy'))}, 201

@bp.post('/crypto/asset-networks')
@P('crypto.registry.manage')
def crypto_asset_network_create():
    b = body(); need(b, 'symbol', 'network_code', 'decimals'); db = get_db()
    return {'id': asset_registry.create_asset_network(db, A(), b['symbol'], b['network_code'], b['decimals'], b.get('contract_address'), b.get('min_deposit_minor', '0'), b.get('confirmations_override'), b.get('withdrawal_fee_policy'))}, 201

@bp.post('/crypto/asset-networks/<int:anid>/status')
@P('crypto.registry.manage')
def crypto_asset_network_status(anid):
    b = body(); return {'state': asset_registry.set_status(get_db(), A(), anid, status=b.get('status'), deposit_enabled=b.get('deposit_enabled'), withdrawal_enabled=b.get('withdrawal_enabled'), reason=b.get('reason'))}

@bp.post('/crypto/<kind>/<int:ident>/status')
@P('crypto.registry.manage')
def crypto_parent_status(kind, ident):
    b = body(); need(b, 'status'); asset_registry.set_parent_status(get_db(), A(), 'asset' if kind == 'assets' else 'network' if kind == 'networks' else kind, ident, b['status'], b.get('reason')); return {'ok': True}
