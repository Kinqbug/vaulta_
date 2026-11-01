"""Legal documents: safe Markdown rendering, versioned publishing, acceptance evidence, legal configuration, cookie consent.

Design notes
* One authoritative PUBLISHED version per document (partial unique index). Published/archived versions are immutable (DB triggers).
* Acceptances reference the exact version row and store the content hash; they are append-only (DB triggers).
* Content is Markdown. It is rendered server-side with every character HTML-escaped (no raw HTML passes through), links are
  limited to http(s)/mailto/relative URLs, and the result is the only thing public endpoints return.
* Deployment details ([LEGAL ENTITY NAME] ...) come from legal_config; anything not configured stays visibly marked.
"""
import os, re, html, hashlib, json, secrets
from . import util
from .util import ApiError
from .db import one, all_, val, tx
from .audit import audit

CONTENT_DIR = os.path.join(os.path.dirname(__file__), 'legal_content')
# slug, title, sort, source file, acceptance needed at signup
CORE_DOCS = [('terms', 'Terms and Conditions', 1), ('privacy', 'Privacy Policy', 2), ('disclosures', 'Financial and Service Disclosures', 3),
             ('risk-disclosure', 'Cryptocurrency and Investment Risk Disclosure', 4), ('accessibility', 'Accessibility Statement', 5), ('cookies', 'Cookie Policy', 6)]
CORE_SLUGS = [s for s, _, _ in CORE_DOCS]
SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,59}$')

TERMS_ACCEPT, PRIVACY_ACK, RISK_ACK = 'TERMS_ACCEPTANCE', 'PRIVACY_NOTICE_ACKNOWLEDGEMENT', 'RISK_DISCLOSURE_ACKNOWLEDGEMENT'

# placeholder token in the text -> legal_config key
TOKENS = {'LEGAL ENTITY NAME': 'legal_entity_name', 'COUNTRY OR JURISDICTION': 'registration_jurisdiction', 'JURISDICTION': 'registration_jurisdiction',
          'REGISTERED ADDRESS': 'registered_address', 'WEBSITE URL': 'website_url', 'CONTACT EMAIL': 'support_email', 'LEGAL EMAIL': 'legal_email',
          'PRIVACY EMAIL': 'privacy_email', 'COMPLAINTS EMAIL': 'complaints_email', 'ACCESSIBILITY EMAIL': 'accessibility_email',
          'GOVERNING JURISDICTION': 'governing_law', 'VERIFIED REGULATORY STATUS': 'regulatory_status',
          'VERIFIED AUTHORISED SERVICES, IF APPLICABLE': 'authorised_services'}
DATE_TOKENS = ('EFFECTIVE DATE', 'LAST UPDATED')
CONFIG_FIELDS = {
 'legal_entity_name': ('Legal entity name', 200), 'registration_jurisdiction': ('Company registration jurisdiction', 200), 'registered_address': ('Registered address', 400),
 'website_url': ('Website URL (https://...)', 200), 'support_email': ('General support email', 200), 'legal_email': ('Legal notices email', 200),
 'privacy_email': ('Privacy contact email', 200), 'complaints_email': ('Complaints email', 200), 'accessibility_email': ('Accessibility contact email', 200),
 'governing_law': ('Governing law / jurisdiction', 200), 'regulatory_status': ('Verified regulatory status', 400), 'authorised_services': ('Verified authorised services (or "None")', 400)}
EMAIL_KEYS = ('support_email', 'legal_email', 'privacy_email', 'complaints_email', 'accessibility_email')
_TOKEN_RE = re.compile(r'\[(' + '|'.join(re.escape(t) for t in sorted(list(TOKENS) + list(DATE_TOKENS), key=len, reverse=True)) + r')\]')

# ---------------------------------------------------------------- configuration
def get_config(db):
    cfg = {k: '' for k in CONFIG_FIELDS}
    for r in db.execute('SELECT key,value FROM legal_config'):
        if r['key'] in cfg: cfg[r['key']] = r['value']
    return cfg

def set_config(db, actor_id, updates):
    if not isinstance(updates, dict) or not updates: raise ApiError(422, 'missing_fields', 'Nothing to update.')
    clean = {}
    for k, v in updates.items():
        if k not in CONFIG_FIELDS: raise ApiError(422, 'unknown_field', f'Unknown setting: {k}')
        v = ' '.join(str(v if v is not None else '').split())
        if len(v) > CONFIG_FIELDS[k][1]: raise ApiError(422, 'too_long', f'{CONFIG_FIELDS[k][0]} is too long.')
        if v and k in EMAIL_KEYS and not util.EMAIL_RE.match(v): raise ApiError(422, 'invalid_email', f'{CONFIG_FIELDS[k][0]}: enter a valid email address.')
        if v and k == 'website_url' and not re.match(r'^https://[^\s/]+\.[^\s]{2,}$', v): raise ApiError(422, 'invalid_url', 'Website URL must start with https://')
        clean[k] = v
    with tx(db):
        before = get_config(db)
        for k, v in clean.items():
            db.execute('INSERT INTO legal_config(key,value,updated_at,updated_by) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at,updated_by=excluded.updated_by', (k, v, util.now(), actor_id))
        after = get_config(db)
        audit(db, actor_id, 'legal.config_updated', 'legal_config', None, {k: before[k] for k in clean}, {k: after[k] for k in clean})
    return after

def missing_config(db): return [k for k, v in get_config(db).items() if not v]

# ---------------------------------------------------------------- safe markdown
_LINK = re.compile(r'\[([^\]\n]+)\]\(([^)\s]+)\)')
_SAFE_URL = re.compile(r'^(https?://[^\s<>"]+|mailto:[^\s<>"]+|/[^\s<>"]*|#[\w-]+)$', re.I)
_MISSING = re.compile(r'\[([A-Z][A-Z ,./()-]{2,})\]')

def _inline(s):
    links = []
    def stash(m):
        url = m.group(2)
        if not _SAFE_URL.match(url): return m.group(1)                      # unsafe scheme: keep the text, drop the link
        links.append((m.group(1), url)); return f'\x00{len(links) - 1}\x00'
    s = _LINK.sub(stash, s)
    s = html.escape(s)
    s = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)
    s = re.sub(r'(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])', r'<em>\1</em>', s)
    s = _MISSING.sub(lambda m: f'<mark class="lg-missing" title="Detail not yet configured">[{m.group(1)}]</mark>', s)
    def back(m):
        text, url = links[int(m.group(1))]
        ext = url.lower().startswith('http')
        return f'<a href="{html.escape(url, quote=True)}"' + (' rel="noopener noreferrer" target="_blank"' if ext else '') + f'>{html.escape(text)}</a>'
    return re.sub(r'\x00(\d+)\x00', back, s)

def _slugify(t):
    s = re.sub(r'[^a-z0-9]+', '-', re.sub(r'<[^>]+>', '', t).lower()).strip('-')
    return s[:60] or 'section'

def render_markdown(md):
    """Markdown subset -> (html, toc). Headings: '###' becomes <h2>, '####' <h3>. Never emits raw HTML from the source."""
    lines = md.replace('\r\n', '\n').split('\n'); out = []; toc = []; used = set(); para = []; items = []; kind = None; table = []
    def flush():
        nonlocal para, items, kind, table
        if para: out.append('<p>' + '<br>'.join(_inline(l.strip()) for l in para) + '</p>'); para = []
        if items: out.append(f'<{kind}>' + ''.join(f'<li>{_inline(i)}</li>' for i in items) + f'</{kind}>'); items = []; kind = None
        if table:
            rows = [[c.strip() for c in r.strip().strip('|').split('|')] for r in table if not re.match(r'^\s*\|?\s*:?-{2,}', r)]
            if rows:
                head, body = rows[0], rows[1:]
                out.append('<div class="lg-table"><table><thead><tr>' + ''.join(f'<th>{_inline(c)}</th>' for c in head) + '</tr></thead><tbody>' +
                           ''.join('<tr>' + ''.join(f'<td>{_inline(c)}</td>' for c in r) + '</tr>' for r in body) + '</tbody></table></div>')
            table = []
    for raw in lines:
        l = raw.rstrip()
        if not l.strip(): flush(); continue
        m = re.match(r'^(#{1,6})\s+(.+?)\s*#*$', l)
        if m:
            flush(); lvl = max(2, min(4, len(m.group(1)) - 1)) if len(m.group(1)) > 1 else 2; text = m.group(2); sid = base = _slugify(text); n = 2
            while sid in used: sid = f'{base}-{n}'; n += 1
            used.add(sid); out.append(f'<h{lvl} id="{sid}">{_inline(text)}</h{lvl}>')
            if lvl == 2: toc.append({'id': sid, 'text': re.sub(r'<[^>]+>', '', html.unescape(_inline(text)))})
            continue
        if re.match(r'^\s*(-{3,}|\*{3,})\s*$', l): flush(); out.append('<hr>'); continue
        m = re.match(r'^\s*[*-]\s+(.*)$', l)
        if m:
            if kind != 'ul': flush(); kind = 'ul'
            items.append(m.group(1)); continue
        m = re.match(r'^\s*\d+[.)]\s+(.*)$', l)
        if m:
            if kind != 'ol': flush(); kind = 'ol'
            items.append(m.group(1)); continue
        if l.lstrip().startswith('|'):
            if para or items: flush()
            table.append(l); continue
        if items or table: flush()
        para.append(l)
    flush()
    return '\n'.join(out), toc

def _fmt_date(ts): 
    if not ts: return ''
    d = util.parse_ts(ts); return f'{d.day} {d.strftime("%B %Y")}'

def substitute(content, cfg, effective_at, published_at):
    def rep(m):
        t = m.group(1)
        if t == 'EFFECTIVE DATE': v = _fmt_date(effective_at)
        elif t == 'LAST UPDATED': v = _fmt_date(published_at)
        else: v = cfg.get(TOKENS[t], '')
        return v.replace('[', '(').replace(']', ')').replace('*', '') if v else m.group(0)
    return _TOKEN_RE.sub(rep, content)

def unresolved_tokens(content, cfg, effective_at, published_at):
    out = []
    for m in _TOKEN_RE.finditer(substitute(content, cfg, effective_at, published_at)):
        if m.group(1) not in out: out.append(m.group(1))
    return out

def render_version(db, v):
    cfg = get_config(db); text = substitute(v['content'], cfg, v['effective_at'], v['published_at'])
    h, toc = render_markdown(text)
    return {'html': h, 'toc': toc, 'unresolved': unresolved_tokens(v['content'], cfg, v['effective_at'], v['published_at'])}

# ---------------------------------------------------------------- reading
def _chash(content): return hashlib.sha256(content.encode()).hexdigest()
def doc_by_slug(db, slug):
    if not SLUG_RE.match(slug or ''): raise ApiError(404, 'not_found', 'Document not found')
    d = one(db, 'SELECT * FROM legal_documents WHERE slug=?', slug)
    if not d: raise ApiError(404, 'not_found', 'Document not found')
    return d
def published(db, doc_id): return one(db, "SELECT * FROM legal_versions WHERE document_id=? AND status='PUBLISHED'", doc_id)
def current(db, slug):
    d = doc_by_slug(db, slug); v = published(db, d['id'])
    if not v: raise ApiError(404, 'not_found', 'Document not available')
    return d, v

def public_view(db, slug, version=None):
    d = doc_by_slug(db, slug)
    if version is None: v = published(db, d['id'])
    else:
        v = one(db, "SELECT * FROM legal_versions WHERE document_id=? AND version=? AND published_at IS NOT NULL AND status IN ('PUBLISHED','ARCHIVED')", d['id'], version)     # never a draft
    if not v: raise ApiError(404, 'not_found', 'Document not found')
    r = render_version(db, v)
    return {'slug': d['slug'], 'title': v['title'], 'version': v['version'], 'status': v['status'], 'is_current': v['status'] == 'PUBLISHED', 'effective_at': v['effective_at'], 'published_at': v['published_at'],
            'content_hash': v['content_hash'], 'html': r['html'], 'toc': r['toc'], 'needs_completion': bool(r['unresolved']), 'unresolved': r['unresolved']}

def public_list(db):
    rows = all_(db, "SELECT d.slug,d.title,d.doc_type,d.region,d.required,d.sort_order,v.version,v.effective_at,v.published_at FROM legal_documents d JOIN legal_versions v ON v.document_id=d.id AND v.status='PUBLISHED' ORDER BY d.sort_order,d.slug")
    return rows

# ---------------------------------------------------------------- seeding (idempotent; never overwrites existing documents)
def ensure_seed(db):
    created = []
    for slug, title, sort in CORE_DOCS:
        if one(db, 'SELECT 1 FROM legal_documents WHERE slug=?', slug): continue
        path = os.path.join(CONTENT_DIR, slug + '.md')
        if not os.path.exists(path): continue
        with open(path, encoding='utf-8') as fh: content = fh.read().strip() + '\n'
        t = util.now()
        with tx(db):
            if one(db, 'SELECT 1 FROM legal_documents WHERE slug=?', slug): continue
            cur = db.execute("INSERT INTO legal_documents(slug,title,doc_type,region,required,sort_order,created_at) VALUES(?,?,'core','GLOBAL',1,?,?)", (slug, title, sort, t))
            db.execute("INSERT INTO legal_versions(document_id,version,title,content,content_hash,status,material,effective_at,published_at,change_note,created_at,updated_at) VALUES(?,?,?,?,?,'PUBLISHED',1,?,?,?,?,?)",
                       (cur.lastrowid, 1, title, content, _chash(content), t, t, 'Initial published version (seeded from the approved draft; legal review required).', t, t))
            audit(db, None, 'legal.seeded', 'legal_document', slug, None, {'version': 1})
        created.append(slug)
    return created

# ---------------------------------------------------------------- administration
def _clean_title(t): 
    t = ' '.join(str(t or '').split())
    if not t or len(t) > 200: raise ApiError(422, 'invalid_title', 'A title of 1-200 characters is required.')
    return t
def _clean_content(c):
    c = str(c or '').replace('\r\n', '\n').strip()
    if len(c) < 20: raise ApiError(422, 'empty_content', 'Document content is required.')
    if len(c) > 300_000: raise ApiError(422, 'too_long', 'Document content is too large.')
    return c + '\n'
def _clean_effective(v):
    if v in (None, ''): return None
    v = str(v).strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}', v): v += 'T00:00:00Z'
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z', v): raise ApiError(422, 'invalid_date', 'Effective date must be YYYY-MM-DD.')
    try: util.parse_ts(v)
    except ValueError: raise ApiError(422, 'invalid_date', 'Effective date is not a valid date.')
    return v
def _note(v): 
    v = ' '.join(str(v or '').split())
    if len(v) > 500: raise ApiError(422, 'too_long', 'Change note is too long.')
    return v

def create_supplement(db, actor_id, slug, title, region, content):
    if not SLUG_RE.match(slug or ''): raise ApiError(422, 'invalid_slug', 'Slug: lowercase letters, numbers and hyphens (2-60 characters).')
    if slug in CORE_SLUGS or one(db, 'SELECT 1 FROM legal_documents WHERE slug=?', slug): raise ApiError(409, 'slug_taken', 'A document with this slug already exists.')
    title = _clean_title(title); region = ' '.join(str(region or '').split())[:40] or 'GLOBAL'; content = _clean_content(content); t = util.now()
    with tx(db):
        cur = db.execute("INSERT INTO legal_documents(slug,title,doc_type,region,required,sort_order,created_by,created_at) VALUES(?,?,'supplement',?,0,100,?,?)", (slug, title, region, actor_id, t))
        db.execute("INSERT INTO legal_versions(document_id,version,title,content,content_hash,status,created_by,updated_by,created_at,updated_at) VALUES(?,1,?,?,?,'DRAFT',?,?,?,?)", (cur.lastrowid, title, content, _chash(content), actor_id, actor_id, t, t))
        audit(db, actor_id, 'legal.supplement_created', 'legal_document', slug, None, {'title': title, 'region': region})
    return slug

def create_draft(db, actor_id, slug):
    d = doc_by_slug(db, slug)
    with tx(db):
        if one(db, "SELECT 1 FROM legal_versions WHERE document_id=? AND status='DRAFT'", d['id']): raise ApiError(409, 'draft_exists', 'A draft already exists for this document. Edit or archive it first.')
        base = published(db, d['id']) or one(db, 'SELECT * FROM legal_versions WHERE document_id=? ORDER BY version DESC LIMIT 1', d['id'])
        n = (val(db, 'SELECT MAX(version) FROM legal_versions WHERE document_id=?', d['id']) or 0) + 1; t = util.now()
        cur = db.execute("INSERT INTO legal_versions(document_id,version,title,content,content_hash,status,material,created_by,updated_by,created_at,updated_at) VALUES(?,?,?,?,?,'DRAFT',0,?,?,?,?)",
                         (d['id'], n, base['title'], base['content'], base['content_hash'], actor_id, actor_id, t, t))
        audit(db, actor_id, 'legal.draft_created', 'legal_version', cur.lastrowid, None, {'slug': slug, 'version': n})
    return cur.lastrowid

def get_version(db, vid):
    v = one(db, 'SELECT v.*,d.slug FROM legal_versions v JOIN legal_documents d ON d.id=v.document_id WHERE v.id=?', vid)
    if not v: raise ApiError(404, 'not_found', 'Version not found')
    return v

def update_draft(db, actor_id, vid, b):
    with tx(db):
        v = get_version(db, vid)
        if v['status'] != 'DRAFT': raise ApiError(409, 'not_draft', 'Only drafts can be edited. Create a new draft to change a published document.')
        title = _clean_title(b.get('title', v['title'])); content = _clean_content(b.get('content', v['content']))
        eff = _clean_effective(b['effective_at']) if 'effective_at' in b else v['effective_at']
        mat = 1 if b.get('material', bool(v['material'])) else 0; note = _note(b.get('change_note', v['change_note']))
        db.execute('UPDATE legal_versions SET title=?,content=?,content_hash=?,effective_at=?,material=?,change_note=?,updated_by=?,updated_at=? WHERE id=?', (title, content, _chash(content), eff, mat, note, actor_id, util.now(), vid))
        audit(db, actor_id, 'legal.draft_updated', 'legal_version', vid, {'hash': v['content_hash']}, {'hash': _chash(content), 'material': mat})
    return get_version(db, vid)

def publish(db, actor_id, vid, confirm, effective_at=None, env='development'):
    if confirm is not True: raise ApiError(422, 'confirmation_required', 'Publishing must be confirmed.')
    with tx(db):
        v = get_version(db, vid)
        if v['status'] != 'DRAFT': raise ApiError(409, 'not_draft', 'Only a draft can be published.')
        _clean_title(v['title']); _clean_content(v['content'])
        if not SLUG_RE.match(v['slug']): raise ApiError(422, 'invalid_slug', 'Invalid slug.')
        eff = _clean_effective(effective_at) if effective_at else (v['effective_at'] or util.now())
        prev = published(db, v['document_id'])
        if prev and not (v['change_note'] or '').strip(): raise ApiError(422, 'change_note_required', 'Add a change note describing what changed before publishing a new version.')
        if env == 'production':
            miss = missing_config(db)
            if miss: raise ApiError(422, 'legal_config_incomplete', 'Complete the legal configuration before publishing in production.', {'missing': miss})
        t = util.now()
        if prev: db.execute("UPDATE legal_versions SET status='ARCHIVED',updated_at=?,updated_by=? WHERE id=?", (t, actor_id, prev['id']))
        db.execute("UPDATE legal_versions SET status='PUBLISHED',effective_at=?,published_at=?,published_by=?,updated_by=?,updated_at=? WHERE id=?", (eff, t, actor_id, actor_id, t, vid))
        audit(db, actor_id, 'legal.published', 'legal_version', vid, {'previous_version': prev['version'] if prev else None}, {'slug': v['slug'], 'version': v['version'], 'hash': v['content_hash'], 'effective_at': eff})
    return get_version(db, vid)

def archive_version(db, actor_id, vid):
    with tx(db):
        v = get_version(db, vid); d = one(db, 'SELECT * FROM legal_documents WHERE id=?', v['document_id'])
        if v['status'] == 'ARCHIVED': raise ApiError(409, 'already_archived', 'Already archived.')
        if v['status'] == 'PUBLISHED' and d['required']: raise ApiError(409, 'required_document', 'A required document must always have a published version. Publish a replacement instead.')
        db.execute("UPDATE legal_versions SET status='ARCHIVED',updated_at=?,updated_by=? WHERE id=?", (util.now(), actor_id, vid))
        audit(db, actor_id, 'legal.archived', 'legal_version', vid, {'status': v['status']}, {'status': 'ARCHIVED', 'slug': v['slug'], 'version': v['version']})

def readiness(db, env='development'):
    docs = []
    for d in all_(db, 'SELECT * FROM legal_documents ORDER BY sort_order,slug'):
        v = published(db, d['id']); cfg = get_config(db)
        docs.append({'slug': d['slug'], 'title': d['title'], 'required': bool(d['required']), 'published': bool(v), 'version': v['version'] if v else None,
                     'unresolved': unresolved_tokens(v['content'], cfg, v['effective_at'], v['published_at']) if v else []})
    miss = missing_config(db)
    return {'env': env, 'missing_config': [{'key': k, 'label': CONFIG_FIELDS[k][0]} for k in miss], 'documents': docs,
            'ready': not miss and all(x['published'] for x in docs if x['required']) and not any(x['unresolved'] for x in docs),
            'checklist': ['Supply every legal configuration value (entity, contacts, governing law, verified regulatory status).', 'Have qualified legal counsel review each document against the live product.',
                          'Confirm the registered jurisdiction, regulatory status and authorised services are stated accurately.', 'Re-publish any document whose placeholders were resolved after counsel review.']}

# ---------------------------------------------------------------- acceptance
def record_acceptance(db, user_id, doc, ver, atype):
    """Server-side timestamp, exact version and content hash. Duplicate calls are harmless (unique constraint)."""
    db.execute('INSERT OR IGNORE INTO legal_acceptances(user_id,document_id,version_id,document_version,acceptance_type,accepted_at,policy_hash) VALUES(?,?,?,?,?,?,?)',
               (user_id, doc['id'], ver['id'], ver['version'], atype, util.now(), ver['content_hash']))

def current_for_acceptance(db, slugs=('terms', 'privacy')):
    out = {}
    for s in slugs:
        d, v = current(db, s); out[s] = {'slug': s, 'title': v['title'], 'version': v['version'], 'version_id': v['id'], 'effective_at': v['effective_at'], 'content_hash': v['content_hash']}
    return out

def accept_signup(db, user_id, terms_version, privacy_version):
    """Called inside the registration transaction. The versions the user was shown must still be the current ones."""
    try: tv, pv = int(terms_version), int(privacy_version)
    except (TypeError, ValueError): raise ApiError(422, 'legal_versions_required', 'Please review the current Terms and Privacy Policy.')
    td, tver = current(db, 'terms'); pd, pver = current(db, 'privacy')
    if (tv, pv) != (tver['version'], pver['version']):
        raise ApiError(409, 'legal_version_changed', 'The Terms or Privacy Policy were updated. Please review the current versions and accept again.', {'terms_version': tver['version'], 'privacy_version': pver['version']})
    record_acceptance(db, user_id, td, tver, TERMS_ACCEPT); record_acceptance(db, user_id, pd, pver, PRIVACY_ACK)

def risk_floor(db, doc_id, cur_version):
    """Lowest version a user's acknowledgement must reach: the latest published-or-superseded version flagged as material (<= current)."""
    return val(db, "SELECT MAX(version) FROM legal_versions WHERE document_id=? AND version<=? AND material=1 AND published_at IS NOT NULL", doc_id, cur_version) or cur_version

def risk_status(db, user_id):
    try: d, v = current(db, 'risk-disclosure')
    except ApiError: return {'required': False, 'acknowledged': True, 'version': None, 'available': False}
    floor = risk_floor(db, d['id'], v['version'])
    ok = val(db, "SELECT 1 FROM legal_acceptances WHERE user_id=? AND document_id=? AND acceptance_type=? AND document_version>=?", user_id, d['id'], RISK_ACK, floor)
    return {'required': True, 'acknowledged': bool(ok), 'version': v['version'], 'available': True}

def require_risk_ack(db, user):
    if not val(db, "SELECT value FROM system_settings WHERE key='risk_ack_required'") == '0':
        s = risk_status(db, user['id'])
        if s['available'] and not s['acknowledged']:
            raise ApiError(428, 'risk_ack_required', 'Please read and acknowledge the Cryptocurrency and Investment Risk Disclosure before continuing.', {'version': s['version'], 'document': '/legal/risk-disclosure/'})

def acknowledge_risk(db, user_id, version):
    with tx(db):
        d, v = current(db, 'risk-disclosure')
        if version is not None and int(version) != v['version']: raise ApiError(409, 'legal_version_changed', 'The Risk Disclosure was updated. Please review the current version.', {'version': v['version']})
        record_acceptance(db, user_id, d, v, RISK_ACK)
    return risk_status(db, user_id)

def my_acceptances(db, user_id):
    return all_(db, 'SELECT a.acceptance_type,a.document_version version,a.accepted_at,d.slug,d.title FROM legal_acceptances a JOIN legal_documents d ON d.id=a.document_id WHERE a.user_id=? ORDER BY a.id DESC', user_id)

# ---------------------------------------------------------------- cookie consent
OPTIONAL = ('preferences', 'analytics', 'marketing')
def record_cookie_consent(db, consent_id, prefs, action):
    if not isinstance(prefs, dict): raise ApiError(422, 'invalid_preferences', 'Preferences must be an object.')
    clean = {k: bool(prefs.get(k) is True) for k in OPTIONAL}
    if action not in ('accept_all', 'reject_optional', 'custom', 'withdraw'): raise ApiError(422, 'invalid_action', 'Unknown consent action.')
    if action == 'accept_all': clean = {k: True for k in OPTIONAL}
    if action in ('reject_optional', 'withdraw'): clean = {k: False for k in OPTIONAL}
    if consent_id is not None and not re.fullmatch(r'[A-Za-z0-9_-]{16,64}', str(consent_id)): raise ApiError(422, 'invalid_id', 'Invalid consent id.')
    cid = consent_id or secrets.token_urlsafe(18)
    pv = val(db, "SELECT v.version FROM legal_versions v JOIN legal_documents d ON d.id=v.document_id WHERE d.slug='cookies' AND v.status='PUBLISHED'")
    db.execute('INSERT INTO cookie_consents(consent_id,preferences,action,policy_version,created_at) VALUES(?,?,?,?,?)', (cid, util.jdump(clean), action, pv, util.now()))
    return {'consent_id': cid, 'preferences': clean, 'policy_version': pv}

def purge_cookie_consents(db, days=730):
    cutoff = util.ts_after(days=-days)
    return db.execute('DELETE FROM cookie_consents WHERE created_at<? AND id NOT IN (SELECT MAX(id) FROM cookie_consents GROUP BY consent_id)', (cutoff,)).rowcount

if __name__ == '__main__':       # python -m server.legal purge   (retention: drop superseded cookie-consent rows older than 24 months)
    import sys
    from .config import Config
    from . import db as dbm
    c = dbm.connect(Config.DATABASE_PATH); dbm.init_schema(c)
    if sys.argv[1:] == ['purge']: print('purged', purge_cookie_consents(c))
    else: print('usage: python -m server.legal purge')
