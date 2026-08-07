#!/usr/bin/env python3
"""Trace a black-on-transparent PNG into an SVG path. Standard library only.

No potrace on this machine, and the source is the easy case: a hard-edged
two-colour glyph. Decode the PNG, threshold it, walk the boundaries between ink
and not-ink with marching squares, simplify each closed contour, and emit them
all as one path with fill-rule="evenodd" so the counters stay holes.
"""
import struct
import sys
import zlib


def decode_png(path):
    data = open(path, "rb").read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, idat, width = 8, b"", None
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour = struct.unpack(">IIBB", body[:10])
            assert depth == 8, f"only 8-bit supported, got {depth}"
        elif kind == b"IDAT":
            idat += body
        elif kind == b"IEND":
            break
        pos += 12 + length
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(idat)
    stride = width * channels
    out, prev = [], bytearray(stride)
    p = 0
    for _ in range(height):
        f = raw[p]; p += 1
        line = bytearray(raw[p:p + stride]); p += stride
        for i in range(stride):
            a = line[i - channels] if i >= channels else 0
            b = prev[i]
            c = prev[i - channels] if i >= channels else 0
            if f == 1:   line[i] = (line[i] + a) & 0xFF
            elif f == 2: line[i] = (line[i] + b) & 0xFF
            elif f == 3: line[i] = (line[i] + (a + b) // 2) & 0xFF
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        out.append(bytes(line)); prev = line
    return width, height, channels, out


def make_mask(width, height, channels, rows, cut=140, light=False):
    """True where there is ink.

    `light=True` inverts the test, for artwork that is pale shapes on a dark
    ground rather than black on transparent.
    """
    mask = [[False] * (width + 2) for _ in range(height + 2)]
    for y in range(height):
        row = rows[y]
        for x in range(width):
            px = row[x * channels:(x + 1) * channels]
            if channels == 4:
                r, g, b, a = px
            elif channels == 3:
                r, g, b = px; a = 255
            elif channels == 2:
                r = g = b = px[0]; a = px[1]
            else:
                r = g = b = px[0]; a = 255
            lum = 0.299 * r + 0.587 * g + 0.114 * b
            mask[y + 1][x + 1] = a > 128 and (lum > cut if light else lum < cut)
    return mask


def contours(mask, width, height):
    """Exact boundary loops by cancelling shared pixel edges.

    Marching squares has ambiguous saddle cases and my first attempt walked into
    them and shattered the glyph into 955 fragments. This has no ambiguity: every
    ink pixel contributes the four edges of its square, an edge shared with
    another ink pixel is dropped, and what remains links head-to-tail into closed
    loops. Wound clockwise for outer boundaries and anticlockwise for counters,
    which is exactly what fill-rule="evenodd" wants.
    """
    ink = lambda x, y: mask[y + 1][x + 1] if 0 <= x < width and 0 <= y < height else False
    nxt = {}
    for y in range(height):
        for x in range(width):
            if not ink(x, y):
                continue
            if not ink(x, y - 1): nxt.setdefault((x, y), []).append((x + 1, y))
            if not ink(x + 1, y): nxt.setdefault((x + 1, y), []).append((x + 1, y + 1))
            if not ink(x, y + 1): nxt.setdefault((x + 1, y + 1), []).append((x, y + 1))
            if not ink(x - 1, y): nxt.setdefault((x, y + 1), []).append((x, y))

    loops = []
    while nxt:
        start = next(iter(nxt))
        loop, point = [], start
        while True:
            outs = nxt.get(point)
            if not outs:
                break
            step = outs.pop()
            if not outs:
                del nxt[point]
            loop.append(point)
            point = step
            if point == start:
                break
        if len(loop) > 8:
            loops.append(loop)
    return loops


def _dp(pts, eps):
    if len(pts) < 3:
        return pts
    x1, y1 = pts[0]; x2, y2 = pts[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** 0.5 or 1
    worst, wi = 0.0, 0
    for i in range(1, len(pts) - 1):
        x0, y0 = pts[i]
        dist = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / norm
        if dist > worst:
            worst, wi = dist, i
    if worst <= eps:
        return [pts[0], pts[-1]]
    return _dp(pts[:wi + 1], eps)[:-1] + _dp(pts[wi:], eps)


def simplify(points, eps):
    """Douglas-Peucker on a *closed* loop.

    Passing the loop straight in makes its first and last point the same, so the
    baseline has zero length, every distance measures as zero, and the whole
    contour collapses to two points. Cut the loop at the point furthest from the
    start and simplify the two arcs separately.
    """
    if len(points) < 4:
        return points
    sys.setrecursionlimit(20000)
    x0, y0 = points[0]
    far = max(range(len(points)),
              key=lambda i: (points[i][0] - x0) ** 2 + (points[i][1] - y0) ** 2)
    head = _dp(points[:far + 1], eps)
    tail = _dp(points[far:] + [points[0]], eps)
    return head[:-1] + tail[:-1]


def to_svg(loops, width, height, eps=0.9):
    parts = []
    for loop in loops:
        pts = simplify(loop, eps)
        if len(pts) < 4:
            continue
        d = "M" + " L".join(f"{x} {y}" for x, y in pts) + " Z"
        parts.append(d)
    return width, height, " ".join(parts)


def crop(loops, pad=2):
    """Trim to what was actually drawn, so the mark has no dead margin."""
    xs = [p[0] for l in loops for p in l]
    ys = [p[1] for l in loops for p in l]
    x0, y0 = min(xs) - pad, min(ys) - pad
    w, h = max(xs) - x0 + pad, max(ys) - y0 + pad
    return [[(p[0] - x0, p[1] - y0) for p in l] for l in loops], w, h


if __name__ == "__main__":
    src = sys.argv[1]
    eps = float(sys.argv[2]) if len(sys.argv) > 2 else 0.9
    cut = float(sys.argv[3]) if len(sys.argv) > 3 else 140
    light = len(sys.argv) > 4 and sys.argv[4] == "light"
    w, h, ch, rows = decode_png(src)
    mask = make_mask(w, h, ch, rows, cut, light)
    loops = contours(mask, w, h)
    # Perimeter is a poor filter: a shading streak can be long and enclose
    # almost nothing. Area is the right test — the letter counters are huge,
    # the artefacts from the drawing's hatching are not.
    def area(loop):
        a = 0.0
        for i in range(len(loop)):
            x1, y1 = loop[i]
            x2, y2 = loop[(i + 1) % len(loop)]
            a += x1 * y2 - x2 * y1
        return abs(a) / 2

    loops = [l for l in loops if area(l) > 900]
    loops, W, H = crop(loops)
    print(f"{w}x{h} -> {W}x{H}, {len(loops)} contours", file=sys.stderr)
    _, _, d = to_svg(loops, W, H, eps)
    print(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">'
          f'<path fill="currentColor" fill-rule="evenodd" d="{d}"/></svg>')
