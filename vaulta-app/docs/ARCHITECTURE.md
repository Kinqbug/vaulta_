# Architecture

```
Browser (static pages + 2 SPAs)  ->  Flask API  ->  services (finance, ledger, portfolio)  ->  SQLite  ->  provider adapters (internal/live)
                                       |-- security: sessions, CSRF, RBAC, rate limits      \-> audit log, notifications
```
**Existing frontend analysis.** Multi-page static HTML/CSS/vanilla JS (no build step). Design tokens in `css/style.css`/`site.css` (black/white/green, Manrope, pill buttons, 24-28px cards). The old "backend" was `js/invest-data.js`: localStorage balances, hardcoded products, instant fake order success. That file now fetches everything from the API; the marketplace UI (`invest.js`) was kept and re-pointed.

**Stack choice.** The upload was static HTML and the build environment had no package registry access, so the backend is Flask + SQLite with raw SQL in a service layer (stdlib for hashing, TOTP, HMAC). Services take a `db` handle and are database-agnostic in shape; porting to PostgreSQL means replacing `db.py` and the SQL dialect details (`INSERT OR IGNORE`, triggers, `BEGIN IMMEDIATE`).

## Money and integrity
- Money is **integer cents**; prices/quantities are decimal strings parsed with `Decimal`. JSON floats are rejected.
- **Balances are derived** from completed ledger rows (`ledger.balance`); nothing caches a balance that could drift. Available = posted - pending debits - open-order reserves.
- Ledger rows are immutable (DB triggers block UPDATE of financial columns and all DELETE). Only `status` moves, via a state machine (`pending -> processing -> completed -> reversed`, etc.). Corrections are new `adjustment` rows with an audit entry.
- Write paths run in `BEGIN IMMEDIATE` transactions (serialised) and money endpoints accept `Idempotency-Key` (same key+body replays the original response; same key+different body is rejected).
- Deposits credit only when the provider confirms (`handle_payment_event`; duplicate events are no-ops). Withdrawals hold funds immediately; large ones go to `held_for_review`; admins step through approve -> process -> complete; reject releases the hold.
- Investments and market orders run the provider call **inside** the DB transaction, so a provider failure rolls everything back.
- Limit/stop orders stay `open` and fill only when a price update crosses the condition (`set_price` / `match_open_orders`). Buy orders reserve cash; sells check holdings net of open sells.

## Schema (versioned SQL migrations in `server/migrations/`, applied by `server/migrate.py`)
Identity: users, user_profiles, sessions, security_events, email_tokens, email_outbox. RBAC: roles, permissions, role_permissions, user_roles. Accounts: accounts (cash/savings/retirement). Markets: assets, asset_prices, investment_products. Portfolio: portfolios, portfolio_holdings, holding_movements, portfolio_snapshots, watchlist_items. Ledger/flows: transactions, transaction_fees, idempotency_keys, deposits, withdrawals, investments, orders, order_executions. Goals: savings_goals, automatic_contributions, retirement_plans. Compliance: kyc_profiles, kyc_documents, kyc_reviews. Engagement: notifications, notification_preferences, support_tickets, support_messages, articles. Governance: audit_logs (append-only, hash-chained), system_settings.

## API (JSON; session cookie + `X-CSRF-Token` on writes)
- **Auth** `/api/auth/*`: register, login, logout, me, verify-email, resend-verification, request-password-reset, reset-password, change-password, 2fa/{setup,enable,disable}, sessions, security-events.
- **User**: dashboard, portfolio, portfolio/history?range=1D..ALL, profile, products, assets, watchlist, investments, orders(+cancel), deposits, withdrawals(+cancel), accounts, transactions, savings(+transfer), goals(+contribute), retirement(+project), notifications(+preferences), support/tickets, kyc(+documents, submit), documents (statements, receipts).
- **Admin** `/api/admin/*` (each route guarded by `require_perm`): overview, users, kyc, accounts, investments, assets(+price), orders, transactions(+adjustments), deposits, withdrawals(+action), savings, retirement, reports, support, notifications(+broadcast), content, audit-logs(+verify), roles(+assign), settings, jobs/{interest,contributions,snapshots,market-tick,match-orders}.

## Security controls implemented (and tested where noted)
scrypt password hashing; password policy; opaque server-side sessions (SHA-256 of token stored; HttpOnly, SameSite=Lax, Secure in production); session rotation on login; idle + absolute expiry; revoke-all on password change/reset/2FA enable/suspension/role change; CSRF token on all authenticated writes (tested); login throttling + account lockout (tested) and per-IP rate limits; non-enumerating login/reset responses; TOTP 2FA with replay protection (tested) enforced for withdrawals and larger investments/orders; RBAC with 23 permissions across 7 roles, enforced server-side on every admin route (tested against all admin routes); separation of duties (no self-approval/self-role-change, last super-admin protected); reason required on sensitive admin actions; append-only hash-chained audit log with verify endpoint (tamper detection tested); CSP/HSTS/frame/nosniff headers; parameterised SQL everywhere; HTML-escaped rendering in the SPAs; KYC uploads validated by magic bytes, random names, stored outside web root, downloads audited; CSV export neutralises formula injection; static server blocks dotfiles/traversal.

## Roles
super_admin (all), operations_admin (txns/deposits/withdrawals/orders), compliance_admin (KYC), investment_admin (products/assets/orders), finance_admin (reports, adjustments), support_admin (users/tickets), content_admin (articles/broadcasts). Any staff role can open the admin overview; sections follow permissions.

## Known limitations
- Internal providers only; no real payment, market data, execution, KYC vendor, or email provider is connected.
- Orders fill whole (no partial fills yet; the status exists). No margin/short selling. Single currency (USD).
- Retirement account type exists but has no contribution/ledger flow beyond the planner. Tax documents are not generated.
- Statements/receipts are simple HTML/CSV, not PDFs. Content is plain-text bodies.
- In-memory rate limiter and SQLite are single-node; see README production checklist.
- Public marketing pages (home, save, trade, retirement) still show their original illustrative demo content; only login/signup/forgot/reset/verify and the Invest page are wired to the backend. The nav "Log In" button is unchanged.
- UI was smoke-tested in headless Chromium (desktop + 390px mobile) across every dashboard/admin page and one full signup -> deposit -> invest flow; it has not had a manual accessibility or cross-browser review.

## General ledger and asset registry (migrations 002-003)
* `transactions` remains the **customer subledger** (cents, USD). Every `completed`/`reversed` subledger row is mirrored into a double-entry GL (`journal_entries` / `journal_lines`) in the **same DB transaction** (`accounting.mirror_transaction`). `accounting.reconcile_subledger()` compares GL customer liabilities to subledger posted balances; mismatches are reported, never auto-fixed.
* GL amounts are integer minor units of the line's `asset_code`, stored as TEXT (18-decimal tokens exceed 64-bit). Entries must balance **per asset**. Posted entries and lines are immutable (DB triggers); corrections are reversal entries; closed periods refuse postings.
* `ledger_assets` (USD seeded) defines what an integer means; `decimals` is immutable. `asset_networks` carries the network-specific representation (e.g. `TOK.SIMETH` vs `TOK.SIMBSC`) with its own decimals/contract. Everything is created **disabled**; enabling is an audited operator action (`crypto.registry.manage`).
* Go-live gate: `PROVIDER_MODE=live` additionally requires `REAL_FUNDS_ENABLED=1` before deposits, withdrawals, investments or orders are accepted. Production refuses to start unless a PostgreSQL URL, live providers and all provider secrets are configured.
