"""Customers management API: summary, filtered list with balances + photos, permission-checked avatar route, audited CSV export."""
import io, os, sys, struct, unittest, zlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.test_vaulta import Base, PW

def png(w=256, h=256, rgb=(30, 120, 200)):
    raw = b''.join(b'\x00' + bytes(rgb) * w for _ in range(h)); c = lambda t, d: struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + c(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + c(b'IDAT', zlib.compress(raw)) + c(b'IEND', b'')

class TestCustomersAdmin(Base):
    def setUp(self):
        super().setUp(); self.admin = self.staff('super_admin', 'root@vaulta.test')
        self.c1 = self.signup('ada@example.com'); self.c2 = self.signup('bob@example.com', kyc=True)
        self.db.execute("UPDATE user_profiles SET first_name='Ada',last_name='Lovelace' WHERE user_id=?", (self.uid('ada@example.com'),))
        self.db.execute("UPDATE users SET kyc_status='under_review' WHERE email='ada@example.com'")
    def upload(self, c, data=None):
        return c.post('/api/profile/avatar', data={'file': (io.BytesIO(data or png()), 'a.png')}, headers={'X-CSRF-Token': c.csrf}, content_type='multipart/form-data')

    def test_summary_counts_are_real_and_exclude_staff(self):
        j = self.admin.get('/api/admin/users/summary').json
        self.assertEqual(j['total'], 2); self.assertEqual(j['verified'], 1); self.assertEqual(j['pending_kyc'], 1); self.assertEqual(j['restricted'], 0); self.assertEqual(j['new_7d'], 2)
        self.assertEqual(j['by_status'], {'active': 2}); self.assertTrue(any(a['kind'] == 'user.registered' for a in j['recent']))
        self.req(self.admin, 'post', f"/api/admin/users/{self.db.execute('SELECT public_id FROM users WHERE email=?', ('bob@example.com',)).fetchone()[0]}/status", {'status': 'restricted', 'reason': 'Manual review'})
        j = self.admin.get('/api/admin/users/summary').json; self.assertEqual(j['restricted'], 1); self.assertEqual(j['by_status'], {'active': 1, 'restricted': 1})
        self.assertEqual(j['recent'][0]['kind'], 'user.status_changed')

    def test_list_has_balance_filters_search_and_sort(self):
        self.fund(self.c1, '250.00', 'ada@example.com')
        r = self.admin.get('/api/admin/users').json; self.assertEqual(r['total'], 2)
        ada = next(u for u in r['items'] if u['email'] == 'ada@example.com'); self.assertEqual(ada['total_balance'], '250.00'); self.assertNotIn('_pk', ada); self.assertIsNone(ada['avatar_url'])
        self.assertEqual([u['email'] for u in self.admin.get('/api/admin/users?q=lovelace').json['items']], ['ada@example.com'])
        self.assertEqual([u['email'] for u in self.admin.get('/api/admin/users?q=Ada Lovelace').json['items']], ['ada@example.com'])
        self.assertEqual([u['email'] for u in self.admin.get('/api/admin/users?kyc=verified').json['items']], ['bob@example.com'])
        self.assertEqual([u['email'] for u in self.admin.get('/api/admin/users?sort=oldest').json['items']], ['ada@example.com', 'bob@example.com'])
        self.assertEqual([u['email'] for u in self.admin.get('/api/admin/users?q=a%25d_a').json['items']], ['ada@example.com'])   # % and _ are stripped (literal 'ada'), not executed as wildcards
        self.assertEqual(self.admin.get('/api/admin/users?q=zzz').json['total'], 0)

    def test_customer_photo_reaches_admin_only_with_permission(self):
        self.assertEqual(self.upload(self.c1).status_code, 201)
        ada = next(u for u in self.admin.get('/api/admin/users').json['items'] if u['email'] == 'ada@example.com'); self.assertRegex(ada['avatar_url'], r'^/api/admin/users/USR-[^/]+/avatar\?v=1$')
        r = self.admin.get(ada['avatar_url']); self.assertEqual(r.status_code, 200); self.assertEqual(r.mimetype, 'image/png'); self.assertEqual(r.headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn('max-age', r.headers['Cache-Control'])
        self.assertEqual(self.admin.get(f"/api/admin/users/{self.db.execute('SELECT public_id FROM users WHERE email=?', ('bob@example.com',)).fetchone()[0]}/avatar").status_code, 404)   # no photo -> initials in the UI
        self.assertEqual(self.c2.get(ada['avatar_url'].split('?')[0]).status_code, 403)                  # a customer cannot read another customer's photo
        self.assertEqual(self.client().get(ada['avatar_url']).status_code, 401)
        self.assertEqual(self.admin.get(f"/api/admin/users/{ada['id']}").json['user']['avatar_url'], ada['avatar_url'])
        self.assertEqual(self.c1.get('/api/auth/me').json['user']['avatar_url'], '/api/profile/avatar?v=1')   # the customer's own dashboard shows the same photo
        self.assertEqual(self.req(self.c1, 'delete', '/api/profile/avatar').status_code, 200)
        self.assertIsNone(next(u for u in self.admin.get('/api/admin/users').json['items'] if u['email'] == 'ada@example.com')['avatar_url'])

    def test_restricting_a_customer_is_reflected_on_their_own_dashboard(self):
        pid = self.db.execute('SELECT public_id FROM users WHERE email=?', ('ada@example.com',)).fetchone()[0]
        self.assertEqual(self.req(self.admin, 'post', f'/api/admin/users/{pid}/status', {'status': 'restricted', 'reason': 'Compliance hold'}).status_code, 200)
        self.assertEqual(self.c1.get('/api/auth/me').json['user']['status'], 'restricted')
        self.assertTrue(self.c1.get('/api/notifications').status_code in (200, 403))
        self.assertEqual(self.req(self.admin, 'post', f'/api/admin/users/{pid}/status', {'status': 'active', 'reason': 'Hold cleared'}).status_code, 200)
        self.assertEqual(self.c1.get('/api/auth/me').json['user']['status'], 'active')

    def test_csv_export_is_permissioned_filtered_audited_and_formula_safe(self):
        self.db.execute("UPDATE user_profiles SET first_name='=HYPERLINK(\"x\")' WHERE user_id=?", (self.uid('bob@example.com'),))
        r = self.admin.get('/api/admin/users/export.csv?kyc=verified'); self.assertEqual(r.status_code, 200); self.assertEqual(r.mimetype, 'text/csv')
        rows = r.data.decode().strip().splitlines(); self.assertEqual(len(rows), 2); self.assertIn('bob@example.com', rows[1]); self.assertNotIn('ada@example.com', r.data.decode())
        self.assertIn("'=HYPERLINK", rows[1])                                                                # neutralised for spreadsheets
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM audit_logs WHERE action='users.exported'").fetchone()[0], 1)
        sup = self.staff('support_admin', 'help@vaulta.test'); self.assertEqual(sup.get('/api/admin/users/export.csv').status_code, 403)   # support can read users but not export them
        self.assertEqual(sup.get('/api/admin/users').status_code, 200); self.assertEqual(self.c1.get('/api/admin/users/export.csv').status_code, 403)

if __name__ == '__main__': unittest.main()
