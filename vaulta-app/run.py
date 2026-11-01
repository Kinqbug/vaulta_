"""Development entry point: python run.py   (use a production WSGI server such as gunicorn in production, plus `python -m server.mailer.worker`)"""
import os
from server.app import create_app
app = create_app()
if __name__ == '__main__':
    # Development convenience: drain the email queue in a background thread so verification emails show up at once.
    # In production this must be the separate worker process; create_app() never starts threads.
    if app.config.get('EMAIL_INLINE_WORKER') and app.config.get('ENV') == 'development':
        from server.mailer import worker, events
        events.configure(app.mailer); worker.start_background(app.mailer)
        print('[email] development worker started (provider=%s: %s)' % (app.mailer.transport.name, app.mailer.status()['warning'] or 'live delivery'))
    app.run(host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '5000')), debug=False)
