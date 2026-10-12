#!/usr/bin/env python3
"""Find joins in the built cut that land inside loud speech, and with --fix nudge them to a quiet trough.

    python3 splice_check.py <edit_dir> [--fix] [--reach 0.12]
    python3 splice_check.py demo          self-check (made-up audio levels, no ffmpeg)

Reads report.json (build_timeline.py) and the source's audio. A kept edge is in speech when the 20 ms
windows either side of it are 10 dB over the take's silence threshold (build_timeline.in_speech, the
same measure the build uses). --fix moves each one into its removed side, to the nearest quiet window
within --reach seconds, never into a removed word (build_timeline.fix_splices), then rewrites
decisions.json, report.json, words.json, paper-edit.md and cut-check.html. Render again after a fix.

The build already moves edges up to 0.6 s on a take without music; this catches what is left: joins over
a music bed (the build leaves those to the word labels) and spans edited after the build.
Exit codes: 0 no edge in speech (or all fixed), 1 edges left in speech, 2 usage
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_timeline as B  # noqa: E402

REACH_S = 0.12


def edges_in_speech(frames, fps, lvl, quiet_db, total):
    """Source seconds of every kept edge (not the take's first or last frame) with speech either side."""
    return [round(f / fps, 2) for fr in frames for f in fr if 0 < f < total and B.in_speech(lvl, f / fps, quiet_db)]


def check(d, fix=False, reach_s=REACH_S):
    d = Path(d)
    rep = d / "report.json"
    if not rep.exists():
        sys.exit(f"ERROR: no report.json in {d}: run build_timeline.py first")
    r = json.loads(rep.read_text(encoding="utf-8"))
    fps, total = r["fps"], B.probe(r["source"])[2]
    lvl = B.window_rms_db(r["source"])
    noise, diag = B.derive_noise_db(lvl)
    if noise is None:
        print(f"splices    not checked: {diag['reason']}")
        return 0
    bad = edges_in_speech(r["frames"], fps, lvl, noise, total)
    print(f"splices    {len(bad)} join(s) in speech" + (": " + ", ".join(f"{t:.2f}s" for t in bad[:10]) if bad else ""))
    if not bad or not fix:
        return 1 if bad else 0
    if (d / "lead.json").exists():   # frames are in play order, not source order: neighbours are wrong
        print("splices    lead.json reorders the cut: not moved. Rebuild, then listen to these joins.")
        return 1
    toks = [t for t in B.load_fitted(d) if t.get("type") in ("word", "audio_event")]
    words = [t for t in toks if t["type"] == "word"]
    removed = [(w["start"], w["end"]) for w in words if B.covered_by(w, r["model_cuts"])]
    frames, moved, left = B.fix_splices(r["frames"], fps, lvl, noise, total, removed, reach_s)
    for a, b in moved:
        print(f"  moved    {a:.2f}s -> {b:.2f}s ({abs(b - a):.2f}s)")
    for t in left:
        print(f"  left     {t:.2f}s: no quiet window within {reach_s:.2f}s, listen to it")
    if moved:
        spans = [{"start": round(s / fps, 6), "end": round(e / fps, 6)} for s, e in frames]
        final = sum(s["end"] - s["start"] for s in spans)
        r.update(frames=frames, final_s=final, splices_in_speech=left)
        (d / "decisions.json").write_text(json.dumps(spans, indent=1), encoding="utf-8")
        rep.write_text(json.dumps(r, indent=1), encoding="utf-8")
        (d / "words.json").write_text(json.dumps(B.retime(words, spans), indent=1), encoding="utf-8")
        import preview_cut
        preview_cut.write_all(d, toks, r["model_cuts"], spans, r["duration"], final)
        print(f"fixed {len(moved)}: render again")
    return 1 if left else 0


def demo():
    fps, total = 25, 75                     # 3 s at 25 fps; 20 ms windows
    lvl = [-20.0] * 150                     # speech the whole take...
    for w in range(80, 85):                 # ...but a quiet trough at 1.60-1.70 s
        lvl[w] = -60.0
    frames = [[0, 38], [55, 75]]            # joins at 1.52 s (keep end) and 2.20 s (keep start)
    assert edges_in_speech(frames, fps, lvl, -50.0, total) == [1.52, 2.2]
    out, moved, left = B.fix_splices(frames, fps, lvl, -50.0, total, (), REACH_S)
    assert moved == [(1.52, 1.6)] and left == [2.2] and out == [[0, 40], [55, 75]], (out, moved, left)
    _, moved, left = B.fix_splices(frames, fps, lvl, -50.0, total)    # the build's 0.6 s reaches both
    assert moved == [(1.52, 1.6), (2.2, 1.68)] and not left, (moved, left)
    assert edges_in_speech([[0, 40], [42, 75]], fps, lvl, -50.0, total) == []
    print("splice_check demo ok")


def main():
    a = sys.argv[1:]
    if a == ["demo"]:
        return demo()
    reach = REACH_S
    if "--reach" in a:
        i = a.index("--reach")
        reach = float(a[i + 1])
        del a[i:i + 2]
    fix = "--fix" in a
    a = [x for x in a if x != "--fix"]
    if len(a) != 1:
        sys.exit(__doc__)
    sys.exit(check(a[0], fix, reach))


if __name__ == "__main__":
    main()
