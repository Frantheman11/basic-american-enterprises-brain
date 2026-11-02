"""A QR code as SVG, with no dependency: byte mode, error correction M, versions 1 to 10.

WHY
    The gym prints a check-in QR and a waiver QR for the door. The server that draws them (a
    client's own box, the demo droplet) carries no QR library, and the engine adds no package for
    one picture. tools/qr.py stays the styled encoder for print sheets; this is the plain one.

HOW
    ISO/IEC 18004: data bits, Reed-Solomon in GF(256), the fixed patterns, the zig-zag fill, all
    eight masks scored by the four penalty rules, the format and version bits. Up to 213 bytes
    (version 10), plenty for a URL. tests/test_gym_qr.py checks every module against the `qrcode`
    package for every mask, so a mistake here cannot hide.
"""
from html import escape

# (blocks, data codewords) per group at level M, and EC codewords per block, versions 1..10
BLOCKS = {1: ([(1, 16)], 10), 2: ([(1, 28)], 16), 3: ([(1, 44)], 26), 4: ([(2, 32)], 18), 5: ([(2, 43)], 24),
          6: ([(4, 27)], 16), 7: ([(4, 31)], 18), 8: ([(2, 38), (2, 39)], 22), 9: ([(3, 36), (2, 37)], 22),
          10: ([(4, 43), (1, 44)], 26)}
ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
         9: [6, 26, 46], 10: [6, 28, 50]}
EC_M = 0  # the format bits for level M

EXP, LOG = [0] * 512, [0] * 256
_x = 1
for _i in range(255):
    EXP[_i], LOG[_x] = _x, _i
    _x = (_x << 1) ^ (0x11D if _x & 0x80 else 0)
for _i in range(255, 512):
    EXP[_i] = EXP[_i - 255]


def _rs(data, n):
    """The n Reed-Solomon codewords for `data`."""
    gen = [1]
    for i in range(n):
        gen = [a ^ b for a, b in zip(gen + [0], [0] + [EXP[LOG[g] + i] if g else 0 for g in gen])]
    rem = list(data) + [0] * n
    for i in range(len(data)):
        c = rem[i]
        if c:
            for j, g in enumerate(gen):
                if g:
                    rem[i + j] ^= EXP[LOG[g] + LOG[c]]
    return rem[len(data):]


def _bch(value, poly, bits):
    v = value << bits
    while v.bit_length() > bits:
        v ^= poly << (v.bit_length() - poly.bit_length())
    return (value << bits) | v


def _codewords(text, version):
    raw = text.encode("utf-8")
    groups, ec = BLOCKS[version]
    capacity = sum(n * k for n, k in groups)
    bits = [0, 1, 0, 0] + [int(b) for b in format(len(raw), "08b" if version < 10 else "016b")]
    for byte in raw:
        bits += [int(b) for b in format(byte, "08b")]
    bits += [0] * min(4, capacity * 8 - len(bits))
    bits += [0] * (-len(bits) % 8)
    data = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    data += [(0xEC, 0x11)[i % 2] for i in range(capacity - len(data))]
    blocks, at = [], 0
    for n, k in groups:
        for _ in range(n):
            blocks.append(data[at:at + k])
            at += k
    out = [b[i] for i in range(max(len(b) for b in blocks)) for b in blocks if i < len(b)]
    ecs = [_rs(b, ec) for b in blocks]
    return out + [e[i] for i in range(ec) for e in ecs]


def _version(text):
    size = len(text.encode("utf-8"))
    for v in range(1, 11):  # the mode (4 bits), the count (8 bits, 16 from version 10), the bytes
        if 4 + (8 if v < 10 else 16) + 8 * size <= 8 * sum(n * k for n, k in BLOCKS[v][0]):
            return v
    raise ValueError("Too long for a QR code here (at most 213 bytes)")


MASKS = [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
         lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
         lambda r, c: (r * c) % 2 + (r * c) % 3 == 0, lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
         lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0]


def _base(version):
    n = 17 + 4 * version
    m = [[None] * n for _ in range(n)]

    def finder(r0, c0):
        for r in range(-1, 8):
            for c in range(-1, 8):
                if 0 <= r0 + r < n and 0 <= c0 + c < n:
                    m[r0 + r][c0 + c] = 0 <= r <= 6 and 0 <= c <= 6 and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4))
    finder(0, 0), finder(0, n - 7), finder(n - 7, 0)
    for p in ALIGN[version]:
        for q in ALIGN[version]:
            if m[p][q] is None:
                for r in range(-2, 3):
                    for c in range(-2, 3):
                        m[p + r][q + c] = max(abs(r), abs(c)) != 1
    for i in range(8, n - 8):
        if m[6][i] is None:
            m[6][i] = i % 2 == 0
        if m[i][6] is None:
            m[i][6] = i % 2 == 0
    return m


def _place_format(m, mask):
    n = len(m)
    bits = _bch((EC_M << 3) | mask, 0x537, 10) ^ 0x5412
    for i in range(15):
        b = bool((bits >> i) & 1)
        # around the top-left finder
        if i < 6:
            m[i][8] = b
        elif i < 8:
            m[i + 1][8] = b
        else:
            m[n - 15 + i][8] = b
        # across the top and the bottom-left
        if i < 8:
            m[8][n - i - 1] = b
        elif i < 9:
            m[8][15 - i] = b
        else:
            m[8][15 - i - 1] = b
    m[n - 8][8] = True  # the dark module


def _place_version(m, version):
    if version < 7:
        return
    n = len(m)
    bits = _bch(version, 0x1F25, 12)
    for i in range(18):
        b = bool((bits >> i) & 1)
        m[i // 3][i % 3 + n - 11] = b
        m[i % 3 + n - 11][i // 3] = b


def _fill(m, codewords, mask):
    n = len(m)
    bits = [(cw >> (7 - i)) & 1 for cw in codewords for i in range(8)]
    k, up, col = 0, True, n - 1
    while col > 0:
        if col == 6:
            col -= 1
        for step in range(n):
            r = n - 1 - step if up else step
            for c in (col, col - 1):
                if m[r][c] is None:
                    dark = bool(bits[k]) if k < len(bits) else False
                    k += 1
                    m[r][c] = dark ^ MASKS[mask](r, c)
        up = not up
        col -= 2


def _penalty(m):
    n = len(m)
    score = 0
    for lines in (m, list(zip(*m))):  # rule 1: five or more in a row
        for line in lines:
            run = 1
            for a, b in zip(line, line[1:]):
                run = run + 1 if a == b else 1
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
    for r in range(n - 1):  # rule 2: 2x2 blocks
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    pat = [True, False, True, True, True, False, True]
    for lines in (m, list(zip(*m))):  # rule 3: the finder-like pattern beside four light modules
        for line in lines:
            line = list(line)
            for i in range(n - 6):
                if line[i:i + 7] == pat and (line[max(0, i - 4):i] == [False] * 4 or line[i + 7:i + 11] == [False] * 4):
                    score += 40
    dark = sum(map(sum, m)) * 100 // (n * n)  # rule 4: balance
    return score + abs(dark - 50) // 5 * 10


def matrix(text, mask=None):
    """The module grid (True is dark). `mask` fixes the mask (the tests use it); else the best."""
    version = _version(text)
    words = _codewords(text, version)
    best = None
    for k in ([mask] if mask is not None else range(8)):
        m = _base(version)
        _place_format(m, k)  # reserve the format and version areas before the fill
        _place_version(m, version)
        _fill(m, words, k)
        score = _penalty(m)
        if best is None or score < best[0]:
            best = (score, m)
    return best[1]


def svg(text, size=240, title=""):
    """The QR as an SVG with a four-module quiet zone, dark on white, sharp at any print size."""
    m = matrix(text)
    n = len(m) + 8
    path = "".join(f"M{c + 4},{r + 4}h1v1h-1z" for r, row in enumerate(m) for c, dark in enumerate(row) if dark)
    label = f"<title>{escape(title)}</title>" if title else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="{size}" height="{size}" '
            f'shape-rendering="crispEdges" role="img">{label}<rect width="{n}" height="{n}" fill="#fff"/>'
            f'<path d="{path}" fill="#000"/></svg>')
