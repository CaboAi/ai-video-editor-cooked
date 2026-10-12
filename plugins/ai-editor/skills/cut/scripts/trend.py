#!/usr/bin/env python3
"""One line per finished cut in $AI_EDITOR_HOME/metrics.jsonl, and the trend across them.

    python3 trend.py log <edit_dir>     after the user approves a cut: lengths from report.json, judge precision
    python3 trend.py tally [N]          the last N rows (default 10) and the false-cut trend
    python3 trend.py demo               self-check (a scratch AI_EDITOR_HOME)

Row: {"date", "video", "before_s", "after_s", "false_cuts", "judge_precision", "from"}. "from" is "cut"
(this log) or "learn" (taste/scripts/learn.py, which knows the false cuts: words the user kept in their
posted cut that our default cut removes). Unknown values are null. Never transcript text.
"""
import datetime
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def path():
    return Path(os.environ.get("AI_EDITOR_HOME", "").strip() or Path.home() / ".ai-video-editor") / "metrics.jsonl"


def append(video, before_s, after_s, false_cuts=None, judge_precision=None, source="cut"):
    row = {"date": datetime.date.today().isoformat(), "video": video,
           "before_s": None if before_s is None else round(before_s, 2),
           "after_s": None if after_s is None else round(after_s, 2), "false_cuts": false_cuts,
           "judge_precision": None if judge_precision is None else round(judge_precision, 3), "from": source}
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def log(d):
    d = Path(d)
    rep = d / "report.json"
    if not rep.exists():
        sys.exit(f"ERROR: no report.json in {d}: run build_timeline.py first")
    r = json.loads(rep.read_text(encoding="utf-8"))
    import judge_cut
    p, n = judge_cut.precision()
    row = append(d.resolve().name, r.get("duration"), r.get("final_s"), judge_precision=p if n else None)
    print(f"logged {row['video']}: {row['before_s']}s -> {row['after_s']}s -> {path()}")


def tally(n=10):
    p = path()
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []
    if not rows:
        print("no cuts logged yet")
        return
    fmt = lambda v, f: "-" if v is None else format(v, f)   # noqa: E731
    print(f"{'date':<11}{'video':<26}{'before':>8}{'after':>8}{'false':>7}{'judge':>7}  from")
    for r in rows[-n:]:
        print(f"{r['date']:<11}{r['video'][:25]:<26}{fmt(r['before_s'], '.1f'):>8}{fmt(r['after_s'], '.1f'):>8}"
              f"{fmt(r['false_cuts'], 'd'):>7}{fmt(r['judge_precision'], '.2f'):>7}  {r['from']}")
    fc = [r["false_cuts"] for r in rows if r["false_cuts"] is not None]
    print(f"\n{len(rows)} row(s); false cuts (oldest to newest, must trend to 0): "
          + (" -> ".join(map(str, fc[-n:])) if fc else "none measured yet (taste/scripts/learn.py measures them)"))


def demo():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AI_EDITOR_HOME"] = tmp
        d = Path(tmp) / "edits" / "take-1"
        d.mkdir(parents=True)
        (d / "report.json").write_text(json.dumps({"duration": 61.234, "final_s": 40.5}), encoding="utf-8")
        log(d)
        append("take-0", 30, 20, false_cuts=3, source="learn")
        append("take-2", 30, 20, false_cuts=1, judge_precision=0.75, source="learn")
        rows = [json.loads(x) for x in path().read_text(encoding="utf-8").splitlines()]
        assert rows[0] == {"date": rows[0]["date"], "video": "take-1", "before_s": 61.23, "after_s": 40.5,
                           "false_cuts": None, "judge_precision": None, "from": "cut"}, rows[0]
        assert all(set(r) == set(rows[0]) for r in rows)
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tally(2)
        assert "take-1" not in out.getvalue() and "3 -> 1" in out.getvalue(), out.getvalue()
    print("trend demo ok")


def main():
    a = sys.argv[1:]
    if a == ["demo"]:
        return demo()
    if len(a) == 2 and a[0] == "log":
        return log(a[1])
    if a[:1] == ["tally"] and len(a) <= 2:
        return tally(int(a[1]) if len(a) == 2 else 10)
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
