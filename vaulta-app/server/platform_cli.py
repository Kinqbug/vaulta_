"""Emergency switch control that does not need a login:  python -m server.platform_cli maintenance off
Keys: maintenance | registration | live-prices | require-2fa.  Every use is written to the audit log (actor: none, reason: cli)."""
import sys
from .config import Config
from . import db as dbm, util, rbac
from .audit import audit

KEYS = {'maintenance': 'maintenance_mode', 'registration': 'registration_enabled', 'live-prices': 'live_prices_enabled', 'require-2fa': 'require_2fa_all'}

def main(argv):
    if len(argv) != 2 or argv[0] not in KEYS or argv[1] not in ('on', 'off'):
        print(__doc__); return 2
    key, v = KEYS[argv[0]], '1' if argv[1] == 'on' else '0'
    c = dbm.connect(Config.DATABASE_PATH); rbac.ensure_rbac(c)
    old = c.execute('SELECT value FROM system_settings WHERE key=?', (key,)).fetchone()[0]
    c.execute('UPDATE system_settings SET value=?,updated_by=NULL,updated_at=? WHERE key=?', (v, util.now(), key))
    audit(c, None, 'setting.changed', 'setting', key, {'value': old}, {'value': v}, 'cli'); c.commit()
    print(f'{key}: {old} -> {v}'); return 0

if __name__ == '__main__': sys.exit(main(sys.argv[1:]))
