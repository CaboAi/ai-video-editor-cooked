#!/usr/bin/env python3
"""Measure each clip's ink extent so the overlay can fit CONTENT, not frame box.

A HyperFrames clip is 1080x1920, but its drawing almost never fills that. Clip 6
uses 66% of its height; clip 7 uses 93%. Fitting a clip into a half-height overlay
panel by its FRAME box therefore halves everything on screen and the logos stop
reading on a phone. Fitting by its INK box keeps them legible.

This scans the render, not the source, so it measures what actually shipped --
including the drop-shadows, which extend past the elements that cast them.

Usage:
  python3 measure_ink.py <clips-dir> [-o beats.json]
  python3 measure_ink.py <clip.mp4>

Emits a `beats` array ready to paste into the Remotion overlay: clip id, duration,
and the ink band as fractions of the clip's own height. `at` and `mode` are left
for you -- this tool measures, it does not decide layout.

Exit codes: 0 ok - 1 error - 2 usage
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

# Anything darker than this counts as ink. The paper ground is #FDF8EC (~249 grey
# after the luma conversion), so 225 clears the ground and the JPEG ringing around
# it without swallowing the faintest designed element (0.16 opacity ink ~= 210).
INK_THRESHOLD = 225

# 4 samples/sec is enough: an element that exists for less than 250ms is a flash,
# and a flash should not be setting the crop for the whole clip.
SAMPLE_FPS = 4

# Scanning at 270x480 is 1/16th the pixels of full res and lands the band within
# ~4px once scaled back up. The margin in the overlay absorbs that.
SCAN_W, SCAN_H = 270, 480


def duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True).stdout.strip()
    return round(float(out), 3)


def ink_band(path, start=None, end=None, trim=0.0):
    """(top, bottom) as fractions of height across the samples.

    `start`/`end` (clip seconds) measure only the window the beat plays: the
    overlay never shows the rest, so it should not set the fit. `trim` drops the
    extremes seen in fewer than that share of samples. On 2026-09-11 a package
    dropping in from off-frame for two samples set clip 5's band to 0.0 and cost
    it 12% of its size at `full`. trim=0 is the old union of every sample.
    """
    from PIL import Image

    window = []
    if start is not None:
        window += ["-ss", str(start)]
    if end is not None:
        window += ["-t", str(end - (start or 0))]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", *window, "-i", path,
             "-vf", f"fps={SAMPLE_FPS},scale={SCAN_W}:{SCAN_H}",
             os.path.join(tmp, "%04d.png")], check=True)

        frames = sorted(glob.glob(os.path.join(tmp, "*.png")))
        if not frames:
            raise RuntimeError(f"no frames extracted from {path}")

        tops, bottoms = [], []
        for f in frames:
            px = Image.open(f).convert("L").load()
            # Step 2 across: ink is never a single isolated column at this
            # scale, and halving the reads halves the runtime.
            rows = [y for y in range(SCAN_H)
                    if any(px[x, y] < INK_THRESHOLD for x in range(0, SCAN_W, 2))]
            if rows:
                tops.append(rows[0])
                bottoms.append(rows[-1])

    if not tops:
        raise RuntimeError(f"{path} is blank -- no ink found in any sample")
    tops.sort()
    bottoms.sort()
    k = min(len(tops) - 1, int(len(tops) * trim))
    return round(tops[k] / SCAN_H, 4), round(bottoms[-1 - k] / SCAN_H, 4)


def clip_id(path):
    """Leading digits of the filename, else the stem. '3 route to chatbot' -> '3'."""
    stem = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"(\d+)", stem)
    return m.group(1) if m else stem


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="a directory of clip MP4s, or one MP4")
    ap.add_argument("-o", "--out", help="write the beats JSON here")
    ap.add_argument("--from", dest="start", type=float,
                    help="clip seconds the beat starts playing at (its `from`)")
    ap.add_argument("--to", dest="end", type=float,
                    help="clip seconds the beat stops playing at")
    ap.add_argument("--trim", type=float, default=0.0,
                    help="drop extremes seen in under this share of samples, e.g. 0.1")
    args = ap.parse_args()

    if os.path.isdir(args.target):
        clips = sorted(glob.glob(os.path.join(args.target, "*.mp4")))
    elif os.path.isfile(args.target):
        clips = [args.target]
    else:
        print(f"ERROR: no such path: {args.target}", file=sys.stderr)
        return 2

    if not clips:
        print(f"ERROR: no .mp4 files in {args.target}", file=sys.stderr)
        return 2

    beats = []
    for c in clips:
        top, bottom = ink_band(c, args.start, args.end, args.trim)
        dur = duration(c)
        beats.append({"clip": clip_id(c), "dur": dur, "ink": [top, bottom]})
        print(f"{os.path.basename(c):32s} {dur:6.2f}s   ink {top:.3f} -> {bottom:.3f}"
              f"   (band {bottom - top:.3f})")

    payload = json.dumps(beats, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload + "\n")
        print(f"\n-> {args.out}")
    else:
        print("\n" + payload)
    return 0


def demo():
    """A dark bar on light ground across the middle half of the frame measures about 0.25-0.75."""
    with tempfile.TemporaryDirectory() as t:
        v = os.path.join(t, "c.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=270x480:d=1:r=30",
                        "-vf", "drawbox=x=40:y=120:w=190:h=240:color=black:t=fill", "-pix_fmt", "yuv420p", v], check=True)
        top, bot = ink_band(v)
        assert abs(top - 0.25) < 0.03 and abs(bot - 0.75) < 0.03, (top, bot)
    print("demo ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
    else:
        sys.exit(main())
