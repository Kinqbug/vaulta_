-- 001_initial: baseline VAULTA schema (SQLite dialect; relational design ports to PostgreSQL). Never edit once released: add a new migration.
-- Money = integer cents. Quantities/prices = TEXT decimals handled with Python Decimal.
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, public_id TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL COLLATE NOCASE, phone TEXT,
  password_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','restricted','suspended','closed')),
  email_verified INTEGER NOT NULL DEFAULT 0, kyc_status TEXT NOT NULL DEFAULT 'not_started',
  risk_profile TEXT, failed_logins INTEGER NOT NULL DEFAULT 0, locked_until TEXT,
  totp_secret TEXT, totp_enabled INTEGER NOT NULL DEFAULT 0, totp_last_step INTEGER NOT NULL DEFAULT 0, withdrawal_2fa INTEGER NOT NULL DEFAULT 1,
  is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, last_login_at TEXT);
CREATE TABLE IF NOT EXISTS user_profiles(
  user_id INTEGER PRIMARY KEY REFERENCES users(id), first_name TEXT, last_name TEXT, country TEXT, date_of_birth TEXT,
  employment TEXT, income_range TEXT, investment_goal TEXT, investment_horizon TEXT, risk_tolerance TEXT);
CREATE TABLE IF NOT EXISTS roles(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT);
CREATE TABLE IF NOT EXISTS permissions(id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, description TEXT);
CREATE TABLE IF NOT EXISTS role_permissions(role_id INTEGER REFERENCES roles(id), permission_id INTEGER REFERENCES permissions(id), PRIMARY KEY(role_id,permission_id));
CREATE TABLE IF NOT EXISTS user_roles(user_id INTEGER REFERENCES users(id), role_id INTEGER REFERENCES roles(id), granted_by INTEGER, granted_at TEXT, PRIMARY KEY(user_id,role_id));
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), token_hash TEXT UNIQUE NOT NULL, csrf_token TEXT NOT NULL,
  created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, expires_at TEXT NOT NULL, ip TEXT, user_agent TEXT, revoked_at TEXT);
CREATE TABLE IF NOT EXISTS security_events(
  id INTEGER PRIMARY KEY, user_id INTEGER, type TEXT NOT NULL, ip TEXT, user_agent TEXT, detail TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_secev_user ON security_events(user_id, created_at);
CREATE TABLE IF NOT EXISTS email_tokens(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL, expires_at TEXT NOT NULL, used_at TEXT);
CREATE TABLE IF NOT EXISTS email_outbox(id INTEGER PRIMARY KEY, user_id INTEGER, to_email TEXT, subject TEXT, body TEXT, non_live INTEGER DEFAULT 1, created_at TEXT);

CREATE TABLE IF NOT EXISTS accounts(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), kind TEXT NOT NULL CHECK(kind IN('cash','savings','retirement')),
  name TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'USD', apy_bps INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_acct_user ON accounts(user_id, kind);

CREATE TABLE IF NOT EXISTS assets(
  id INTEGER PRIMARY KEY, symbol TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  asset_class TEXT NOT NULL CHECK(asset_class IN('Stocks','ETFs','Bonds','Crypto','Real Estate','Commodities')),
  provider TEXT DEFAULT 'internal', external_id TEXT, currency TEXT DEFAULT 'USD',
  last_price TEXT NOT NULL, prev_close TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','halted','archived')),
  tradable INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, price_updated_at TEXT);
CREATE TABLE IF NOT EXISTS asset_prices(asset_id INTEGER NOT NULL REFERENCES assets(id), ts TEXT NOT NULL, price TEXT NOT NULL, PRIMARY KEY(asset_id, ts));
CREATE TABLE IF NOT EXISTS investment_products(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL, asset_id INTEGER NOT NULL REFERENCES assets(id),
  category TEXT NOT NULL, kind TEXT, issuer TEXT, risk TEXT NOT NULL CHECK(risk IN('Low','Moderate','High')), min_investment_cents INTEGER NOT NULL,
  fee_bps INTEGER NOT NULL, illustrative_return_pct TEXT, horizon TEXT, description TEXT, score INTEGER DEFAULT 5,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','paused','archived')), documents TEXT DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT);

CREATE TABLE IF NOT EXISTS portfolios(id INTEGER PRIMARY KEY, user_id INTEGER UNIQUE NOT NULL REFERENCES users(id), name TEXT DEFAULT 'Main portfolio', created_at TEXT);
CREATE TABLE IF NOT EXISTS portfolio_holdings(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id), asset_id INTEGER NOT NULL REFERENCES assets(id),
  quantity TEXT NOT NULL, cost_basis_cents INTEGER NOT NULL, realized_pl_cents INTEGER NOT NULL DEFAULT 0, acquired_at TEXT, updated_at TEXT, UNIQUE(user_id, asset_id));
CREATE TABLE IF NOT EXISTS holding_movements(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, asset_id INTEGER NOT NULL, qty_delta TEXT NOT NULL, price TEXT NOT NULL, ts TEXT NOT NULL, source TEXT, ref TEXT);
CREATE INDEX IF NOT EXISTS ix_hm_user ON holding_movements(user_id, ts);
CREATE TABLE IF NOT EXISTS portfolio_snapshots(user_id INTEGER NOT NULL, day TEXT NOT NULL, value_cents INTEGER NOT NULL, PRIMARY KEY(user_id, day));

CREATE TABLE IF NOT EXISTS transactions(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id), account_id INTEGER NOT NULL REFERENCES accounts(id),
  type TEXT NOT NULL CHECK(type IN('deposit','withdrawal','investment','trade','fee','interest','transfer','refund','adjustment')),
  direction TEXT NOT NULL CHECK(direction IN('credit','debit')), amount_cents INTEGER NOT NULL CHECK(amount_cents>0), currency TEXT NOT NULL DEFAULT 'USD',
  status TEXT NOT NULL CHECK(status IN('pending','processing','completed','failed','cancelled','reversed')),
  reference TEXT, idempotency_key TEXT, metadata TEXT DEFAULT '{}', non_live INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_txn_idem ON transactions(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_txn_user ON transactions(user_id, created_at);
CREATE INDEX IF NOT EXISTS ix_txn_acct ON transactions(account_id, status);
CREATE TABLE IF NOT EXISTS transaction_fees(id INTEGER PRIMARY KEY, transaction_id INTEGER NOT NULL REFERENCES transactions(id), amount_cents INTEGER NOT NULL, kind TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS idempotency_keys(user_id INTEGER, key TEXT, scope TEXT, response TEXT, created_at TEXT, PRIMARY KEY(user_id,key,scope));

CREATE TABLE IF NOT EXISTS deposits(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, txn_id INTEGER, amount_cents INTEGER NOT NULL, currency TEXT DEFAULT 'USD',
  method TEXT NOT NULL, provider TEXT NOT NULL, external_ref TEXT, internal_ref TEXT, fee_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK(status IN('pending','processing','completed','failed','reversed')), non_live INTEGER DEFAULT 1, created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE IF NOT EXISTS withdrawals(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, txn_id INTEGER, amount_cents INTEGER NOT NULL, currency TEXT DEFAULT 'USD',
  method TEXT NOT NULL, destination TEXT NOT NULL, provider_ref TEXT,
  status TEXT NOT NULL CHECK(status IN('pending','held_for_review','approved','processing','completed','rejected','failed','cancelled')),
  review_reason TEXT, reviewed_by INTEGER, reviewed_at TEXT, non_live INTEGER DEFAULT 1, created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE IF NOT EXISTS investments(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, product_id INTEGER NOT NULL, asset_id INTEGER NOT NULL, txn_id INTEGER,
  amount_cents INTEGER NOT NULL, fee_cents INTEGER NOT NULL, quantity TEXT, unit_price TEXT,
  status TEXT NOT NULL CHECK(status IN('pending','processing','completed','failed','cancelled','reversed')), non_live INTEGER DEFAULT 1, created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE IF NOT EXISTS orders(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, asset_id INTEGER NOT NULL, side TEXT NOT NULL CHECK(side IN('buy','sell')),
  order_type TEXT NOT NULL CHECK(order_type IN('market','limit','stop','stop_limit')), quantity TEXT NOT NULL, filled_qty TEXT NOT NULL DEFAULT '0',
  limit_price TEXT, stop_price TEXT, triggered INTEGER NOT NULL DEFAULT 0, execution_price TEXT, fee_cents INTEGER NOT NULL DEFAULT 0, reserved_cents INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL CHECK(status IN('pending','open','partially_filled','filled','cancelled','rejected','failed')), reason TEXT, idempotency_key TEXT, non_live INTEGER DEFAULT 1,
  created_at TEXT NOT NULL, executed_at TEXT, cancelled_at TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS ux_ord_idem ON orders(user_id, idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE TABLE IF NOT EXISTS order_executions(id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, quantity TEXT NOT NULL, price TEXT NOT NULL, fee_cents INTEGER, ts TEXT, provider TEXT, provider_ref TEXT);

CREATE TABLE IF NOT EXISTS savings_goals(
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, account_id INTEGER NOT NULL, name TEXT NOT NULL, category TEXT DEFAULT 'Custom', target_cents INTEGER NOT NULL, current_cents INTEGER NOT NULL DEFAULT 0,
  target_date TEXT, frequency TEXT, contribution_cents INTEGER, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','completed','paused','cancelled')), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS automatic_contributions(
  id INTEGER PRIMARY KEY, goal_id INTEGER NOT NULL, user_id INTEGER NOT NULL, frequency TEXT NOT NULL CHECK(frequency IN('weekly','biweekly','monthly')),
  amount_cents INTEGER NOT NULL, next_run TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, last_result TEXT);
CREATE TABLE IF NOT EXISTS retirement_plans(
  user_id INTEGER PRIMARY KEY, current_age INTEGER, retirement_age INTEGER, current_savings_cents INTEGER, monthly_contribution_cents INTEGER,
  employer_match_pct TEXT DEFAULT '0', expected_return_pct TEXT, target_cents INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS watchlist_items(user_id INTEGER NOT NULL, asset_id INTEGER NOT NULL, added_at TEXT, PRIMARY KEY(user_id,asset_id));

CREATE TABLE IF NOT EXISTS kyc_profiles(
  user_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'not_started' CHECK(status IN('not_started','pending','under_review','verified','rejected','requires_info')),
  legal_name TEXT, date_of_birth TEXT, country TEXT, address TEXT, submitted_at TEXT, reviewed_by INTEGER, reviewed_at TEXT, rejection_reason TEXT, info_request TEXT);
CREATE TABLE IF NOT EXISTS kyc_documents(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, doc_type TEXT NOT NULL, stored_name TEXT NOT NULL, original_name TEXT, mime TEXT, size INTEGER, sha256 TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS kyc_reviews(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, reviewer_id INTEGER, decision TEXT NOT NULL, reason TEXT, created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, type TEXT NOT NULL, title TEXT NOT NULL, body TEXT, link TEXT, read_at TEXT, deleted_at TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_notif_user ON notifications(user_id, created_at);
CREATE TABLE IF NOT EXISTS notification_preferences(user_id INTEGER NOT NULL, type TEXT NOT NULL, in_app INTEGER DEFAULT 1, email INTEGER DEFAULT 0, PRIMARY KEY(user_id,type));
CREATE TABLE IF NOT EXISTS support_tickets(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, user_id INTEGER NOT NULL, category TEXT NOT NULL, subject TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','in_progress','waiting_for_user','resolved','closed')), priority TEXT DEFAULT 'normal' CHECK(priority IN('low','normal','high','urgent')),
  assigned_to INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS support_messages(id INTEGER PRIMARY KEY, ticket_id INTEGER NOT NULL, author_id INTEGER NOT NULL, is_staff INTEGER DEFAULT 0, internal INTEGER DEFAULT 0, body TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS articles(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, title TEXT NOT NULL, category TEXT NOT NULL CHECK(category IN('article','guide','market_insight','faq','education','announcement')),
  summary TEXT, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN('draft','published','scheduled','archived')), publish_at TEXT, author_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_logs(
  id INTEGER PRIMARY KEY, actor_id INTEGER, action TEXT NOT NULL, entity TEXT, entity_id TEXT, prev_value TEXT, new_value TEXT, reason TEXT, ip TEXT, user_agent TEXT,
  created_at TEXT NOT NULL, prev_hash TEXT, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS system_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL, description TEXT, updated_by INTEGER, updated_at TEXT);

-- ===== Integrity triggers: the ledger and the audit log are append-only =====
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_logs BEGIN SELECT RAISE(ABORT,'audit_logs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_logs BEGIN SELECT RAISE(ABORT,'audit_logs is append-only'); END;
CREATE TRIGGER IF NOT EXISTS secev_no_update BEFORE UPDATE ON security_events BEGIN SELECT RAISE(ABORT,'security_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS secev_no_delete BEFORE DELETE ON security_events BEGIN SELECT RAISE(ABORT,'security_events is append-only'); END;
CREATE TRIGGER IF NOT EXISTS txn_immutable BEFORE UPDATE OF ref,user_id,account_id,type,direction,amount_cents,currency,created_at ON transactions BEGIN SELECT RAISE(ABORT,'ledger fields are immutable; post an adjustment instead'); END;
CREATE TRIGGER IF NOT EXISTS txn_no_delete BEFORE DELETE ON transactions BEGIN SELECT RAISE(ABORT,'ledger rows cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS fee_no_update BEFORE UPDATE ON transaction_fees BEGIN SELECT RAISE(ABORT,'fees are immutable'); END;
CREATE TRIGGER IF NOT EXISTS exec_no_update BEFORE UPDATE ON order_executions BEGIN SELECT RAISE(ABORT,'executions are immutable'); END;
