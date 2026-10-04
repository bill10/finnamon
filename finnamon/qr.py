"""A QR code for the terminal, stdlib only: byte mode, error correction M, versions 1-10 (up to 213 bytes, plenty for
the pairing address `finnamon remote` prints). Written from ISO/IEC 18004; tests/test_remote.py checks it against
codes another encoder made. render() draws it in half blocks with explicit black-on-white colours and a 4-module
quiet zone, so it scans the same on a light or a dark terminal."""
from __future__ import annotations

# version: (ec codewords per block, [(blocks, data codewords per block), ...]) at level M
BLOCKS = {1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
          6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
          10: (26, [(4, 43), (1, 44)])}
ALIGN = {2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}
MASKS = [lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0, lambda r, c: (r + c) % 3 == 0,
         lambda r, c: (r // 2 + c // 3) % 2 == 0, lambda r, c: r * c % 2 + r * c % 3 == 0,
         lambda r, c: (r * c % 2 + r * c % 3) % 2 == 0, lambda r, c: ((r + c) % 2 + r * c % 3) % 2 == 0]

EXP, LOG = [0] * 512, [0] * 256   # GF(256) over x^8+x^4+x^3+x^2+1
_x = 1
for _i in range(255):
    EXP[_i] = EXP[_i + 255] = _x; LOG[_x] = _i
    _x = (_x << 1) ^ (0x11D if _x & 0x80 else 0)


def _mul(a: int, b: int) -> int:
    return EXP[LOG[a] + LOG[b]] if a and b else 0


def _ec(data: list[int], n: int) -> list[int]:
    gen = [1]
    for i in range(n):   # (x - a^0)(x - a^1)...(x - a^(n-1))
        gen = [a ^ _mul(b, EXP[i]) for a, b in zip(gen + [0], [0] + gen)]
    rem = [0] * n
    for d in data:
        f = d ^ rem[0]
        rem = [r ^ _mul(g, f) for r, g in zip(rem[1:] + [0], gen[1:])]
    return rem


def _bch(value: int, poly: int, bits: int) -> int:
    v = value << bits
    for i in range(v.bit_length() - 1, bits - 1, -1):
        if v >> i & 1:
            v ^= poly << (i - bits)
    return value << bits | v


def _codewords(data: bytes, ver: int) -> list[int]:
    ec, groups = BLOCKS[ver]
    cap = sum(b * n for b, n in groups)
    bits = "0100" + format(len(data), "016b" if ver >= 10 else "08b") + "".join(format(b, "08b") for b in data)
    bits += "0" * min(4, cap * 8 - len(bits)); bits += "0" * (-len(bits) % 8)
    words = [int(bits[i:i + 8], 2) for i in range(0, len(bits), 8)]
    words += [0xEC, 0x11] * ((cap - len(words)) // 2) + [0xEC] * ((cap - len(words)) % 2)
    blocks, i = [], 0
    for b, n in groups:
        for _ in range(b):
            blocks.append(words[i:i + n]); i += n
    out = [blk[j] for j in range(max(map(len, blocks))) for blk in blocks if j < len(blk)]
    eccs = [_ec(blk, ec) for blk in blocks]
    return out + [e[j] for j in range(ec) for e in eccs]


def _penalty(m: list[list[bool]]) -> int:
    n, p = len(m), 0
    lines = m + [list(c) for c in zip(*m)]
    for line in lines:   # runs of five or more, and the finder-like 1011101 with four light on a side
        run = 1
        for a, b in zip(line, line[1:] + [None]):
            if a == b:
                run += 1
            else:
                p += run - 2 if run >= 5 else 0; run = 1
        s = "".join("1" if x else "0" for x in line)
        p += 40 * (s.count("10111010000") + s.count("00001011101"))
    p += 3 * sum(m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1] for r in range(n - 1) for c in range(n - 1))
    dark = sum(map(sum, m))
    return p + 10 * ((abs(dark * 20 - n * n * 10) + n * n - 1) // (n * n) - 1)


def matrix(text: str, mask: int | None = None) -> list[list[bool]]:
    """The modules, True = dark, without the quiet zone. mask None picks the lowest penalty, as the standard says."""
    data = text.encode()
    ver = next((v for v in BLOCKS if len(data) + (3 if v >= 10 else 2) <= sum(b * n for b, n in BLOCKS[v][1])), None)
    if ver is None:
        raise ValueError(f"too long for a terminal QR code ({len(data)} bytes)")
    n = 17 + 4 * ver
    m = [[False] * n for _ in range(n)]
    fixed = [[False] * n for _ in range(n)]

    def put(r, c, dark):
        m[r][c] = dark; fixed[r][c] = True

    for r0, c0 in ((0, 0), (0, n - 7), (n - 7, 0)):   # finders and their separators
        for r in range(-1, 8):
            for c in range(-1, 8):
                if 0 <= r0 + r < n and 0 <= c0 + c < n:
                    put(r0 + r, c0 + c, 0 <= r <= 6 and 0 <= c <= 6 and (r in (0, 6) or c in (0, 6) or (2 <= r <= 4 and 2 <= c <= 4)))
    for i in range(8, n - 8):   # timing
        put(6, i, i % 2 == 0); put(i, 6, i % 2 == 0)
    pos = ALIGN.get(ver, [])
    for r0 in pos:
        for c0 in pos:
            if (r0, c0) not in ((6, 6), (6, pos[-1]), (pos[-1], 6)):   # not on a finder
                for r in range(-2, 3):
                    for c in range(-2, 3):
                        put(r0 + r, c0 + c, max(abs(r), abs(c)) != 1)
    put(n - 8, 8, True)   # the dark module
    for i in range(9):   # reserve the format areas; drawn per mask below
        fixed[8][i] = fixed[i][8] = True
    for i in range(8):
        fixed[8][n - 1 - i] = fixed[n - 1 - i][8] = True
    if ver >= 7:
        v = _bch(ver, 0x1F25, 12)
        for i in range(18):
            put(n - 11 + i % 3, i // 3, bool(v >> i & 1)); put(i // 3, n - 11 + i % 3, bool(v >> i & 1))

    bits = "".join(format(w, "08b") for w in _codewords(data, ver))
    order, i, right = [], 0, n - 1
    while right >= 1:   # two-column zigzag from the bottom right, skipping the vertical timing column
        if right == 6:
            right = 5
        for k in range(n):
            r = n - 1 - k if (right + 1) & 2 == 0 else k
            for c in (right, right - 1):
                if not fixed[r][c]:
                    m[r][c] = i < len(bits) and bits[i] == "1"; i += 1; order.append((r, c))
        right -= 2

    def masked(k):
        out = [row[:] for row in m]
        for r, c in order:
            out[r][c] ^= MASKS[k](r, c)
        f = _bch(k, 0x537, 10) ^ 0x5412   # level M's two bits are 00
        for j in range(15):
            b = bool(f >> j & 1)
            out[j if j < 6 else 7 if j == 6 else 8][8 if j < 8 else 7 if j == 8 else 14 - j] = b   # by the top-left finder
            out[8 if j < 8 else n - 15 + j][n - 1 - j if j < 8 else 8] = b   # split under the top-right and beside the bottom-left
        return out

    if mask is not None:
        return masked(mask)
    return min((masked(k) for k in range(8)), key=_penalty)


def render(text: str) -> str:
    """Two module rows per line ('▀' with the top row as foreground, the bottom as background), black on bright white."""
    m = matrix(text)
    q, n = 4, len(m)
    grid = [[False] * (n + 2 * q) for _ in range(q)] + [[False] * q + row + [False] * q for row in m] + [[False] * (n + 2 * q) for _ in range(q + 1)]
    lines = []
    for r in range(0, n + 2 * q, 2):
        lines.append("".join(f"\x1b[{30 if t else 97};{40 if b else 107}m▀" for t, b in zip(grid[r], grid[r + 1])) + "\x1b[0m")
    return "\n".join(lines)
