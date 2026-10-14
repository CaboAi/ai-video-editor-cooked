#!/usr/bin/env python3
"""Three composition gates, measured on the render. See visual-system.md.

Density and "does it look designed" are not the same thing, and the numbers that
separate them were derived by ranking 25 in-system clips by eye and then finding
what sorted with the ranking:

  dead band     tallest fully-empty horizontal run. The strongest single
                predictor. Best four clips 0.11-0.20, worst four 0.31-0.43.
  ink coverage  share of the frame that is not ground, at the densest frame.
                Necessary, not sufficient: ink crammed into a strip still fails
                the band (ratelimit-bucket, 17.4% coverage, band 0.27).
  stroke range  distinct stroke-widths and their spread. A background plane needs
                a weight lighter than the connectors; one weight reads flat.
                Measured as RENDERED, not as authored : see stroke_widths().
                The SPREAD floor depends on the aspect : see SPREAD_MIN_WIDE.

THESE GATES ARE FOR DRAWN BEATS. A clip that is mostly a captured screen or a
video panel scores 0 stroke weights (a card drawn with a CSS border has no
stroke-width attribute at all) and often fails the dead band, because one screen
in a card legitimately occupies a band. Read a FAIL on such a clip as "not a
drawn beat", not as a defect. Judge those against the "Captured screens" rules in
visual-system.md instead.

Usage:
  python3 check_composition.py <render.mp4> [index.html]

Exit codes: 0 all gates pass - 1 a gate failed - 2 usage

Thresholds live in references/clip-brief-card.md. Change one here, change it there.
"""
import os
import re
import subprocess
import sys

import numpy as np

import ground

W, H = 108, 192          # 1/10 scale keeps a 6px stroke ~0.6px, still detectable
FPS = 4
LUMA_DELTA = 12          # off the modal ground by this much counts as ink
BAND_MAX = 0.25
STRIP_MAX = 0.30       # looser than the band: a 9:16 frame has width to spare
COVER_MIN = 0.06
WEIGHTS_MIN = 3

# NOT RE-FITTED. 2.5 was set on the original 25-clip ranking, when this gate read
# authored stroke-widths and ignored viewBox scaling. stroke_widths() now
# measures rendered widths, so the number below predates the measurement it
# governs. On corrected numbers three reels move under it (one 9.72 -> 1.67,
# one 5.62 -> 2.25, one 3.12 -> 1.39) and two move over it, so
# the population is about where it was (17 of 40 below before, 18 after) but
# the first is one of the four the ranking put at the TOP. Re-fitting this wants
# the ranking, not this file. Flagged 2026-09-20.
SPREAD_MIN = 2.5         # 9:16 reels

# 16:9, the cream-and-ink diagrams. FITTED, NOT GUESSED : do not raise this back
# to 2.5 (three agents had to write up that 2.5 failed every approved clip).
#
# The reels put a 300px subject mark beside a 1.6px hairline, so 2.5x is easy
# there. The cream-and-ink 16:9 look holds a deliberately narrow band: rows and
# connectors at 4, cards at 5, subject marks at 6-7. At 2.5x the gate failed
# EVERY approved clip in the family, which means it was not measuring it.
#
# RE-FITTED 2026-09-20 on RENDERED widths, after stroke_widths() started
# applying viewBox scale. The first fit (1.35) was made on authored numbers and
# is superseded : those figures described a different measurement, so do not
# reconcile the two tables. Rendered spread over the 14 clips of the family:
#
#   1.50  three clips  [8, 10, 12]
#          one clip  [4, 5, 5.14, 6]
#   1.75  one clip  [4, 5, 6, 6.35, 7]
#   1.88  one clip  [4, 5, 6, 7.5]
#   1.91  one clip  [6.27, 7.27, 7.5, 9.09, 10, 12]
#   2.39  one clip  [3.14, 5, 6.12, 7.5]
#   2.50  two clips
#   3.00  three clips
#   3.94  one clip
#
# Floor = the family minimum (1.50) less a 0.05 notch, so a clip that lands
# exactly on the minimum is not failed by a float comparison. It still catches
# flat: one weight is 1.00, a uniformly-scaled [4,5] is 1.25, [5,6] is 1.20.
#
# CAVEAT, and the reason `stroke weights` stays at 3 for both aspects: aspect is
# a proxy for the look, not the look itself. Older 16:9 diagrams are a
# different system and measure up to 15.3x, so this floor no longer catches
# flatness in one of those. The weights count is what carries that case.
SPREAD_MIN_WIDE = 1.45


def frames(path):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vf",
         f"fps={FPS},scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
        capture_output=True).stdout
    n = W * H
    if len(raw) < n:
        sys.exit(f"no frames read from {path}")
    return np.frombuffer(raw, dtype=np.uint8)[:len(raw) // n * n].reshape(-1, H, W)


def analyse(f, plate=None):
    """Ink mask against the frame's OWN modal ground, or the dusk ground plate."""
    return ground.mask(f, plate, LUMA_DELTA)


NUM = r'[-+]?[0-9]*\.?[0-9]+'
SW = r'stroke-width[:="\s]+(%s)' % NUM


def stroke_widths(src):
    """RENDERED stroke-widths, distinct, sorted, 2dp.

    An authored `stroke-width` is in viewBox units, so what the viewer sees is
    that number times the svg's own scale. Reading the attributes flat measures
    the author's intent instead of the picture: clip authors [4, 5, 6]
    and renders [6.27, 7.27, 7.5, 9.09, 10, 12] : six weights, not three,
    because its parts are drawn at 1.5x to 2x. The proxy agreed with the render
    across most of the family only because most scales sit near 1.0, and it
    produced a false positive on the one clip with a sub-1.0 scale (clip,
    a laptop at 220/280). Fixed 2026-09-20.

    Scale is min(width/viewBox-w, height/viewBox-h), which is what the default
    preserveAspectRatio="xMidYMid meet" actually applies.

    CEILING: a `transform="scale(...)"` on an inner <g> is not walked. Mirrors
    (scale(-1,1)) are magnitude 1 so they do not matter, and nothing in the
    house system scales a group for size. If that changes, walk the stack.
    """
    out, spans = [], []
    for m in re.finditer(r'<svg\b([^>]*)>(.*?)</svg>', src, re.S):
        attrs, body = m.group(1), m.group(2)
        spans.append((m.start(), m.end()))
        w = re.search(r'\bwidth="(%s)"' % NUM, attrs)
        h = re.search(r'\bheight="(%s)"' % NUM, attrs)
        vb = re.search(r'viewBox="\s*%s[\s,]+%s[\s,]+(%s)[\s,]+(%s)'
                       % (NUM, NUM, NUM, NUM), attrs)
        scale = 1.0
        if w and vb and float(vb[1]) > 0:
            sx = float(w[1]) / float(vb[1])
            sy = float(h[1]) / float(vb[2]) if h and float(vb[2]) > 0 else sx
            scale = min(sx, sy)
        # the svg's OWN attrs carry a stroke-width too, and missing those was
        # its own bug: it dropped the heaviest mark in six clips.
        out += [round(float(x) * scale, 2) for x in re.findall(SW, attrs + body)]
    outside = src
    for a, b in reversed(spans):
        outside = outside[:a] + outside[b:]
    out += [round(float(x), 2) for x in re.findall(SW, outside)]   # CSS rules
    return sorted(set(out))


def is_wide(src):
    """True for a 16:9 clip. Read off the root's own data attrs, which
    check_clip.py already requires, so this costs no extra ffprobe. Unreadable
    attrs fall back to the stricter 9:16 floor."""
    w = re.search(r'data-width="([0-9.]+)"', src)
    h = re.search(r'data-height="([0-9.]+)"', src)
    if not (w and h) or float(h[1]) == 0:
        return False
    return float(w[1]) > float(h[1])


def longest_empty(mask_axis):
    """Tallest run of empty rows (or columns) as a fraction."""
    empty = ~mask_axis
    best = run = 0
    for e in empty:
        run = run + 1 if e else 0
        best = max(best, run)
    return best / len(empty)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    path = sys.argv[1]

    fs = frames(path)
    plate = ground.plate(path, f"scale={W}:{H},format=gray", W, H)
    masks = [analyse(f, plate) for f in fs]
    cover = [m.mean() for m in masks]
    peak = int(np.argmax(cover))
    m = masks[peak]

    band = longest_empty(m.any(axis=1))      # empty rows
    strip = longest_empty(m.any(axis=0))     # empty columns
    fails = []

    print(f"densest frame  t={peak / FPS:.1f}s")
    print(f"ink coverage   {cover[peak] * 100:5.1f}%   (>= {COVER_MIN * 100:.0f}%)")
    print(f"dead band      {band:5.2f}    (<= {BAND_MAX})")
    print(f"dead strip     {strip:5.2f}    (<= {STRIP_MAX})")
    if cover[peak] < COVER_MIN:
        fails.append("ink coverage")
    if band > BAND_MAX:
        fails.append("dead band")
    if strip > STRIP_MAX:
        fails.append("dead strip")

    if plate is not None:
        print("stroke range   n/a  (dusk look: depth comes from glass, z and blur, not stroke weight)")
    elif len(sys.argv) > 2:
        src = open(sys.argv[2], encoding="utf-8").read()
        w = stroke_widths(src)
        spread = (w[-1] / w[0]) if w and w[0] > 0 else 0
        wide = is_wide(src)
        floor = SPREAD_MIN_WIDE if wide else SPREAD_MIN
        print(f"stroke weights {len(w):5d}    (>= {WEIGHTS_MIN})  {w}")
        print(f"stroke spread  {spread:5.2f}x  (>= {floor}x, "
              f"{'16:9 diagram' if wide else '9:16 reel'})")
        if len(w) < WEIGHTS_MIN:
            fails.append("stroke weights")
        if spread < floor:
            fails.append("stroke spread")

    print()
    if fails:
        print("FAIL: " + ", ".join(fails))
        print("Gates are necessary, not sufficient : still look at the frames.")
        print("If this clip is mostly a captured screen or video, these gates do")
        print("not apply to it : see 'Captured screens' in visual-system.md.")
        return 1
    print("pass. Now look at the frames: no gate sees a beat that is simply wrong.")
    return 0


def demo():
    """Self-check: a strip of ink must fail the band, a spread must pass."""
    strip = np.zeros((192, 108), bool)
    strip[80:110, :] = True
    assert longest_empty(strip.any(axis=1)) > BAND_MAX, "strip should fail"
    spread = np.zeros((192, 108), bool)
    spread[::7, ::5] = True
    assert longest_empty(spread.any(axis=1)) <= BAND_MAX, "spread should pass"

    # the aspect carve-out: a real 16:9 root reads wide, a 9:16 one does not,
    # and anything unreadable falls back to the stricter floor
    root = 'data-width="%s" data-height="%s"'
    assert is_wide(root % (1920, 1080)), "16:9 root should read wide"
    assert not is_wide(root % (1080, 1920)), "9:16 root should not"
    assert not is_wide('<div data-hyperframes-root>'), "missing attrs -> strict"
    assert not is_wide(root % (1920, 0)), "zero height -> strict"

    # stroke_widths scales by viewBox, so an authored 4 drawn at 2x is an 8 on
    # screen. This is the whole point of the 2026-09-20 fix.
    two_x = '<svg width="680" height="600" viewBox="0 0 340 300" stroke-width="6">' \
            '<rect stroke-width="4"/><rect stroke-width="5"/></svg>'
    assert stroke_widths(two_x) == [8.0, 10.0, 12.0], stroke_widths(two_x)
    # a sub-1.0 scale is the case the flat regex got wrong (a laptop drawing)
    small = '<svg width="220" height="147" viewBox="0 0 280 187">' \
            '<rect stroke-width="5"/></svg>'
    assert stroke_widths(small) == [3.93], stroke_widths(small)
    # a stroke-width on the <svg> tag itself must not be dropped
    assert stroke_widths('<svg stroke-width="6"></svg>') == [6.0]
    # no viewBox, and a CSS rule outside any svg, both scale 1.0
    assert stroke_widths('<svg width="99"><rect stroke-width="3"/></svg>') == [3.0]
    assert stroke_widths('.x { stroke-width: 2.5; }') == [2.5]

    # the fitted floor: the family minimum passes, flat still fails
    assert 12.0 / 8.0 >= SPREAD_MIN_WIDE, "[8,10,12] is the family min, must pass"
    assert 10.0 / 8.0 < SPREAD_MIN_WIDE, "a uniform [4,5] is flat, must fail"
    assert 12.0 / 8.0 < SPREAD_MIN, "the 9:16 floor must stay where it was"
    print("demo ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("demo", "--demo"):
        demo()
    else:
        sys.exit(main())
