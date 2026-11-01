"""Configuration. All secrets come from environment variables (see .env.example)."""
import os, re, secrets

def _load_dotenv(path):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_load_dotenv(os.path.join(ROOT, '.env'))

ENVS = ('development', 'staging', 'production')
_ENV = os.getenv('APP_ENV') or os.getenv('VAULTA_ENV') or 'development'
_DEFAULT_CRYPTO = {'development': 'mock', 'staging': 'testnet', 'production': 'live'}

def crypto_env_problems(env, mode):
    """The ONLY place that decides which wallet/chain infrastructure may run in which environment."""
    if env not in ENVS: return [f'APP_ENV must be one of {", ".join(ENVS)} (got {env!r})']
    if mode not in ('mock', 'testnet', 'live'): return [f'CRYPTO_PROVIDER_MODE must be mock, testnet or live (got {mode!r})']
    if env == 'production' and mode != 'live': return [f'production requires CRYPTO_PROVIDER_MODE=live; the {mode} provider may never run in production']
    if env == 'staging' and mode != 'testnet': return [f'staging requires CRYPTO_PROVIDER_MODE=testnet (got {mode}); staging may not use mock or production wallet infrastructure']
    if env == 'development' and mode == 'live': return ['development may not connect to production wallet infrastructure (CRYPTO_PROVIDER_MODE=live)']
    return []

EMAIL_PROVIDERS = ('dev', 'resend', 'ses', 'smtp', 'mock')
_ADDR = re.compile(r'^[^@\s<>]{1,64}@[^@\s<>]+\.[^@\s<>]{2,}$')

def email_env_problems(c):
    """The ONLY place that decides which email transport may run in which environment."""
    out, prov, env = [], c.EMAIL_PROVIDER, c.ENV
    if prov and prov not in EMAIL_PROVIDERS: return [f'EMAIL_PROVIDER must be one of {", ".join(EMAIL_PROVIDERS)} (got {prov!r})']
    if c.EMAIL_FROM_ADDRESS and not _ADDR.match(c.EMAIL_FROM_ADDRESS): out.append('EMAIL_FROM_ADDRESS is not a valid email address')
    if c.PUBLIC_BASE_URL and not re.match(r'^https?://[^\s/]+$', c.PUBLIC_BASE_URL): out.append('PUBLIC_BASE_URL must be an origin such as https://app.example.com (no path)')
    if env == 'production':
        if prov not in ('resend', 'ses', 'smtp'): out.append('production requires EMAIL_PROVIDER=resend, ses or smtp: the dev/mock email transports never deliver and may not run in production')
        if prov == 'smtp' and not os.getenv('SETTINGS_ENCRYPTION_KEY'): out.append('SETTINGS_ENCRYPTION_KEY is required for EMAIL_PROVIDER=smtp (it encrypts the SMTP password stored via the admin UI)')
        if prov != 'smtp' and not c.EMAIL_FROM_ADDRESS: out.append('EMAIL_FROM_ADDRESS is required (a sender on a domain you have verified with the provider)')
        if prov != 'smtp' and not c.PUBLIC_BASE_URL.startswith('https://') or prov == 'smtp' and c.PUBLIC_BASE_URL and not c.PUBLIC_BASE_URL.startswith('https://'): out.append('PUBLIC_BASE_URL must be set to the https:// origin of the app (used for every link in every email)')
        if prov == 'resend':
            if not c.EMAIL_PROVIDER_KEY: out.append('EMAIL_PROVIDER_KEY (Resend API key) is required')
            if not c.EMAIL_WEBHOOK_SECRET: out.append('EMAIL_WEBHOOK_SECRET is required so delivery webhooks can be verified; without it nothing can be confirmed as delivered')
        if prov == 'ses' and not (c.AWS_ACCESS_KEY_ID and c.AWS_SECRET_ACCESS_KEY and c.EMAIL_SES_REGION): out.append('SES requires AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY and EMAIL_SES_REGION')
    else:
        if prov == 'mock' and env != 'development': out.append('the mock email transport is for development/tests only')
        if prov in ('resend', 'ses', 'smtp') and not c.EMAIL_SANDBOX_ALLOWLIST: out.append(f'{env} with a real email provider requires EMAIL_SANDBOX_ALLOWLIST (addresses or @domains that may receive mail) so test data can never reach real customers')
    return out

class Config:
    ENV = _ENV                                               # development | staging | production  (APP_ENV, or legacy VAULTA_ENV)
    CRYPTO_PROVIDER_MODE = os.getenv('CRYPTO_PROVIDER_MODE') or _DEFAULT_CRYPTO.get(_ENV, 'mock')   # mock | testnet | live
    DATABASE_PATH = os.getenv('DATABASE_PATH', os.path.join(ROOT, 'instance', 'vaulta.db'))
    AVATAR_MAX_BYTES = int(os.getenv('AVATAR_MAX_BYTES', str(2 * 1024 * 1024)))   # cropped avatar upload limit (the browser re-encodes to ~512px, so this is generous)
    UPLOAD_DIR = os.getenv('UPLOAD_DIR', os.path.join(ROOT, 'instance', 'private_uploads'))
    WEB_ROOT = os.getenv('WEB_ROOT', os.path.join(ROOT, 'web'))
    AUTH_SECRET = os.getenv('AUTH_SECRET', '')
    PROVIDER_MODE = os.getenv('PROVIDER_MODE', 'internal')   # internal | live  (internal = no external provider connected)
    PAYMENT_PROVIDER_KEY = os.getenv('PAYMENT_PROVIDER_KEY', '')
    MARKET_DATA_API_KEY = os.getenv('MARKET_DATA_API_KEY', '')
    KYC_PROVIDER_KEY = os.getenv('KYC_PROVIDER_KEY', '')
    EMAIL_PROVIDER_KEY = os.getenv('EMAIL_PROVIDER_KEY', '')   # Resend API key (server-side only)
    # ---- Email delivery (see docs/EMAIL.md). dev = capture locally, never touches the network. resend | ses = real delivery. mock = tests only. ----
    EMAIL_PROVIDER = (os.getenv('EMAIL_PROVIDER') or ('dev' if _ENV != 'production' else '')).strip().lower()
    EMAIL_FROM_ADDRESS = os.getenv('EMAIL_FROM_ADDRESS', '').strip()
    EMAIL_FROM_NAME = os.getenv('EMAIL_FROM_NAME', 'VAULTA').strip()
    EMAIL_REPLY_TO = os.getenv('EMAIL_REPLY_TO', '').strip()
    PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').strip().rstrip('/')    # the ONLY source of links placed in emails (never request headers)
    EMAIL_WEBHOOK_SECRET = os.getenv('EMAIL_WEBHOOK_SECRET', '').strip()      # Resend/Svix signing secret (whsec_...)
    EMAIL_RESEND_BASE_URL = os.getenv('EMAIL_RESEND_BASE_URL', 'https://api.resend.com').rstrip('/')
    EMAIL_SES_REGION = os.getenv('EMAIL_SES_REGION', '').strip()
    EMAIL_SES_CONFIGURATION_SET = os.getenv('EMAIL_SES_CONFIGURATION_SET', '').strip()
    EMAIL_SES_ENDPOINT = os.getenv('EMAIL_SES_ENDPOINT', '').strip()          # test/VPC override
    AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID', '').strip()
    AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY', '').strip()
    EMAIL_SANDBOX_ALLOWLIST = os.getenv('EMAIL_SANDBOX_ALLOWLIST', '').strip()   # outside production a real provider may ONLY mail these addresses / @domains
    EMAIL_LINK_ALLOWED_HOSTS = os.getenv('EMAIL_LINK_ALLOWED_HOSTS', '').strip() # extra hosts administrators may link to in campaigns
    EMAIL_DEFAULT_TIMEZONE = os.getenv('EMAIL_DEFAULT_TIMEZONE', 'UTC').strip()
    EMAIL_INLINE_WORKER = os.getenv('EMAIL_INLINE_WORKER', '1' if _ENV == 'development' else '0') == '1'   # honoured by run.py only (dev convenience). create_app never starts threads; production runs `python -m server.mailer.worker`
    STORAGE_KEY = os.getenv('STORAGE_KEY', '')
    COOKIE_SECURE = os.getenv('COOKIE_SECURE', '1' if _ENV == 'production' else '0') == '1'
    SESSION_IDLE_MINUTES = int(os.getenv('SESSION_IDLE_MINUTES', '60'))
    SESSION_ABSOLUTE_HOURS = int(os.getenv('SESSION_ABSOLUTE_HOURS', '12'))
    MAX_FAILED_LOGINS = int(os.getenv('MAX_FAILED_LOGINS', '5'))
    LOCKOUT_MINUTES = int(os.getenv('LOCKOUT_MINUTES', '15'))
    DATABASE_URL = os.getenv('DATABASE_URL', '')              # postgresql://... (required in production; SQLite is dev/test only)
    REAL_FUNDS_ENABLED = os.getenv('REAL_FUNDS_ENABLED', '0') == '1'   # go-live gate: stays 0 until licence/custody/AML approvals are effective
    RATE_LIMIT_ENABLED = os.getenv('RATE_LIMIT_ENABLED', '1') == '1'
    MAX_CONTENT_LENGTH = 6 * 1024 * 1024
    TESTING = False

    @classmethod
    def validate(cls):
        problems = crypto_env_problems(cls.ENV, cls.CRYPTO_PROVIDER_MODE)
        if os.getenv('APP_ENV') and os.getenv('VAULTA_ENV') and os.getenv('APP_ENV') != os.getenv('VAULTA_ENV'): problems.append('APP_ENV and VAULTA_ENV disagree; set only one')
        forbidden = sorted(k for k in os.environ if any(w in k.upper() for w in ('PRIVATE_KEY', 'SEED_PHRASE', 'MNEMONIC', 'RECOVERY_PHRASE', 'WALLET_PASSWORD', 'SIGNING_KEY')))
        if forbidden: problems.append('signing secrets must never be configured in the Vaulta process (' + ', '.join(forbidden) + ')')
        if cls.ENV == 'production':
            if len(cls.AUTH_SECRET) < 32: problems.append('AUTH_SECRET must be set (>=32 chars)')
            if cls.PROVIDER_MODE != 'live': problems.append('PROVIDER_MODE must be "live": simulated providers may never run in production')
            if not cls.DATABASE_URL.startswith(('postgresql://', 'postgres://')): problems.append('DATABASE_URL (PostgreSQL) is required: SQLite is for development/testing only')
            missing = [k for k in ('PAYMENT_PROVIDER_KEY', 'MARKET_DATA_API_KEY', 'KYC_PROVIDER_KEY') if not getattr(cls, k)]
            if missing: problems.append('missing provider secrets: ' + ', '.join(missing))
        problems += email_env_problems(cls)
        if problems: raise RuntimeError('Refusing to start' + (' in production' if cls.ENV == 'production' else '') + ': ' + '; '.join(problems))
        if cls.ENV != 'production' and not cls.AUTH_SECRET: cls.AUTH_SECRET = secrets.token_hex(32)   # ephemeral dev secret
