-- 008_email_center: real email delivery pipeline. Extends the existing email_outbox (it was a write-only log; nothing ever delivered it)
-- and adds templates, versions, automation rules, attempts, provider events, suppression, consent, campaigns and reminders.
-- Never edit once released: add a new migration.

-- ===== 1. Extend the existing outbox in place. Pre-existing rows were never sent: they are labelled legacy_unsent and can never be delivered. =====
ALTER TABLE email_outbox ADD COLUMN status TEXT NOT NULL DEFAULT 'legacy_unsent';
ALTER TABLE email_outbox ADD COLUMN mail_class TEXT;
ALTER TABLE email_outbox ADD COLUMN template_id TEXT;
ALTER TABLE email_outbox ADD COLUMN template_version INTEGER;
ALTER TABLE email_outbox ADD COLUMN event_type TEXT;
ALTER TABLE email_outbox ADD COLUMN entity_ref TEXT;
ALTER TABLE email_outbox ADD COLUMN idempotency_key TEXT;
ALTER TABLE email_outbox ADD COLUMN context_json TEXT;
ALTER TABLE email_outbox ADD COLUMN secret_json TEXT;          -- one-time links; scrubbed (set NULL) once the message reaches any final state; never returned by any API
ALTER TABLE email_outbox ADD COLUMN overrides_json TEXT;       -- administrator-written content (plain text only)
ALTER TABLE email_outbox ADD COLUMN next_attempt_at TEXT;
ALTER TABLE email_outbox ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE email_outbox ADD COLUMN max_attempts INTEGER NOT NULL DEFAULT 6;
ALTER TABLE email_outbox ADD COLUMN locked_until TEXT;
ALTER TABLE email_outbox ADD COLUMN provider TEXT;
ALTER TABLE email_outbox ADD COLUMN provider_message_id TEXT;
ALTER TABLE email_outbox ADD COLUMN accepted_at TEXT;
ALTER TABLE email_outbox ADD COLUMN delivered_at TEXT;
ALTER TABLE email_outbox ADD COLUMN last_error TEXT;
ALTER TABLE email_outbox ADD COLUMN campaign_id INTEGER;
ALTER TABLE email_outbox ADD COLUMN created_by INTEGER;
ALTER TABLE email_outbox ADD COLUMN updated_at TEXT;
-- Legacy rows stored one-time verification/reset tokens in plaintext. They were never delivered; remove the bodies that carry them.
UPDATE email_outbox SET body='[removed: legacy body contained a one-time link]' WHERE body LIKE '%token=%';

CREATE UNIQUE INDEX ux_outbox_idem ON email_outbox(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX ix_outbox_due ON email_outbox(status, next_attempt_at);
CREATE INDEX ix_outbox_user ON email_outbox(user_id, created_at);
CREATE INDEX ix_outbox_provider_msg ON email_outbox(provider_message_id);
CREATE INDEX ix_outbox_campaign ON email_outbox(campaign_id);
CREATE INDEX ix_outbox_template ON email_outbox(template_id, created_at);

CREATE TRIGGER trg_outbox_status_ins BEFORE INSERT ON email_outbox
WHEN NEW.status NOT IN ('legacy_unsent','queued','sending','retry','accepted','delivered','delayed','bounced','complained','failed','suppressed','blocked','cancelled','dev_captured')
BEGIN SELECT RAISE(ABORT,'invalid email status'); END;
CREATE TRIGGER trg_outbox_status_upd BEFORE UPDATE OF status ON email_outbox
WHEN NEW.status NOT IN ('legacy_unsent','queued','sending','retry','accepted','delivered','delayed','bounced','complained','failed','suppressed','blocked','cancelled','dev_captured')
BEGIN SELECT RAISE(ABORT,'invalid email status'); END;
-- A legacy row can never become deliverable.
CREATE TRIGGER trg_outbox_legacy_final BEFORE UPDATE OF status ON email_outbox
WHEN OLD.status='legacy_unsent' AND NEW.status<>'legacy_unsent'
BEGIN SELECT RAISE(ABORT,'legacy outbox rows were never sent and cannot be delivered'); END;

-- ===== 2. Templates and immutable versions =====
CREATE TABLE email_templates(
  id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  mail_class TEXT NOT NULL CHECK(mail_class IN('transactional','security','service','marketing','admin')),
  trigger_event TEXT,                                   -- NULL = no automated trigger exists in the backend
  manual_send INTEGER NOT NULL DEFAULT 0 CHECK(manual_send IN(0,1)),
  pref_gated INTEGER NOT NULL DEFAULT 0 CHECK(pref_gated IN(0,1)),
  is_system INTEGER NOT NULL DEFAULT 1 CHECK(is_system IN(0,1)),
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN('active','inactive')),
  current_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  updated_by INTEGER REFERENCES users(id)
);
CREATE INDEX ix_etpl_category ON email_templates(category, status);

CREATE TABLE email_template_versions(
  id INTEGER PRIMARY KEY,
  template_id TEXT NOT NULL REFERENCES email_templates(id),
  version INTEGER NOT NULL,
  state TEXT NOT NULL CHECK(state IN('draft','published','superseded')),
  subject TEXT NOT NULL,
  spec TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  note TEXT,
  created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
  published_by INTEGER REFERENCES users(id), published_at TEXT,
  UNIQUE(template_id, version)
);
CREATE UNIQUE INDEX ux_etv_one_draft ON email_template_versions(template_id) WHERE state='draft';
CREATE TRIGGER trg_etv_immutable BEFORE UPDATE OF subject,spec,version,template_id ON email_template_versions
WHEN OLD.state<>'draft' BEGIN SELECT RAISE(ABORT,'published template versions are immutable'); END;
CREATE TRIGGER trg_etv_nodelete BEFORE DELETE ON email_template_versions
WHEN OLD.state<>'draft' BEGIN SELECT RAISE(ABORT,'published template versions cannot be deleted'); END;

-- ===== 3. Automation rules (event -> template) and reminder rules =====
CREATE TABLE email_automation_rules(
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN('event','reminder')),
  event_type TEXT NOT NULL,
  template_id TEXT NOT NULL REFERENCES email_templates(id),
  name TEXT NOT NULL, description TEXT,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),
  delay_minutes INTEGER NOT NULL DEFAULT 0 CHECK(delay_minutes>=0),
  params TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL, updated_by INTEGER REFERENCES users(id)
);
CREATE INDEX ix_ear_event ON email_automation_rules(event_type, enabled);

-- ===== 4. Delivery attempts and provider events =====
CREATE TABLE email_attempts(
  id INTEGER PRIMARY KEY,
  email_id INTEGER NOT NULL REFERENCES email_outbox(id),
  attempt_no INTEGER NOT NULL,
  transport TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT,
  outcome TEXT NOT NULL CHECK(outcome IN('accepted','captured','retryable_error','permanent_error','blocked','suppressed','unknown')),
  http_status INTEGER, error_code TEXT, error_message TEXT, provider_message_id TEXT
);
CREATE INDEX ix_eatt_email ON email_attempts(email_id, attempt_no);
CREATE TRIGGER trg_eatt_nodelete BEFORE DELETE ON email_attempts BEGIN SELECT RAISE(ABORT,'delivery attempts are append-only'); END;

CREATE TABLE email_events(
  id INTEGER PRIMARY KEY,
  email_id INTEGER REFERENCES email_outbox(id),
  provider TEXT NOT NULL,
  provider_event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  occurred_at TEXT, received_at TEXT NOT NULL,
  detail TEXT,
  applied INTEGER NOT NULL DEFAULT 0,
  UNIQUE(provider, provider_event_id)                    -- webhook replays are no-ops
);
CREATE INDEX ix_eev_email ON email_events(email_id);
CREATE TRIGGER trg_eev_nodelete BEFORE DELETE ON email_events BEGIN SELECT RAISE(ABORT,'provider events are append-only'); END;

-- ===== 5. Suppression, preferences and consent =====
CREATE TABLE email_suppressions(
  id INTEGER PRIMARY KEY,
  email_key TEXT NOT NULL,                              -- lower-cased address
  scope TEXT NOT NULL CHECK(scope IN('all','marketing')),
  reason TEXT NOT NULL CHECK(reason IN('hard_bounce','complaint','unsubscribe','manual','invalid_address')),
  source TEXT, email_id INTEGER REFERENCES email_outbox(id),
  created_at TEXT NOT NULL, created_by INTEGER REFERENCES users(id),
  released_at TEXT, released_by INTEGER REFERENCES users(id), release_reason TEXT
);
CREATE UNIQUE INDEX ux_supp_active ON email_suppressions(email_key, scope) WHERE released_at IS NULL;

CREATE TABLE communication_preferences(
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  marketing_opt_in INTEGER NOT NULL DEFAULT 0 CHECK(marketing_opt_in IN(0,1)),     -- opt-IN only: the default is no marketing email
  marketing_changed_at TEXT, marketing_source TEXT,
  timezone TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE communication_consent_log(
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  channel TEXT NOT NULL CHECK(channel IN('email_marketing')),
  action TEXT NOT NULL CHECK(action IN('opt_in','opt_out')),
  source TEXT NOT NULL, ip TEXT, created_at TEXT NOT NULL
);
CREATE INDEX ix_ccl_user ON communication_consent_log(user_id, id);
CREATE TRIGGER trg_ccl_noupdate BEFORE UPDATE ON communication_consent_log BEGIN SELECT RAISE(ABORT,'consent records are append-only'); END;
CREATE TRIGGER trg_ccl_nodelete BEFORE DELETE ON communication_consent_log BEGIN SELECT RAISE(ABORT,'consent records are append-only'); END;

-- ===== 6. Campaigns =====
CREATE TABLE email_campaigns(
  id INTEGER PRIMARY KEY,
  ref TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN('individual','selected','broadcast')),
  template_id TEXT NOT NULL REFERENCES email_templates(id),
  mail_class TEXT NOT NULL CHECK(mail_class IN('service','marketing','admin')),
  audience TEXT NOT NULL,                               -- JSON: {segment, user_ids}
  overrides TEXT NOT NULL DEFAULT '{}',
  variables TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL CHECK(status IN('draft','pending_approval','approved','scheduled','sending','completed','cancelled','failed')),
  estimated_count INTEGER, final_count INTEGER,
  scheduled_at TEXT,
  created_by INTEGER NOT NULL REFERENCES users(id),
  submitted_at TEXT, approved_by INTEGER REFERENCES users(id), approved_at TEXT,
  launched_at TEXT, completed_at TEXT, cancelled_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX ix_ecamp_status ON email_campaigns(status, created_at);
CREATE TABLE email_campaign_recipients(
  id INTEGER PRIMARY KEY,
  campaign_id INTEGER NOT NULL REFERENCES email_campaigns(id),
  user_id INTEGER NOT NULL REFERENCES users(id),
  email_id INTEGER REFERENCES email_outbox(id),
  status TEXT NOT NULL CHECK(status IN('queued','skipped')),
  skip_reason TEXT,
  UNIQUE(campaign_id, user_id)
);

-- ===== 7. Reminders =====
CREATE TABLE email_reminder_state(
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  rule_code TEXT NOT NULL,
  sent_count INTEGER NOT NULL DEFAULT 0,
  last_sent_at TEXT,
  stopped_at TEXT, stop_reason TEXT,
  UNIQUE(user_id, rule_code)
);

-- ===== 8. Development capture: rendered mail written ONLY when APP_ENV=development with the dev transport. Never used in staging/production. =====
CREATE TABLE email_dev_capture(
  email_id INTEGER PRIMARY KEY REFERENCES email_outbox(id),
  subject TEXT, html TEXT, text TEXT, created_at TEXT NOT NULL
);
