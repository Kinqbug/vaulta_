-- 010: administrator-configurable content for the separate Deposit and Withdraw pages.
-- Everything customers read on those pages (arrival and processing text, instructions, notices, logo alt text) is DATA in the registry, not frontend code.
-- All columns are nullable: an empty value falls back to a safe, generic default computed by the server. Existing rows and ledger identities are untouched.

ALTER TABLE asset_networks ADD COLUMN estimated_arrival TEXT;          -- e.g. '1-3 network confirmations'. An estimate, never a guarantee.
ALTER TABLE asset_networks ADD COLUMN processing_time TEXT;            -- withdrawal processing description, e.g. 'Reviewed by our team, usually within 24 hours'
ALTER TABLE asset_networks ADD COLUMN deposit_instructions TEXT;       -- extra deposit guidance for this asset on this network
ALTER TABLE asset_networks ADD COLUMN withdrawal_instructions TEXT;    -- extra withdrawal guidance for this asset on this network
ALTER TABLE asset_networks ADD COLUMN network_notice TEXT;             -- network-specific warning shown on both pages
ALTER TABLE crypto_assets ADD COLUMN logo_alt TEXT;                    -- accessible alternative text for the logo (defaults to '<name> logo')
ALTER TABLE crypto_assets ADD COLUMN notice TEXT;                      -- asset-specific notice shown on both pages
