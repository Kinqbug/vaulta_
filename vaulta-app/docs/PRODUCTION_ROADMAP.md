# Production roadmap (status against Vaulta_Production_Architecture_Claude_Prompt.docx)
Real customer funds stay disabled until the organisation's licence/custody/AML approvals are effective. This repository is **not production-ready**.

| # | Milestone | Status |
|---|-----------|--------|
| M1 | Migrations / PostgreSQL foundation | **Partial.** Versioned checksummed SQL migrations and production start-up refusals done. **Not done:** PostgreSQL adapter, Alembic, Redis, background workers (no Postgres or network in the build sandbox). |
| M2 | Double-entry general ledger | **Done** (SQLite): immutable entries, per-asset balancing, reversals, period close, subledger mirror and reconciliation, integrity verifier, admin read APIs. |
| M3 | Multi-asset / crypto registry | **Done**: registry, strict decimal parsing, audited enable/suspend. Asset list is operator-supplied data; nothing is pre-approved. |
| M4 | Custody abstraction (wallet providers, addresses) | Not started |
| M5 | Crypto deposits | Not started |
| M6 | Crypto withdrawals | Not started |
| M7 | Wallet/address security (hot/cold, whitelists) | Not started |
| M8 | Reconciliation (blockchain/custodian vs GL) | Started: GL vs subledger only |
| M9 | Admin crypto operations | Started: roles `crypto_operations` and `auditor`; registry and ledger APIs (no UI yet) |
| M10 | Valuation / FX | Not started |
| M11-M17 | Compliance, maker-checker, customer UI, security hardening, observability, DR, launch gates | Not started |

Decisions needed from the organisation before M4: custodian/wallet provider, supported assets and networks, confirmation policies, licence status, jurisdictions.
