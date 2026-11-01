# Wallets & Reconciliation, Asset Settings and global Back navigation

## Routes (admin SPA, hash-routed)
| Route | Page |
|---|---|
| `#/settings-crypto?tab=wallets` | **Wallets & Reconciliation: Company Wallets** (summary cards, table, pending address changes, reconciliation panel) |
| `#/settings-crypto?tab=reconciliation` | **Reconciliation** records (per transaction), latest run per asset, exceptions |
| `#/asset-settings/<asset-id>?tab=general\|network\|wallet[&net=<code>][&wallet=<id>][&change=1]` | **Asset Settings**, a dedicated page (never a dialog) |
| `#/crypto-sources` | Customer sending-address verification (kept from the old Settings · Crypto page) |

Files: `web/js/wallets-admin.js`, `web/js/asset-settings.js`, `web/css/wallets.css`, `server/wallet_console.py`, migration `012_network_explorer.sql`.

## New admin API (`/api/admin/crypto`, all permission-checked server-side)
`GET /wallets/overview` · `GET /wallets/<id>/qr.svg[?download=1]` · `GET /reconciliation/records` (filters: asset, network, type, status, from, to, q, page) · `GET /reconciliation/summary` ·
`GET /assets/<id>/settings` · `POST /blockchain-networks/<id>/explorer`. Everything else reuses the existing audited endpoints (asset update with multipart logo, asset-network config, wallet settings, two-administrator address change requests).

## What is real and what is not
* Balances come from the configured `BlockchainProvider`. If it cannot answer (unimplemented live adapter, outage) the UI shows **Unavailable**, never zero. Fiat values are labelled *est.* and only appear with a valid, fresh asset price. The development mock chain is labelled "Test chain, no real funds".
* Reconciliation status (pending, confirming, reconciled, mismatch, failed) is **derived** from deposit/withdrawal/ledger/exception records. A credited deposit whose ledger credit differs from the on-chain amount is a *mismatch*. There is no endpoint that edits an amount or forces a status; the only manual step is the existing audited exception update. Withdrawal rows have no independent on-chain amount (shown *Unavailable*).
* QR codes are **generated** by the server from the validated stored address (bare address, same payload customers get). Uploading an external QR image is deliberately not supported: no QR decoder is a project dependency, and accepting an unverifiable image could point customers at another address.
* Asset symbol, asset type, contract address and decimals are part of the ledger identity and are read-only. Registry rows, wallets and ledger assets can never be deleted (database triggers), so "remove a network" means **disable** it; the page shows how many deposits/withdrawals/ledger lines reference it.
* Address changes: unchanged two-person workflow (proposer + a different approver, fresh 2FA each, new wallet starts inactive, old address kept in history).
* No private key, seed phrase or signing credential field exists anywhere.

## Back button (`web/js/nav.js`, `web/css/nav.css`)
One module for both SPAs. Each entry the app creates is stamped in `history.state.vn`.
1. A stamped previous entry exists: Back calls `history.back()` (filters kept in memory / sessionStorage are therefore restored).
2. Otherwise (bookmark, pasted URL, new tab, arrived from another site): Back **replaces** the current entry with the page's parent (`#/asset-settings/3` → `#/settings-crypto`, `#/profile/security` → `#/profile`, `#/settings/*` → overview, any top-level page → dashboard). It never calls `history.back()` there, so it cannot leave Vaulta, and cannot ping-pong.
3. Targets must match `#/[A-Za-z0-9_\-./]*`; the normal router and server still enforce authentication and permissions.
4. The dashboard homes (admin overview, customer dashboard) never show the Back button.
5. `VNav.guard(fn)` asks before discarding unsaved edits (Asset Settings registers one); `beforeunload` is covered too.
Dialogs/sheets are not pages and keep their own close controls. The old in-page back links on Profile sub-pages and the Deposit/Withdraw header (which called raw `history.back()`) were removed in favour of this.

## Run / test
```bash
python -m server.seed && python -m server.crypto_setup --dev-demo-wallets   # dev data + mock wallets
python run.py                                                                # /admin/#/settings-crypto
python -m unittest tests.test_wallet_console tests.test_wallet_ui           # new tests (UI tests need Playwright + Chromium)
python -m unittest discover -s tests                                         # everything (~13 min)
```
