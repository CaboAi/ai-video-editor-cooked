#!/usr/bin/env python3
"""When each clip's animation starts and finishes moving.

A beat that is shorter than its clip's motion cuts the animation off mid-build.
This prints, per clip, the window in which it is actually animating, so the
beat can be given a `from` that lands the motion on the words AND finishes it
inside the beat.

    python3 clip_motion.py edits/NAME/images/motion-01-over.mp4
    python3 clip_motion.py edits/NAME/motion          every clip.mp4 under it
    python3 clip_motion.py demo

motion.py place reads it: a card that leaves before `motion end` cuts the build off.
"""
import argparse
import pathlib
import subprocess
import sys

W, H, FPS = 54, 96, 30


def motion(path):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path),
         "-vf", f"fps={FPS},scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    import numpy as np

    a = np.frombuffer(raw, dtype="uint8")
    n = a.size // (W * H)
    if n < 2:
        return None
    a = a[: n * W * H].reshape(n, H, W).astype("int16")
    d = np.abs(np.diff(a, axis=0)).mean(axis=(1, 2))
    # Relative to this clip's own biggest move: house clips animate softly and
    # an absolute threshold either misses them all or fires on compression noise.
    thr = max(0.25, float(d.max()) * 0.06)
    moving = [i for i, v in enumerate(d) if v > thr]
    dur = n / FPS
    if not moving:
        return dur, 0.0, 0.0
    return dur, (moving[0] + 1) / FPS, (moving[-1] + 1) / FPS


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="a clip, or a directory of them")
    args = ap.parse_args()

    try:
        import numpy  # noqa: F401
    except ImportError:
        sys.exit("needs numpy: python3 -m pip install numpy")

    p = pathlib.Path(args.target)
    clips = sorted(p.rglob("*.mp4")) if p.is_dir() else [p]
    if not clips:
        sys.exit(f"no .mp4 under {p}")

    for c in clips:
        m = motion(c)
        if m is None:
            print(f"{c.name}: could not decode")
            continue
        dur, first, last = m
        print(f"{c.name:<12} dur {dur:5.2f}s   motion {first:5.2f}-{last:5.2f}s"
              f"   needs {last - first:5.2f}s   tail hold {dur - last:4.2f}s")


def demo():
    """A box that moves from 0.5 s to 1.5 s of a 2 s clip: motion about 0.5-1.5."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        v = pathlib.Path(t) / "c.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=white:s=108x192:d=2:r=30",
                        "-f", "lavfi", "-i", "color=c=black:s=40x40:d=2:r=30", "-filter_complex",
                        "overlay=x='if(lt(t,0.5),0,if(lt(t,1.5),(t-0.5)*60,60))':y=60", "-pix_fmt", "yuv420p", str(v)],
                       check=True)
        dur, first, last = motion(v)
        assert abs(first - 0.5) < 0.15 and abs(last - 1.5) < 0.15 and abs(dur - 2) < 0.05, (dur, first, last)
    print("demo ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo()
    else:
        main()
