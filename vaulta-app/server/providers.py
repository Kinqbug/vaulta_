"""Provider abstraction layer. Business logic only ever talks to these interfaces.
INTERNAL implementations run against the internal ledger when no external provider is connected; rows are flagged non_live in the database.
LIVE implementations are deliberately unimplemented stubs: they raise ProviderNotConfigured
rather than pretend money moved."""
import os, secrets, hashlib, random
from decimal import Decimal
from . import util
from .util import ApiError

class ProviderNotConfigured(ApiError):
    def __init__(self, name): super().__init__(503, 'provider_not_configured', f'{name} is not configured. No transaction was created.')

# ---------- Payments ----------
class PaymentProvider:
    name = 'abstract'; non_live = True
    def create_deposit(self, user_id, amount_cents, method): raise NotImplementedError
    def create_payout(self, user_id, amount_cents, destination): raise NotImplementedError

class InternalPaymentProvider(PaymentProvider):
    name = 'internal-pay'; non_live = True
    def create_deposit(self, user_id, amount_cents, method):
        return {'external_ref': 'int_pay_' + secrets.token_hex(6), 'status': 'processing'}   # settles later via signed event
    def create_payout(self, user_id, amount_cents, destination):
        return {'external_ref': 'int_out_' + secrets.token_hex(6), 'status': 'processing'}
    def internal_event(self, external_ref, outcome='succeeded'):
        """What a real provider would POST to our webhook."""
        return {'type': 'deposit.' + outcome, 'external_ref': external_ref}

class LivePaymentProvider(PaymentProvider):
    name = 'live'; non_live = False
    def __init__(self, key): self.key = key
    def create_deposit(self, *a, **k): raise ProviderNotConfigured('Payment provider')
    def create_payout(self, *a, **k): raise ProviderNotConfigured('Payment provider')

# ---------- Market data ----------
class MarketDataProvider:
    name = 'abstract'
    def next_price(self, asset): raise NotImplementedError

class InternalMarketData(MarketDataProvider):
    name = 'internal-market'
    def next_price(self, asset, rnd=None):
        rnd = rnd or random
        vol = {'Crypto': .03, 'Stocks': .012, 'ETFs': .008, 'Commodities': .01, 'Real Estate': .004, 'Bonds': .002}.get(asset['asset_class'], .01)
        p = Decimal(asset['last_price']) * (Decimal(1) + Decimal(str(rnd.gauss(0.0002, vol))))
        return max(p, Decimal('0.01')).quantize(Decimal('0.0001'))

class LiveMarketData(MarketDataProvider):
    name = 'live'
    def __init__(self, key): self.key = key
    def next_price(self, asset): raise ProviderNotConfigured('Market data provider')

# ---------- Trading / execution ----------
class TradingProvider:
    name = 'abstract'; non_live = True
    def execute(self, order, price): raise NotImplementedError

class InternalTradingProvider(TradingProvider):
    name = 'internal-exec'; non_live = True
    def execute(self, order, price):
        return {'status': 'filled', 'price': price, 'provider_ref': 'int_fill_' + secrets.token_hex(5)}

class LiveTradingProvider(TradingProvider):
    name = 'live'; non_live = False
    def execute(self, *a, **k): raise ProviderNotConfigured('Trading/execution provider')

# ---------- KYC ----------
class KYCProvider:
    name = 'manual'
    def submit(self, user_id, payload): return {'status': 'under_review', 'provider': 'manual-review'}   # staff review in the admin dashboard

# ---------- Email ----------
# The previous EmailProvider only inserted a row into email_outbox and printed to the terminal: it never delivered anything.
# Delivery now lives in server/mailer (transports + outbox worker). `EmailProvider` remains as the name of the development transport.
from .mailer.transports import DevTransport as EmailProvider, build_transport as build_email_transport

# ---------- Storage (private, never under the web root) ----------
ALLOWED = {b'\xff\xd8\xff': ('image/jpeg', 'jpg'), b'\x89PNG\r\n\x1a\n': ('image/png', 'png'), b'%PDF-': ('application/pdf', 'pdf')}
class LocalPrivateStorage:
    def __init__(self, root): self.root = root; os.makedirs(root, mode=0o700, exist_ok=True)
    def save(self, data: bytes):
        if len(data) > 5 * 1024 * 1024: raise ApiError(413, 'file_too_large', 'Files must be 5 MB or smaller.')
        for magic, (mime, ext) in ALLOWED.items():          # verify real content, not the filename
            if data.startswith(magic):
                name = secrets.token_hex(16) + '.' + ext
                with open(os.path.join(self.root, name), 'wb') as f: f.write(data)
                return name, mime, hashlib.sha256(data).hexdigest()
        raise ApiError(415, 'unsupported_file', 'Upload a JPG, PNG or PDF.')
    def path(self, name): return os.path.join(self.root, os.path.basename(name))

# ---------- Compliance screening (sender / destination addresses) ----------
class ComplianceProvider:
    name = 'internal-screen'
    def screen(self, db, address_format, address_key):
        """-> (clear: bool, reason). The internal screen only checks Vaulta's own blocklist; it is NOT a sanctions-screening service."""
        r = db.execute('SELECT reason FROM crypto_address_blocklist WHERE address_format=? AND address_key=?', (address_format, address_key)).fetchone()
        return (False, 'blocklisted: ' + r['reason']) if r else (True, '')
class LiveComplianceProvider(ComplianceProvider):
    name = 'live-screen'
    def screen(self, db, *a, **k): raise ProviderNotConfigured('Address screening provider')

class Providers:
    def __init__(self, cfg):
        from . import chain_providers
        live = cfg['PROVIDER_MODE'] == 'live'
        self.crypto_mode = cfg.get('CRYPTO_PROVIDER_MODE', 'mock'); self.env = cfg.get('ENV', 'development')
        self.chain, self.wallets = chain_providers.build(self.crypto_mode)
        self.compliance = LiveComplianceProvider() if self.crypto_mode == 'live' else ComplianceProvider()
        self.crypto_real_funds = bool(cfg.get('REAL_FUNDS_ENABLED', False)) and self.crypto_mode == 'live'
        self.mode = 'live' if live else 'internal'
        self.real_funds_enabled = bool(cfg.get('REAL_FUNDS_ENABLED', False)) and live   # simulated mode can never hold real funds
        self.payments = LivePaymentProvider(cfg['PAYMENT_PROVIDER_KEY']) if live else InternalPaymentProvider()
        self.market = LiveMarketData(cfg['MARKET_DATA_API_KEY']) if live else InternalMarketData()
        self.trading = LiveTradingProvider() if live else InternalTradingProvider()
        self.kyc = KYCProvider(); self.email = build_email_transport(cfg)
        self.storage = LocalPrivateStorage(cfg['UPLOAD_DIR'])
