-- 007: Trade terminal: market favourites, coin->Vaulta asset map, market-data cache, community chat, moderation.

-- Favourites are keyed by the market-data provider's coin id (any listed coin), so they cannot reuse watchlist_items (FK to the internal assets registry).
CREATE TABLE coin_favorites(
  user_id INTEGER NOT NULL REFERENCES users(id),
  coin_id TEXT NOT NULL CHECK(length(coin_id) BETWEEN 1 AND 100),
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, coin_id)
);

-- Which market-data coins correspond to an asset that Vaulta can actually trade (internal ledger). Everything else is view-only.
CREATE TABLE market_asset_map(
  coin_id TEXT PRIMARY KEY,
  symbol TEXT NOT NULL REFERENCES assets(symbol)
);
INSERT OR IGNORE INTO market_asset_map(coin_id, symbol)
  SELECT m.coin_id, a.symbol FROM (SELECT 'bitcoin' coin_id,'BTC' symbol UNION ALL SELECT 'ethereum','ETH' UNION ALL SELECT 'solana','SOL'
         UNION ALL SELECT 'tether','USDT' UNION ALL SELECT 'usd-coin','USDC') m JOIN assets a ON a.symbol=m.symbol;

-- Last good provider responses, kept so the UI can show clearly-labelled stale data when the provider is unavailable.
CREATE TABLE market_cache(
  cache_key TEXT PRIMARY KEY,
  payload TEXT NOT NULL,
  fetched_at TEXT NOT NULL
);

CREATE TABLE chat_channels(
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE CHECK(length(slug) BETWEEN 2 AND 40 AND slug NOT GLOB '*[^a-z0-9-]*'),
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  kind TEXT NOT NULL DEFAULT 'topic' CHECK(kind IN('general','coin','topic','announcement')),
  coin_id TEXT,
  write_policy TEXT NOT NULL DEFAULT 'members' CHECK(write_policy IN('members','staff')),
  sort_order INTEGER NOT NULL DEFAULT 100,
  archived INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
INSERT INTO chat_channels(slug,name,description,kind,coin_id,write_policy,sort_order,created_at) VALUES
 ('general','General','Open discussion for the Vaulta community.','general',NULL,'members',1,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('bitcoin','Bitcoin','Everything BTC.','coin','bitcoin','members',2,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('ethereum','Ethereum','Everything ETH.','coin','ethereum','members',3,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('solana','Solana','Everything SOL.','coin','solana','members',4,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('altcoins','Altcoins','Discussion of every other coin.','topic',NULL,'members',5,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('market-analysis','Market Analysis','Charts, ideas and analysis. Not financial advice.','topic',NULL,'members',6,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('trading-discussion','Trading Discussion','Order types, strategy and platform questions.','topic',NULL,'members',7,strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 ('announcements','Announcements','Official Vaulta announcements. Read-only for members.','announcement',NULL,'staff',8,strftime('%Y-%m-%dT%H:%M:%SZ','now'));

CREATE TABLE chat_messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id INTEGER NOT NULL REFERENCES chat_channels(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  content TEXT NOT NULL CHECK(length(content) BETWEEN 1 AND 500),
  client_nonce TEXT,
  reply_to INTEGER REFERENCES chat_messages(id),
  status TEXT NOT NULL DEFAULT 'visible' CHECK(status IN('visible','removed')),
  removed_by INTEGER REFERENCES users(id),
  removed_reason TEXT,
  removed_at TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_chat_messages_channel ON chat_messages(channel_id, id);
CREATE INDEX ix_chat_messages_user ON chat_messages(user_id, created_at);
CREATE UNIQUE INDEX uq_chat_nonce ON chat_messages(user_id, client_nonce) WHERE client_nonce IS NOT NULL;
-- Message text is immutable once stored (moderators can only remove, never rewrite).
CREATE TRIGGER trg_chat_messages_immutable BEFORE UPDATE ON chat_messages
WHEN NEW.content IS NOT OLD.content OR NEW.user_id IS NOT OLD.user_id OR NEW.channel_id IS NOT OLD.channel_id OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT, 'chat messages are immutable'); END;
CREATE TRIGGER trg_chat_messages_nodelete BEFORE DELETE ON chat_messages BEGIN SELECT RAISE(ABORT, 'chat messages cannot be deleted; remove them instead'); END;

CREATE TABLE chat_reports(
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES chat_messages(id),
  reporter_id INTEGER NOT NULL REFERENCES users(id),
  reason TEXT NOT NULL CHECK(reason IN('spam','abuse','scam','impersonation','other')),
  details TEXT,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','actioned','dismissed')),
  resolved_by INTEGER REFERENCES users(id),
  resolved_at TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(message_id, reporter_id)
);
CREATE INDEX ix_chat_reports_status ON chat_reports(status, id);

CREATE TABLE chat_mutes(
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  channel_id INTEGER REFERENCES chat_channels(id),     -- NULL = muted everywhere
  until TEXT NOT NULL,
  reason TEXT,
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  revoked_at TEXT,
  revoked_by INTEGER REFERENCES users(id)
);
CREATE INDEX ix_chat_mutes_user ON chat_mutes(user_id, until);

CREATE TABLE chat_blocks(
  user_id INTEGER NOT NULL REFERENCES users(id),
  blocked_user_id INTEGER NOT NULL REFERENCES users(id),
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, blocked_user_id),
  CHECK(user_id != blocked_user_id)
);

-- Presence: a heartbeat row per user and channel; "online" means seen in the last ~45 seconds.
CREATE TABLE chat_presence(
  user_id INTEGER NOT NULL REFERENCES users(id),
  channel_id INTEGER NOT NULL REFERENCES chat_channels(id),
  last_seen TEXT NOT NULL,
  PRIMARY KEY(user_id, channel_id)
);
CREATE INDEX ix_chat_presence_channel ON chat_presence(channel_id, last_seen);
