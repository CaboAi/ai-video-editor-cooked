#!/usr/bin/env python3
"""Does a rendered edit look like the creator it copies? The same measures on both sides.

    python3 run.py match.py <style dir> <render.mp4> [--json] [--keep DIR]
    python3 run.py match.py demo        self-check (synthetic rows, no video)

<style dir> is a teardown folder (creator-teardowns/<handle>/: style.json and video/<id>.mp4 with the
per-video measures beside them). The render is measured by the teardown's own scripts (visual.py, look.py,
graphics.py, sound.py), run on a one-video teardown in a temp folder, so both columns come from the same
code. Its words come from cut.transcript.json beside the render (the cut's verify pass), else transcribe.py
(Whisper, local). Card kinds are named by gemini.py kinds when the creator's were. Colour grade is sampled here with ffmpeg on both sides.

Prints one row a feature: the creator's value (median or majority of the kept videos), ours, and pass or
FAIL against the tolerance. Exit 0 when every measured feature passes, 1 otherwise, 2 on usage.
The font family cannot be read off pixels: ours comes from plan.json beside the render and is compared with
the name the look pass gave the creator's font. --keep DIR keeps the render's measures (for sheets).
"""
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

SAFE = (5.6, 13.1, 13.0, 25.0)   # 9:16 left, top, right, bottom margins in % (TikTok and Reels UI); ai-editor plan.py's SAFE
SAFE_WIDE = (3, 5, 3, 10)


def load(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def med(xs, nd=2):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), nd) if xs else None


def mode(xs):
    xs = [x for x in xs if x is not None]
    return Counter(xs).most_common(1)[0][0] if xs else None


def rgb(h):
    h = (h or "").lstrip("#")
    return tuple(int(h[k:k + 2], 16) for k in (0, 2, 4)) if len(h) == 6 else None


def cdist(a, b):
    a, b = rgb(a), rgb(b)
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5 if a and b else None


def grade(mp4, fps=1, w=96):
    """(mean luma 0-255, mean saturation 0-1) over one frame a second, small."""
    import numpy as np
    pr = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                         "-of", "csv=p=0", str(mp4)], capture_output=True, text=True).stdout.strip().split("\n")[0]
    W, H = (int(v) for v in pr.split(",")[:2])
    h = max(2, round(w * H / W / 2) * 2)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(mp4), "-vf", f"fps={fps},scale={w}:{h}", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    a = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(np.float32) / 255
    if not len(a):
        return None, None
    y = 0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]
    mx, mn = a.max(-1), a.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    return round(float(y.mean() * 255), 1), round(float(sat.mean()), 3)


def safe_share(look, gfx, portrait):
    """% of text lines and graphics fully inside the platform's safe zone (over all samples)."""
    l, t, r, b = SAFE if portrait else SAFE_WIDE
    boxes = [box for s in (look or {}).get("samples", []) for _, box, _ in s["lines"]]
    boxes += [g["box"] for g in (gfx or {}).get("graphics", []) if g.get("kind") != "set"
              and g["box"][2] * g["box"][3] < 0.6]          # full-frame b-roll is not an element
    if not boxes:
        return None
    ok = sum(x * 100 >= l - 1 and y * 100 >= t - 1 and (x + w) * 100 <= 100 - r + 1 and (y + h) * 100 <= 100 - b + 1
             for x, y, w, h in boxes)
    return round(100 * ok / len(boxes))


def side_of(cap, face):
    """Where the captions sit against the face: above, on or below it (look.py's medians)."""
    if not (cap.get("y_pct") and face.get("y_pct") and face.get("height_pct")):
        return None
    top, bottom = face["y_pct"] - face["height_pct"] / 2, face["y_pct"] + face["height_pct"] / 2
    return "above" if cap["y_pct"] < top else "below" if cap["y_pct"] > bottom else "on"


def video_row(vdir, vid, tdir, cache=True):
    """Every comparable number for one video, from the teardown's per-video files."""
    vis = load(vdir / f"{vid}.visual.json") or {}
    look = load(vdir / f"{vid}.look.json") or {}
    gfx = load(vdir / f"{vid}.graphics.json") or {}
    snd = load(vdir / f"{vid}.sound.json") or {}
    tr = load(tdir / f"{vid}.json") or {}
    dur = vis.get("duration_s") or look.get("duration_s") or 0
    words = [w for w in tr.get("words", []) if w.get("type") == "word"]
    gs = [g for g in gfx.get("graphics", []) if g.get("kind") != "set"]
    kinds = Counter()
    for g in gs:
        kinds[g.get("kind")] += g["hold_s"]
    cap = look.get("captions") or {}
    samples = look.get("samples") or []
    faced = sum(1 for s in samples if s.get("face"))
    from look import weight_of
    gp = vdir / f"{vid}.grade.json"
    g = load(gp) if cache else None
    if not g:
        g = dict(zip(("luma", "sat"), grade(vdir / f"{vid}.mp4")))
        gp.write_text(json.dumps(g), encoding="utf-8")
    try:
        from report import hook
        title = bool(hook(vid, words, look, vis, gfx).get("title_on_screen"))
    except Exception:   # noqa: BLE001  report.py missing a field on an old teardown: unmeasured, not a crash
        title = None
    zs = vis.get("zooms") or []
    portrait = (vis.get("height") or 16) >= (vis.get("width") or 9)
    return {
        "id": vid, "duration_s": dur,
        "shots_per_min": round((len(vis.get("cuts", [])) + 1) / dur * 60, 1) if dur and vis else None,
        "longest_hold_s": max(vis["shots_s"]) if vis.get("shots_s") else None,
        "zooms_per_min": vis.get("zooms_per_min"),
        "zoom_scale": med([max(z["scale"], 1 / z["scale"]) for z in zs]) if zs else None,
        "captions": cap.get("present"),
        "words_per_line": round(cap["words_per_caption"] / max(1, cap.get("lines") or 1), 1) if cap.get("present") else None,
        "weight": weight_of(cap.get("stem_ratio")) if cap.get("present") else None,
        "case": cap.get("case") if cap.get("present") else None,
        "color": cap.get("color") if cap.get("present") else None,
        "box": cap.get("box") if cap.get("present") else None,
        "y_pct": cap.get("y_pct") if cap.get("present") else None,
        "cap_side": side_of(cap, look.get("face") or {}) if cap.get("present") else None,
        **{k: cap.get(k) if cap.get("present") else None for k in ("accent_pct", "accent_keyword_pct", "light_pct", "stack_pct")},
        # look.py's rule: a second face under 10% of the lines is noise, not a face
        "light_face": (cap.get("light_face") if (cap.get("light_pct") or 0) >= 10 else None) or "none"
        if cap.get("present") and "light_pct" in cap else None,
        "big_ratio": round(cap["size_max_pct"] / cap["size_pct"], 2) if cap.get("present") and cap.get("size_max_pct") and cap.get("size_pct") else None,
        "highlight": (cap.get("highlight_color") or "none") if cap.get("present") and "color" in cap else None,
        "card_pct": round(100 * min(dur, sum(g["hold_s"] for g in gs)) / dur) if dur and gfx else None,
        "card_kind": kinds.most_common(1)[0][0] if kinds else ("none" if gfx else None),
        "kinds": dict(kinds),
        "broll_pct": round(100 * (1 - faced / len(samples))) if samples and look.get("face", {}).get("present_pct") is not None else None,
        "title_open": title,
        "safe_pct": safe_share(look, gfx, portrait),
        "wpm": round(len(words) / dur * 60) if words and dur else None,
        "luma": g.get("luma"), "sat": g.get("sat"),
        "sfx_per_min": snd.get("sfx_per_min"),
        "music": (snd.get("music") or {}).get("present") if snd else None,
    }


def side(rows):
    """Per-video rows -> one value a feature (median, or the majority)."""
    num = ("shots_per_min", "longest_hold_s", "zooms_per_min", "zoom_scale", "words_per_line", "weight", "y_pct",
           "card_pct", "broll_pct", "safe_pct", "wpm", "luma", "sat", "sfx_per_min", "accent_pct", "accent_keyword_pct",
           "light_pct", "stack_pct", "big_ratio")
    out = {k: med([r[k] for r in rows], 3 if k == "sat" else 2 if k == "zoom_scale" else 1) for k in num}
    for k in ("captions", "case", "color", "box", "highlight", "card_kind", "music", "cap_side", "light_face"):
        out[k] = mode(r[k] for r in rows)
    faces = [r["light_face"] for r in rows if r["light_face"] not in (None, "none")]
    if faces and (out["light_pct"] or 0) >= 10:   # her second face is the one the videos that use one agree on
        out["light_face"] = mode(faces)
    # an accent in a third of the videos or more is hers (each video's hex differs a little): the one most others sit near
    hs = [r["highlight"] for r in rows if r["highlight"] not in (None, "none")]
    if hs and len(hs) >= len([r for r in rows if r["highlight"] is not None]) / 3:
        out["highlight"] = max(hs, key=lambda h: sum(_le(cdist(h, x), 60) for x in hs))
    t = [r["title_open"] for r in rows if r["title_open"] is not None]
    out["title_open"] = (sum(t) >= len(t) / 2) if t else None
    kinds = Counter()
    for r in rows:
        kinds.update(r["kinds"])
    tot = sum(kinds.values()) or 1
    out["kinds"] = {k: round(100 * v / tot) for k, v in kinds.most_common()}
    return out


def _le(d, lim):
    return d is not None and d <= lim


def near(a, b, rel, absol):
    return abs(a - b) <= max(absol, rel * abs(a))


# feature, label, test(creator, ours) -> bool, format
def rules(c, o):
    def num(k, rel, absol):
        return lambda: near(c[k], o[k], rel, absol)
    return [
        ("shots_per_min", "cut rate (shots a minute)", num("shots_per_min", 0.35, 2)),
        ("longest_hold_s", "longest hold (s)", num("longest_hold_s", 0.4, 2)),
        ("zooms_per_min", "zooms a minute", num("zooms_per_min", 0.4, 1.5)),
        ("zoom_scale", "zoom size (x)", num("zoom_scale", 0.1, 0.06)),   # visual.py's punch scales step 0.2 past 1.6
        ("captions", "captions burned in", lambda: c["captions"] == o["captions"]),
        ("words_per_line", "caption words a line", num("words_per_line", 0, 1)),
        ("font", "caption font family", lambda: (c["font"] or "").lower() == (o["font"] or "").lower()),
        ("weight", "caption weight", num("weight", 0, 100)),
        ("case", "caption case", lambda: c["case"] == o["case"]),
        ("color", "caption colour", lambda: _le(cdist(c["color"], o["color"]), 60)),
        ("box", "caption box", lambda: c["box"] == o["box"]),
        ("y_pct", "caption height (% down)", num("y_pct", 0, 6)),
        ("cap_side", "captions against the face", lambda: c["cap_side"] == o["cap_side"]),
        ("accent_pct", "caption lines in the accent colour (%)", num("accent_pct", 0, 10)),
        ("accent_keyword_pct", "accent lines that are keywords (%)", lambda: (c["accent_keyword_pct"] >= 50) == (o["accent_keyword_pct"] >= 50)),
        ("light_pct", "caption lines in a second, lighter face (%)", num("light_pct", 0, 8)),
        ("light_face", "that second face", lambda: c["light_face"] == o["light_face"]),
        ("big_ratio", "big keyword size (x the usual)", num("big_ratio", 0.25, 0.2)),
        ("stack_pct", "caption pages stacked on 2+ lines (%)", num("stack_pct", 0, 15)),
        ("highlight", "caption highlight colour", lambda: c["highlight"] == o["highlight"] == "none"
         or _le(cdist(c["highlight"], o["highlight"]), 60)),
        ("card_pct", "card / overlay time (%)", num("card_pct", 0.3, 10)),
        ("card_kind", "main card kind", lambda: o["card_kind"] == c["card_kind"]
         or (c["kinds"].get(o["card_kind"], 0) >= 20)),
        ("broll_pct", "b-roll (speaker off screen, %)", num("broll_pct", 0.3, 10)),
        ("title_open", "title or hook text at the open", lambda: c["title_open"] == o["title_open"]),
        ("safe_pct", "text and cards inside the safe zone (%)", lambda: o["safe_pct"] >= min(95, c["safe_pct"])),
        ("wpm", "pace after the cut (words a minute)", num("wpm", 0.12, 10)),
        ("luma", "grade: mean brightness (0-255)", num("luma", 0, 15)),
        ("sat", "grade: mean saturation (0-1)", num("sat", 0, 0.06)),
        ("music", "music bed", lambda: bool(c["music"]) == (str(o["music"]).startswith("yes") or o["music"] is True)),
        ("sfx_per_min", "sound effects a minute", num("sfx_per_min", 0.5, 2)),
    ]


def compare(c, o):
    """[(feature, label, creator, ours, 'pass' | 'FAIL' | 'n/a')]."""
    out = []
    for k, label, test in rules(c, o):
        a, b = c.get(k), o.get(k)
        if a is None or b is None:
            # one side has no captions: the caption fields are a fail only when the creator has them
            res = "FAIL" if k in ("words_per_line", "case", "color", "y_pct", "weight") and a is not None and \
                c.get("captions") and not o.get("captions") else "n/a"
        else:
            res = "pass" if test() else "FAIL"
        out.append((k, label, a, b, res))
    return out


def table(rows, handle):
    def f(v):
        return "-" if v is None else ("yes" if v is True else "no" if v is False else str(v))
    w = max(len(r[1]) for r in rows)
    lines = [f"{'feature':<{w}}  {'@' + handle:>16}  {'ours':>16}  result", "-" * (w + 44)]
    lines += [f"{lb:<{w}}  {f(a):>16}  {f(b):>16}  {res}" for _, lb, a, b, res in rows]
    n = [r for r in rows if r[4] != "n/a"]
    ok = sum(r[4] == "pass" for r in n)
    lines.append(f"match: {ok} of {len(n)} features pass ({round(100 * ok / len(n)) if n else 0}%)")
    return "\n".join(lines)


def creator_side(sdir):
    vdir, tdir = sdir / "video", sdir / "transcripts"
    vids = sorted(p.stem for p in vdir.glob("*.mp4"))
    if not vids:
        sys.exit(f"no videos in {vdir}: run the teardown first")
    c = side([video_row(vdir, v, tdir) for v in vids])
    style = load(sdir / "style.json") or {}
    c["font"] = (style.get("captions") or {}).get("font_match")
    return c, style


def measure_render(render, work):
    """Run the teardown's measures on the render as a one-video teardown in `work`."""
    t = work / "creator-teardowns" / "match"
    (t / "video").mkdir(parents=True, exist_ok=True)
    (t / "transcripts").mkdir(exist_ok=True)
    mp4 = t / "video" / "render.mp4"
    if not mp4.exists():
        try:
            os.link(render, mp4)
        except OSError:
            shutil.copy2(render, mp4)
    words = render.parent / "cut.transcript.json"
    tj = t / "transcripts" / "render.json"
    if not tj.exists():
        if words.exists():
            ws = load(words)
            ws = ws.get("words", ws) if isinstance(ws, dict) else ws
            tj.write_text(json.dumps({"words": ws, "audio_duration_secs": ws[-1]["end"] if ws else 0}), encoding="utf-8")
        else:
            subprocess.run([sys.executable, str(HERE / "transcribe.py"), str(mp4), str(tj), "--engine", "whisper"], check=True)
    env = dict(os.environ, CT_JOBS="1")
    for script in ("visual.py", "look.py", "graphics.py", "sound.py"):
        r = subprocess.run([sys.executable, str(HERE / script), "measure", "match"], cwd=work, env=env,
                           capture_output=True, text=True)
        if r.returncode:
            sys.exit(f"{script} measure failed on the render:\n{r.stderr[-800:]}")
    # the creator's card kinds were named by gemini.py when a key exists: name ours the same way (exit 2: no key)
    subprocess.run([sys.executable, str(HERE / "gemini.py"), "kinds", "match"], cwd=work, env=env, capture_output=True)
    o = side([video_row(t / "video", "render", t / "transcripts", cache=False)])
    plan = load(render.parent / "plan.json") or {}
    o["font"] = ((plan.get("captions") or {}).get("style") or {}).get("font_match")
    if o["music"] is None and plan:   # sound.py hears a bed only in pauses; a tight cut has none: the plan says
        o["music"] = "yes (plan)" if plan.get("music") else "no (plan)"
    return o


def demo():
    row = {"shots_per_min": 9.0, "longest_hold_s": 6.0, "zooms_per_min": 4.0, "zoom_scale": 1.15, "captions": True,
           "words_per_line": 3.0, "weight": 700, "case": "lower", "color": "#FFFFFF", "box": False, "y_pct": 65.0,
           "highlight": "none", "cap_side": "below", "accent_pct": 16, "accent_keyword_pct": 80,
           "light_pct": 11, "light_face": "serif", "stack_pct": 40, "big_ratio": 1.6, "card_pct": 40, "card_kind": "real screenshot", "kinds": {"real screenshot": 30.0},
           "broll_pct": 10, "title_open": True, "safe_pct": 98, "wpm": 180, "luma": 110.0, "sat": 0.25,
           "sfx_per_min": 2.0, "music": False}
    c = side([row, {**row, "shots_per_min": 11.0, "title_open": False}, {**row, "title_open": True}])
    assert c["shots_per_min"] == 9.0 and c["title_open"] is True and c["kinds"] == {"real screenshot": 100}, c
    c["font"] = "Montserrat"
    o = dict(c, font="montserrat")
    assert all(r[4] == "pass" for r in compare(c, o)), compare(c, o)
    assert side_of({"y_pct": 38.6}, {"y_pct": 48.9, "height_pct": 8.2}) == "above"
    assert compare(c, dict(o, music="yes (plan)"))[-2][4] == "FAIL" and compare(dict(c, music=True), dict(o, music="yes (plan)"))[-2][4] == "pass"
    o = dict(o, captions=False, words_per_line=None, case=None, color=None, y_pct=None, weight=None, highlight=None,
             zooms_per_min=0.0, card_kind="logo", safe_pct=80, wpm=150, music=True)
    bad = {r[0] for r in compare(c, o) if r[4] == "FAIL"}
    assert bad == {"captions", "words_per_line", "weight", "case", "color", "y_pct", "zooms_per_min", "card_kind",
                   "safe_pct", "wpm", "music"}, bad
    assert "match: " in table(compare(c, o), "h")
    print("match ok")


def main():
    a = sys.argv[1:]
    if a == ["demo"]:
        return demo()
    if len(a) < 2 or a[0].startswith("-"):
        print(__doc__)
        sys.exit(2)
    sdir, render = Path(a[0]).resolve(), Path(a[1]).resolve()
    keep = Path(a[a.index("--keep") + 1]).resolve() if "--keep" in a else None
    c, style = creator_side(sdir)
    work = keep or Path(tempfile.mkdtemp(prefix="match-"))
    try:
        o = measure_render(render, work)
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
    rows = compare(c, o)
    if "--json" in a:
        print(json.dumps({"creator": c, "ours": o, "rows": rows}, indent=1, default=str))
    print(table(rows, style.get("handle") or sdir.name))
    sys.exit(0 if all(r[4] != "FAIL" for r in rows) else 1)


if __name__ == "__main__":
    main()
