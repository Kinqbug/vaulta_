"""Trade terminal API.
Market data (/api/terminal/...): normalised provider data only; the browser never calls the provider. Tradability comes from Vaulta's own asset registry.
Community chat (/api/community/...): authenticated, persisted, moderated."""
from decimal import Decimal
from flask import Blueprint, request, g
from . import util, security as sec, marketdata as md, community as cm
from .util import ApiError
from .db import get_db, one, all_
from .routes_auth import body

bp = Blueprint('terminal', __name__, url_prefix='/api/terminal')
chat = Blueprint('community', __name__, url_prefix='/api/community')

SORTS = {'market_cap': ('market_cap_desc', 'market_cap_asc'), 'volume': ('volume_desc', 'volume_asc')}
LOCAL = {'price': 'price', 'change_24h': 'change_24h_pct', 'change_7d': 'change_7d_pct', 'name': 'name', 'volume': 'volume_24h', 'market_cap': 'market_cap'}

def _int(name, default, lo, hi):
    v = request.args.get(name, str(default))
    if not v.lstrip('-').isdigit(): raise ApiError(422, 'invalid_parameter', f'{name} must be an integer.')
    n = int(v)
    if not lo <= n <= hi: raise ApiError(422, 'invalid_parameter', f'{name} must be between {lo} and {hi}.')
    return n

KNOWN = (('bitcoin', 'BTC'), ('ethereum', 'ETH'), ('solana', 'SOL'), ('tether', 'USDT'), ('usd-coin', 'USDC'))
def ensure_asset_map(db):
    """Link provider coin ids to Vaulta registry assets that exist (idempotent; assets are often seeded after migrations run)."""
    for cid, sym in KNOWN: db.execute('INSERT OR IGNORE INTO market_asset_map(coin_id,symbol) SELECT ?,symbol FROM assets WHERE symbol=?', (cid, sym))

def _tradable_map(db):
    ensure_asset_map(db)
    return {r['coin_id']: r for r in all_(db, "SELECT m.coin_id,a.symbol,a.name,a.last_price,a.status,a.tradable FROM market_asset_map m JOIN assets a ON a.symbol=m.symbol")}

def _decorate(db, rows):
    tm = _tradable_map(db); fav = {r['coin_id'] for r in db.execute('SELECT coin_id FROM coin_favorites WHERE user_id=?', (g.user['id'],))} if g.user else set()
    for r in rows:
        t = tm.get(r['id']); r['favorite'] = r['id'] in fav
        r['trade'] = {'enabled': bool(t and t['status'] == 'active' and t['tradable']), 'symbol': t['symbol'] if t else None,
                      'reason': None if (t and t['status'] == 'active' and t['tradable']) else ('Trading is currently halted for this asset.' if t else 'Market data only: Vaulta does not offer orders for this asset.')}
    return rows

@bp.get('/status')
@sec.login_required
def status(): return {'market_data': md.status()}

@bp.get('/trade-context')
@sec.login_required
def trade_context():
    from . import ledger, rbac
    db = get_db(); acct = ledger.account_for(db, g.user['id']); bal = ledger.balance(db, acct['id'])
    return {'available_cents': bal['available_cents'], 'currency': 'USD', 'fee_bps': rbac.setting(db, 'trade_fee_bps'), 'order_types': ['market', 'limit', 'stop', 'stop_limit'], 'execution': 'simulated',
            'execution_note': 'Orders execute against Vaulta\'s internal ledger at Vaulta\'s own quote. No exchange order is placed.'}

@bp.get('/markets')
@sec.login_required
def markets():
    db = get_db(); tab = request.args.get('tab', 'all'); q = request.args.get('q', '').strip(); sort = request.args.get('sort', 'market_cap'); direction = request.args.get('dir', 'desc')
    page = _int('page', 1, 1, 400); per = _int('per_page', 50, 1, 100)
    if tab not in ('all', 'trending', 'gainers', 'losers', 'favorites'): raise ApiError(422, 'invalid_parameter', 'Unknown tab.')
    if sort not in LOCAL: raise ApiError(422, 'invalid_parameter', 'Unknown sort field.')
    if direction not in ('asc', 'desc'): raise ApiError(422, 'invalid_parameter', 'dir must be asc or desc.')
    if len(q) > 60: raise ApiError(422, 'invalid_parameter', 'Search text is too long.')
    scope = None
    if q: rows, meta = md.search(db, q); scope = 'Search results (up to 25 matches).'; total = len(rows)
    elif tab == 'trending': rows, meta = md.trending(db); scope = 'Trending on CoinGecko in the last 24 hours.'; total = len(rows)
    elif tab == 'favorites':
        ids = [r['coin_id'] for r in db.execute('SELECT coin_id FROM coin_favorites WHERE user_id=? ORDER BY created_at DESC LIMIT 250', (g.user['id'],))]
        if not ids: return {'coins': [], 'page': 1, 'per_page': per, 'total': 0, 'has_more': False, 'tab': tab, 'meta': None, 'scope': 'Your favourites.'}
        rows, meta = md.markets(db, ids=ids, per_page=len(ids)); total = len(rows); scope = 'Your favourites.'
    elif tab in ('gainers', 'losers') or sort not in SORTS:
        rows, meta = md.markets(db, per_page=250); rows = [r for r in rows if r['change_24h_pct'] is not None and (r['volume_24h'] or 0) > 0] if tab != 'all' else rows
        scope = 'Ranked among the top 250 coins by market cap.'; total = len(rows)
        if tab in ('gainers', 'losers'): rows = sorted(rows, key=lambda r: r['change_24h_pct'], reverse=(tab == 'gainers'))
    else:
        order = SORTS[sort][0 if direction == 'desc' else 1]; rows, meta = md.markets(db, order=order, page=page, per_page=per); total = None
    if (q or tab in ('trending', 'favorites') or tab in ('gainers', 'losers') or sort not in SORTS) and total is not None:
        if sort in LOCAL and tab == 'all' or (q and sort != 'market_cap'):
            k = LOCAL[sort]; rows = sorted(rows, key=lambda r: (r[k] is None, (r[k] if r[k] is not None else 0) * (1 if direction == 'asc' else -1)) if k != 'name' else (False, r[k].lower()), reverse=False)
        start = (page - 1) * per; has_more = start + per < len(rows); rows = rows[start:start + per]
    else: has_more = len(rows) == per
    return {'coins': _decorate(db, rows), 'page': page, 'per_page': per, 'total': total, 'has_more': has_more, 'tab': tab, 'sort': sort, 'dir': direction, 'scope': scope, 'meta': meta}

@bp.get('/coins/<coin_id>')
@sec.login_required
def coin(coin_id):
    db = get_db(); d, meta = md.coin(db, coin_id); d = _decorate(db, [d])[0]
    ensure_asset_map(db); t = one(db, 'SELECT a.symbol,a.last_price,a.prev_close FROM market_asset_map m JOIN assets a ON a.symbol=m.symbol WHERE m.coin_id=?', coin_id)
    if t: d['vaulta_quote'] = {'symbol': t['symbol'], 'price': t['last_price'], 'label': 'Vaulta demo quote used for simulated order execution (not the live market price)'}
    return {'coin': d, 'meta': meta}

@bp.get('/coins/<coin_id>/chart')
@sec.login_required
def chart(coin_id):
    c, meta = md.chart(get_db(), coin_id, request.args.get('tf', '1D')); return {'chart': c, 'meta': meta}

@bp.get('/coins/<coin_id>/orderbook')
@sec.login_required
def orderbook(coin_id):
    if not md.COIN_RE.match(coin_id): raise ApiError(422, 'invalid_coin', 'Invalid coin id.')
    return {'available': False, 'bids': [], 'asks': [], 'reason': 'No exchange or order-book integration is configured. Vaulta executes simulated orders against its own ledger, so there is no depth to display.'}

@bp.get('/coins/<coin_id>/fills')
@sec.login_required
def fills(coin_id):
    db = get_db()
    if not md.COIN_RE.match(coin_id): raise ApiError(422, 'invalid_coin', 'Invalid coin id.')
    ensure_asset_map(db); t = one(db, 'SELECT symbol FROM market_asset_map WHERE coin_id=?', coin_id)
    if not t: return {'available': False, 'fills': [], 'reason': 'Vaulta does not trade this asset, so there are no platform fills to show.'}
    rows = all_(db, "SELECT e.ts,e.price,e.quantity,o.side FROM order_executions e JOIN orders o ON o.id=e.order_id JOIN assets a ON a.id=o.asset_id WHERE a.symbol=? ORDER BY e.id DESC LIMIT 30", t['symbol'])
    return {'available': True, 'symbol': t['symbol'], 'fills': rows, 'source': 'Vaulta platform fills (simulated internal ledger, anonymised). Not exchange trades.'}

@bp.get('/favorites')
@sec.login_required
def favorites(): return {'favorites': [r['coin_id'] for r in get_db().execute('SELECT coin_id FROM coin_favorites WHERE user_id=? ORDER BY created_at DESC', (g.user['id'],))]}

@bp.put('/favorites/<coin_id>')
@sec.login_required
@sec.rate_limit('fav', 120, 60)
def fav_add(coin_id):
    db = get_db()
    if not md.COIN_RE.match(coin_id): raise ApiError(422, 'invalid_coin', 'Invalid coin id.')
    if db.execute('SELECT COUNT(*) FROM coin_favorites WHERE user_id=?', (g.user['id'],)).fetchone()[0] >= 250 and not one(db, 'SELECT 1 FROM coin_favorites WHERE user_id=? AND coin_id=?', g.user['id'], coin_id):
        raise ApiError(409, 'favorites_limit', 'You can keep up to 250 favourites.')
    db.execute('INSERT OR IGNORE INTO coin_favorites(user_id,coin_id,created_at) VALUES(?,?,?)', (g.user['id'], coin_id, util.now())); return {'favorite': True}

@bp.delete('/favorites/<coin_id>')
@sec.login_required
def fav_del(coin_id):
    get_db().execute('DELETE FROM coin_favorites WHERE user_id=? AND coin_id=?', (g.user['id'], coin_id)); return {'favorite': False}

# ---------------------------------------------------------------- community chat
@chat.get('/channels')
@sec.login_required
def channels(): return {'channels': cm.channels(get_db(), g.user)}

@chat.get('/channels/<slug>/messages')
@sec.login_required
def messages(slug):
    db = get_db(); a = request.args
    for k in ('before', 'after'):
        if a.get(k) is not None and not a[k].isdigit(): raise ApiError(422, 'invalid_parameter', f'{k} must be a message id.')
    if a.get('limit') is not None and not a['limit'].isdigit(): raise ApiError(422, 'invalid_parameter', 'limit must be a number.')
    r = cm.history(db, g.user, slug, int(a['before']) if a.get('before') else None, int(a['after']) if a.get('after') else None, int(a.get('limit', 50)))
    c = cm.channel(db, slug)
    if a.get('poll') == '1': cm.touch_presence(db, g.user['id'], c['id']); r['presence'] = cm.presence(db, slug)
    return r

@chat.post('/channels/<slug>/messages')
@sec.login_required
@sec.rate_limit('chat_send', 40, 60, key_fn=lambda: str(g.user['id']))
def send(slug):
    b = body(); m, dup = cm.send(get_db(), g.user, slug, b.get('content'), b.get('nonce'), b.get('reply_to'))
    return {'message': m, 'duplicate': dup}, (200 if dup else 201)

@chat.get('/channels/<slug>/presence')
@sec.login_required
def presence(slug): return cm.presence(get_db(), slug)

@chat.post('/messages/<int:mid>/report')
@sec.login_required
@sec.rate_limit('chat_report', 20, 3600, key_fn=lambda: str(g.user['id']))
def report(mid):
    b = body(); return cm.report(get_db(), g.user, mid, b.get('reason'), b.get('details')), 201

@chat.delete('/messages/<int:mid>')
@sec.login_required
def remove(mid):
    cm.remove_message(get_db(), g.user, mid, (request.get_json(silent=True) or {}).get('reason')); return {'ok': True}

@chat.get('/blocks')
@sec.login_required
def blocks(): return {'blocked': cm.blocked(get_db(), g.user)}
@chat.put('/blocks/<public_id>')
@sec.login_required
def block(public_id): cm.block(get_db(), g.user, public_id); return {'blocked': True}
@chat.delete('/blocks/<public_id>')
@sec.login_required
def unblock(public_id): cm.unblock(get_db(), g.user, public_id); return {'blocked': False}

@chat.get('/moderation/reports')
@sec.require_perm('chat.moderate')
def mod_reports(): return {'reports': cm.list_reports(get_db(), g.user, request.args.get('status', 'open'))}
@chat.post('/moderation/reports/<int:rid>/resolve')
@sec.require_perm('chat.moderate')
def mod_resolve(rid): cm.resolve_report(get_db(), g.user, rid, body().get('action')); return {'ok': True}
@chat.post('/moderation/mute')
@sec.require_perm('chat.moderate')
def mod_mute():
    b = body(); return cm.mute(get_db(), g.user, b.get('user_id'), b.get('minutes'), b.get('channel'), b.get('reason'))
@chat.post('/moderation/unmute')
@sec.require_perm('chat.moderate')
def mod_unmute(): return cm.unmute(get_db(), g.user, body().get('user_id'))
