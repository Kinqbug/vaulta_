import json
from decimal import Decimal
from flask import Blueprint, request, jsonify, g, current_app
from . import util, security as sec, finance as fin
from .util import ApiError, cstr
from .db import get_db, one, all_
from .routes_auth import body

bp = Blueprint('public', __name__, url_prefix='/api')

def series_for(db, asset_id, n=12, days=90):
    from datetime import timedelta
    since = (util.utcnow() - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')
    rows = [float(r['price']) for r in db.execute('SELECT price FROM asset_prices WHERE asset_id=? AND ts>=? ORDER BY ts', (asset_id, since))]
    if len(rows) <= n: return rows
    step = (len(rows) - 1) / (n - 1); return [rows[round(i * step)] for i in range(n)]

def product_public(db, p):
    p = dict(p)
    first = one(db, "SELECT price FROM asset_prices WHERE asset_id=? AND ts>=? ORDER BY ts LIMIT 1", p['asset_id'], util.ts_after(days=-365))
    hist = round(float((Decimal(p['last_price']) / Decimal(first['price']) - 1) * 100), 1) if first else None
    basis = 'illustrative' if p['category'] in ('Bonds', 'Real Estate') else 'historical'
    return {'id': p['id'], 'slug': p['slug'], 'name': p['name'], 'category': p['category'], 'kind': p['kind'], 'issuer': p['issuer'], 'risk': p['risk'], 'min_investment': cstr(p['min_investment_cents']),
            'fee_pct': str(Decimal(p['fee_bps']) / 100), 'fee_bps': p['fee_bps'], 'return_pct': p['illustrative_return_pct'] if basis == 'illustrative' else (hist if hist is not None else p['illustrative_return_pct']),
            'return_basis': basis, 'horizon': p['horizon'], 'description': p['description'], 'score': p['score'], 'added': p['created_at'][:10], 'status': p['status'],
            'nav': p['last_price'], 'series': series_for(db, p['asset_id']), 'documents': json.loads(p['documents'] or '[]'),
            'disclaimer': 'Investing involves risk, including possible loss of principal. Past performance does not guarantee future results. Illustrative figures are not forecasts.'}

PRODUCT_SQL = 'SELECT p.*,a.last_price FROM investment_products p JOIN assets a ON a.id=p.asset_id'
@bp.get('/products')
def products():
    db = get_db(); return {'products': [product_public(db, r) for r in db.execute(PRODUCT_SQL + " WHERE p.status='active' AND a.status='active' ORDER BY p.score DESC")]}
@bp.get('/products/<slug>')
def product(slug):
    db = get_db(); r = one(db, PRODUCT_SQL + ' WHERE p.slug=? AND p.status!="archived"', slug)
    if not r: raise ApiError(404, 'not_found', 'Product not found')
    return {'product': product_public(db, r)}

@bp.get('/content')
def content():
    db = get_db(); cat = request.args.get('category'); now = util.now()
    sql = "SELECT slug,title,category,summary,publish_at,updated_at FROM articles WHERE (status='published' OR (status='scheduled' AND publish_at<=?))" + (' AND category=?' if cat else '') + ' ORDER BY COALESCE(publish_at,created_at) DESC'
    return {'articles': all_(db, sql, *( [now, cat] if cat else [now]))}
@bp.get('/content/<slug>')
def article(slug):
    r = one(get_db(), "SELECT slug,title,category,summary,body,publish_at,updated_at FROM articles WHERE slug=? AND (status='published' OR (status='scheduled' AND publish_at<=?))", slug, util.now())
    if not r: raise ApiError(404, 'not_found', 'Article not found')
    return {'article': dict(r)}

@bp.post('/webhooks/internal-payments')
def payments_webhook():
    """Signed provider callback. In live mode a provider-specific verifier replaces this; internal events are HMAC-signed with a server secret."""
    if current_app.providers.mode != 'internal': raise ApiError(404, 'not_found', 'Not found')
    raw = request.get_data()
    import hmac
    if not hmac.compare_digest(sec.sign_payload(raw), request.headers.get('X-Vaulta-Signature', '')): raise ApiError(401, 'bad_signature', 'Invalid signature')
    return jsonify(fin.handle_payment_event(get_db(), json.loads(raw)))

@bp.get('/dev/last-email')
@sec.login_required
def last_email():
    if current_app.config['ENV'] != 'development': raise ApiError(404, 'not_found', 'Not found')
    r = one(get_db(), 'SELECT c.subject,c.text body,c.created_at FROM email_dev_capture c JOIN email_outbox o ON o.id=c.email_id WHERE o.user_id=? ORDER BY c.email_id DESC LIMIT 1', g.user['id'])
    return {'email': dict(r) if r else None, 'note': 'Development only: shows the last email captured by the development transport (nothing was delivered). Run the worker (python run.py does this in development) so queued mail is captured.'}
