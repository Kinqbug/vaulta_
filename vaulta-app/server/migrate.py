"""Versioned, checksummed SQL migrations (M1).

Files live in server/migrations/NNN_name.sql and are applied once, in order, each inside its own transaction.
The applied checksum is stored in schema_migrations; if an already-applied file is edited the app refuses to start
(released migrations are immutable: add a new one instead). The runner is deliberately small and dialect-neutral so the
same numbered revisions can be ported 1:1 to Alembic revisions on PostgreSQL (see docs/PRODUCTION_ROADMAP.md)."""
import os, re, sqlite3, hashlib
from . import util

DIR = os.path.join(os.path.dirname(__file__), 'migrations')
_NAME = re.compile(r'^(\d{3})_([a-z0-9_]+)\.sql$')

def discover(directory=DIR):
    out = []
    for f in sorted(os.listdir(directory)):
        m = _NAME.match(f)
        if m:
            with open(os.path.join(directory, f), encoding='utf-8') as fh: sql = fh.read()
            out.append((m.group(1), m.group(2), sql, hashlib.sha256(sql.encode()).hexdigest()))
    versions = [v for v, *_ in out]
    if len(set(versions)) != len(versions): raise RuntimeError('Duplicate migration version numbers')
    return out

def statements(sql):
    """Split a script into statements, keeping CREATE TRIGGER ... BEGIN ... END; bodies intact."""
    buf = ''
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            if buf.strip(): yield buf.strip()
            buf = ''
    if buf.strip() and not all(l.strip().startswith('--') or not l.strip() for l in buf.splitlines()):
        raise RuntimeError('Migration ends with an incomplete statement')

def run(c, directory=DIR):
    c.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version TEXT PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)')
    applied = {r[0]: r[2] for r in c.execute('SELECT version,name,checksum FROM schema_migrations')}
    ran = []
    for version, name, sql, checksum in discover(directory):
        if version in applied:
            if applied[version] != checksum:
                raise RuntimeError(f'Migration {version}_{name} was modified after it was applied. Released migrations are immutable; add a new migration.')
            continue
        c.execute('BEGIN IMMEDIATE')
        try:
            # another process may have applied it while we waited for the write lock
            if c.execute('SELECT 1 FROM schema_migrations WHERE version=?', (version,)).fetchone():
                c.execute('ROLLBACK'); continue
            for stmt in statements(sql): c.execute(stmt)
            c.execute('INSERT INTO schema_migrations(version,name,checksum,applied_at) VALUES(?,?,?,?)', (version, name, checksum, util.now()))
            c.execute('COMMIT')
        except BaseException:
            c.execute('ROLLBACK'); raise
        ran.append(version)
    return ran
