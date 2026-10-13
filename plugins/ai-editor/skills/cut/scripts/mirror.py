#!/usr/bin/env python3
"""Is the take mirrored? A phone's front camera often saves the picture flipped, so a book, a shirt or a
sign in the shot reads backwards. Text is the tell: OCR a few frames as they are and flipped, and when the
flipped frames read far better, the take is mirrored.

    python3 run.py mirror.py <source> <edit_dir>      writes <edit_dir>/mirror.json {"mirrored": true|false, ...}
    python3 run.py mirror.py demo                     self-check

Many creators post their front-camera takes mirrored, as they saw themselves, so this never flips on its own:
the cut skill asks, and render.py --flip flips cut.mp4 on a yes.
No text in the shot, or no OCR engine (Apple Vision via ocrmac on a Mac, else RapidOCR): "mirrored" is false.
Exit codes: 0 ok, 1 error, 2 usage
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SAMPLES = 8
RATIO = 2.0      # flipped must read this many times better than as shot
MIN_SCORE = 30   # and have at least this much readable text (about two short words at full confidence)


def score(found):
    """found: [(text, confidence)]. Letters in real-looking words, weighted by confidence squared, so a
    low-confidence guess at mirrored letters counts for little."""
    return sum(c * c * len(w) for t, c in found if c >= 0.5 for w in re.findall(r"[A-Za-z]{3,}", t))


def decide(plain, flipped):
    return flipped >= MIN_SCORE and flipped >= RATIO * max(plain, 1.0)


def reader():
    """fn(PIL image) -> [(text, confidence)], or None with no OCR engine."""
    try:
        from ocrmac import ocrmac
        return lambda im: [(t, c) for t, c, _ in ocrmac.OCR(im, recognition_level="accurate").recognize()]
    except ImportError:
        pass
    try:
        import numpy as np
        from rapidocr_onnxruntime import RapidOCR
        eng = RapidOCR()
        return lambda im: [(t, float(c)) for _, t, c in (eng(np.asarray(im.convert("RGB"))[:, :, ::-1])[0] or [])]
    except ImportError:
        return None


def duration(src):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def check(src):
    from PIL import Image, ImageOps
    read = reader()
    if read is None:
        return {"mirrored": False, "why": "no OCR engine"}
    dur = duration(src)
    plain = flipped = 0.0
    with tempfile.TemporaryDirectory() as tmp:
        for i in range(SAMPLES):
            f = Path(tmp) / f"{i}.png"
            t = dur * (i + 0.5) / SAMPLES
            subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(src), "-frames:v", "1",
                            "-vf", "scale=-2:960", str(f), "-y"], check=True)
            im = Image.open(f).convert("RGB")
            plain += score(read(im))
            flipped += score(read(ImageOps.mirror(im)))
    return {"mirrored": decide(plain, flipped), "plain": round(plain, 1), "flipped": round(flipped, 1)}


def demo():
    # a cover read cleanly once flipped, garbage as shot (measured on a real front-camera take)
    shot = [("V FEYDENERIL IVETE", 1.0), ("no vielass", 0.3), ("EM 1ONE", 0.5)]
    flip = [("JORDAN AVERY", 1.0), ("The SEVEN", 1.0), ("HABITS", 1.0), ("of a GOOD TEAM", 1.0)]
    assert decide(score(shot), score(flip)) and not decide(score(flip), score(shot))
    assert not decide(0, 12), "a stray word is not enough"
    assert not decide(40, 60), "both read: not mirrored"
    assert score([("hello world", 0.4)]) == 0
    read = reader()
    if read:
        from PIL import Image, ImageDraw, ImageFont, ImageOps
        im = Image.new("RGB", (1500, 200), "white")
        try:
            font = ImageFont.truetype("Arial.ttf", 64)
        except OSError:
            font = ImageFont.load_default(size=64)
        ImageDraw.Draw(im).text((30, 60), "Readable words appear here on this cover page", fill="black", font=font)
        back = ImageOps.mirror(im)
        assert decide(score(read(back)), score(read(im))), "text drawn backwards reads after the flip"
    print("mirror demo ok" + ("" if read else " (no OCR engine: OCR part skipped)"))
    return 0


def main(argv):
    if argv == ["demo"]:
        return demo()
    if len(argv) != 2 or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 2
    src, d = Path(argv[0]), Path(argv[1])
    if not src.exists():
        print(f"no such file: {src}", file=sys.stderr)
        return 1
    d.mkdir(parents=True, exist_ok=True)
    got = check(src)
    (d / "mirror.json").write_text(json.dumps(got), encoding="utf-8")
    print("mirrored: text in the shot reads backwards (a front-camera take). Ask before flipping (render.py --flip)"
          if got["mirrored"] else f"not mirrored ({got.get('why') or 'text reads as shot, or no text'})")
    print(d / "mirror.json")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
