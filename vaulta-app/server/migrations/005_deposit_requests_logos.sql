-- 005: user-submitted deposit requests with screenshot evidence, admin-managed asset logos/limits, manual withdrawal processing.
-- Extends the single crypto funding system (no parallel tables for balances): approved requests credit through the same ledger postings.

ALTER TABLE crypto_assets ADD COLUMN description TEXT;
ALTER TABLE crypto_assets ADD COLUMN logo_path TEXT;            -- managed file name under UPLOAD_DIR/asset-logos (never a user-supplied path)
ALTER TABLE crypto_assets ADD COLUMN logo_mime TEXT;
ALTER TABLE crypto_assets ADD COLUMN logo_sha256 TEXT;
ALTER TABLE crypto_assets ADD COLUMN logo_updated_at TEXT;
ALTER TABLE crypto_assets ADD COLUMN visible INTEGER NOT NULL DEFAULT 1 CHECK(visible IN(0,1));
ALTER TABLE asset_networks ADD COLUMN max_deposit TEXT;         -- minor units, NULL = no cap (min_deposit already exists)
ALTER TABLE asset_networks ADD COLUMN min_withdrawal TEXT;
ALTER TABLE asset_networks ADD COLUMN max_withdrawal TEXT;
ALTER TABLE crypto_deposits ADD COLUMN origin TEXT NOT NULL DEFAULT 'chain' CHECK(origin IN('chain','manual_request'));
ALTER TABLE crypto_withdrawals ADD COLUMN processing_mode TEXT NOT NULL DEFAULT 'custody' CHECK(processing_mode IN('custody','manual'));
ALTER TABLE crypto_withdrawals ADD COLUMN processed_by INTEGER;
ALTER TABLE crypto_withdrawals ADD COLUMN processed_at TEXT;
ALTER TABLE crypto_withdrawals ADD COLUMN admin_note TEXT;
ALTER TABLE crypto_withdrawals ADD COLUMN manual_attested INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS deposit_requests(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL,
  user_id INTEGER NOT NULL REFERENCES users(id), intent_id INTEGER REFERENCES deposit_intents(id),
  asset_network_id INTEGER NOT NULL REFERENCES asset_networks(id), network_id INTEGER NOT NULL REFERENCES blockchain_networks(id),
  company_wallet_id INTEGER NOT NULL REFERENCES company_crypto_wallets(id), destination_address TEXT NOT NULL,   -- snapshot of the address the customer was shown
  claimed_amount TEXT NOT NULL CHECK(claimed_amount NOT GLOB '*[^0-9]*' AND claimed_amount<>''), claimed_txid TEXT, txid_key TEXT, sender_address TEXT, user_note TEXT,
  screenshot_name TEXT, screenshot_mime TEXT, screenshot_size INTEGER, screenshot_sha256 TEXT, screenshot_original TEXT,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN('pending','approved','rejected','cancelled')),
  verified_amount TEXT, verification_method TEXT CHECK(verification_method IS NULL OR verification_method IN('chain','manual_attestation')), verification_detail TEXT NOT NULL DEFAULT '{}',
  admin_notes TEXT, rejection_reason TEXT, reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT,
  deposit_id INTEGER REFERENCES crypto_deposits(id), evidence_duplicate_of INTEGER REFERENCES deposit_requests(id),
  submitted_at TEXT NOT NULL, updated_at TEXT);
CREATE INDEX IF NOT EXISTS ix_dr_user ON deposit_requests(user_id, id);
CREATE INDEX IF NOT EXISTS ix_dr_status ON deposit_requests(status, id);
-- the same transaction cannot be claimed twice on one network while a request is live or approved (a rejected claim may be re-submitted)
CREATE UNIQUE INDEX IF NOT EXISTS ux_dr_txid ON deposit_requests(network_id, txid_key) WHERE txid_key IS NOT NULL AND status IN('pending','approved');
-- decisions are final: an approved/rejected/cancelled request can never be edited; corrections are separate, audited adjustments
CREATE TRIGGER IF NOT EXISTS dr_decided_final BEFORE UPDATE ON deposit_requests WHEN OLD.status<>'pending'
  BEGIN SELECT RAISE(ABORT,'a decided deposit request is final; use an audited adjustment'); END;
CREATE TRIGGER IF NOT EXISTS dr_evidence_immutable BEFORE UPDATE OF screenshot_name,screenshot_sha256,screenshot_mime,user_id,claimed_amount,claimed_txid,destination_address,asset_network_id ON deposit_requests
  BEGIN SELECT RAISE(ABORT,'submitted evidence and claim details are immutable'); END;
CREATE TRIGGER IF NOT EXISTS dr_no_delete BEFORE DELETE ON deposit_requests BEGIN SELECT RAISE(ABORT,'deposit requests are never deleted'); END;

CREATE TABLE IF NOT EXISTS deposit_request_events(
  id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES deposit_requests(id), event TEXT NOT NULL, actor_id INTEGER, note TEXT, created_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS dre_no_update BEFORE UPDATE ON deposit_request_events BEGIN SELECT RAISE(ABORT,'events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS dre_no_delete BEFORE DELETE ON deposit_request_events BEGIN SELECT RAISE(ABORT,'events cannot be deleted'); END;
