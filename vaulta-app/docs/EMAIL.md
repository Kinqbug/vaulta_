# VAULTA Email Communication & Automation Center

This is a working email pipeline, not a mock-up: a message is queued **inside the same database transaction** as the real state change that causes it, a
worker delivers it through a production provider, and its status is updated only from evidence (the provider's API response, then signed provider webhooks).

> **Before this feature** `server/providers.py` only inserted a row into `email_outbox` and printed to the terminal. Nothing in the application ever
> delivered an email, verification/reset links were relative paths, and one-time tokens were stored in plain text in the outbox. All of that is replaced (see "What changed").

## 1. Architecture

```
 workflow (auth / finance / crypto / support / admin)            admin Email Center, campaigns, reminders
        │  events.emit(db, "deposit.confirmed", user, ref)               │
        ▼   (same DB transaction as the state change)                    ▼
   ┌──────────────────────────  email_outbox  (status=queued, idempotency key)  ─────────────────────────┐
   └───────────────────────────────────────────────┬──────────────────────────────────────────────────────┘
                                                   ▼  worker: python -m server.mailer.worker   (claim → render → guards → send)
                         transports: dev (capture) | mock (tests) | resend | ses
                                                   ▼
        status = accepted   ──►  signed webhook (Svix/Resend) at /api/webhooks/email/resend  ──►  delivered | bounced | complained | delayed
```

| Module (`server/mailer/`) | Responsibility |
|---|---|
| `catalog.py` | the 85 templates (default content), supported variables, event labels, content validation |
| `templates_db.py` | seeding, immutable versions, drafts, publishing (separation of duties), duplication, automation rules |
| `render.py` + `templates/layout.{html,txt}` | the shared layout: HTML (tables + inline CSS) and plain text; escaping; link policy; variable validation |
| `events.py` | `emit()` and the context builders that read the verified state back from the database |
| `outbox.py` | idempotent enqueue, claim, send, retry/backoff, crash recovery, webhook status updates |
| `transports.py` | dev, mock, Resend REST, SES v2 REST with hand-written SigV4 (verified against AWS's published test vector) |
| `webhooks.py` | Svix signature verification (timestamp tolerance, constant-time compare, replay-safe) |
| `prefs.py`, `segments.py`, `campaigns.py`, `reminders.py` | consent/suppression/unsubscribe, audiences over the real schema, sends and approval, reminders |
| `routes.py` | admin API (`/api/admin/email/*`), public unsubscribe, customer preferences, webhook |
| `web/js/email-admin.js` | the admin UI (9 sections) |

Existing code reused rather than rebuilt: `email_outbox` (extended, migration 008), `notify()` (in-app notifications kept), RBAC and the hash-chained `audit()`,
`legal_config` (verified website/support email/entity/address for the footer), `system_settings`, the `VAULTA_ADMIN_EXT` admin plugin hook, `email_tokens`, `notification_preferences`.

## 2. What a status means

| Status | Evidence |
|---|---|
| `queued` / `retry` | waiting for the worker (retry: provider/network error; exponential backoff 1 min → 6 h, ±10 % jitter; max attempts configurable, default 6) |
| `sending` | claimed by a worker (5-minute lease) |
| `accepted` | **the provider's API accepted it. This is not proof of inbox delivery** |
| `delivered` | a **signature-verified provider webhook** reported delivery |
| `delayed` / `bounced` / `complained` | verified provider webhook (a *permanent* bounce also suppresses the address) |
| `failed` | permanent error, retries exhausted, render/data error, or an interrupted send whose outcome is unknown and cannot be safely repeated |
| `suppressed` | skipped at send time: unsubscribed, no marketing consent, suppressed address, closed account |
| `blocked` | sandbox guard (see §6) |
| `dev_captured` | the development transport stored it locally. **Nothing was sent** |
| `legacy_unsent` | rows from the old system. Never sent, and a database trigger makes them impossible to deliver |

SES: SNS event verification (X.509) is not implemented, so SES messages stay `accepted` and are never shown as delivered. Use Resend if you need delivery confirmation.

## 3. Mail classes and consent

| Class | Examples | Consent rule |
|---|---|---|
| `security` | new sign-in, password change, 2FA, wallet change | always sent; cannot be switched off |
| `transactional` | verification, KYC, deposits, withdrawals, trades, support | always sent |
| `service` | maintenance notice, account notices, savings/retirement | no marketing consent needed; non-promotional; some are opt-in via the customer's existing notification preferences (`pref_gated`) |
| `admin` | one-to-one staff messages | one customer at a time; never broadcast |
| `marketing` | newsletter, product/feature news, re-engagement | **opt-in only**, verified address only, unsubscribe link + RFC 8058 `List-Unsubscribe` headers |

Consent is recorded in an append-only `communication_consent_log`. The existing "promotional" toggle in notification settings is wired to it. Unsubscribe links are
HMAC-signed (no login needed). Suppression (hard bounce, complaint, manual) blocks everything for that address.

## 4. Templates (85)

Every template has HTML + plain-text output from one layout that follows the reference design (dark outer background, centred light card ≤ 600 px, compact official logo, bold
headline, stacked fields, boxed details, one black pill button, restrained footer), in strictly black/white/gray. Footer facts (entity, address, website, support email) come only
from `legal_config`; if unset they are omitted, never invented. Templates marked **none** have no automatic trigger because the workflow does not exist yet.

**Registration & verification**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `WELCOME_001` | Welcome to VAULTA | transactional | Customer registers |
| `EMAIL_VERIFY_001` | Verify email address | transactional | Verification link issued (sign-up or resend) |
| `EMAIL_VERIFY_REMINDER_001` | Reminder: verify email address | transactional | Reminder: email still unverified |
| `EMAIL_VERIFY_FINAL_001` | Final reminder: verify email address | transactional | Final reminder: email still unverified |
| `EMAIL_VERIFIED_001` | Email successfully verified | transactional | Verification token validated |
| `KYC_COMPLETE_001` | Complete identity verification | service | Reminder: identity verification not started/completed |
| `KYC_SUBMITTED_001` | Identity verification submitted | transactional | KYC submitted |
| `KYC_APPROVED_001` | Identity verification approved | transactional | KYC approved |
| `KYC_REJECTED_001` | Identity verification unsuccessful | transactional | KYC rejected |
| `KYC_INFO_REQUIRED_001` | Additional verification information required | transactional | KYC: more information requested |
| `KYC_INFO_REMINDER_001` | Reminder: verification information outstanding | transactional | Reminder: requested verification information still outstanding |

**Deposits**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `DEPOSIT_REQUESTED_001` | Deposit request created | transactional | Deposit created (fiat) |
| `DEPOSIT_PENDING_001` | Deposit pending | transactional | Crypto deposit request submitted for review |
| `DEPOSIT_DETECTED_001` | Cryptocurrency deposit detected | transactional | Incoming crypto transaction detected and attributed |
| `DEPOSIT_CONFIRMED_001` | Deposit confirmed | transactional | Deposit credited |
| `DEPOSIT_FAILED_001` | Deposit unsuccessful | transactional | Deposit failed / request not approved |
| `DEPOSIT_REVIEW_001` | Deposit under review | transactional | Crypto deposit held for review |
| `DEPOSIT_REVERSED_001` | Deposit reversed | transactional | Deposit reversed |

**Withdrawals**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `WITHDRAWAL_REQUESTED_001` | Withdrawal requested | transactional | Withdrawal requested |
| `WITHDRAWAL_REVIEW_001` | Withdrawal under review | transactional | Withdrawal held for review |
| `WITHDRAWAL_APPROVED_001` | Withdrawal approved | transactional | Withdrawal approved |
| `WITHDRAWAL_PROCESSING_001` | Withdrawal processing | transactional | Withdrawal processing / broadcast |
| `WITHDRAWAL_COMPLETED_001` | Withdrawal completed | transactional | Withdrawal completed |
| `WITHDRAWAL_FAILED_001` | Withdrawal unsuccessful | transactional | Withdrawal failed |
| `WITHDRAWAL_REJECTED_001` | Withdrawal rejected | transactional | Withdrawal rejected |
| `WITHDRAWAL_CANCELLED_001` | Withdrawal cancelled | transactional | Withdrawal cancelled by customer |

**Investments**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `INVESTMENT_RECEIVED_001` | Investment request received | transactional | Investment request recorded, not yet confirmed |
| `INVESTMENT_CONFIRMED_001` | Investment confirmed | transactional | Investment confirmed (record completed) |
| `INVESTMENT_CANCELLED_001` | Investment cancelled | transactional | **none: no such workflow exists in the backend** |
| `INVESTMENT_MATURED_001` | Investment matured | transactional | **none: no such workflow exists in the backend** |
| `INVESTMENT_STATEMENT_001` | Investment statement available | service | **none: no such workflow exists in the backend** |
| `INVESTMENT_UPDATE_001` | Investment product update | service | manual send by an administrator |

**Trading**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `TRADE_ORDER_PLACED_001` | Trade order placed | transactional | Order accepted and resting on the book |
| `TRADE_ORDER_FILLED_001` | Trade order executed | transactional | Order executed |
| `TRADE_ORDER_PARTIAL_001` | Trade partially filled | transactional | **none: no such workflow exists in the backend** |
| `TRADE_ORDER_CANCELLED_001` | Trade order cancelled | transactional | Order cancelled |
| `TRADE_ORDER_REJECTED_001` | Trade order rejected | transactional | Order could not be executed |
| `TRADE_ORDER_EXPIRED_001` | Trade order expired | transactional | **none: no such workflow exists in the backend** |
| `TRADE_PRICE_ALERT_001` | Price alert | service | **none: no such workflow exists in the backend** |

**Savings & retirement**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `SAVINGS_ACCOUNT_001` | Savings account created | service | **none: no such workflow exists in the backend** |
| `SAVINGS_CONTRIBUTION_001` | Savings contribution recorded | service | Savings contribution recorded |
| `SAVINGS_GOAL_001` | Savings goal created | service | Savings goal created |
| `SAVINGS_MILESTONE_001` | Savings milestone reached | service | Savings goal milestone crossed |
| `SAVINGS_CONTRIBUTION_FAILED_001` | Savings contribution unsuccessful | service | Scheduled contribution skipped |
| `SAVINGS_GOAL_ACHIEVED_001` | Savings goal achieved | service | Savings goal reached |
| `RETIREMENT_UPDATED_001` | Retirement plan updated | service | Retirement plan saved |
| `RETIREMENT_REVIEW_001` | Retirement plan review reminder | service | Retirement plan not reviewed for a year |

**Security**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `SECURITY_NEW_LOGIN_001` | New login detected | security | Sign-in from a new IP address |
| `SECURITY_NEW_DEVICE_001` | New device detected | security | Sign-in from a new browser/device |
| `SECURITY_PASSWORD_CHANGED_001` | Password changed | security | Password changed or reset |
| `SECURITY_PASSWORD_RESET_001` | Password reset requested | security | Password reset link issued |
| `SECURITY_2FA_ENABLED_001` | Two-factor authentication enabled | security | Two-factor authentication enabled |
| `SECURITY_2FA_DISABLED_001` | Two-factor authentication disabled | security | Two-factor authentication disabled |
| `SECURITY_WALLET_CHANGED_001` | Wallet configuration changed | security | Customer source wallet address registered/verified/removed |
| `SECURITY_WITHDRAWAL_ALERT_001` | Withdrawal security alert | security | Withdrawal to a new destination or held for review |
| `SECURITY_ACCOUNT_RESTRICTED_001` | Account restriction notice | security | Account restricted or suspended by staff |
| `SECURITY_SUSPICIOUS_001` | Suspicious activity alert | security | Account locked after repeated failed sign-ins |

**Account administration**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `ACCOUNT_PROFILE_UPDATED_001` | Profile updated | service | Profile updated |
| `ACCOUNT_EMAIL_CHANGED_001` | Email address changed | security | **none: no such workflow exists in the backend** |
| `ACCOUNT_PHONE_CHANGED_001` | Phone number changed | security | Phone number changed |
| `ACCOUNT_ACCESS_RESTORED_001` | Account access restored | service | Account reactivated by staff |
| `ACCOUNT_TERMS_UPDATED_001` | Terms updated | service | manual send by an administrator |
| `ACCOUNT_PRIVACY_UPDATED_001` | Privacy notice updated | service | manual send by an administrator |
| `ACCOUNT_STATEMENT_001` | Account statement available | service | **none: no such workflow exists in the backend** |

**Support**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `SUPPORT_TICKET_CREATED_001` | Support request received | transactional | Support ticket created |
| `SUPPORT_REPLY_001` | Support team replied | transactional | Staff reply recorded |
| `SUPPORT_INFO_REQUIRED_001` | Additional information required | transactional | Ticket moved to waiting for customer |
| `SUPPORT_RESOLVED_001` | Support request resolved | transactional | Ticket resolved |
| `SUPPORT_CLOSED_001` | Support request closed | transactional | Ticket closed |

**Announcements & marketing**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `ANNOUNCEMENT_GENERAL_001` | General announcement | service | manual send by an administrator |
| `ANNOUNCEMENT_MAINTENANCE_001` | Scheduled maintenance | service | manual send by an administrator |
| `ANNOUNCEMENT_FEATURE_001` | New feature announcement | marketing | manual send by an administrator |
| `ANNOUNCEMENT_INTERRUPTION_001` | Service interruption | service | manual send by an administrator |
| `NEWSLETTER_001` | Newsletter | marketing | manual send by an administrator |
| `EDUCATION_001` | Educational content | marketing | manual send by an administrator |
| `ANNOUNCEMENT_PRODUCT_001` | Product announcement | marketing | manual send by an administrator |
| `REENGAGE_001` | Customer re-engagement | marketing | Re-engagement (marketing consent required) |

**Administrative communication**

| ID | Name | Class | Trigger |
|---|---|---|---|
| `ADMIN_CUSTOM_001` | Custom email | admin | manual send by an administrator |
| `ADMIN_ACCOUNT_REVIEW_001` | Account review notice | admin | manual send by an administrator |
| `ADMIN_DOCUMENT_REQUEST_001` | Document request | admin | manual send by an administrator |
| `ADMIN_TRANSACTION_REVIEW_001` | Transaction review notice | admin | manual send by an administrator |
| `ADMIN_WITHDRAWAL_CLARIFY_001` | Withdrawal clarification | admin | manual send by an administrator |
| `ADMIN_SECURITY_NOTICE_001` | Security notice | admin | manual send by an administrator |
| `ADMIN_SUPPORT_RESPONSE_001` | Customer-service response | admin | manual send by an administrator |
| `ADMIN_SERVICE_UPDATE_001` | Individual service update | admin | manual send by an administrator |

## 5. Automation triggers

Each fires from the real transition, inside its transaction, with an idempotency key `<rule>:<entity ref>[:qualifier]`.

| Event | When it fires | Template |
|---|---|---|
| `user.registered` | Customer registers | `WELCOME_001` |
| `auth.verification_requested` | Verification link issued (sign-up or resend) | `EMAIL_VERIFY_001` |
| `auth.email_verified` | Verification token validated | `EMAIL_VERIFIED_001` |
| `reminder.verify_email` | Reminder: email still unverified | `EMAIL_VERIFY_REMINDER_001` |
| `reminder.verify_email_final` | Final reminder: email still unverified | `EMAIL_VERIFY_FINAL_001` |
| `reminder.kyc_incomplete` | Reminder: identity verification not started/completed | `KYC_COMPLETE_001` |
| `reminder.kyc_info_outstanding` | Reminder: requested verification information still outstanding | `KYC_INFO_REMINDER_001` |
| `reminder.reengagement` | Re-engagement (marketing consent required) | `REENGAGE_001` |
| `reminder.retirement_review` | Retirement plan not reviewed for a year | `RETIREMENT_REVIEW_001` |
| `kyc.submitted` | KYC submitted | `KYC_SUBMITTED_001` |
| `kyc.approved` | KYC approved | `KYC_APPROVED_001` |
| `kyc.rejected` | KYC rejected | `KYC_REJECTED_001` |
| `kyc.info_required` | KYC: more information requested | `KYC_INFO_REQUIRED_001` |
| `deposit.requested` | Deposit created (fiat) | `DEPOSIT_REQUESTED_001` |
| `deposit.pending` | Crypto deposit request submitted for review | `DEPOSIT_PENDING_001` |
| `deposit.detected` | Incoming crypto transaction detected and attributed | `DEPOSIT_DETECTED_001` |
| `deposit.confirmed` | Deposit credited | `DEPOSIT_CONFIRMED_001` |
| `deposit.failed` | Deposit failed / request not approved | `DEPOSIT_FAILED_001` |
| `deposit.review` | Crypto deposit held for review | `DEPOSIT_REVIEW_001` |
| `deposit.reversed` | Deposit reversed | `DEPOSIT_REVERSED_001` |
| `withdrawal.requested` | Withdrawal requested | `WITHDRAWAL_REQUESTED_001` |
| `withdrawal.review` | Withdrawal held for review | `WITHDRAWAL_REVIEW_001` |
| `withdrawal.approved` | Withdrawal approved | `WITHDRAWAL_APPROVED_001` |
| `withdrawal.processing` | Withdrawal processing / broadcast | `WITHDRAWAL_PROCESSING_001` |
| `withdrawal.completed` | Withdrawal completed | `WITHDRAWAL_COMPLETED_001` |
| `withdrawal.failed` | Withdrawal failed | `WITHDRAWAL_FAILED_001` |
| `withdrawal.rejected` | Withdrawal rejected | `WITHDRAWAL_REJECTED_001` |
| `withdrawal.cancelled` | Withdrawal cancelled by customer | `WITHDRAWAL_CANCELLED_001` |
| `investment.received` | Investment request recorded, not yet confirmed | `INVESTMENT_RECEIVED_001` |
| `investment.confirmed` | Investment confirmed (record completed) | `INVESTMENT_CONFIRMED_001` |
| `trade.order_placed` | Order accepted and resting on the book | `TRADE_ORDER_PLACED_001` |
| `trade.order_filled` | Order executed | `TRADE_ORDER_FILLED_001` |
| `trade.order_cancelled` | Order cancelled | `TRADE_ORDER_CANCELLED_001` |
| `trade.order_rejected` | Order could not be executed | `TRADE_ORDER_REJECTED_001` |
| `savings.contribution_recorded` | Savings contribution recorded | `SAVINGS_CONTRIBUTION_001` |
| `savings.goal_created` | Savings goal created | `SAVINGS_GOAL_001` |
| `savings.milestone` | Savings goal milestone crossed | `SAVINGS_MILESTONE_001` |
| `savings.contribution_failed` | Scheduled contribution skipped | `SAVINGS_CONTRIBUTION_FAILED_001` |
| `savings.goal_achieved` | Savings goal reached | `SAVINGS_GOAL_ACHIEVED_001` |
| `retirement.updated` | Retirement plan saved | `RETIREMENT_UPDATED_001` |
| `security.new_login` | Sign-in from a new IP address | `SECURITY_NEW_LOGIN_001` |
| `security.new_device` | Sign-in from a new browser/device | `SECURITY_NEW_DEVICE_001` |
| `security.password_changed` | Password changed or reset | `SECURITY_PASSWORD_CHANGED_001` |
| `security.password_reset_requested` | Password reset link issued | `SECURITY_PASSWORD_RESET_001` |
| `security.2fa_enabled` | Two-factor authentication enabled | `SECURITY_2FA_ENABLED_001` |
| `security.2fa_disabled` | Two-factor authentication disabled | `SECURITY_2FA_DISABLED_001` |
| `security.wallet_changed` | Customer source wallet address registered/verified/removed | `SECURITY_WALLET_CHANGED_001` |
| `security.withdrawal_alert` | Withdrawal to a new destination or held for review | `SECURITY_WITHDRAWAL_ALERT_001` |
| `security.account_restricted` | Account restricted or suspended by staff | `SECURITY_ACCOUNT_RESTRICTED_001` |
| `security.suspicious_activity` | Account locked after repeated failed sign-ins | `SECURITY_SUSPICIOUS_001` |
| `account.profile_updated` | Profile updated | `ACCOUNT_PROFILE_UPDATED_001` |
| `account.phone_changed` | Phone number changed | `ACCOUNT_PHONE_CHANGED_001` |
| `account.access_restored` | Account reactivated by staff | `ACCOUNT_ACCESS_RESTORED_001` |
| `support.ticket_created` | Support ticket created | `SUPPORT_TICKET_CREATED_001` |
| `support.reply` | Staff reply recorded | `SUPPORT_REPLY_001` |
| `support.info_required` | Ticket moved to waiting for customer | `SUPPORT_INFO_REQUIRED_001` |
| `support.resolved` | Ticket resolved | `SUPPORT_RESOLVED_001` |
| `support.closed` | Ticket closed | `SUPPORT_CLOSED_001` |

Rules for specific cases: a withdrawal *approval* is never described as a payout; an order *placed* email is sent only for a resting order (a filled order gets *executed* instead);
the builder re-reads the order/deposit/investment row and **refuses to describe a state the record is not in**; internal hold/risk reasons and support-message text are never put in an email.
The **security withdrawal alert** is sent only for a destination never used before or a withdrawal held for review. **New device / new login** compares against recorded successful sign-ins
(first sign-in is never "new").

Reminders (`reminders.py`, run by the worker every 5 min or "Run now"): verification at 24 h and 72 h, incomplete KYC (max 3, weekly), outstanding KYC information, retirement review (off),
re-engagement (off; opt-in only). Quiet hours (default 21:00–08:00) follow the **customer's** time zone and only postpone. Reminders stop automatically when the condition is resolved.

## 6. Environment variables

| Variable | Purpose |
|---|---|
| `EMAIL_PROVIDER` | `resend` or `ses` (required in production); `dev` (default outside production: captures, sends nothing); `mock` (tests) |
| `EMAIL_PROVIDER_KEY` | Resend API key (secret) |
| `EMAIL_WEBHOOK_SECRET` | Resend webhook signing secret `whsec_…` (secret). Without it nothing can be confirmed delivered and the webhook refuses everything |
| `EMAIL_FROM_ADDRESS`, `EMAIL_FROM_NAME`, `EMAIL_REPLY_TO` | sender on a **domain you have verified with the provider** |
| `PUBLIC_BASE_URL` | `https://` origin of the app. **The only source of links in emails** (never request headers). Required in production |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `EMAIL_SES_REGION`, `EMAIL_SES_CONFIGURATION_SET` | SES |
| `EMAIL_SANDBOX_ALLOWLIST` | comma-separated addresses / `@domains`. Outside production a real provider may mail **only** these; the app refuses to start staging with a real provider and no allow-list |
| `EMAIL_LINK_ALLOWED_HOSTS` | extra hosts administrators may link to in a campaign button |
| `EMAIL_DEFAULT_TIMEZONE` | quiet-hours zone for customers with none set (default `UTC`) |
| `EMAIL_INLINE_WORKER` | development only (`run.py` starts the worker thread). Production uses the separate worker |

Startup validation (`Config.validate`) **refuses** production with `dev`/`mock`, a missing sender, a non-https `PUBLIC_BASE_URL`, or Resend without key/webhook secret. No key is hard-coded anywhere (a test scans the source).
Tunables (Email Center → Email Settings): approval threshold (default 50), maximum recipients (5000), quiet hours, tracking (off), max attempts (6).

### Resend setup
1. Create a Resend account, add and verify your sending domain (SPF, DKIM; add a DMARC record).
2. Create an API key with "sending access" → `EMAIL_PROVIDER_KEY`. Set `EMAIL_FROM_ADDRESS` to an address on that domain.
3. Add a webhook to `https://<your-host>/api/webhooks/email/resend` for **email.delivered, email.delivery_delayed, email.bounced, email.complained, email.failed** (optionally opened/clicked: ignored unless tracking is enabled). Copy its signing secret → `EMAIL_WEBHOOK_SECRET`.
4. Set `EMAIL_PROVIDER=resend`, `PUBLIC_BASE_URL=https://…`, then start the app **and the worker**.

## 7. Run it

```bash
# migrations (008_email_center.sql) apply automatically when the app starts; to apply without serving:
python -c "from server.app import create_app; create_app()"

python run.py                         # development: dev transport + in-process worker; emails are CAPTURED, never sent
# production
gunicorn "server.app:create_app()"    # web   (or your WSGI server of choice)
python -m server.mailer.worker        # worker: run as its own supervised process (systemd/Docker). --once for cron
```
Several workers may run; a message is claimed with a compare-and-set and cannot be sent twice. A worker that dies mid-send is recovered: it is re-sent only if the provider honours the idempotency key (Resend), otherwise failed as "outcome unknown" rather than risk a duplicate.

## 8. Security notes
- Every admin endpoint is guarded server-side by an `email.*` permission (matrix tested for every endpoint × role). Sending, publishing, approving large sends, provider settings and suppression are separate permissions. A campaign over the threshold needs a **different** administrator to approve it; security/transactional templates must be published by someone other than the author.
- One-time links live in `secret_json` only until the message is sent, then are erased; no API returns them; the old plaintext tokens in `email_outbox.body` were removed by the migration. Encrypt the database at rest.
- Previews render in a sandboxed iframe; all values are escaped; administrators write plain text only; custom button links must be https on the app host or an allow-listed host; headers are checked for CR/LF.
- Test sends go only to the signed-in administrator's own address. Logs mask recipient addresses; viewing a message body is audited.
- Verification/reset: per-user cooldown (2 min) and hourly cap in addition to the existing per-IP rate limits.

## 9. What changed in existing files
`server/providers.py` (email provider now selects a transport), `server/app.py` (mailer, seeding, blueprints, CSRF exemption for the token-authenticated unsubscribe), `server/config.py`, `server/rbac.py`,
`server/notify.py` (no longer writes fake outbox rows), `server/security.py` (`log_event` returns the event id), `server/finance.py`, `server/crypto_deposits.py`, `server/crypto_withdrawals.py`, `server/routes_auth.py`, `server/routes_user.py`,
`server/routes_admin.py`, `server/routes_public.py` (dev mailbox), `run.py`, `web/admin/index.html`, `web/js/admin.js` ("Send email" on a customer), `.env.example`, `README.md`. New: `server/mailer/*`, `server/migrations/008_email_center.sql`, `web/js/email-admin.js`, `web/js/unsubscribe.js`, `web/unsubscribe/index.html`, `tests/test_email_*.py`, `tests/email_base.py`.

## 10. Known limitations (not hidden)
- **Not verified against live providers.** This environment has no network and no credentials. Resend and SES requests were verified against local fake servers and (SigV4) AWS's published vector; the Resend webhook scheme follows Svix's documented algorithm and is tested with locally-signed payloads. Do a live test with a verified domain before launch.
- SES delivery events (SNS) are not verified/handled: SES mail stays `accepted`.
- No workflow exists for: email change, partial fills, order expiry, price alerts, investment cancellation/maturity, scheduled statements, savings-account creation (separate from sign-up). Those templates exist for later but have **no automatic trigger**; the Automations tab lists them.
- Terms/privacy update notices are sent by an administrator (the legal module does not announce versions by email by itself).
- New-device detection uses IP and User-Agent from recorded sign-ins; there is no GeoIP, so "location" is the IP address only.
- Footer social icons from the reference image are omitted: there are no verified social URLs to link to.
- Outlook desktop ignores rounded corners (the button is square there). Dark-mode email clients are asked to keep the light card (`color-scheme: light`).
- Open/click tracking is off by default and not shown in the UI beyond provider events.
- The customer dashboard has no dedicated email-preferences screen; consent is managed through the existing notification settings ("promotional") and the unsubscribe page, plus `GET/PUT /api/me/communication-preferences`.
- **Production database blocker (inherited, not introduced here).** The whole application is SQLite-dialect SQL; the PostgreSQL adapter is still an open item in `docs/PRODUCTION_ROADMAP.md` (M1), and `Config.validate` refuses to start production on SQLite. The mailer follows the same conventions as the rest of the code and will need porting together with it. SQLite-specific parts: `INSERT OR IGNORE` / `ON CONFLICT` upserts, `json_extract`, `RAISE(ABORT, …)` triggers, partial unique indexes, `BEGIN IMMEDIATE`, and the compare-and-set claim in `outbox.deliver_one` (on PostgreSQL use `SELECT … FOR UPDATE SKIP LOCKED`). Until then, production email cannot be switched on in this codebase; this is the same gate as the rest of go-live.

## 11. Production deployment checklist
- [ ] PostgreSQL port of the application completed (roadmap M1), including the SQLite-specific SQL listed in §10
- [ ] `APP_ENV=production`, `EMAIL_PROVIDER=resend|ses`, `PUBLIC_BASE_URL=https://…`, sender domain verified (SPF/DKIM/DMARC)
- [ ] `EMAIL_PROVIDER_KEY` and `EMAIL_WEBHOOK_SECRET` set from a secret manager; webhook configured and a live test shows `accepted` → `delivered`
- [ ] Worker running under a supervisor and monitored (alert on `failed` and a growing `queued` backlog)
- [ ] `legal_config` filled (entity, address, website, support email) so footers are complete
- [ ] Roles reviewed: who can send, publish, approve bulk, change settings
- [ ] Bounce/complaint rates watched; suppression list reviewed
- [ ] Database encrypted at rest and backed up (the outbox holds recipient addresses)
- [ ] Send each security template to yourself in Gmail, Outlook and a phone client (Email Center → Templates → More… → Send test email)


## SMTP host (admin-managed)
`EMAIL_PROVIDER=smtp` sends through the SMTP server configured at **Admin > Email Center > Email & SMTP** (permission `email.smtp.manage`, super admin by default).
- Fields: enable switch, host, port (25/465/587/2465/2525/2587), security (STARTTLS, implicit TLS, or none in development only), authentication, username, password, sender, reply-to, public base URL, language, timezone.
- **Password**: encrypted with `SETTINGS_ENCRYPTION_KEY` (Fernet; the key lives only in the environment). It is never returned by any API, never logged or audited; leaving the field blank keeps the stored one; "Remove" clears it. Without the key a password cannot be saved.
- **Safety**: certificates are always verified; credentials are never sent unencrypted; the host is resolved and the connection pinned to a vetted IP, and private/loopback/link-local/metadata addresses are refused (SSRF); header injection is rejected.
- **Test SMTP connection** connects, negotiates TLS and authenticates, sends nothing. **Send test email** sends one message to one explicit address through the normal outbox (visible in logs), respecting the sandbox allow-list outside production.
- **Statuses**: `accepted` = the server returned 250 after DATA. SMTP provides no delivery proof, so these messages never become `delivered` without a provider event feed. If the connection drops while the body is being sent the outcome is *unknown*: the message is failed with `outcome_unknown` and **not** auto-retried (no duplicate emails); retry manually after checking the server logs. 4xx replies and network errors retry with exponential backoff; 5xx and authentication failures are permanent.
- Turning the switch off pauses the queue (messages stay `queued`).
