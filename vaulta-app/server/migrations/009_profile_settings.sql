-- 009_profile_settings: profile photo reference + per-user display preferences. Additive only (no existing rows are changed).
-- Avatar files live in the private upload directory under avatars/ with random server-generated names; the DB stores only the name.
ALTER TABLE user_profiles ADD COLUMN avatar_name TEXT;
ALTER TABLE user_profiles ADD COLUMN avatar_sha256 TEXT;
ALTER TABLE user_profiles ADD COLUMN avatar_version INTEGER NOT NULL DEFAULT 0;
-- Display preferences. Allowed language/currency values are enforced by the API (server/routes_settings.py) so new ones need no migration.
-- Changing currency is display-only: ledger amounts and transactions are never converted or touched.
CREATE TABLE IF NOT EXISTS user_settings(
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  language TEXT NOT NULL DEFAULT 'en',
  currency TEXT NOT NULL DEFAULT 'USD',
  theme TEXT NOT NULL DEFAULT 'light' CHECK(theme IN ('light','dark','system')),
  updated_at TEXT NOT NULL);
