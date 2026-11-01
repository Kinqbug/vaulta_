"""Upload safety for deposit screenshots and asset logos. Pure standard library.

  * The file's REAL content decides its type (signature + structure walk). The client's filename and Content-Type are ignored.
  * Rasters (PNG / JPEG / WebP) are parsed structurally: chunk CRCs, markers, dimensions, no trailing bytes (blocks appended payloads / polyglots).
  * SVG is accepted ONLY for admin logos and ONLY if it passes a strict allow-list: no scripts, styles blocks, event handlers, external references,
    foreignObject, DOCTYPE/ENTITY (XML bombs), animation or embedded images. Violations are REJECTED (not silently "cleaned"); the stored file is the re-serialised tree.
  * Stored under random names (128-bit token) in a private directory, mode 0600, created with O_EXCL. Names are never derived from user input.
  * Served only by authenticated endpoints with nosniff + a restrictive CSP (see file_response).
"""
import os, re, secrets, struct, zlib, hashlib
import xml.etree.ElementTree as ET
from flask import Response
from .util import ApiError

SCREENSHOT_MAX_BYTES = 5 * 1024 * 1024
LOGO_MAX_BYTES = 1 * 1024 * 1024
SVG_MAX_BYTES = 100 * 1024
SCREENSHOT_MAX_SIDE, SCREENSHOT_MAX_PIXELS = 12000, 60_000_000
LOGO_MIN_SIDE, LOGO_MAX_SIDE = 32, 2048
_NAME = re.compile(r'^[0-9a-f]{32}\.(png|jpg|webp|svg)$')
EXT = {'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/svg+xml': 'svg'}

def _bad(msg, code='invalid_image', status=422): raise ApiError(status, code, msg)

# ------------------------------------------------------------------ raster parsing
def _png(d):
    if not d.startswith(b'\x89PNG\r\n\x1a\n'): return None
    pos, w, h, idat, first = 8, 0, 0, False, True
    while pos + 12 <= len(d):
        n = struct.unpack('>I', d[pos:pos + 4])[0]; typ = d[pos + 4:pos + 8]
        if n > len(d) or pos + 12 + n > len(d): _bad('The PNG file is truncated or corrupt.')
        body = d[pos + 8:pos + 8 + n]
        if zlib.crc32(typ + body) & 0xffffffff != struct.unpack('>I', d[pos + 8 + n:pos + 12 + n])[0]: _bad('The PNG file is corrupt (checksum mismatch).')
        if first:
            if typ != b'IHDR' or n != 13: _bad('The PNG file is malformed.')
            w, h = struct.unpack('>II', body[:8]); first = False
        if typ == b'IDAT': idat = True
        pos += 12 + n
        if typ == b'IEND':
            if pos != len(d): _bad('The PNG file contains extra data after its end.')
            if not idat: _bad('The PNG file has no image data.')
            return 'image/png', w, h
    _bad('The PNG file is truncated or corrupt.')

def _jpeg(d):
    if not d.startswith(b'\xff\xd8\xff'): return None
    if not d.endswith(b'\xff\xd9'): _bad('The JPEG file is truncated, corrupt or has extra data after its end.')
    pos, w, h = 2, 0, 0
    while pos + 4 <= len(d):
        if d[pos] != 0xff: _bad('The JPEG file is malformed.')
        m = d[pos + 1]
        if m == 0xff: pos += 1; continue
        if m in (0xd8, 0x01) or 0xd0 <= m <= 0xd7: pos += 2; continue
        if m == 0xd9: break
        n = struct.unpack('>H', d[pos + 2:pos + 4])[0]
        if n < 2 or pos + 2 + n > len(d): _bad('The JPEG file is malformed.')
        if m in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            h, w = struct.unpack('>HH', d[pos + 5:pos + 9]); return 'image/jpeg', w, h
        if m == 0xda: _bad('The JPEG file has no frame header.')
        pos += 2 + n
    _bad('The JPEG file is malformed.')

def _webp(d):
    if not (d[:4] == b'RIFF' and d[8:12] == b'WEBP'): return None
    if struct.unpack('<I', d[4:8])[0] + 8 != len(d): _bad('The WebP file is truncated, corrupt or has extra data.')
    typ = d[12:16]; w = h = 0
    if typ == b'VP8 ' and d[23:26] == b'\x9d\x01\x2a': w, h = struct.unpack('<HH', d[26:30]); w &= 0x3fff; h &= 0x3fff
    elif typ == b'VP8L' and d[20:21] == b'\x2f':
        b = struct.unpack('<I', d[21:25])[0]; w, h = (b & 0x3fff) + 1, ((b >> 14) & 0x3fff) + 1
    elif typ == b'VP8X': w, h = 1 + int.from_bytes(d[24:27], 'little'), 1 + int.from_bytes(d[27:30], 'little')
    else: _bad('Unsupported or malformed WebP file.')
    return 'image/webp', w, h

def detect_raster(d: bytes):
    """-> (mime, width, height) from the file's real content, or ApiError. Never trusts the filename."""
    for f in (_png, _jpeg, _webp):
        r = f(d)
        if r: return r
    raise ApiError(415, 'unsupported_file', 'Unsupported file type. Upload a JPEG, PNG or WebP image.')

def validate_screenshot(d: bytes):
    if not d: _bad('The file is empty.')
    if len(d) > SCREENSHOT_MAX_BYTES: raise ApiError(413, 'file_too_large', 'Screenshots must be 5 MB or smaller.')
    mime, w, h = detect_raster(d)
    if w < 100 or h < 100: _bad('The image is too small to be a readable screenshot.')
    if max(w, h) > SCREENSHOT_MAX_SIDE or w * h > SCREENSHOT_MAX_PIXELS: _bad('The image dimensions are too large.')
    return mime, w, h

# ------------------------------------------------------------------ SVG (logos only)
SVG_NS = 'http://www.w3.org/2000/svg'
_TAGS = {'svg', 'g', 'path', 'circle', 'ellipse', 'rect', 'line', 'polyline', 'polygon', 'defs', 'lineargradient', 'radialgradient', 'stop', 'clippath', 'title', 'desc', 'text', 'tspan'}
_ATTRS = {'viewbox', 'width', 'height', 'x', 'y', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'd', 'points', 'transform', 'fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin',
          'stroke-miterlimit', 'stroke-dasharray', 'stroke-opacity', 'fill-opacity', 'opacity', 'fill-rule', 'clip-rule', 'offset', 'stop-color', 'stop-opacity', 'gradientunits', 'gradienttransform',
          'fx', 'fy', 'id', 'clip-path', 'preserveaspectratio', 'version', 'font-family', 'font-size', 'font-weight', 'text-anchor', 'dx', 'dy', 'role', 'aria-label', 'style', 'xmlns', 'spreadmethod', 'clippathunits'}
_STYLE_PROPS = {'fill', 'stroke', 'stroke-width', 'opacity', 'fill-opacity', 'stroke-opacity', 'stop-color', 'stop-opacity', 'fill-rule', 'clip-rule', 'stroke-linecap', 'stroke-linejoin', 'font-size', 'font-weight', 'font-family', 'text-anchor'}
_URL = re.compile(r'url\(\s*([^)]*)\)', re.I)
_BAD = re.compile(r'javascript:|data:|expression\(|@import|<|>|&#|\\', re.I)

def _local(t): return t.rsplit('}', 1)[-1].lower()

def _value_ok(v):
    if _BAD.search(v): return False
    return all(m.strip('\'" ').startswith('#') for m in _URL.findall(v))

def sanitize_svg(d: bytes) -> bytes:
    """Strict allow-list validation. Returns the re-serialised, safe SVG, or raises (never 'cleans' silently)."""
    if len(d) > SVG_MAX_BYTES: raise ApiError(413, 'file_too_large', 'SVG logos must be 100 KB or smaller.')
    try: t = d.decode('utf-8')
    except UnicodeDecodeError: _bad('The SVG must be UTF-8 text.', 'unsafe_svg')
    low = t.lower()
    for bad in ('<!doctype', '<!entity', '<![cdata[', '<?xml-stylesheet', '<script', 'javascript:', 'foreignobject', '<iframe', '<image', '<use', '<a ', '<style', '<animate', '<set ', '<filter', '<pattern', '<mask', '<symbol', '<marker', '<switch'):
        if bad in low: _bad(f'This SVG contains content that is not allowed ({bad.strip("<")}). Export a plain-shape SVG or upload a PNG.', 'unsafe_svg')
    try: root = ET.fromstring(t)
    except ET.ParseError: _bad('The file is not valid SVG.', 'unsafe_svg')
    if root.tag != '{%s}svg' % SVG_NS: _bad('The file is not a standard SVG document.', 'unsafe_svg')
    def walk(el, depth=0):
        if depth > 20: _bad('The SVG is nested too deeply.', 'unsafe_svg')
        if _local(el.tag) not in _TAGS or (el.tag.startswith('{') and not el.tag.startswith('{%s}' % SVG_NS)): _bad(f'SVG element <{_local(el.tag)}> is not allowed.', 'unsafe_svg')
        for k, v in list(el.attrib.items()):
            name = _local(k)
            if k.startswith('{') or name.startswith('on') or name not in _ATTRS: _bad(f'SVG attribute "{name}" is not allowed.', 'unsafe_svg')
            if name == 'style':
                for decl in filter(None, (x.strip() for x in v.split(';'))):
                    prop, _, val = decl.partition(':')
                    if prop.strip().lower() not in _STYLE_PROPS or not _value_ok(val): _bad('The SVG contains an unsafe style declaration.', 'unsafe_svg')
            elif not _value_ok(v): _bad(f'SVG attribute "{name}" contains an unsafe value.', 'unsafe_svg')
        if el.text and _local(el.tag) not in ('text', 'tspan', 'title', 'desc') and el.text.strip(): _bad('Unexpected text inside the SVG.', 'unsafe_svg')
        for c in el: walk(c, depth + 1)
    walk(root)
    if not root.get('viewBox') and not (root.get('width') and root.get('height')): _bad('The SVG needs a viewBox or width and height.', 'unsafe_svg')
    ET.register_namespace('', SVG_NS)
    return ET.tostring(root, encoding='utf-8', xml_declaration=False)

def validate_logo(d: bytes):
    """-> (mime, stored_bytes). Rasters are kept byte-for-byte after structural validation; SVG is stored in sanitised form."""
    if not d: _bad('The file is empty.')
    head = d.lstrip()[:512].lower()
    if head.startswith(b'<svg') or head.startswith(b'<?xml') or b'<svg' in head:
        return 'image/svg+xml', sanitize_svg(d)
    if len(d) > LOGO_MAX_BYTES: raise ApiError(413, 'file_too_large', 'Logos must be 1 MB or smaller.')
    mime, w, h = detect_raster(d)
    if w < LOGO_MIN_SIDE or h < LOGO_MIN_SIDE: _bad(f'The logo must be at least {LOGO_MIN_SIDE}x{LOGO_MIN_SIDE} pixels.')
    if max(w, h) > LOGO_MAX_SIDE: _bad(f'The logo must be at most {LOGO_MAX_SIDE}x{LOGO_MAX_SIDE} pixels.')
    return mime, d

# ------------------------------------------------------------------ private storage
def _dir(root, sub):
    p = os.path.join(root, sub); os.makedirs(p, mode=0o700, exist_ok=True); return p

def store(root, sub, data: bytes, mime):
    """Write under a random, server-generated name. Returns (name, sha256)."""
    name = secrets.token_hex(16) + '.' + EXT[mime]
    fd = os.open(os.path.join(_dir(root, sub), name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f: f.write(data)
    return name, hashlib.sha256(data).hexdigest()

def load(root, sub, name):
    if not isinstance(name, str) or not _NAME.match(name): raise ApiError(404, 'not_found', 'File not found')     # blocks any path traversal
    p = os.path.join(root, sub, name)
    if not os.path.isfile(p): raise ApiError(404, 'not_found', 'File not found')
    with open(p, 'rb') as f: return f.read()

def remove(root, sub, name):
    try:
        if name and _NAME.match(name): os.remove(os.path.join(root, sub, name))
    except OSError: pass

def file_response(data: bytes, mime, filename=None, download=False, etag=None):
    """Authenticated-endpoint response: never sniffed, never framed, never cached by shared caches, scripts disabled even if the type were misjudged."""
    h = {'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, no-store' if etag is None else 'private, max-age=300, must-revalidate',
         'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; sandbox", 'Cross-Origin-Resource-Policy': 'same-origin'}
    if etag: h['ETag'] = '"%s"' % etag
    if filename and download: h['Content-Disposition'] = 'attachment; filename="%s"' % re.sub(r'[^A-Za-z0-9._-]', '_', filename)[:80]
    return Response(data, mimetype=mime, headers=h)
