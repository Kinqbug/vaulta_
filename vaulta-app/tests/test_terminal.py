"""Trade terminal: market-data service (mock provider), favourites, tradability, chat persistence/authorisation/moderation."""
import json, time, unittest
from test_vaulta import Base, PW
from server import marketdata as md

def coin(i, sym, rank, price, ch=1.5, vol=1e9):
    return {'id': i, 'symbol': sym, 'name': i.title(), 'image': f'https://coin-images.coingecko.com/{i}.png', 'market_cap_rank': rank, 'current_price': price, 'market_cap': price * 1e6, 'total_volume': vol,
            'price_change_percentage_24h_in_currency': ch, 'price_change_24h': price * ch / 100, 'price_change_percentage_7d_in_currency': ch * 2, 'circulating_supply': 1e6,
            'high_24h': price * 1.02, 'low_24h': price * .98, 'sparkline_in_7d': {'price': [price * (1 + k / 100) for k in range(40)]}, 'last_updated': '2026-10-16T10:00:00Z'}
COINS = [coin('bitcoin', 'btc', 1, 80000, 2.0), coin('ethereum', 'eth', 2, 3000, -1.0), coin('solana', 'sol', 5, 150, 7.5), coin('dogecoin', 'doge', 9, .2, -6.0), coin('pepe', 'pepe', 30, .00001, 12.0)]

class Fake:
    """Programmable CoinGecko stand-in: records requests, can fail or rate-limit."""
    def __init__(s): s.calls = []; s.mode = 'ok'; s.ohlc = [[1700000000000, 100, 110, 90, 105], [1700001800000, 105, 120, 100, 118]]
    def __call__(s, url, headers, timeout):
        s.calls.append((url, headers)); path = url.split('/api/v3')[1] if '/api/v3' in url else url
        if s.mode == 'timeout': raise TimeoutError('timed out')
        if s.mode == '429': return 429, {'Retry-After': '30'}, b'{}'
        if s.mode == '401': return 401, {}, b'{}'
        if s.mode == 'bad': return 200, {}, b'<html>oops</html>'
        if path.startswith('/coins/markets'):
            from urllib.parse import urlparse, parse_qs
            q = parse_qs(urlparse(url).query); ids = q.get('ids', [None])[0]
            rows = [c for c in COINS if not ids or c['id'] in ids.split(',')]
            if s.mode == 'empty': rows = []
            if s.mode == 'malformed': rows = {'error': 'x'}
            if q.get('order', [''])[0].endswith('asc'): rows = rows[::-1]
            pp = int(q.get('per_page', ['100'])[0]); pg = int(q.get('page', ['1'])[0]); return 200, {}, json.dumps(rows[(pg - 1) * pp: pg * pp] if isinstance(rows, list) else rows).encode()
        if path.startswith('/search/trending'): return 200, {}, json.dumps({'coins': [{'item': {'id': 'pepe'}}, {'item': {'id': 'solana'}}]}).encode()
        if path.startswith('/search'): return 200, {}, json.dumps({'coins': [{'id': 'solana'}, {'id': 'solar-coin'}]}).encode()
        if path.startswith('/coins/') and '/ohlc' in path: return 200, {}, json.dumps(s.ohlc if s.mode != 'ohlc_empty' else []).encode()
        if path.startswith('/coins/'):
            i = path.split('/')[2].split('?')[0]
            return 200, {}, json.dumps({'id': i, 'symbol': 'btc', 'name': i.title(), 'image': {'large': 'https://coin-images.coingecko.com/x.png'}, 'market_cap_rank': 1, 'categories': ['L1'], 'links': {'homepage': ['https://example.org', '']},
                                        'description': {'en': '<p>Digital <b>money</b> &amp; more</p>'}, 'market_data': {'current_price': {'usd': 80000}, 'market_cap': {'usd': 1.6e12}, 'total_volume': {'usd': 3e10}, 'ath': {'usd': 90000}, 'price_change_percentage_24h': 2.0,
                                                                                                                             'circulating_supply': 19e6, 'max_supply': 21e6}}).encode()
        return 404, {}, b'{}'

class TermBase(Base):
    def setUp(s):
        super().setUp(); md.reset(); s.fake = Fake(); md.set_transport(s.fake)
        for sym, name, px in (('BTC', 'Bitcoin (demo quote)', '64250.00'), ('ETH', 'Ethereum (demo quote)', '3180.00')):
            s.db.execute("INSERT INTO assets(symbol,name,asset_class,provider,external_id,last_price,prev_close,status,tradable,created_at) VALUES(?,?,'Crypto','internal',?,?,?,'active',1,'2026-01-01T00:00:00Z')", (sym, name, 'int:' + sym, px, px))
    def clear_cache(s): md.reset(); s.db.execute('DELETE FROM market_cache')
    def tearDown(s): md.set_transport(None); md.reset(); super().tearDown()
    def user(s, email='t1@example.com', **k): return s.signup(email, **k)
    def admin(s, role, email=None): return s.staff(role, email)
    def j(s, c, path): r = c.get(path); return r.status_code, r.json

class TestMarketData(TermBase):
    def test_requires_login(self): self.assertEqual(self.client().get('/api/terminal/markets').status_code, 401)
    def test_markets_valid_normalised_and_decorated(self):
        c = self.user(); st, j = self.j(c, '/api/terminal/markets?per_page=10'); self.assertEqual(st, 200)
        self.assertEqual([x['id'] for x in j['coins']][:3], ['bitcoin', 'ethereum', 'solana']); b = j['coins'][0]
        for k in ('price', 'market_cap', 'volume_24h', 'change_24h_pct', 'change_7d_pct', 'circulating_supply', 'sparkline', 'rank', 'image'): self.assertIsNotNone(b[k], k)
        self.assertLessEqual(len(b['sparkline']), 60); self.assertFalse(j['meta']['stale']); self.assertEqual(j['meta']['provider'], 'CoinGecko')
        tr = {x['id']: x['trade'] for x in j['coins']}; self.assertTrue(tr['bitcoin']['enabled']); self.assertEqual(tr['bitcoin']['symbol'], 'BTC'); self.assertFalse(tr['dogecoin']['enabled']); self.assertIn('Market data only', tr['dogecoin']['reason'])
    def test_api_key_header_never_returned_and_sent_server_side(self):
        import os; os.environ['COINGECKO_API_KEY'] = 'SECRET-KEY-123'
        try:
            c = self.user(); r = c.get('/api/terminal/markets'); self.assertNotIn('SECRET-KEY-123', r.get_data(as_text=True))
            self.assertEqual(self.fake.calls[0][1].get('x-cg-demo-api-key'), 'SECRET-KEY-123'); s = c.get('/api/terminal/status'); self.assertNotIn('SECRET-KEY-123', s.get_data(as_text=True)); self.assertTrue(s.json['market_data']['api_key_configured'])
        finally: del os.environ['COINGECKO_API_KEY']
    def test_parameter_validation(self):
        c = self.user()
        for q in ('page=0', 'page=abc', 'per_page=101', 'tab=bogus', 'sort=hax', 'dir=sideways', 'q=' + 'x' * 61): self.assertEqual(c.get('/api/terminal/markets?' + q).status_code, 422, q)
        self.assertEqual(c.get('/api/terminal/coins/Bad%20ID/chart').status_code, 422); self.assertEqual(c.get('/api/terminal/coins/bitcoin/chart?tf=5Y').status_code, 422)
    def test_pagination_passes_through(self):
        c = self.user(); j = c.get('/api/terminal/markets?per_page=2&page=2').json; self.assertEqual([x['id'] for x in j['coins']], ['solana', 'dogecoin']); self.assertTrue(j['has_more'] is False or j['has_more'] is True)
        self.assertIn('page=2', self.fake.calls[-1][0]); self.assertIn('per_page=2', self.fake.calls[-1][0])
    def test_gainers_losers_search_trending(self):
        c = self.user(); g = c.get('/api/terminal/markets?tab=gainers').json['coins']; self.assertEqual(g[0]['id'], 'pepe'); self.assertEqual(c.get('/api/terminal/markets?tab=losers').json['coins'][0]['id'], 'dogecoin')
        self.assertIn('top 250', c.get('/api/terminal/markets?tab=gainers').json['scope']); t = c.get('/api/terminal/markets?tab=trending').json['coins']; self.assertEqual([x['id'] for x in t], ['pepe', 'solana'])
        s = c.get('/api/terminal/markets?q=sol').json['coins']; self.assertEqual([x['id'] for x in s], ['solana'])
    def test_local_sorting(self):
        c = self.user(); j = c.get('/api/terminal/markets?sort=price&dir=asc').json['coins']; self.assertEqual(j[0]['id'], 'pepe'); self.assertEqual(c.get('/api/terminal/markets?sort=change_24h&dir=desc').json['coins'][0]['id'], 'pepe')
    def test_empty_and_malformed(self):
        c = self.user(); self.fake.mode = 'empty'; j = c.get('/api/terminal/markets').json; self.assertEqual(j['coins'], [])
        self.clear_cache(); self.fake.mode = 'malformed'; r = c.get('/api/terminal/markets'); self.assertEqual(r.status_code, 503); self.assertEqual(r.json['error']['code'], 'market_data_unavailable')
    def test_timeout_serves_stale_cache_with_flag_then_unavailable(self):
        c = self.user(); self.assertFalse(c.get('/api/terminal/markets').json['meta']['stale'])
        md._mem.clear()
        for k, (d, t) in list(md._mem.items()): md._mem[k] = (d, t - 10_000)
        # age the persisted cache so the TTL has expired
        self.db.execute("UPDATE market_cache SET fetched_at='2020-01-01T00:00:00Z'"); self.db.execute("DELETE FROM market_cache WHERE 0")
        self.fake.mode = 'timeout'; r = c.get('/api/terminal/markets'); self.assertEqual(r.status_code, 503)           # >24h old: too stale to show
        self.db.execute("UPDATE market_cache SET fetched_at=?", (time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 3600)),)); md._mem.clear()
        j = c.get('/api/terminal/markets').json; self.assertTrue(j['meta']['stale']); self.assertEqual(j['meta']['stale_reason'], 'timeout'); self.assertGreaterEqual(j['meta']['age_seconds'], 3500)
    def test_rate_limit_cools_down_and_does_not_hammer_provider(self):
        c = self.user(); self.fake.mode = '429'; self.assertEqual(c.get('/api/terminal/markets').status_code, 503); n = len(self.fake.calls)
        for _ in range(5): c.get('/api/terminal/markets')
        self.assertEqual(len(self.fake.calls), n); self.assertGreater(md.status()['rate_limited_for_seconds'], 0)
    def test_auth_failure_is_reported_without_secrets(self):
        c = self.user(); self.fake.mode = '401'; r = c.get('/api/terminal/markets'); self.assertEqual(r.status_code, 503); self.assertEqual(r.json['error']['details']['reason'], 'auth')
    def test_cache_prevents_duplicate_provider_calls(self):
        c = self.user(); c.get('/api/terminal/markets'); c.get('/api/terminal/markets'); c.get('/api/terminal/markets'); self.assertEqual(sum('/coins/markets' in u for u, _ in self.fake.calls), 1)
    def test_coin_detail_sanitised(self):
        c = self.user(); j = c.get('/api/terminal/coins/bitcoin').json['coin']; self.assertEqual(j['description'], 'Digital money & more'); self.assertEqual(j['homepage'], 'https://example.org'); self.assertEqual(j['vaulta_quote']['symbol'], 'BTC'); self.assertTrue(j['trade']['enabled'])

class TestChart(TermBase):
    def test_valid_ordered_ohlc_and_granularity(self):
        c = self.user(); self.fake.ohlc = [[3000000, 5, 6, 4, 5.5], [1000000, 1, 2, .5, 1.5], [2000000, 2, 3, 1, 2.5]]
        j = c.get('/api/terminal/coins/bitcoin/chart?tf=1D').json['chart']; self.assertEqual([x['t'] for x in j['candles']], [1000, 2000, 3000]); self.assertEqual(j['granularity'], '30 minutes'); self.assertEqual(j['time_is'], 'candle_close')
        self.assertIn('days=1', self.fake.calls[-1][0]); self.assertEqual(c.get('/api/terminal/coins/bitcoin/chart?tf=1Y').json['chart']['granularity'], '4 days')
    def test_invalid_rows_dropped_never_fabricated(self):
        c = self.user(); self.fake.ohlc = [[1000000, 1, 2, 0.5, 1.5], [2000000, 1, 0.5, 2, 1], ['x', 1, 1, 1, 1], [3000000, -1, 1, 1, 1], [4000000, 1, 5, 1, 2], [5000000, None, 1, 1, 1]]
        j = c.get('/api/terminal/coins/bitcoin/chart').json['chart']; self.assertEqual([x['t'] for x in j['candles']], [1000, 4000])
    def test_empty_history(self):
        c = self.user(); self.fake.mode = 'ohlc_empty'; j = c.get('/api/terminal/coins/bitcoin/chart').json['chart']; self.assertTrue(j['empty']); self.assertEqual(j['candles'], [])
    def test_switching_coins_uses_separate_cache_entries(self):
        c = self.user(); c.get('/api/terminal/coins/bitcoin/chart'); c.get('/api/terminal/coins/ethereum/chart'); self.assertEqual(sum('/ohlc' in u for u, _ in self.fake.calls), 2)

class TestTradingBoundaries(TermBase):
    def test_orderbook_is_never_fabricated(self):
        c = self.user(); j = c.get('/api/terminal/coins/bitcoin/orderbook').json; self.assertFalse(j['available']); self.assertEqual(j['bids'], []); self.assertEqual(j['asks'], [])
    def test_fills_only_for_tradable_assets_and_anonymised(self):
        c = self.user(kyc=True); self.fund(c, '5000.00', 't1@example.com'); r = self.req(c, 'post', '/api/orders', {'symbol': 'BTC', 'side': 'buy', 'order_type': 'market', 'quantity': '0.01'}); self.assertIn(r.status_code, (200, 201), r.json)
        j = c.get('/api/terminal/coins/bitcoin/fills').json; self.assertTrue(j['available']); self.assertGreaterEqual(len(j['fills']), 1); self.assertEqual(set(j['fills'][0]), {'ts', 'price', 'quantity', 'side'})
        self.assertFalse(c.get('/api/terminal/coins/dogecoin/fills').json['available'])
    def test_market_data_only_asset_cannot_be_ordered(self):
        c = self.user(kyc=True); self.fund(c, '5000.00', 't1@example.com')
        for sym in ('DOGE', 'PEPE', 'dogecoin'): self.assertIn(self.req(c, 'post', '/api/orders', {'symbol': sym, 'side': 'buy', 'order_type': 'market', 'quantity': '1'}).status_code, (404, 409, 422))

class TestFavorites(TermBase):
    def test_persist_idempotent_and_private(self):
        a = self.user('a1@example.com'); b = self.user('b1@example.com')
        for _ in range(2): self.assertEqual(self.req(a, 'put', '/api/terminal/favorites/solana').status_code, 200)
        self.assertEqual(a.get('/api/terminal/favorites').json['favorites'], ['solana']); self.assertEqual(b.get('/api/terminal/favorites').json['favorites'], [])
        j = a.get('/api/terminal/markets?tab=favorites').json; self.assertEqual([x['id'] for x in j['coins']], ['solana']); self.assertTrue(j['coins'][0]['favorite'])
        self.assertEqual(self.req(a, 'delete', '/api/terminal/favorites/solana').status_code, 200); self.assertEqual(a.get('/api/terminal/favorites').json['favorites'], [])
        self.assertEqual(self.req(a, 'put', '/api/terminal/favorites/Bad%20Id').status_code, 422); self.assertEqual(self.client().get('/api/terminal/favorites').status_code, 401)
        self.assertEqual(a.get('/api/terminal/markets?tab=favorites').json['coins'], [])

class TestChat(TermBase):
    def send(self, c, slug='general', content='hello world', **k): return self.req(c, 'post', f'/api/community/channels/{slug}/messages', dict({'content': content}, **k))
    def test_requires_authentication(self):
        c = self.client()
        for m, p in (('get', '/api/community/channels'), ('get', '/api/community/channels/general/messages'), ('post', '/api/community/channels/general/messages')): self.assertEqual(getattr(c, m)(p, **({'json': {'content': 'x'}} if m == 'post' else {})).status_code, 401)
    def test_send_persist_history_and_identity_from_session(self):
        a = self.user('c1@example.com'); r = self.send(a, content='gm <b>bold</b>', user_id=999, role='admin', name='Mallory'); self.assertEqual(r.status_code, 201, r.json); m = r.json['message']
        self.assertTrue(m['mine']); self.assertEqual(m['content'], 'gm <b>bold</b>'); self.assertNotEqual(m['user']['name'], 'Mallory')
        row = self.db.execute('SELECT user_id,content FROM chat_messages').fetchone(); self.assertEqual(row['user_id'], self.uid('c1@example.com'))
        b = self.user('c2@example.com'); h = b.get('/api/community/channels/general/messages').json; self.assertEqual([x['content'] for x in h['messages']], ['gm <b>bold</b>']); self.assertFalse(h['messages'][0]['mine'])
    def test_pagination_and_polling_cursor(self):
        a = self.user('c3@example.com')
        for i in range(7): self.db.execute("INSERT INTO chat_messages(channel_id,user_id,content,created_at) VALUES(1,?,?,'2026-01-01T00:00:0%dZ')" % i, (self.uid('c3@example.com'), f'm{i}'))
        h = a.get('/api/community/channels/general/messages?limit=3').json; self.assertEqual([x['content'] for x in h['messages']], ['m4', 'm5', 'm6']); self.assertTrue(h['has_more'])
        older = a.get(f"/api/community/channels/general/messages?limit=3&before={h['messages'][0]['id']}").json; self.assertEqual([x['content'] for x in older['messages']], ['m1', 'm2', 'm3'])
        newer = a.get(f"/api/community/channels/general/messages?after={h['messages'][-1]['id']}&poll=1").json; self.assertEqual(newer['messages'], []); self.assertEqual(newer['presence']['count'], 1)
        self.send(a, content='fresh'); self.assertEqual([x['content'] for x in a.get(f"/api/community/channels/general/messages?after={h['messages'][-1]['id']}").json['messages']], ['fresh'])
    def test_validation_limits_and_spam(self):
        a = self.user('c4@example.com')
        for bad in ('', '   ', 'x' * 501, 'a https://a.com https://b.com https://c.com', 'z' * 30): self.assertEqual(self.send(a, content=bad).status_code, 422, bad[:20])
        self.assertEqual(self.send(a, content=None).status_code, 422); self.assertEqual(self.send(a, content='ok\u202e').json['message']['content'], 'ok')
        self.assertEqual(self.send(a, content='same').status_code, 201); self.assertEqual(self.send(a, content='same').status_code, 409)
    def test_burst_rate_limit_enforced_by_database(self):
        a = self.user('c5@example.com'); codes = [self.send(a, content=f'msg {i}').status_code for i in range(8)]; self.assertEqual(codes[:5], [201] * 5); self.assertEqual(set(codes[5:]), {429})
    def test_nonce_prevents_duplicates_on_retry(self):
        a = self.user('c6@example.com'); r1 = self.send(a, content='once', nonce='abcdefgh1234'); r2 = self.send(a, content='once', nonce='abcdefgh1234'); self.assertEqual((r1.status_code, r2.status_code), (201, 200)); self.assertTrue(r2.json['duplicate'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0], 1); self.assertEqual(self.send(a, content='x', nonce='no').status_code, 422)
    def test_unverified_email_cannot_post_but_can_read(self):
        c = self.user('c7@example.com', verify=False); self.assertEqual(c.get('/api/community/channels/general/messages').status_code, 200); self.assertEqual(self.send(c).json['error']['code'], 'email_unverified')
        self.assertFalse([x for x in c.get('/api/community/channels').json['channels'] if x['slug'] == 'general'][0]['can_write'])
    def test_announcements_staff_only_and_channel_listing(self):
        a = self.user('c8@example.com'); ch = {x['slug']: x for x in a.get('/api/community/channels').json['channels']}
        self.assertEqual(list(ch)[:3], ['general', 'bitcoin', 'ethereum']); self.assertTrue(ch['announcements']['read_only']); self.assertFalse(ch['announcements']['can_write']); self.assertEqual(self.send(a, 'announcements').json['error']['code'], 'channel_read_only')
        s = self.admin('support_admin', 'mod@example.com'); self.assertEqual(self.send(s, 'announcements', 'Maintenance tonight').status_code, 201)
        self.assertEqual(a.get('/api/community/channels/announcements/messages').json['messages'][0]['content'], 'Maintenance tonight'); self.assertEqual(self.send(a, 'nope').status_code, 404); self.assertEqual(a.get('/api/community/channels/Bad Slug/messages').status_code, 404)
    def test_reply_validation(self):
        a = self.user('c9@example.com'); m = self.send(a, content='root').json['message']; r = self.send(a, content='child', reply_to=m['id']); self.assertEqual(r.json['message']['reply_to']['id'], m['id'])
        self.assertEqual(self.send(a, 'bitcoin', 'cross', reply_to=m['id']).json['error']['code'], 'invalid_reply')
    def test_report_remove_mute_and_audit(self):
        a = self.user('d1@example.com'); b = self.user('d2@example.com'); mod = self.admin('support_admin', 'mod2@example.com'); m = self.send(a, content='buy my coin now').json['message']
        self.assertEqual(self.req(b, 'post', f"/api/community/messages/{m['id']}/report", {'reason': 'scam'}).status_code, 201); self.assertEqual(self.req(b, 'post', f"/api/community/messages/{m['id']}/report", {'reason': 'scam'}).status_code, 409)
        self.assertEqual(self.req(a, 'post', f"/api/community/messages/{m['id']}/report", {'reason': 'spam'}).json['error']['code'], 'own_message'); self.assertEqual(self.req(b, 'post', f"/api/community/messages/{m['id']}/report", {'reason': 'lol'}).status_code, 422)
        self.assertEqual(self.req(b, 'get', '/api/community/moderation/reports').status_code, 403); rep = self.req(mod, 'get', '/api/community/moderation/reports').json['reports']; self.assertEqual(rep[0]['content'], 'buy my coin now')
        self.assertEqual(self.req(b, 'delete', f"/api/community/messages/{m['id']}").status_code, 403); self.assertEqual(self.req(mod, 'delete', f"/api/community/messages/{m['id']}").status_code, 200)
        self.assertEqual(b.get('/api/community/channels/general/messages').json['messages'][0], {'id': m['id'], 'removed': True, 'created_at': b.get('/api/community/channels/general/messages').json['messages'][0]['created_at']})
        self.assertEqual(self.req(mod, 'get', '/api/community/moderation/reports?status=actioned').json['reports'][0]['message_removed'], True)
        pid = self.db.execute("SELECT public_id FROM users WHERE email='d1@example.com'").fetchone()[0]
        self.assertEqual(self.req(b, 'post', '/api/community/moderation/mute', {'user_id': pid, 'minutes': 30}).status_code, 403); self.assertEqual(self.req(mod, 'post', '/api/community/moderation/mute', {'user_id': pid, 'minutes': 0}).status_code, 422)
        self.assertEqual(self.req(mod, 'post', '/api/community/moderation/mute', {'user_id': pid, 'minutes': 30, 'reason': 'scam'}).status_code, 200); self.assertEqual(self.send(a, content='again').json['error']['code'], 'muted')
        self.assertEqual(self.req(mod, 'post', '/api/community/moderation/unmute', {'user_id': pid}).json['revoked'], 1); self.assertEqual(self.send(a, content='again').status_code, 201)
        acts = {r[0] for r in self.db.execute("SELECT action FROM audit_logs WHERE action LIKE 'chat.%'")}; self.assertTrue({'chat.message_removed', 'chat.user_muted', 'chat.user_unmuted'} <= acts)
    def test_staff_cannot_be_muted_and_messages_immutable(self):
        mod = self.admin('support_admin', 'mod3@example.com'); su = self.admin('super_admin', 'su@example.com'); pid = self.db.execute("SELECT public_id FROM users WHERE email='su@example.com'").fetchone()[0]
        self.assertEqual(self.req(mod, 'post', '/api/community/moderation/mute', {'user_id': pid, 'minutes': 5}).status_code, 403); self.send(mod, content='hi there')
        import sqlite3
        with self.assertRaises(sqlite3.DatabaseError): self.db.execute("UPDATE chat_messages SET content='edited'")
        with self.assertRaises(sqlite3.DatabaseError): self.db.execute('DELETE FROM chat_messages')
    def test_blocking_hides_messages_for_blocker_only(self):
        a = self.user('e1@example.com'); b = self.user('e2@example.com'); self.send(a, content='from a'); pid = self.db.execute("SELECT public_id FROM users WHERE email='e1@example.com'").fetchone()[0]
        self.assertEqual(self.req(b, 'put', f'/api/community/blocks/{pid}').status_code, 200); self.assertEqual(b.get('/api/community/channels/general/messages').json['messages'], []); self.assertEqual(len(a.get('/api/community/channels/general/messages').json['messages']), 1)
        self.assertEqual(self.req(b, 'delete', f'/api/community/blocks/{pid}').status_code, 200); self.assertEqual(len(b.get('/api/community/channels/general/messages').json['messages']), 1)
        self.assertEqual(self.req(a, 'put', f'/api/community/blocks/{pid}').json['error']['code'], 'self_block')
    def test_presence_counts_recent_pollers_only(self):
        a = self.user('f1@example.com'); b = self.user('f2@example.com'); a.get('/api/community/channels/general/messages?poll=1'); b.get('/api/community/channels/general/messages?poll=1')
        self.assertEqual(a.get('/api/community/channels/general/presence').json['count'], 2); self.db.execute("UPDATE chat_presence SET last_seen='2020-01-01T00:00:00Z'"); self.assertEqual(a.get('/api/community/channels/general/presence').json['count'], 0)
        b.get('/api/community/channels/bitcoin/messages?poll=1'); self.assertEqual(self.db.execute('SELECT COUNT(*) FROM chat_presence WHERE user_id=?', (self.uid('f2@example.com'),)).fetchone()[0], 1)

if __name__ == '__main__': unittest.main()

class TestImagesAndCsp(TermBase):
    def test_only_allowlisted_https_logo_hosts_pass_through_and_csp_allows_them(self):
        c = self.user(); COINS[0]['image'] = 'https://evil.example/x.png'; COINS[1]['image'] = 'http://coin-images.coingecko.com/x.png'
        try:
            j = c.get('/api/terminal/markets').json['coins']; self.assertIsNone(j[0]['image']); self.assertIsNone(j[1]['image']); self.assertIsNotNone(j[2]['image'])
        finally: COINS[0]['image'] = 'https://coin-images.coingecko.com/bitcoin.png'; COINS[1]['image'] = 'https://coin-images.coingecko.com/ethereum.png'
        csp = c.get('/api/terminal/status').headers['Content-Security-Policy']; self.assertIn('https://coin-images.coingecko.com', csp); self.assertNotIn('evil.example', csp); self.assertIn("script-src 'self'", csp)
