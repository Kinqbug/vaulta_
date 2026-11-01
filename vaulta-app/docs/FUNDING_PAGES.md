# Deposit and Withdraw pages

Customer routes (hash-routed SPA): `/app/#/deposit` and `/app/#/withdraw`. They replace the old combined page. Both require a signed-in customer.

## Where everything comes from
Nothing on either page is hard-coded. Assets, logos, networks, addresses, limits, fees, arrival/processing text, instructions and notices are read from the
admin-managed registry (`/api/crypto/assets`, `/api/crypto/withdrawal-info`, `/api/crypto/withdrawal-quote`, `/api/crypto/validate-address`) and the customer's own ledger.
Withdraw amounts and fees are always recalculated by the server (`crypto_withdrawals.request_withdrawal`); the page only previews them.

## Admin: Admin console -> Assets -> "Cryptocurrencies (funding)"
- **Edit** an asset: name, description, logo (upload / replace / remove, validated server-side), logo alt text, asset notice, visibility.
- **Configure** an asset on a network: deposit/withdrawal switches, limits, fees, confirmations, and the customer-facing text
  (estimated arrival, withdrawal processing time, deposit/withdrawal instructions, network warning). Blank text falls back to a generic default.
- **Blockchain networks** tab: create (always created DISABLED, needs an address format), rename, change default confirmations, activate/suspend.
  A network only carries funds once a blockchain provider that can monitor and pay out on it is connected.
- Adding a new asset (e.g. Ethereum-based) needs no frontend change: its logo, name, networks and copy appear on both pages and in history once it is active.

## Status wording
Withdrawal requests show **Pending review** until the backend reports a later state. "Completed" is shown only when the backend marks it completed.
A deposit screenshot is evidence for staff review, never proof of payment.

## Not included
Network icons, daily withdrawal limits, withdrawal address allowlists, saved withdrawal addresses, PDF proof uploads, "awaiting payment"/"expired" rows in history.

## Tests
`python -m unittest discover -s tests` (432 tests). New: `tests/test_funding_pages.py` (API), `tests/test_funding_ui.py` (static checks + Chromium/Playwright; skipped if unavailable).
