#!/usr/bin/env python3
"""Split the traced OB into the pieces the site actually needs.

  mark.svg   the full monogram, currentColor, for the header and the tile
  icon.svg   the O alone on its tile, self-coloured, for the favicon

The favicon can't use currentColor — it renders outside any CSS context — so it
carries its own colours. And it's the O rather than OB because at 16px two
letters of bone are a smudge; one is still a shape.
"""
import re
import sys

src = open(sys.argv[1]).read()
vb = re.search(r'viewBox="0 0 (\d+) (\d+)"', src)
W, H = int(vb.group(1)), int(vb.group(2))
d = re.search(r'\sd="([^"]+)"', src).group(1)

subpaths = ["M" + s.strip() for s in d.split("M") if s.strip()]


def bbox(sub):
    pts = [tuple(map(float, p.split())) for p in re.findall(r"[ML]\s*([-\d.]+ [-\d.]+)", sub)]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


boxes = [bbox(s) for s in subpaths]
# The gap between the two letters is the widest empty vertical band.
mid = (max(b[2] for b in boxes) + min(b[0] for b in boxes)) / 2
left = [s for s, b in zip(subpaths, boxes) if (b[0] + b[2]) / 2 < mid]
lb = [b for b in boxes if (b[0] + b[2]) / 2 < mid]
print(f"{len(subpaths)} subpaths -> {len(left)} in the O", file=sys.stderr)

x0 = min(b[0] for b in lb); y0 = min(b[1] for b in lb)
x1 = max(b[2] for b in lb); y1 = max(b[3] for b in lb)
ow, oh = x1 - x0, y1 - y0


def shift(sub, dx, dy):
    return re.sub(r"([ML])\s*([-\d.]+) ([-\d.]+)",
                  lambda m: f"{m.group(1)}{float(m.group(2)) - dx:.1f} {float(m.group(3)) - dy:.1f}",
                  sub)


o_path = " ".join(shift(s, x0, y0) for s in left)

open("mark.svg", "w").write(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" fill="currentColor">'
    f'<path fill-rule="evenodd" d="{d}"/></svg>')

# 32-unit tile, the O inset so it breathes; nested viewBox does the scaling.
pad, box = 5, 32
iw = box - pad * 2
ih = iw * oh / ow
open("icon.svg", "w").write(
    f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {box} {box}">'
    f'<rect width="{box}" height="{box}" rx="7" fill="#2f5d8a"/>'
    f'<svg x="{pad}" y="{(box - ih) / 2:.1f}" width="{iw}" height="{ih:.1f}"'
    f' viewBox="0 0 {ow:.0f} {oh:.0f}">'
    f'<path fill="#ffffff" fill-rule="evenodd" d="{o_path}"/></svg></svg>')

print("wrote mark.svg and icon.svg", file=sys.stderr)
