"""Email worker. Production: run as its own process next to the web app.

    python -m server.mailer.worker                # loop forever
    python -m server.mailer.worker --once         # one pass (cron / tests)

Each pass: deliver due messages (claim -> send -> record), recover interrupted sends, refresh campaign status, and every few minutes run reminders.
Several workers may run at once: a message is claimed with a compare-and-set, so it is never sent twice by two workers.
"""
import sys, time, argparse, logging
from .. import db as dbm, util
from ..config import Config
from . import events, reminders, campaigns
from .outbox import Mailer
from .transports import build_transport

log = logging.getLogger('vaulta.mailer.worker')

def build(cfg=None):
    Config.validate(); cfg = cfg or Config
    return Mailer({k: getattr(cfg, k) for k in dir(cfg) if k.isupper()}, build_transport(cfg))

def one_pass(mailer, run_reminders=False):
    db = dbm.connect(mailer._c('DATABASE_PATH'))
    try:
        res = mailer.deliver_due(db, limit=50)
        campaigns.refresh_status(db)
        if run_reminders: res['reminders'] = reminders.run(db, mailer)
        return res
    finally: db.close()

def loop(mailer, interval=5, stop=lambda: False):
    last_rem = 0
    while not stop():
        try:
            due = time.time() - last_rem > 300
            res = one_pass(mailer, run_reminders=due)
            if due: last_rem = time.time()
            if res.get('processed') or res.get('recovered'): log.info('email worker: %s', {k: v for k, v in res.items() if k != 'reminders'})
        except Exception: log.exception('email worker pass failed')
        time.sleep(interval)

def start_background(mailer, interval=3):
    """Development convenience (run.py): same loop on a daemon thread."""
    import threading
    t = threading.Thread(target=loop, args=(mailer, interval), daemon=True, name='vaulta-email-worker'); t.start(); return t

if __name__ == '__main__':
    ap = argparse.ArgumentParser(); ap.add_argument('--once', action='store_true'); ap.add_argument('--interval', type=int, default=5); a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    m = build(); events.configure(m)
    st = m.status(); log.info('email worker starting: provider=%s live=%s env=%s', st['provider'], st['live_delivery'], st['environment'])
    if not st['live_delivery']: log.warning(st['warning'])
    if a.once: print(one_pass(m, run_reminders=True))
    else: loop(m, a.interval)
