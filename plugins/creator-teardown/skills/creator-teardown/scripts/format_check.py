#!/usr/bin/env python3
"""The format check: a style copies ONE editing format, not a blend of everything a creator posts.

  check <handle>   reads video/<id>.visual.json and video/<id>.look.json (visual.py and look.py
                   measure), gives each video a few measured features, groups the videos whose
                   features match, keeps the largest group (a tie: the higher median view
                   multiple) and writes format.json. When the kept group is a majority of 3 or
                   more ("applied"), the videos left out move to video/left-out/, so graphics.py,
                   sound.py and report.py see the kept format only, and visual.py, look.py (and
                   graphics.py, sound.py when they ran) merge style.json again from the kept
                   videos (each caches per video: seconds). Otherwise nothing moves and the style
                   uses every video. Safe to rerun: it reads video/left-out/ too.
  demo             self-check on made-up feature rows. No network.

Features (all already measured; nothing new is decoded):
  frame     16:9 (a landscape file) | letterbox (a 16:9 band between black bars) | split (the
            face in the lower part with graphics on screen 30%+ of the time) | full
  kind      talking head (a face 50%+ of the time) | two people (the face sits left AND right of
            centre, 20%+ of face time each: a podcast or skit cut; there is no speaker count, the
            transcripts are not diarized) | screen recording (a face 30% or less, graphics 50%+)
            | b-roll
  captions  none, or words per caption, height on screen, size, upper case or not
  shot      median shot length       length   the video's duration
Two videos are one format when frame and kind are the same, both or neither have captions, and
with captions: words per caption within 1 or 2x, heights within 15 points, sizes within 1.5x,
upper case on both or neither; shots within 2.5x (or both 4 s or more); lengths within 3x. A
feature not measured on either video matches. Videos are taken most measured first, then
most-viewed; each joins the first group whose first video it matches, else starts a group (matching
the first video, not any member, so a group cannot drift from one format to another one step at a
time). Then every video that matches the kept group's first video joins it.

format.json:
  {"consistent": true when the kept group is more than half the videos,
   "applied": consistent and 3 or more kept: only then do videos move and style.json name the format,
   "groups": [{"name": "talking-head videos with word captions", "videos": [ids], "views_x": 1.4}],
   "kept": [ids], "left_out": [{"id", "url", "why": "two-person videos in 16:9 (16:9, not ...)"}],
   "line": the one line to tell the user}

Usage (the tool venv python):
  ~/.ai-video-editor/venv/bin/python scripts/format_check.py check <handle>
  ~/.ai-video-editor/venv/bin/python scripts/format_check.py demo

Exit codes: 0 ok - 1 error - 2 usage
"""
import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from fetch import OUT_ROOT, slug  # noqa: E402

FACE_PCT = 50          # a face this % of the time: a talking head
SCREEN_FACE, SCREEN_GFX = 30, 50
SPLIT_FACE_Y, SPLIT_GFX = 62, 30
SIDE_SHARE = 0.2       # face time left of 40% and right of 60% of the width: two people
WORDS_DIFF, WORDS_X = 1, 2.0
Y_POINTS = 15
SIZE_X, SHOT_X, LENGTH_X = 1.5, 2.5, 3.0
SHOT_SLOW = 4.0       # shots this long on both: few cuts, and a median of 2-3 shots is noise
LEFT_OUT = "left-out"


def features(vid, vis, look):
    """One video's feature row from its visual.json and look.json (either may be missing)."""
    vis, look = vis or {}, look or {}
    face, cap = look.get("face") or {}, look.get("captions") or {}
    gfx = (look.get("graphics") or {}).get("share_pct") or 0
    fp = face.get("present_pct")
    w, h = vis.get("width") or 0, vis.get("height") or 0
    if w > h:
        frame = "16:9"
    elif look.get("letterbox"):
        frame = "letterbox"
    elif fp and (face.get("y_pct") or 0) >= SPLIT_FACE_Y and gfx >= SPLIT_GFX:
        frame = "split"
    else:
        frame = "full" if (w or look) else None
    xs = [s["face"][0] + s["face"][2] / 2 for s in look.get("samples") or [] if s.get("face")]
    # ponytail: face sides stand in for a speaker count; diarized transcripts would be the real one
    two = len(xs) >= 10 and min(sum(x < 0.4 for x in xs), sum(x > 0.6 for x in xs)) >= SIDE_SHARE * len(xs)
    kind = (None if fp is None else "two people" if two else "talking head" if fp >= FACE_PCT
            else "screen recording" if fp <= SCREEN_FACE and gfx >= SCREEN_GFX else "b-roll")
    if cap.get("present") is None:
        caps = None                       # no OCR engine: unknown, matches anything
    elif not cap["present"]:
        caps = False
    else:
        caps = {"words": cap.get("words_per_caption"), "y": cap.get("y_pct"), "size": cap.get("size_pct"),
                "upper": cap.get("case") == "upper"}
    return {"id": vid, "frame": frame, "kind": kind, "captions": caps,
            "shot_s": vis.get("median_shot_s"), "length_s": vis.get("duration_s")}


def _within(a, b, x):
    return not a or not b or max(a, b) <= x * min(a, b)   # None or 0: not measured, matches


KIND_WORDS = {"talking head": "a talking head", "two people": "two people", "screen recording": "a screen recording",
              "b-roll": "b-roll"}


def diffs(seed, f):
    """How `f` differs from the group's first video `seed`, in plain words. Empty: same format."""
    out = []
    if seed["frame"] and f["frame"] and seed["frame"] != f["frame"]:
        out.append(f"{f['frame']} frame, not {seed['frame']}")
    if seed["kind"] and f["kind"] and seed["kind"] != f["kind"]:
        out.append(f"{KIND_WORDS[f['kind']]}, not {KIND_WORDS[seed['kind']]}")
    a, b = seed["captions"], f["captions"]
    if a is not None and b is not None:
        if bool(a) != bool(b):
            out.append("no captions" if not b else "captions where the others have none")
        elif a and b:
            wa, wb = a["words"], b["words"]
            if wa is not None and wb is not None and abs(wa - wb) > WORDS_DIFF and not _within(wa, wb, WORDS_X):
                out.append(f"{wb} word{'s' * (wb != 1)} a caption, not {wa}")
            if a["y"] is not None and b["y"] is not None and abs(a["y"] - b["y"]) > Y_POINTS:
                out.append(f"captions at {b['y']:.0f}% height, not {a['y']:.0f}%")
            if not _within(a["size"], b["size"], SIZE_X):
                out.append(f"caption size {b['size']}%, not {a['size']}%")
            if a["upper"] != b["upper"]:
                out.append("upper-case captions" if b["upper"] else "captions not in upper case")
    if not _within(seed["shot_s"], f["shot_s"], SHOT_X) and min(seed["shot_s"], f["shot_s"]) < SHOT_SLOW:
        out.append(f"shots {f['shot_s']} s, not {seed['shot_s']} s")
    if not _within(seed["length_s"], f["length_s"], LENGTH_X):
        out.append(f"{f['length_s']:.0f} s long, not {seed['length_s']:.0f} s")
    return out


def name(f):
    """A format in plain words, e.g. "talking-head videos in letterbox with word captions"."""
    kind = {"talking head": "talking-head videos", "two people": "two-person videos",
            "screen recording": "screen recordings", "b-roll": "b-roll videos"}.get(f["kind"], "videos")
    frame = {"16:9": " in 16:9", "letterbox": " in letterbox", "split": " in split screen"}.get(f["frame"], "")
    c = f["captions"]
    if c is False:
        caps = ", no captions"
    elif not c:
        caps = ""
    else:
        w = c["words"] or 0
        caps = (" with word captions" if w <= 2 else " with short caption lines" if w <= 5 else " with sentence captions")
        caps = caps.replace(" with ", " with upper-case ") if c["upper"] else caps
    return kind + frame + caps


FEATURES = ("frame", "kind", "captions", "shot_s", "length_s")
MIN_KEPT = 3           # fewer kept videos than this: format.json is written, nothing moves


def measured(r):
    return sum(r.get(k) is not None for k in FEATURES)


def group(rows):
    """rows: feature rows with "views_x" (and "url") -> the format.json dict."""
    seen, uniq = set(), []
    for r in rows:   # one row an id
        if r["id"] not in seen:
            seen.add(r["id"])
            uniq.append(r)
    # Seeds: the most measured first (an unmeasured feature matches anything, so it must not seed), then most viewed.
    rows = sorted(uniq, key=lambda r: (-measured(r), -(r.get("views_x") or 0)))
    groups = []
    for r in rows:
        g = next((g for g in groups if not diffs(g[0], r)), None)
        (g.append(r) if g else groups.append([r]))

    def vx(g):
        return round(statistics.median(r.get("views_x") or 0 for r in g), 2)
    best = max(groups, key=lambda g: (len(g), vx(g))) if groups else []   # max keeps the first on a full tie
    for g in groups:   # matching the seed is not transitive: a video that matches the kept seed joins it
        if g is not best:
            best += [r for r in g if not diffs(best[0], r)]
            g[:] = [r for r in g if diffs(best[0], r)]
    groups = [g for g in groups if g]
    groups.sort(key=lambda g: (g is not best, -len(g), -vx(g)))

    def label(g):   # named from a fully measured member: a wildcard says only "videos"
        return name(next((r for r in g if measured(r) == len(FEATURES)), g[0]))
    left = [{"id": r["id"], "url": r.get("url"), "why": f"{name(r)} ({'; '.join(diffs(best[0], r))})"}
            for g in groups if g is not best for r in g]
    n, k, fmt = len(rows), len(best), label(best)
    out = {"consistent": k > n / 2,
           "groups": [{"name": label(g), "videos": [r["id"] for r in g], "views_x": vx(g)} for g in groups],
           "kept": [r["id"] for r in best], "left_out": left}
    # Only a clear majority of 3 or more is a format to copy; otherwise nothing moves and style.json names none.
    out["applied"] = out["consistent"] and k >= MIN_KEPT
    if not left:
        out["line"] = f"All {n} videos share one format: {fmt}." if n > 1 else f"One video: {fmt}."
    elif out["applied"]:
        out["line"] = f"Using {k} of {n}: {n - k} look like a different format. The style copies their {fmt}."
    elif out["consistent"]:
        out["line"] = (f"Too few videos to pick one format ({k} of {n} match): the style uses all {n}. "
                       f"More videos of one kind give a truer style.")
    else:
        out["line"] = (f"These {n} videos are edited in different ways, so the style mixes them. "
                       + (f"{k} of them are {fmt}. " if k > 1 else "")
                       + "More videos edited the same way give a truer style.")
    return out


def rows_of(outdir):
    """Feature rows for every measured video, kept or left out before."""
    vj = outdir / "videos.json"
    meta = {v["id"]: v for v in (json.loads(vj.read_text(encoding="utf-8")).get("videos") or [])} if vj.exists() else {}
    rows, seen = [], set()
    for d in (outdir / "video", outdir / "video" / LEFT_OUT):   # video/ first: a re-downloaded copy wins
        for p in sorted(d.glob("*.visual.json")):
            vid = p.name[:-len(".visual.json")]
            if vid in seen:
                continue
            seen.add(vid)
            lp = d / f"{vid}.look.json"
            look = json.loads(lp.read_text(encoding="utf-8")) if lp.exists() else None
            r = features(vid, json.loads(p.read_text(encoding="utf-8")), look)
            v = meta.get(vid, {})
            r.update(views_x=v.get("vs_median"), url=v.get("webpage_url"))
            rows.append(r)
    return rows


def place(outdir, kept, ids):
    """Every file of a kept video in video/, of a left-out one (of `ids`) in video/left-out/. True when any moved."""
    vdir, lo = outdir / "video", outdir / "video" / LEFT_OUT
    moved = False
    for f in sorted([*vdir.glob("*.*"), *(lo.glob("*.*") if lo.exists() else [])]):
        if not f.is_file():
            continue
        vid = max((i for i in ids if f.name.startswith(i + ".")), key=len, default=None)   # ids may hold dots
        if vid is None:
            continue
        target = vdir if vid in kept else lo
        if f.parent != target:
            target.mkdir(exist_ok=True)
            f.replace(target / f.name)
            moved = True
    return moved


def cmd_check(a):
    outdir = OUT_ROOT / slug(a.handle)
    rows = rows_of(outdir)
    if not rows:
        sys.exit(f"no video/*.visual.json in {outdir}. Run visual.py measure and look.py measure first.")
    out = group(rows)
    (outdir / "format.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(out["line"])
    for x in out["left_out"]:
        print(f"  {'left out' if out['applied'] else 'differs'} {x['id']}: {x['why']}")
    ids = {r["id"] for r in rows}
    if place(outdir, set(out["kept"]) if out["applied"] else ids, ids):
        # style.json was merged from other videos: merge it again from these (each script caches per video)
        vdir = outdir / "video"
        later = [s for s, ext in (("graphics.py", "graphics"), ("sound.py", "sound")) if any(vdir.glob(f"*.{ext}.json"))]
        for script in ["visual.py", "look.py", *later]:
            # a re-merge, not a new measuring step: the home page's steps stay as they are
            r = subprocess.run([sys.executable, str(HERE / script), "measure", a.handle],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env={**os.environ, "AI_EDITOR_NO_PROGRESS": "1"})
            if r.returncode:
                sys.exit(f"{script} measure failed: {r.stderr.strip()[-400:]}")
        print("style.json merged again from the videos in video/; look.md rewritten.")
    print(f"-> {outdir / 'format.json'}")


def demo():
    def row(vid, x, frame="full", kind="talking head", words=1, y=70, size=5.0, upper=False, shot=2.0, length=40):
        caps = words if words in (None, False) else {"words": words, "y": y, "size": size, "upper": upper}
        return {"id": vid, "frame": frame, "kind": kind, "captions": caps, "shot_s": shot, "length_s": length,
                "views_x": x, "url": f"https://example.com/{vid}"}

    # Two formats: four talking heads with word captions, two podcast clips. The talking heads are kept.
    th = [row("a", 3.0), row("b", 1.2, words=2, y=66), row("c", 0.9, size=6.2, shot=2.6), row("d", 0.8, length=55)]
    pod = [row("p1", 5.0, frame="16:9", kind="two people", words=6, y=80, shot=7.5, length=150),
           row("p2", 1.0, frame="16:9", kind="two people", words=5, y=82, shot=8.0, length=170)]
    out = group(th + pod)
    assert out["consistent"] and out["applied"] and out["kept"] == ["a", "b", "c", "d"], out
    assert out["groups"][0]["name"] == "talking-head videos with word captions" and out["groups"][0]["views_x"] == 1.05
    assert out["groups"][1]["name"] == "two-person videos in 16:9 with sentence captions", out["groups"]
    why = {x["id"]: x["why"] for x in out["left_out"]}
    assert set(why) == {"p1", "p2"} and "16:9 frame, not full" in why["p1"] and "two people, not a talking head" in why["p1"]
    assert out["line"].startswith("Using 4 of 6: 2 look like a different format."), out["line"]
    # A tie: two groups of two, the higher median multiple wins.
    lb = [row("l1", 0.5, frame="letterbox"), row("l2", 0.7, frame="letterbox")]
    out = group([row("f1", 2.0), row("f2", 4.0)] + lb)
    assert out["kept"] == ["f2", "f1"] and not out["consistent"] and not out["applied"], out
    out = group([row("f1", 0.2), row("f2", 0.4)] + lb)
    assert out["kept"] == ["l2", "l1"] and out["groups"][0]["name"] == "talking-head videos in letterbox with word captions"
    # All mixed: not consistent, the best video is still kept and the line says so.
    mixed = [row("m1", 1.0), row("m2", 2.0, frame="16:9"), row("m3", 0.5, words=False),
             row("m4", 0.8, kind="screen recording", shot=9.0)]
    out = group(mixed)
    assert not out["consistent"] and not out["applied"] and out["kept"] == ["m2"] and len(out["left_out"]) == 3, out
    assert "edited in different ways" in out["line"] and "no captions" in {x["id"]: x["why"] for x in out["left_out"]}["m3"]
    # One video: consistent, kept.
    out = group([row("solo", None, words=None)])
    assert out["consistent"] and not out["applied"] and out["kept"] == ["solo"] and out["line"] == "One video: talking-head videos.", out
    # Two of three agree: too few to call a format, nothing moves.
    out = group([row("a", 1), row("b", 1), row("c", 1, frame="16:9")])
    assert out["consistent"] and not out["applied"] and "Too few videos" in out["line"], out
    # Not transitive: r joins A's group, B's group of 3 wins, and r (which matches B too) moves into it.
    out = group([row("A", 5, shot=1.0), row("r", 4, shot=2.0), row("B", 3, shot=3.0), row("b2", 2, shot=3.0),
                 row("b3", 1, shot=3.0)])
    assert out["kept"] == ["B", "b2", "b3", "r"] and [x["id"] for x in out["left_out"]] == ["A"], out
    assert out["left_out"][0]["why"].endswith("(shots 1.0 s, not 3.0 s)"), out
    # A wildcard (nothing measured but the frame) never seeds or names a group, even when most viewed.
    w = {"id": "W", "frame": "full", "kind": None, "captions": None, "shot_s": None, "length_s": None, "views_x": 9}
    out = group([w, row("t1", 2), row("t2", 1), row("p1", 1.5, frame="16:9", kind="two people", words=False, shot=8, length=150)])
    assert out["kept"][:2] == ["t1", "t2"] and "W" in out["kept"] and out["applied"], out
    assert out["groups"][0]["name"] == "talking-head videos with word captions", out["groups"]
    # One row an id.
    assert group([row("a", 2), row("a", 2), row("b", 1)])["kept"] == ["a", "b"]
    # A chain must not drift: each step is close, the ends are not.
    out = group([row("s1", 3, shot=1.0), row("s2", 2, shot=2.4), row("s3", 1, shot=5.5)])
    assert out["kept"] == ["s1", "s2"], out
    assert not diffs(row("x", 1, shot=4.1), row("y", 1, shot=16.9))   # both slow: a few cuts each, one format
    # Features from real visual.json / look.json shapes.
    vis = {"width": 1080, "height": 1920, "median_shot_s": 2.1, "duration_s": 41.0}
    look = {"letterbox": None, "face": {"present_pct": 88, "y_pct": 40},
            "captions": {"present": True, "words_per_caption": 1, "y_pct": 70.0, "size_pct": 4.5, "case": "upper"},
            "graphics": {"share_pct": 10}, "samples": [{"face": [0.4, 0.3, 0.2, 0.15]}] * 12}
    f = features("v", vis, look)
    assert (f["frame"], f["kind"], f["captions"]["upper"]) == ("full", "talking head", True), f
    assert name(f) == "talking-head videos with upper-case word captions", name(f)
    sides = {**look, "samples": [{"face": [0.1, 0.3, 0.2, 0.15]}] * 6 + [{"face": [0.7, 0.3, 0.2, 0.15]}] * 6}
    assert features("v", {**vis, "width": 1920, "height": 1080}, sides)["kind"] == "two people"
    assert features("v", vis, {**look, "letterbox": [34.2, 31.7]})["frame"] == "letterbox"
    assert features("v", vis, {**look, "face": {"present_pct": 70, "y_pct": 75}, "graphics": {"share_pct": 60}})["frame"] == "split"
    assert features("v", vis, {**look, "captions": {"present": None}})["captions"] is None
    assert features("v", vis, None)["kind"] is None
    assert features("v", vis, {**look, "face": {"present_pct": None}})["kind"] is None   # no face detector
    # End to end on files: the left-out video moves to video/left-out/, a rerun moves nothing.
    with tempfile.TemporaryDirectory() as d:
        od = Path(d)
        (od / "video").mkdir()
        meta = []
        for vid, x, w, h, fp in (("k1", 2.0, 1080, 1920, 90), ("k2", 1.0, 1080, 1920, 85), ("o1", 3.0, 1920, 1080, 90)):
            (od / "video" / f"{vid}.mp4").write_bytes(b"")
            (od / "video" / f"{vid}.visual.json").write_text(json.dumps({**vis, "width": w, "height": h}), encoding="utf-8")
            (od / "video" / f"{vid}.look.json").write_text(json.dumps({**look, "face": {"present_pct": fp, "y_pct": 40}}),
                                                           encoding="utf-8")
            meta.append({"id": vid, "vs_median": x, "webpage_url": f"https://example.com/{vid}"})
        (od / "videos.json").write_text(json.dumps({"videos": meta}), encoding="utf-8")
        out = group(rows_of(od))
        assert out["kept"] == ["k1", "k2"] and out["left_out"][0]["url"] == "https://example.com/o1", out
        (od / "video" / "x9.mp4").write_bytes(b"")   # not measured: not the check's to move
        assert place(od, set(out["kept"]), {"k1", "k2", "o1"}) and (od / "video" / LEFT_OUT / "o1.look.json").exists()
        assert sorted(p.name for p in (od / "video").glob("*.mp4")) == ["k1.mp4", "k2.mp4", "x9.mp4"]
        assert group(rows_of(od))["kept"] == ["k1", "k2"] and not place(od, {"k1", "k2"}, {"k1", "k2", "o1"})
        # A re-download of o1 sits in video/ again: one row for o1, the copy in video/ wins.
        (od / "video" / "o1.visual.json").write_text(json.dumps({**vis, "width": 1920, "height": 1080}), encoding="utf-8")
        assert [r["id"] for r in rows_of(od)].count("o1") == 1
        # Dotted ids: "a.b" files are a.b's, not a's.
        (od / "video" / "a.b.mp4").write_bytes(b"")
        (od / "video" / "a.mp4").write_bytes(b"")
        place(od, {"a"}, {"a", "a.b"})
        assert (od / "video" / LEFT_OUT / "a.b.mp4").exists() and (od / "video" / "a.mp4").exists()
    print("format_check demo ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p = sub.add_parser("check")
    p.add_argument("handle")
    p.set_defaults(fn=cmd_check)
    sub.add_parser("demo").set_defaults(fn=lambda a: demo())
    a = ap.parse_args()
    if not getattr(a, "fn", None):
        ap.print_help()
        sys.exit(2)
    a.fn(a)


if __name__ == "__main__":
    main()
