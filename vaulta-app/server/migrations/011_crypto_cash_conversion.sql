-- 011: crypto funding lands in the customer's Account Balance (USD cash). Every conversion between crypto and cash is a recorded, reversible fact.
--   deposit     crypto credited on-chain  -> USD credited to the cash account at the market rate (less any configured spread)
--   withdrawal  customer asks to withdraw -> USD debited from the cash account, converted to the asset at the market rate (plus any configured spread)
--   legacy      one-off conversion of crypto balances that existed before this change
-- The crypto ledger and its reconciliation keep working unchanged: a deposit is still credited to the customer's crypto liability first and converted immediately after.
CREATE TABLE IF NOT EXISTS crypto_conversions(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
  direction TEXT NOT NULL CHECK(direction IN('deposit','withdrawal','legacy')),
  ledger_code TEXT NOT NULL, units TEXT NOT NULL,               -- minor units of the ledger asset
  price TEXT NOT NULL, spread_bps INTEGER NOT NULL DEFAULT 0, usd_cents INTEGER NOT NULL CHECK(usd_cents>0),
  source_type TEXT, source_id INTEGER,                          -- crypto_deposits.id / crypto_withdrawals.id / user id for legacy
  crypto_entry_id INTEGER, cash_txn_id INTEGER, reversed_at TEXT, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_cconv_source ON crypto_conversions(direction, source_type, source_id, ledger_code);
CREATE INDEX IF NOT EXISTS ix_cconv_user ON crypto_conversions(user_id);
ALTER TABLE crypto_withdrawals ADD COLUMN conversion_id INTEGER;
ALTER TABLE crypto_withdrawals ADD COLUMN usd_debit_cents INTEGER;
ALTER TABLE crypto_withdrawals ADD COLUMN rate TEXT;
INSERT OR IGNORE INTO gl_accounts(code,name,type,normal_side,is_system,created_at) VALUES('2920','Crypto/cash conversion clearing','liability','credit',1,strftime('%Y-%m-%dT%H:%M:%SZ','now'));
