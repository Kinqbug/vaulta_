"""VAULTA email communication and automation center.

    catalog     template definitions, supported variables, event labels (seeded into the database)
    render      template -> HTML + plain text on the shared layout; variable validation and escaping
    transports  dev / mock / Resend / SES delivery adapters behind one interface
    outbox      persistent queue: enqueue (idempotent), claim, deliver, retry, webhook status updates
    events      domain events -> emails (called from the real backend workflows, inside their transactions)
    prefs       consent, unsubscribe tokens, suppression
    segments    audience queries over the real schema
    campaigns   individual / selected / broadcast sends with approval
    reminders   scheduled, cancellable reminders
    routes      admin API, public unsubscribe, provider webhooks
    worker      `python -m server.mailer.worker`
"""
