-- Block-explorer base URL per blockchain network (shown on Asset Settings and used for transaction links). NULL = not configured.
ALTER TABLE blockchain_networks ADD COLUMN explorer_url TEXT;
