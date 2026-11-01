"""Minimal QR Code encoder (byte mode, error-correction level M, versions 1-10 = up to 213 bytes) producing an SVG string.
Dependency-free so deposit addresses can be rendered server-side under a strict CSP. The address is ALWAYS also shown as text next to the code;
tests decode the generated images to prove the code contains exactly the address."""
EC = {1: (10, [16]), 2: (16, [28]), 3: (26, [44]), 4: (18, [32] * 2), 5: (24, [43] * 2), 6: (16, [27] * 4), 7: (18, [31] * 4), 8: (22, [38, 38, 39, 39]), 9: (22, [36] * 3 + [37] * 2), 10: (26, [43] * 4 + [44])}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}

_EXP = [0] * 512; _LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x; _LOG[_x] = _i; _x <<= 1
    if _x & 0x100: _x ^= 0x11d
for _i in range(255, 512): _EXP[_i] = _EXP[_i - 255]
def _mul(a, b): return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]

def _rs(data, n):
    gen = [1]
    for i in range(n):
        nxt = [0] * (len(gen) + 1)
        for j, c in enumerate(gen): nxt[j] ^= c; nxt[j + 1] ^= _mul(c, _EXP[i])
        gen = nxt
    rem = [0] * n
    for b in data:
        f = b ^ rem[0]; rem = rem[1:] + [0]
        for i in range(n): rem[i] ^= _mul(gen[i + 1], f)
    return rem

def _codewords(data: bytes, v):
    ec_len, blocks = EC[v]; cap = sum(blocks)
    bits = '0100' + format(len(data), '08b' if v < 10 else '016b') + ''.join(format(b, '08b') for b in data)
    bits += '0' * min(4, cap * 8 - len(bits)); bits += '0' * (-len(bits) % 8)
    cw = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11); i = 0
    while len(cw) < cap: cw.append(pad[i % 2]); i += 1
    out_d, out_e, pos = [], [], 0
    for n in blocks: blk = cw[pos:pos + n]; pos += n; out_d.append(blk); out_e.append(_rs(blk, ec_len))
    res = []
    for i in range(max(blocks)): res += [b[i] for b in out_d if i < len(b)]
    for i in range(ec_len): res += [b[i] for b in out_e]
    return res

def _bit(x, i): return (x >> i) & 1

def _matrix(cw, v, mask):
    size = 17 + 4 * v; M = [[0] * size for _ in range(size)]; F = [[False] * size for _ in range(size)]
    def put(x, y, val, fn=True): M[y][x] = 1 if val else 0; F[y][x] = fn
    for i in range(size): put(6, i, i % 2 == 0); put(i, 6, i % 2 == 0)
    for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
        for dy in range(-4, 5):
            for dx in range(-4, 5):
                x, y = cx + dx, cy + dy
                if 0 <= x < size and 0 <= y < size: d = max(abs(dx), abs(dy)); put(x, y, d not in (2, 4))
    pts = ALIGN[v]
    for cy in pts:
        for cx in pts:
            if (cx, cy) in ((6, 6), (6, pts[-1]), (pts[-1], 6)): continue
            for dy in range(-2, 3):
                for dx in range(-2, 3): put(cx + dx, cy + dy, max(abs(dx), abs(dy)) != 1)
    def fmt():
        data = mask; rem = data
        for _ in range(10): rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        for i in range(6): put(8, i, _bit(bits, i))
        put(8, 7, _bit(bits, 6)); put(8, 8, _bit(bits, 7)); put(7, 8, _bit(bits, 8))
        for i in range(9, 15): put(14 - i, 8, _bit(bits, i))
        for i in range(8): put(size - 1 - i, 8, _bit(bits, i))
        for i in range(8, 15): put(8, size - 15 + i, _bit(bits, i))
        put(8, size - 8, 1)
    fmt()
    if v >= 7:
        rem = v
        for _ in range(12): rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
        bits = v << 12 | rem
        for i in range(18): a, b = size - 11 + i % 3, i // 3; put(a, b, _bit(bits, i)); put(b, a, _bit(bits, i))
    i = 0; total = len(cw) * 8
    for right in range(size - 1, 0, -2):
        if right == 6: right = 5
        for vert in range(size):
            for j in range(2):
                x = right - j; up = ((right + 1) & 2) == 0; y = size - 1 - vert if up else vert
                if not F[y][x] and i < total: M[y][x] = _bit(cw[i >> 3], 7 - (i & 7)); i += 1
    fns = [lambda x, y: (x + y) % 2 == 0, lambda x, y: y % 2 == 0, lambda x, y: x % 3 == 0, lambda x, y: (x + y) % 3 == 0, lambda x, y: (x // 3 + y // 2) % 2 == 0,
           lambda x, y: x * y % 2 + x * y % 3 == 0, lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0, lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0][mask]
    for y in range(size):
        for x in range(size):
            if not F[y][x] and fns(x, y): M[y][x] ^= 1
    return M

def _penalty(M):
    n = len(M); p = 0
    for rows in (M, [list(r) for r in zip(*M)]):
        for r in rows:
            run = 1
            for i in range(1, n):
                if r[i] == r[i - 1]: run += 1
                else: p += 3 + run - 5 if run >= 5 else 0; run = 1
            p += 3 + run - 5 if run >= 5 else 0
    for y in range(n - 1):
        for x in range(n - 1):
            if M[y][x] == M[y][x + 1] == M[y + 1][x] == M[y + 1][x + 1]: p += 3
    dark = sum(map(sum, M)); p += (abs(dark * 20 - n * n * 10) // (n * n)) * 10
    return p

def encode(text: str):
    data = text.encode('utf-8')
    for v in range(1, 11):
        if len(data) <= sum(EC[v][1]) - (2 if v < 10 else 3): break
    else: raise ValueError('text too long for this QR encoder')
    cw = _codewords(data, v)
    return min((_matrix(cw, v, m) for m in range(8)), key=_penalty)

def svg(text: str, scale=6, quiet=4):
    M = encode(text); n = len(M); dim = (n + 2 * quiet) * scale
    path = ''.join(f'M{(x + quiet) * scale},{(y + quiet) * scale}h{scale}v{scale}h-{scale}z' for y in range(n) for x in range(n) if M[y][x])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {dim} {dim}" width="{dim}" height="{dim}" role="img" aria-label="QR code of the deposit address" shape-rendering="crispEdges">'
            f'<rect width="100%" height="100%" fill="#fff"/><path d="{path}" fill="#000"/></svg>')
