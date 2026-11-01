"""Public-address validation for the supported chain families. Pure standard library, no keys, no network access.

  btc     base58check (P2PKH/P2SH) and bech32/bech32m (segwit, taproot); mainnet vs testnet prefixes are enforced
  evm     0x + 40 hex; EIP-55 mixed-case checksum is verified (company wallets additionally REQUIRE the checksummed form)
  solana  base58 that decodes to exactly 32 bytes

A validator answers one question: "is this string a well-formed address for THIS network?". It cannot tell you who controls it.
"""
import hashlib, re

FORMATS = ('btc', 'evm', 'solana')
PLACEHOLDER = 'NOT_CONFIGURED'
B58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
_B58I = {c: i for i, c in enumerate(B58)}

class InvalidAddress(ValueError):
    pass

# ---------------- Keccak-256 (Ethereum's hash; hashlib only ships the NIST SHA-3 variant) ----------------
_M = (1 << 64) - 1
def _rol(x, n): n %= 64; return ((x << n) | (x >> (64 - n))) & _M if n else x
def _keccak_f(L):
    R = 1
    for _ in range(24):
        C = [L[x][0] ^ L[x][1] ^ L[x][2] ^ L[x][3] ^ L[x][4] for x in range(5)]
        D = [C[(x + 4) % 5] ^ _rol(C[(x + 1) % 5], 1) for x in range(5)]
        L = [[L[x][y] ^ D[x] for y in range(5)] for x in range(5)]
        x, y = 1, 0; cur = L[x][y]
        for t in range(24):
            x, y = y, (2 * x + 3 * y) % 5
            cur, L[x][y] = L[x][y], _rol(cur, (t + 1) * (t + 2) // 2)
        for y in range(5):
            T = [L[x][y] for x in range(5)]
            for x in range(5): L[x][y] = T[x] ^ ((~T[(x + 1) % 5]) & T[(x + 2) % 5])
        for j in range(7):
            res = (R & 1) != 0
            R = ((R << 1) ^ 0x71) & 0xFF if R & 0x80 else (R << 1) & 0xFF
            if res: L[0][0] ^= 1 << ((1 << j) - 1)
    return L

def keccak256(data: bytes) -> bytes:
    rate = 136; st = [[0] * 5 for _ in range(5)]
    p = bytearray(data); p.append(0x01)
    while len(p) % rate: p.append(0)
    p[-1] |= 0x80
    for off in range(0, len(p), rate):
        blk = p[off:off + rate]
        for i in range(rate // 8): st[i % 5][i // 5] ^= int.from_bytes(blk[8 * i:8 * i + 8], 'little')
        st = _keccak_f(st)
    out = b''.join(st[i % 5][i // 5].to_bytes(8, 'little') for i in range(4))
    return out[:32]

# ---------------- base58 ----------------
def b58decode(s: str) -> bytes:
    if not s: raise InvalidAddress('empty')
    n = 0
    for ch in s:
        if ch not in _B58I: raise InvalidAddress('invalid base58 character')
        n = n * 58 + _B58I[ch]
    body = n.to_bytes((n.bit_length() + 7) // 8, 'big') if n else b''
    return b'\x00' * (len(s) - len(s.lstrip('1'))) + body

def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, 'big'); out = ''
    while n: n, r = divmod(n, 58); out = B58[r] + out
    return '1' * (len(b) - len(b.lstrip(b'\x00'))) + out

def b58check_decode(s: str) -> bytes:
    raw = b58decode(s)
    if len(raw) < 5: raise InvalidAddress('too short')
    body, chk = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4] != chk: raise InvalidAddress('bad checksum')
    return body

def b58check_encode(body: bytes) -> str:
    return b58encode(body + hashlib.sha256(hashlib.sha256(body).digest()).digest()[:4])

# ---------------- bech32 / bech32m (BIP-173 / BIP-350) ----------------
_BECH = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'
def _polymod(values):
    gen = (0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3); chk = 1
    for v in values:
        b = chk >> 25; chk = ((chk & 0x1ffffff) << 5) ^ v
        for i in range(5): chk ^= gen[i] if (b >> i) & 1 else 0
    return chk
def _hrp_expand(h): return [ord(c) >> 5 for c in h] + [0] + [ord(c) & 31 for c in h]
def bech32_decode(s: str):
    if s != s.lower() and s != s.upper(): raise InvalidAddress('mixed case')
    s = s.lower()
    if len(s) > 90 or '1' not in s: raise InvalidAddress('bad bech32 structure')
    hrp, data = s.rsplit('1', 1)
    if not hrp or len(data) < 6 or any(c not in _BECH for c in data): raise InvalidAddress('bad bech32 characters')
    vals = [_BECH.index(c) for c in data]; pm = _polymod(_hrp_expand(hrp) + vals)
    if pm == 1: spec = 'bech32'
    elif pm == 0x2bc830a3: spec = 'bech32m'
    else: raise InvalidAddress('bad checksum')
    return hrp, vals[:-6], spec
def _convertbits(data, frm, to, pad=True):
    acc = bits = 0; ret = []; maxv = (1 << to) - 1
    for v in data:
        if v < 0 or v >> frm: raise InvalidAddress('bad value')
        acc = (acc << frm) | v; bits += frm
        while bits >= to: bits -= to; ret.append((acc >> bits) & maxv)
    if pad:
        if bits: ret.append((acc << (to - bits)) & maxv)
    elif bits >= frm or ((acc << (to - bits)) & maxv): raise InvalidAddress('bad padding')
    return ret
def bech32_encode(hrp, data, spec='bech32'):
    const = 1 if spec == 'bech32' else 0x2bc830a3
    pm = _polymod(_hrp_expand(hrp) + data + [0] * 6) ^ const
    return hrp + '1' + ''.join(_BECH[d] for d in data + [(pm >> 5 * (5 - i)) & 31 for i in range(6)])

# ---------------- per-family validators ----------------
def _btc(addr, testnet):
    if addr[:1] in '13mn2':
        body = b58check_decode(addr)
        if len(body) != 21: raise InvalidAddress('wrong payload length')
        ok = {0x6f, 0xc4} if testnet else {0x00, 0x05}
        if body[0] not in ok: raise InvalidAddress('address is for the other Bitcoin network' if body[0] in ({0x00, 0x05} | {0x6f, 0xc4}) else 'unknown version byte')
        return
    hrp, data, spec = bech32_decode(addr)
    if hrp != ('tb' if testnet else 'bc'): raise InvalidAddress('address is for the other Bitcoin network')
    if not data: raise InvalidAddress('empty witness')
    ver, prog = data[0], bytes(_convertbits(data[1:], 5, 8, False))
    if ver > 16: raise InvalidAddress('bad witness version')
    if not 2 <= len(prog) <= 40: raise InvalidAddress('bad witness program length')
    if ver == 0:
        if spec != 'bech32' or len(prog) not in (20, 32): raise InvalidAddress('bad v0 witness program')
    elif spec != 'bech32m': raise InvalidAddress('witness v1+ requires bech32m')
    elif ver == 1 and len(prog) != 32: raise InvalidAddress('bad taproot program length')

def eip55(addr: str) -> str:
    a = addr[2:].lower(); h = keccak256(a.encode()).hex()
    return '0x' + ''.join(c.upper() if c.isalpha() and int(h[i], 16) >= 8 else c for i, c in enumerate(a))

def _evm(addr, require_checksum):
    if not re.fullmatch(r'0x[0-9a-fA-F]{40}', addr): raise InvalidAddress('not a 0x + 40 hex address')
    if int(addr, 16) == 0: raise InvalidAddress('the zero address cannot receive funds')
    letters = [c for c in addr[2:] if c.isalpha()]
    mixed = any(c.isupper() for c in letters) and any(c.islower() for c in letters)
    if mixed or (require_checksum and letters):
        if addr != eip55(addr):
            raise InvalidAddress('EIP-55 checksum mismatch' if mixed else 'use the EIP-55 checksummed (mixed-case) form')

def _sol(addr):
    if not 32 <= len(addr) <= 44: raise InvalidAddress('wrong length for a Solana address')
    if len(b58decode(addr)) != 32: raise InvalidAddress('does not decode to a 32-byte key')

def validate(fmt, address, *, testnet=False, strict=False):
    """Raise InvalidAddress unless `address` is well-formed for `fmt`. strict=True is used for company wallets and contracts."""
    if fmt not in FORMATS: raise InvalidAddress(f'unknown address format {fmt!r}')
    if not isinstance(address, str) or address != address.strip() or not address: raise InvalidAddress('empty or padded with whitespace')
    if address == PLACEHOLDER: raise InvalidAddress('placeholder address')
    if fmt == 'btc': _btc(address, testnet)
    elif fmt == 'evm': _evm(address, strict)
    else: _sol(address)
    return True

def is_valid(fmt, address, **kw):
    try: return validate(fmt, address, **kw)
    except InvalidAddress: return False

def address_key(fmt, address):
    """Canonical comparison form: EVM addresses are case-insensitive, base58/bech32 are not (bech32 is lower-cased)."""
    if fmt == 'evm': return address.lower()
    if fmt == 'btc' and address.lower().startswith(('bc1', 'tb1')): return address.lower()
    return address

def family_of(address):
    """Best-effort guess used ONLY to produce a helpful error message ('that looks like a Solana address')."""
    if re.fullmatch(r'0x[0-9a-fA-F]{40}', address or ''): return 'evm'
    if (address or '').lower().startswith(('bc1', 'tb1')) or re.fullmatch(r'[13mn2][1-9A-HJ-NP-Za-km-z]{25,39}', address or ''): return 'btc'
    if re.fullmatch(r'[1-9A-HJ-NP-Za-km-z]{32,44}', address or ''): return 'solana'
    return None
