-- 003_multi_asset: asset/network registry. Nothing is hard-coded: every asset/network is data, created disabled,
-- and enabled only by an authorised operator. USDT/USDC on different networks are DIFFERENT ledger assets (ledger_code e.g. USDT.ETH).
CREATE TABLE IF NOT EXISTS ledger_assets(
  code TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN('fiat','crypto')), decimals INTEGER NOT NULL CHECK(decimals BETWEEN 0 AND 36),
  created_at TEXT NOT NULL);
INSERT OR IGNORE INTO ledger_assets(code,kind,decimals,created_at) VALUES('USD','fiat',2,'2026-01-01T00:00:00Z');
-- code and decimals define what every stored integer means: they can never change.
CREATE TRIGGER IF NOT EXISTS la_immutable BEFORE UPDATE ON ledger_assets BEGIN SELECT RAISE(ABORT,'ledger assets are immutable'); END;
CREATE TRIGGER IF NOT EXISTS la_no_delete BEFORE DELETE ON ledger_assets BEGIN SELECT RAISE(ABORT,'ledger assets cannot be deleted'); END;

CREATE TABLE IF NOT EXISTS crypto_assets(
  id INTEGER PRIMARY KEY, symbol TEXT UNIQUE NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN('native','token','stablecoin')),
  status TEXT NOT NULL DEFAULT 'disabled' CHECK(status IN('disabled','active','suspended')), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS blockchain_networks(
  id INTEGER PRIMARY KEY, network_code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, chain_type TEXT NOT NULL CHECK(chain_type IN('utxo','account')),
  chain_id TEXT, is_testnet INTEGER NOT NULL DEFAULT 0, confirmations_required INTEGER NOT NULL CHECK(confirmations_required>=1),
  finality_policy TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'disabled' CHECK(status IN('disabled','active','suspended')), created_at TEXT NOT NULL);
-- decimals live here, not on the asset: the same token has different decimals on different networks.
CREATE TABLE IF NOT EXISTS asset_networks(
  id INTEGER PRIMARY KEY, ledger_code TEXT UNIQUE NOT NULL REFERENCES ledger_assets(code),
  asset_id INTEGER NOT NULL REFERENCES crypto_assets(id), network_id INTEGER NOT NULL REFERENCES blockchain_networks(id),
  contract_address TEXT, decimals INTEGER NOT NULL, min_deposit TEXT NOT NULL DEFAULT '0', confirmations_override INTEGER,
  withdrawal_fee_policy TEXT NOT NULL DEFAULT '{}', deposit_enabled INTEGER NOT NULL DEFAULT 0, withdrawal_enabled INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'disabled' CHECK(status IN('disabled','active','suspended')), created_at TEXT NOT NULL, updated_at TEXT,
  UNIQUE(asset_id, network_id));
CREATE UNIQUE INDEX IF NOT EXISTS ux_an_contract ON asset_networks(network_id, lower(contract_address)) WHERE contract_address IS NOT NULL;
CREATE TRIGGER IF NOT EXISTS an_identity_immutable BEFORE UPDATE OF ledger_code,asset_id,network_id,contract_address,decimals ON asset_networks
  BEGIN SELECT RAISE(ABORT,'asset/network identity, contract and decimals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS an_no_delete BEFORE DELETE ON asset_networks BEGIN SELECT RAISE(ABORT,'asset networks cannot be deleted; disable them'); END;
