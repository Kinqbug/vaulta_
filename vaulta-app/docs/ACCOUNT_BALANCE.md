# Account Balance, funding and Total Portfolio Value

## The rule
* **Funding goes to the Account Balance** (the customer's USD cash account, shown as "Account Balance" on both dashboards). A crypto deposit is **not** a portfolio holding.
* **Total Portfolio Value = investing + trading holdings + savings (which includes goals) + a funded retirement account.** Idle cash is not part of it.
  `net_worth` = portfolio value + Account Balance. Everything (investing, trading, goals, savings, retirement) is funded *from* the Account Balance.
* Goal contributions move cash into the savings account, so counting savings already counts goals; they are never added twice.
  The retirement planner's typed-in figures are projections, not money, and are never counted.

## Deposits (crypto -> Account Balance)
1. The deposit is verified and credited exactly as before (on-chain monitor or staff approval of a request; nothing changed in the risk/approval rules).
2. In the same database transaction it is converted: the crypto leaves the customer's crypto liability (Dr customer / Cr `CRYPTO_CONVERSION`) and a completed USD
   `deposit` is posted to the cash account at `units x price` (rounded down to the cent). The company keeps the crypto in custody.
3. If the asset has **no usable USD price** (missing, non-positive, asset paused, or older than `crypto_price_max_age_minutes`), nothing is credited and the
   deposit is held for review with `price_unavailable`. It is never guessed. Admin asset cards show a red "No USD price" badge when this applies:
   add/enable a priced market asset with the same ticker (Admin -> Assets -> Market).

## Withdrawals (Account Balance -> crypto)
The customer chooses an amount in the asset. The server debits `ceil((amount + fee) x price)` dollars, converts it, and then reserves exactly as before
(2FA, review, separation of duties, completion rules unchanged). The rate is **locked at request time** and stored on the withdrawal.
Cancel / reject / fail reverse the reserve **and both conversion entries**, so the customer gets back exactly the dollars debited, even if the price moved.
"Withdraw all" is computed by the server against the Account Balance (binary search over the same conversion the server charges with).

## Settings (Admin -> Settings)
* `crypto_conversion_spread_bps` (default 0): customers receive `price x (1 - spread)` on deposits and pay `price x (1 + spread)` on withdrawals.
* `crypto_price_max_age_minutes` (default 0 = no limit): refuse conversion when the asset price is older than this.

## Accounting
* New GL account `2920 CRYPTO_CONVERSION` (clearing). Each conversion is one row in `crypto_conversions` (rate, spread, units, dollars, both ledger refs, reversal).
* Reconciliation counts the crypto the company acquired by conversion as still owed/held, so "held >= owed" and missing-funds detection are unchanged.
  Expected customer crypto balances are derived independently from deposits, withdrawals and conversions; any residual customer crypto liability is flagged.
* The cash side of a conversion appears in balances, statements and exports; the Transactions page lists the crypto item instead (with its dollar value) to avoid double listing.

## Existing balances
On startup, customers' pre-existing crypto balances are converted to Account Balance once (idempotent, one `legacy` conversion per balance). A balance whose asset
has no usable price is left untouched and logged, and is retried on the next start.

## Migration
`011_crypto_cash_conversion.sql` adds `crypto_conversions`, three columns on `crypto_withdrawals`, and the `2920` account. No data is deleted.
