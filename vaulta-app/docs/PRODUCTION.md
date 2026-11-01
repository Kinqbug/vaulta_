# Production deployment, readiness and recovery

> **Status: not ready for production.** See "Known blockers". The application is ready for **staging verification** only.

## 1. Environments
| | development | staging | production |
|---|---|---|---|
| `APP_ENV` | `development` | `staging` | `production` |
| Demo seed (`python -m server.seed`) | allowed | **refused** | **refused** |
| Dev simulator routes (`/api/dev/*`) | registered (mock chain only) | 404 | 404 |
| `/api/health` | detailed | `{"ok": true}` only | `{"ok": true}` only |
| Email transport | `dev` (captured, never sent) | provider + sandbox allow-list | `smtp` / `resend` / `ses` |
| Chain provider | mock | testnet | live (**not implemented**) |

The app **refuses to start** in production if the database contains accounts flagged `is_demo=1`, if test networks or mock wallets are enabled, or if configuration is unsafe (`Config.validate`).

## 2. First deployment
1. Install: `pip install -r requirements.txt` (adds `cryptography`).
2. Copy `.env.example` to your secret manager; set `APP_ENV`, `AUTH_SECRET`, `PUBLIC_BASE_URL` (https), `SETTINGS_ENCRYPTION_KEY`, `EMAIL_PROVIDER`, etc.
3. Back up (section 5), then apply migrations: they run automatically at start and are checksummed and immutable. `python -m server.prodcheck` lists any that are pending.
4. Create the first administrator: `python -m server.bootstrap_admin --email you@company.com` (hidden password prompt, policy enforced, refuses if a super admin exists, writes an audit record, creates **no** customers, balances or assets).
5. Sign in, verify the email address, **enable two-factor authentication**.
6. Admin > Email Center > **Email & SMTP**: enter host/port/security/credentials/sender, save, **Test SMTP connection**, then **Send test email** to yourself.
7. Run the worker as a separate supervised process: `python -m server.mailer.worker` (systemd/Procfile). Production does not use the inline dev worker.
8. Run `python -m server.prodcheck` until it reports **0 blockers**. Exit code 1 means blockers.

## 3. Existing database that contains demo data
```
python -m server.prodcheck --demo-report                      # dry run: counts + ids, changes nothing
python -m server.prodcheck --disable-demo-accounts --backup-dir /secure/backups --confirm
```
The second command takes a verified SQLite backup first, then **closes** (never deletes) accounts flagged `is_demo=1` and revokes their sessions; it is audited. Ledger entries, audit logs and customer records are never deleted by any tool here. Simulated rows (`non_live=1`) and internal-priced assets are reported as blockers for a human decision: remove them through a reviewed migration after an accountant has classified them.

## 4. Credentials
| Credential | Purpose | Variable / field | Production |
|---|---|---|---|
| App signing secret | sessions, unsubscribe tokens | `AUTH_SECRET` | required |
| Settings encryption key | encrypts the SMTP password | `SETTINGS_ENCRYPTION_KEY` | required for `smtp` |
| SMTP host + credentials | transactional email | Admin > Email & SMTP (stored encrypted) | required if `smtp` |
| Resend API key / webhook secret | alternative email provider, delivery events | `EMAIL_PROVIDER_KEY`, `EMAIL_WEBHOOK_SECRET` | if `resend` |
| AWS keys + region | SES | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `EMAIL_SES_REGION` | if `ses` |
| CoinGecko key | market data | `COINGECKO_API_KEY`, `COINGECKO_PLAN` | recommended |
| Sender-domain DNS (SPF, DKIM, DMARC) | inbox placement | at your DNS/mail provider | required |
| Blockchain RPC/indexer, custody/signing | live chain verification | **not implemented** | blocker for automated deposits |

## 5. Backup and rollback
- Backup (SQLite): `sqlite3 instance/vaulta.db ".backup 'backup-$(date +%F).db'"` then `sqlite3 backup.db "PRAGMA integrity_check"`; copy off-host together with the private upload directory.
- Before every release: backup, deploy, `python -m server.prodcheck`.
- Rollback: stop app and worker, restore the pre-release database **and** the previous code (migrations are forward-only and immutable, so code and schema must roll back together), start, verify `/api/health`.
- Restore drill: restore into a scratch path monthly and run `python -m server.prodcheck` against it.

## 6. Known blockers (honest status)
1. **PostgreSQL is not implemented.** `Config.validate` demands `DATABASE_URL=postgresql://...` in production, but the data layer is SQLite-only (SQLite-specific SQL, `BEGIN IMMEDIATE`, `INSERT OR IGNORE`). Production start is therefore refused until a PostgreSQL layer exists, or the owner explicitly decides to run a controlled pilot on SQLite and changes that rule knowingly.
2. **No live blockchain connectivity or custody/signing adapter.** `CRYPTO_PROVIDER_MODE=live` is a stub; deposits are verified manually from evidence plus the admin's own explorer check, and withdrawals are completed manually with a recorded transaction hash.
3. **No live payment/KYC/trading execution provider.** Fiat funding and simulated trading stay behind `PROVIDER_MODE`; `REAL_FUNDS_ENABLED` must stay `0`.
4. Real-money operation needs licensing, custody, compliance and an independent security review: none of that is done here.
5. SMTP only proves server acceptance; delivery confirmation needs Resend webhooks or an equivalent provider event feed.
