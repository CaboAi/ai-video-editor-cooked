#!/usr/bin/env python3
"""A style's picture and example video, both cut from one of its own finished edits. Needs ffmpeg; the eyes-open
check needs OpenCV (the editor's venv: run it with run.py) and is skipped without it.

    python3 thumbs.py make <edit dir> <style dir> [--at SEC] [--clip SEC] [--sheet out.jpg]
    python3 thumbs.py demo        self-check

<edit dir> is a finished style-edit folder: render.mp4, render.plan.json (or plan.json) and face.json.
<style dir> gets:
  thumb.jpg    720x1280, the frame that shows the style best: a card fully in (not entering or leaving),
               the biggest one, a built card over a lone logo, a caption on screen, the frame still (no move mid-transition), not too dark,
               the speaker's eyes open (no blink, no half-shut lids)
  thumb.json   {"focus": [x, y], "t", "clip": [start, end]}: the window crops the picture with focus as
               CSS object-position, chosen so the card and the head both stay inside a 4:5 card and a
               wide shelf tile
  example.mp4  8-15 s, the stretch with the most cards and titles on screen, 720p H.264, under 3 MB
--at and --clip override the picked frame and clip start. --sheet writes the ~40 candidate frames as a
contact sheet (the picked one framed) to check by eye. Creator styles keep their account picture; this
is for the plugin's sample styles and for styles built here that have a demo edit.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

CLIP_S, MIN_S, MAX_MB = 12.0, 8.0, 3.0
SHUT = 0.9   # eyes under this share of the speaker's median openness count as shut
SHAPES = (4 / 5, 150 / 124)   # width / height of the crops a 9:16 picture is shown in (styles card, Home shelf tile)


def ff(*args, timeout=300):
    return subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], capture_output=True, timeout=timeout)


def duration(v):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(v)],
                       capture_output=True, text=True, timeout=30)
    return float(r.stdout.strip() or 0)


def load(edit):
    edit = Path(edit)
    plan = next(json.loads(f.read_text(encoding="utf-8")) for f in (edit / "render.plan.json", edit / "plan.json") if f.is_file())
    try:
        heads = json.loads((edit / "face.json").read_text(encoding="utf-8")).get("heads") or []
    except (OSError, ValueError):
        heads = []
    return plan, heads, edit / "render.mp4"


def elements(plan):
    """[(start, end, settled_from, settled_to, rect [x, y, w, h] in %)]: every card and title text."""
    out = []
    for c in plan.get("cards") or []:
        enter = ((c.get("props") or {}).get("enter") or {}).get("dur") or 0.35
        rect = c.get("key") or c.get("box")
        if rect:
            out.append((c["start"], c["end"], c["start"] + enter + 0.12, c["end"] - 0.25, rect))
    for t in plan.get("texts") or []:
        h = (t.get("size_pct") or 4) * 2.5
        out.append((t["start"], t["end"], t["start"] + 0.4, t["end"] - 0.3, [15, (t.get("y_pct") or 50) - h / 2, 70, h]))
    return out


def head_at(heads, t):
    h = min(heads, key=lambda h: abs(h["t"] - t), default=None)
    return h and h.get("box")


def grey(video, t):
    return ff("-ss", f"{max(t, 0):.2f}", "-i", video, "-frames:v", "1", "-vf", "scale=24:42,format=gray", "-f", "rawvideo", "-").stdout


def openness(grey_eye):
    """How open one eye is, from a grey patch around its centre (0.6 x 0.4 of the distance between the eyes): the
    share of rows that hold a dark blob (iris and pupil). An open eye's iris is round and fills many rows; a shut
    lid leaves one thin dark lash line."""
    rows = [list(map(int, r)) for r in grey_eye]
    px = sorted(v for r in rows for v in r)
    dark = px[int(0.12 * (len(px) - 1))]
    return sum(sum(v <= dark for v in r) > 0.12 * len(r) for r in rows) / len(rows)


def eyes(video, ts):
    """{t: openness of both eyes (mean) or None (no face, or no OpenCV)} at each t, from YuNet's eye landmarks."""
    try:
        import cv2
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from ai_editor import models
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        cap, model = cv2.VideoCapture(str(video)), str(models.fetch("yunet.onnx", check=False))
    except Exception:   # noqa: BLE001  no OpenCV or no model: no eye check
        return {t: None for t in ts}
    out, det = {}, None
    for t in ts:
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, fr = cap.read()
        if not ok:
            out[t] = None
            continue
        k = 360 / fr.shape[1]
        sm = cv2.resize(fr, (360, round(fr.shape[0] * k)))
        det = det or cv2.FaceDetectorYN.create(model, "", (sm.shape[1], sm.shape[0]), 0.6)
        faces = det.detect(sm)[1]
        if faces is None:
            out[t] = None
            continue
        f = max(faces, key=lambda r: r[2] * r[3]) / k
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        d = max(8.0, float(((f[6] - f[4]) ** 2 + (f[7] - f[5]) ** 2) ** 0.5))
        pats = [g[max(0, int(y - .2 * d)):int(y + .2 * d), max(0, int(x - .3 * d)):int(x + .3 * d)] for x, y in (f[4:6], f[6:8])]
        out[t] = sum(map(openness, pats)) / 2 if all(p.size for p in pats) else None
    cap.release()
    return out


def focus(rects):
    """CSS object-position [x, y] % that keeps every rect (in % of a 9:16 frame) inside each SHAPES crop."""
    top, bot = min(r[1] for r in rects) / 100, max(r[1] + r[3] for r in rects) / 100
    lo, hi = 0.0, 1.0
    for shape in SHAPES:
        v = min(1.0, (9 / 16) / shape)   # the share of the height that crop shows
        if v >= 1:
            continue
        a0, a1 = max(0.0, bot - v), min(top, 1 - v)   # where the window's top may sit
        if a0 > a1:   # cannot fit both: keep the top (the card) and as much head as fits
            a0 = a1 = min(top, 1 - v)
        lo, hi = max(lo, a0 / (1 - v)), min(hi, a1 / (1 - v))
    y = (lo + hi) / 2 if lo <= hi else (top + bot) / 2
    x = (min(r[0] for r in rects) + max(r[0] + r[2] for r in rects)) / 2
    return [round(x, 1), round(y * 100, 1)]


def pick(plan, heads, video, n=40):
    """The best frame: [(score, t, rects)] of up to n candidates, best first."""
    els = elements(plan)
    caps = (plan.get("captions") or {}).get("chunks") or []
    ts = sorted({round(a + (b - a) * k / m, 2) for _, _, a, b, _ in els if b > a for m in [max(2, int((b - a) / 0.4))] for k in range(m + 1)})   # every ~0.4 s of each card's settled stretch
    ts = [ts[i * len(ts) // n] for i in range(n)] if len(ts) > n else ts   # spread over the whole edit, not its first n
    out, ts = [], [t for t in ts if any(a <= t <= b for _, _, a, b, _ in els)]
    eye = eyes(video, ts)
    logos = [(c["start"], c["end"]) for c in plan.get("cards") or [] if ((c.get("anim") or {}).get("type") if isinstance(c.get("anim"), dict) else None) == "logo"]
    seen = sorted(v for v in eye.values() if v)
    mid = seen[len(seen) // 2] if seen else None   # this speaker's usual openness: shut is judged against it
    for t in ts:
        on = [r for _, _, a, b, r in els if a <= t <= b]
        g0, g1 = grey(video, t - 0.08), grey(video, t + 0.08)
        if not g0 or len(g0) != len(g1):
            continue
        still = sum(abs(p - q) for p, q in zip(g0, g1)) / len(g0) / 255   # 0 = the frame does not move
        light = sum(g0) / len(g0)
        area = max(r[2] * r[3] for r in on) / 100 / 30   # a 30% x 1/3 card scores ~1
        cap = any(c["start"] <= t < c["end"] for c in caps)
        score = area + 0.2 * cap - 12 * still - (0.5 if not 45 < light < 215 else 0)
        if any(a <= t <= b for a, b in logos) and len(on) == 1:   # a lone logo shows less of the style than a built card
            score -= 0.4
        if mid and eye[t] is not None:   # a blink or half-shut lids rule the frame out; wide open scores a little higher
            score += -5 if eye[t] < SHUT * mid else 0.5 * (eye[t] / mid - 1)
        head = head_at(heads, t)
        out.append((round(score, 3), t, on + ([head] if head else [])))
    return sorted(out, key=lambda s: -s[0])


def clip_start(plan, dur, length):
    """The start of the stretch with the most seconds of cards and titles on screen (earliest on a tie)."""
    els = elements(plan)
    cover = lambda s: sum(max(0, min(e, s + length) - max(b, s)) for b, e, *_ in els)
    starts = [round(i * 0.25, 2) for i in range(int(max(0, dur - length) / 0.25) + 1)]
    return max(starts, key=lambda s: (round(cover(s), 2), -s))


def encode(video, start, length, out):
    for crf in (26, 29, 32, 35):
        r = ff("-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", video, "-vf", "scale=720:-2", "-c:v", "libx264",
               "-preset", "slow", "-crf", crf, "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", out)
        if r.returncode:
            raise RuntimeError(r.stderr.decode()[-400:])
        if Path(out).stat().st_size <= MAX_MB * 1e6:
            return crf
    raise RuntimeError(f"{out} is over {MAX_MB} MB even at crf 35")


def sheet(video, cands, best_t, out):
    with tempfile.TemporaryDirectory() as d:
        for i, (_, t, _) in enumerate(sorted(cands, key=lambda c: c[1])):
            pad = "pad=iw+12:ih+12:6:6:red" if t == best_t else "pad=iw+12:ih+12:6:6:white"
            ff("-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf", f"scale=150:-2,{pad}", f"{d}/{i:03d}.png")
        ff("-i", f"{d}/%03d.png", "-vf", "tile=10x4", "-frames:v", "1", out)


def make(edit, style_dir, at=None, clip=None, sheet_out=None):
    plan, heads, video = load(edit)
    style_dir = Path(style_dir)
    style_dir.mkdir(parents=True, exist_ok=True)
    cands = pick(plan, heads, video)
    if at is not None:
        els = [r for a, b, _, _, r in elements(plan) if a <= at <= b]
        head = head_at(heads, at)
        best = (None, at, els + ([head] if head else []))
    elif cands:
        best = cands[0]
    else:
        raise SystemExit("no frame with a card or title fully on screen: pass --at")
    t, rects = best[1], best[2]
    r = ff("-ss", f"{t:.2f}", "-i", video, "-frames:v", "1", "-vf",
           "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280", "-q:v", "3", style_dir / "thumb.jpg")
    if r.returncode:
        raise RuntimeError(r.stderr.decode()[-400:])
    dur = duration(video)
    length = min(CLIP_S, dur)
    if length < MIN_S:
        raise SystemExit(f"the edit is {dur:.1f} s; an example needs {MIN_S:.0f} s")
    s = clip if clip is not None else clip_start(plan, dur, length)
    crf = encode(video, s, length, style_dir / "example.mp4")
    meta = {"focus": focus(rects) if rects else [50, 40], "t": t, "clip": [s, round(s + length, 2)]}
    (style_dir / "thumb.json").write_text(json.dumps(meta) + "\n", encoding="utf-8")
    if sheet_out:
        sheet(video, cands, t, sheet_out)
    return {**meta, "crf": crf, "mb": round((style_dir / "example.mp4").stat().st_size / 1e6, 2), "candidates": len(cands)}


def demo():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        ed = d / "edit"
        ed.mkdir()
        # 20 s grey take: a small card 1-3 s, a big card 6-9.5 s (drawn), a flash at 7.5 s (mid-transition)
        r = ff("-f", "lavfi", "-i", "color=c=0x808080:s=540x960:d=20:r=30", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
               "-vf", "drawbox=x=70:y=125:w=400:h=240:color=white:t=fill:enable='between(t,6,9.5)',"
               "drawbox=x=0:y=0:w=540:h=960:color=black:t=fill:enable='between(t,7.4,7.6)'",
               "-t", "20", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", ed / "render.mp4")
        assert r.returncode == 0, r.stderr
        plan = {"cards": [{"start": 1, "end": 3, "box": [10, 10, 80, 10]},
                          {"start": 6, "end": 9.5, "box": [13, 13, 74, 25], "props": {"enter": {"dur": 0.23}}}],
                "texts": [], "captions": {"chunks": [{"start": 6.5, "end": 9.4, "text": "hi"}]}}
        (ed / "render.plan.json").write_text(json.dumps(plan), encoding="utf-8")
        (ed / "face.json").write_text(json.dumps({"step_s": 0.5, "heads": [{"t": i / 2, "box": [35, 32, 30, 22]} for i in range(40)]}), encoding="utf-8")
        out = make(ed, d / "style", sheet_out=d / "sheet.jpg")
        assert 6.35 <= out["t"] <= 9.25 and not 7.3 <= out["t"] <= 7.7, out   # the big card, settled, not the flash
        fx, fy = out["focus"]
        assert 40 <= fx <= 60 and 0 <= fy <= 100, out
        top, bot = 0.13, 0.54   # card top to head bottom: inside every crop at that focus
        for shape in SHAPES:
            v = (9 / 16) / shape
            a = (1 - v) * fy / 100
            assert a <= top + 1e-6 and a + v >= bot - 1e-6, (shape, a, v)
        assert out["clip"][0] <= 6 and out["clip"][1] >= 9.5 and out["clip"][1] - out["clip"][0] == CLIP_S, out
        assert out["mb"] < MAX_MB and abs(duration(d / "style" / "example.mp4") - CLIP_S) < 0.2, out
        w, h = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0",
                               d / "style" / "thumb.jpg"], capture_output=True, text=True).stdout.split(",")
        assert (int(w), int(h)) == (720, 1280) and (d / "sheet.jpg").stat().st_size > 0
        assert json.loads((d / "style" / "thumb.json").read_text(encoding="utf-8"))["focus"] == out["focus"]
        assert focus([[20, 80, 60, 10]])[1] > 80   # a low element moves the window down
        # an open eye (a dark iris disc on white) against a shut one (skin, one thin lash line)
        iris = [[30 if (x - 30) ** 2 + (y - 20) ** 2 < 100 else 230 for x in range(60)] for y in range(40)]
        lid = [[30 if y in (21, 22) else 150 + (x * 7 + y * 13) % 40 for x in range(60)] for y in range(40)]
        assert openness(iris) > 0.4 > 0.1 > openness(lid), (openness(iris), openness(lid))
        assert set(eyes(ed / "render.mp4", [1.0, 7.0]).values()) == {None}, "no face, no eye score"
        print("thumbs demo ok", out)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["demo"]:
        demo()
    elif a[:1] == ["make"] and len(a) >= 3:
        opt = lambda k: a[a.index(k) + 1] if k in a else None
        num = lambda k: float(opt(k)) if opt(k) is not None else None
        print(json.dumps(make(a[1], a[2], num("--at"), num("--clip"), opt("--sheet"))))
    else:
        sys.exit(__doc__)
