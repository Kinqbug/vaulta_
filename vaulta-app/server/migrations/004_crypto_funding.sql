-- 004_crypto_funding: company-controlled deposit wallets, deposit intents, on-chain deposits, crypto withdrawals, reconciliation.
-- This schema stores PUBLIC wallet information only. There is deliberately no column anywhere for private keys, seed phrases,
-- mnemonics or wallet passwords; `python -m server.verify` scans the live schema and fails if such a column ever appears.
-- Amounts are integer minor units stored as TEXT (18-decimal assets overflow SQLite's 64-bit integers).

ALTER TABLE blockchain_networks ADD COLUMN address_format TEXT CHECK(address_format IS NULL OR address_format IN('btc','evm','solana'));
-- which attribution methods the network can support: memo_or_source (memo field + verified sender), source (verified sender only), none (manual only)
ALTER TABLE blockchain_networks ADD COLUMN attribution_mode TEXT NOT NULL DEFAULT 'source' CHECK(attribution_mode IN('memo_or_source','source','none'));

CREATE TABLE IF NOT EXISTS company_crypto_wallets(
  id INTEGER PRIMARY KEY,
  asset_network_id INTEGER NOT NULL REFERENCES asset_networks(id),
  wallet_label TEXT NOT NULL,
  public_address TEXT NOT NULL,
  wallet_type TEXT NOT NULL DEFAULT 'deposit' CHECK(wallet_type IN('deposit','hot','cold','sweep','operational','treasury','customer_deposit')),
  provider TEXT NOT NULL DEFAULT 'external_custody',
  status TEXT NOT NULL DEFAULT 'inactive' CHECK(status IN('active','inactive','maintenance','manual_review','retired')),
  is_deposit_enabled INTEGER NOT NULL DEFAULT 0 CHECK(is_deposit_enabled IN(0,1)),
  minimum_deposit TEXT,                     -- minor units; NULL = asset-network default
  confirmation_requirement INTEGER CHECK(confirmation_requirement IS NULL OR confirmation_requirement>=1),   -- NULL = asset-network / network default
  risk_review_threshold TEXT,               -- minor units; deposits at/above need a second person's approval
  owner_user_id INTEGER REFERENCES users(id),                -- future customer-specific addresses
  parent_wallet_id INTEGER REFERENCES company_crypto_wallets(id),   -- future sweep hierarchy
  replaced_by_wallet_id INTEGER REFERENCES company_crypto_wallets(id),
  retired_at TEXT, created_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT);
-- one live wallet per asset-network and role (deposit/hot/cold/...), unless it is a customer-specific address
CREATE UNIQUE INDEX IF NOT EXISTS ux_ccw_one_live ON company_crypto_wallets(asset_network_id, wallet_type)
  WHERE status IN('active','maintenance','manual_review') AND owner_user_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_ccw_addr ON company_crypto_wallets(asset_network_id, wallet_type, lower(public_address))
  WHERE status<>'retired' AND public_address<>'NOT_CONFIGURED';
CREATE INDEX IF NOT EXISTS ix_ccw_addr ON company_crypto_wallets(lower(public_address));
CREATE TRIGGER IF NOT EXISTS ccw_identity_immutable BEFORE UPDATE OF public_address,asset_network_id,wallet_type,owner_user_id ON company_crypto_wallets
  BEGIN SELECT RAISE(ABORT,'wallet address is immutable: retire the wallet and create a new one through the approval workflow'); END;
CREATE TRIGGER IF NOT EXISTS ccw_retired_final BEFORE UPDATE ON company_crypto_wallets WHEN OLD.status='retired' AND NOT (NEW.status='retired' AND OLD.replaced_by_wallet_id IS NEW.replaced_by_wallet_id)
  BEGIN SELECT RAISE(ABORT,'retired wallets are historical records and cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS ccw_no_delete BEFORE DELETE ON company_crypto_wallets BEGIN SELECT RAISE(ABORT,'wallet configurations are never deleted: retire them'); END;

-- two-person approval for every address change (high-risk operation)
CREATE TABLE IF NOT EXISTS wallet_change_requests(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL,
  asset_network_id INTEGER NOT NULL REFERENCES asset_networks(id), wallet_type TEXT NOT NULL DEFAULT 'deposit',
  current_wallet_id INTEGER REFERENCES company_crypto_wallets(id), old_address TEXT, new_address TEXT NOT NULL, new_label TEXT NOT NULL,
  reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','approved','rejected','cancelled','expired')),
  requested_by INTEGER NOT NULL, requested_at TEXT NOT NULL, requested_ip TEXT, requested_ua TEXT, expires_at TEXT NOT NULL,
  decided_by INTEGER, decided_at TEXT, decision_reason TEXT, decided_ip TEXT, resulting_wallet_id INTEGER REFERENCES company_crypto_wallets(id));
CREATE UNIQUE INDEX IF NOT EXISTS ux_wcr_pending ON wallet_change_requests(asset_network_id, wallet_type) WHERE status='pending';

-- dedicated, immutable record of every wallet configuration event (in addition to the hash-chained audit log)
CREATE TABLE IF NOT EXISTS wallet_change_events(
  id INTEGER PRIMARY KEY, event_type TEXT NOT NULL, asset_symbol TEXT, ledger_code TEXT, network_code TEXT, wallet_id INTEGER,
  old_address TEXT, new_address TEXT, actor_id INTEGER, approver_id INTEGER, request_ref TEXT, reason TEXT, ip TEXT, user_agent TEXT, created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS wce_no_update BEFORE UPDATE ON wallet_change_events BEGIN SELECT RAISE(ABORT,'wallet change events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS wce_no_delete BEFORE DELETE ON wallet_change_events BEGIN SELECT RAISE(ABORT,'wallet change events cannot be deleted'); END;

-- a customer says "I am about to send X" and gets a reference. Intents never match deposits by amount.
CREATE TABLE IF NOT EXISTS deposit_intents(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
  asset_network_id INTEGER NOT NULL REFERENCES asset_networks(id), company_wallet_id INTEGER NOT NULL REFERENCES company_crypto_wallets(id),
  expected_amount TEXT, reference_code TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','fulfilled','expired','cancelled')),
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, closed_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_di_one_open ON deposit_intents(user_id, asset_network_id) WHERE status='open';

-- sender addresses a customer has registered. Only VERIFIED addresses can attribute deposits; a verified address belongs to exactly one customer.
CREATE TABLE IF NOT EXISTS customer_source_addresses(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), network_id INTEGER NOT NULL REFERENCES blockchain_networks(id),
  address TEXT NOT NULL, address_key TEXT NOT NULL, challenge TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','verified','revoked','rejected')),
  proof_method TEXT, verified_by INTEGER, verified_at TEXT, note TEXT, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_csa_verified ON customer_source_addresses(network_id, address_key) WHERE status='verified';
CREATE UNIQUE INDEX IF NOT EXISTS ux_csa_user ON customer_source_addresses(user_id, network_id, address_key) WHERE status IN('pending','verified');

CREATE TABLE IF NOT EXISTS crypto_deposits(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL,
  network_id INTEGER NOT NULL REFERENCES blockchain_networks(id), asset_network_id INTEGER REFERENCES asset_networks(id),
  company_wallet_id INTEGER REFERENCES company_crypto_wallets(id), user_id INTEGER REFERENCES users(id), intent_id INTEGER REFERENCES deposit_intents(id),
  tx_hash TEXT NOT NULL, event_index INTEGER NOT NULL DEFAULT 0, sender TEXT, recipient TEXT NOT NULL, contract TEXT, memo TEXT,
  amount TEXT NOT NULL CHECK(amount NOT GLOB '*[^0-9]*' AND amount<>''), block_number INTEGER, block_hash TEXT,
  confirmations INTEGER NOT NULL DEFAULT 0, required_confirmations INTEGER,
  status TEXT NOT NULL CHECK(status IN('detected','validating','unconfirmed','confirming','confirmed','risk_review','credited',
        'rejected','manual_review','duplicate','wrong_network','unsupported_asset','reorged','failed')),
  status_reason TEXT, attribution_method TEXT CHECK(attribution_method IS NULL OR attribution_method IN('memo','verified_source','manual')), attribution_note TEXT,
  override_minimum INTEGER NOT NULL DEFAULT 0, risk_approval_required INTEGER NOT NULL DEFAULT 0, risk_approved_by INTEGER,
  received_entry_id INTEGER REFERENCES journal_entries(id), credit_entry_id INTEGER REFERENCES journal_entries(id),
  detected_at TEXT NOT NULL, confirmed_at TEXT, credited_at TEXT, updated_at TEXT,
  -- THE duplicate-protection key: a transfer *event* (tx + log/output/instruction index) exists once, ever.
  UNIQUE(network_id, tx_hash, event_index));
CREATE INDEX IF NOT EXISTS ix_cd_user ON crypto_deposits(user_id, id);
CREATE INDEX IF NOT EXISTS ix_cd_status ON crypto_deposits(status);
CREATE TRIGGER IF NOT EXISTS cd_identity_immutable BEFORE UPDATE OF network_id,tx_hash,event_index,recipient,amount,contract ON crypto_deposits
  WHEN OLD.status IN('credited') BEGIN SELECT RAISE(ABORT,'a credited deposit cannot be altered'); END;
CREATE TRIGGER IF NOT EXISTS cd_credited_final BEFORE UPDATE OF user_id ON crypto_deposits WHEN OLD.status='credited' AND OLD.user_id IS NOT NEW.user_id
  BEGIN SELECT RAISE(ABORT,'a credited deposit cannot be re-attributed'); END;
CREATE TRIGGER IF NOT EXISTS cd_no_delete BEFORE DELETE ON crypto_deposits BEGIN SELECT RAISE(ABORT,'deposits are never deleted'); END;

CREATE TABLE IF NOT EXISTS crypto_deposit_events(
  id INTEGER PRIMARY KEY, deposit_id INTEGER NOT NULL REFERENCES crypto_deposits(id), from_status TEXT, to_status TEXT, note TEXT, actor_id INTEGER, created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS cde_no_update BEFORE UPDATE ON crypto_deposit_events BEGIN SELECT RAISE(ABORT,'deposit events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS cde_no_delete BEFORE DELETE ON crypto_deposit_events BEGIN SELECT RAISE(ABORT,'deposit events cannot be deleted'); END;

CREATE TABLE IF NOT EXISTS chain_scan_cursors(network_id INTEGER NOT NULL, address_key TEXT NOT NULL, last_block INTEGER NOT NULL DEFAULT 0, updated_at TEXT, PRIMARY KEY(network_id,address_key));

CREATE TABLE IF NOT EXISTS crypto_address_blocklist(
  id INTEGER PRIMARY KEY, address_format TEXT NOT NULL CHECK(address_format IN('btc','evm','solana')), address_key TEXT NOT NULL, reason TEXT NOT NULL, added_by INTEGER, created_at TEXT NOT NULL,
  UNIQUE(address_format,address_key));

CREATE TABLE IF NOT EXISTS crypto_withdrawals(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id),
  asset_network_id INTEGER NOT NULL REFERENCES asset_networks(id), company_wallet_id INTEGER REFERENCES company_crypto_wallets(id),
  destination TEXT NOT NULL, amount TEXT NOT NULL CHECK(amount NOT GLOB '*[^0-9]*' AND amount<>''), fee TEXT NOT NULL DEFAULT '0',
  status TEXT NOT NULL CHECK(status IN('pending_approval','approved','signing','broadcast','confirming','completed','rejected','cancelled','failed')),
  status_reason TEXT, tx_hash TEXT, provider_ref TEXT, block_number INTEGER, block_hash TEXT, confirmations INTEGER NOT NULL DEFAULT 0, network_fee TEXT NOT NULL DEFAULT '0',
  risk_flags TEXT NOT NULL DEFAULT '[]', reserve_entry_id INTEGER REFERENCES journal_entries(id), completion_entry_id INTEGER REFERENCES journal_entries(id), release_entry_id INTEGER REFERENCES journal_entries(id),
  approved_by INTEGER, approved_at TEXT, created_at TEXT NOT NULL, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_cw_user ON crypto_withdrawals(user_id, id);
CREATE TRIGGER IF NOT EXISTS cw_no_delete BEFORE DELETE ON crypto_withdrawals BEGIN SELECT RAISE(ABORT,'withdrawals are never deleted'); END;

-- wallet-to-wallet movements (sweeps, treasury transfers). Recorded separately from customer deposits/withdrawals.
CREATE TABLE IF NOT EXISTS wallet_movements(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, asset_network_id INTEGER NOT NULL REFERENCES asset_networks(id),
  from_wallet_id INTEGER NOT NULL REFERENCES company_crypto_wallets(id), to_wallet_id INTEGER NOT NULL REFERENCES company_crypto_wallets(id),
  amount TEXT NOT NULL CHECK(amount NOT GLOB '*[^0-9]*' AND amount<>''), tx_hash TEXT, status TEXT NOT NULL DEFAULT 'recorded' CHECK(status IN('recorded','broadcast','confirmed','failed')),
  reason TEXT NOT NULL, created_by INTEGER, created_at TEXT NOT NULL, CHECK(from_wallet_id<>to_wallet_id));

CREATE TABLE IF NOT EXISTS reconciliation_runs(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, started_by INTEGER, status TEXT NOT NULL DEFAULT 'running' CHECK(status IN('running','clean','exceptions','error')),
  summary TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS reconciliation_exceptions(
  id INTEGER PRIMARY KEY, run_id INTEGER REFERENCES reconciliation_runs(id), dedupe_key TEXT NOT NULL, kind TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'high' CHECK(severity IN('low','medium','high','critical')), ledger_code TEXT, details TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','investigating','resolved','waived')), resolution_note TEXT, resolved_by INTEGER, resolved_at TEXT, created_at TEXT NOT NULL, last_seen_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_rx_open ON reconciliation_exceptions(dedupe_key) WHERE status IN('open','investigating');
