# Legal documentation system

Six legal documents (Terms and Conditions, Privacy Policy, Financial and Service Disclosures, Cryptocurrency and Investment Risk Disclosure,
Accessibility Statement, Cookie Policy) are stored, versioned, published and accepted through the existing Flask + SQLite stack.
**Adding the documents does not make Vaulta legally compliant.** Read "Before going live" below.

## Routes
| Page | Notes |
|---|---|
| `/legal/terms`, `/legal/privacy`, `/legal/disclosures`, `/legal/risk-disclosure`, `/legal/accessibility`, `/legal/cookies` | Work when opened directly (`/legal/x` redirects to `/legal/x/`). Content comes from the API. |
| `/legal/view/?doc=<slug>` | Viewer for regional supplements created in the admin console. |

API (public): `GET /api/legal/documents`, `GET /api/legal/documents/<slug>[?version=N]`, `GET /api/legal/current?slugs=terms,privacy`, `POST /api/legal/cookie-consent`.
API (signed in): `GET /api/legal/risk-status`, `POST /api/legal/risk-acknowledgement`, `GET /api/legal/my-acceptances`.
API (admin, `/api/admin/legal/...`): list, status, config, supplements, versions, drafts, preview, publish, archive, acceptances.
Public endpoints only ever return PUBLISHED (or previously published, now ARCHIVED) versions; drafts are admin-only.

## Data model (migration `006_legal_documents.sql`)
* `legal_documents` (unique `slug`, `required`, `doc_type` core|supplement, `region`).
* `legal_versions` (`status` DRAFT|PUBLISHED|ARCHIVED, `version`, `content`, `content_hash`, `material`, `effective_at`, `published_at`, `created_by/updated_by/published_by`).
  Partial unique indexes allow one PUBLISHED and one DRAFT per document. Triggers make published/archived content immutable, block deletes and block backwards status changes.
* `legal_acceptances` (`TERMS_ACCEPTANCE`, `PRIVACY_NOTICE_ACKNOWLEDGEMENT`, `RISK_DISCLOSURE_ACKNOWLEDGEMENT`): user, document, **version row**, version number, **content hash**, server timestamp.
  Append-only (triggers). No IP address or user-agent is stored here, by design.
* `legal_config`: deployment details substituted into the documents. `cookie_consents`: append-only log of cookie choices (random browser id, categories, action, policy version, timestamp; no IP, no user id).

## Permissions (enforced in the API on every route)
`legal.read` (view documents, history, readiness; acceptances also need `users.read`), `legal.manage` (drafts, supplements, configuration, archive drafts),
`legal.publish` (publish). Super admin has all three; compliance admin has read + manage (cannot publish); auditor has read. Every change is written to the hash-chained audit log.

## Publishing workflow
Create draft (copies the current text) → edit → preview → publish (confirmation step). The previous version is archived, never overwritten. A change note is required from v2 onwards.
Mark a version **material** when users must act again: new sign-ups see it, and a material Risk Disclosure forces re-acknowledgement before investing or trading.
In `ENV=production`, publishing is refused until every legal configuration value is supplied.

## Acceptance flow
* **Registration** requires an unticked-by-default checkbox, sends the versions shown (`terms_version`, `privacy_version`), and the server records both acceptances in the same transaction as the account.
  If a document was republished meanwhile, registration returns `409 legal_version_changed` and creates nothing. The Privacy Policy is an acknowledgement only; cookie/marketing consent is separate.
* **Risk Disclosure**: `POST /api/investments` and `POST /api/orders` return `428 risk_ack_required` until the user has acknowledged the current (or latest material) version. The dashboard shows an acknowledgement screen on first visit to Invest/Trade. Setting `risk_ack_required` = `0` in system settings turns the gate off.

## Cookie consent
`web/js/consent.js` (loaded on every page). Optional categories are off by default; Accept / Reject / Manage are visually identical. The choice is stored in the first-party cookie `vaulta_consent` (180 days) and logged minimally on the server.
Optional scripts must be declared `<script type="text/plain" data-consent="analytics" data-src="...">` (or use `VaultaConsent.run`), and never execute before consent. Withdrawal runs registered cleanups and reloads if such a script had run.
**Inventory today:** the code base loads no analytics, advertising or other optional technology. Storage in use: the HTTP-only session cookie, `vaulta_consent`, and demo/UI state in `localStorage`/`sessionStorage` (strictly necessary). The consent UI says so.
Retention: `python -m server.legal purge` removes superseded consent rows older than 24 months.

## Before going live (manual steps)
1. Admin console → **Legal → Legal configuration**: supply every value. Nothing is invented; unresolved details are highlighted on the public pages with a "Not final" notice and block production publishing.
   Required: legal entity name, registration jurisdiction, registered address, website URL, support / legal / privacy / complaints / accessibility emails, governing law, **verified** regulatory status, **verified** authorised services (or "None").
2. Have qualified counsel review all six documents against the live product and every jurisdiction you serve; re-publish (as a new version) after edits.
3. Confirm the wording about custody, fees, processing times and regulatory status matches reality (see discrepancies).
4. Schedule the cookie-consent purge; decide retention periods for acceptance evidence (append-only by design) and KYC/verification records.
5. Add regional supplements (admin → "New regional supplement") where local law needs them. **Not implemented:** server-side jurisdiction gating of services. Vaulta cannot infer residency from an IP address; restrictions must be added with a verified residency/eligibility process.

## Discrepancies between the legal drafts and the product (flagged, text left as supplied)
* **Demo/mock mode.** By default the platform runs in `CRYPTO_PROVIDER_MODE=mock` with real funds disabled, trading executes on an internal ledger, and the site describes itself as a fictional concept/demo. The drafts describe live cryptocurrency funding, custody and trading. Do not publish them for real customers until real providers, custody and licensing exist.
* **Custody / third-party providers**: no custodian, wallet provider, exchange or market-data provider is integrated in the default configuration; the drafts only say these "may" exist. State the real arrangement before launch.
* **Staking, lending, leverage, derivatives, stablecoins, community/chat features**: none exist. The drafts use "where offered/where provided" wording, so they are accurate but broader than the product. High-yield savings and the Interest Vault are the only yield-style features.
* **Withdrawals**: the product adds 2FA, review and separation-of-duties controls; the drafts do not promise any processing time (correct). No fee schedule is displayed by the legal text.
* **Account closure**: the Terms mention closure through "account settings or support"; there is no self-service closure, only support tickets.
* **Complaints / external dispute resolution / regulatory complaint channels** need real details.
* **IP address and user agent** are recorded in security events (covered by the Privacy Policy "Technical information").
* **Identity verification** (KYC documents) exists and matches the Privacy Policy.

## Items for legal counsel
Governing law and jurisdiction clauses; liability limitation; consumer-protection carve-outs; eligibility/age; sanctions language; data-transfer mechanisms; retention periods; cookie law in each target market; the "no guaranteed returns" and "custody" statements; whether the click-through risk acknowledgement is sufficient for any regulated product.

## Seed content
`server/legal_content/*.md` are the source drafts. Seeding runs at startup, is idempotent, and never overwrites an existing document. The Cookie Policy draft contained one stray sentence unrelated to cookies ("Expected result: A Vaulta application with six functioning legal pages...") that looked like pasted instructions; it was **left out**.
