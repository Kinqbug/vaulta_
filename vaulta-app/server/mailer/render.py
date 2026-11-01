"""Template -> HTML + plain text.

Safety properties (all covered by tests):
  * only {{variables}} from catalog.VARS are substituted; no template language is evaluated from stored/admin content
  * every value is stripped of control characters and escaped by the layout (Jinja autoescape, sandboxed environment)
  * links come from PUBLIC_BASE_URL (trusted configuration), never from request headers; custom button links must be https and on an allowed host
  * a required variable that is missing/blank STOPS the render: nothing is invented, no placeholder amount/date/reference is ever substituted
"""
import os, re, base64
from urllib.parse import urlparse
from jinja2.sandbox import SandboxedEnvironment
from jinja2 import FileSystemLoader, select_autoescape, StrictUndefined
from . import catalog as cat

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"
_env = SandboxedEnvironment(loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), 'templates')), autoescape=select_autoescape(['html']), undefined=StrictUndefined)
_CTRL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\u2028\u2029\u202a-\u202e\u2066-\u2069]')   # control + bidi-override characters

class RenderError(Exception):
    def __init__(self, code, message, variables=None): super().__init__(message); self.code, self.message, self.variables = code, message, variables or []

class Rendered:
    def __init__(self, subject, html, text, preheader, links, headline): self.subject, self.html, self.text, self.preheader, self.links, self.headline = subject, html, text, preheader, links, headline

def build_links(base):
    if not base: return {}
    a = base + '/app/'
    return {'dashboard_url': a, 'transaction_url': a + '#/transactions', 'portfolio_url': a + '#/portfolio', 'trade_url': a + '#/trade', 'goal_url': a + '#/goals',
            'support_url': a + '#/support', 'security_url': a + '#/settings', 'kyc_url': a + '#/settings'}

def clean(v, multiline=False, limit=None):
    s = _CTRL.sub('', '' if v is None else str(v)).replace('\r\n', '\n').replace('\r', '\n')
    if multiline: s = re.sub(r'\n{3,}', '\n\n', s).strip()
    else: s = re.sub(r'\s+', ' ', s).strip()
    return s[:limit or (4000 if multiline else 300)]

def fill(text, values):
    """Substitute {{name}} tokens with already-cleaned values. Unknown tokens are left as errors upstream (spec_problems)."""
    return cat.PLACEHOLDER.sub(lambda m: values.get(m.group(1), ''), text or '')

def _paras(text, values, multiline_vars):
    """One paragraph per spec entry; a paragraph that is exactly one multiline variable expands to one paragraph per blank-line block."""
    m = cat.PLACEHOLDER.fullmatch((text or '').strip())
    if m and m.group(1) in multiline_vars:
        blocks = [b for b in re.split(r'\n\s*\n', values.get(m.group(1), '')) if b.strip()]
        return [[ln for ln in b.split('\n')] for b in blocks]
    s = fill(text, values).strip()
    return [[s]] if s else []

_GOOD = ('completed', 'credited', 'verified', 'confirmed', 'approved', 'executed', 'enabled', 'active', 'goal reached', 'resolved')
_BAD = ('unsuccessful', 'rejected', 'failed', 'cancelled', 'reversed', 'disabled', 'expired', 'not executed', 'closed', 'restricted', 'suspended', 'not completed')
def badge_style(text):
    t = (text or '').lower()
    if t in _GOOD: return 'background-color:#111111;color:#ffffff;border:1px solid #111111;'
    if t in _BAD: return 'background-color:#4a4a4a;color:#ffffff;border:1px solid #4a4a4a;'
    return 'background-color:#ffffff;color:#111111;border:1px solid #111111;'

def link_ok(url, base, extra_hosts=()):
    """Administrator-supplied links: https only (http allowed solely for a development base URL), on the app host or an allow-listed host."""
    try: u = urlparse(url)
    except ValueError: return False
    if u.scheme not in ('https', 'http') or not u.hostname or re.search(r'\s', url) or len(url) > 2000 or u.username or u.password: return False
    b = urlparse(base) if base else None
    if u.scheme == 'http' and not (b and b.scheme == 'http' and u.hostname == b.hostname): return False
    return u.hostname == (b.hostname if b else None) or u.hostname in {h.strip().lower() for h in extra_hosts if h.strip()}

def logo_data_uri(web_root):
    p = os.path.join(web_root or '', 'assets', 'brand', 'vaulta-logo-black.png')
    try: return 'data:image/png;base64,' + base64.b64encode(open(p, 'rb').read()).decode()
    except OSError: return ''

def footer_for(mail_class, base, legal, unsubscribe_url=None):
    legal = legal or {}
    site = legal.get('website_url') or base
    links = [('Website', site)] if site else []
    if base: links += [('Privacy policy', base + '/legal/privacy/'), ('Contact us', base + '/contact/')]
    if mail_class == 'marketing' and unsubscribe_url: links.append(('Unsubscribe', unsubscribe_url))
    line = ' · '.join(x for x in (legal.get('legal_entity_name'), legal.get('registered_address')) if x)       # only verified values from legal_config; never invented
    reason = {'marketing': 'You are receiving this because you opted in to updates from VAULTA. You can unsubscribe at any time.',
              'service': 'You are receiving this service notice because you have a VAULTA account.',
              'admin': 'This message was sent to you individually by VAULTA about your account.'}.get(mail_class, 'You are receiving this email because of activity on your VAULTA account.')
    return {'links': links, 'legal_line': line, 'support_email': legal.get('support_email') or '', 'reason': reason}

def render(subject, spec, ctx, *, secret=None, optional=(), mail_class='transactional', base_url='', legal=None, unsubscribe_url=None, banner=None,
           overrides=None, inline_logo_root=None, extra_hosts=()):
    ov = overrides or {}
    spec = dict(spec)
    if ov.get('headline'): spec['headline'] = ov['headline']
    if ov.get('cta_label') and spec.get('cta'): spec['cta'] = {**spec['cta'], 'label': ov['cta_label']}
    if ov.get('subject'): subject = ov['subject']
    values = {**build_links(base_url), **{k: v for k, v in (ctx or {}).items() if v not in (None, '')}, **{k: v for k, v in (secret or {}).items() if v not in (None, '')}}
    multiline = {k for k, (_, kind) in cat.VARS.items() if kind == 'multiline'}
    vals = {k: (v if k in cat.LINK_VARS else clean(v, multiline=k in multiline)) for k, v in values.items() if k in cat.VARS}
    for k in cat.LINK_VARS:                                           # links must be absolute http(s) URLs
        if k in vals and not re.match(r'^https?://[^\s]+$', vals[k]): vals.pop(k)
    ref = cat.referenced_vars(subject, spec)
    missing = [v for v in ref if v not in optional and not vals.get(v)]
    if missing: raise RenderError('missing_variables', 'Required values are missing: ' + ', '.join(missing), missing)
    show = lambda row_text: all(vals.get(v) for v in cat.PLACEHOLDER.findall(row_text) if v in optional)   # rows that depend on an empty optional value are dropped

    subj = clean(fill(subject, {k: clean(v) for k, v in vals.items()}), limit=200)
    headline = clean(fill(spec['headline'], vals), limit=200)
    intro = [p for t in spec['intro'] if show(t) for p in _paras(t, vals, multiline)]
    after = [p for t in spec.get('after', []) if show(t) for p in _paras(t, vals, multiline)]
    details = [(clean(fill(l, vals)), [x for x in fill(v, vals).split('\n')]) for l, v in spec.get('details', []) if show(l + v)]
    summary = [(clean(fill(l, vals)), clean(fill(v, vals))) for l, v in spec.get('summary', []) if show(l + v)]
    badge = clean(fill(spec.get('badge', ''), vals), limit=40)
    notice = clean(fill(spec.get('notice', ''), vals), multiline=True, limit=600)
    cta = spec.get('cta'); cta_url = cta_label = None
    if cta:
        cta_url = vals.get(cta['url_var']); cta_label = clean(cta['label'], limit=40)
        if ov.get('cta_url'):
            if not link_ok(ov['cta_url'], base_url, extra_hosts): raise RenderError('link_not_allowed', 'The button link must be an https address on VAULTA or an approved host.')
            cta_url = ov['cta_url']
    preheader = clean(fill(spec.get('preheader') or (intro[0][0] if intro and intro[0] else ''), vals), limit=110)
    home = (legal or {}).get('website_url') or base_url or '#'
    logo = (logo_data_uri(inline_logo_root) if inline_logo_root is not None else (base_url + '/assets/brand/vaulta-logo-black.png' if base_url else ''))
    secret_link = bool(cta and cta['url_var'] in cat.SECRET_VARS)
    page = dict(subject=subj, preheader=preheader, headline=headline, intro=intro, after=after, details=details, summary=summary, summary_title=spec.get('summary_title') or 'Details',
                badge=badge, badge_style=badge_style(badge), notice=notice, cta_url=cta_url, cta_label=cta_label, show_link_fallback=secret_link, banner=banner, font=FONT,
                logo_url=logo, home_url=home, security_reminder=mail_class == 'security' or secret_link, footer=footer_for(mail_class, base_url, legal, unsubscribe_url))
    html = _env.get_template('layout.html').render(**page)
    text = re.sub(r'\n{3,}', '\n\n', _env.get_template('layout.txt').render(**page)).strip() + '\n'
    links = [u for u in re.findall(r'href="([^"]+)"', html) if u.startswith(('http://', 'https://'))]
    return Rendered(subj, html, text, preheader, links, headline)

# Clearly-labelled sample values for previews and administrator test sends. Never used for a real customer message.
SAMPLE = {'first_name': 'Sample', 'email': 'sample.customer@example.com', 'customer_id': 'USR-SAMPLE', 'transaction_reference': 'SAMPLE-0001', 'transaction_status': 'Pending', 'amount': '100.00',
          'asset_symbol': 'USD', 'network': 'Sample network', 'transaction_date': '2000-01-01 12:00 UTC', 'destination': '••••SAMPLE', 'investment_name': 'Sample Fund', 'investment_amount': '$100.00',
          'investment_reference': 'INV-SAMPLE', 'order_type': 'Limit', 'trade_side': 'Buy', 'order_quantity': '1.00', 'executed_quantity': '1.00', 'execution_price': '$100.00', 'limit_price': '$100.00',
          'trading_fee': '$0.10', 'order_reference': 'ORD-SAMPLE', 'goal_name': 'Sample goal', 'target_amount': '$1,000.00', 'current_amount': '$250.00', 'progress_percentage': '25%',
          'contribution_amount': '$50.00', 'retirement_age': '65', 'event_timestamp': '2000-01-01 12:00 UTC', 'ip_address': '203.0.113.10', 'device': 'Sample browser', 'reason': 'Sample reason text.',
          'account_status': 'Restricted', 'changed_fields': 'Sample fields', 'wallet_action': 'Address added', 'wallet_address': 'sample…ADDR', 'ticket_reference': 'TKT-SAMPLE', 'ticket_subject': 'Sample subject',
          'expires_in': '48 hours', 'document_name': 'Sample document', 'effective_date': '2000-01-31', 'maintenance_window': '2000-01-01 01:00–03:00 UTC',
          'message_body': 'This is sample message text.\n\nIt can have more than one paragraph.', 'action_url': 'https://example.com/sample-link', 'document_url': 'https://example.com/legal/sample/'}
