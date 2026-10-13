#!/usr/bin/env python3
"""The review page: the user watches a render and leaves notes at moments on its timeline. Stdlib only.

One page per edit folder, the hand-over after every render (the cut, the styled edit, a clip, a
product video). Notes save with the edit, never in the plugin:

    <folder>/review.json    every round, note, reply and send/approve event
    <folder>/review/        a frame still per note, and any image the user attached

    python3 review.py <folder> start --video cut.mp4 [--stage cut|edit] [--transcript words.json] [--plan plan.json]
                                     [--name NAME] [--note "..."]
    python3 review.py <folder> start --raw --video raw.mov --stage cut   round 0: the raw clip, open while the cut runs.
                                     No Approve or Stop; notes queue for round 1, which the next `start` or
                                     `round` opens (each raw time moved onto the cut by decisions.json)
    python3 review.py <folder> serve [--no-open]   serve the page and open it (run in the background)
    python3 review.py <folder> wait                block until the user presses send or approve (run in the background)
    python3 review.py <folder> status "rendering" [--log render.log] [--done]   what Claude is doing, live on the page
    python3 review.py <folder> final               the cut is the finished video ("just the cut"): no edit follows
    python3 review.py <folder> status --step captions|visuals|plan|render [--log ..]   a step of the styled edit:
                                     fills the page's "Making the edit" bar (the scripts report their own, step())
    python3 review.py <folder> resolve <id> fixed|wontfix|open [--reply "..."] [--new-t 12.3]
    python3 review.py <folder> round --video v2.mp4 [--stage cut|edit] [--transcript ..] [--plan ..] [--note ".."]
    python3 review.py <folder> alt <name> --video other.mp4 [--transcript ..]   another video in this round
    python3 review.py <folder> learn --scope this-style|style|video|everyone --rule <id> <section> "<rule>" [key=value] ...
                                     the round's preference notes into the user's taste (taste.py add); --none if
                                     none. Each note records it: "taste": rule id | video-only | suggested | one-off.
                                     this-style: the creator style the edit used (<folder>/style.json "source", or
                                     --style <style.json>): its overrides and its edit-like-<slug> skill
    python3 review.py <folder> learn --propose '<json>'   the final approve's LEARN block: candidate rules
                                     [{"rule", "section", "set"?}] onto the page's "Teach <style>" panel
    python3 review.py <folder> learn --done ["summary"]   save the rules the user ticked (styleskill.learn) and
                                     show "Saved to <style>. Next video uses it."
    python3 review.py demo                         self-check on a generated 6 s clip

Each round also gets review/data-<stage><round>.json for the page, built from the edit folder when
the files are there: the cut's transcript with every cut word and pause struck (decisions.json +
words.raw.json), the timeline's segments (cut points, or chapters.txt on the edit), and the edit's
Cards / Zooms / Captions tracks (plan.json). A missing file leaves that part out.
A note saved while a round is with Claude (after send) is queued for the next round: "queued": true, round
n+1. `wait` never sees it. `round` makes it an open note of the new round, moved to where the same words
are said when both rounds have the cut's transcript (else at the same time), with a new frame still.
Stop merges queued notes into the reopened round.
`status` exits 3 with STOPPED when the user pressed Stop on the page: stop the round and run `wait`.
The Transcript tab (cut stage) restores or cuts words: POST /edits {"restore": [[s, e]], "cut": [[s, e]]} in raw-take
seconds (and "pause": removed pauses to put back) rewrites spans.json and keep.json (apply_edits; *.prev.json kept) and `wait` prints "EDITS <n> transcript edits applied
to spans.json: rebuild, render, verify, round". Stop puts spans.prev.json and keep.prev.json back.

`wait` exits when the user presses send or approve, and prints every note of the round: its time,
the text, the words being said, the card on screen (from plan.json) and the frame still's path.
While `wait` runs the page says Claude is listening; without it the notes still save, and the next
`wait` returns at once with them.

The server listens on 127.0.0.1 only, and every request needs the random token in the URL it
prints (then a SameSite cookie), as preview.py does: another page open in the browser cannot read
the notes or press send.
"""
import argparse
import base64
import contextlib
import hashlib
import hmac
import io
import json
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, str(Path(__file__).resolve().parent.parent))   # `from ai_editor import ...` (styleskill, taste)
import askhook  # noqa: E402
import live  # noqa: E402

HTML = Path(__file__).with_name("review.html")
LOCK = threading.RLock()   # re-entrant: /edits holds it across apply_edits and update
BEAT_S = 6          # `wait` touches review/.listening at least this often; older = nobody listening
IMAGE_TYPES = ("png", "jpeg", "gif", "webp")
MAX_IMAGE = 15 << 20


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(path, d):
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")   # one per writer, never shared
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    for i in range(20):   # Windows refuses to replace a file another process is reading this instant
        try:
            return os.replace(tmp, path)
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.05)


class FileLock:
    """Cross-process and cross-thread: the page's server, the CLI and the style-edit scripts (step()) all write
    review.json. A lock file beside it made with O_EXCL (home.py's _Lock)."""
    def __init__(self, path, wait=10):
        self.p, self.tries = Path(path).with_name(Path(path).name + ".lock"), int(wait / 0.02)

    def __enter__(self):
        for _ in range(self.tries):
            try:
                os.close(os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:   # a writer that died holding it: older than 10 s is stale
                    if time.time() - self.p.stat().st_mtime > 10:
                        self.p.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.02)
        raise TimeoutError(f"{self.p} is held; delete it if no review.py is running")

    def __exit__(self, *a):
        self.p.unlink(missing_ok=True)


def update(path, fn):
    with FileLock(path):
        d = load(path)
        out = fn(d)
        save(path, d)
        return out


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def probe(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                        "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip().split(",")
    return len(r) >= 2 and r[1].isdigit() and int(r[1]) > int(r[0])   # ffprobe can print a trailing comma


def duration(video):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True).stdout.strip()
    try:
        return float(r)
    except ValueError:
        return None


def kept_s(w, spans):
    """Seconds of a word inside the kept spans (build_timeline.is_kept's measure)."""
    return sum(max(0.0, min(w["end"], s["end"]) - max(w["start"], s["start"])) for s in spans)


def cut_transcript(folder, dur):
    """The source transcript laid on the cut: every word, cut ones marked, removed pauses as chips, in lines.
    None unless decisions.json (kept source spans) and words.raw.json are there and add up to this video."""
    try:
        spans = sorted(load(folder / "decisions.json"), key=lambda s: s["start"])
        words = [w for w in load(folder / "words.raw.json") if w.get("type", "word") == "word"]
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if not spans or not words or dur is None or abs(sum(s["end"] - s["start"] for s in spans) - dur) > 0.5:
        return None
    offs, t = [], 0.0
    for s in spans:
        offs.append(t)
        t += s["end"] - s["start"]

    def on_cut(w):   # a kept word's start on the cut's timeline, in the span it plays in (build_timeline.retime)
        i = max(range(len(spans)), key=lambda k: min(w["end"], spans[k]["end"]) - max(w["start"], spans[k]["start"]))
        return round(max(w["start"], spans[i]["start"]) - spans[i]["start"] + offs[i], 2)

    why = span_times(folder)
    lines, line, prev = [], [], 0.0
    for w in words:
        if not all(isinstance(w.get(k), (int, float)) for k in ("start", "end")):   # no timing: shown, never editable
            line.append({"w": str(w.get("text", "")).strip(), "t": None, "nt": 1})
            continue
        gap = {"start": prev, "end": w["start"]}
        removed = (gap["end"] - gap["start"]) - kept_s(gap, spans)
        if removed >= 0.3:
            line.append({"p": round(removed, 1)})
        kept = kept_s(w, spans) >= min(0.1, (w["end"] - w["start"]) / 2)
        # s/e: the word on the raw take, for the transcript editor (select, restore or cut, preview)
        x = {"w": w["text"].strip(), "t": on_cut(w) if kept else None, "s": round(w["start"], 3), "e": round(w["end"], 3)}
        if not kept:   # why it is cut: g = the spans.json entry, k = its kind; no span = the pause trim
            g = next((i for i, (a, b, _, _) in enumerate(why) if a - 1e-3 <= w["start"] and w["end"] <= b + 1e-3), None)
            x.update({"k": why[g][2], "g": g, "n": why[g][3]} if g is not None else {"k": "pause"})
        line.append(x)
        prev = w["end"]
        if w["text"].strip()[-1:] in ".?!" or len(line) >= 24:
            lines.append(line)
            line = []
    lines += [line] if line else []
    try:
        src = float(load(folder / "report.json")["duration"])
    except (OSError, ValueError, KeyError, TypeError):
        src = max((w["end"] for w in words if isinstance(w.get("end"), (int, float))), default=0.0)
    cuts = sum(1 for a, b in zip([{"end": 0.0}] + spans, spans + [{"start": src}]) if b["start"] - a["end"] > 0.05)
    return {"lines": [{"t": next((x["t"] for x in ln if x.get("t") is not None), None), "tokens": ln} for ln in lines],
            "cuts": cuts, "source_s": round(src, 1), "points": [round(o, 2) for o in offs[1:]],
            "kept": [[round(s["start"], 3), round(s["end"], 3)] for s in spans]}


def chapters_of(folder):
    """chapters.txt ("0:00 title" lines, chapters.py) as [[seconds, title], ...]."""
    try:
        rows = (folder / "chapters.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for r in rows:
        m = re.match(r"(?:(\d+):)?(\d+):(\d+)\s+(.+)", r.strip())
        if m:
            out.append([int(m[1] or 0) * 3600 + int(m[2]) * 60 + int(m[3]), m[4]])
    return out


def data_of(folder, name):
    try:
        return load(folder / "review" / Path(name).name) if name else {}
    except (OSError, ValueError):
        return {}


def upgrade_transcript(folder, p):
    """A cut round's data file written before words carried their raw-take times (s/e): every word looked untimed, so
    the page could neither edit nor follow it. Rebuilt from decisions.json + words.raw.json when they still add up."""
    try:
        data = load(p)
        toks = [x for ln in (data.get("transcript") or {}).get("lines", []) for x in ln["tokens"] if "w" in x]
        if not toks or any("s" in x or x.get("nt") for x in toks):
            return
        tx = cut_transcript(folder, data.get("duration"))
        if tx:
            data["transcript"] = tx
            save(p, data)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass


def round_data(folder, video, stage, rnd, plan):
    """review/data-<stage><round>.json: what the page draws beside the video. Every part optional."""
    dur = duration(video)
    d = {"duration": dur}
    tx = cut_transcript(folder, dur)
    if tx and stage == "cut":
        d["transcript"] = tx
    if stage == "edit" and chapters_of(folder):
        d["chapters"] = chapters_of(folder)
    elif tx:
        d["points"] = tx["points"]
    try:
        p = load(plan) if plan else None
    except (OSError, ValueError):
        p = None
    if stage == "edit" and isinstance(p, dict):
        span = lambda xs: [[round(x["start"], 2), round(x["end"], 2)] for x in xs or [] if "start" in x and "end" in x]
        d["tracks"] = {"cards": span(p.get("cards")), "zooms": span(p.get("zooms")),
                       "captions": span((p.get("captions") or {}).get("chunks"))}
    name = f"data-{stage}{rnd}.json"
    (folder / "review" / name).write_text(json.dumps(d), encoding="utf-8")
    return name


def words_of(transcript):
    """A transcript's words: the plugin's list shape, or {"words": [...]} from other tools."""
    if not transcript or not Path(transcript).exists():
        return []
    d = load(transcript)
    ws = d.get("words", []) if isinstance(d, dict) else d
    return [w for w in ws if isinstance(w, dict) and w.get("type", "word") == "word"]


def words_at(transcript, t0, t1):
    got = [w["text"].strip() for w in words_of(transcript) if w["end"] >= t0 - 1.5 and w["start"] <= t1 + 1.5]
    return " ".join(x for x in got if x)


def card_at(plan, t):
    """The plan.json card on screen at t, so a note maps to the card it is about."""
    if not plan or not Path(plan).exists():
        return None
    for i, c in enumerate(load(plan).get("cards", [])):
        if c.get("start", 0) <= t < c.get("end", 0):
            kind = (c.get("anim") or {}).get("type") or c.get("lane") or "image"
            return {"i": i, "kind": kind, "trigger_word": c.get("trigger_word"), "src": c.get("src"),
                    "start": c["start"], "end": c["end"]}
    return None


def grab(video, t, out):
    """The frame at t as a jpg. At the very end (the playhead on the last frame) `-ss t` lands past the last
    frame and writes nothing, so a note lost its still: seek from the end instead."""
    d = duration(video)
    seek = ["-sseof", "-0.1"] if d and t >= d - 0.05 else ["-ss", f"{t:.3f}"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *seek, "-i", str(video), "-frames:v", "1",
                    "-vf", "scale=960:-2", "-q:v", "3", str(out)], check=False)


_HOME = {"at": 0.0, "url": None}
THEMES = ("light", "dark", "system")


def _ui_json():
    return Path(os.environ.get("AI_EDITOR_HOME", "").strip() or Path.home() / ".ai-video-editor") / "ui.json"


def ui():
    """$AI_EDITOR_HOME/ui.json: the theme, the first videos (the first-edit notice) and whether it was dismissed."""
    try:
        d = load(_ui_json())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def set_ui(**kw):
    _ui_json().parent.mkdir(parents=True, exist_ok=True)
    with FileLock(_ui_json()):
        save(_ui_json(), {**ui(), **kw})


def theme():
    """The light/dark choice, one for the window and every review page (they run on different ports, so
    a browser's per-origin storage cannot share it). "system" until the user picks."""
    t = ui().get("theme")
    return t if t in THEMES else "system"


def set_theme(t):
    if t not in THEMES:
        raise ValueError(t)
    set_ui(theme=t)


INTRO_VIDEOS = 3   # the first-edit notice shows on round 1 of the cut and of the edit of the user's first 3 videos


def seen(folder):
    """Count this edit among the user's first videos. Never raises: the notice is a nicety."""
    try:
        vs = ui().get("videos") or []
        if str(folder) not in vs and len(vs) < INTRO_VIDEOS:
            set_ui(videos=vs + [str(folder)])
    except (OSError, TimeoutError):
        pass


def intro(folder, d):
    """Show "Your first videos teach it your style": round 1 of a stage, one of the first videos, not dismissed."""
    u = ui()
    return bool(not u.get("intro_off") and str(folder) in (u.get("videos") or []) and d.get("round") == 1 and not d.get("raw"))


def themed(html):
    """The page with the saved theme already on <html>, so it paints in it from the first frame."""
    t = theme()
    return html if t == "system" else html.replace(b"<html", f'<html data-theme="{t}"'.encode(), 1)


def home_url():
    """The home page's address (home.py keeps one fixed address in $AI_EDITOR_HOME/home/.server) when it
    answers, else None. Checked at most every 10 s."""
    if time.time() - _HOME["at"] < 10:
        return _HOME["url"]
    f = Path(os.environ.get("AI_EDITOR_HOME", "").strip() or Path.home() / ".ai-video-editor") / "home" / ".server"
    try:
        url = f.read_text(encoding="utf-8").strip()
        urllib.request.urlopen(url.replace("/?t=", "/state?t="), timeout=1).read()
    except (OSError, ValueError):
        url = None
    _HOME.update(at=time.time(), url=url)
    return url


def listening(folder):
    try:
        return time.time() - (folder / "review" / ".listening").stat().st_mtime < BEAT_S
    except OSError:
        return False


BUSY_S = 900   # a Claude command on this edit in the last 15 min: Claude is mid-round, not gone


def busy(folder):
    """Claude ran a review command here recently (status, round, resolve...): it is working even with no
    `wait` running, and takes notes sent now next."""
    try:
        return time.time() - (folder / "review" / ".claude").stat().st_mtime < BUSY_S
    except OSError:
        return False


def log_pct(path):
    """The last "N/M" in a render log (Remotion prints "Rendered 812/3255"), as a percent."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4096))
            tail = f.read().decode(errors="ignore")
    except OSError:
        return None
    m = re.findall(r"(\d+)/(\d+)", tail)
    return round(100 * int(m[-1][0]) / int(m[-1][1])) if m and int(m[-1][1]) else None


def log_round(d):
    """Every round's video is kept, so the page has a tab per round."""
    rs = [r for r in d.setdefault("rounds", []) if (r["stage"], r["round"]) != (d["stage"], d["round"])]
    old = next((r for r in d["rounds"] if (r["stage"], r["round"]) == (d["stage"], d["round"])), {})
    d["rounds"] = rs + [{**{k: d.get(k) for k in ("stage", "round", "video", "note", "label", "alts", "data")},
                         "at": old.get("at") or now()}]


def alt_of(d, name, rnd=None):
    """A version's video and transcript. No name = the round's main video."""
    r = rnd or d
    a = next((a for a in r.get("alts") or [] if a["name"] == name), None)
    return a or {"video": r["video"], "transcript": d.get("transcript")}


def editable(d, c):
    """The page may change or delete a note of the open round, or one queued for the next."""
    return c.get("queued") or (c["round"] == d["round"] and c["stage"] == d["stage"] and not d.get("sent"))


def remap(t, old, new):
    """A time on the old round's cut moved to where the same word is said on the new one, by the two rounds'
    cut transcripts (both from words.raw.json, so word n is the same word). None when that cannot be told."""
    flat = lambda tx: [x for ln in tx["lines"] for x in ln["tokens"] if "w" in x]
    if not old or not new or len(flat(old)) != len(flat(new)):
        return None
    a, b = flat(old), flat(new)
    i = max((k for k, x in enumerate(a) if x["t"] is not None and x["t"] <= t), default=None)
    j = next((k for k in range(i, len(b)) if b[k]["t"] is not None), None) if i is not None else None
    if j is None:
        return None
    return round(b[j]["t"] + (t - a[i]["t"] if j == i else 0), 2)


def raw_to_cut(folder, t):
    """A time on the raw clip moved onto the cut by decisions.json (the kept source spans). A moment the cut
    removed lands where the cut picks up again. None without decisions.json."""
    try:
        spans = sorted(load(folder / "decisions.json"), key=lambda s: s["start"])
    except (OSError, ValueError, TypeError, KeyError):
        return None
    off = 0.0
    for s in spans:
        if t < s["end"]:
            return round(off + max(0.0, t - s["start"]), 2)
        off += s["end"] - s["start"]
    return round(off, 2) if spans else None


def raw_video(folder, d):
    """The raw take the cut came from: round 0's clip, else report.json's source. None when neither is there."""
    r0 = next((r for r in d.get("rounds", []) if (r["stage"], r["round"]) == ("cut", 0)), None)
    try:
        p = Path(r0["video"] if r0 else load(folder / "report.json")["source"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return p if p.is_file() else None


def _locate():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "cut" / "scripts"))
    from textnorm import locate, nwords
    return locate, nwords


def timed(t):
    return all(isinstance(t.get(k), (int, float)) for k in ("start", "end"))


def span_range(sp, toks, locate):
    """The token range [a, b] a spans.json entry quotes, as build_timeline.resolve_spans picks it. None if it does not resolve."""
    hits = locate(sp.get("text", ""), toks)
    if "after" in sp:
        hits = [h for h in hits if toks[h[0]]["start"] >= float(sp["after"])]
    if "occurrence" in sp:
        k = int(sp["occurrence"]) - 1
        return hits[k] if 0 <= k < len(hits) else None
    return hits[0] if hits and ("after" in sp or len(hits) == 1) else None


def cut_toks(folder):
    """The tokens spans.json quotes, exactly as build_timeline.py lists them (untimed ones too, so "occurrence"
    counts the same words)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "cut" / "scripts"))
    from textnorm import load_words
    return [t for t in load_words(folder / "words.raw.json") if isinstance(t, dict) and t.get("type") in ("word", "audio_event")]


def files_sha(folder):
    """One hash over spans.json and keep.json as they are now: Stop only puts the old ones back over what /edits wrote."""
    h = hashlib.sha256()
    for n in ("spans.json", "keep.json"):
        f = folder / n
        h.update(f.read_bytes() if f.exists() else b"-")
    return h.hexdigest()


def span_times(folder):
    """[(start, end, kind, note)] in raw-take seconds for each spans.json entry that resolves; [] without the files."""
    try:
        locate, _ = _locate()
        toks, spans = cut_toks(folder), load(folder / "spans.json")
    except (OSError, ValueError, ImportError, KeyError, TypeError):
        return []
    out = []
    for sp in spans if isinstance(spans, list) else []:
        ab = span_range(sp, toks, locate) if isinstance(sp, dict) else None
        if ab and timed(toks[ab[0]]) and timed(toks[ab[1]]):
            out.append((toks[ab[0]]["start"], toks[ab[1]]["end"], sp.get("kind", "retake"), sp.get("note", "")))
    return out


def minus(ranges, cuts):
    """[[s, e]] ranges with every [a, b] of cuts taken out."""
    for a, b in cuts:
        ranges = [r for s, e in ranges for r in ([s, min(e, a)], [max(s, b), e]) if r[1] - r[0] > 1e-3]
    return ranges


def merged(ranges):
    out = []
    for s, e in sorted(ranges):
        if out and s <= out[-1][1] + 1e-3:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def apply_edits(folder, restore, cut, pause=()):
    """The review page's transcript edits onto spans.json and keep.json, deterministically. restore/cut/pause:
    [[start, end], ...] in raw-take seconds. A token is in a range when its middle is, or the range sits inside it (a
    cut inside a word takes the whole word). Restore drops those tokens from the spans covering them (a span loses them
    or splits); a restored word no span covers was taken by the pause trim, so its time goes into keep.json, which
    build_timeline.py leaves in. pause: removed pauses to put back whole (keep.json too). Cut adds a "manual" span
    quoting each run of kept tokens and takes those words out of keep.json. Each new span quotes its words, with
    "occurrence" when they are said more than once. spans.prev.json and keep.prev.json keep what was there.
    Spans that end up overlapping in time merge into one; the result is checked with build_timeline.resolve_spans
    before anything is written. Writes are atomic. Returns the counts, or raises ValueError."""
    locate, nwords = _locate()
    import build_timeline
    toks = cut_toks(folder)
    sp_path, kp_path = folder / "spans.json", folder / "keep.json"
    spans = load(sp_path) if sp_path.exists() else []
    keep0 = load(kp_path) if kp_path.exists() else []
    try:
        dec = load(folder / "decisions.json")
    except (OSError, ValueError):
        dec = None   # no build yet: nothing tells which words the pause trim took

    def picked(ranges):
        out = set()
        for r in ranges or []:
            a, b = float(r[0]), float(r[1])
            out |= {i for i, t in enumerate(toks) if t.get("type") == "word" and timed(t)   # never a hidden (cough) span
                    and (a - 1e-3 <= (t["start"] + t["end"]) / 2 <= b + 1e-3 or (t["start"] <= a and b <= t["end"]))}
        return out
    R, C = picked(restore), picked(cut)
    C -= R
    ranges = []
    for i, sp in enumerate(spans):
        ab = span_range(sp, toks, locate)
        if ab is None:
            raise ValueError(f"span {i} ({sp.get('text', '')[:40]!r}) does not match the transcript: fix spans.json first")
        ranges.append(ab)
    covered = {i for a, b in ranges for i in range(a, b + 1)}

    def runs(idx):
        out = []
        for i in sorted(idx):
            if out and i == out[-1][-1] + 1:
                out[-1].append(i)
            else:
                out.append([i])
        bad = lambda t: not nwords(t.get("text", "")) or not timed(t)
        for r in out:   # trim tokens with no words (a lone dash) or no timing: a quote cannot start or end on them
            while r and bad(toks[r[0]]):
                r.pop(0)
            while r and bad(toks[r[-1]]):
                r.pop()
        return [r for r in out if r]

    def quote(run, base):
        q = {k: v for k, v in base.items() if k not in ("text", "occurrence", "after")}
        q["text"] = " ".join(toks[i]["text"].strip() for i in run)
        hits = locate(q["text"], toks)
        if len(hits) > 1:
            q["occurrence"] = hits.index((run[0], run[-1])) + 1
        return q
    new = []
    for sp, (a, b) in zip(spans, ranges):
        keep = [i for i in range(a, b + 1) if i not in R]
        new += [sp] if len(keep) == b - a + 1 else [quote(r, sp) for r in runs(keep)]
    new += [quote(r, {"kind": "manual", "note": "cut on the review page"}) for r in runs(C - covered)]
    rng = []
    for q in new:   # the new file must build: every quote lands on the words it was made from
        ab = span_range(q, toks, locate)
        if ab is None or not (timed(toks[ab[0]]) and timed(toks[ab[1]])):
            raise ValueError(f"could not quote {q['text'][:40]!r} unambiguously")
        rng.append([toks[ab[0]]["start"], toks[ab[1]]["end"], ab, q])
    rng.sort(key=lambda x: x[0])
    new = []
    for x in rng:   # spans that overlap in time (a cut beside a sound token) become one, as build_timeline asks
        if new and x[0] < new[-1][1]:
            last = new[-1]
            a, b = min(last[2][0], x[2][0]), max(last[2][1], x[2][1])
            base = last[3] if last[3].get("kind") != "manual" else x[3]
            last[:] = [min(last[0], x[0]), max(last[1], x[1]), (a, b), quote(list(range(a, b + 1)), base)]
        else:
            new.append(x)
    new = [x[3] for x in new]
    try:
        with contextlib.redirect_stderr(io.StringIO()) as err:
            build_timeline.resolve_spans(new, toks)
    except SystemExit:
        raise ValueError("the changed cut would not build: " + err.getvalue().strip().replace("ERROR: ", "")[:200])
    # the pause trim's words: restored ones not in a span and not playing now (decisions.json), as runs of time
    trimmed = runs({i for i in R - covered if dec is not None and kept_s(toks[i], dec) < min(0.1, (toks[i]["end"] - toks[i]["start"]) / 2)})
    add = [[toks[r[0]]["start"], toks[r[-1]]["end"]] for r in trimmed] + [[float(a), float(b)] for a, b in pause or []]
    keep = merged(minus([[k["start"], k["end"]] for k in keep0] + add, [[toks[i]["start"], toks[i]["end"]] for i in C]))
    keep = [{"start": round(s, 3), "end": round(e, 3)} for s, e in keep]
    out = {"restored": len(R & covered) + sum(map(len, trimmed)), "cut": len(C - covered), "pauses": len(pause or []),
           "spans": len(new), "keep": len(keep)}
    if out["restored"] or out["cut"] or out["pauses"]:
        save(folder / "spans.prev.json", spans)
        save(folder / "keep.prev.json", keep0)
        save(sp_path, new)
        if keep or kp_path.exists():
            save(kp_path, keep)
        out["sha"] = files_sha(folder)
    return out


def fmt(t):
    return f"{int(t // 60)}:{t % 60:04.1f}"


def stage_name(s):
    return {"cut": "the cut", "edit": "the edit"}.get(s, s)


# ---------------------------------------------------------------- server
def make_handler(folder, token):
    rj = folder / "review.json"

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def cookie_name(self):
            return f"ave-review-{self.server.server_address[1]}"   # cookies ignore ports: one per server

        def authorized(self):
            q = parse_qs(urlsplit(self.path).query).get("t", [""])[0]
            c = SimpleCookie()
            try:
                c.load(self.headers.get("Cookie") or "")
            except Exception:
                pass
            got = c[self.cookie_name()].value if self.cookie_name() in c else ""
            return any(hmac.compare_digest(v.encode(), token.encode()) for v in (q, got) if v)

        def send(self, code, body, ctype="application/json", cookie=False):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            if cookie:
                self.send_header("Set-Cookie", f"{self.cookie_name()}={token}; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def file(self, p, ctype):
            # Range requests: without 206 the browser cannot seek a long mp4
            size = p.stat().st_size
            start, end = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
            if m and (m[1] or m[2]):
                start, end = (int(m[1]), int(m[2]) if m[2] else size - 1) if m[1] else (size - int(m[2]), size - 1)
                end = min(end, size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            else:
                self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            self.end_headers()
            try:
                with open(p, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        b = f.read(min(1 << 20, left))
                        if not b:
                            break
                        self.wfile.write(b)
                        left -= len(b)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self.authorized():
                return self.send(403, {"error": "open the page with the URL review.py printed"})
            path = urlsplit(self.path).path
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            if askhook.route(self, "GET", path, q):   # Claude's questions (askhook.py)
                return
            if path == "/":
                return self.send(200, themed(HTML.read_bytes()), "text/html; charset=utf-8", cookie=True)
            if path == "/state":
                d = load(rj)
                d["theme"] = theme()
                d["listening"], d["busy"] = listening(folder), busy(folder)
                d["page"] = live.page_version(HTML, HTML.with_name("ask.js"))   # the tab reloads when this changes
                d["cutcheck"] = (folder / "cut-check.html").exists()
                d["rawvideo"] = raw_video(folder, d) is not None   # the transcript editor's preview plays the raw take
                d["home"] = home_url()   # the Home button: back to the editor's window, or how to start it
                d["intro"] = intro(folder, d)
                for x in d.get("activity", [])[-1:]:
                    if x.get("log") and not x.get("done"):
                        x["pct"] = log_pct(x["log"])
                        try:   # a render still writing its log is alive even when Claude is quiet
                            x["log_age"] = round(time.time() - Path(x["log"]).stat().st_mtime)
                        except OSError:
                            pass
                if making_edit(d):
                    d["making"] = {"style": making_style(folder), "steps": EDIT_STEPS}
                return self.send(200, d)
            if path == "/video":
                d = load(rj)
                if q.get("k") == "raw":
                    p = raw_video(folder, d)
                    return self.file(p, mimetypes.guess_type(p.name)[0] or "video/mp4") if p else self.send(404, {})
                r = next((r for r in d.get("rounds", []) if f"{r['stage']}-{r['round']}" == q.get("k")), d)
                p = Path(alt_of(d, q.get("alt", ""), r)["video"])
                return self.file(p, "video/mp4") if p.exists() else self.send(404, {})
            if path == "/data":   # the round's transcript, segments and tracks (round_data)
                d = load(rj)
                r = next((r for r in d.get("rounds", []) if f"{r['stage']}-{r['round']}" == q.get("k")), None)
                p = folder / "review" / Path(r.get("data") or "-").name if r else None
                if p and p.is_file() and (r["stage"], r["round"]) == (d["stage"], d["round"]) == ("cut", d["round"]):
                    upgrade_transcript(folder, p)
                return self.send(200, p.read_bytes() if p and p.is_file() else {})
            if path.startswith("/frames/"):
                p = folder / "review" / Path(unquote(path)).name
                ct = {".png": "image/png", ".gif": "image/gif", ".webp": "image/webp"}.get(p.suffix, "image/jpeg")
                return self.file(p, ct) if p.is_file() else self.send(404, {})
            # the cut skill's transcript page, with its video beside it
            if path in ("/cut-check.html", "/cut.mp4") and (folder / path[1:]).exists():
                return self.file(folder / path[1:], "text/html; charset=utf-8" if path.endswith("html") else "video/mp4")
            self.send(404, {})

        def do_POST(self):
            if not self.authorized():
                return self.send(403, {"error": "open the page with the URL review.py printed"})
            if not askhook.same_origin(self):   # a POST from another site's page, even with the cookie
                return self.send(403, {"error": "not from this page"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except ValueError:
                return self.send(400, {"error": "not json"})
            path = urlsplit(self.path).path
            if askhook.route(self, "POST", path, {}, body):
                return
            if path == "/theme":   # the toggle: one choice for the window and every review page
                try:
                    set_theme(body.get("theme"))
                except ValueError:
                    return self.send(400, {"error": "light, dark or system"})
                return self.send(200, {"theme": theme()})
            if path == "/intro":   # "Got it" on the first-edit notice: never again
                set_ui(intro_off=True)
                return self.send(200, {})
            if path == "/learn":   # the learn panel: the ticked rules, or Not now. `wait` hands them to Claude
                def lc(d):
                    lr = d.get("learn") or {}
                    if lr.get("state") != "proposed":
                        return False
                    ids = sorted({i for i in body.get("chosen") or [] if isinstance(i, int) and 0 <= i < len(lr["rules"])})
                    lr.update(state="chosen" if ids and not body.get("skip") else "skipped", chosen=ids, handled=False)
                    return True
                return self.send(200, {}) if update(rj, lc) else self.send(409, {"error": "nothing to choose"})
            if path == "/comment":
                return self.comment(body)
            if path == "/edit":
                return self.edit(body)
            if path == "/edits":
                return self.transcript_edits(body)
            if path == "/scope":   # a saved note: all versions ("*") or one version (a name, None = main)
                def sc(d):
                    for c in d["comments"]:
                        if c["id"] == body.get("id") and editable(d, c):
                            c["alt"] = body.get("alt") or None
                update(rj, sc)
                return self.send(200, {})
            if path == "/delete":
                def rm(d):
                    d["comments"] = [c for c in d["comments"] if not (c["id"] == body.get("id") and editable(d, c))]
                update(rj, rm)
                return self.send(200, {})
            if path == "/check":   # "got it" on one of Claude's answers
                ids = set(body.get("ids", []))
                def ck(d):
                    for c in d["comments"]:
                        if c["id"] in ids:
                            c["checked"] = True
                update(rj, ck)
                return self.send(200, {})
            if path == "/event":
                if body.get("type") not in ("send", "approve", "stop"):
                    return self.send(400, {})
                if body["type"] == "approve" and load(rj).get("raw"):   # nothing to approve before the first cut
                    return self.send(409, {"error": "the cut is not ready yet"})
                if body["type"] == "stop" and load(rj).get("raw"):   # the first cut cannot be stopped from here
                    return self.send(409, {"error": "the first cut is on its way"})
                if body["type"] == "stop":   # the round's notes open again; `status` tells Claude to stop
                    def st(d):
                        if d.get("sent") and d["events"] and (d["events"][-1]["type"] in ("send", "edits") or making_edit(d)):
                            e = d["events"][-1]
                            for ev in d["events"]:   # a send or approve `wait` has not taken yet is taken back
                                ev["handled"] = True
                            # stop undoes the edits, but only over the files /edits wrote: never over Claude's later change
                            if e["type"] == "edits" and e.get("sha") == files_sha(folder) and (folder / "spans.prev.json").exists():
                                save(folder / "spans.json", load(folder / "spans.prev.json"))
                                if (folder / "keep.prev.json").exists():
                                    save(folder / "keep.json", load(folder / "keep.prev.json"))
                            d["events"].append({"type": "stop", "stage": d["stage"], "round": d["round"],
                                                "at": now(), "handled": True})
                            d["sent"], d["stopped"] = False, now()
                            for c in d["comments"]:   # notes queued meanwhile join the reopened round
                                if c.pop("queued", None):
                                    c["round"] = d["round"]
                            for x in d.get("activity", []):
                                x["done"] = True
                    update(rj, st)
                    return self.send(200, {})
                style = style_label(folder) if body["type"] == "approve" else None

                def ev(d):
                    if d.get("sent") and d["events"] and d["events"][-1]["type"] == "edits":
                        return False   # Claude is applying transcript changes: Stop first
                    d["events"].append({"type": body["type"], "stage": d["stage"], "round": d["round"],
                                        "at": now(), "handled": False})
                    d["sent"] = True
                    d.pop("stopped", None)
                    if body["type"] == "approve" and (d["stage"] == "edit" or d.get("final")):   # the finished video
                        d["learn"] = {"state": "reading" if session_notes(d) else "none", "style": style}
                    return True
                if not update(rj, ev):
                    return self.send(409, {"error": "Claude is applying your transcript changes"})
                return self.send(200, {})
            self.send(404, {})

        def comment(self, body):
            text = str(body.get("text", "")).strip()
            imgs = body.get("images") or []
            if not text and not imgs:
                return self.send(400, {"error": "empty"})
            try:
                t = float(body["t"])
                te = float(body["t_end"]) if body.get("t_end") is not None else None
            except (KeyError, TypeError, ValueError):
                return self.send(400, {"error": "no time"})
            d = load(rj)
            last = d["events"][-1]["type"] if d["events"] else None
            if d.get("sent") and last not in ("send", "edits"):   # approved: nothing comes after it on this stage
                return self.send(409, {"error": "this round is approved"})
            queued = bool(d.get("sent"))   # with Claude: the note waits for the next round
            alt = body.get("alt") or None
            src = alt_of(d, alt if alt != "*" else None)
            cid = max([c["id"] for c in d["comments"]] + [0]) + 1
            frame = f"r{d['stage']}{d['round']}-c{cid}.jpg"
            grab(src["video"], t, folder / "review" / frame)
            images = save_images(folder, imgs, f"r{d['stage']}{d['round']}-c{cid}")
            c = {"id": cid, "round": d["round"] + queued, "stage": d["stage"], "t": round(t, 2), "alt": alt,
                 "t_end": round(te, 2) if te is not None else None, "text": text,
                 "words": words_at(src.get("transcript"), t, te if te is not None else t),
                 "card": card_at(d.get("plan"), t) if d["stage"] == "edit" else None,
                 "frame": frame, "images": images, "status": "open", "reply": None, "new_t": None, "at": now()}
            if queued:
                c["queued"] = True
            update(rj, lambda d: d["comments"].append(c))
            return self.send(200, c)

        def transcript_edits(self, body):
            """The transcript editor's Apply: restore/cut word ranges (raw-take seconds) into spans.json, then an
            "edits" event, so `wait` exits and Claude rebuilds, renders, verifies and opens the next round."""
            try:
                restore, cut, pause = (list(body.get(k) or []) for k in ("restore", "cut", "pause"))
                for r in restore + cut + pause:
                    if len(r) != 2 or float(r[0]) > float(r[1]):
                        raise ValueError
            except (TypeError, ValueError, AttributeError):
                return self.send(400, {"error": "restore, cut and pause take [[start, end], ...]"})
            with LOCK:   # one Apply at a time: the sent check, the files and the event move together
                d = load(rj)
                if d.get("stage") != "cut" or d.get("raw"):
                    return self.send(409, {"error": "words change on the cut only"})
                if d.get("sent"):
                    return self.send(409, {"error": "Claude has this round"})
                try:
                    out = apply_edits(folder, restore, cut, pause)
                except Exception as e:   # a spans.json or transcript this cannot read: say so, never drop the connection
                    return self.send(409, {"error": str(e) or type(e).__name__})
                if not (out["restored"] or out["cut"] or out["pauses"]):
                    return self.send(200, {**out, "event": False})

                def ev(d):
                    d["events"].append({"type": "edits", "stage": d["stage"], "round": d["round"], "at": now(), "handled": False,
                                        "n": len(restore) + len(cut) + len(pause), "restore": restore, "cut": cut, "pause": pause,
                                        "counts": {k: v for k, v in out.items() if k != "sha"}, "sha": out["sha"]})
                    d["sent"] = True
                    d.pop("stopped", None)
                update(rj, ev)
            return self.send(200, {**out, "event": True})

        def edit(self, body):
            """A note changed in place: its text, its time (moved to the playhead: new frame still and words),
            its version scope, its images (kept names plus new data urls). Same id; refused once Claude has it."""
            d = load(rj)
            c = next((x for x in d["comments"] if x["id"] == body.get("id")), None)
            if not c:
                return self.send(404, {"error": "no such note"})
            if not editable(d, c):
                return self.send(409, {"error": "this round is with Claude" if d.get("sent") else "this note is from an earlier round"})
            new = dict(c)
            if "text" in body:
                new["text"] = str(body["text"] or "").strip()
            if "alt" in body:
                new["alt"] = body["alt"] or None
            if "images" in body:
                new["images"] = save_images(folder, body["images"] or [], f"r{c['stage']}{c['round']}-c{c['id']}",
                                            keep=c.get("images") or [])
            if not new["text"] and not new["images"]:
                return self.send(400, {"error": "empty"})
            src = alt_of(d, new["alt"] if new["alt"] != "*" else None)
            if "t" in body:
                try:
                    t = round(float(body["t"]), 2)
                except (TypeError, ValueError):
                    return self.send(400, {"error": "no time"})
                if c["t_end"] is not None:
                    new["t_end"] = round(t + c["t_end"] - c["t"], 2)
                new["t"], new["frame"] = t, f"r{c['stage']}{c['round']}-c{c['id']}-{int(time.time() * 1000) % 10 ** 8}.jpg"
                grab(src["video"], t, folder / "review" / new["frame"])
                new["card"] = card_at(d.get("plan"), t) if c["stage"] == "edit" else None
            if "t" in body or "alt" in body:
                new["words"] = words_at(src.get("transcript"), new["t"], new["t_end"] if new["t_end"] is not None else new["t"])
            new["edited"] = now()

            def put(d):   # again under the lock: the round may have gone to Claude meanwhile
                for i, x in enumerate(d["comments"]):
                    if x["id"] == new["id"]:
                        if not editable(d, x):
                            return False
                        d["comments"][i] = {**new, "round": x["round"], **({"queued": True} if x.get("queued") else {})}
                        return True
                return False
            if not update(rj, put):
                return self.send(409, {"error": "this round is with Claude"})
            return self.send(200, new)
    return H


def save_images(folder, imgs, prefix, keep=()):
    """A note's pictures: names it already has (kept) and pasted or dropped data urls (saved). Only these types,
    under 15 MB each, at most 6."""
    out = []
    for im in imgs:
        if len(out) == 6 or not isinstance(im, str):
            continue
        if im in keep:
            out.append(im)
            continue
        m = re.match(r"data:image/(png|jpeg|gif|webp);base64,(.+)", im, re.S)
        if not m:
            continue
        try:
            raw = base64.b64decode(m[2])
        except ValueError:
            continue
        if len(raw) > MAX_IMAGE:
            continue
        k = 1
        while any((folder / "review" / f"{prefix}-img{k}.{x}").exists() for x in ("jpg", "png", "gif", "webp")):
            k += 1
        name = f"{prefix}-img{k}.{'jpg' if m[1] == 'jpeg' else m[1]}"
        (folder / "review" / name).write_bytes(raw)
        out.append(name)
    return out


def running(folder):
    """The URL of this folder's server if one answers, else None."""
    f = folder / "review" / ".server"
    try:
        url = f.read_text(encoding="utf-8").strip()
        s = json.loads(urllib.request.urlopen(url.replace("/?t=", "/state?t="), timeout=2).read())
        return url if s.get("folder") == str(folder) else None
    except (OSError, ValueError):
        return None


OPEN = webbrowser.open   # a new browser tab (the demo swaps it)


def home_opens(folder):
    """True when a showing home window holds this edit's video job: it goes to this page in its own tab."""
    if not askhook.live("home"):
        return False
    f = Path(os.environ.get("AI_EDITOR_HOME", "").strip() or Path.home() / ".ai-video-editor") / "home" / "state.json"
    try:
        jobs = json.loads(f.read_text(encoding="utf-8")).get("jobs") or []
    except (OSError, ValueError):
        return False
    return any(j.get("type") == "video" and j.get("state") == "taken" and (j.get("args") or {}).get("edit")
               and Path(j["args"]["edit"]).resolve() == folder.resolve() for j in jobs)


def show(url, folder):
    """A new tab, unless the home window is about to show this page itself."""
    if not home_opens(folder):
        OPEN(url)


def asker(folder, started):
    """Questions on this page come from the session that last ran a review.py command on this edit
    (review/.session), else the one that started the server. They name the edit."""
    def owner(sid):
        try:
            cur = (folder / "review" / ".session").read_text(encoding="utf-8").strip()
        except OSError:
            cur = ""
        own = cur or started
        return folder.name if not own or sid == own else None   # no session known (older Claude Code): anyone
    return owner


def serve(folder, port=0, open_browser=True):
    """(server, url). An already running server for this folder is reused: (None, its url)."""
    url = running(folder)
    if url:
        if open_browser:
            show(url, folder)
        return None, url
    token, want, last = secrets.token_urlsafe(32), port, folder / "review" / ".server"
    if last.is_file() and (os.name == "nt" or last.stat().st_mode & 0o077 == 0):   # only our own private file
        m = re.match(r"http://127\.0\.0\.1:(\d+)/\?t=([\w-]+)$", last.read_text(encoding="utf-8").strip())
        if m:   # the same address as last time, so an open tab or a saved link keeps working
            token, want = m.group(2), port or int(m.group(1))
    # exclusive bind on Windows, where SO_REUSEADDR (HTTPServer's default) lets two servers share a port
    cls = type("Server", (ThreadingHTTPServer,), {"allow_reuse_address": os.name != "nt"})
    try:
        srv = cls(("127.0.0.1", want), make_handler(folder, token))
    except OSError:   # the old port is taken now: a new one, same token
        srv = cls(("127.0.0.1", port), make_handler(folder, token))
    url = f"http://127.0.0.1:{srv.server_address[1]}/?t={token}"
    with os.fdopen(os.open(last, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        f.write(url)   # private: the token in it lets a page in
    if os.name != "nt":
        os.chmod(last, 0o600)
    askhook.register(srv, url, "review", asker(folder, askhook.session()))
    if open_browser:
        show(url, folder)
    return srv, url


# ---------------------------------------------------------------- cli
def keep(folder, video, stage, rnd, name=None):
    """A copy of the round's video in <folder>/review/, so the next render (written to the same cut.mp4)
    never changes what an earlier round's tab replays. A copy, not a hardlink: ffmpeg and Remotion rewrite
    the file in place, and a hardlink would change with it."""
    src = Path(video).resolve()
    tag = "-" + re.sub(r"[^\w-]", "_", name) if name else ""   # outside the f-string: Python 3.9 (a Mac's built-in) has no backslash there
    dst = folder / "review" / f"v-{stage}{rnd}{tag}{src.suffix}"
    if src != dst.resolve():
        # A Mac clones on APFS (cp -c: no extra disk until one side changes, and a rewrite of the cut
        # never reaches the clone); anything else, or a cp -c refused off APFS, copies.
        # ponytail: Linux btrfs/xfs could reflink with cp --reflink=auto if disk use matters there
        dst.unlink(missing_ok=True)
        if sys.platform != "darwin" or subprocess.run(["cp", "-c", str(src), str(dst)], capture_output=True).returncode:
            shutil.copyfile(src, dst)
    return str(dst)


def cmd_start(a, folder):
    (folder / "review").mkdir(parents=True, exist_ok=True)
    rj = folder / "review.json"
    d = load(rj) if rj.exists() else {"folder": str(folder), "name": folder.name, "comments": [], "events": []}
    raw = getattr(a, "raw", False)
    if d.get("raw") and not raw:   # the cut after the raw clip: round 1, with the notes left on the raw clip
        return cmd_round(a, folder)
    d.update(folder=str(folder), stage=a.stage, round=0 if raw else d.get("round", 0) + 1 if d.get("stage") == a.stage else 1,
             video=str(Path(a.video).resolve()), note=a.note or "", sent=False, alts=[],
             transcript=str(Path(a.transcript).resolve()) if a.transcript else None,
             plan=str(Path(a.plan).resolve()) if a.plan else None, vertical=probe(a.video), label=a.name)
    d.pop("stopped", None)
    d.pop("raw", None)
    if raw:   # with Claude from the start: notes queue for the cut, as after a send
        d.update(raw=True, sent=True)
        d["events"].append({"type": "send", "stage": d["stage"], "round": 0, "at": now(), "handled": True, "raw": True})
    # the raw clip is a source, never rewritten: played where it is, not copied (it can be gigabytes)
    d["video"] = d["video"] if raw else keep(folder, a.video, d["stage"], d["round"])
    d["data"] = round_data(folder, d["video"], d["stage"], d["round"], d["plan"])
    log_round(d)
    save(rj, d)
    if not raw:
        seen(folder)
    try:   # the home page's library lists this edit wherever it was made
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import library
        library.record(folder)
    except (OSError, ValueError, ImportError):
        pass
    print(f"{rj}: {stage_name(d['stage'])}, round {d['round']}")


def cmd_round(a, folder):
    d0 = load(folder / "review.json")
    if a.stage == "edit" and d0["stage"] == "cut" and d0.get("stopped"):
        print(f"STOPPED: the user pressed Stop on the page at {d0['stopped']}, which took the cut's approve back. "
              f"Do not open the edit: say so in one line and run `wait` again.")
        sys.exit(3)
    if d0.get("stopped"):
        sys.exit("STOPPED: the user pressed Stop on the page. Do not open a round; run `wait` for their next send.")

    def f(d):
        if a.stage and a.stage != d["stage"]:
            d["stage"], d["round"] = a.stage, 1
        else:
            d["round"] += 1
        d["video"] = keep(folder, a.video, d["stage"], d["round"])
        d["vertical"] = probe(a.video)
        if a.transcript:
            d["transcript"] = str(Path(a.transcript).resolve())
        if a.plan:
            d["plan"] = str(Path(a.plan).resolve())
        d["note"], d["sent"], d["alts"] = a.note or "", False, []   # versions belong to a round: add them again
        d["label"] = a.name or d.get("label")
        old = data_of(folder, d.get("data"))
        raw = d.pop("raw", False)
        d["data"] = round_data(folder, d["video"], d["stage"], d["round"], d.get("plan"))
        new = data_of(folder, d["data"])
        for c in d["comments"]:   # notes queued while Claude worked open on this round, where their words are said
            if c.pop("queued", None):
                nt = raw_to_cut(folder, c["t"]) if raw else remap(c["t"], old.get("transcript"), new.get("transcript"))
                if nt is None:
                    nt = min(c["t"], new.get("duration") or c["t"])
                    if old.get("duration") and new.get("duration") and abs(old["duration"] - new["duration"]) > 0.05:
                        c["moved"] = "time"   # the cut changed and no transcript says where: same time, check it
                elif abs(nt - c["t"]) >= 0.05:
                    c["moved"] = "words"
                if c.get("moved"):
                    c["t_was"] = c["t"]
                if c["t_end"] is not None:
                    c["t_end"] = round(c["t_end"] + nt - c["t"], 2)
                c.update(round=d["round"], stage=d["stage"], t=nt, frame=f"r{d['stage']}{d['round']}-c{c['id']}.jpg")
                src = alt_of(d, c["alt"] if c["alt"] != "*" else None)
                if c["alt"] not in (None, "*"):   # versions belong to a round: its version is gone, so the main video
                    c["alt"], src = None, alt_of(d, None)
                grab(src["video"], nt, folder / "review" / c["frame"])
                c["words"] = words_at(src.get("transcript"), nt, c["t_end"] if c["t_end"] is not None else nt)
                c["card"] = card_at(d.get("plan"), nt) if d["stage"] == "edit" else None
        d.pop("stopped", None)
        for x in d.get("activity", []):
            x["done"] = True
        log_round(d)
        return d
    d = update(folder / "review.json", f)
    seen(folder)
    still = [str(c["id"]) for c in d["comments"]
             if c["status"] == "open" and c["stage"] == d["stage"] and c["round"] < d["round"]]
    print(f"{stage_name(d['stage'])}, round {d['round']}" + (f"; still open: {', '.join(still)}" if still else ""))
    unlearned = [str(c["id"]) for c in d["comments"] if "taste" not in c and (c["stage"], c["round"]) != (d["stage"], d["round"])]
    if unlearned:
        print(f"notes not learned yet: {', '.join(unlearned)}. Run `learn` (see `wait`'s last line)")
    if not running(folder):
        print("the page is not running: start it with `serve`")


def cmd_resolve(a, folder):
    def f(d):
        for c in d["comments"]:
            if c["id"] == a.id:
                c["status"] = a.status
                if a.reply is not None:
                    c["reply"] = a.reply
                if a.new_t is not None:
                    c["new_t"] = a.new_t
                return c
        sys.exit(f"no note {a.id}")
    c = update(folder / "review.json", f)
    print(f"#{c['id']} {c['status']}")


# The style-edit's own steps, in order. The page fills its "Making the edit" bar from them (render by its log's
# percent); plan.py, retakes.py captions, route.py and edit.py report them with step(), Claude with status --step.
EDIT_STEPS = {"captions": "Writing the captions", "visuals": "Finding the visuals",
              "plan": "Planning zooms, cards and sound", "render": "Rendering the edit"}


def add_step(d, text, log=None, key=None, done=False):
    """Close the open step and add this one. The same step key again only refreshes its log."""
    act = d.setdefault("activity", [])
    last = act[-1] if act else {}
    if key and last.get("step") == key and not last.get("done") and last.get("round") == d["round"]:
        last["log"] = str(Path(log).resolve()) if log else last.get("log")
        return
    for x in act:
        x["done"] = True
    if not done:
        act.append({"text": text or EDIT_STEPS.get(key, key), "at": now(), "round": d["round"], "done": False,
                    "log": str(Path(log).resolve()) if log else None, **({"step": key} if key else {})})
    d["activity"] = act[-30:]


def making_edit(d):
    """Claude is making the styled edit: the cut was approved and no edit round has landed yet, or a sent edit
    round is being redone."""
    e = d["events"][-1] if d.get("events") else {}
    return bool(d.get("sent") and not d.get("stopped") and not d.get("final") and
                ((e.get("type") == "approve" and e.get("stage") == "cut" and d.get("stage") == "cut")
                 or (e.get("type") == "send" and d.get("stage") == "edit")))


def step(folder, key, log=None):
    """A style-edit script's step on the review page while Claude makes the edit. No page, not making the edit,
    or stopped: nothing. Never raises: the page is a nicety."""
    try:
        folder = Path(folder).resolve()
        rj = folder / "review.json"
        if key not in EDIT_STEPS or not rj.is_file():
            return False
        if not update(rj, lambda d: making_edit(d) and (add_step(d, None, log, key) or True)):
            return False
        (folder / "review" / ".claude").touch()   # Claude is working on it: the page shows the bar, not "not listening"
        return True
    except Exception:
        return False


def making_style(folder):
    """What the edit is styled as, for the page's "Making the edit" card."""
    try:
        from ai_editor import styleskill
        src = styleskill.sources(folder)
    except Exception:
        src = []
    if len(src) == 1:
        return f"@{re.sub(r'-mine$', '', src[0].parent.name)}'s style"
    return "your blended style" if src else "your style"


def cmd_final(folder):
    """The cut is the finished video ("just the cut"): Approve says so and the page waits for no edit."""
    update(folder / "review.json", lambda d: d.update(final=True))
    print("the cut is final: no styled edit follows")


def cmd_status(a, folder):
    """One step of what Claude is doing for the next round. Each new step closes the one before."""
    stopped = load(folder / "review.json").get("stopped")
    if stopped:
        print(f"STOPPED: the user pressed Stop on the page at {stopped}. Stop this round's work, say so in one "
              f"line, and run `wait` again: the notes are open for more.")
        sys.exit(3)
    key = getattr(a, "step", None)
    if not (a.text or key or a.done):
        sys.exit("status takes a text, --step or --done")
    update(folder / "review.json", lambda d: add_step(d, a.text, a.log, key, a.done))
    print("done" if a.done else f"status: {a.text or EDIT_STEPS[key]}")


def cmd_alt(a, folder):
    """Add (or replace) another video in this round: the other aspect, another clip."""
    def f(d):
        d["alts"] = [x for x in d.get("alts") or [] if x["name"] != a.name] + [
            {"name": a.name, "video": keep(folder, a.video, d["stage"], d["round"], a.name),
             "transcript": str(Path(a.transcript).resolve()) if a.transcript else None}]
        log_round(d)
        return d
    d = update(folder / "review.json", f)
    print(f"round {d['round']}: {1 + len(d['alts'])} videos "
          f"({', '.join([d.get('label') or 'main'] + [x['name'] for x in d['alts']])})")


def cmd_wait(folder, poll=1.0):
    rj = folder / "review.json"
    beat = folder / "review" / ".listening"
    try:
        while True:
            beat.touch()
            # read only: a write here could drop a note the page saves in the same instant
            d0 = load(rj)
            if any(not e["handled"] for e in d0["events"]) or (d0.get("learn") or {}).get("handled") is False:
                break
            time.sleep(poll)
    finally:
        beat.unlink(missing_ok=True)

    (folder / "review" / ".claude").touch()   # Claude has the send or approve now: working, not gone

    def take(d):
        ev = [e for e in d["events"] if not e["handled"]]
        for e in ev:
            e["handled"] = True
        if not ev:   # the user answered the learn panel
            d["learn"]["handled"] = True
        return ev, d
    ev, d = update(rj, take)
    if not ev:
        return learn_chosen(folder, d)
    e = ev[-1]
    r = next((r for r in d.get("rounds", []) if (r["stage"], r["round"]) == (e["stage"], e["round"])), d)
    cs = sorted((c for c in d["comments"] if c["stage"] == e["stage"] and c["round"] == e["round"]), key=lambda c: c["t"])
    for x in ev:   # an Apply then a send: the edits still have to be built
        if x["type"] == "edits":
            print(edits_text(folder, x))
    print(f"{'SEND' if e['type'] == 'edits' else e['type'].upper()}: {d['name']} {stage_name(e['stage'])}, round {e['round']}, "
          f"{len(cs)} note{'' if len(cs) == 1 else 's'}. video {r['video']}")
    for x in r.get("alts") or []:
        print(f"  also {x['name']}: {x['video']}")
    for c in cs:
        span = fmt(c["t"]) + (f"-{fmt(c['t_end'])}" if c["t_end"] is not None else "")
        ver = " [all videos]" if c.get("alt") == "*" else f" [{c['alt']}]" if c.get("alt") else (
            f" [{r.get('label') or 'main'}]" if r.get("alts") else "")
        k = c.get("card")
        card = f" [card {k['i']} {k['kind']} '{k['trigger_word'] or ''}']" if k else ""
        print(f"#{c['id']} {span} ({c['t']}s){ver}{card}: {c['text']}\n    said: \"{c['words']}\""
              f"\n    frame: {folder / 'review' / c['frame']}"
              + "".join(f"\n    image: {folder / 'review' / x}" for x in c.get("images") or []))
    if e["type"] == "approve" and d.get("learn"):
        print(learn_prompt(folder, d))
    elif cs:
        print(learn_hint(folder))
    return e["type"]


def edits_text(folder, e):
    """What `wait` prints for the transcript editor's Apply: the line Claude acts on, the words, the commands."""
    try:
        words = [w for w in load(folder / "words.raw.json") if w.get("type", "word") == "word" and "start" in w]
    except (OSError, ValueError):
        words = []
    said = lambda a, b: " ".join(w["text"].strip() for w in words if a - 1e-3 <= (w["start"] + w["end"]) / 2 <= b + 1e-3
                                 or w["start"] <= a and b <= w["end"])   # apply_edits' rule
    try:
        src = load(folder / "report.json")["source"]
    except (OSError, ValueError, KeyError):
        src = "<source>"
    cut = Path(__file__).resolve().parents[2] / "skills" / "cut" / "scripts"
    rows = [f"EDITS {e['n']} transcript edit{'' if e['n'] == 1 else 's'} applied to spans.json: rebuild, render, verify, round",
            f"  {e['counts']['restored']} word(s) restored, {e['counts']['cut']} cut (kind manual); the spans before are in {folder / 'spans.prev.json'}"]
    rows += [f"  restore {a:.2f}-{b:.2f}s: \"{said(a, b)}\"" for a, b in e["restore"]]
    rows += [f"  cut {a:.2f}-{b:.2f}s: \"{said(a, b)}\"" for a, b in e["cut"]]
    rows += [f"  pause back {a:.2f}-{b:.2f}s" for a, b in e.get("pause") or []]
    if e["counts"].get("keep"):
        rows.append(f"  keep.json: {e['counts']['keep']} range(s) the pause trim leaves in (build_timeline.py reads it)")
    q = (lambda x: subprocess.list2cmdline([str(x)])) if os.name == "nt" else (lambda x: shlex.quote(str(x)))
    rows.append(f"  then: python3 {q(cut / 'build_timeline.py')} {q(src)} {q(folder)}; python3 {q(cut / 'render.py')} {q(src)} {q(folder)}; "
                f"verify_cut.py; python3 {q(Path(__file__).resolve())} {q(folder)} round --video {q(folder / 'cut.mp4')}")
    return "\n".join(rows)


def session_notes(d):
    return [c for c in d["comments"] if c.get("text") and not c.get("queued")]


def style_label(folder):
    """What the learn panel calls the style the edit used: its name on the home page, else My style."""
    try:
        from ai_editor import styleskill
        return styleskill.display(edit_style(folder))
    except Exception:
        return "My style"


def learn_prompt(folder, d):
    """The final approve: Claude turns the whole session's notes into rules the page offers with checkboxes."""
    if d["learn"]["state"] == "none":
        return "LEARN: no notes this session, so nothing to learn. The page says so; say nothing about it."
    st = d["learn"]["style"]
    lines = [f"LEARN: the video is approved. Turn this session's notes and your replies (below) into at most 6 "
             f"one-line rules for how {st} edits next time, e.g. \"Captions one size smaller\", \"Fewer zooms in the "
             f"first 5 s\". Leave out one-off fixes (a word cut at 0:12) and notes already saved as rules. Post them "
             f"([] when none is about the style); the page shows them with checkboxes:",
             f"  python3 {Path(__file__).resolve()} {folder} learn --propose "
             f"'[{{\"rule\": \"...\", \"section\": \"cut|captions|visuals|sound|layout\", \"set\": \"captions.size_pct=4.5\"}}]'",
             "  (or a path to a .json file holding that list). \"set\" only when a taste setting maps (the taste "
             "skill's table). Then run `wait` again: it returns with the rules the user ticked."]
    for c in sorted(session_notes(d), key=lambda c: (c["stage"] != "cut", c["round"], c["t"])):
        saved = f" [saved as rule {c['taste']}]" if isinstance(c.get("taste"), int) else ""
        lines.append(f"#{c['id']} {stage_name(c['stage'])} r{c['round']}: {c['text']}"
                     + (f" -> reply: {c['reply']}" if c.get("reply") else "") + saved)
    return "\n".join(lines)


def learn_chosen(folder, d):
    lr = d["learn"]
    if lr["state"] == "skipped":
        print("LEARN: the user pressed Not now. Nothing is saved; say nothing more about it.")
        return "learn"
    rules = [lr["rules"][i] for i in lr["chosen"]]
    print(f"LEARN: the user ticked {len(rules)} rule{'s' if len(rules) != 1 else ''} for {lr['style']}:")
    for r in rules:
        print(f"  - [{r['section']}] {r['rule']}" + (f" ({r['set']})" if r.get("set") else ""))
    print(f"Save them (styleskill.learn: the style's overrides, rules and skill) and tell the page:\n"
          f"  python3 {Path(__file__).resolve()} {folder} learn --done")
    return "learn"


SECTIONS = ("cut", "captions", "visuals", "sound", "layout")


def learn_propose(folder, raw):
    """Claude's candidate rules onto the page's learn panel."""
    try:
        f = Path(raw)
        rules = json.loads(f.read_text(encoding="utf-8") if raw.endswith(".json") and f.is_file() else raw)
    except (OSError, ValueError) as e:
        sys.exit(f"--propose takes a JSON list: {e}")
    if not isinstance(rules, list):
        sys.exit("--propose takes a JSON list")
    out = []
    for r in rules[:8]:
        if not isinstance(r, dict) or not str(r.get("rule", "")).strip():
            sys.exit(f"each rule needs a \"rule\": {r}")
        sec = str(r.get("section", "")).lower()
        if sec not in SECTIONS:
            sys.exit(f"section must be one of {', '.join(SECTIONS)}: {r}")
        st = r.get("set") or None
        if st is not None and (not isinstance(st, str) or "=" not in st):
            sys.exit(f"\"set\" is key.path=value: {r}")
        out.append({"rule": str(r["rule"]).strip().rstrip("."), "section": sec, "set": st})

    def f(d):
        if not d.get("learn"):
            sys.exit("learn --propose comes after the final approve")
        d["learn"].update(state="proposed" if out else "empty", rules=out, chosen=[], handled=None)
    update(folder / "review.json", f)
    print(f"{len(out)} rule{'s' if len(out) != 1 else ''} on the page" + ("; run `wait` for the user's pick" if out else ""))


def learn_done(folder, summary):
    """Save the ticked rules into the edit's style (one creator style, else My style) and show it on the page."""
    from ai_editor import styleskill, taste
    lr = load(folder / "review.json").get("learn") or {}
    if lr.get("state") != "chosen":
        sys.exit("nothing ticked to save: run `wait` for the user's pick first")
    sp = edit_style(folder)
    for r in (lr["rules"][i] for i in lr["chosen"]):
        setting, _, raw = (r.get("set") or "").partition("=")
        status, x = styleskill.learn(sp, r["section"], r["rule"], setting or None, taste.parse(raw) if setting else None,
                                     edit=folder, source="review")
        print(f"{status} rule #{x['id']} {x['section']}: {x['rule']} (skill {styleskill.name(sp)})")
    style = styleskill.display(sp)
    update(folder / "review.json", lambda d: d["learn"].update(state="done", style=style,
                                                                 summary=summary or f"Saved to {style}. Next video uses it."))
    print(f"saved to {style}")


def edit_style(folder):
    """The one creator style.json this edit used (styleskill.sources), else None."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ai_editor import styleskill
    src = styleskill.sources(folder)
    return src[0] if len(src) == 1 else None


def learn_hint(folder):
    sp = edit_style(folder)
    scopes = "this-style|style|video|everyone" if sp else "style|video|everyone"
    return (f"LEARN: pick the notes that are preferences, ask the scope once for all of them"
            + (f" (this edit used @{sp.parent.name}'s style: \"This style\" first)" if sp else "") + ", then run:\n"
            f"  python3 {Path(__file__).resolve()} {folder} learn --scope {scopes}"
            f" --rule <id> <section> \"<rule>\" [key=value] ...   (no preferences: learn --none)")


def learned_round(d):
    """The round `wait` last handed over: the one `learn` acts on."""
    e = next((e for e in reversed(d["events"]) if e["handled"] and e["type"] != "stop"), None)
    return (e["stage"], e["round"]) if e else (d["stage"], d["round"])


def cmd_learn(a, folder):
    """Review notes into the user's taste: each --rule note saved with taste.add (scope asked once for the
    batch), every other note of the round marked one-off, and each note records what happened to it."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    if getattr(a, "propose", None) is not None:
        return learn_propose(folder, a.propose)
    if getattr(a, "done", None) is not None:
        return learn_done(folder, a.done)
    from ai_editor import taste
    rules = {}
    for r in a.rule or []:
        if len(r) not in (3, 4) or not r[0].isdigit() or (len(r) == 4 and "=" not in r[3]):
            sys.exit(f"--rule takes <id> <section> \"<rule>\" [key=value], got {r}")
        rules[int(r[0])] = r[1:]
    if not rules and not a.none:
        sys.exit("give --rule for each preference note, or --none")
    if rules and not a.scope:
        sys.exit("--scope this-style|style|video|everyone: ask the user once for the batch")
    from ai_editor import styleskill
    sp = None
    if a.scope == "this-style":
        src = styleskill.sources(getattr(a, "style", None) or folder)
        if len(src) != 1:
            sys.exit("--scope this-style: " + ("this edit used no creator style" if not src else "this edit blends several creators")
                     + "; pass --style creator-teardowns/<handle>/style.json, or use --scope style")
        sp = src[0]

    def f(d):
        stage, rnd = learned_round(d)
        known = {c["id"]: c for c in d["comments"]}
        if set(rules) - set(known):
            sys.exit(f"no note {', '.join(map(str, sorted(set(rules) - set(known))))}")
        out = []
        for c in d["comments"]:
            if c["id"] in rules:
                section, rule, *kv = rules[c["id"]]
                setting, _, raw = kv[0].partition("=") if kv else (None, "", "")
                value = taste.parse(raw) if setting else None
                scope = f"video:{d['name']}" if a.scope == "video" else styleskill.scope(sp)
                status, r = taste.add(section, rule, setting, value, scope, folder, source="review", style=sp)
                c.update(rule=rule, taste={"style": r["id"], "this-style": r["id"], "video": "video-only"}.get(a.scope, "suggested"))
                if a.scope == "everyone":
                    taste.suggest(c["text"], rule, f"review note ({r['section']})", note=f"{d['name']}#{c['id']}")
                out.append(f"#{c['id']} -> {status} rule #{r['id']} {r['section']}: {rule}"
                           + {"video": " (this video only)", "everyone": " (+ suggested to everyone)",
                              "this-style": f" (this style, skill {styleskill.name(sp)})",
                              "style": " (every video, skill my-style)"}[a.scope])
            elif (c["stage"], c["round"]) == (stage, rnd) and "taste" not in c:
                c["taste"] = "one-off"
        return out
    for line in update(folder / "review.json", f) or ["no preference notes: all marked one-off"]:
        print(line)


def _post_code(url, body):
    """The HTTP status of a POST (the demo's concurrency and refusal checks)."""
    try:
        return urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), method="POST")).status
    except urllib.error.HTTPError as err:
        return err.code


def demo():
    import contextlib
    import io
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="review_demo_"))
    os.environ["AI_EDITOR_HOME"] = str(tmp / "home")   # start records the folder in the library index there
    vid = tmp / "v.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=6",
                    "-f", "lavfi", "-i", "sine=duration=6", "-shortest", "-pix_fmt", "yuv420p", str(vid)], check=True)
    tr = tmp / "words.json"
    tr.write_text(json.dumps([{"text": "hello", "start": 1.0, "end": 1.4, "type": "word"},
                              {"text": " ", "start": 1.4, "end": 1.5, "type": "spacing"},
                              {"text": "world", "start": 1.5, "end": 2.0, "type": "word"},
                              {"text": "later", "start": 5.0, "end": 5.5, "type": "word"}]), encoding="utf-8")
    pl = tmp / "plan.json"
    pl.write_text(json.dumps({"cards": [{"src": "images/a.png", "start": 3.5, "end": 5.5, "trigger_word": "later"}]}), encoding="utf-8")
    folder = tmp / "edits" / "demo"
    ns = argparse.Namespace
    cmd_start(ns(video=str(vid), stage="cut", transcript=str(tr), plan=None, note=None, name=None), folder)
    assert str(folder.resolve()) in load(tmp / "home" / "library.json")["edits"], "start lists the edit in the library"
    end = tmp / "end.jpg"   # the playhead on the last frame: -ss there writes nothing, so grab seeks from the end
    grab(vid, duration(vid), end)
    assert end.exists() and end.stat().st_size > 1000, "no still at the last frame"
    assert "Move to playhead" not in HTML.read_text(encoding="utf-8") and 'id="homebtn"' in HTML.read_text(encoding="utf-8")
    assert load(folder / "review.json")["vertical"] is False
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-review"
    srv, url = serve(folder, 0, open_browser=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, tok = url.split("/?t=")

    def get(p, **h):
        return urllib.request.urlopen(urllib.request.Request(base + p + ("&" if "?" in p else "?") + "t=" + tok, headers=h))

    def post(p, b):
        return json.loads(urllib.request.urlopen(urllib.request.Request(
            base + p + "?t=" + tok, json.dumps(b).encode(), method="POST")).read())

    def waited():
        o = io.StringIO()
        with contextlib.redirect_stdout(o):
            cmd_wait(folder, poll=0.01)
        return o.getvalue()

    # no token: nothing reads or writes
    for p, data in (("/", None), ("/state", None), ("/video", None), ("/event", b'{"type":"send"}')):
        try:
            urllib.request.urlopen(urllib.request.Request(base + p, data, method="POST" if data else "GET"))
            raise AssertionError(f"{p} answered without the token")
        except urllib.error.HTTPError as e:
            assert e.code == 403, (p, e.code)
    assert not load(folder / "review.json")["events"]
    assert running(folder) == url and serve(folder, 0, open_browser=False) == (None, url)
    # a tab opens only when no home window is showing; with one, the home window goes there itself
    global OPEN
    opened, OPEN = [], (lambda u: opened.append(u))
    serve(folder, 0)
    assert opened == [url], opened
    hp = askhook.pages() / "1"
    hp.write_text(json.dumps({"url": "http://127.0.0.1:1/?t=x", "kind": "home", "session": ""}), encoding="utf-8")
    serve(folder, 0)
    assert opened == [url] * 2, "a home window busy with something else: this page still gets its tab"
    sf = tmp / "home" / "home" / "state.json"
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(json.dumps({"jobs": [{"type": "video", "state": "taken", "args": {"edit": str(folder)}}]}), encoding="utf-8")
    serve(folder, 0)
    assert opened == [url] * 2, "the home window holds this edit's job: it goes there itself, no second tab"
    hp.unlink()
    sf.unlink()
    OPEN = webbrowser.open
    r = get("/")
    assert b"review" in r.read() and "HttpOnly" in r.headers["Set-Cookie"]
    # Claude's question (askhook.py): posted, polled by the showing page, answered in its own words, cleared
    assert '<script src="/ask.js">' in HTML.read_text(encoding="utf-8") and b"askq" in get("/ask.js").read()
    qs = [{"question": "Keep the intro?", "header": "Intro", "options": [{"label": "Keep", "description": ""}], "multiSelect": False}]
    try:
        post("/ask", {"id": "q0", "questions": qs, "session": "sess-other"})
        raise AssertionError("took another session's question")
    except urllib.error.HTTPError as e:
        assert e.code == 409
    try:   # a POST from another site's page
        urllib.request.urlopen(urllib.request.Request(base + "/theme?t=" + tok, b'{"theme":"dark"}', {"Origin": "http://evil.example"}, method="POST"))
        raise AssertionError("took a cross-site POST")
    except urllib.error.HTTPError as e:
        assert e.code == 403
    os.environ.pop("CLAUDE_CODE_SESSION_ID")
    (folder / "review" / ".session").unlink(missing_ok=True)
    assert asker(folder, "")("sess-anyone") == "demo", "no session known (older Claude Code): any question"
    assert asker(folder, "sess-review")("sess-anyone") is None
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-review"
    post("/ask", {"id": "q1", "questions": qs, "session": "sess-review"})
    assert json.loads(get("/ask?v=1").read())["ask"] == {"id": "q1", "questions": qs, "about": "demo"} and askhook.live() == [url]
    post("/ask/answer", {"id": "q1", "answers": ["Cut it to 3 seconds"]})
    assert json.loads(get("/ask?id=q1").read())["result"] == {"answers": ["Cut it to 3 seconds"]}
    assert json.loads(get("/ask").read())["ask"] is None
    post("/ask/clear", {"id": "q1"})
    assert not srv.asks
    r = get("/video?k=cut-1", Range="bytes=10-19")
    assert r.status == 206 and len(r.read()) == 10 and r.headers["Content-Range"].endswith(f"/{vid.stat().st_size}")
    # a note: its words, its frame still; an attached image (a non-image is dropped); delete
    c = post("/comment", {"t": 1.2, "t_end": None, "text": "cut the hello"})
    assert c["words"] == "hello world" and c["card"] is None and (folder / "review" / c["frame"]).stat().st_size > 0
    png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJ"
           "RU5ErkJggg==")
    # edit in place, same id: the text; an image added then removed; moved to the playhead and back
    e = post("/edit", {"id": c["id"], "text": "cut the hello, please"})
    assert e["id"] == c["id"] and e["text"] == "cut the hello, please" and e["edited"] and e["t"] == 1.2, e
    e = post("/edit", {"id": c["id"], "images": [png]})
    assert e["images"] == [f"rcut1-c{c['id']}-img1.png"] and e["text"] == "cut the hello, please", e
    assert post("/edit", {"id": c["id"], "images": []})["images"] == []
    e = post("/edit", {"id": c["id"], "t": 5.1})
    assert e["t"] == 5.1 and e["words"] == "later" and e["frame"] != c["frame"] and (folder / "review" / e["frame"]).stat().st_size > 0, e
    c = post("/edit", {"id": c["id"], "t": 1.2})
    assert c["words"] == "hello world" and [x["text"] for x in load(folder / "review.json")["comments"]] == ["cut the hello, please"]
    for bad in ({"id": c["id"], "text": ""}, {"id": 999, "text": "x"}):
        try:
            post("/edit", bad)
            raise AssertionError(f"edit {bad}")
        except urllib.error.HTTPError as err:
            assert err.code in (400, 404), err.code
    ci = post("/comment", {"t": 1.5, "text": "", "images": [png, "data:text/html;base64,PGI+"]})
    assert ci["images"] == [f"rcut1-c{ci['id']}-img1.png"]
    assert get("/frames/" + ci["images"][0]).headers["Content-Type"] == "image/png"
    post("/delete", {"id": ci["id"]})
    assert [x["id"] for x in load(folder / "review.json")["comments"]] == [c["id"]]
    assert not json.loads(get("/state").read())["listening"]
    # the first-edit notice: round 1 of the user's first videos, until dismissed; the theme keeps the count
    assert json.loads(get("/state").read())["intro"], "round 1 of the first video: the notice shows"
    set_theme("dark")
    assert ui()["videos"] == [str(folder)] and theme() == "dark"
    set_theme("system")
    post("/intro", {})
    assert not json.loads(get("/state").read())["intro"] and ui()["intro_off"]
    set_ui(intro_off=False)
    for i in range(4):
        seen(tmp / f"v{i}")
    assert len(ui()["videos"]) == INTRO_VIDEOS and intro(tmp / "v0", {"round": 1}), ui()
    assert not intro(tmp / "v2", {"round": 1}) and not intro(tmp / "v0", {"round": 2}), "after 3 videos, and after round 1: never"
    assert json.loads(get("/state").read())["home"] is None   # no home page running: the button says how to start it
    hs = ThreadingHTTPServer(("127.0.0.1", 0), type("H", (BaseHTTPRequestHandler,), {
        "do_GET": lambda self: (self.send_response(200), self.end_headers(), self.wfile.write(b"{}")), "log_message": lambda *a: None}))
    threading.Thread(target=hs.serve_forever, daemon=True).start()
    hurl = f"http://127.0.0.1:{hs.server_address[1]}/?t=abc"
    (tmp / "home" / "home").mkdir(parents=True, exist_ok=True)
    (tmp / "home" / "home" / ".server").write_text(hurl, encoding="utf-8")
    _HOME["at"] = 0
    assert json.loads(get("/state").read())["home"] == hurl, "the Home button reads home.py's fixed address"
    hs.shutdown()
    hs.server_close()
    # the cut's transcript: kept words on the cut's timeline, the cut word struck, the removed pause a chip
    (folder / "words.raw.json").write_text(json.dumps([
        {"text": "hello", "start": 0.0, "end": 0.4, "type": "word"}, {"text": "um,", "start": 0.5, "end": 0.8, "type": "word"},
        {"text": "world.", "start": 2.0, "end": 2.5, "type": "word"}, {"text": "later", "start": 3.0, "end": 9.0, "type": "word"}]), encoding="utf-8")
    (folder / "decisions.json").write_text(json.dumps([{"start": 0.0, "end": 0.45}, {"start": 1.95, "end": 7.5}]), encoding="utf-8")
    tx = cut_transcript(folder, 6.0)
    toks = [x for ln in tx["lines"] for x in ln["tokens"]]
    assert toks[1]["k"] == "pause"   # cut with no spans.json entry: the pause trim took it
    assert [{k: x[k] for k in x if k not in ("s", "e", "k")} for x in toks] == [{"w": "hello", "t": 0.0}, {"w": "um,", "t": None}, {"p": 1.1},
                                                                          {"w": "world.", "t": 0.5}, {"w": "later", "t": 1.5}], toks
    assert toks[1]["s"] == 0.5 and toks[1]["e"] == 0.8 and tx["kept"] == [[0.0, 0.45], [1.95, 7.5]], tx
    assert [ln["t"] for ln in tx["lines"]] == [0.0, 1.5] and tx["points"] == [0.45] and tx["cuts"] == 2, tx
    assert cut_transcript(folder, 30.0) is None   # decisions that do not add up to this video: no transcript
    round_data(folder, vid, "cut", 1, None)
    assert json.loads(get("/data?k=cut-1").read())["transcript"]["cuts"] == 2
    assert json.loads(get("/data?k=nope").read()) == {}
    # stop does nothing until a send; after a send it opens the notes again and `status` tells Claude to stop
    post("/event", {"type": "stop"})
    assert not load(folder / "review.json")["sent"]
    post("/event", {"type": "send"})
    cq = post("/comment", {"t": 2.0, "text": "queued then stopped"})   # with Claude: queued for the next round
    assert cq["queued"] and cq["round"] == 2, cq
    post("/event", {"type": "stop"})
    d = load(folder / "review.json")
    assert not d["sent"] and d["stopped"] and d["events"][-1]["type"] == "stop" and d["events"][-1]["handled"], d["events"]
    q = next(x for x in d["comments"] if x["id"] == cq["id"])
    assert q["round"] == 1 and "queued" not in q, q   # stop: the queued note joins the reopened round
    post("/delete", {"id": cq["id"]})
    d = load(folder / "review.json")
    try:
        with contextlib.redirect_stdout(io.StringIO()) as o:
            cmd_status(ns(text="rendering", log=None, done=False), folder)
        raise AssertionError("status after stop")
    except SystemExit as e:
        assert e.code == 3 and "STOPPED" in o.getvalue()
    d["events"] = []
    save(folder / "review.json", d)
    # send: wait prints the note and its still; the page says the round is with Claude
    post("/event", {"type": "send"})
    out = waited()
    assert out.startswith("SEND: demo the cut, round 1, 1 note.") and "#1 0:01.2" in out and 'said: "hello world"' in out, out
    assert str(folder / "review" / c["frame"]) in out and not (folder / "review" / ".listening").exists()
    assert "): cut the hello, please" in out, out   # wait hands over the final text
    try:   # Claude has this round: its notes are read-only
        post("/edit", {"id": c["id"], "text": "too late"})
        raise AssertionError("edit after pick-up")
    except urllib.error.HTTPError as err:
        assert err.code == 409
    # a note while Claude works: queued for the next round, deletable, never in what `wait` hands over
    cx = post("/comment", {"t": 5.2, "text": "delete me"})
    post("/delete", {"id": cx["id"]})
    cn = post("/comment", {"t": 1.2, "text": "and the pause"})
    assert cn["queued"] and cn["round"] == 2 and cn["words"] == "hello world", cn
    cn = post("/edit", {"id": cn["id"], "text": "and the pause after it"})   # a queued note stays editable, and queued
    assert next(x for x in load(folder / "review.json")["comments"] if x["id"] == cn["id"])["queued"]
    assert [x["id"] for x in load(folder / "review.json")["comments"]] == [c["id"], cn["id"]]
    assert all(e["handled"] for e in load(folder / "review.json")["events"])   # nothing for `wait` to return on
    assert "LEARN:" in out.splitlines()[-2] and f"{folder} learn --scope style|video|everyone" in out, out
    # round 2 on the styled edit: the card under a note comes with it, Claude's answer shows.
    # The cut's note was never learned: round says so, then learn --none marks it one-off
    o = io.StringIO()
    with contextlib.redirect_stdout(o):
        cmd_round(ns(video=str(vid), stage="edit", transcript=None, plan=str(pl), note=None, name=None), folder)
    assert "notes not learned yet: 1." in o.getvalue(), o.getvalue()
    # the queued note is an open note of the new round, its words and a new frame still with it
    n = next(x for x in load(folder / "review.json")["comments"] if x["id"] == cn["id"])
    assert (n["stage"], n["round"], n["status"], n["words"]) == ("edit", 1, "open", "hello world") and "queued" not in n, n
    assert n["frame"] == f"redit1-c{cn['id']}.jpg" and (folder / "review" / n["frame"]).stat().st_size > 0, n
    # after a re-cut, a queued note moves to where its words are said (the word "world" moved 0.5 s later)
    otx = {"lines": [{"tokens": [{"w": "hello", "t": 0.0}, {"w": "um,", "t": 0.4}, {"w": "world", "t": 0.8}]}]}
    ntx = {"lines": [{"tokens": [{"w": "hello", "t": 0.0}, {"p": 0.3}, {"w": "um,", "t": None}, {"w": "world", "t": 0.4}]}]}
    assert remap(1.0, otx, ntx) == 0.6 and remap(0.5, otx, ntx) == 0.4 and remap(1.0, otx, None) is None
    learn = lambda **k: cmd_learn(ns(**{"scope": None, "rule": None, "none": False, "style": None, **k}), folder)
    with contextlib.redirect_stdout(io.StringIO()):
        learn(none=True)
    assert load(folder / "review.json")["comments"][0]["taste"] == "one-off"
    cmd_resolve(ns(id=1, status="fixed", reply="cut it", new_t=0.9), folder)
    c2 = post("/comment", {"t": 4.0, "t_end": 5.0, "text": "card smaller"})
    assert c2["card"]["trigger_word"] == "later" and c2["stage"] == "edit" and c2["round"] == 1, c2
    s = json.loads(get("/state").read())
    assert s["comments"][0]["status"] == "fixed" and not s["sent"] and [r["stage"] for r in s["rounds"]] == ["cut", "edit"]
    assert not s.get("stopped") and all(r["at"] for r in s["rounds"])
    trk = json.loads(get("/data?k=edit-1").read())
    assert trk["tracks"] == {"cards": [[3.5, 5.5]], "zooms": [], "captions": []} and "transcript" not in trk, trk
    # another video in the round: its own notes, notes on every video, scope switched and back
    cmd_alt(ns(name="wide", video=str(vid), transcript=str(tr)), folder)
    assert get("/video?k=edit-1&alt=wide").status == 200
    c3 = post("/comment", {"t": 5.1, "text": "louder here", "alt": "wide"})
    assert c3["alt"] == "wide" and c3["words"] == "later", c3
    c4 = post("/comment", {"t": 1.0, "text": "on all of them", "alt": "*"})
    post("/scope", {"id": c3["id"], "alt": "*"})
    assert next(x for x in load(folder / "review.json")["comments"] if x["id"] == c3["id"])["alt"] == "*"
    post("/scope", {"id": c3["id"], "alt": "wide"})
    assert post("/edit", {"id": c3["id"], "alt": "*"})["alt"] == "*" and post("/edit", {"id": c3["id"], "alt": "wide"})["alt"] == "wide"
    # live progress from a render log
    lg = tmp / "r.log"
    lg.write_text("Rendered 10/40\nRendered 30/40\n", encoding="utf-8")
    cmd_status(ns(text="rendering", log=str(lg), done=False), folder)
    st = json.loads(get("/state").read())["activity"][-1]
    assert st["text"] == "rendering" and st["pct"] == 75 and not st["done"], st
    post("/event", {"type": "approve"})
    out = waited()
    assert out.startswith("APPROVE: demo the edit, round 1, 4 notes.") and f"also wide: {folder / 'review' / 'v-edit1-wide.mp4'}" in out, out
    assert f"#{c4['id']} 0:01.0 (1.0s) [all videos]: on all of them" in out, out
    assert f"#{c3['id']} 0:05.1 (5.1s) [wide] [card 0 image 'later']: louder here" in out, out
    # the final approve: LEARN asks Claude for rules from every note of the session, the page waits for them
    assert "LEARN: the video is approved" in out and "#1 the cut r1: cut the hello, please -> reply: cut it" in out, out
    assert json.loads(get("/state").read())["learn"] == {"state": "reading", "style": "My style"}
    try:
        post("/comment", {"t": 2, "text": "after approve"})
        raise AssertionError("a note after approve")
    except urllib.error.HTTPError as e:
        assert e.code == 409
    # learn: the preference notes go to taste (scope asked once a batch), every note records what happened
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ai_editor import taste
    taste.HOME = tmp / "home"
    old_skills = os.environ.get("AI_EDITOR_SKILLS")
    os.environ["AI_EDITOR_SKILLS"] = str(tmp / "skills")   # never the user's real skills folder
    o = io.StringIO()
    with contextlib.redirect_stdout(o):
        learn(scope="style", rule=[[str(c2["id"]), "visuals", "Cards fill the space they have"]])
        learn(scope="video", rule=[[str(c3["id"]), "sound", "Sound louder on this one", "sfx_db=-6"]])
        learn(scope="everyone", rule=[[str(c4["id"]), "captions", "Captions stay inside the safe zone"]])
    cs = {c["id"]: c for c in load(folder / "review.json")["comments"]}
    rid = next(r["id"] for r in taste.load_rules() if r["rule"] == "Cards fill the space they have")
    assert cs[c2["id"]]["taste"] == rid and cs[c3["id"]]["taste"] == "video-only", cs
    assert cs[c4["id"]]["taste"] == "suggested" and cs[c4["id"]]["rule"] == "Captions stay inside the safe zone", cs
    assert "Cards fill the space they have" in taste.load_md()["Visuals"] and "sfx_db" not in taste.read_json()
    sug = [json.loads(x) for x in (tmp / "home" / "suggestions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sug[-1]["note"] == taste.note_key(f"demo#{c4['id']}") and sug[-1]["what"] == "on all of them", sug
    assert "from a review note" in taste.report() and "3 came from review-page notes" in taste.report(), taste.report()
    # this-style: the edit used a creator's style, so LEARN offers it first; the setting goes to that style's
    # overrides and its skill, not to taste.json; without a creator style it refuses
    try:
        learn(scope="this-style", rule=[[str(c2["id"]), "captions", "Captions too big", "captions.size_pct=4.5"]])
        raise AssertionError("this-style with no creator style")
    except SystemExit as e:
        assert "no creator style" in str(e), e
    sp = tmp / "creator-teardowns" / "alice" / "style.json"
    sp.parent.mkdir(parents=True)
    sp.write_text('{"handle": "alice"}', encoding="utf-8")
    (folder / "style.json").write_text(json.dumps({"handle": "alice", "source": [str(sp)]}), encoding="utf-8")
    assert "learn --scope this-style|style|video|everyone" in learn_hint(folder) and "@alice" in learn_hint(folder)
    with contextlib.redirect_stdout(o):
        learn(scope="this-style", rule=[[str(c2["id"]), "captions", "Captions too big", "captions.size_pct=4.5"]])
    assert "(this style, skill edit-like-alice)" in o.getvalue(), o.getvalue()
    r = next(r for r in taste.load_rules() if r["rule"] == "Captions too big")
    assert r["scope"] == "style:alice" and next(c for c in load(folder / "review.json")["comments"] if c["id"] == c2["id"])["taste"] == r["id"]
    assert json.loads((sp.parent / "style.overrides.json").read_text(encoding="utf-8")) == {"captions": {"size_pct": 4.5}}
    assert "size_pct" not in taste.read_json().get("captions", {})
    assert "- Captions too big." in (tmp / "skills" / "edit-like-alice" / "SKILL.md").read_text(encoding="utf-8")
    assert "Cards fill the space they have" in (tmp / "skills" / "my-style" / "SKILL.md").read_text(encoding="utf-8")
    # propose -> the page ticks two of three -> wait prints them -> done saves them into the edit's style
    props = [{"rule": "Captions one size smaller.", "section": "captions", "set": "captions.size_pct=4"},
             {"rule": "Fewer zooms in the first 5 s", "section": "visuals"}, {"rule": "Keep the pauses short", "section": "Cut"}]
    for bad in ("not json", '[{"rule": "x", "section": "colour"}]', '[{"rule": "x", "section": "cut", "set": "size"}]'):
        try:
            learn(propose=bad)
            raise AssertionError(f"propose {bad}")
        except SystemExit as e:
            assert e.code, bad
    with contextlib.redirect_stdout(io.StringIO()):
        learn(propose=json.dumps(props))
    lr = json.loads(get("/state").read())["learn"]
    assert lr["state"] == "proposed" and [r["rule"] for r in lr["rules"]][0] == "Captions one size smaller", lr
    try:
        learn(done="")
        raise AssertionError("done before the user ticked")
    except SystemExit as e:
        assert "nothing ticked" in str(e)
    post("/learn", {"chosen": [0, 2, 9]})
    out = waited()
    assert out.startswith("LEARN: the user ticked 2 rules for My style:") and "Captions one size smaller (captions.size_pct=4)" in out, out
    assert "Fewer zooms" not in out and "learn --done" in out, out
    with contextlib.redirect_stdout(io.StringIO()):
        learn(done="")
    lr = load(folder / "review.json")["learn"]
    assert lr["state"] == "done" and lr["summary"] == "Saved to @alice. Next video uses it.", lr
    sk = (tmp / "skills" / "edit-like-alice" / "SKILL.md").read_text(encoding="utf-8")
    assert "- Keep the pauses short." in sk and "Fewer zooms" not in sk, sk
    assert json.loads((sp.parent / "style.overrides.json").read_text(encoding="utf-8"))["captions"]["size_pct"] == 4
    try:
        post("/learn", {"chosen": [1]})
        raise AssertionError("a second pick")
    except urllib.error.HTTPError as e:
        assert e.code == 409
    if old_skills is None:
        os.environ.pop("AI_EDITOR_SKILLS", None)
    else:
        os.environ["AI_EDITOR_SKILLS"] = old_skills
    # every round replays its own video: the next render overwriting the same file changes no earlier tab
    rs = load(folder / "review.json")["rounds"]
    assert [Path(r["video"]).name for r in rs] == ["v-cut1.mp4", "v-edit1.mp4"], rs
    size = vid.stat().st_size
    vid.write_bytes(b"the next render")
    assert len(get("/video?k=cut-1").read()) == size and len(get("/video?k=edit-1&alt=wide").read()) == size
    # the footer after a pick-up: an approve says approved, a send says Claude is working (node runs the page's own function)
    js = re.search(r"^function listenText\(S\)\{.*?\n\}", HTML.read_text(encoding="utf-8"), re.M | re.S)
    assert js, "review.html has no listenText()"
    if shutil.which("node"):
        ev = lambda t, n: {"listening": False, "sent": True, "round": n, "comments": [],
                           "events": [{"type": t, "stage": "edit", "handled": True}]}
        mid = {"listening": False, "busy": True, "sent": False, "round": 1, "stage": "edit", "comments": [], "events": []}
        o = subprocess.run(["node", "-e", js.group(0) + "for(const s of " + json.dumps([ev("approve", 0), ev("send", 1), mid])
                            + ")console.log(listenText(s))"], capture_output=True, text=True, check=True).stdout
        assert o.splitlines() == ["Approved", "Claude is working on round 2", "Claude is working. Notes you send now are next."], o
    # approve on the cut: the style-edit's scripts fill the page's "Making the edit" steps, in place, never twice
    sf = tmp / "edits" / "steps"
    sf.mkdir(parents=True)
    (sf / "review").mkdir()
    save(sf / "review.json", {"folder": str(sf), "name": "steps", "stage": "cut", "round": 1, "sent": False,
                              "comments": [], "events": []})
    assert not step(sf, "captions") and not step(tmp / "nowhere", "captions"), "no approve yet: nothing"
    update(sf / "review.json", lambda d: d.update(sent=True, events=[{"type": "approve", "stage": "cut", "round": 1,
                                                                      "at": now(), "handled": True}]))
    assert step(sf, "captions") and step(sf, "captions") and not step(sf, "nonsense")
    assert step(sf, "visuals") and step(sf, "plan") and step(sf, "plan", log=lg)
    with contextlib.redirect_stdout(io.StringIO()):
        cmd_status(ns(text="", log=str(lg), done=False, step="render"), sf)
    act = load(sf / "review.json")["activity"]
    assert [x.get("step") for x in act] == ["captions", "visuals", "plan", "render"], act
    assert [x["done"] for x in act] == [True, True, True, False] and act[-1]["text"] == "Rendering the edit", act
    assert act[2]["log"] == str(lg.resolve()) and busy(sf)
    srv3, url3 = serve(sf, 0, open_browser=False)   # the page's Stop takes the approve back; `status` says STOPPED
    threading.Thread(target=srv3.serve_forever, daemon=True).start()
    b3, t3 = url3.split("/?t=")
    s3 = json.loads(urllib.request.urlopen(b3 + "/state?t=" + t3).read())
    assert s3["making"]["style"] == "your style" and list(s3["making"]["steps"]) == list(EDIT_STEPS), s3.get("making")
    keep_d = load(sf / "review.json")
    update(sf / "review.json", lambda d: d["events"][-1].update(handled=False))   # Stop before `wait` took the approve
    urllib.request.urlopen(urllib.request.Request(b3 + "/event?t=" + t3, b'{"type":"stop"}', method="POST"))
    d = load(sf / "review.json")
    assert not d["sent"] and d["stopped"] and not making_edit(d) and not step(sf, "render"), d
    assert all(e["handled"] for e in d["events"]), "a stopped approve never reaches `wait`"
    try:   # and the edit cannot open on the stopped page
        with contextlib.redirect_stdout(io.StringIO()) as o:
            cmd_round(ns(video=str(folder / "review" / "v-cut1.mp4"), stage="edit", transcript=None, plan=None,
                         note=None, name=None), sf)
        raise AssertionError("an edit round after Stop")
    except SystemExit as e:
        assert e.code == 3 and "STOPPED" in o.getvalue()
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            cmd_status(ns(text="", log=None, done=False, step="render"), sf)
        raise AssertionError("status after a stop on the approve")
    except SystemExit as e:
        assert e.code == 3
    srv3.shutdown()
    save(sf / "review.json", keep_d)
    assert making_style(sf) == "your style" and making_style(folder) == "@alice's style"
    with contextlib.redirect_stdout(io.StringIO()):
        cmd_round(ns(video=str(folder / "review" / "v-cut1.mp4"), stage="edit", transcript=None, plan=None, note=None,
                     name=None), sf)
    d = load(sf / "review.json")
    assert not making_edit(d) and all(x["done"] for x in d["activity"]) and not step(sf, "render"), "the edit landed"
    d.update(sent=True, events=d["events"] + [{"type": "send", "stage": "edit", "round": 1, "at": now(), "handled": True}])
    save(sf / "review.json", d)
    assert step(sf, "render"), "a sent edit round is being redone: steps again"
    # "just the cut": final, so an approved cut makes no edit and takes no steps
    d.update(stage="cut", final=True, events=d["events"] + [{"type": "approve", "stage": "cut", "round": 1,
                                                             "at": now(), "handled": True}])
    save(sf / "review.json", d)
    assert not making_edit(load(sf / "review.json")) and not step(sf, "plan")
    # the lock: writers in many threads (and processes) never lose an update or share a tmp file
    cnt = tmp / "count.json"
    save(cnt, {"n": 0})
    ths = [threading.Thread(target=lambda: [update(cnt, lambda d: d.update(n=d["n"] + 1)) for _ in range(25)]) for _ in range(4)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    sub = [subprocess.Popen([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(Path(__file__).parent)!r}); "
                             f"import review; [review.update(review.Path({str(cnt)!r}), lambda d: d.update(n=d['n'] + 1)) "
                             f"for _ in range(25)]"]) for _ in range(2)]
    assert all(x.wait() == 0 for x in sub)
    assert load(cnt)["n"] == 150 and not list(tmp.glob("count*.tmp")) and not (tmp / "count.json.lock").exists()
    (tmp / "count.json.lock").touch()
    os.utime(tmp / "count.json.lock", (time.time() - 30, time.time() - 30))   # a writer that died holding it
    update(cnt, lambda d: d.update(n=0))
    # round 0: the raw clip opens before the cut. No approve or stop; a note waits for round 1, moved onto the cut
    vid.unlink()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=6",
                    "-pix_fmt", "yuv420p", str(vid)], check=True)
    rf = tmp / "edits" / "raw"
    with contextlib.redirect_stdout(io.StringIO()):
        cmd_start(ns(video=str(vid), stage="cut", transcript=None, plan=None, note=None, name=None, raw=True), rf)
    d0 = load(rf / "review.json")
    assert (d0["round"], d0["raw"], d0["sent"]) == (0, True, True) and d0["events"][-1]["handled"], d0
    srv2, url2 = serve(rf, 0, open_browser=False)
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    urllib.request.urlopen(url2).read()   # a server that answered: its port sits in TIME_WAIT after it stops
    srv2.shutdown()
    srv2.server_close()
    srv2, again = serve(rf, 0, open_browser=False)   # a restart keeps the same address, so open tabs keep working
    assert again == url2, (again, url2)
    if os.name != "nt":
        assert (rf / "review" / ".server").stat().st_mode & 0o777 == 0o600
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    b2, t2 = url2.split("/?t=")
    p2 = lambda p, b: urllib.request.urlopen(urllib.request.Request(b2 + p + "?t=" + t2, json.dumps(b).encode(), method="POST"))
    for t in ("approve", "stop"):
        try:
            p2("/event", {"type": t})
            raise AssertionError(t + " on the raw clip")
        except urllib.error.HTTPError as e:
            assert e.code == 409, e
    n0 = json.loads(p2("/comment", {"t": 4.0, "text": "cut this bit"}).read())
    assert n0["queued"] and n0["round"] == 1, n0
    (rf / "decisions.json").write_text(json.dumps([{"start": 0.0, "end": 1.0}, {"start": 3.0, "end": 6.0}]), encoding="utf-8")
    cut = tmp / "rawcut.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(vid), "-t", "4", str(cut)], check=True)
    with contextlib.redirect_stdout(io.StringIO()):
        cmd_start(ns(video=str(cut), stage="cut", transcript=None, plan=None, note=None, name=None, raw=False), rf)
    d1 = load(rf / "review.json")
    c = next(c for c in d1["comments"] if c["id"] == n0["id"])
    assert (d1["round"], d1["sent"], "raw" in d1) == (1, False, False) and [r["round"] for r in d1["rounds"]] == [0, 1], d1
    assert (c["round"], c["t"], c["moved"], "queued" in c) == (1, 2.0, "words", False), c
    p2("/event", {"type": "approve"})
    assert "learn" not in load(rf / "review.json"), "the cut's approve is not the end: nothing to learn yet"
    # the final approve with no notes all session: the panel says so, LEARN asks nothing
    update(rf / "review.json", lambda d: d.update(final=True, sent=False, comments=[]))
    p2("/event", {"type": "approve"})
    assert load(rf / "review.json")["learn"]["state"] == "none"
    o = io.StringIO()
    with contextlib.redirect_stdout(o):
        cmd_wait(rf, poll=0.01)
    assert "LEARN: no notes this session" in o.getvalue(), o.getvalue()
    # the transcript editor: Apply turns restore/cut word ranges into spans.json, keeps spans.prev.json, and `wait`
    # exits with the EDITS line. Restoring part of a span splits it; a cut inside a word takes the whole word
    ef = tmp / "edits" / "tx"
    ws = "so the thing is the thing is we ship it today okay".split()
    (ef / "review").mkdir(parents=True)
    (ef / "words.raw.json").write_text(json.dumps([{"text": w, "start": i * 0.5, "end": i * 0.5 + 0.4, "type": "word"}
                                                    for i, w in enumerate(ws)] + [{"text": "untimed", "type": "word"}]), encoding="utf-8")
    sp0 = [{"text": "the thing is the thing is", "kind": "retake", "occurrence": 1, "note": "keeper after"},
           {"text": "okay", "kind": "filler"}]
    (ef / "spans.json").write_text(json.dumps(sp0), encoding="utf-8")
    (ef / "decisions.json").write_text(json.dumps([{"start": 0.0, "end": 0.45}, {"start": 3.45, "end": 6.0}]), encoding="utf-8")
    tv = tmp / "tx.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=3",
                    "-pix_fmt", "yuv420p", str(tv)], check=True)
    with contextlib.redirect_stdout(io.StringIO()):
        cmd_start(ns(video=str(tv), stage="cut", transcript=None, plan=None, note=None, name=None, raw=False), ef)
    tx = load(ef / "review" / load(ef / "review.json")["data"])["transcript"]
    flat = [x for ln in tx["lines"] for x in ln["tokens"] if "w" in x]
    assert flat[1] == {"w": "the", "t": None, "s": 0.5, "e": 0.9, "k": "retake", "g": 0, "n": "keeper after"} and flat[-1] == {"w": "untimed", "t": None, "nt": 1}, flat
    assert tx["kept"] == [[0.0, 0.45], [3.45, 6.0]], tx
    srv3, url3 = serve(ef, 0, open_browser=False)
    threading.Thread(target=srv3.serve_forever, daemon=True).start()
    b3, t3 = url3.split("/?t=")
    p3 = lambda b: json.loads(urllib.request.urlopen(urllib.request.Request(b3 + "/edits?t=" + t3, json.dumps(b).encode(), method="POST")).read())
    # a data file from before words had raw times: /data rebuilds it, so the page can edit and follow the words
    dp = ef / "review" / load(ef / "review.json")["data"]
    old = load(dp)
    for ln in old["transcript"]["lines"]:
        ln["tokens"] = [{k: v for k, v in x.items() if k not in ("s", "e", "k", "g", "n")} for x in ln["tokens"] if not x.get("nt")]
    save(dp, old)
    got = json.loads(urllib.request.urlopen(b3 + "/data?k=cut-1&t=" + t3).read())
    assert got["transcript"]["lines"][0]["tokens"][1]["s"] == 0.5, got
    try:
        urllib.request.urlopen(urllib.request.Request(b3 + "/edits", b"{}", method="POST"))
        raise AssertionError("/edits without the token")
    except urllib.error.HTTPError as err:
        assert err.code == 403
    assert p3({"restore": [[6.0, 6.4]]})["event"] is False and not (ef / "spans.prev.json").exists()   # kept already: no change
    # restore "is the" (1.5-2.4 s): the retake span splits into two; cut "ship" by a range inside the word
    r = p3({"restore": [[1.5, 2.4]], "cut": [[4.05, 4.1]]})
    assert (r["restored"], r["cut"], r["event"]) == (2, 1, True), r
    sp1 = load(ef / "spans.json")
    assert sp1 == [{"kind": "retake", "note": "keeper after", "text": "the thing", "occurrence": 1},
                   {"kind": "retake", "note": "keeper after", "text": "thing is", "occurrence": 2},
                   {"kind": "manual", "note": "cut on the review page", "text": "ship"},
                   {"kind": "filler", "text": "okay"}], sp1
    assert load(ef / "spans.prev.json") == sp0
    try:   # Claude has the round now
        p3({"cut": [[0.0, 0.4]]})
        raise AssertionError("edits while Claude has the round")
    except urllib.error.HTTPError as err:
        assert err.code == 409
    o = io.StringIO()
    with contextlib.redirect_stdout(o):
        cmd_wait(ef, poll=0.01)
    o = o.getvalue()
    assert o.startswith("EDITS 2 transcript edits applied to spans.json: rebuild, render, verify, round"), o
    assert 'restore 1.50-2.40s: "is the"' in o and 'cut 4.05-4.10s: "ship"' in o and "build_timeline.py" in o and "\nSEND: tx the cut, round 1" in o, o
    # Stop while Claude applies them: spans.json goes back
    urllib.request.urlopen(urllib.request.Request(b3 + "/event?t=" + t3, b'{"type": "stop"}', method="POST"))
    assert load(ef / "spans.json") == sp0 and not load(ef / "review.json")["sent"]
    # a whole span restored: it goes; the next span keeps its fields
    assert p3({"restore": [[5.5, 5.9]]})["restored"] == 1 and load(ef / "spans.json") == sp0[:1]
    # the pause trim's words: Restore puts their time in keep.json (build_timeline.py leaves it in), a removed pause
    # comes back whole, Stop puts keep.json back, and a later cut takes its words out of keep.json again
    stop3 = lambda: urllib.request.urlopen(urllib.request.Request(b3 + "/event?t=" + t3, b'{"type": "stop"}', method="POST"))
    stop3()
    (ef / "decisions.json").write_text(json.dumps([{"start": 0.0, "end": 0.45}, {"start": 3.45, "end": 4.45}, {"start": 5.45, "end": 6.0}]),
                                       encoding="utf-8")   # "it today" (4.5-5.4 s) fall in a trimmed pause
    r = p3({"restore": [[4.5, 5.4]], "pause": [[0.4, 0.5]]})
    assert (r["restored"], r["pauses"], r["event"]) == (2, 1, True), r
    assert load(ef / "keep.json") == [{"start": 0.4, "end": 0.5}, {"start": 4.5, "end": 5.4}] and load(ef / "spans.json") == sp0
    assert load(ef / "keep.prev.json") == []
    o = edits_text(ef, load(ef / "review.json")["events"][-1])
    assert 'restore 4.50-5.40s: "it today"' in o and "pause back 0.40-0.50s" in o and "keep.json: 2 range(s)" in o, o
    stop3()
    assert load(ef / "keep.json") == [] and load(ef / "spans.json") == sp0
    assert apply_edits(ef, [[3.5, 3.9]], [])["restored"] == 0   # "we" plays already: nothing to keep
    assert apply_edits(ef, [[4.5, 5.4]], [])["keep"] == 1
    r = apply_edits(ef, [], [[5.0, 5.4]])
    assert r["cut"] == 1 and load(ef / "keep.json") == [{"start": 4.5, "end": 5.0}], r
    assert {"kind": "manual", "note": "cut on the review page", "text": "today"} in load(ef / "spans.json")
    # two Applies at once: one wins, the other is refused, and spans.prev.json is what was there before both
    before = load(ef / "spans.json")
    outs = []
    th = [threading.Thread(target=lambda c=c: outs.append(_post_code(b3 + "/edits?t=" + t3, {"cut": [c]}))) for c in ([0.0, 0.4], [3.5, 3.9])]
    [x.start() for x in th]
    [x.join() for x in th]
    assert sorted(outs) == [200, 409] and load(ef / "spans.prev.json") == before, outs
    # while Claude applies them: send and approve are refused (Stop first)
    assert _post_code(b3 + "/event?t=" + t3, {"type": "send"}) == 409 and _post_code(b3 + "/event?t=" + t3, {"type": "approve"}) == 409
    # Stop after Claude changed spans.json itself: nothing is put back over Claude's change; round refuses while stopped
    mine = load(ef / "spans.json") + [{"text": "so", "kind": "filler", "occurrence": 1}]
    save(ef / "spans.json", mine)
    stop3()
    assert load(ef / "spans.json") == mine
    try:
        cmd_round(ns(video=str(tv), stage=None, transcript=None, plan=None, note=None, name=None), ef)
        raise AssertionError("round while stopped")
    except SystemExit as err:
        assert "STOPPED" in str(err)
    # an Apply then a send in one `wait`: the EDITS block still prints
    def two(d):
        d["events"] += [{"type": "edits", "stage": "cut", "round": d["round"], "at": now(), "handled": False, "n": 1,
                         "restore": [], "cut": [[0.0, 0.4]], "pause": [], "counts": {"restored": 0, "cut": 1, "keep": 0}},
                        {"type": "send", "stage": "cut", "round": d["round"], "at": now(), "handled": False}]
    update(ef / "review.json", two)
    o = io.StringIO()
    with contextlib.redirect_stdout(o):
        cmd_wait(ef, poll=0.01)
    assert "EDITS 1 transcript edit applied" in o.getvalue() and "\nSEND: tx" in o.getvalue(), o.getvalue()
    # the reviewer's cases, straight on apply_edits: a restore never sweeps in a hidden (cough) span; a cut beside an
    # overlapping (laughs) merges into one span that builds; an untimed "the" counts in "occurrence" as build counts it
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills" / "cut" / "scripts"))
    import build_timeline

    def mk(words, spans):
        f = Path(tempfile.mkdtemp(dir=tmp))
        (f / "words.raw.json").write_text(json.dumps(words), encoding="utf-8")
        (f / "spans.json").write_text(json.dumps(spans), encoding="utf-8")
        return f
    W_ = lambda t, a, b, ty="word": {"text": t, "start": a, "end": b, "type": ty}
    f = mk([W_("a", 0, .4), W_("b", .5, .9), W_("(cough)", 1.0, 1.3, "audio_event"), W_("c", 1.4, 1.8)],
           [{"text": "b", "kind": "retake"}, {"text": "(cough)", "kind": "audio_event"}])
    apply_edits(f, [[0.5, 1.8]], [])
    assert load(f / "spans.json") == [{"text": "(cough)", "kind": "audio_event"}], load(f / "spans.json")
    f = mk([W_("so", 0, .5), W_("(laughs)", .45, .9, "audio_event"), W_("yes", 1.0, 1.3)], [{"text": "(laughs)", "kind": "audio_event"}])
    apply_edits(f, [], [[0, 0.5]])
    assert load(f / "spans.json") == [{"kind": "audio_event", "text": "so (laughs)"}], load(f / "spans.json")
    toks_ = [t for t in load(f / "words.raw.json") if t.get("type") in ("word", "audio_event")]
    assert len(build_timeline.resolve_spans(load(f / "spans.json"), toks_)) == 1
    f = mk([W_("the", 0, .2), {"text": "the", "type": "word"}, W_("the", 1, 1.2)], [])
    apply_edits(f, [], [[1, 1.2]])
    toks_ = [t for t in load(f / "words.raw.json") if t.get("type") in ("word", "audio_event")]
    assert load(f / "spans.json")[0]["occurrence"] == 3 and build_timeline.resolve_spans(load(f / "spans.json"), toks_)[0]["start"] == 1
    srv3.shutdown()
    srv3.server_close()
    srv2.shutdown()
    srv2.server_close()
    srv.shutdown()
    srv.server_close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("review demo ok")


def main():
    if sys.argv[1:2] == ["demo"]:
        return demo()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="the edit folder: edits/<name> (or product/<name>, clips/<name>)")
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("start")
    s.add_argument("--video", required=True)
    s.add_argument("--stage", choices=["cut", "edit"], default="edit")
    s.add_argument("--transcript", help="words timed to this video (edits/<name>/words.json)")
    s.add_argument("--plan", help="plan.json, so each note names the card on screen")
    s.add_argument("--name", help="what to call this video when the round has others (default main)")
    s.add_argument("--note")
    s.add_argument("--raw", action="store_true", help="round 0: the raw clip, shown while the cut is made")
    s = sp.add_parser("serve")
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--no-open", action="store_true", help="do not open the browser (tests, headless checks)")
    sp.add_parser("wait")
    sp.add_parser("final", help="the cut is the finished video: no styled edit follows its approve")
    s = sp.add_parser("round")
    s.add_argument("--video", required=True)
    s.add_argument("--stage", choices=["cut", "edit"])
    s.add_argument("--transcript")
    s.add_argument("--plan")
    s.add_argument("--note")
    s.add_argument("--name")
    s = sp.add_parser("status")
    s.add_argument("text", nargs="?", default="")
    s.add_argument("--log", help="a render log; the page shows the last N/M in it as progress")
    s.add_argument("--step", choices=list(EDIT_STEPS), help="one of the edit's steps: fills the page's Making the edit bar")
    s.add_argument("--done", action="store_true")
    s = sp.add_parser("alt")
    s.add_argument("name")
    s.add_argument("--video", required=True)
    s.add_argument("--transcript")
    s = sp.add_parser("resolve")
    s.add_argument("id", type=int)
    s.add_argument("status", choices=["fixed", "wontfix", "open"])
    s.add_argument("--reply")
    s.add_argument("--new-t", type=float, help="where the note's moment is in the new render")
    s = sp.add_parser("learn", help="save the round's preference notes to the user's taste")
    s.add_argument("--scope", choices=["this-style", "style", "video", "everyone"], help="asked once for the batch")
    s.add_argument("--style", help="this-style: the creator style.json (default: the one the edit used)")
    s.add_argument("--rule", nargs="+", action="append", metavar="ARG", help='<id> <section> "<rule>" [key=value]')
    s.add_argument("--none", action="store_true", help="no note in this round is a preference")
    s.add_argument("--propose", metavar="JSON", help="after the final approve: candidate rules for the page's learn panel")
    s.add_argument("--done", nargs="?", const="", metavar="SUMMARY", help="save the rules the user ticked into the style")
    a = ap.parse_args()
    folder = Path(a.folder).resolve()
    if getattr(a, "stage", None) == "edit":  # an edit round reads the edit folder's own files
        for k, f in (("transcript", "words.json"), ("plan", "plan.json")):
            if getattr(a, k, 0) is None and (folder / f).exists():
                setattr(a, k, str(folder / f))
    if a.cmd != "start" and not (folder / "review.json").exists():
        sys.exit(f"no review in {folder}: run `start` first")
    if a.cmd != "serve":   # every Claude-side command marks Claude as working on this edit
        (folder / "review").mkdir(parents=True, exist_ok=True)
        (folder / "review" / ".claude").touch()
        if askhook.session():   # this session's questions now go to this edit's page
            (folder / "review" / ".session").write_text(askhook.session(), encoding="utf-8")
    if a.cmd == "start":
        if not Path(a.video).exists():
            sys.exit(f"no such file: {a.video}")
        cmd_start(a, folder)
    elif a.cmd == "serve":
        srv, url = serve(folder, a.port, open_browser=not a.no_open)
        print(url, flush=True)
        if srv:
            threading.Thread(target=live.reexec_on_change, args=(srv,), daemon=True).start()   # newest code, same port
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                pass
        else:
            print("already serving this edit" + ("" if a.no_open else "; opened it"))
    elif a.cmd == "wait":
        cmd_wait(folder)
    elif a.cmd == "round":
        cmd_round(a, folder)
    elif a.cmd == "resolve":
        cmd_resolve(a, folder)
    elif a.cmd == "alt":
        cmd_alt(a, folder)
    elif a.cmd == "status":
        cmd_status(a, folder)
    elif a.cmd == "final":
        cmd_final(folder)
    elif a.cmd == "learn":
        cmd_learn(a, folder)


if __name__ == "__main__":
    main()
