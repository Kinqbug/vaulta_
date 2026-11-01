-- 006: legal documents, immutable versions, user acceptance evidence, legal configuration, cookie-consent records.

CREATE TABLE legal_documents(
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE CHECK(length(slug) BETWEEN 2 AND 60 AND slug NOT GLOB '*[^a-z0-9-]*'),
  title TEXT NOT NULL,
  doc_type TEXT NOT NULL DEFAULT 'core' CHECK(doc_type IN ('core','supplement')),
  region TEXT NOT NULL DEFAULT 'GLOBAL',
  required INTEGER NOT NULL DEFAULT 0 CHECK(required IN (0,1)),
  sort_order INTEGER NOT NULL DEFAULT 100,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL
);

CREATE TABLE legal_versions(
  id INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL REFERENCES legal_documents(id),
  version INTEGER NOT NULL CHECK(version > 0),
  title TEXT NOT NULL,
  content TEXT NOT NULL CHECK(length(content) > 0),
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','PUBLISHED','ARCHIVED')),
  material INTEGER NOT NULL DEFAULT 0 CHECK(material IN (0,1)),   -- 1 = users must re-acknowledge (risk disclosure) / re-accept
  effective_at TEXT,
  published_at TEXT,
  change_note TEXT,
  created_by INTEGER REFERENCES users(id),
  updated_by INTEGER REFERENCES users(id),
  published_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(document_id, version)
);
CREATE UNIQUE INDEX uq_legal_one_published ON legal_versions(document_id) WHERE status='PUBLISHED';
CREATE UNIQUE INDEX uq_legal_one_draft ON legal_versions(document_id) WHERE status='DRAFT';
CREATE INDEX ix_legal_versions_doc ON legal_versions(document_id, version);

-- Published and archived versions are immutable; versions can never be deleted; status only moves forward.
CREATE TRIGGER trg_legal_versions_immutable BEFORE UPDATE ON legal_versions
WHEN OLD.status IN ('PUBLISHED','ARCHIVED') AND (NEW.content IS NOT OLD.content OR NEW.title IS NOT OLD.title OR NEW.content_hash IS NOT OLD.content_hash
     OR NEW.version IS NOT OLD.version OR NEW.document_id IS NOT OLD.document_id OR NEW.effective_at IS NOT OLD.effective_at OR NEW.material IS NOT OLD.material)
BEGIN SELECT RAISE(ABORT, 'published legal versions are immutable'); END;

CREATE TRIGGER trg_legal_versions_status BEFORE UPDATE OF status ON legal_versions
WHEN (OLD.status='ARCHIVED' AND NEW.status!='ARCHIVED') OR (OLD.status='PUBLISHED' AND NEW.status='DRAFT')
BEGIN SELECT RAISE(ABORT, 'invalid legal version status change'); END;

CREATE TRIGGER trg_legal_versions_nodelete BEFORE DELETE ON legal_versions
BEGIN SELECT RAISE(ABORT, 'legal versions cannot be deleted'); END;

CREATE TRIGGER trg_legal_documents_nodelete BEFORE DELETE ON legal_documents
BEGIN SELECT RAISE(ABORT, 'legal documents cannot be deleted'); END;

-- Acceptance evidence: append-only, bound to the exact version (and content hash) the user was shown.
-- Deliberately no IP address or user-agent column: the timestamp, user and version are sufficient evidence.
CREATE TABLE legal_acceptances(
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id),
  document_id INTEGER NOT NULL REFERENCES legal_documents(id),
  version_id INTEGER NOT NULL REFERENCES legal_versions(id),
  document_version INTEGER NOT NULL,
  acceptance_type TEXT NOT NULL CHECK(acceptance_type IN ('TERMS_ACCEPTANCE','PRIVACY_NOTICE_ACKNOWLEDGEMENT','RISK_DISCLOSURE_ACKNOWLEDGEMENT')),
  accepted_at TEXT NOT NULL,
  policy_hash TEXT NOT NULL,
  UNIQUE(user_id, version_id, acceptance_type)
);
CREATE INDEX ix_legal_acceptances_user ON legal_acceptances(user_id, acceptance_type);
CREATE INDEX ix_legal_acceptances_version ON legal_acceptances(version_id);
CREATE TRIGGER trg_legal_acceptances_noupdate BEFORE UPDATE ON legal_acceptances BEGIN SELECT RAISE(ABORT, 'acceptance records are append-only'); END;
CREATE TRIGGER trg_legal_acceptances_nodelete BEFORE DELETE ON legal_acceptances BEGIN SELECT RAISE(ABORT, 'acceptance records are append-only'); END;

-- Deployment-specific legal details (entity, contacts, governing law, regulatory status). Never invented; empty until an administrator supplies them.
CREATE TABLE legal_config(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  updated_by INTEGER REFERENCES users(id)
);

-- Cookie consent log (minimal): random browser-held id, the categories chosen, the policy version shown, and a timestamp. No IP, no user id.
CREATE TABLE cookie_consents(
  id INTEGER PRIMARY KEY,
  consent_id TEXT NOT NULL,
  preferences TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('accept_all','reject_optional','custom','withdraw')),
  policy_version INTEGER,
  created_at TEXT NOT NULL
);
CREATE INDEX ix_cookie_consents_id ON cookie_consents(consent_id, id);
CREATE TRIGGER trg_cookie_consents_noupdate BEFORE UPDATE ON cookie_consents BEGIN SELECT RAISE(ABORT, 'consent records are append-only'); END;
