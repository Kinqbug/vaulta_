import sqlite3, os, contextlib
from flask import g


def connect(path):
    if path != ':memory:': os.makedirs(os.path.dirname(path), exist_ok=True)
    c = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=15000')
    if path != ':memory:': c.execute('PRAGMA journal_mode=WAL')
    return c

def _migrate(c):
    """Rename the legacy per-row flag column to non_live on databases created before the rename."""
    for t in ('email_outbox', 'transactions', 'deposits', 'withdrawals', 'investments', 'orders'):
        cols = [r[1] for r in c.execute(f'PRAGMA table_info({t})')]
        if 'sandbox' in cols and 'non_live' not in cols:
            c.execute(f'ALTER TABLE {t} RENAME COLUMN sandbox TO non_live')
    c.execute("UPDATE assets SET provider='internal' WHERE provider='sandbox'")

def init_schema(c):
    """Apply versioned migrations (server/migrations). Legacy databases created before migrations existed are adopted by 001 (IF NOT EXISTS)."""
    from . import migrate
    migrate.run(c)
    try: _migrate(c)
    except Exception: pass

def get_db():
    from flask import current_app
    if 'db' not in g: g.db = connect(current_app.config['DATABASE_PATH'])
    return g.db

def close_db(_=None):
    d = g.pop('db', None)
    if d is not None: d.close()

@contextlib.contextmanager
def tx(db):
    """Serialised write transaction. Re-entrant: nested use joins the outer transaction."""
    if db.in_transaction:
        yield db; return
    db.execute('BEGIN IMMEDIATE')
    try:
        yield db
        db.execute('COMMIT')
    except BaseException:
        db.execute('ROLLBACK'); raise

def one(db, sql, *a): return db.execute(sql, a).fetchone()
def all_(db, sql, *a): return [dict(r) for r in db.execute(sql, a).fetchall()]
def val(db, sql, *a):
    r = db.execute(sql, a).fetchone(); return r[0] if r else None
