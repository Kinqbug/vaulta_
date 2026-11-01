"""Local stand-in for the CoinGecko API (response shapes follow the documented endpoints). For offline development and browser tests only:
    python tests/tools/mock_coingecko.py 5090     then run the app with COINGECKO_BASE_URL=http://127.0.0.1:5090/api/v3
Data are synthetic test fixtures and must never be used outside development."""
import json, math, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
NAMES = [('bitcoin', 'btc', 'Bitcoin', 80000), ('ethereum', 'eth', 'Ethereum', 3200), ('tether', 'usdt', 'Tether', 1), ('solana', 'sol', 'Solana', 150), ('usd-coin', 'usdc', 'USDC', 1), ('dogecoin', 'doge', 'Dogecoin', .2), ('pepe', 'pepe', 'Pepe', .00001)]
COINS = []
for i in range(120):
    if i < len(NAMES): cid, sym, name, px = NAMES[i]
    else: cid, sym, name, px = f'coin-{i}', f'c{i}', f'Coin {i}', 100 / (i + 1)
    ch = round(math.sin(i * 1.7) * 9, 2)
    COINS.append({'id': cid, 'symbol': sym, 'name': name, 'image': f'https://coin-images.coingecko.com/{cid}.png', 'market_cap_rank': i + 1, 'current_price': px, 'market_cap': px * 1e7 / (i + 1), 'total_volume': px * 1e6 / (i + 1) + 1,
                  'price_change_percentage_24h_in_currency': ch, 'price_change_24h': px * ch / 100, 'price_change_percentage_7d_in_currency': round(ch * 1.4, 2), 'circulating_supply': 1e7 / (i + 1), 'high_24h': px * 1.03, 'low_24h': px * .97,
                  'sparkline_in_7d': {'price': [px * (1 + .05 * math.sin(k / 6 + i)) for k in range(168)]}, 'last_updated': '2026-10-16T10:00:00.000Z'})
BYID = {c['id']: c for c in COINS}
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def send(self, code, obj, hdr=None):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type', 'application/json'); [self.send_header(k, v) for k, v in (hdr or {}).items()]; self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}; p = u.path.replace('/api/v3', '')
        if open('/tmp/mock_cg_mode', 'a+').close() or True: mode = (open('/tmp/mock_cg_mode').read().strip() if __import__('os').path.exists('/tmp/mock_cg_mode') else 'ok')
        if mode == '429': return self.send(429, {'status': {'error_code': 429}}, {'Retry-After': '20'})
        if mode == 'down': return self.send(500, {})
        if p == '/coins/markets':
            rows = [c for c in COINS if 'ids' not in q or c['id'] in q['ids'].split(',')]
            if q.get('order') == 'volume_desc': rows = sorted(rows, key=lambda c: -c['total_volume'])
            pp, pg = int(q.get('per_page', 100)), int(q.get('page', 1)); return self.send(200, rows[(pg - 1) * pp: pg * pp])
        if p == '/search/trending': return self.send(200, {'coins': [{'item': {'id': i}} for i in ('pepe', 'solana', 'dogecoin')]})
        if p == '/search': s = q.get('query', '').lower(); return self.send(200, {'coins': [{'id': c['id']} for c in COINS if s in c['id'] or s in c['name'].lower() or s == c['symbol']][:25]})
        if p.startswith('/coins/') and p.endswith('/ohlc'):
            c = BYID.get(p.split('/')[2]); 
            if not c: return self.send(404, {})
            d = int(q.get('days', 1)); step = 1800 if d <= 2 else 14400 if d <= 30 else 345600; n = min(120, int(d * 86400 / step)); now = int(time.time()); px = c['current_price']; rows = []
            for k in range(n):
                t = (now - (n - k) * step) * 1000; a = px * (1 + .02 * math.sin(k / 5)); b = px * (1 + .02 * math.sin((k + 1) / 5)); rows.append([t, a, max(a, b) * 1.004, min(a, b) * .996, b])
            return self.send(200, rows)
        if p.startswith('/coins/'):
            c = BYID.get(p.split('/')[2])
            if not c: return self.send(404, {})
            return self.send(200, {'id': c['id'], 'symbol': c['symbol'], 'name': c['name'], 'image': {'large': c['image']}, 'market_cap_rank': c['market_cap_rank'], 'categories': ['Layer 1'], 'links': {'homepage': ['https://example.org']}, 'description': {'en': f"<p>{c['name']} is a synthetic test asset.</p>"}, 'genesis_date': '2009-01-03',
                                   'market_data': {'current_price': {'usd': c['current_price']}, 'market_cap': {'usd': c['market_cap']}, 'total_volume': {'usd': c['total_volume']}, 'high_24h': {'usd': c['high_24h']}, 'low_24h': {'usd': c['low_24h']}, 'ath': {'usd': c['current_price'] * 1.5}, 'atl': {'usd': c['current_price'] * .01},
                                                   'price_change_percentage_24h': c['price_change_percentage_24h_in_currency'], 'price_change_24h': c['price_change_24h'], 'price_change_percentage_7d': c['price_change_percentage_7d_in_currency'], 'price_change_percentage_30d': 11.2, 'price_change_percentage_1y': 80.5, 'circulating_supply': c['circulating_supply'], 'max_supply': 21000000}})
        self.send(404, {})
if __name__ == '__main__': ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1]) if len(sys.argv) > 1 else 5090), H).serve_forever()
