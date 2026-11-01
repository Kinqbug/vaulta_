"""Profile settings: avatar upload/replace/remove, preferences, data export, revoke other sessions, migration."""
import io, os, sys, struct, zlib, json, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.test_vaulta import Base, PW

def png(w, h, rgb=(200, 30, 30)):
    raw = b''.join(b'\x00' + bytes(rgb) * w for _ in range(h))
    def ch(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + ch(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + ch(b'IDAT', zlib.compress(raw)) + ch(b'IEND', b'')

class Settings(Base):
    def up(self, c, data, name='a.png', ctype='image/png'):
        return c.post('/api/profile/avatar', data={'file': (io.BytesIO(data), name, ctype)}, headers={'X-CSRF-Token': c.csrf}, content_type='multipart/form-data')
    def files(self): 
        d = os.path.join(self.tmp, 'up', 'avatars'); return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_migration_adds_columns_and_table(self):
        cols = {r[1] for r in self.db.execute('PRAGMA table_info(user_profiles)')}
        self.assertTrue({'avatar_name', 'avatar_sha256', 'avatar_version'} <= cols)
        self.assertTrue(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='user_settings'").fetchone())

    def test_avatar_requires_auth(self):
        c = self.client()
        self.assertEqual(c.get('/api/profile/avatar').status_code, 401)
        self.assertEqual(c.post('/api/profile/avatar', data={'file': (io.BytesIO(png(256, 256)), 'a.png')}, content_type='multipart/form-data').status_code, 415)       # unauthenticated non-JSON writes are refused by the global guard
        self.assertEqual(c.post('/api/profile/avatar', json={}).status_code, 401)

    def test_upload_serves_and_shows_in_user(self):
        c = self.signup()
        self.assertIsNone(self.req(c, 'get', '/api/auth/me').json['user']['avatar_url'])
        self.assertEqual(c.get('/api/profile/avatar').status_code, 404)
        r = self.up(c, png(256, 256)); self.assertEqual(r.status_code, 201, r.json)
        url = r.json['user']['avatar_url']; self.assertIn('v=1', url)
        g = c.get(url); self.assertEqual(g.status_code, 200); self.assertEqual(g.mimetype, 'image/png')
        self.assertEqual(g.headers['X-Content-Type-Options'], 'nosniff'); self.assertIn('private', g.headers['Cache-Control']); self.assertNotIn('no-store', g.headers['Cache-Control'])
        self.assertEqual(c.get(url, headers={'If-None-Match': g.headers['ETag']}).status_code, 304)
        self.assertIn('v=1', self.req(c, 'get', '/api/auth/me').json['user']['avatar_url'])
        self.assertIn('v=1', self.req(c, 'get', '/api/profile').json['user']['avatar_url'])

    def test_rejects_bad_files(self):
        c = self.signup()
        self.assertEqual(self.up(c, b'<?php echo 1;', 'x.png').status_code, 415)           # wrong content despite .png + image/png
        self.assertEqual(self.up(c, b'MZ\x90\x00' + b'0' * 100, 'x.jpg', 'image/jpeg').status_code, 415)
        self.assertEqual(self.up(c, b'').status_code, 422)
        self.assertEqual(self.up(c, png(200, 100)).status_code, 422)                        # not square
        self.assertEqual(self.up(c, png(64, 64)).status_code, 422)                          # too small
        self.assertEqual(self.up(c, png(256, 256) + b'tail').status_code, 422)             # appended payload
        self.app.config['AVATAR_MAX_BYTES'] = 500
        self.assertEqual(self.up(c, png(256, 256, (1, 2, 3)) + b'').status_code in (413, 422), True)
        self.assertEqual(self.files(), [])
        self.assertIsNone(self.req(c, 'get', '/api/auth/me').json['user']['avatar_url'])

    def test_oversize(self):
        c = self.signup(); self.app.config['AVATAR_MAX_BYTES'] = 100
        self.assertEqual(self.up(c, png(256, 256)).status_code, 413)

    def test_replace_removes_old_file_and_bumps_version(self):
        c = self.signup(); self.up(c, png(256, 256)); first = self.files(); self.assertEqual(len(first), 1)
        r = self.up(c, png(300, 300, (0, 0, 255))); self.assertIn('v=2', r.json['user']['avatar_url'])
        self.assertEqual(len(self.files()), 1); self.assertNotEqual(self.files(), first)

    def test_remove(self):
        c = self.signup(); self.up(c, png(256, 256))
        r = self.req(c, 'delete', '/api/profile/avatar'); self.assertEqual(r.status_code, 200); self.assertIsNone(r.json['user']['avatar_url'])
        self.assertEqual(self.files(), []); self.assertEqual(c.get('/api/profile/avatar').status_code, 404)

    def test_users_cannot_touch_each_others_photo(self):
        a, b = self.signup('a@example.com'), self.signup('b@example.com')
        self.up(a, png(256, 256)); self.up(b, png(256, 256, (0, 200, 0)))
        self.assertNotEqual(a.get('/api/profile/avatar').data, b.get('/api/profile/avatar').data)
        self.req(b, 'delete', '/api/profile/avatar')
        self.assertEqual(a.get('/api/profile/avatar').status_code, 200)                       # a's photo survives b's delete
        # a user id in the request is ignored: the owner is always the session user
        r = a.post('/api/profile/avatar?user_id=2', data={'user_id': '2', 'file': (io.BytesIO(png(256, 256, (9, 9, 9))), 'a.png')}, headers={'X-CSRF-Token': a.csrf}, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 201); self.assertEqual(b.get('/api/profile/avatar').status_code, 404)

    def test_csrf_enforced(self):
        c = self.signup(); r = c.post('/api/profile/avatar', data={'file': (io.BytesIO(png(256, 256)), 'a.png')}, content_type='multipart/form-data')
        self.assertEqual(r.status_code, 403)

    def test_preferences_persist_and_validate(self):
        c = self.signup(); j = self.req(c, 'get', '/api/settings/preferences').json
        self.assertEqual(j['preferences'], {'language': 'en', 'currency': 'USD', 'theme': 'light'})
        self.assertFalse(j['channels']['push']['available']); self.assertFalse(j['channels']['sms']['available'])
        self.assertEqual(self.req(c, 'put', '/api/settings/preferences', {'theme': 'light'}).status_code, 200)
        self.assertEqual(self.req(c, 'put', '/api/settings/preferences', {'theme': 'dark'}).status_code, 422)      # dark mode is not offered until every page supports it
        self.assertEqual(self.req(c, 'put', '/api/settings/preferences', {'theme': 'neon'}).status_code, 422)
        self.assertEqual(self.req(c, 'put', '/api/settings/preferences', {'currency': 'EUR'}).status_code, 422)   # unsupported: no exchange-rate source
        self.assertEqual(self.req(c, 'put', '/api/settings/preferences', {'language': 'fr'}).status_code, 422)
        self.assertEqual(self.req(c, 'get', '/api/auth/me').json['user']['theme'], 'light')
        # survives sign out / sign in; another user is unaffected
        self.req(c, 'post', '/api/auth/logout'); c2 = self.client()
        r = c2.post('/api/auth/login', json={'email': 'a@example.com', 'password': PW}); self.assertEqual(r.json['user']['theme'], 'light')
        other = self.signup('o@example.com'); self.assertEqual(self.req(other, 'get', '/api/settings/preferences').json['preferences'], {'language': 'en', 'currency': 'USD', 'theme': 'light'})

    def test_currency_preference_never_touches_ledger(self):
        c = self.signup(kyc=True); self.fund(c, '500.00')
        before = self.db.execute('SELECT COUNT(*),COALESCE(SUM(amount_cents),0) FROM transactions').fetchone()
        self.req(c, 'put', '/api/settings/preferences', {'currency': 'USD', 'theme': 'light'})
        self.assertEqual(tuple(self.db.execute('SELECT COUNT(*),COALESCE(SUM(amount_cents),0) FROM transactions').fetchone()), tuple(before))

    def test_export_is_scoped_and_has_no_secrets(self):
        a = self.signup('a@example.com'); b = self.signup('b@example.com')
        self.req(a, 'put', '/api/profile', {'first_name': 'Ada', 'phone': '+2348012345678'})
        self.req(b, 'put', '/api/profile', {'first_name': 'Bob'})
        r = self.req(a, 'get', '/api/settings/export'); self.assertEqual(r.status_code, 200)
        self.assertIn('attachment', r.headers['Content-Disposition']); self.assertEqual(r.headers['Cache-Control'], 'no-store')
        txt = r.get_data(as_text=True); d = json.loads(txt)
        self.assertEqual(d['profile']['first_name'], 'Ada'); self.assertEqual(d['account']['email'], 'a@example.com')
        for secret in ('password_hash', 'totp_secret', 'token_hash', 'csrf_token', 'scrypt:', 'b@example.com', 'Bob'): self.assertNotIn(secret, txt)
        self.assertEqual(self.client().get('/api/settings/export').status_code, 401)

    def test_revoke_other_sessions(self):
        c1 = self.signup(); c2 = self.client(); r = c2.post('/api/auth/login', json={'email': 'a@example.com', 'password': PW}); c2.csrf = r.json['csrf_token']
        self.assertEqual(self.req(c1, 'get', '/api/auth/sessions').json['sessions'].__len__(), 2)
        r = self.req(c1, 'post', '/api/auth/sessions/revoke-others'); self.assertEqual(r.json['revoked'], 1)
        self.assertEqual(c2.get('/api/auth/me').status_code, 401)                             # the other device is really signed out
        self.assertEqual(c1.get('/api/auth/me').status_code, 200)
        self.assertTrue(self.db.execute("SELECT 1 FROM security_events WHERE type='other_sessions_revoked'").fetchone())

if __name__ == '__main__': unittest.main()
