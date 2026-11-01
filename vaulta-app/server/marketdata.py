"""Market-data service (CoinGecko). Backend-only: the browser never talks to the provider and never sees the key.

Verified against the CoinGecko API documentation:
  * Demo plan  : https://api.coingecko.com/api/v3        header x-cg-demo-api-key   (~30 calls/min, 10k/month)
  * Pro plans  : https://pro-api.coingecko.com/api/v3    header x-cg-pro-api-key
  * /coins/markets   vs_currency, order, per_page (<=250), page, ids, sparkline, price_change_percentage
  * /coins/{id}/ohlc vs_currency, days (1,7,14,30,90,180,365,max). Timestamps are candle CLOSE times (ms).
                     Auto granularity: 1-2 days = 30m, 3-30 days = 4h, 31+ days = 4 days. Demo history is limited to 365 days.
  * /search/trending, /search, /coins/{id}
No price, volume, market cap or candle is ever invented. Cached data is always labelled with its age and a stale flag.
"""
import os, re, json, time, threading, logging, urllib.request, urllib.parse, urllib.error, html as _html
from . import util
from .util import ApiError

log = logging.getLogger('vaulta.market')
IMAGE_HOSTS = tuple(h.strip() for h in (os.getenv('MARKET_IMAGE_HOSTS') or 'coin-images.coingecko.com,assets.coingecko.com').split(',') if h.strip())      # hosts coin logos may be loaded from (also used for the CSP img-src)
COIN_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,99}$')
TTL = {'markets': 60, 'trending': 300, 'search': 300, 'coin': 120, 'ohlc': 600}
MAX_STALE = 24 * 3600
TIMEFRAMES = {'1D': (1, '30 minutes'), '7D': (7, '4 hours'), '30D': (30, '4 hours'), '90D': (90, '4 days'), '1Y': (365, '4 days')}

class ProviderError(Exception):
    def __init__(self, kind, msg='', retry_after=None): super().__init__(msg or kind); self.kind = kind; self.retry_after = retry_after

# ---------------------------------------------------------------- provider access
class Config:
    @staticmethod
    def get():
        plan = (os.getenv('COINGECKO_PLAN') or 'demo').strip().lower(); key = os.getenv('COINGECKO_API_KEY', '').strip()
        base = os.getenv('COINGECKO_BASE_URL') or ('https://pro-api.coingecko.com/api/v3' if plan == 'pro' else 'https://api.coingecko.com/api/v3')
        hdr = 'x-cg-pro-api-key' if plan == 'pro' else 'x-cg-demo-api-key'
        return {'plan': plan, 'key': key, 'base': base.rstrip('/'), 'header': hdr, 'timeout': float(os.getenv('MARKET_TIMEOUT_SECONDS', '8')),
                'max_per_min': int(os.getenv('MARKET_MAX_CALLS_PER_MINUTE', '25' if plan != 'pro' else '300'))}

_lock = threading.Lock(); _state = {'calls': [], 'cooldown_until': 0.0, 'last_ok': None, 'last_error': None, 'last_error_kind': None}
_mem = {}; _key_locks = {}; _transport = None

def set_transport(fn):            # tests inject a fake provider: fn(url, headers, timeout) -> (status, headers_dict, body_bytes)
    global _transport; _transport = fn
def reset():
    _mem.clear(); _key_locks.clear(); _state.update({'calls': [], 'cooldown_until': 0.0, 'last_ok': None, 'last_error': None, 'last_error_kind': None})

def _default_transport(url, headers, timeout):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return r.status, dict(r.headers), r.read(5_000_000)
    except urllib.error.HTTPError as e: return e.code, dict(e.headers or {}), (e.read(100_000) if e.fp else b'')

def _budget_ok(cfg):
    now = time.time()
    with _lock:
        if now < _state['cooldown_until']: return False
        _state['calls'] = [t for t in _state['calls'] if t > now - 60]
        if len(_state['calls']) >= cfg['max_per_min']: return False
        _state['calls'].append(now); return True

def _call(path, params):
    """One provider request, with rate budget, cooldown after 429, timeout and response validation."""
    cfg = Config.get()
    if not _budget_ok(cfg): raise ProviderError('rate_limited', 'Local rate budget exhausted or provider cooling down', max(1, int(_state['cooldown_until'] - time.time())))
    url = cfg['base'] + path + ('?' + urllib.parse.urlencode(params) if params else '')
    headers = {'Accept': 'application/json', 'User-Agent': 'Vaulta/1.0 market-data'}
    if cfg['key']: headers[cfg['header']] = cfg['key']
    try: status, rh, raw = (_transport or _default_transport)(url, headers, cfg['timeout'])
    except Exception as e:
        kind = 'timeout' if 'timed out' in str(e).lower() or isinstance(e, TimeoutError) else 'network'
        _state['last_error'], _state['last_error_kind'] = time.time(), kind; log.warning('market provider %s: %s', kind, type(e).__name__); raise ProviderError(kind, str(type(e).__name__))
    if status == 429:
        ra = None
        try: ra = int(rh.get('Retry-After') or rh.get('retry-after') or 60)
        except (TypeError, ValueError): ra = 60
        with _lock: _state['cooldown_until'] = time.time() + min(max(ra, 5), 300)
        _state['last_error'], _state['last_error_kind'] = time.time(), 'rate_limited'; log.warning('market provider rate-limited; cooling down %ss', ra); raise ProviderError('rate_limited', 'Provider rate limit', ra)
    if status in (401, 403): _state['last_error'], _state['last_error_kind'] = time.time(), 'auth'; log.error('market provider rejected credentials (HTTP %s)', status); raise ProviderError('auth', 'Provider rejected the API key')
    if status >= 400: _state['last_error'], _state['last_error_kind'] = time.time(), 'http'; log.warning('market provider HTTP %s for %s', status, path); raise ProviderError('http', f'HTTP {status}')
    try: data = json.loads(raw.decode('utf-8'))
    except Exception: _state['last_error'], _state['last_error_kind'] = time.time(), 'malformed'; raise ProviderError('malformed', 'Provider returned invalid JSON')
    _state['last_ok'] = time.time(); return data

# ---------------------------------------------------------------- caching (fresh / stale / unavailable)
def _kl(key):
    with _lock: return _key_locks.setdefault(key, threading.Lock())

def cached(db, key, ttl, fetch):
    """Return (data, meta). Concurrent callers for the same key share one provider request. On provider failure the last good
    value is returned with stale=True (and the failure kind); with no cached value an ApiError 503 is raised."""
    now = time.time(); ent = _mem.get(key)
    if ent is None and db is not None:
        r = db.execute('SELECT payload,fetched_at FROM market_cache WHERE cache_key=?', (key,)).fetchone()
        if r:
            try: ent = (json.loads(r['payload']), util.parse_ts(r['fetched_at']).timestamp()); _mem[key] = ent
            except Exception: ent = None
    from . import rbac
    if db is not None and not rbac.setting(db, 'live_prices_enabled'):       # switched off by an administrator: never call the provider
        if ent: return ent[0], _meta(ent[1], True, 'paused', 'cache')
        raise ApiError(503, 'market_data_paused', 'Live market prices are switched off by the platform administrator.', {'reason': 'paused'})
    if ent and now - ent[1] < ttl: return ent[0], _meta(ent[1], False, None, 'cache')
    with _kl(key):
        ent = _mem.get(key) or ent
        if ent and time.time() - ent[1] < ttl: return ent[0], _meta(ent[1], False, None, 'cache')
        try:
            data = fetch(); t = time.time(); _mem[key] = (data, t)
            if db is not None:
                db.execute('INSERT INTO market_cache(cache_key,payload,fetched_at) VALUES(?,?,?) ON CONFLICT(cache_key) DO UPDATE SET payload=excluded.payload,fetched_at=excluded.fetched_at', (key, json.dumps(data), util.now()))
            return data, _meta(t, False, None, 'live')
        except ProviderError as e:
            if ent and time.time() - ent[1] < MAX_STALE: return ent[0], _meta(ent[1], True, e.kind, 'cache')
            raise ApiError(503, 'market_data_unavailable', _human(e), {'reason': e.kind, 'retry_after': e.retry_after})

def _human(e):
    return {'rate_limited': 'The market-data provider is rate-limiting requests. Please try again shortly.', 'timeout': 'The market-data provider timed out.', 'network': 'The market-data provider could not be reached.',
            'auth': 'Market data is not configured correctly (the provider rejected the API key).', 'malformed': 'The market-data provider returned an unexpected response.'}.get(e.kind, 'Market data is temporarily unavailable.')

def _meta(t, stale, reason, source):
    return {'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(t)), 'age_seconds': max(0, int(time.time() - t)), 'stale': stale, 'stale_reason': reason, 'source': source, 'provider': 'CoinGecko'}

# ---------------------------------------------------------------- normalisation (never fabricate)
def _img(u):
    if not isinstance(u, str): return None
    p = urllib.parse.urlparse(u)
    return u if p.scheme == 'https' and p.hostname in IMAGE_HOSTS else None

def _num(v):
    if isinstance(v, bool): return None
    if isinstance(v, (int, float)) and v == v and v not in (float('inf'), float('-inf')): return v
    return None

def _coin_row(c):
    if not isinstance(c, dict) or not isinstance(c.get('id'), str) or not COIN_RE.match(c['id']): return None
    spark = (c.get('sparkline_in_7d') or {}).get('price') if isinstance(c.get('sparkline_in_7d'), dict) else None
    if isinstance(spark, list):
        spark = [round(x, 8) for x in spark if _num(x) is not None]
        if len(spark) > 60: step = len(spark) / 60; spark = [spark[int(i * step)] for i in range(60)]
    else: spark = None
    img = _img(c.get('image'))
    return {'id': c['id'], 'symbol': str(c.get('symbol') or '').upper()[:20], 'name': str(c.get('name') or c['id'])[:80], 'image': img, 'rank': _num(c.get('market_cap_rank')),
            'price': _num(c.get('current_price')), 'market_cap': _num(c.get('market_cap')), 'volume_24h': _num(c.get('total_volume')),
            'change_24h_pct': _num(c.get('price_change_percentage_24h_in_currency', c.get('price_change_percentage_24h'))), 'change_24h_abs': _num(c.get('price_change_24h')),
            'change_7d_pct': _num(c.get('price_change_percentage_7d_in_currency')), 'circulating_supply': _num(c.get('circulating_supply')),
            'high_24h': _num(c.get('high_24h')), 'low_24h': _num(c.get('low_24h')), 'sparkline': spark or None, 'last_updated': c.get('last_updated') if isinstance(c.get('last_updated'), str) else None}

def _markets_raw(params):
    p = {'vs_currency': 'usd', 'sparkline': 'true', 'price_change_percentage': '24h,7d'}; p.update(params)
    data = _call('/coins/markets', p)
    if not isinstance(data, list): raise ProviderError('malformed', 'markets: expected a list')
    return [r for r in (_coin_row(c) for c in data) if r]

def markets(db, order='market_cap_desc', page=1, per_page=100, ids=None):
    key = f"markets:{order}:{page}:{per_page}:{','.join(ids) if ids else ''}"
    p = {'order': order, 'per_page': per_page, 'page': page}
    if ids: p['ids'] = ','.join(ids)
    return cached(db, key, TTL['markets'], lambda: _markets_raw(p))

def trending(db):
    def fetch():
        d = _call('/search/trending', {})
        if not isinstance(d, dict) or not isinstance(d.get('coins'), list): raise ProviderError('malformed', 'trending')
        ids = [it['item']['id'] for it in d['coins'] if isinstance(it, dict) and isinstance(it.get('item'), dict) and isinstance(it['item'].get('id'), str) and COIN_RE.match(it['item']['id'])][:15]
        return ids
    ids, meta = cached(db, 'trending', TTL['trending'], fetch)
    if not ids: return [], meta
    rows, m2 = markets(db, ids=ids, per_page=len(ids))
    pos = {i: n for n, i in enumerate(ids)}; rows = sorted([r for r in rows if r['id'] in pos], key=lambda r: pos[r['id']])
    return rows, (m2 if m2['stale'] else meta)

def search(db, q):
    q = ' '.join(q.split())[:60]
    def fetch():
        d = _call('/search', {'query': q})
        if not isinstance(d, dict) or not isinstance(d.get('coins'), list): raise ProviderError('malformed', 'search')
        return [c['id'] for c in d['coins'] if isinstance(c, dict) and isinstance(c.get('id'), str) and COIN_RE.match(c['id'])][:25]
    ids, meta = cached(db, 'search:' + q.lower(), TTL['search'], fetch)
    if not ids: return [], meta
    rows, m2 = markets(db, ids=ids, per_page=len(ids)); pos = {i: n for n, i in enumerate(ids)}
    return sorted([r for r in rows if r['id'] in pos], key=lambda r: pos[r['id']]), (m2 if m2['stale'] else meta)

def _strip(t): return re.sub(r'\s+', ' ', _html.unescape(re.sub(r'<[^>]+>', ' ', t or ''))).strip()

def coin(db, cid):
    if not COIN_RE.match(cid or ''): raise ApiError(422, 'invalid_coin', 'Invalid coin id.')
    def fetch():
        d = _call(f'/coins/{urllib.parse.quote(cid)}', {'localization': 'false', 'tickers': 'false', 'market_data': 'true', 'community_data': 'false', 'developer_data': 'false', 'sparkline': 'false'})
        if not isinstance(d, dict) or d.get('id') != cid: raise ProviderError('malformed', 'coin')
        md = d.get('market_data') if isinstance(d.get('market_data'), dict) else {}
        usd = lambda k: _num((md.get(k) or {}).get('usd')) if isinstance(md.get(k), dict) else None
        img = (d.get('image') or {}).get('large') if isinstance(d.get('image'), dict) else None
        home = [u for u in ((d.get('links') or {}).get('homepage') or []) if isinstance(u, str) and u.startswith('https://')][:1]
        return {'id': cid, 'symbol': str(d.get('symbol') or '').upper()[:20], 'name': str(d.get('name') or cid)[:80], 'image': _img(img),
                'rank': _num(d.get('market_cap_rank')), 'categories': [str(c)[:40] for c in (d.get('categories') or []) if c][:6], 'homepage': home[0] if home else None,
                'description': _strip(((d.get('description') or {}).get('en')) if isinstance(d.get('description'), dict) else '')[:700], 'genesis_date': d.get('genesis_date') if isinstance(d.get('genesis_date'), str) else None,
                'price': usd('current_price'), 'market_cap': usd('market_cap'), 'fully_diluted_valuation': usd('fully_diluted_valuation'), 'volume_24h': usd('total_volume'), 'high_24h': usd('high_24h'), 'low_24h': usd('low_24h'),
                'ath': usd('ath'), 'ath_date': ((md.get('ath_date') or {}).get('usd') if isinstance(md.get('ath_date'), dict) else None), 'atl': usd('atl'),
                'change_24h_pct': _num(md.get('price_change_percentage_24h')), 'change_24h_abs': _num(md.get('price_change_24h')), 'change_7d_pct': _num(md.get('price_change_percentage_7d')),
                'change_30d_pct': _num(md.get('price_change_percentage_30d')), 'change_1y_pct': _num(md.get('price_change_percentage_1y')),
                'circulating_supply': _num(md.get('circulating_supply')), 'total_supply': _num(md.get('total_supply')), 'max_supply': _num(md.get('max_supply')), 'last_updated': md.get('last_updated') if isinstance(md.get('last_updated'), str) else None}
    return cached(db, 'coin:' + cid, TTL['coin'], fetch)

def normalize_ohlc(rows):
    """Valid, strictly time-ordered candles only. Provider timestamps are candle CLOSE times in ms. Drops malformed or inconsistent rows."""
    out = {}
    for r in rows if isinstance(rows, list) else []:
        if not (isinstance(r, list) and len(r) >= 5): continue
        t, o, h, l, c = r[:5]
        if any(_num(x) is None for x in (t, o, h, l, c)): continue
        if min(o, h, l, c) <= 0 or h < max(o, c, l) - 1e-12 or l > min(o, c, h) + 1e-12: continue
        out[int(t // 1000)] = {'t': int(t // 1000), 'o': o, 'h': h, 'l': l, 'c': c}
    return [out[k] for k in sorted(out)]

def chart(db, cid, tf):
    if not COIN_RE.match(cid or ''): raise ApiError(422, 'invalid_coin', 'Invalid coin id.')
    tf = (tf or '1D').upper()
    if tf not in TIMEFRAMES: raise ApiError(422, 'invalid_timeframe', 'Timeframe must be one of ' + ', '.join(TIMEFRAMES))
    days, gran = TIMEFRAMES[tf]
    def fetch():
        d = _call(f'/coins/{urllib.parse.quote(cid)}/ohlc', {'vs_currency': 'usd', 'days': days})
        if not isinstance(d, list): raise ProviderError('malformed', 'ohlc')
        return normalize_ohlc(d)
    candles, meta = cached(db, f'ohlc:{cid}:{days}', TTL['ohlc'], fetch)
    return {'coin_id': cid, 'timeframe': tf, 'granularity': gran, 'time_is': 'candle_close', 'candles': candles, 'empty': not candles}, meta

def status():
    cfg = Config.get(); now = time.time()
    return {'provider': 'CoinGecko', 'plan': cfg['plan'], 'api_key_configured': bool(cfg['key']), 'base_url_overridden': bool(os.getenv('COINGECKO_BASE_URL')),
            'rate_limited_for_seconds': max(0, int(_state['cooldown_until'] - now)), 'calls_last_minute': len([t for t in _state['calls'] if t > now - 60]), 'budget_per_minute': cfg['max_per_min'],
            'last_success_age_seconds': int(now - _state['last_ok']) if _state['last_ok'] else None, 'last_error_kind': _state['last_error_kind'],
            'note': None if cfg['key'] else 'No COINGECKO_API_KEY set: using the keyless public tier, which is heavily rate-limited. Set a free Demo key for reliable use.'}
