# VAULTA crypto funding architecture

Crypto is the platform's **only** deposit/withdrawal method. Customers: *Deposit & withdraw* page (`#/deposit`). Staff: *Deposits* and *Withdrawals* admin pages
(manual review, attribution, risk approval, withdrawal approval/signing; roles `operations_admin`, `crypto_operations`, read-only for `finance_admin`/`compliance_admin`/`auditor`)
and *Settings → Crypto* (company wallets, source-address verification, reconciliation). Legacy bank/card funding is off unless the `fiat_funding_enabled` setting is set to 1.

```
Customer → deposit intent → on-chain payment → BlockchainProvider (monitor) → independent verification → confirmations/finality
        → attribution → risk/compliance → double-entry ledger → customer crypto balance → Portfolio
```

Supported initially: **BTC, ETH, USDT, USDC, SOL**. Networks, token contracts, confirmation policy and minimums are configuration (registry + wallet rows), never code.

## 1. Where things live

| Concern | Module | Tables |
|---|---|---|
| Asset / network registry (existing) | `assets.py` | `crypto_assets`, `blockchain_networks`, `asset_networks`, `ledger_assets` |
| Company wallets (public data only), two-person change workflow | `crypto_wallets.py` | `company_crypto_wallets`, `wallet_change_requests`, `wallet_change_events` |
| Address validation (BTC base58check/bech32/bech32m, EVM EIP-55, Solana) | `chain_addresses.py` | n/a |
| Provider abstractions + mock chain | `chain_providers.py` | n/a |
| Intents, source addresses, attribution, state machine, monitor | `crypto_deposits.py` | `deposit_intents`, `customer_source_addresses`, `crypto_deposits`, `crypto_deposit_events`, `chain_scan_cursors` |
| Ledger postings | `crypto_ledger.py` | `journal_*` (existing GL) |
| Withdrawals | `crypto_withdrawals.py` | `crypto_withdrawals` |
| Reconciliation | `crypto_recon.py` | `reconciliation_runs`, `reconciliation_exceptions` |
| Verification CLI | `verify.py` | n/a |
| QR (dependency-free, decode-tested) | `qr.py` | n/a |
| HTTP | `routes_crypto.py` | n/a |

## 2. Keys never enter Vaulta
No table or column holds a private key, seed phrase, mnemonic, recovery phrase or wallet password. `verify` scans the live schema, the config refuses to start if the
environment contains variables named like secrets, and every wallet endpoint rejects request bodies containing such fields. Withdrawals call
`WalletProvider.create_transaction/submit_transaction`; signing happens inside the custody system. `MockWalletProvider` (dev) moves in-memory balances only;
`TestnetWalletProvider` / `SecureCustodyProvider` are unimplemented adapters that raise `provider_not_configured`.

## 3. Deposit attribution (shared company addresses)
A blockchain transfer does not say which customer sent it, so the **amount is never used**. A deposit is credited automatically only when:

1. **Memo** (networks whose `attribution_mode='memo_or_source'`, Solana): the memo equals an open intent's reference code on the same asset and wallet; or
2. **Verified sender**: the transfer's sender is an address a customer proved they control (signed-message proof, or staff review with evidence), that address is
   verified for exactly one customer (DB-unique), and that customer has an open intent for this asset/wallet.

If memo and sender disagree, or neither applies, the deposit becomes `manual_review`. The funds are recognised in custody (suspense) once final, but no customer is
credited until staff attribute it (`attribute` requires a reason and a **different** administrator to approve before credit).
UTXO providers must report `sender` only when all inputs share one address, otherwise `None`.

## 4. State machine
`detected → validating → unconfirmed → confirming → confirmed → risk_review → credited`.
Exceptional: `rejected, manual_review, duplicate, wrong_network, unsupported_asset, reorged, failed`. Illegal transitions raise; credited rows are immutable (DB triggers).

* **Validating** re-reads the transfer from the provider and compares recipient, contract, amount, success. Customer-supplied hashes are only a hint to look.
* **Finality** is per asset-network: wallet override → asset-network override → network default; Solana additionally requires the `finalized` commitment.
* **Duplicate protection**: `UNIQUE(network_id, tx_hash, event_index)` (EVM log index / UTXO vout / Solana instruction index) plus idempotent ledger keys `cdep:{id}:received|credit`.
* **Wrong network** (company address seen on a registered but unsupported network), **wrong asset** (unknown token contract, or an asset not configured for that address): recorded, never credited, surfaced by reconciliation.
* **Reorg**: before credit the deposit returns to `reorged` and the custody entry is reversed; if it re-appears the same row revives. After credit a vanished transaction raises a **critical** reconciliation exception (no automatic debit).
* **Minimum** deposits below the threshold go to `manual_review` (`below_minimum`), never silently credited.
* **Risk step** fails closed: KYC, account status, maintenance mode, address blocklist/screening provider, per-wallet second-approval threshold, `REAL_FUNDS_ENABLED` in live mode.

## 5. Ledger
```
confirmed on-chain     Dr CUSTODY_CRYPTO            Cr SUSPENSE
credited to customer   Dr SUSPENSE                  Cr CRYPTO-U{user}  (liability, per ledger asset e.g. USDT.ETHEREUM)
withdrawal reserved    Dr CRYPTO-U{user}            Cr CRYPTO_WD_CLEARING (+ Cr FEE_INCOME)
withdrawal completed   Dr CRYPTO_WD_CLEARING        Cr CUSTODY_CRYPTO   (+ Dr NETWORK_FEE_EXPENSE Cr CUSTODY_CRYPTO)
```
Customer crypto balances are general-ledger balances; the Portfolio reads them (`portfolio._crypto_rows`) and values them with the existing `assets` prices. There is no second
balance store. Transferred-in crypto has no cost-basis tracking yet, so cost = current value (no invented gain/loss).

## 6. Wallet administration
Admin → **Settings → Crypto → Company wallets**. Address changes: propose (RBAC `crypto.wallets.manage` + fresh 2FA + reason + validation) → approve by a **different** administrator
(`crypto.wallets.approve`, fresh 2FA; the custody provider must confirm it controls the address) → old wallet **retired** (never deleted), new wallet created **inactive** with deposits off until
explicitly enabled (2FA again). Open intents on the old wallet are cancelled; deposits sent to a retired wallet go to manual review. Every step writes an immutable
`wallet_change_events` row (old/new address, asset, network, admin, approver, reason, IP, user-agent, timestamp) and the hash-chained audit log. Support staff have no wallet permissions.
`NOT_CONFIGURED` placeholders can never be activated or shown to customers.

## 7. Withdrawals
Request (KYC, **mandatory 2FA**, balance, destination validated for the network, not a Vaulta address, screened) → funds **reserved in the ledger in the same transaction**
(earlier than the stylised flow, so two requests cannot spend one balance) → approval by a different staff member → custody signs/broadcasts (compare-and-set prevents double submit;
an unknown submission outcome is never auto-retried) → confirmations → completion entry. Rejection/failure reverses the reservation.

## 8. Reconciliation
`rc.reconcile` compares per asset-network: on-chain holdings across **all** company wallets (so sweeps to cold storage are neutral) vs ledger custody vs customer liabilities
(+ unattributed + in-flight), re-derives each customer's expected balance from deposit/withdrawal records, rescans chains for unrecorded transfers, and checks ledger integrity.
Exceptions: `ledger_mismatch, insolvency, customer_balance_mismatch, missing_transaction, duplicate_credit, incorrect_customer_attribution, unmatched_transaction, unexpected_asset, wrong_network, ledger_integrity, post_credit_reorg`.
Critical ones cannot be waived. Wallet-to-wallet movements are recorded in `wallet_movements`, separately from deposits (schema also supports hot/cold/sweep/treasury wallets and customer-specific addresses).

## 9. Running it
See README. Monitoring: `POST /api/admin/crypto/monitor/run` (or call `crypto_deposits.run_once` from a scheduler/worker every few seconds–minutes).
`python -m server.verify` prints per-asset PASS/FAIL. Configuration checks run against your real DB; behaviour checks run the real engine on a throw-away DB with the mock chain.
Placeholders FAIL by design.

## 10. Integration work you must do before any real funds
1. Implement `BlockchainProvider` for each network (node/indexer): `get_transaction`, `list_incoming`, confirmations/commitment, balances, `verify_ownership` (signed-message verification).
2. Implement `WalletProvider` against your custody/HSM/MPC vendor (`controls_address` must be authoritative). Keys stay there.
3. Replace the internal address screen with a real sanctions/AML provider (`ComplianceProvider`).
4. Verify token contracts against the issuers; review default confirmations/minimums in `crypto_setup.SPEC`.
5. Run reconciliation on a schedule and alert on any open exception; run the monitor continuously.
6. Obtain the legal, licensing, custody, compliance, security and operational approvals for your jurisdiction and business model. Until then keep `REAL_FUNDS_ENABLED=0`.


## 12. Deposit requests: screenshot evidence and administrator approval

The customer-facing flow on **Deposit & withdraw**: choose an asset (logo from the registry) → choose a network → get the configured deposit address, QR code and instructions
(`POST /api/crypto/deposit-intents`) → send from their wallet/exchange → upload a screenshot with the amount and optional TXID, sending address and note
(`POST /api/crypto/deposit-requests`, multipart, optional `Idempotency-Key`) → track it (Submitted → In review → Approved/Rejected → Credited).
Administrators work the queue in **Admin → Deposits** (search by reference, email, customer ID, TXID or address; filter by status and asset; pending/approved/rejected counts).

**A screenshot is evidence, not proof.** Approval requires one of:
1. **Chain verification** (`crypto_deposits.chain_check`): the transaction is looked up by the BlockchainProvider, must pay *this* Vaulta address, in *this* asset/contract, on *this* network, succeeded, and be final
   under the asset's confirmation policy. The credited amount is the **on-chain** amount (a difference from the claim requires a note).
2. **Manual attestation** when no provider can verify (no TXID, or no provider connected): the administrator must tick an explicit "I verified this independently" box, enter the verified amount and describe how
   (>= 20 characters). It is recorded as `verification_method='manual_attestation'` and audited. If a provider *is* connected and says the transaction is missing, mismatched or unconfirmed, attestation is **not** available.

**Credited exactly once.** Approval runs in one `BEGIN IMMEDIATE` transaction with a compare-and-set on `status='pending'`; double-clicks, retries and simultaneous approvals by several administrators yield one credit
(`tests/test_crypto_requests.py::test_simultaneous_approvals...`). The credit uses the same ledger postings and the same `crypto_deposits` identity (`UNIQUE(network, tx_hash, event_index)`) as the on-chain monitor, so a transfer can be credited
through a request **or** the monitor, never both (a late monitor detection of a manually credited transaction goes to manual review as `duplicate_of_manual_credit`). Decided requests are immutable (DB triggers);
corrections are separate, audited adjustments. Rejection requires a reason (shown to the customer), credits nothing, keeps the evidence, and the customer may submit a new request.
Staff cannot approve or reject their own deposits; the customer sees only their own requests and screenshots (other users get 404).

**Evidence handling** (`uploads.py`): JPEG/PNG/WebP only, decided from the file's real content (PNG chunk CRCs, JPEG markers, WebP RIFF structure; trailing/appended data is rejected), max 5 MB and dimension limits, random 128-bit filenames,
private directory with mode 0600 outside the web root, served only by authenticated endpoints with `nosniff`, `Cache-Control: private, no-store` and a sandboxing CSP. Admin views/downloads of evidence are audited. Rejected uploads leave no files behind.

## 13. Asset catalog and logos (Admin → Assets → Cryptocurrencies)

Administrators with `crypto.registry.manage` create and edit assets (name, symbol, type, description, logo, visibility), add supported networks per asset and configure each asset-network:
deposit/withdrawal switches, status, deposit and withdrawal limits, withdrawal fee (flat + percentage, rounded up), and the deposit confirmation requirement. Everything customers see is read from these records.
There are no deposit fees: the sender pays the network fee. Hidden/disabled assets are not offered and cannot receive new requests; existing balances stay visible.

**Logos** are validated by content, stored under random names and served by `GET /api/crypto/logos/<id>` (authenticated, ETag-versioned). Replacing a logo updates the same asset and removes the old file only after the database commit.
PNG/JPEG/WebP (<= 1 MB, 32-2048 px) are accepted. **SVG** (<= 100 KB) is accepted only if it passes a strict allow-list (plain shapes/gradients/text; no scripts, styles blocks, event handlers, external references, `<use>`, `<image>`,
`foreignObject`, DOCTYPE/ENTITY) and **unsafe SVGs are rejected, not silently cleaned**. A remote logo URL is intentionally not supported (the CSP allows only same-origin images and fetching URLs server-side invites SSRF).
The frontend renders every logo through the single `UI.logo` component, which shows a generated symbol badge when there is no logo or it fails to load. Logos are included in the deposit/withdrawal selectors, balances, funding history, portfolio holdings, market and
watchlist rows, the transaction history, and the admin review/withdrawal/asset screens. An asset with network registrations or ledger history cannot be deleted: hide or disable it instead.

## 14. Withdrawals (same system, accurate states)
Request (2FA, KYC, limits and fee preview from the asset configuration, destination validated for the network) → funds **reserved in the ledger immediately** → **approval** by a different staff member → **processing** either through the custody
provider (`process`) or **manually** (`manual_process` records the TXID, network fee and note: the withdrawal becomes *broadcast/processing*, never completed) → **completed** only when the chain confirms it, or, when no provider is connected, when
an administrator attests they verified it (`manual_complete`). Rejection/failure reverses the reservation. Concurrent requests cannot overspend a balance (tested with six simultaneous requests).

## 15. Transaction history
Crypto funding is merged **read-only** into `/api/transactions` (and `/api/transactions/<ref>`) with the asset, network and TXID. It is deliberately **not** written to the legacy `transactions` table, because `accounting.backfill` mirrors every completed row of
that USD-cents table into the USD cash ledger and would double-count money. The general ledger remains the only balance authority.

## 16. Settings added
`crypto_screenshot_required` (default 1), `crypto_max_pending_requests` (default 5), `fiat_funding_enabled` (default 0).

## 17. Known limitations
* Screenshots cannot be authenticated (they can be fabricated): approval therefore depends on chain verification or an accountable, audited administrator attestation. OCR/auto-reading of screenshots is not implemented.
* Manually attested credits are not backed by an on-chain observation, so the next reconciliation reports `ledger_mismatch` until the funds are actually visible on-chain (intended).
* No real chain/custody adapters (by design, see §10). The mock chain lives in process memory: separate CLI processes do not share it.
* Internal address screening is a blocklist only, not a sanctions service.
* Refunds of unattributed/rejected funds and recovery of wrong-network/unsupported-asset deposits are manual custody procedures (reported, not automated).
* Transferred-in crypto has no cost-basis tracking; USD valuation uses the platform's existing quote table.
* SQLite is development-only (as elsewhere in the project); the same constraints apply on PostgreSQL but have only been exercised on SQLite here.
