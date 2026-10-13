#!/usr/bin/env python3
"""The style page's ready-made choices: caption, zoom, cut and card presets, and the real previews of them.
Stdlib only (plus ffmpeg and the installed renderer for previews).

    python3 presets.py list     every caption preset (built in, then the user's custom/captions/) and the others
    python3 presets.py demo     self-check (no renderer needed)

Caption presets are files: plugins/ai-editor/presets/captions/<name>.json (built in) and
<library>/custom/captions/<name>.json (the user's, custom.py; same name = the user's wins). Each is a captions
block of style.json that plan.py and Captions.tsx already read. Picking one saves captions.preset=<name>;
plan.py lays the block over the creator's measured captions, and taste.json's own caption keys (size, height)
still win. To add one: drop a JSON file in either folder. It shows up as a tile on the style page.

Zoom presets save zoom.preset=<name> (resolved the same way). Cut presets save cut.max_pause (the cut reads it).
Card frequency saves cards.per_min (plan.py keeps at most that many cards a minute); cards.on=false plans none.

Previews: each caption tile is a still rendered by the real StyleEdit composition (remotion/render.mjs many, one
bundle for every missing tile) over one frame of the user's clip with a phrase from their transcript, cached in
$AI_EDITOR_HOME/home/presets/ by a hash of everything that changes the picture.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import custom  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[2]
BUILT_IN = PLUGIN / "presets" / "captions"
ORDER = ("plain", "bold", "karaoke", "minimal")
AUDIO_EXT = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus")
EFFECT_NOTE = {"pop": "pops in", "karaoke": "lights up", "reveal": "fades in", "slide": "slides up", "lift": "lifts"}

# Zoom presets: a zoom block of style.json (plan.py place_zooms). per_min counts zoom changes a minute.
ZOOM = {
    "none": {"label": "None", "sub": "Camera stays still", "zoom": {"per_min": 0}},
    # the house default (profile.py DEFAULT_STYLE): a 0.8 s push on a sentence start, and never more than 5 s without
    # a zoom change, so on most takes a change lands every 5 s
    "smooth": {"label": "Smooth", "sub": "Slow push in, 0.8 s",
               "zoom": {"per_min": 3, "kind": "push", "scale": 1.18, "duration_s": 0.8, "on": "sentence_start", "max_hold_s": 5.0}},
    "punchy": {"label": "Punchy", "sub": "Snaps in fast",
               "zoom": {"per_min": 6, "kind": "punch", "scale": 1.15, "duration_s": 0, "on": "sentence_start"}},
}
# Cut presets: the pause the cut tightens to (build_timeline.py cut.max_pause; the taste skill's table).
CUT = {"relaxed": {"label": "Relaxed", "sub": "Keeps your pauses", "max_pause": 0.25},
       "natural": {"label": "Natural", "sub": "Short breaths stay", "max_pause": 0.15},
       "tight": {"label": "Tight", "sub": "Cuts every pause", "max_pause": 0.12}}
# How often a card (logo, screenshot, scene) may land: at most this many a minute (plan.py thin_cards).
CARDS = {"rarely": {"label": "Rarely", "per_min": 2}, "sometimes": {"label": "Sometimes", "per_min": 4},
         "often": {"label": "Often", "per_min": 16}}   # a card edit's rate: a new card about every 4 s
# Card looks the page offers (remotion/src/look.tsx LOOKS). The flat poster grounds and glass stay out: plan.py
# refuses them unless the user's own brand kit names them.
LOOKS = {"neutral": "Neutral, white cards", "editorial": "Editorial, warm paper", "editorial-dark": "Editorial, dark",
         "mono": "Mono, plain type"}
DEFAULTS = {"captions": "plain", "zoom": "smooth", "cut": "natural", "cards": "sometimes", "look": "neutral"}


# What a caption preset may set (Captions.tsx CaptionStyle): key -> (type, low, high) or a set of allowed words.
# Anything else in a file, or a value of the wrong type or out of range, is dropped with a warning.
HEX = re.compile(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?|#[0-9A-Fa-f]{3}")
FIELDS = {"style": str, "font_match": str, "size_pct": (float, 1, 15), "weight": (int, 100, 900), "case": {"as_is", "lower", "upper", "sentence"},
          "words_per_caption": (int, 1, 8), "max_lines": (int, 1, 3), "y_pct": (float, 10, 95), "width_pct": (float, 30, 100),
          "color": HEX, "highlight_color": HEX, "emphasis_color": HEX, "box_color": HEX, "inactive_opacity": (float, 0, 1),
          "active_scale": (float, 1, 2), "active_lift": (float, 0, 40), "stroke": bool, "box": bool, "shadow": bool, "present": bool,
          "effect": {"pop", "slide", "word_highlight", "karaoke", "reveal", "lift", "none"},
          "animation": {"pop", "slide", "word_highlight", "none"}}


def clean(block, where):
    """The keys of a caption block FIELDS allows, with sane types; each one dropped is named once on stderr."""
    out, bad = {}, []
    for k, v in (block or {}).items():
        rule = FIELDS.get(k)
        if rule is bool:
            ok = isinstance(v, bool)
        elif rule is str:
            ok = isinstance(v, str) and 0 < len(v) <= 64
        elif isinstance(rule, set):
            ok = v in rule
        elif isinstance(rule, re.Pattern):
            ok = isinstance(v, str) and bool(rule.fullmatch(v))
        elif isinstance(rule, tuple):
            ok = isinstance(v, (int, float)) and not isinstance(v, bool) and rule[1] <= v <= rule[2]
            v = rule[0](v) if ok else v
        else:
            ok = False
        if ok:
            out[k] = v
        else:
            bad.append(k)
    if bad:
        print(f"warning: {where}: ignored {', '.join(f'{k}={block[k]!r}' for k in bad)} (not a caption setting, or a bad value)", file=sys.stderr)
    return out


def _read(f):
    try:
        d = json.loads(Path(f).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"warning: {f} is not valid JSON ({e}); ignored", file=sys.stderr)
        return {}
    return clean(d, f) if isinstance(d, dict) else {}


def built_in():
    """{name: captions block} of the presets shipped with the plugin, in ORDER (the user's files never replace these)."""
    fs = sorted(BUILT_IN.glob("*.json"), key=lambda f: (f.stem not in ORDER, ORDER.index(f.stem) if f.stem in ORDER else 0, f.stem))
    return {k: v for k, v in ((f.stem, _read(f)) for f in fs) if v}


def captions():
    """{name: captions block}: the built-in ones in ORDER, then the user's custom/captions/*.json (same name: the user's)."""
    out = built_in()
    for f in sorted((custom.path(False) / "captions").glob("*.json")):
        out[f.stem] = _read(f) or out.get(f.stem)
    return {k: v for k, v in out.items() if v}


def caption(name):
    """One caption preset by name ({} when there is none), stamped with its name as "style"."""
    if not name or not re.fullmatch(r"[A-Za-z0-9 _.-]{1,64}", str(name)):
        return {}
    f = custom.find("captions", f"{name}.json")
    block = (_read(f) if f else {}) or (_read(BUILT_IN / f"{name}.json") if (BUILT_IN / f"{name}.json").is_file() else {})
    return {**block, "style": block.get("style") or name} if block else {}


def label(name):
    return str(name).replace("-", " ").replace("_", " ").capitalize()


def note(block, words=None):
    fx = block.get("effect") or block.get("animation") or "none"
    n = block.get("words_per_caption") or words or 3
    return EFFECT_NOTE.get(fx) or f"{n:g} word{'s' if n != 1 else ''}"


def apply(style, mine):
    """style with captions.preset and zoom.preset resolved (mine = taste.json + this style's overrides, which
    plan.py merges over the result, so the user's own keys still win). An unknown name warns and is ignored."""
    out = dict(style)
    name = (mine.get("captions") or {}).get("preset") or (style.get("captions") or {}).get("preset")
    if name:
        block = caption(name)
        if not block:
            print(f"warning: captions.preset {name!r}: no {custom.path(False) / 'captions' / (name + '.json')} and no "
                  f"built-in preset of that name; using the style's captions", file=sys.stderr)
        out["captions"] = {**(style.get("captions") or {}), **block}
    z = (mine.get("zoom") or {}).get("preset") or (style.get("zoom") or {}).get("preset")
    if z:
        if z not in ZOOM:
            print(f"warning: zoom.preset {z!r} is not one of {', '.join(ZOOM)}; using the style's zooms", file=sys.stderr)
        else:
            out["zoom"] = {**(style.get("zoom") or {}), **ZOOM[z]["zoom"]}
    return out


def closest_zoom(z):
    z = z or {}
    if z.get("preset") in ZOOM:
        return z["preset"]
    if not z.get("per_min"):
        return "none"
    return "punchy" if z.get("kind", "punch") == "punch" else "smooth"


def nearest(table, key, v):
    return min(table, key=lambda k: abs(table[k][key] - float(v)))


# ---------------------------------------------------------------- previews
def cache_dir():
    return Path(os.environ.get("AI_EDITOR_HOME", "").strip() or Path.home() / ".ai-video-editor") / "home" / "presets"


def _key(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]


def source_key(src):
    """What a preview depends on in the picture: the file and its mtime and size (re-rendered when it changes)."""
    if not src:
        return "none"
    p = Path(src)
    try:
        return f"{p.resolve()}:{int(p.stat().st_mtime)}:{p.stat().st_size}"
    except OSError:
        return str(p)


def phrase(edit_dir=None):
    """Five spoken words from the newest edit's transcript (a run that starts a sentence), else the stand-in."""
    for f in ("words.json", "cut.transcript.json", "words.raw.json"):
        p = Path(edit_dir) / f if edit_dir else None
        if not p or not p.is_file():
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        ws = [re.sub(r"[^\w'’-]", "", w.get("text", "")) for w in (d.get("words", []) if isinstance(d, dict) else d)
              if isinstance(w, dict) and w.get("type", "word") == "word"]
        ws = [w for w in ws if w]
        starts = [i for i in range(len(ws) - 4) if ws[i][:1].isupper()] + ([0] if len(ws) >= 5 else [])
        if starts:
            return ws[starts[0]:starts[0] + 5], True
    return ["I", "will", "push", "these", "changes"], False


def words_for(block, ws, real, default_n=3):
    n = int(block.get("words_per_caption") or default_n)
    return ws[:n] if real or n >= 4 else ws[2:2 + n]


def chunk_of(words, effect):
    """One caption page with spoken timings, and the frame to show: karaoke mid-line, the rest once all are in."""
    ws, t = [], 0.1
    for w in words:
        ws.append({"text": w, "start": round(t, 3), "end": round(t + 0.3, 3)})
        t += 0.32
    show = ws[min(2, len(ws) - 1)]["start"] - 0.01 if effect == "karaoke" and len(ws) > 2 else ws[-1]["start"] + 0.32
    return {"text": " ".join(words), "start": 0.0, "end": round(t + 1.0, 3), "words": ws}, round(show * 30)


def frame_of(src, out, size=(720, 1280)):
    """One 9:16 frame of src (a clip a quarter of the way in, or an image) as a 3 s still video for the renderer."""
    if src and Path(src).suffix.lower() in AUDIO_EXT:
        return None
    w, h = size
    fit = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    jpg = Path(out).with_suffix(".jpg")
    dur = 0.0
    if src and Path(src).suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                           capture_output=True, text=True, timeout=30)
        try:
            dur = float(r.stdout.strip() or 0)
        except ValueError:
            dur = 0.0
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *(["-ss", f"{dur * 0.25:.2f}"] if dur else []), "-i", str(src),
                        "-frames:v", "1", "-vf", fit, "-q:v", "3", str(jpg)], capture_output=True, timeout=60)
    if r.returncode or not jpg.is_file():
        return None
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(jpg), "-t", "3", "-r", "30", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-preset", "ultrafast", str(out)], capture_output=True, timeout=60)
    return jpg if r.returncode == 0 and Path(out).is_file() else None


def band_luma(jpg, y_pct):
    """Mean brightness (0-255) of the band a caption sits on: plan.py's contrast ladder, first step only."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(jpg), "-vf",
                        f"crop=iw*0.8:ih*0.08:iw*0.1:ih*{max(0.0, min(0.92, y_pct / 100 - 0.04)):.3f},scale=1:1,format=gray",
                        "-f", "rawvideo", "-"], capture_output=True, timeout=30)
    return r.stdout[0] if r.stdout else 0


FAIL_TTL_S = 120   # a failed tile is tried again after this long (the renderer may have been installed since)
KEEP_FILES, KEEP_DAYS = 60, 7   # the preview cache: at most this many files, none older than this


class Renderer:
    """Renders missing tiles in the background, one batch at a time. want() returns the cached file or None.
    A key queued or being rendered is never queued again (the page polls every 1.5 s)."""
    def __init__(self):
        self.lock = threading.Lock()
        self.queue, self.inflight, self.busy, self.failed = {}, set(), False, {}

    def error(self, key):
        e = self.failed.get(key)
        return e[1] if e and time.time() - e[0] < FAIL_TTL_S else None

    def want(self, key, job):
        f = cache_dir() / f"{key}.jpg"
        if f.is_file():
            return f
        with self.lock:
            if self.error(key) or key in self.queue or key in self.inflight:
                return None
            self.failed.pop(key, None)
            self.queue[key] = job
            if not self.busy:
                self.busy = True
                threading.Thread(target=self._run, daemon=True).start()
        return None

    def _run(self):
        try:
            while True:
                with self.lock:
                    batch = {k: j for k, j in self.queue.items() if not (cache_dir() / f"{k}.jpg").is_file()}
                    self.queue = {}
                    if not batch:
                        return
                    self.inflight = set(batch)
                try:
                    err = render_batch(batch)
                except Exception as e:   # noqa: BLE001  a render thread that dies would leave busy set for good
                    err = f"{type(e).__name__}: {e}"
                with self.lock:
                    self.inflight = set()
                    for k in batch:
                        if err and not (cache_dir() / f"{k}.jpg").is_file():
                            self.failed[k] = (time.time(), err)
                if err:
                    print(f"presets: previews did not render: {err[-400:]}", file=sys.stderr)
                prune()
        finally:
            with self.lock:
                self.busy, self.inflight = False, set()


def prune():
    """The preview cache stays small: files older than KEEP_DAYS go, then all but the KEEP_FILES newest."""
    d = cache_dir()
    if not d.is_dir():
        return
    fs = sorted((f for f in d.iterdir() if f.is_file()), key=lambda f: f.stat().st_mtime, reverse=True)
    old = time.time() - KEEP_DAYS * 86400
    for i, f in enumerate(fs):
        if i >= KEEP_FILES or f.stat().st_mtime < old or (f.name.endswith((".tmp.jpg", ".part.mp4")) and f.stat().st_mtime < time.time() - 900):
            f.unlink(missing_ok=True)


RENDERER = Renderer()


_EDIT = str(PLUGIN / "skills" / "style-edit" / "scripts")


def renderer_dir():
    """The installed renderer with this plugin's composition source copied in, or None when it is not installed
    (the page never installs it: the setup skill does)."""
    if _EDIT not in sys.path:
        sys.path.append(_EDIT)
    import edit
    if not (edit.REMOTION / "node_modules" / "remotion").exists():
        return None
    edit.mirror(edit.SRC / "src", edit.REMOTION / "src")
    for f in ("render.mjs", "tsconfig.json"):
        edit.copy_changed(edit.SRC / f, edit.REMOTION / f)
    return edit.REMOTION


def render_batch(batch):
    """batch: {key: {"block": captions block, "words": [...], "src": picture}}. One bundle, one still each.
    Returns an error string, or None."""
    rem = renderer_dir()
    if rem is None:
        return "the renderer is not installed (setup skill)"
    out_dir = cache_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    pub = Path(tempfile.mkdtemp(prefix="presets_pub_"))
    try:
        jobs, bgs = [], {}
        for key, j in batch.items():
            sk = source_key(j["src"])
            if sk not in bgs:
                name = f"bg{len(bgs)}.mp4"
                bgs[sk] = (name, frame_of(j["src"], pub / name))
            name, jpg = bgs[sk]
            if jpg is None:
                return f"could not read a frame of {j['src']}"
            block = dict(j["block"])
            chunk, frame = chunk_of(j["words"], block.get("effect") or block.get("animation"))
            if block.get("shadow") is False and not block.get("box") and band_luma(jpg, block.get("y_pct") or 66) > 150:
                chunk["treat"] = "shadow"   # plan.py treat_captions: a light background gets the soft shadow first
            plan = {"video": name, "width": 720, "height": 1280, "fps": 30, "durationInFrames": 90,
                    "captions": {"style": block, "chunks": [chunk]}, "zooms": [], "cards": [], "look": {"preset": "neutral"},
                    "motion": "smooth"}
            jobs.append({"plan": plan, "frame": frame, "out": str(out_dir / f"{key}.tmp.jpg")})
        jf = pub / "jobs.json"
        jf.write_text(json.dumps(jobs), encoding="utf-8")
        r = subprocess.run(["node", str(rem / "render.mjs"), "many", str(pub), str(jf)], cwd=rem, capture_output=True,
                           text=True, timeout=600)
        done = {Path(x[3:].strip()).name for x in r.stdout.splitlines() if x.startswith("ok ")}   # what node says it wrote
        for key in batch:
            tmp = out_dir / f"{key}.tmp.jpg"
            if tmp.name in done and tmp.is_file():
                os.replace(tmp, out_dir / f"{key}.jpg")
            else:
                tmp.unlink(missing_ok=True)
        missing = [k for k in batch if not (out_dir / f"{k}.jpg").is_file()]
        return ((r.stderr or r.stdout or "render.mjs failed")[-800:]) if r.returncode or missing else None
    finally:
        shutil.rmtree(pub, ignore_errors=True)


_SRC_HASH = []


def renderer_version():
    """A hash of the composition source (remotion/src): a changed caption renderer re-renders every tile."""
    if not _SRC_HASH:
        h = hashlib.sha1()
        for f in sorted((PLUGIN / "remotion" / "src").rglob("*")):
            if f.is_file():
                h.update(f.name.encode() + f.read_bytes())
        _SRC_HASH.append(h.hexdigest()[:12])
    return _SRC_HASH[0]


def tile(block, words, real, src):
    """(key, url or None, error) for one caption still; a missing one is queued for the renderer."""
    ws = words_for(block, words, real)
    key = _key(block, ws, source_key(src), renderer_version())
    f = RENDERER.want(key, {"block": block, "words": ws, "src": src})
    return key, (f"/style/tile?k={key}&v={int(f.stat().st_mtime)}" if f else None), RENDERER.error(key)


# ---------------------------------------------------------------- the cut: 5 s before and after
def tighten(words, a, b, max_pause):
    """Spans of the raw take [a, b] the cut keeps under max_pause: speech kept, a gap longer than max_pause shortened
    to two thirds of it (build_timeline.py's pause rule; retakes are not judged here)."""
    keep = 2 / 3 * max_pause
    ws = [w for w in words if w.get("type", "word") == "word" and w["end"] > a and w["start"] < b]
    if not ws:
        return [(a, b)]
    spans, s = [], max(a, ws[0]["start"] - 0.05)
    for w, n in zip(ws, ws[1:]):
        gap = n["start"] - w["end"]
        if gap > max_pause:
            spans.append((s, w["end"] + keep / 2))
            s = n["start"] - keep / 2
    spans.append((s, min(b, ws[-1]["end"] + 0.1)))
    return [(round(x, 3), round(y, 3)) for x, y in spans if y > x]


_CUT_LOCKS = {}
_CUT_LOCK = threading.Lock()


def _encode(cmd, out, timeout):
    """ffmpeg into out.part.mp4, renamed only when it worked: a stopped or failed encode never leaves a broken file."""
    part = out.with_name(out.stem + ".part.mp4")
    try:
        r = subprocess.run([*cmd, str(part)], capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        part.unlink(missing_ok=True)
        return False
    if r.returncode or not part.is_file():
        part.unlink(missing_ok=True)
        return False
    os.replace(part, out)
    return True


def cut_clips(edit_dir, max_pause, seconds=5.0):
    """(before.mp4, after.mp4) for the newest cut's opening seconds: the raw take as filmed, then the same words with
    this preset's pause rule. Cached by take, transcript and setting. None when there is nothing to cut from."""
    edit_dir = Path(edit_dir)
    try:
        src = Path(json.loads((edit_dir / "report.json").read_text(encoding="utf-8"))["source"])
        wf = edit_dir / "words.raw.json"
        words = json.loads(wf.read_text(encoding="utf-8"))
        words = words.get("words", []) if isinstance(words, dict) else words
        a = max(0.0, next(w["start"] for w in words if isinstance(w, dict) and w.get("type", "word") == "word") - 0.1)
    except (OSError, ValueError, KeyError, TypeError, StopIteration):
        return None
    if not src.is_file():
        return None
    b = a + seconds
    out = cache_dir()
    out.mkdir(parents=True, exist_ok=True)
    base = _key(source_key(src), int(wf.stat().st_mtime), a, seconds, "cut-v2")
    before, after = out / f"{base}-before.mp4", out / f"{base}-{max_pause:g}.mp4"
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-f", "mp4"]
    with _CUT_LOCK:
        lock = _CUT_LOCKS.setdefault(base, threading.Lock())
    with lock:   # two clicks at once make the clips once
        if not before.is_file():
            _encode(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{seconds:.3f}", "-i", str(src),
                     "-vf", "scale=540:-2", *enc], before, 120)
        if not after.is_file():
            got, kept = [], 0.0
            for x, y in tighten(words, a, b + 3, max_pause):   # the same 5 s of footage after the cut
                if kept >= seconds:
                    break
                y = min(y, x + seconds - kept)
                got.append((x, y))
                kept += y - x
            # input seeking to a: the trims are relative to it
            parts = "".join(f"[0:v]trim={x - a:.3f}:{y - a:.3f},setpts=PTS-STARTPTS[v{i}];"
                            f"[0:a]atrim={x - a:.3f}:{y - a:.3f},asetpts=PTS-STARTPTS[a{i}];" for i, (x, y) in enumerate(got))
            cat = "".join(f"[v{i}][a{i}]" for i in range(len(got))) + f"concat=n={len(got)}:v=1:a=1[vc][a];[vc]scale=540:-2[v]"
            _encode(["ffmpeg", "-v", "error", "-y", "-ss", f"{a:.3f}", "-t", f"{b + 3 - a:.3f}", "-i", str(src),
                     "-filter_complex", parts + cat, "-map", "[v]", "-map", "[a]", *enc], after, 180)
    prune()
    return (before, after) if before.is_file() and after.is_file() else None


def demo():
    tmp = Path(tempfile.mkdtemp(prefix="presets_demo_"))
    os.environ["AI_EDITOR_LIBRARY"] = str(tmp / "lib")
    os.environ["AI_EDITOR_HOME"] = str(tmp / "home")
    cs = captions()
    assert list(cs)[:4] == list(ORDER), list(cs)
    plain = cs["plain"]
    assert plain["font_match"] == "TikTok Sans" and plain["color"] == "#FFFFFF" and plain["weight"] == 600, plain
    assert all(c.get("shadow") is False and not c.get("stroke") and not c.get("box") for c in cs.values()), "no shadows"
    assert note(cs["bold"]) == "pops in" and note(plain, 3) == "3 words" and note(cs["karaoke"]) == "lights up"
    # a user's preset is a file in custom/captions: it appears after the built-in ones and plan.py can use it
    (custom.path() / "captions" / "yellow.json").write_text('{"color": "#FFE14D", "weight": 800}', encoding="utf-8")
    assert list(captions())[-1] == "yellow" and caption("yellow")["style"] == "yellow"
    assert caption("../etc") == {} and caption("nope") == {}
    # a user's file never replaces the built-in set profile.py builds from, and bad values are dropped, not passed on
    (custom.path() / "captions" / "plain.json").write_text("{broken", encoding="utf-8")
    assert built_in()["plain"] == plain and captions()["plain"] == plain, "a broken custom plain.json keeps the built-in one"
    (custom.path() / "captions" / "odd.json").write_text('{"words_per_caption": "two", "size_pct": 99, "color": "red", "weight": 700, "x": 1}',
                                                       encoding="utf-8")
    assert caption("odd") == {"weight": 700, "style": "odd"} and "odd" in captions()
    assert clean({"size_pct": 5, "box": "yes", "case": "upper"}, "t") == {"size_pct": 5.0, "case": "upper"}
    # the renderer: a queued key is not queued twice, a failure is retried after its TTL, a crash never leaves it busy
    rr = Renderer()
    rr.busy = True   # no thread: only the bookkeeping
    assert rr.want("k1", {}) is None and rr.want("k1", {}) is None and list(rr.queue) == ["k1"]
    rr.failed["k2"] = (time.time() - FAIL_TTL_S - 1, "old")
    assert rr.error("k2") is None and rr.want("k2", {}) is None and "k2" in rr.queue
    global render_batch
    real_rb, render_batch = render_batch, lambda b: 1 / 0
    rr2 = Renderer()
    rr2.queue = {"k3": {}}
    rr2.busy = True
    rr2._run()
    render_batch = real_rb
    assert not rr2.busy and rr2.error("k3").startswith("ZeroDivisionError"), rr2.failed
    # the cache is capped
    cd = cache_dir()
    cd.mkdir(parents=True)
    for i in range(KEEP_FILES + 5):
        (cd / f"{i:020x}.jpg").write_bytes(b"x")
    (cd / "old.jpg").write_bytes(b"x")
    os.utime(cd / "old.jpg", (1, 1))
    prune()
    assert len(list(cd.iterdir())) == KEEP_FILES and not (cd / "old.jpg").exists()
    st = {"captions": {"size_pct": 7, "words_per_caption": 1, "font_match": "Inter"}, "zoom": {"per_min": 9, "kind": "punch", "on": "every_cut"}}
    got = apply(st, {"captions": {"preset": "karaoke", "size_pct": 5}, "zoom": {"preset": "smooth"}})
    assert got["captions"]["effect"] == "karaoke" and got["captions"]["words_per_caption"] == 4 and got["captions"]["font_match"] == "TikTok Sans"
    assert got["zoom"] == {"per_min": 3, "kind": "push", "on": "sentence_start", "scale": 1.18, "duration_s": 0.8, "max_hold_s": 5.0}, got["zoom"]
    assert apply(st, {"zoom": {"preset": "none"}})["zoom"]["per_min"] == 0 and apply(st, {}) == st
    assert closest_zoom({"per_min": 9.5, "kind": "push"}) == "smooth" and closest_zoom({"per_min": 4, "kind": "punch"}) == "punchy"
    assert closest_zoom({}) == "none" and nearest(CUT, "max_pause", 0.2) == "relaxed" and nearest(CARDS, "per_min", 5) == "sometimes"
    # the phrase: five words that start a sentence, from the user's own transcript
    ed = tmp / "ed"
    ed.mkdir()
    (ed / "words.json").write_text(json.dumps([{"text": t, "start": i, "end": i + 0.5} for i, t in
                                               enumerate("so, This whole thing is wild. Right".split())]), encoding="utf-8")
    ws, real = phrase(ed)
    assert ws == ["This", "whole", "thing", "is", "wild"] and real
    assert words_for(cs["bold"], ws, real) == ["This", "whole"] and words_for(cs["bold"], *phrase(None)) == ["push", "these"]
    ch, fr = chunk_of(["I", "will", "push", "these"], "karaoke")
    assert ch["words"][1]["start"] < fr / 30 < ch["words"][2]["start"], "karaoke shows mid-line: two words lit"
    # the cut preview's spans: gaps over the pause are shortened to 2/3 of it, speech kept
    w = [{"text": "a", "start": 0.0, "end": 0.5}, {"text": "b", "start": 1.5, "end": 2.0}, {"text": "c", "start": 2.1, "end": 2.6}]
    sp = tighten(w, 0, 5, 0.15)
    assert len(sp) == 2 and abs((sp[0][1] - 0.5) + (1.5 - sp[1][0]) - 0.1) < 1e-6, sp
    assert len(tighten(w, 0, 5, 1.2)) == 1, "a pause under the setting stays"
    shutil.rmtree(tmp, ignore_errors=True)
    print("presets demo ok")


def main():
    a = sys.argv[1:]
    if a == ["demo"]:
        return demo()
    if a == ["list"]:
        return print(json.dumps({"captions": list(captions()), "zoom": list(ZOOM), "cut": list(CUT), "cards": list(CARDS),
                                 "looks": list(LOOKS)}, indent=1))
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
