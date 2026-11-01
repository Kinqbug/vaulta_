-- 002_general_ledger: double-entry general ledger alongside the customer subledger (transactions table).
-- Amounts are integer minor units of the line's asset_code, stored as TEXT so 18-decimal crypto quantities never overflow
-- (SQLite integers are 64-bit; PostgreSQL port: NUMERIC(78,0)). Sums are done in Python / NUMERIC, never with SQL SUM on TEXT.
CREATE TABLE IF NOT EXISTS accounting_periods(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, starts_on TEXT NOT NULL, ends_on TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN('open','closed')), closed_by INTEGER, closed_at TEXT);
CREATE TABLE IF NOT EXISTS gl_accounts(
  id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  type TEXT NOT NULL CHECK(type IN('asset','liability','equity','income','expense')),
  normal_side TEXT NOT NULL CHECK(normal_side IN('debit','credit')),
  is_system INTEGER NOT NULL DEFAULT 0, owner_user_id INTEGER, owner_account_id INTEGER UNIQUE,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','closed')), created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS journal_entries(
  id INTEGER PRIMARY KEY, ref TEXT UNIQUE NOT NULL, idempotency_key TEXT UNIQUE, entry_type TEXT NOT NULL, description TEXT,
  source_type TEXT, source_ref TEXT, period_id INTEGER NOT NULL REFERENCES accounting_periods(id),
  reverses_entry_id INTEGER UNIQUE REFERENCES journal_entries(id), created_by INTEGER,
  line_count INTEGER NOT NULL CHECK(line_count>=2), sealed INTEGER NOT NULL DEFAULT 0, posted_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_je_source ON journal_entries(source_type, source_ref);
CREATE TABLE IF NOT EXISTS journal_lines(
  id INTEGER PRIMARY KEY, entry_id INTEGER NOT NULL REFERENCES journal_entries(id), line_no INTEGER NOT NULL,
  gl_account_id INTEGER NOT NULL REFERENCES gl_accounts(id), asset_code TEXT NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN('debit','credit')), amount TEXT NOT NULL CHECK(amount GLOB '[1-9]*' AND amount NOT GLOB '*[^0-9]*'),
  UNIQUE(entry_id, line_no));
CREATE INDEX IF NOT EXISTS ix_jl_account ON journal_lines(gl_account_id, asset_code);
-- Maintained only by the posting engine; verify_integrity() recomputes it from journal_lines. Stored as signed (debits - credits).
CREATE TABLE IF NOT EXISTS account_balances(
  gl_account_id INTEGER NOT NULL REFERENCES gl_accounts(id), asset_code TEXT NOT NULL, balance TEXT NOT NULL, last_line_id INTEGER, updated_at TEXT,
  PRIMARY KEY(gl_account_id, asset_code));

CREATE TRIGGER IF NOT EXISTS je_period_open BEFORE INSERT ON journal_entries
  WHEN (SELECT status FROM accounting_periods WHERE id=NEW.period_id)<>'open'
  BEGIN SELECT RAISE(ABORT,'accounting period is closed'); END;
CREATE TRIGGER IF NOT EXISTS je_insert_unsealed BEFORE INSERT ON journal_entries WHEN NEW.sealed<>0
  BEGIN SELECT RAISE(ABORT,'entries are created unsealed and sealed after their lines are written'); END;
CREATE TRIGGER IF NOT EXISTS je_seal_only BEFORE UPDATE ON journal_entries
  WHEN NOT (OLD.sealed=0 AND NEW.sealed=1 AND OLD.id=NEW.id AND OLD.ref=NEW.ref AND OLD.entry_type=NEW.entry_type AND OLD.period_id=NEW.period_id
            AND OLD.posted_at=NEW.posted_at AND OLD.line_count=NEW.line_count AND OLD.created_at=NEW.created_at
            AND OLD.idempotency_key IS NEW.idempotency_key AND OLD.reverses_entry_id IS NEW.reverses_entry_id)
  BEGIN SELECT RAISE(ABORT,'journal entries are immutable once posted; post a reversal instead'); END;
CREATE TRIGGER IF NOT EXISTS je_seal_complete BEFORE UPDATE OF sealed ON journal_entries
  WHEN NEW.sealed=1 AND (SELECT COUNT(*) FROM journal_lines WHERE entry_id=OLD.id)<>OLD.line_count
  BEGIN SELECT RAISE(ABORT,'entry line count mismatch'); END;
CREATE TRIGGER IF NOT EXISTS je_no_delete BEFORE DELETE ON journal_entries BEGIN SELECT RAISE(ABORT,'journal entries cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS jl_no_add_to_sealed BEFORE INSERT ON journal_lines
  WHEN (SELECT sealed FROM journal_entries WHERE id=NEW.entry_id)<>0 BEGIN SELECT RAISE(ABORT,'cannot add lines to a posted entry'); END;
CREATE TRIGGER IF NOT EXISTS jl_no_update BEFORE UPDATE ON journal_lines BEGIN SELECT RAISE(ABORT,'journal lines are immutable'); END;
CREATE TRIGGER IF NOT EXISTS jl_no_delete BEFORE DELETE ON journal_lines BEGIN SELECT RAISE(ABORT,'journal lines cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS gl_no_delete BEFORE DELETE ON gl_accounts BEGIN SELECT RAISE(ABORT,'GL accounts cannot be deleted; close them'); END;
CREATE TRIGGER IF NOT EXISTS gl_identity_immutable BEFORE UPDATE OF code,type,normal_side,owner_account_id ON gl_accounts BEGIN SELECT RAISE(ABORT,'GL account identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS ap_no_delete BEFORE DELETE ON accounting_periods BEGIN SELECT RAISE(ABORT,'accounting periods cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS ap_closed_final BEFORE UPDATE ON accounting_periods WHEN OLD.status='closed' BEGIN SELECT RAISE(ABORT,'closed periods cannot be changed'); END;
