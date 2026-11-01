"""Screenshot deposit requests, admin approval, asset catalog with logos, manual withdrawal processing. Real app, real database, mock chain only."""
import io, os, re, sys, struct, threading, unittest, zlib
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_crypto import CryptoBase
from test_vaulta import PW
from server import uploads, accounting as acc, crypto_deposits as cd, crypto_withdrawals as cwd, crypto_recon as rc, crypto_ledger as cl, crypto_setup as cs, chain_providers as cp, util, db as dbm
from server.util import ApiError
try:
    import cv2, numpy as np
except ImportError: cv2 = None

def png(w=200, h=300, seed=0, noise=False):
    raw = b''.join(b'\x00' + (os.urandom(w * 3) if noise else bytes(((x * 3 + y + seed) % 256) for x in range(w * 3))) for y in range(h))
    def ch(t, d): return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + ch(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + ch(b'IDAT', zlib.compress(raw, 0 if noise else 6)) + ch(b'IEND', b'')

SVG_OK = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="#f7931a"/><path d="M20 20h24v24H20z" style="fill:#fff;stroke:#000"/></svg>'
SVG_BAD = [b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><script>alert(1)</script></svg>', b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1" onload="x()"><rect width="1" height="1"/></svg>',
           b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1" fill="url(http://evil.example/x)"/></svg>', b'<!DOCTYPE svg [<!ENTITY a "b">]><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
           b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><foreignObject/></svg>', b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><image href="http://evil.example/a.png"/></svg>',
           b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><a href="javascript:alert(1)"><rect width="1" height="1"/></a></svg>', b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"><rect width="1" height="1" style="fill:red;background:url(javascript:x)"/></svg>']

class TestUploadValidation(unittest.TestCase):
    def test_png_accepted_and_content_not_filename_decides(self):
        self.assertEqual(uploads.validate_screenshot(png())[0], 'image/png')
    @unittest.skipUnless(cv2, 'OpenCV needed to synthesise JPEG/WebP')
    def test_jpeg_and_webp_accepted(self):
        img = np.zeros((120, 160, 3), np.uint8); img[:, :80] = (40, 160, 220)
        self.assertEqual(uploads.validate_screenshot(cv2.imencode('.jpg', img)[1].tobytes())[0], 'image/jpeg'); self.assertEqual(uploads.validate_screenshot(cv2.imencode('.webp', img)[1].tobytes())[0], 'image/webp')
    def test_rejects_disguised_truncated_padded_and_oversized(self):
        good = png()
        for name, d, code in (('html as png', b'<html><script>alert(1)</script></html>', 'unsupported_file'), ('exe', b'MZ' + b'\0' * 400, 'unsupported_file'), ('truncated', good[:-20], 'invalid_image'), ('payload appended', good + b'<?php evil ?>', 'invalid_image'),
                              ('empty', b'', 'invalid_image'), ('svg as screenshot', SVG_OK, 'unsupported_file'), ('gif', b'GIF89a' + b'\0' * 50, 'unsupported_file'), ('tiny', png(20, 20), 'invalid_image')):
            with self.assertRaises(ApiError, msg=name) as e: uploads.validate_screenshot(d)
            self.assertEqual(e.exception.code, code, name)
        with self.assertRaises(ApiError) as e: uploads.validate_screenshot(png(1000, 1800, noise=True))
        self.assertEqual(e.exception.code, 'file_too_large')
        bad_crc = bytearray(good); bad_crc[40] ^= 0xff
        with self.assertRaises(ApiError): uploads.validate_screenshot(bytes(bad_crc))
    def test_logo_rules(self):
        self.assertEqual(uploads.validate_logo(png(64, 64))[0], 'image/png'); self.assertEqual(uploads.validate_logo(SVG_OK)[0], 'image/svg+xml')
        for bad in SVG_BAD:
            with self.assertRaises(ApiError, msg=bad[:50]) as e: uploads.validate_logo(bad)
            self.assertEqual(e.exception.code, 'unsafe_svg')
        with self.assertRaises(ApiError): uploads.validate_logo(png(16, 16))
        with self.assertRaises(ApiError): uploads.validate_logo(png(3000, 40))
        stored = uploads.validate_logo(SVG_OK)[1]; self.assertNotIn(b'script', stored.lower())
    def test_storage_uses_random_private_names_and_blocks_traversal(self):
        import tempfile; root = tempfile.mkdtemp(); n1, h1 = uploads.store(root, 'x', png(), 'image/png'); n2, _ = uploads.store(root, 'x', png(), 'image/png')
        self.assertNotEqual(n1, n2); self.assertRegex(n1, r'^[0-9a-f]{32}\.png$'); self.assertEqual(os.stat(os.path.join(root, 'x', n1)).st_mode & 0o777, 0o600)
        for evil in ('../../etc/passwd', '..%2f', n1 + '/../x', '', None): 
            with self.assertRaises(ApiError): uploads.load(root, 'x', evil)

class RB(CryptoBase):
    CODE = 'USDT.ETHEREUM'; AMT = 100_000_000
    def multipart(self, c, url, fields, file='default', headers=None, name='proof.png'):
        data = dict(fields)
        if file == 'default': file = png(seed=len(self._shots) if hasattr(self, '_shots') else 0)
        if file is not None: data['screenshot'] = (io.BytesIO(file), name)
        return c.post(url, data=data, content_type='multipart/form-data', headers={'X-CSRF-Token': c.csrf, **(headers or {})})
    def customer(self, email='a@example.com'): c = self.signup(email, kyc=True); return c, {'id': self.uid(email)}
    def start(self, c, code=None): r = self.req(c, 'post', '/api/crypto/deposit-intents', {'ledger_code': code or self.CODE}); self.assertEqual(r.status_code, 201, r.json); return r.json['intent']
    def submit(self, c, intent, amount='100', txid=None, **kw):
        f = {'intent_ref': intent['ref'], 'network': intent['network'], 'amount': amount}
        if txid: f['tx_hash'] = txid
        f.update(kw.pop('fields', {})); return self.multipart(c, '/api/crypto/deposit-requests', f, **kw)
    def ops(self, email='ops@vaulta.test', role='operations_admin'): return self.staff(role, email)
    def approve(self, a, rid, **b): return self.req(a, 'post', f'/api/admin/crypto/deposit-requests/{rid}/approve', b)
    def reject(self, a, rid, reason='Transaction details do not match our records'): return self.req(a, 'post', f'/api/admin/crypto/deposit-requests/{rid}/reject', {'reason': reason})
    def paid(self, amount=None, sender='0x' + '9' * 40, confs=12): return self.pay(self.CODE, amount or self.AMT, sender, confs=confs)
    def one(self, sql, *a): return self.db.execute(sql, a).fetchone()
    def rid(self, ref): return self.one('SELECT id FROM deposit_requests WHERE ref=?', ref)[0]

class TestDepositRequests(RB):
    def test_full_flow_from_screenshot_to_credit_with_every_record(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); h = self.paid()
        r = self.submit(c, i, '100', h); self.assertEqual(r.status_code, 201, r.json); q = r.json['request']
        self.assertEqual((q['status'], q['asset'], q['network_code'], q['amount'], q['has_screenshot']), ('pending', 'USDT', 'ethereum', '100.000000', True)); self.assertEqual(q['stage'], 1)
        self.assertEqual(self.bal(u, self.CODE), 0)                                                  # unverified deposits never become spendable
        lst = a.get('/api/admin/crypto/deposit-requests?status=pending').json; self.assertEqual(lst['total'], 1); row = lst['requests'][0]
        self.assertEqual((row['user']['email'], row['asset'], row['network'], row['destination_address'], row['tx_hash']), ('a@example.com', 'USDT', 'Ethereum (test network)', self.wal[self.CODE]['public_address'], h))
        det = a.get(f"/api/admin/crypto/deposit-requests/{row['id']}").json; self.assertEqual(det['chain_check']['status'], 'verified'); self.assertEqual(det['chain_check']['confirmations'], 12)
        ev = a.get(f"/api/admin/crypto/deposit-requests/{row['id']}/evidence"); self.assertEqual((ev.status_code, ev.mimetype), (200, 'image/png'))
        r = self.approve(a, row['id'], note='checked block explorer'); self.assertEqual(r.status_code, 200, r.json); self.assertEqual(r.json['request']['status'], 'approved')
        self.assertEqual(self.bal(u, self.CODE), self.AMT); self.assert_gl()
        d = self.one('SELECT * FROM crypto_deposits'); self.assertEqual((d['status'], d['origin'], d['user_id']), ('credited', 'manual_request', u['id']))
        rq = self.one('SELECT * FROM deposit_requests'); self.assertEqual((rq['status'], rq['reviewed_by'], rq['verification_method'], rq['verified_amount']), ('approved', self.uid('ops@vaulta.test'), 'chain', str(self.AMT))); self.assertTrue(rq['reviewed_at'])
        self.assertEqual(self.one("SELECT COUNT(*) FROM journal_entries WHERE entry_type IN('crypto.deposit_received','crypto.deposit_credit')")[0], 2)
        self.assertTrue(self.one("SELECT 1 FROM audit_logs WHERE action='crypto.deposit_request_approved' AND actor_id=?", self.uid('ops@vaulta.test')))
        self.assertTrue(self.one("SELECT 1 FROM notifications WHERE user_id=? AND title='Deposit approved'", u['id']))
        self.assertEqual(self.one("SELECT status FROM deposit_intents")[0], 'fulfilled')
        hist = c.get('/api/crypto/funding-history').json['items']; self.assertEqual((hist[0]['kind'], hist[0]['status'], hist[0]['ref']), ('deposit', 'approved', q['ref']))
        tx = c.get('/api/transactions').json; t0 = tx['transactions'][0]; self.assertEqual((t0['crypto'], t0['type'], t0['amount'], t0['currency'], t0['status']), (True, 'deposit', '100.000000', 'USDT', 'completed'))
        self.assertEqual(c.get(f"/api/transactions/{q['ref']}").json['transaction']['ref'], q['ref'])
        row = self.one("SELECT amount_cents,metadata,status FROM transactions WHERE user_id=? AND type='deposit'", u['id'])                # the deposit landed in the Account Balance: one completed cash credit, tagged as a crypto conversion
        self.assertEqual((row[0], row[2]), (10_000, 'completed')); self.assertIn('crypto_conversion', row[1]); self.assertEqual(self.cash(u), 10_000)
        self.assertEqual((self.liability(u, self.CODE), c.get('/api/crypto/balances').json['balances']), (0, []))                              # no crypto holding is left behind
        self.assertEqual(sum(1 for x in tx['transactions'] if x['ref'] != q['ref'] and x.get('type') == 'deposit'), 0)                         # the cash side is not listed twice on the Transactions page
        self.assertEqual(c.get('/api/portfolio').json['summary']['portfolio_value'], '0.00'); self.assertEqual(c.get('/api/dashboard').json['summary']['available_cash'], '100.00')
        cv = self.one("SELECT direction,usd_cents,price,reversed_at FROM crypto_conversions"); self.assertEqual(tuple(cv), ('deposit', 10_000, '1.0000', None))
        self.assertEqual(rc.reconcile(self.db, self.P)['status'], 'clean')
    def test_double_click_and_retries_credit_once(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); h = self.paid(); rid = self.rid(self.submit(c, i, '100', h).json['request']['ref'])
        self.assertEqual(self.approve(a, rid).status_code, 200); r2 = self.approve(a, rid); self.assertEqual((r2.status_code, r2.json['error']['code']), (409, 'invalid_state'))
        self.assertEqual(self.approve(self.ops('o2@vaulta.test'), rid).status_code, 409); self.assertEqual(self.bal(u, self.CODE), self.AMT)
        self.assertEqual(self.one("SELECT COUNT(*) FROM journal_entries WHERE entry_type='crypto.deposit_credit'")[0], 1)
    def test_simultaneous_approvals_from_many_administrators_credit_exactly_once(self):
        c, u = self.customer(); i = self.start(c); h = self.paid(); rid = self.rid(self.submit(c, i, '100', h).json['request']['ref'])
        admins = [self.ops(f'o{k}@vaulta.test') for k in range(5)]; out = []; gate = threading.Barrier(len(admins))
        def go(cl_):
            gate.wait(); out.append(self.approve(cl_, rid).status_code)
        ts = [threading.Thread(target=go, args=(x,)) for x in admins]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(sorted(out), [200, 409, 409, 409, 409], out); self.assertEqual(self.bal(u, self.CODE), self.AMT)
        self.assertEqual(self.one("SELECT COUNT(*) FROM journal_entries WHERE entry_type='crypto.deposit_credit'")[0], 1); self.assert_gl()
    def test_rejection_requires_reason_credits_nothing_notifies_and_keeps_evidence(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); h = self.paid(); q = self.submit(c, i, '100', h).json['request']; rid = self.rid(q['ref'])
        self.assertEqual(self.reject(a, rid, 'no').status_code, 422); r = self.reject(a, rid, 'The screenshot shows a different amount than claimed'); self.assertEqual(r.status_code, 200)
        self.assertEqual(self.bal(u, self.CODE), 0); rq = self.one('SELECT * FROM deposit_requests'); self.assertEqual((rq['status'], rq['reviewed_by']), ('rejected', self.uid('ops@vaulta.test'))); self.assertTrue(rq['reviewed_at'] and rq['rejection_reason'])
        self.assertEqual(a.get(f'/api/admin/crypto/deposit-requests/{rid}/evidence').status_code, 200)                     # evidence stays available to administrators
        note = self.one("SELECT body FROM notifications WHERE user_id=? AND title='Deposit not approved'", u['id']); self.assertIn('different amount', note[0])
        mine = c.get(f"/api/crypto/deposit-requests/{q['ref']}").json['request']; self.assertEqual((mine['status'], mine['stage_state']), ('rejected', 'failed')); self.assertIn('different amount', mine['rejection_reason'])
        self.assertEqual(self.approve(a, rid).status_code, 409); self.assertEqual(self.reject(a, rid).status_code, 409); self.assertEqual(self.bal(u, self.CODE), 0)
        again = self.submit(c, i, '100', h); self.assertEqual(again.status_code, 201, again.json)                              # a rejected claim may be resubmitted
        self.assertEqual(self.approve(a, self.rid(again.json['request']['ref'])).status_code, 200); self.assertEqual(self.bal(u, self.CODE), self.AMT)
    def test_decided_requests_cannot_be_silently_modified(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); h = self.paid(); rid = self.rid(self.submit(c, i, '100', h).json['request']['ref']); self.approve(a, rid)
        for sql in ("UPDATE deposit_requests SET status='rejected'", "UPDATE deposit_requests SET admin_notes='x'", "UPDATE deposit_requests SET screenshot_sha256='x'", "UPDATE deposit_requests SET claimed_amount='1'", 'DELETE FROM deposit_requests', 'DELETE FROM deposit_request_events', "UPDATE deposit_request_events SET event='x'"):
            with self.assertRaises(Exception, msg=sql): self.db.execute(sql)
    def test_manual_attestation_when_the_chain_cannot_verify(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); rid = self.rid(self.submit(c, i, '100').json['request']['ref'])         # no TXID supplied
        self.assertEqual(self.approve(a, rid).json['error']['code'], 'verification_required')
        self.assertEqual(self.approve(a, rid, attest=True, note='short', verified_amount='100').json['error']['code'], 'note_required')
        self.assertEqual(self.approve(a, rid, attest=True, note='Checked the exchange withdrawal receipt and block explorer for this transfer', verified_amount='').json['error']['code'], 'verified_amount_required')
        self.assertEqual(self.bal(u, self.CODE), 0)
        r = self.approve(a, rid, attest=True, note='Checked the exchange withdrawal receipt and block explorer for this transfer', verified_amount='99.5'); self.assertEqual(r.status_code, 200, r.json)
        rq = self.one('SELECT * FROM deposit_requests'); self.assertEqual((rq['verification_method'], rq['verified_amount']), ('manual_attestation', '99500000')); self.assertEqual(self.bal(u, self.CODE), 99_500_000)
        self.assertIn('ledger_mismatch', {e['kind'] for e in (rc.reconcile(self.db, self.P) and rc.list_exceptions(self.db))})       # attested funds that are not on-chain are flagged, never hidden
    def test_chain_verification_blocks_unverifiable_claims(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c)
        rid = self.rid(self.submit(c, i, '100', '0x' + 'ab' * 32).json['request']['ref']); self.assertEqual(self.approve(a, rid).json['error']['code'], 'transaction_not_found'); self.assertEqual(self.approve(a, rid, attest=True, note='x' * 30, verified_amount='100').json['error']['code'], 'transaction_not_found')
        other = self.chain.fund('ethereum', cs.demo_address('evm', 'elsewhere'), self.AMT, sender='0x' + '8' * 40, contract=self.info[self.CODE]['contract_address'], mine=True); self.chain.mine('ethereum', 12)
        r2 = self.rid(self.submit(c, i, '100', other).json['request']['ref']); self.assertEqual(self.approve(a, r2).json['error']['code'], 'transaction_mismatch')
        wrongtok = self.chain.fund('ethereum', self.wal[self.CODE]['public_address'], self.AMT, sender='0x' + '8' * 40, contract='0x' + '5' * 40, mine=True); self.chain.mine('ethereum', 12)
        r3 = self.rid(self.submit(c, i, '100', wrongtok).json['request']['ref']); self.assertEqual(self.approve(a, r3).json['error']['code'], 'transaction_mismatch')
        young = self.paid(confs=3); r4 = self.rid(self.submit(c, i, '100', young).json['request']['ref']); e = self.approve(a, r4); self.assertEqual((e.status_code, e.json['error']['code']), (409, 'insufficient_confirmations'))
        self.assertEqual(self.bal(u, self.CODE), 0); self.chain.mine('ethereum', 9); self.assertEqual(self.approve(a, r4).status_code, 200); self.assertEqual(self.bal(u, self.CODE), self.AMT)
    def test_onchain_amount_wins_and_a_difference_needs_a_note(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); h = self.paid(amount=90_000_000); rid = self.rid(self.submit(c, i, '100', h).json['request']['ref'])
        self.assertEqual(self.approve(a, rid).json['error']['code'], 'note_required'); self.assertEqual(self.approve(a, rid, note='customer overstated; credited the verified on-chain amount').status_code, 200); self.assertEqual(self.bal(u, self.CODE), 90_000_000)
    def test_a_transaction_can_only_be_claimed_and_credited_once(self):
        c, u = self.customer(); b, ub = self.customer('b@example.com'); a = self.ops(); i = self.start(c); ib = self.start(b); h = self.paid()
        self.assertEqual(self.submit(c, i, '100', h).status_code, 201); self.assertEqual(self.submit(c, i, '100', h.upper().replace('0X', '0x')).json['error']['code'], 'duplicate_transaction'); self.assertEqual(self.submit(b, ib, '100', h).json['error']['code'], 'duplicate_transaction')
        self.approve(a, self.rid(c.get('/api/crypto/deposit-requests').json['requests'][0]['ref'])); self.assertEqual(self.submit(b, ib, '100', h).json['error']['code'], 'duplicate_transaction'); self.assertEqual((self.bal(u, self.CODE), self.bal(ub, self.CODE)), (self.AMT, 0))
    def test_request_and_onchain_monitor_cannot_both_credit(self):
        c, u = self.customer(); a = self.ops(); src = self.verify_sender(u, self.CODE); i = self.start(c); h = self.pay(self.CODE, self.AMT, src)
        self.scan(); self.assertEqual(self.bal(u, self.CODE), self.AMT)                                                          # the verified-sender path already credited it
        i2 = self.start(c); self.assertEqual(self.submit(c, i2, '100', h).json['error']['code'], 'already_credited'); self.scan(); self.assertEqual(self.bal(u, self.CODE), self.AMT)
    def test_unmatched_chain_deposit_is_resolved_by_the_request_without_double_credit(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); h = self.paid(); self.scan(); self.assertEqual(self.dep(h)['status'], 'manual_review')       # monitor saw it first: unattributed
        rid = self.rid(self.submit(c, i, '100', h).json['request']['ref']); self.assertEqual(self.approve(a, rid).status_code, 200); self.assertEqual(self.bal(u, self.CODE), self.AMT)
        for _ in range(3): self.scan()
        self.assertEqual(self.bal(u, self.CODE), self.AMT); self.assertEqual(self.one('SELECT COUNT(*) FROM crypto_deposits')[0], 1); self.assertEqual(acc.balance(self.db, 'SUSPENSE', self.CODE), 0)
    def test_manual_credit_then_late_chain_detection_is_not_credited_again(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); h = self.paid(); rid = self.rid(self.submit(c, i, '100', h).json['request']['ref'])
        with mock.patch.object(self.P.chain, 'get_transaction', side_effect=cp.ChainProviderNotConfigured('Blockchain provider')):
            self.assertEqual(self.approve(a, rid, attest=True, note='Verified on the block explorer: transfer is final', verified_amount='100').status_code, 200)
        self.assertEqual(self.bal(u, self.CODE), self.AMT); src = self.verify_sender(u, self.CODE)
        self.db.execute("UPDATE deposit_intents SET status='open'"); self.scan(); self.scan(); self.assertEqual(self.bal(u, self.CODE), self.AMT)
        late = [d for d in self.db.execute("SELECT status,status_reason FROM crypto_deposits WHERE origin='chain'")]; self.assertTrue(all(d['status'] != 'credited' for d in late), [tuple(d) for d in late])
    def test_users_cannot_approve_their_own_deposits_or_reach_review_endpoints(self):
        c, u = self.customer(); i = self.start(c); h = self.paid(); rid = self.rid(self.submit(c, i, '100', h).json['request']['ref'])
        self.assertEqual(self.approve(c, rid).status_code, 403); self.assertEqual(self.reject(c, rid).status_code, 403); self.assertEqual(c.get('/api/admin/crypto/deposit-requests').status_code, 403)
        self.db.execute("INSERT INTO user_roles(user_id,role_id) SELECT ?,id FROM roles WHERE name='operations_admin'", (u['id'],)); me = self.client(); r = me.post('/api/auth/login', json={'email': 'a@example.com', 'password': PW}); me.csrf = r.json['csrf_token']
        e = self.approve(me, rid); self.assertEqual((e.status_code, e.json['error']['code']), (403, 'separation_of_duties')); self.assertEqual(self.reject(me, rid).json['error']['code'], 'separation_of_duties'); self.assertEqual(self.bal(u, self.CODE), 0)
        self.assertEqual(self.client().get('/api/admin/crypto/deposit-requests').status_code, 401)
        for role in ('support_admin', 'content_admin'):
            s = self.staff(role, f'{role}@vaulta.test'); self.assertEqual(s.get('/api/admin/crypto/deposit-requests').status_code, 403); self.assertEqual(self.approve(s, rid).status_code, 403)
    def test_screenshots_are_private_to_their_owner_and_authorised_staff(self):
        c, u = self.customer(); other, _ = self.customer('b@example.com'); i = self.start(c); q = self.submit(c, i, '100', self.paid()).json['request']
        self.assertEqual(c.get(f"/api/crypto/deposit-requests/{q['ref']}/evidence").status_code, 200); self.assertEqual(other.get(f"/api/crypto/deposit-requests/{q['ref']}/evidence").status_code, 404)
        self.assertEqual(other.get(f"/api/crypto/deposit-requests/{q['ref']}").status_code, 404); self.assertEqual(other.get('/api/crypto/deposit-requests').json['requests'], []); self.assertEqual(self.client().get(f"/api/crypto/deposit-requests/{q['ref']}/evidence").status_code, 401)
        r = c.get(f"/api/crypto/deposit-requests/{q['ref']}/evidence"); self.assertEqual(r.headers['X-Content-Type-Options'], 'nosniff'); self.assertIn('no-store', r.headers['Cache-Control']); self.assertIn("sandbox", r.headers['Content-Security-Policy'])
        a = self.ops(); self.assertEqual(a.get(f"/api/admin/crypto/deposit-requests/{self.rid(q['ref'])}/evidence?download=1").headers['Content-Disposition'].startswith('attachment'), True)
        self.assertTrue(self.one("SELECT 1 FROM audit_logs WHERE action='crypto.deposit_evidence_downloaded'"))
        name = self.one('SELECT screenshot_name FROM deposit_requests')[0]; root = self.app.config['UPLOAD_DIR']; self.assertRegex(name, r'^[0-9a-f]{32}\.png$'); self.assertNotIn('proof', name)
        p = os.path.join(root, 'deposit-evidence', name); self.assertTrue(os.path.isfile(p)); self.assertEqual(os.stat(p).st_mode & 0o777, 0o600); self.assertFalse(p.startswith(os.path.join(os.path.dirname(__file__), '..', 'web')))
    def test_upload_validation_over_http(self):
        c, u = self.customer(); i = self.start(c)
        self.assertEqual(self.submit(c, i, '100', file=None).json['error']['code'], 'screenshot_required')
        for name, data, code, status in (('html', b'<script>alert(1)</script>', 'unsupported_file', 415), ('svg', SVG_OK, 'unsupported_file', 415), ('trunc', png()[:-30], 'invalid_image', 422), ('big', png(1000, 1800, noise=True), 'file_too_large', 413)):
            r = self.submit(c, i, '100', file=data, name=name + '.png'); self.assertEqual((r.status_code, r.json['error']['code']), (status, code), name)
        r = self.submit(c, i, '100', file=b'0' * (7 * 1024 * 1024)); self.assertEqual(r.status_code, 413); self.assertEqual(self.one('SELECT COUNT(*) FROM deposit_requests')[0], 0)
        self.assertEqual(os.listdir(os.path.join(self.app.config['UPLOAD_DIR'], 'deposit-evidence')) if os.path.isdir(os.path.join(self.app.config['UPLOAD_DIR'], 'deposit-evidence')) else [], [])     # rejected uploads leave no files behind
    def test_form_validation_network_amount_txid_and_limits(self):
        c, u = self.customer(); i = self.start(c); a = self.staff('crypto_operations', 'cops@vaulta.test'); anid = self.info[self.CODE]['an_id']
        self.assertEqual(self.submit(c, i, '100', fields={'network': 'solana'}).json['error']['code'], 'network_mismatch'); self.assertEqual(self.submit(c, i, '1').json['error']['code'], 'below_minimum')
        self.assertEqual(self.submit(c, i, '0').json['error']['code'], 'invalid_amount'); self.assertEqual(self.submit(c, i, 'abc').status_code, 422); self.assertEqual(self.submit(c, i, '100', 'not-a-hash').json['error']['code'], 'invalid_tx_hash')
        self.assertEqual(self.submit(c, i, '100', fields={'sender_address': self.sender('SOL', 'x')}).json['error']['code'], 'invalid_address')
        self.assertEqual(self.req(a, 'post', f'/api/admin/crypto/asset-networks/{anid}/config', {'max_deposit': '500', 'reason': 'cap deposits for launch'}).status_code, 200); self.assertEqual(self.submit(c, i, '501').json['error']['code'], 'above_maximum')
        self.assertEqual(self.submit(c, i, '500').status_code, 201); self.assertEqual(self.submit(c, {**i, 'ref': 'DIN-NOPE'}, '100').status_code, 404)
    def test_disabled_hidden_and_unconfigured_assets_cannot_receive_requests(self):
        c, u = self.customer(); i = self.start(c); a = self.staff('crypto_operations', 'cops@vaulta.test'); ops = self.ops(); anid = self.info[self.CODE]['an_id']; aid = self.info[self.CODE]['asset_id']; h = self.paid(); q = self.submit(c, i, '100', h)
        rid = self.rid(q.json['request']['ref'])
        self.assertEqual(self.req(a, 'post', f'/api/admin/crypto/asset-networks/{anid}/config', {'deposit_enabled': False, 'reason': 'pause USDT deposits for maintenance'}).status_code, 200)
        self.assertEqual(self.req(c, 'post', '/api/crypto/deposit-intents', {'ledger_code': self.CODE}).json['error']['code'], 'asset_unavailable'); self.assertEqual(self.submit(c, i, '100').json['error']['code'], 'asset_unavailable')
        self.assertNotIn('USDT', [x['symbol'] for x in c.get('/api/crypto/assets').json['assets']]); self.assertEqual(self.approve(ops, rid).json['error']['code'], 'asset_unavailable'); self.assertEqual(self.bal(u, self.CODE), 0)
        self.req(a, 'post', f'/api/admin/crypto/asset-networks/{anid}/config', {'deposit_enabled': True, 'reason': 'maintenance finished, resuming'}); self.assertIn('USDT', [x['symbol'] for x in c.get('/api/crypto/assets').json['assets']])
        self.assertEqual(self.req(a, 'post', f'/api/admin/crypto/assets/{aid}', {'visible': False}).status_code, 200); self.assertNotIn('USDT', [x['symbol'] for x in c.get('/api/crypto/assets').json['assets']])
        self.assertEqual(self.req(c, 'post', '/api/crypto/deposit-intents', {'ledger_code': self.CODE}).json['error']['code'], 'asset_unavailable')
    def test_idempotent_submission_and_pending_cap(self):
        c, u = self.customer(); i = self.start(c); f = png(seed=9); a = self.multipart(c, '/api/crypto/deposit-requests', {'intent_ref': i['ref'], 'network': 'ethereum', 'amount': '50'}, file=f, headers={'Idempotency-Key': 'k-1'})
        b = self.multipart(c, '/api/crypto/deposit-requests', {'intent_ref': i['ref'], 'network': 'ethereum', 'amount': '50'}, file=f, headers={'Idempotency-Key': 'k-1'}); self.assertEqual((a.status_code, b.status_code), (201, 201))
        self.assertEqual(a.json['request']['ref'], b.json['request']['ref']); self.assertEqual(self.one('SELECT COUNT(*) FROM deposit_requests')[0], 1); self.assertEqual(len(os.listdir(os.path.join(self.app.config['UPLOAD_DIR'], 'deposit-evidence'))), 1)
        for k in range(4): self.assertEqual(self.submit(c, i, '50').status_code, 201)
        self.assertEqual(self.submit(c, i, '50').json['error']['code'], 'too_many_pending')
    def test_customer_can_cancel_a_pending_request_but_not_after_a_decision(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); q1 = self.submit(c, i, '50').json['request']; q2 = self.submit(c, i, '60').json['request']
        self.assertEqual(self.req(c, 'post', f"/api/crypto/deposit-requests/{q1['ref']}/cancel").status_code, 200); self.assertEqual(self.approve(a, self.rid(q1['ref']), attest=True, note='x' * 30, verified_amount='50').status_code, 409)
        self.reject(a, self.rid(q2['ref'])); self.assertEqual(self.req(c, 'post', f"/api/crypto/deposit-requests/{q2['ref']}/cancel").status_code, 409)
    def test_admin_search_filter_and_pagination(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); refs = [self.submit(c, i, str(10 + k)).json['request']['ref'] for k in range(3)]; self.reject(a, self.rid(refs[0]))
        j = a.get('/api/admin/crypto/deposit-requests').json; self.assertEqual((j['total'], j['counts']), (3, {'pending': 2, 'rejected': 1}))
        self.assertEqual(a.get('/api/admin/crypto/deposit-requests?status=rejected').json['total'], 1); self.assertEqual(a.get(f'/api/admin/crypto/deposit-requests?q={refs[1]}').json['total'], 1)
        self.assertEqual(a.get('/api/admin/crypto/deposit-requests?q=a@example.com').json['total'], 3); self.assertEqual(a.get('/api/admin/crypto/deposit-requests?q=zzz').json['total'], 0)
        self.assertEqual(len(a.get('/api/admin/crypto/deposit-requests?size=2&page=2').json['requests']), 1); self.assertEqual(a.get('/api/admin/crypto/deposit-requests?asset=BTC').json['total'], 0)
    def test_duplicate_screenshot_is_flagged_for_the_reviewer(self):
        c, u = self.customer(); a = self.ops(); i = self.start(c); f = png(seed=5); q1 = self.submit(c, i, '50', file=f).json['request']; q2 = self.submit(c, i, '60', file=f).json['request']
        self.assertEqual(a.get(f"/api/admin/crypto/deposit-requests/{self.rid(q2['ref'])}").json['request']['evidence_duplicate_of'], q1['ref'])

class TestCatalogAndLogos(RB):
    def setUp(self):
        super().setUp(); self.cops = self.staff('crypto_operations', 'cops@vaulta.test')
    def create(self, c, symbol='DOGE', logo=None, **f):
        d = {'symbol': symbol, 'name': 'Dogecoin', 'type': 'native', 'description': 'Much wow', 'visible': 'true', **f}
        if logo is not None: d['logo'] = (io.BytesIO(logo), 'logo.png')
        return c.post('/api/admin/crypto/assets', data=d, content_type='multipart/form-data', headers={'X-CSRF-Token': getattr(c, 'csrf', '')})
    def test_create_asset_with_logo_networks_limits_fees_and_serve_it_everywhere(self):
        r = self.create(self.cops, logo=png(64, 64, seed=3), network_code='ethereum', decimals='8', min_deposit='1', max_deposit='1000', min_withdrawal='2', max_withdrawal='500', fee_flat='0.5', fee_percent='1.5', confirmations='7', activate='true'); self.assertEqual(r.status_code, 201, r.json)
        a = r.json['asset']; n = a['networks'][0]; self.assertEqual((a['symbol'], a['visible'], a['status'], bool(a['logo'])), ('DOGE', True, 'active', True))
        self.assertEqual((n['min_deposit'], n['max_deposit'], n['min_withdrawal'], n['max_withdrawal'], n['fee_flat'], n['fee_percent'], n['confirmations_override']), ('1.00000000', '1000.00000000', '2.00000000', '500.00000000', '0.50000000', '1.5', 7))
        lg = self.cops.get(a['logo']); self.assertEqual((lg.status_code, lg.mimetype, lg.headers['X-Content-Type-Options']), (200, 'image/png', 'nosniff')); self.assertEqual(lg.data[:8], b'\x89PNG\r\n\x1a\n')
        self.assertEqual(self.client().get(a['logo']).status_code, 401); self.assertEqual(self.cops.get(a['logo'], headers={'If-None-Match': lg.headers['ETag']}).status_code in (200, 304), True)
        self.assertEqual(self.one('SELECT COUNT(*) FROM crypto_assets WHERE symbol="DOGE"')[0], 1); self.assertTrue(self.one("SELECT 1 FROM audit_logs WHERE action='crypto.asset_catalog_created'"))
    def test_replacing_a_logo_updates_the_same_asset_and_removes_the_old_file(self):
        a = self.create(self.cops, logo=png(64, 64, 1)).json['asset']; old = self.one('SELECT logo_path FROM crypto_assets WHERE id=?', a['id'])[0]; root = os.path.join(self.app.config['UPLOAD_DIR'], 'asset-logos'); self.assertTrue(os.path.exists(os.path.join(root, old)))
        r = self.cops.post(f"/api/admin/crypto/assets/{a['id']}", data={'logo': (io.BytesIO(SVG_OK), 'new.svg')}, content_type='multipart/form-data', headers={'X-CSRF-Token': self.cops.csrf}); self.assertEqual(r.status_code, 200, r.json)
        b = r.json['asset']; self.assertEqual(b['id'], a['id']); self.assertNotEqual(b['logo'], a['logo']); self.assertEqual(self.one('SELECT COUNT(*) FROM crypto_assets WHERE symbol="DOGE"')[0], 1)
        self.assertFalse(os.path.exists(os.path.join(root, old))); lg = self.cops.get(b['logo']); self.assertEqual(lg.mimetype, 'image/svg+xml'); self.assertIn('sandbox', lg.headers['Content-Security-Policy']); self.assertNotIn(b'script', lg.data.lower())
        names = self.client(); rm = self.req(self.cops, 'post', f"/api/admin/crypto/assets/{a['id']}", {'remove_logo': True}); self.assertIsNone(rm.json['asset']['logo']); self.assertEqual(self.cops.get(f"/api/crypto/logos/{a['id']}").status_code, 404)
    def test_unsafe_or_invalid_logos_are_refused_and_nothing_is_stored(self):
        for bad, code in [(b, 'unsafe_svg') for b in SVG_BAD] + [(b'<html>x</html>', 'unsupported_file'), (png(16, 16), 'invalid_image'), (png(64, 64) + b'junk', 'invalid_image'), (png(1200, 1000, noise=True), 'file_too_large')]:
            r = self.cops.post('/api/admin/crypto/assets', data={'symbol': 'BAD', 'name': 'Bad', 'logo': (io.BytesIO(bad), 'x.svg')}, content_type='multipart/form-data', headers={'X-CSRF-Token': self.cops.csrf}); self.assertEqual((r.status_code in (413, 415, 422), r.json['error']['code']), (True, code), bad[:40])
        self.assertEqual(self.one('SELECT COUNT(*) FROM crypto_assets WHERE symbol="BAD"')[0], 0); d = os.path.join(self.app.config['UPLOAD_DIR'], 'asset-logos'); self.assertEqual(os.listdir(d) if os.path.isdir(d) else [], [])
    def test_unauthorised_users_cannot_create_edit_or_delete_assets(self):
        a = self.create(self.cops, 'XRP').json['asset']; cust, _ = self.customer(); sup = self.staff('support_admin', 'sup@vaulta.test'); ops = self.ops(); anon = self.client()
        for who in (cust, sup, ops):
            self.assertEqual(self.create(who, 'ZZZ').status_code, 403); self.assertEqual(self.req(who, 'post', f"/api/admin/crypto/assets/{a['id']}", {'name': 'hack'}).status_code, 403)
            self.assertEqual(self.req(who, 'delete', f"/api/admin/crypto/assets/{a['id']}").status_code, 403); self.assertEqual(self.req(who, 'post', f"/api/admin/crypto/asset-networks/1/config", {'deposit_enabled': True, 'reason': 'x' * 20}).status_code, 403)
        self.assertIn(self.create(anon, 'ZZZ').status_code, (401, 415)); self.assertEqual(self.one('SELECT name FROM crypto_assets WHERE id=?', a['id'])[0], 'Dogecoin')
    def test_delete_is_only_for_assets_without_history(self):
        fresh = self.create(self.cops, 'LTC', logo=png(64, 64, 7)).json['asset']; path = os.path.join(self.app.config['UPLOAD_DIR'], 'asset-logos', self.one('SELECT logo_path FROM crypto_assets WHERE id=?', fresh['id'])[0])
        self.assertEqual(self.req(self.cops, 'delete', f"/api/admin/crypto/assets/{fresh['id']}").status_code, 200); self.assertFalse(os.path.exists(path)); self.assertEqual(self.one('SELECT COUNT(*) FROM crypto_assets WHERE symbol="LTC"')[0], 0)
        e = self.req(self.cops, 'delete', f"/api/admin/crypto/assets/{self.info[self.CODE]['asset_id']}"); self.assertEqual((e.status_code, e.json['error']['code']), (409, 'has_history'))
    def test_logos_appear_in_every_asset_context_from_the_backend_record(self):
        c, u = self.customer(); b = self.cops; aid = self.info['ETH']['asset_id']; r = b.post(f'/api/admin/crypto/assets/{aid}', data={'logo': (io.BytesIO(png(64, 64, 4)), 'eth.png')}, content_type='multipart/form-data', headers={'X-CSRF-Token': b.csrf}); url = r.json['asset']['logo']; self.assertTrue(url)
        assets = c.get('/api/crypto/assets').json; self.assertEqual({a['logo'] for a in assets['assets'] if a['symbol'] == 'ETH'}, {url}); self.assertEqual({a['logo'] for a in assets['withdrawal_assets'] if a['symbol'] == 'ETH'}, {url}); self.assertIsNone([a for a in assets['assets'] if a['symbol'] == 'BTC'][0]['logo'])
        cur = type('C', (), {'lastrowid': self.one("SELECT id FROM assets WHERE symbol='ETH'")[0]}); self.db.execute('INSERT INTO asset_prices(asset_id,ts,price) VALUES(?,?,?)', (cur.lastrowid, util.now(), '3000'))
        self.assertEqual({x['logo'] for x in c.get('/api/assets').json['assets'] if x['symbol'] == 'ETH'}, {url})
        src = self.verify_sender(u, 'ETH'); self.intent(u, 'ETH'); self.pay('ETH', 10**17, src); self.scan(); self.assertEqual(self.liability(u, 'ETH'), 0); self.assertGreater(self.cash(u), 0)
        self.assertEqual(c.get('/api/crypto/balances').status_code, 200); item = c.get('/api/crypto/funding-history').json['items']; self.assertEqual(item[0]['logo'], url)
        self.assertEqual(self.staff('crypto_operations', 'c2@vaulta.test').get('/api/admin/crypto/assets').json['assets'][1]['logo'], url)
    def test_hidden_assets_disappear_from_selectors_but_holders_keep_their_logo(self):
        c, u = self.customer(); aid = self.info['ETH']['asset_id']; self.cops.post(f'/api/admin/crypto/assets/{aid}', data={'logo': (io.BytesIO(png(64, 64, 4)), 'e.png')}, content_type='multipart/form-data', headers={'X-CSRF-Token': self.cops.csrf})
        other, _ = self.customer('b@example.com'); self.assertEqual(other.get(f'/api/crypto/logos/{aid}').status_code, 200)
        self.req(self.cops, 'post', f'/api/admin/crypto/assets/{aid}', {'visible': False}); self.assertNotIn('ETH', [x['symbol'] for x in other.get('/api/crypto/assets').json['assets']]); self.assertEqual(other.get(f'/api/crypto/logos/{aid}').status_code, 404)
        self.assertEqual(self.cops.get(f'/api/crypto/logos/{aid}').status_code, 200)
    def test_catalog_validation(self):
        self.assertEqual(self.create(self.cops, 'x').status_code, 422); self.assertEqual(self.create(self.cops, 'DOGE', network_code='ethereum', decimals='8', min_deposit='5', max_deposit='1').json['error']['code'], 'invalid_limits')
        self.assertEqual(self.create(self.cops, 'ADA', fee_percent='5').status_code, 201); an = self.create(self.cops, 'ZEC', network_code='ethereum', decimals='8').json['asset']['networks'][0]['id']
        for bad in ({'fee_percent': '99'}, {'fee_percent': 'abc'}, {'min_withdrawal': '-1'}, {'confirmations': '0'}, {'max_deposit': 'x'}): self.assertEqual(self.req(self.cops, 'post', f'/api/admin/crypto/asset-networks/{an}/config', bad).status_code, 422, bad)
        self.assertEqual(self.create(self.cops, 'DOGE').status_code, 201); self.assertEqual(self.create(self.cops, 'DOGE').json['error']['code'], 'exists'); self.assertEqual(self.req(self.cops, 'post', '/api/admin/crypto/assets', {'symbol': 'QQQ', 'name': 'Q', 'private_key': 'abc'}).json['error']['code'], 'secret_material_refused')

class TestWithdrawalManagement(RB):
    def fund(self, u, c, code=None, amt=None):
        code = code or self.CODE; src = self.verify_sender(u, code); self.intent(u, code); self.pay(code, amt or 200_000_000, src); self.scan()
    def wd(self, c, u, amount='40', dest=None, code=None):
        self.totp(u['id']); return self.req(c, 'post', '/api/crypto/withdrawals', {'ledger_code': code or self.CODE, 'amount': amount, 'destination': dest or self.sender(self.CODE, 'dest'), 'code': self.code(u['id'])})
    def test_limits_fees_and_quote(self):
        c, u = self.customer(); self.fund(u, c); cops = self.staff('crypto_operations', 'cops@vaulta.test'); an = self.info[self.CODE]['an_id']
        self.assertEqual(self.req(cops, 'post', f'/api/admin/crypto/asset-networks/{an}/config', {'min_withdrawal': '10', 'max_withdrawal': '100', 'fee_flat': '1', 'fee_percent': '2', 'reason': 'launch fee schedule'}).status_code, 200)
        q = c.get(f'/api/crypto/withdrawal-quote?ledger_code={self.CODE}&amount=50').json; self.assertEqual((q['fee'], q['total_debit'], q['min'], q['max']), ('2.000000', '52.000000', '10.000000', '100.000000'))
        self.assertEqual(self.wd(c, u, '5').json['error']['code'], 'below_minimum'); self.assertEqual(self.wd(c, u, '101').json['error']['code'], 'above_maximum'); r = self.wd(c, u, '50'); self.assertEqual(r.status_code, 201, r.json)
        w = r.json['withdrawal']; self.assertEqual((w['amount'], w['fee'], w['status']), ('50.000000', '2.000000', 'pending_approval')); self.assertEqual(self.bal(u, self.CODE), 200_000_000 - 52_000_000)
        ops = self.ops(); row = ops.get('/api/admin/crypto/withdrawals').json['withdrawals'][0]; self.assertEqual((row['user']['email'], row['network'], row['fee'], row['destination'], bool(row['asset_id'])), ('a@example.com', 'Ethereum (test network)', '2.000000', w['destination'], True))
    def test_disabled_asset_blocks_withdrawals(self):
        c, u = self.customer(); self.fund(u, c); cops = self.staff('crypto_operations', 'cops@vaulta.test'); an = self.info[self.CODE]['an_id']
        self.req(cops, 'post', f'/api/admin/crypto/asset-networks/{an}/config', {'withdrawal_enabled': False, 'reason': 'pause withdrawals for maintenance'}); self.assertEqual(self.wd(c, u).json['error']['code'], 'asset_unavailable')
        self.assertNotIn('USDT', [x['symbol'] for x in c.get('/api/crypto/assets').json['withdrawal_assets']]); self.assertEqual(self.bal(u, self.CODE), 200_000_000)
    def test_manual_processing_is_not_completion(self):
        c, u = self.customer(); self.fund(u, c); w = self.wd(c, u, '40').json['withdrawal']; ops = self.ops(); wid = w['id']; A = lambda act, **b: self.req(ops, 'post', f'/api/admin/crypto/withdrawals/{wid}/{act}', b)
        self.assertEqual(A('manual_process', tx_hash='0x' + '1' * 64).json['error']['code'], 'invalid_state'); self.assertEqual(A('approve').status_code, 200)
        self.assertEqual(A('manual_process', tx_hash='nope').json['error']['code'], 'invalid_tx_hash'); r = A('manual_process', tx_hash='0x' + '1' * 64, note='sent from treasury console'); self.assertEqual(r.status_code, 200, r.json)
        self.assertEqual((r.json['withdrawal']['status'], r.json['withdrawal']['processing_mode'], r.json['withdrawal']['tx_hash']), ('broadcast', 'manual', '0x' + '1' * 64)); self.assertEqual(acc.balance(self.db, 'CRYPTO_WD_CLEARING', self.CODE), 40_000_000)
        self.assertEqual(A('manual_complete').json['error']['code'], 'transaction_not_found'); self.assertEqual(self.one('SELECT status FROM crypto_withdrawals')[0], 'broadcast')
        info = self.info[self.CODE]; h = self.chain.send('ethereum', [dict(index=0, sender=self.wal[self.CODE]['public_address'], recipient=self.sender(self.CODE, 'dest'), contract=info['contract_address'], amount=40_000_000)])
        self.db.execute('UPDATE crypto_withdrawals SET tx_hash=?', (h,)); self.chain.mine('ethereum', 3); self.assertEqual(A('manual_complete').json['error']['code'], 'insufficient_confirmations')
        self.chain.mine('ethereum', 9); r = A('manual_complete'); self.assertEqual((r.status_code, r.json['withdrawal']['status']), (200, 'completed')); self.assertEqual(acc.balance(self.db, 'CRYPTO_WD_CLEARING', self.CODE), 0); self.assert_gl()
        self.assertEqual(rc.reconcile(self.db, self.P)['status'], 'clean'); self.assertTrue(self.one("SELECT 1 FROM audit_logs WHERE action='crypto.withdrawal_manual_processed'"))
    def test_manual_completion_without_a_chain_provider_needs_an_attestation(self):
        c, u = self.customer(); self.fund(u, c); w = self.wd(c, u, '40').json['withdrawal']; ops = self.ops(); wid = w['id']; A = lambda act, **b: self.req(ops, 'post', f'/api/admin/crypto/withdrawals/{wid}/{act}', b)
        A('approve'); A('manual_process', tx_hash='0x' + '2' * 64)
        with mock.patch.object(self.P.chain, 'get_transaction', side_effect=cp.ChainProviderNotConfigured('Blockchain provider')):
            self.assertEqual(A('manual_complete').json['error']['code'], 'verification_required'); self.assertEqual(A('manual_complete', attest=True, note='short').json['error']['code'], 'verification_required')
            r = A('manual_complete', attest=True, note='Confirmed final on the block explorer with the treasury operator'); self.assertEqual((r.status_code, r.json['withdrawal']['status'], r.json['withdrawal']['manual_attested']), (200, 'completed', True))
        self.assertEqual(A('manual_complete', attest=True, note='x' * 30).status_code, 409)
    def test_duplicate_txid_and_own_approval_are_refused(self):
        c, u = self.customer(); self.fund(u, c); w1 = self.wd(c, u, '10').json['withdrawal']; w2 = self.wd(c, u, '10').json['withdrawal']; ops = self.ops()
        for w in (w1, w2): self.req(ops, 'post', f"/api/admin/crypto/withdrawals/{w['id']}/approve", {})
        self.assertEqual(self.req(ops, 'post', f"/api/admin/crypto/withdrawals/{w1['id']}/manual_process", {'tx_hash': '0x' + '3' * 64}).status_code, 200)
        self.assertEqual(self.req(ops, 'post', f"/api/admin/crypto/withdrawals/{w2['id']}/manual_process", {'tx_hash': '0x' + '3' * 64}).json['error']['code'], 'duplicate_transaction')
    def test_concurrent_withdrawal_requests_never_overspend(self):
        c, u = self.customer(); self.fund(u, c, amt=100_000_000); self.totp(u['id']); path = self.app.config['DATABASE_PATH']; results = []; gate = threading.Barrier(6)
        def go(k):
            con = dbm.connect(path); gate.wait()
            try: cwd.request_withdrawal(con, self.P, u, self.CODE, '40', self.sender(self.CODE, 'dest'), '000000'); results.append('ok')
            except ApiError as e: results.append(e.code)
            finally: con.close()
        with mock.patch('server.finance.require_2fa', lambda *a, **kw: None):          # patched ONCE for all threads (per-thread patching races and leaks)
            ts = [threading.Thread(target=go, args=(k,)) for k in range(6)]; [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(results.count('ok'), 2, results); self.assertEqual(results.count('insufficient_funds'), 4, results); self.assertEqual(self.bal(u, self.CODE), 20_000_000)
        self.assertEqual(self.one("SELECT COUNT(*) FROM crypto_withdrawals")[0], 2); self.assert_gl()

if __name__ == '__main__': unittest.main()
