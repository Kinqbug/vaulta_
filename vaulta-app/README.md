# VAULTA: full-stack platform

The original static Vaulta site (public pages, design system) now sits on a real backend:
accounts, sessions, a ledger, portfolios, orders, a user dashboard, and a separate admin console.
**No external payment, market-data or execution provider is connected by default (`PROVIDER_MODE=internal`): balances are recorded on the internal ledger only and no real money moves until live providers are configured.**

## Quick start
```bash
pip install -r requirements.txt      # Flask only; everything else is Python stdlib
cp .env.example .env                 # optional in development
python -m server.seed                # creates instance/vaulta.db + instance/dev-credentials.txt (random passwords, chmod 600)
python run.py                        # http://127.0.0.1:5000
python -m unittest discover -s tests # 50 backend tests
```
Open `/` (public site), `/signup/`, `/login/`, `/app/` (user dashboard), `/admin/` (staff only; staff logins redirect there).
Credentials for the seeded demo customer and staff accounts are in `instance/dev-credentials.txt`. They are generated per install, never hardcoded, and the file is git-ignored.
To set your own: `ADMIN_EMAIL=you@x.com ADMIN_PASSWORD='...' python -m server.seed` (only on an empty database).

## Layout
```
web/                 existing public site (unchanged visuals) + new pages
  login, signup, forgot-password, reset-password, verify-email   now call the real API
  invest/            marketplace now reads products from /api/products, orders via /api/investments
  app/  admin/       dashboard SPAs   js/app.js, js/admin.js, js/ui.js (shared kit), css/dash.css
server/
  app.py             factory, security headers, CSRF, error handling, static serving
  routes_*.py        HTTP layer (auth, user, admin, public)
  finance.py         deposits, withdrawals, investments, savings, retirement, orders
  ledger.py          append-only ledger, balances derived from it, idempotency, state machine
  portfolio.py       server-side valuation, allocation, history
  security.py        password hashing, sessions, TOTP 2FA, rate limits, RBAC decorators
  rbac.py audit.py notify.py providers.py seed.py migrate.py accounting.py assets.py
  migrations/   (001 baseline, 002 general ledger, 003 asset/network registry)
tests/               unittest suite
docs/ARCHITECTURE.md design notes, schema, API, security, limitations
```

## Crypto funding (BTC, ETH, USDT, USDC, SOL)

**Funding is cryptocurrency-only.** Crypto *is* the Deposit & withdraw system, not a separate add-on: customers use the normal **Deposit & withdraw** page, and staff manage it from
the normal admin **Deposits** and **Withdrawals** pages. Wallet configuration, sending-address verification and reconciliation live under **Admin → Settings → Crypto**.
The legacy bank/card endpoints are switched off by the `fiat_funding_enabled` system setting (default `0`, returns `crypto_only`); an admin can set it to `1` to bring them back.

**Screenshot deposits and asset logos.** Customers deposit by sending crypto to the configured address and submitting a screenshot (plus TXID/amount) on the Deposit & withdraw page; administrators review it in **Admin → Deposits** (large preview, chain verification, notes, approve/reject)
and a deposit is credited exactly once, only after verification and approval. Administrators manage cryptocurrencies, logos, networks, limits and fees in **Admin → Assets → Cryptocurrencies (funding)**. See `docs/CRYPTO.md` §12-16.

Customers can fund their account with crypto sent to **company-controlled Vaulta deposit addresses**. Deposits are independently verified on-chain, reliably
attributed to one customer, risk-checked, and posted to the **double-entry ledger**. Full design, controls and limits: [`docs/CRYPTO.md`](docs/CRYPTO.md).

```bash
python -m server.seed                         # dev data + crypto registry (BTC, ETH, USDT/Ethereum, USDC/Ethereum, SOL) with NOT_CONFIGURED wallets
python -m server.crypto_setup --dev-demo-wallets   # DEVELOPMENT ONLY: attach clearly-labelled MOCK wallets (no keys exist for them) to try the flow
python run.py                                 # customer: /app/#/deposit   admin: /admin/#/deposits, /admin/#/withdrawals, /admin/#/settings-crypto
python -m server.verify                       # crypto system verification (exit code 0 only if every check passes)
python -m unittest discover -s tests          # includes tests/test_crypto.py and tests/test_crypto_api.py
```

* **Environments.** `APP_ENV=development` uses the in-memory mock chain and mock wallet provider (no real funds). `staging` requires testnet providers.
  `production` requires live providers. The app refuses to start on any other pairing, and `python -m server.verify` reports it.
* **Wallet addresses are public data.** Supply real ones via `BTC_WALLET_ADDRESS` etc. (applied only to a placeholder at first setup) or the admin console
  (**Admin → Settings → Crypto → Company wallets**). Changing an address needs two administrators, each with a fresh 2FA code, and is fully audited.
  **Never** put private keys, seed phrases or signing credentials in Vaulta: signing happens in an external custody system behind `WalletProvider`.
* **Real chain connectivity is not included.** `RpcBlockchainProvider` and the staging/production wallet providers are deliberately unimplemented adapters that
  refuse to operate until you implement them against your node/indexer and custody vendor (see `docs/CRYPTO.md` §Integration).
* **Real customer assets stay disabled** until the legal, licensing, custody, compliance, security and operational requirements for your jurisdiction and business model
  are satisfied. In `live` mode funding is blocked unless `REAL_FUNDS_ENABLED=1`.

**Wallets, Asset Settings and Back navigation:** see [`docs/WALLET_CONSOLE.md`](docs/WALLET_CONSOLE.md) (admin **Crypto Operations → Wallets & Reconciliation**, dedicated `#/asset-settings/<id>` page, shared top-left Back button in both dashboards).

## Environment variables
See `.env.example`. Key ones: `AUTH_SECRET` (required in production, 32+ chars), `DATABASE_PATH`, `UPLOAD_DIR` (KYC files, outside the web root), `PROVIDER_MODE` (`internal`|`live`), provider keys (server-side only).

## Going to production: read this first
This is a solid foundation, **not a launch-ready regulated product**. Before real users or money:
1. Implement the `live` provider adapters in `server/providers.py` (payments, market data, execution, KYC, email). Unconfigured live providers refuse with 503 and roll back; they never fake success.
2. Move SQLite to PostgreSQL (schema is relational and portable; swap `db.py`, add migrations such as Alembic).
3. Run behind HTTPS with `VAULTA_ENV=production` (Secure cookies + HSTS), a real WSGI server (gunicorn), and Redis for rate limiting (current limiter is per-process memory).
4. Real email delivery (verification and reset links currently go to an internal outbox; `/api/dev/last-email` exposes it in development only).
5. Independent security review, penetration test, and legal/compliance review (licensing, KYC/AML, custody, tax reporting). None of that is done here.
6. Encrypt KYC documents at rest / use object storage; add malware scanning to uploads; add backups.
7. Hash 2FA secrets at rest with a KMS-held key (currently stored in the DB as base32).

## Email communication & automation
Transactional email, an admin Email Center (compose, templates, automations, campaigns, outbox, delivery logs, suppression, settings), consent/unsubscribe, reminders and
provider webhooks. **Read [docs/EMAIL.md](docs/EMAIL.md)** (architecture, statuses, env vars, Resend setup, limitations, production checklist).
Development captures mail locally and sends nothing; production requires `EMAIL_PROVIDER=resend|ses`, a verified sender, an https `PUBLIC_BASE_URL`, and a running worker: `python -m server.mailer.worker`.

## Legal documents
The six legal documents, versioning, acceptance records, risk-disclosure gate and cookie consent are described in [docs/LEGAL.md](docs/LEGAL.md) (including the go-live checklist and open items for counsel).

## Trade terminal and community chat
Market explorer, chart, order panel and moderated community chat live in the Trade page. See [docs/TRADE_TERMINAL.md](docs/TRADE_TERMINAL.md) for setup, what is simulated, and limitations.


## Production finishing (SMTP, support chat, safety tooling)
* `docs/PRODUCTION.md`: environments, first-admin bootstrap (`python -m server.bootstrap_admin`), readiness check (`python -m server.prodcheck`), demo-data audit, backup/rollback and the **honest list of blockers**.
* `docs/EMAIL.md` (SMTP section): admin-managed SMTP with encrypted password; `docs/SUPPORT.md`: live support chat.
* `python -m server.seed` now refuses to run outside `APP_ENV=development`.
