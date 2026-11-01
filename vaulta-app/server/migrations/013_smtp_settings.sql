-- SMTP host configuration for the Email Center. One row (id=1). The password is stored ONLY as an authenticated-encryption token
-- (Fernet, key from SETTINGS_ENCRYPTION_KEY, which lives outside the database and source tree). It is never returned by any API.
CREATE TABLE IF NOT EXISTS email_smtp_settings(
  id INTEGER PRIMARY KEY CHECK (id = 1),
  enabled INTEGER NOT NULL DEFAULT 0,
  host TEXT NOT NULL DEFAULT '',
  port INTEGER NOT NULL DEFAULT 587,
  security TEXT NOT NULL DEFAULT 'starttls' CHECK (security IN ('starttls','ssl','none')),
  auth_method TEXT NOT NULL DEFAULT 'login' CHECK (auth_method IN ('login','none')),
  username TEXT NOT NULL DEFAULT '',
  password_enc TEXT,
  from_address TEXT NOT NULL DEFAULT '',
  from_name TEXT NOT NULL DEFAULT '',
  reply_to TEXT NOT NULL DEFAULT '',
  public_base_url TEXT NOT NULL DEFAULT '',
  default_language TEXT NOT NULL DEFAULT 'en',
  default_timezone TEXT NOT NULL DEFAULT 'UTC',
  updated_by INTEGER,
  updated_at TEXT,
  last_test_at TEXT,
  last_test_ok INTEGER,
  last_test_error TEXT,
  last_send_ok_at TEXT,
  last_send_error_at TEXT,
  last_send_error TEXT
);
