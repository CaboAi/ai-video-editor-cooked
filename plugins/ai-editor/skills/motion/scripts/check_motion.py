#!/usr/bin/env python3
"""The motion gates from references/build-loop.md, as code instead of prose.

build-loop.md specifies four measurements with exact thresholds and then leaves
each one to be re-derived by whoever is building the clip. Assuming drift works
is how a 0.22px clip shipped; not sampling frame 0 is how an authored-state
flash shipped twice. This runs them.

  smoothness   frame-to-frame delta series: no dead frame, no spike. A clip that
               reads smooth measured min 0.134, max 1.823 over 5.8s.
  entrance     frames 0-3 must rise from bare ground. A clean clip measured
               0 / 96 / 135 / 186 ink px. `fps=1/1` never samples t=0, so the
               contact sheet cannot settle this.
  drift        travel of a RIGID EDGE, not a centroid: ink entering and leaving
               a crop moves the centroid against the element's own motion and
               under-reads by about half.
  reversal     every direction change under 10% of that element's peak velocity.
               On a clip that reads smooth they land at 2-7%.

Downsampling is done by ffmpeg (area average). Never decimate with numpy [::n]:
at a 6px stroke a 1px drift flips sampled pixels between paper and ink and
manufactures spikes that are not in the clip.

Pass index.html too where you have it: a clip carrying a captured screen or a
video panel produces a legitimate 40x delta the moment that media appears, so
its spikes are reported and not failed. Same carve-out as check_composition.py.

Usage:
  python3 check_motion.py <render.mp4> [index.html]       smoothness + entrance
  python3 check_motion.py <render.mp4> --track X,Y,W,H    + drift and reversal

--track measures the ink bounding box inside the crop, so the crop must hold ONE
element for its whole life. A crop that something else enters is measuring the
union of two things, not a track: the tool says so when it sees the jump.
  python3 check_motion.py <render.mp4> --allow-frame0     clip opens with ink

Exit codes: 0 all gates pass - 1 a gate failed - 2 usage

Thresholds live in references/clip-brief-card.md. Change one here, change it there.
"""
import os
import re
import subprocess
import sys

import numpy as np

import ground

SW, SH = 54, 96          # smoothness raster. Area-averaged by ffmpeg.
FPS = 30
DEAD_RUN = 3             # a single near-still frame is invisible. Three in a row
                         # is a 0.1s stall, and a stall next to a move is the
                         # jolt. Fail on the RUN, not on the count.
DEAD_MAX = 0.02          # a delta below this is a frozen frame. Calibrated on
                         # 16 renders: clips that never freeze bottom out at
                         # 0.021-0.160, the frozen ones sit at exactly 0.000.
LEAD_INK = 0.05          # the clip STARTS at the first frame carrying this share
                         # of its peak ink. Before that nothing has entered yet,
                         # and "nothing moved" is the clip opening on bare ground.
TAIL_FRAMES = 4          # and the LAST few frames are exempt too: a drift period
                         # sized to land on a sine turnaround ends at zero
                         # velocity ON PURPOSE (gotcha 13). That is the fix, not
                         # the fault.
SPIKE_LOCAL = 4.0        # x the local median
SPIKE_GLOBAL = 3.0       # and x the global median, so a quiet stretch is safe
RW, RH = 270, 480        # entrance raster: big enough to count a mid-draw stroke
INK_DELTA = 51           # ~20% of full scale off the modal ground counts as ink
FRAME0_MAX = 0.15        # frame 0 ink, as a share of mid-clip ink
TRAVEL_MIN = 8.0         # px. Below this the drift is not visible at all.
REVERSAL_MAX = 0.10      # of that element's peak per-frame speed
JUMP_MAX = 40.0          # px/frame. Above this the bbox is not tracking one
                         # element, it is jumping to something that just entered.


def landscape(path):
    """True for a wider-than-tall clip (a card): the rasters keep their pixel count, turned on their side."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip().split(",")
    return len(out) == 2 and int(out[0]) > int(out[1])


def raster(path, vf, w, h, extra=()):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", vf, *extra,
         "-f", "rawvideo", "-"], capture_output=True).stdout
    n = w * h
    if len(raw) < n:
        sys.exit(f"no frames read from {path}")
    return np.frombuffer(raw, dtype=np.uint8)[:len(raw) // n * n].reshape(-1, h, w)


def ink(f, plate=None):
    """Mask against the frame's OWN modal ground (cream or white), or the dusk ground plate."""
    return ground.mask(f, plate, INK_DELTA)


def rolling_median(x, win=15):
    pad = win // 2
    p = np.pad(x, pad, mode="edge")
    return np.array([np.median(p[i:i + win]) for i in range(len(x))])


def smoothness(path):
    global SW, SH, RW, RH
    if landscape(path) and SW < SH:
        SW, SH, RW, RH = SH, SW, RH, RW
    fs = raster(path, f"fps={FPS},scale={SW}:{SH},format=gray", SW, SH).astype(int)
    d = np.abs(np.diff(fs, axis=0)).mean(axis=(1, 2))
    if len(d) < 4:
        sys.exit("clip too short to measure")

    # Skip the lead-in. A clip that opens on bare ground has nothing to move.
    plate = ground.plate(path, f"scale={SW}:{SH},format=gray", SW, SH)
    share = np.array([ink(f, plate).mean() for f in fs])
    lit = np.where(share > LEAD_INK * share.max())[0]
    start = int(lit[0]) if len(lit) else 0

    end = max(start + 1, len(d) - TAIL_FRAMES)
    loc, glob = rolling_median(d), float(np.median(d[start:end]))
    dead = [i for i in range(start, end) if d[i] < DEAD_MAX]
    spike = [i for i in range(start, end)
             if d[i] > SPIKE_LOCAL * max(loc[i], 1e-6) and d[i] > SPIKE_GLOBAL * glob]
    return d, dead, spike, start, end


def entrance(path):
    """Ink px on frames 0-3, and a mid-clip frame for scale."""
    first = raster(path, f"select='lt(n\\,4)',scale={RW}:{RH},format=gray",
                   RW, RH, extra=["-fps_mode", "passthrough"])
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip())
    mid = raster(path, f"scale={RW}:{RH},format=gray", RW, RH,
                 extra=["-ss", f"{dur / 2:.2f}", "-frames:v", "1"])
    plate = ground.plate(path, f"scale={RW}:{RH},format=gray", RW, RH)
    counts = [int(ink(f, plate).sum()) for f in first[:4]]
    return counts, int(ink(mid[0], plate).sum())


def track(path, box):
    """Per-frame ink bbox inside a crop. Returns the bottom and left edge series."""
    x, y, w, h = box
    fs = raster(path, f"crop={w}:{h}:{x}:{y},fps={FPS},format=gray", w, h)
    plate = ground.plate(path, f"crop={w}:{h}:{x}:{y},format=gray", w, h)
    bot, left = [], []
    for f in fs:
        m = ink(f, plate)
        rows, cols = np.where(m.any(axis=1))[0], np.where(m.any(axis=0))[0]
        if not len(rows):
            continue
        bot.append(float(rows[-1]))
        left.append(float(cols[0]))
    return np.array(bot), np.array(left)


def reversals(series):
    """[(index, speed at the turn / peak speed)] for every direction change.

    Tracks the last NON-ZERO direction, so a turn with a still frame in the
    middle of it still counts as one turn rather than disappearing.
    """
    d = np.diff(series)
    peak = float(np.max(np.abs(d))) if len(d) else 0.0
    out, sign, speed = [], 0, 0.0
    for i, v in enumerate(d):
        if v == 0:
            continue
        s = np.sign(v)
        if sign and s != sign:
            out.append((i, speed / peak if peak else 0.0))
        sign, speed = s, abs(v)
    return out, peak


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    path = argv[1]
    html = argv[2] if len(argv) > 2 and argv[2].endswith(".html") else None
    media = bool(html and re.search(r"<img[\s>]|<video[\s>]", open(html, encoding="utf-8").read()))
    box = None
    if "--track" in argv:
        box = [int(v) for v in argv[argv.index("--track") + 1].split(",")]
    allow0 = "--allow-frame0" in argv
    fails, warns, measures = [], [], {}

    d, dead, spike, start, end = smoothness(path)
    body = d[start:end]
    measures |= {"delta_min": round(float(body.min()), 3),
                 "delta_max": round(float(body.max()), 3)}
    spread = (f"spread {body.max() / body.min():5.1f}x" if body.min() > 0
              else "FROZEN somewhere")
    print(f"frame delta    min {body.min():5.3f}  max {body.max():5.3f}  {spread}"
          f"{f'   (from t={start / FPS:.2f}s, before that the frame is bare)' if start else ''}")
    if dead:
        run = best = 1
        at = first = dead[0]
        for a, b in zip(dead, dead[1:]):
            run = run + 1 if b == a + 1 else 1
            if run > best:
                best, at = run, b - run + 1
        measures["dead_run"] = best
        verdict = "STALL" if best >= DEAD_RUN else "warn "
        (fails if best >= DEAD_RUN else warns).append("dead frames")
        print(f"  {verdict} {len(dead)} near-still frame(s), longest run {best} "
              f"({best / FPS:.2f}s) from t={at / FPS:.2f}s, first at "
              f"t={first / FPS:.2f}s -- a stall next to a move is the jolt. "
              "Overlap the beats: start the next while the last is still settling.")
    if spike:
        (warns if media else fails).append("motion spikes")
        for i in spike[:4]:
            print(f"  SPIKE t={i / FPS:.2f}s  delta {d[i]:.3f} vs local median "
                  f"{np.median(d[max(0, i - 7):i + 8]):.3f}")
        if media:
            print("  (reported, not failed: this clip carries captured media, and "
                  "a screen arriving is a real content change)")

    counts, mid = entrance(path)
    measures |= {"frame0_ink": counts[0], "mid_ink": mid}
    share = counts[0] / mid if mid else 0
    print(f"entrance ramp  {' / '.join(str(c) for c in counts)} ink px   "
          f"(mid-clip {mid}, frame 0 is {share * 100:.0f}%)")
    if share > FRAME0_MAX and not allow0:
        (warns if media else fails).append("frame 0 flash")
        print("  FLASH frame 0 already carries the drawing. A late fromTo needs "
              "its from-state in CSS *and* immediateRender:false (gotcha 12).")
        if media:
            print("  (reported, not failed: a captured screen in this clip is "
                  "legitimately on the canvas before the camera reaches it)")
    if counts != sorted(counts):
        warns.append("ramp not rising")
        print("  warn  ink is not rising across frames 0-3")

    if box:
        bot, left = track(path, box)
        if len(bot) < 4:
            fails.append("nothing tracked")
            print(f"  EMPTY no ink in crop {box}")
        else:
            for name, s in (("vertical", bot), ("horizontal", left)):
                travel = float(s.max() - s.min())
                revs, peak = reversals(s)
                worst = max((r for _, r in revs), default=0.0)
                measures[f"travel_{name}"] = round(travel, 1)
                measures[f"reversal_{name}"] = round(worst, 3)
                print(f"{name:14s} travel {travel:5.1f}px  peak "
                      f"{peak:5.2f}px/frame  worst reversal {worst * 100:5.1f}% "
                      f"of peak ({len(revs)} turns)")
                if peak > JUMP_MAX:
                    warns.append(f"{name} crop not isolated")
                    print(f"  CROP  {peak:.0f}px in one frame is something "
                          "entering or leaving the crop, not an element moving. "
                          "Re-crop around ONE element and read this again.")
                    continue
                if travel < TRAVEL_MIN:
                    fails.append(f"{name} travel")
                    print(f"  STILL {travel:.1f}px is not visible motion. Derive "
                          "the period from the element's VISIBLE life (gotcha 13).")
                if worst > REVERSAL_MAX:
                    fails.append(f"{name} reversal")
                    print("  KINK  a direction reversed at speed. Split the axes so "
                          "the turn happens at zero velocity on that axis.")

    print()
    if fails:
        print("FAIL: " + ", ".join(fails))
        return 1
    print("pass. Drift and entrances measure clean. Still look at the frames.")
    return 0


def demo():
    """Self-check: a reversal at speed is caught, one at rest is not."""
    kink = np.concatenate([np.arange(0, 20, 2.0), np.arange(18, -2, -2.0)])
    # the plateau at the join is deliberate: a turn with a still frame in it is
    # still a turn, and the old adjacent-pair test lost exactly that case
    revs, peak = reversals(kink)
    assert revs and revs[0][1] > REVERSAL_MAX, f"kink missed: {revs}"

    smooth = 10 * np.sin(np.linspace(0, 2 * np.pi, 120))
    revs, peak = reversals(smooth)
    assert revs, "no turn found in a sine"
    assert max(r for _, r in revs) < REVERSAL_MAX, "sine turn should be gentle"

    flat = np.ones(40) * 5
    assert float(flat.max() - flat.min()) < TRAVEL_MIN, "flat should fail travel"
    print("demo ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("demo", "--demo"):
        demo()
    else:
        sys.exit(main(sys.argv))
