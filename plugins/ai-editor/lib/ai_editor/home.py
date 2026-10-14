#!/usr/bin/env python3
"""The home page: first run in the browser. Setup checks, optional keys, pick a creator, watch their
videos get studied, see the style and the skill it became, drop a video. Stdlib only.

Claude stays the brain. The page only queues jobs; Claude takes each one with `wait`, runs the skill
for it, and reports back with `status`:

    python3 home.py serve [--root DIR] [--no-open]   serve the page and open it (run in the background);
                                                 DIR defaults to the library (library.py), wherever this runs
    python3 home.py wait [--job ID]              block until the page queues a job, print it as one line,
                                                 exit (run in the background, again after each job).
                                                 --job: take that one job (a Claude the page opened for it)
        JOB teardown @handle platform=tiktok take=captions,pace root=DIR
        JOB teardown @name urls=DIR/creator-teardowns/name/picks.txt take= root=DIR
        JOB skill @handle take=captions,pace,graphics,entrances,layout root=DIR
        JOB video /path/take.mov as=@handle aspect=9:16 edit=DIR/edits/take root=DIR
                                                 (as=mine: the saved style; as=defaults: no creator)
        JOB video takes=DIR/edits/a/takes.txt as=mine aspect=9:16 edit=DIR/edits/a root=DIR
                                                 (several clips as one video: join them in the listed order)
                                                 (either may end with script=DIR/edits/a/script.md before root=)
        JOB cancel teardown                       the user pressed Cancel
        ALREADY ...                               another wait is running: nothing to do
        GONE ...                                  (--job) that job is done, cancelled or another Claude's
    python3 home.py status <job> [--step "..."] [--detail "video 6 of 10"] [--progress 0.6]
                                 [--result '<json>' | --result @file.json] [--done] [--failed "..."]
                                 <job> is teardown, skill, video or setup: its latest job
    python3 home.py demo                         self-check (no network, no keys)

Connect Modal and Test render run here, not through Claude: the server runs `setup.py modal-connect` or
`modal-test` and shows its STEP lines as the job's steps (jobs `modal` and `modal-test`, owner "home").

State: $AI_EDITOR_HOME/home/state.json (default ~/.ai-video-editor). Keys typed on the page go
straight to keys.py (prefix check, one free verify request, then saved 0600); a key value is never
written to state.json, printed, logged or sent back to the page.

Numbers on the page come from the teardown's own style.json and videos.json, read by this server.
Claude's `--result` adds words only (the skill, prompts, what not to copy). Nothing is made up: a part
not measured yet shows as empty.

The server listens on 127.0.0.1 only and every request needs the token in the URL it prints (then a
SameSite cookie), as review.py does. The address stays the same: port 47821 (the next free one if that is
taken, remembered in state.json) and one random token per install ($AI_EDITOR_HOME/home/token, 0600), so
the launcher and a bookmark always open the same page.

Sessions: the window is shared by every Claude Code session on the computer, so each job remembers the
session that took it (`owner`, from CLAUDE_CODE_SESSION_ID). `status` from another session never writes
to it, `wait` never takes a job reserved for the Claude opened for that video (every video gets one, claude_for), and a
question (askhook.py) reaches this page only from a session holding one of its jobs or listening for one.
"""
import argparse
import base64
import glob
import hmac
import http.client
import json
import os
import platform
import re
import secrets
import shutil
import statistics
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
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, quote, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import askhook  # noqa: E402
import assets  # noqa: E402
import custom  # noqa: E402
import keys  # noqa: E402
import library  # noqa: E402
import links  # noqa: E402
import live  # noqa: E402
import presets  # noqa: E402
from review import load, now, save, set_theme, theme, themed  # noqa: E402  (atomic write; review.py's server owns the rest)

HTML = Path(__file__).with_name("home.html")
SETUP = Path(__file__).resolve().parents[2] / "skills" / "setup" / "scripts" / "setup.py"
BEAT_S = 6
JOBS = ("teardown", "skill", "video", "modal", "modal-test", "setup", "style-change", "update")
TAKE = ("captions", "pace", "graphics", "sound")          # the page's parts; editplan.py --take names
# The page's "graphics" box is her visuals as a whole: what she shows, how it moves in and where it sits.
EDITPLAN_PARTS = {"graphics": ("graphics", "entrances", "layout")}
KEY_NAMES = tuple(keys.VARS)
TOOLS = {"python": ("Python", "runs the editor"), "ffmpeg": ("ffmpeg", "cuts and joins video"),
         "node": ("Node", "draws captions and zooms"), "git": ("git", "for the installers"),
         "python packages": ("Python packages", "pinned versions"),
         "transcription model": ("Transcription model", "500 MB, runs offline"),
         "renderer": ("The renderer", "700 MB"), "free disk": ("Free disk", "3 GB or more")}
TOP, CONTROL, SPARE = 5, 2, 2   # creator-teardown quick mode: 5 most viewed + 2 closest to the median + 2 spares
PORT = 47821              # the fixed address; the next free one up to PORT + 19 when it is taken
REVIEW = Path(__file__).with_name("review.py")
DEMO = Path(__file__).with_name("demo")   # stock photos for empty previews (demo/CREDITS.md)
HANDLE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
PLUGIN_JSON = Path(__file__).resolve().parents[2] / ".claude-plugin" / "plugin.json"
RAW = "https://raw.githubusercontent.com/nextwork-projects/ai-video-editor-cooked/main/"
COVER_MAX = 50 << 20   # a style's chosen image, or a short clip to take a frame from
SCRIPT_MAX = 200_000   # a script's text, in bytes; the file it came from may be up to 1 MB (a .docx with a logo)
SCRIPT_EXT = (".txt", ".md", ".docx", ".pdf")


SCRIPT_UNPACK = 20 << 20   # what a .pdf's streams or a .docx's document.xml may unpack to; more is refused
PDF_TOKEN = re.compile(rb"\((?:\\.|[^\\()])*\)|/[^\s/\[\]()<>{}%]*|<[^<>]*>|[\[\]]|[-+]?(?:\d+\.?\d*|\.\d+)|[A-Za-z*'\"]+")


def pdf_text(data):
    """The words of a simple PDF: literal strings shown by Tj, TJ, ' and \" in its content streams (plain or
    Flate). '' when they do not come out as words, or the streams unpack past SCRIPT_UNPACK."""
    # ponytail: no font maps, so hex strings and custom encodings (most Word exports) read as nothing and the
    # page asks for a paste; a real PDF parser is the upgrade if pasting turns out to be a chore
    import zlib
    esc = {b"n": b"\n", b"r": b"", b"t": b" ", b"(": b"(", b")": b")", b"\\": b"\\"}
    lit = lambda b: re.sub(rb"\\([0-7]{1,3}|.)", lambda m: bytes([int(m.group(1), 8) & 255]) if m.group(1)[:1].isdigit()
                           else esc.get(m.group(1), m.group(1)), b[1:-1], flags=re.S)
    out, left = [], SCRIPT_UNPACK
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        s = m.group(1)
        try:
            d = zlib.decompressobj()
            s = d.decompress(s, left + 1)
            if d.unconsumed_tail:
                return ""
        except zlib.error:
            pass
        left -= len(s)
        if left < 0:
            return ""
        line, args = [], []
        for t in PDF_TOKEN.findall(s):   # one pass over tokens: strings never split on an apostrophe inside them
            if t[:1] in b"(/<[]0123456789+-.":
                args.append(t)
                continue
            if t in (b"Tj", b"TJ"):
                for a in (args[args.index(b"["):] if t == b"TJ" and b"[" in args else args[-1:]):
                    if a[:1] == b"(":
                        line.append(lit(a))
                    elif a[:1] not in b"[]</" and float(a) < -200:
                        line.append(b" ")
            elif t in (b"'", b'"', b"Td", b"TD", b"T*", b"Tm", b"BT", b"ET"):
                out.append(b"".join(line).decode("latin-1").strip())
                line = [lit(args[-1])] if t in (b"'", b'"') and args and args[-1][:1] == b"(" else []
            args = []
        out.append(b"".join(line).decode("latin-1").strip())
    text = "\n".join(x for x in out if x)
    ok = sum(c.isalpha() or c.isspace() or c in ".,!?'\"-:;" for c in text)
    return text if len(text.split()) >= 3 and ok >= 0.85 * len(text) else ""


def body_len(headers):
    """Content-Length as an int; -1 when it is not a number (the caller answers 400)."""
    try:
        return int(headers.get("Content-Length") or 0)
    except ValueError:
        return -1


def script_text(name, data):
    """A script file's words as plain text: .txt and .md as they are, .docx from its paragraphs, a simple .pdf
    from its text. '' when nothing readable comes out."""
    import io
    import zipfile
    from xml.etree import ElementTree as ET
    ext = Path(name).suffix.lower()
    try:
        if ext == ".docx":
            W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            with zipfile.ZipFile(io.BytesIO(data)) as z, z.open("word/document.xml") as f:
                xml = f.read(SCRIPT_UNPACK + 1)   # the zip's declared size can lie: read at most the limit
            if len(xml) > SCRIPT_UNPACK or b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:   # old expat expands entities
                return ""
            root = ET.fromstring(xml)
            text = "\n".join("".join(e.text or "" if e.tag == W + "t" else " " if e.tag == W + "tab" else "\n"
                                      for e in p.iter() if e.tag in (W + "t", W + "tab", W + "br")) for p in root.iter(W + "p"))
        elif ext == ".pdf":
            text = pdf_text(data)
        else:
            text = data.decode("utf-8-sig")
    except (zipfile.BadZipFile, KeyError, ET.ParseError, UnicodeDecodeError, ValueError, OSError, EOFError):
        return ""
    return re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n").replace("\x00", "")).strip()


def home():
    return Path(os.environ.get("AI_EDITOR_HOME", "").strip() or Path.home() / ".ai-video-editor") / "home"


def state_path():
    return home() / "state.json"


def read():
    try:
        return load(state_path())
    except (OSError, ValueError):
        return {"root": str(library.path()), "jobs": []}


class _Lock:
    """Cross-process: the server, `wait` and `status` all write state.json. A lock file made with O_EXCL."""
    def __init__(self, wait=10):
        self.tries = int(wait / 0.025)

    def __enter__(self):
        self.p = home() / ".lock"
        self.p.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(self.tries):
            try:
                os.close(os.open(self.p, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except (FileExistsError, PermissionError):   # Windows: a lock being deleted refuses a new open
                try:   # a writer that died holding it: older than 10 s is stale
                    if time.time() - self.p.stat().st_mtime > 10:
                        self.p.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(0.025)
        raise TimeoutError(f"{self.p} is held; delete it if no home.py is running")

    def __exit__(self, *a):
        self.p.unlink(missing_ok=True)


def update(fn, wait=10):
    with _Lock(wait):
        d = read()
        out = fn(d)
        d["home_py"] = Path(__file__).resolve().as_posix()   # where progress.py finds this file
        save(state_path(), d)
        return out


FOLDER_KINDS = ("videos", "styles")


def folders():
    """folders.json: {videos|styles: {folders: [{id, name}], items: {edit dir | style id: folder id}}}."""
    try:
        d = json.loads((home() / "folders.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {k: {"folders": list((d.get(k) or {}).get("folders") or []), "items": dict((d.get(k) or {}).get("items") or {})}
            for k in FOLDER_KINDS}


def folder_change(kind, op, body, listed):
    """One folder change, validated, written atomically. listed: the items the page can file (edit dirs or style
    ids). Raises ValueError with a line the page shows."""
    if kind not in FOLDER_KINDS:
        raise ValueError("not a list")
    with _Lock(10):
        d = folders()
        k = d[kind]
        ids = {f["id"] for f in k["folders"]}
        fid = body.get("id")

        def name():
            n = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()
            if not n:
                raise ValueError("Give the folder a name.")
            if len(n) > 40:
                raise ValueError("Keep the name under 40 letters.")
            if any(f["name"].lower() == n.lower() and f["id"] != fid for f in k["folders"]):
                raise ValueError(f"There is already a folder called {n}.")
            return n
        if op != "create" and op != "move" and fid not in ids:
            raise ValueError("no such folder")
        if op == "create":
            fid = secrets.token_hex(4)
            k["folders"].append({"id": fid, "name": name()})
        elif op == "rename":
            next(f for f in k["folders"] if f["id"] == fid)["name"] = name()
        elif op == "delete":   # its items go back to All; nothing is deleted
            k["folders"] = [f for f in k["folders"] if f["id"] != fid]
            k["items"] = {i: f for i, f in k["items"].items() if f != fid}
        else:
            item, to = str(body.get("item") or ""), body.get("folder")
            if kind == "videos":
                item = str(Path(item).resolve()) if item else ""
            if item not in listed:
                raise ValueError("That is not on your list.")
            if to is not None and to not in ids:
                raise ValueError("no such folder")
            if to is None:
                k["items"].pop(item, None)
            else:
                k["items"][item] = to
        home().mkdir(parents=True, exist_ok=True)
        save(home() / "folders.json", d)
        return {"ok": True, "id": fid, "folders": d}


def link_set(vids):
    """Pasted video links as one study: the creator when every link names the same one, the app they are on
    (instagram when any is, since it ranks by likes), and a plain note."""
    handles = {i.get("handle") for i in vids}
    plats = {i.get("platform") for i in vids}
    plat = "instagram" if "instagram" in plats else plats.pop() if len(plats) == 1 else "tiktok"
    n = len(vids)
    note = f"{n} video link{'s' if n > 1 else ''}." + (" 6 or more gives a truer style." if n < 6 else "") \
        + (" Instagram ranks by likes, not views." if plat == "instagram" else "")
    h = handles.pop() if len(handles) == 1 else None
    return {"kind": "creator", "platform": plat, "urls": [i["url"] for i in vids],
            "handle": re.sub(r"[^a-z0-9._-]", "", h.lower()) if h else None, "note": note}


def latest(d, kind):
    return next((j for j in reversed(d["jobs"]) if j["type"] == kind), None)


RESERVE_S = 180   # a job the page opened a new Claude for: other waits leave it alone this long (home.html too)
OPENED_S = 900    # ... and this long once its Terminal window opened (a trust prompt can hold it there)
STALE_S = 900     # a taken job nobody reported on this long: its session stopped, another may adopt it


def job_name(j):
    """What a job is about, in a few words: the video's edit folder name, the creator, the style."""
    a = j.get("args") or {}
    if a.get("edit"):
        return Path(a["edit"]).name
    if a.get("handle"):
        return "@" + a["handle"]
    return {"style-change": "your style", "modal": "cloud renders", "modal-test": "a test render"}.get(j["type"], j["type"])


def stale(j):
    """A taken job whose session stopped reporting STALE_S ago."""
    return j["state"] == "taken" and time.time() - (j.get("u") or 0) > STALE_S


def may_write(j, sid):
    """True when session sid may write to job j: it took it, nobody did, sid is unknown (outside Claude), or
    its session stopped (stale: sid adopts it)."""
    return not sid or not j.get("owner") or j["owner"] == sid or stale(j)


def adopt(j, sid):
    """sid takes over a stale job of a stopped session. A line to print, or None."""
    if sid and j.get("owner") and j["owner"] != sid:
        j["owner"] = sid
        return f"adopted {j['id']}: the Claude session that had it stopped reporting {STALE_S // 60} minutes ago"
    return None


def asks_for(sid):
    """askhook's owner rule for the home page: the job sid is working on, "" when it only listens, else None.
    With no session known at all (Claude Code without CLAUDE_CODE_SESSION_ID): any question, as before."""
    d = read()
    taken = [j for j in reversed(d["jobs"]) if j["state"] == "taken"]
    j = next((j for j in taken if sid and j.get("owner") == sid), None)
    if j:
        return job_name(j)
    lis = listener()
    if sid and sid == lis:
        return ""
    if not lis and not any(j.get("owner") for j in taken):
        return job_name(taken[0]) if taken else ""
    return None


def asks_dir(sid):
    """Where an answer's images are saved: the asking session's edit folder (<edit>/asks) once it exists, else
    $AI_EDITOR_HOME/asks."""
    j = next((j for j in reversed(read()["jobs"]) if sid and j["state"] == "taken" and j.get("owner") == sid), None)
    e = Path((j or {}).get("args", {}).get("edit") or "")
    return e / "asks" if str(e) != "." and e.is_dir() else home().parent / "asks"


def listening():
    try:
        return time.time() - (home() / ".listening").stat().st_mtime < BEAT_S
    except OSError:
        return False


def listener():
    """The session of the `wait` that is listening now ("" when it has none, None when nobody listens)."""
    if not listening():
        return None
    try:
        return (home() / ".listening").read_text(encoding="utf-8").strip()
    except OSError:
        return None


# ---------------------------------------------------------------- creator: listing and measured numbers
def fetch_py():
    """creator-teardown's fetch.py: beside this plugin in the repo, or in the plugin cache."""
    env = os.environ.get("AI_EDITOR_FETCH_PY")
    here = Path(__file__).resolve()
    cands = ([env] if env else []) + [str(here.parents[3] / "creator-teardown" / "skills" / "creator-teardown" / "scripts" / "fetch.py")] \
        + sorted(glob.glob(str(here.parents[4] / "creator-teardown" / "*" / "skills" / "creator-teardown" / "scripts" / "fetch.py")))
    return next((Path(c) for c in cands if c and Path(c).is_file()), None)


def reach(v):
    return v.get("view_count") or v.get("like_count") or 0


def picks(vids, median, pasted=False):
    """What quick mode studies, as fetch.py pick: the TOP most viewed, SPARE more for its format check, then the
    CONTROL closest to the median, all of the creator's usual length (within 2x of the median) when enough are.
    Pasted links: every one."""
    ds = [v["duration"] for v in vids if v.get("duration")]
    if not pasted and len(ds) >= max(3, len(vids) / 2):
        m = statistics.median(ds)
        usual = [v for v in vids if v.get("duration") and m / 2 <= v["duration"] <= m * 2]
        vids = usual if len(usual) >= TOP + CONTROL + SPARE else vids
    ranked = sorted(vids, key=reach, reverse=True)
    if pasted:
        return [dict(v, role=f"Top {i + 1}") for i, v in enumerate(ranked)]
    ctrl = sorted((v for v in ranked[TOP:] if reach(v)), key=lambda v: abs(reach(v) - median))[:CONTROL]
    spare = [v for v in ranked[TOP:] if v not in ctrl][:SPARE]   # the control first: spares give way on a small profile
    return [dict(v, role=f"Top {i + 1}") for i, v in enumerate(ranked[:TOP])] + \
        [dict(v, role=f"Top {TOP + i + 1}, spare") for i, v in enumerate(spare)] + \
        [dict(v, role="Control, median") for v in ctrl]


def listing(root, handle):
    """videos.json (written by fetch.py list) -> what H2 and H3 show. None when not listed."""
    try:
        d = json.loads((Path(root) / "creator-teardowns" / handle / "videos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    keep = ("id", "title", "duration", "view_count", "like_count", "vs_median", "thumbnail", "webpage_url")
    return {"handle": d.get("handle"), "platform": d.get("platform"), "count": d.get("count"),
            "median": d.get("median_views"), "ranked_by": d.get("ranked_by"),
            "videos": [{k: v.get(k) for k in keep + ("role",)} for v in picks(d.get("videos") or [], d.get("median_views") or 0, d.get("pasted"))]}


def run_listing(root, handle, platform):
    """fetch.py list in the background (yt-dlp --flat-playlist: no downloads, no key, a few seconds)."""
    f = fetch_py()
    if not f:
        return update(lambda d: d.__setitem__("lookup", {**d.get("lookup", {}), "status": "nofetch"}))
    try:
        r = subprocess.run([sys.executable, str(f), "list", handle, "--platform", platform, "--limit", "40"],
                           cwd=root, capture_output=True, text=True, timeout=120)
        ok = r.returncode == 0
        msg = "" if ok else (r.stderr.strip().splitlines() or ["yt-dlp returned nothing"])[-1][:200]
    except (OSError, subprocess.TimeoutExpired) as e:
        ok, msg = False, f"listing stopped: {e}"[:200]

    def put(d):
        if (d.get("lookup") or {}).get("handle") == handle:
            d["lookup"].update(status="ok" if ok else "failed", msg=msg)
    update(put)


def num(x, unit=""):
    return None if x is None else f"{x:g}{unit}" if isinstance(x, (int, float)) else str(x)


def measured(root, handle):
    """Plain lines from creator-teardowns/<handle>/style.json. Each part is there only once a script
    measured it, so the page fills in as the teardown runs. No number here comes from anywhere else."""
    try:
        s = json.loads((Path(root) / "creator-teardowns" / handle / "style.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"tiles": [], "rows": {}, "videos": None}
    p, z, c, g = (s.get(k) or {} for k in ("pace", "zoom", "captions", "graphics"))
    snd, hook, win = s.get("sound") or {}, (s.get("hook") or {}).get("winner") or {}, s.get("winners") or {}
    tiles, rows = [], {}
    if p.get("median_shot_s") is not None:
        tiles.append({"big": num(p["median_shot_s"], " s"), "label": "between cuts"})
    if z.get("kind") and z.get("per_min"):
        tiles.append({"big": num(z["scale"], "x"), "label": f"{z['kind']} zoom, {num(z['per_min'])} a minute"} if z.get("scale")
                     else {"big": num(z["per_min"]), "label": f"{z['kind']} zooms a minute"})
    if hook.get("first_word_s") is not None:
        tiles.append({"big": num(hook["first_word_s"], " s"), "label": "to the first word"})
    if p.get("wpm"):
        tiles.append({"big": num(p["wpm"]), "label": "words a minute"})
    if p:
        rows["pace"] = " ".join(x for x in (
            p.get("median_shot_s") is not None and f"A cut every {num(p['median_shot_s'])} s.",
            p.get("max_pause_s") is not None and f"Pauses top out at {num(p['max_pause_s'])} s.",
            p.get("wpm") and f"{num(p['wpm'])} words a minute.") if x)
    if z:
        rows["zooms"] = ("No zooms." if not z.get("kind") else
                         f"{z['kind'].capitalize()} zooms" + (f", {num(z['per_min'])} a minute" if z.get("per_min") else "")
                         + (f", to {num(z['scale'])}x" if z.get("scale") else "")   # a style without a scale: no "to Nonex"
                         + (f", {z['ease']}" if z.get("ease") and z["ease"] != "none" else "") + ".")
    if c:
        if c.get("present") is False:
            rows["captions"] = "No captions."
        elif c.get("words_per_caption") is not None:
            wpc = c["words_per_caption"]
            rows["captions"] = " ".join(x for x in (
                f"{num(wpc)} word{'' if wpc == 1 else 's'} at a time"
                + (f", {num(round(c['y_pct']))}% down the frame." if c.get("y_pct") is not None else "."),
                c.get("case") and f"{c['case'].capitalize()} case" + (f", weight {c['weight']}" if c.get("weight") else "") + ".",
                c.get("color") and f"{c['color']}" + (f" on a {c['box_color']} box" if c.get("box") else "") + ".",
                c.get("entrance") and f"Enters: {c['entrance']}.") if x)
    gpm, gsh = (g.get(k + "_measured", g.get(k)) for k in ("per_min", "share_pct"))   # graphics.py's count wins, as in look.md
    if gpm is not None or gsh is not None:
        rows["graphics"] = " ".join(x for x in (
            gpm is not None and f"{num(gpm)} graphics a minute.",
            gsh is not None and f"On screen {num(gsh)}% of the time.") if x)
    if snd:
        m = (snd.get("music") or {}).get("present_pct")
        rows["sound"] = " ".join(x for x in (
            snd.get("sfx_per_min") is not None and f"{num(snd['sfx_per_min'])} sound effects a minute.",
            m is not None and f"Music in {num(m)}% of videos.") if x)
    if hook:
        rows["hook"] = " ".join(x for x in (
            hook.get("first_word_s") is not None and f"First word at {num(hook['first_word_s'])} s.",
            hook.get("first_caption_s") is not None and f"Caption at {num(hook['first_caption_s'])} s.",
            hook.get("first_cut_s") is not None and f"First cut at {num(hook['first_cut_s'])} s.") if x)
    if win.get("differs"):
        rows["winners"] = " ".join(win["differs"][:2]) + \
            f" ({win.get('n_winners')} top against {win.get('n_control')} median: a lead, not proof.)"
    rows = {k: v for k, v in rows.items() if v}
    return {"tiles": tiles, "rows": rows, "videos": s.get("videos"),
            "report": (Path(root) / "creator-teardowns" / handle / "teardown.html").exists()}


# ---------------------------------------------------------------- styles: the "Pick a style" cards
def place(p):
    """A folder as the page shows it: under the home folder by name ("Movies › AI Editor"), else in full."""
    p = Path(p)
    try:
        return " › ".join(p.relative_to(Path.home()).parts) or str(p)
    except ValueError:
        return str(p)


def facts(s):
    """[[label, value]] measured in a style.json, at most three. A part not measured is left out."""
    p, z, c = (s.get(k) or {} for k in ("pace", "zoom", "captions"))
    out = []
    if p.get("median_shot_s") is not None:
        out.append(["Cut every", num(p["median_shot_s"], " s")])
    if z.get("kind"):
        out.append(["Zooms", z["kind"].capitalize() + (f", {num(z['per_min'])} a minute" if z.get("per_min") else "")])
    if c.get("present") is False:
        out.append(["Captions", "None"])
    elif c.get("words_per_caption") is not None:
        out.append(["Captions", f"{num(c['words_per_caption'])} word{'' if c['words_per_caption'] == 1 else 's'} at a time"])
    m = ((s.get("sound") or {}).get("music") or {}).get("present_pct")
    if m is not None and len(out) < 3:
        out.append(["Music", f"In {num(m)}% of videos"])
    return out[:3]


def look(s):
    """How the caption sample on a card is drawn: only what the style.json says."""
    c = s.get("captions") or {}
    if c.get("present") is False:
        return None
    return {k: c.get(k) for k in ("color", "box", "box_color", "case", "weight", "y_pct", "stroke")}


def _taste():
    """taste.py and styleskill.py, imported late so AI_EDITOR_HOME set by a self-check is the one they use."""
    sys.path.insert(1, str(Path(__file__).resolve().parents[1]))
    from ai_editor import styleskill, taste
    return taste, styleskill


def _profile():
    try:
        return json.loads((home().parent / "profile.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def style_of(root, sid):
    """(style.json path or None for My style, the style as edits use it: measured < corrections)."""
    taste, ss = _taste()
    if sid == "mine":
        have = dict(library.styles(root))
        base = next((c["handle"] for c in _profile().get("creators") or [] if c.get("handle") in have), None)
        from ai_editor.profile import DEFAULT_STYLE
        return None, taste.merge(have[base] if base else DEFAULT_STYLE, taste.read_json()), base
    h = re.sub(r"[^a-z0-9._-]", "", str(sid).lstrip("@").lower())
    p = Path(root) / "creator-teardowns" / h / "style.json"
    if not h or not p.is_file():
        raise KeyError(sid)
    sp = ss.preferred(p)
    return sp, taste.merge(json.loads(sp.read_text(encoding="utf-8") or "{}"), ss.load_overrides(sp)), h


def suggest_name(sid, base):
    """The name a style's field falls back to: My style "Like @<creator>", else the user's login + " style";
    a creator's style "@<handle> style" (so the two never claim one skill name)."""
    if sid != "mine":
        return f"@{base} style"[:40]
    if base:
        return f"Like @{base}"[:40]
    import getpass
    try:
        who = re.sub(r"[^A-Za-z0-9._-]", "", getpass.getuser())[:33]
    except Exception:
        who = ""
    return f"{who} style" if len(who) >= 2 else "My style"


def naming(sid, sp, base):
    """named: the user gave (or kept) a name; suggest: what the name field offers until they do."""
    taste, ss = _taste()
    return {"named": bool(ss.names().get(ss.key(sp), {}).get("name")), "suggest": suggest_name(sid, base)}


def skill_of(st):
    """The skill a skill style names (style.json "skill"), when it is a usable skill name, else None."""
    v = st.get("skill") if isinstance(st, dict) else None
    return v if isinstance(v, str) and re.fullmatch(r"[a-z0-9][a-z0-9:_-]{0,63}", v) else None


def skill_dirs(root):
    """Where Claude Code loads skills from, as far as the page can see (read only): $AI_EDITOR_SKILLS, the library's and
    the working folder's .claude/skills, ~/.claude/skills, then the plugins' own skills."""
    env = [Path(x) for x in os.environ.get("AI_EDITOR_SKILLS", "").split(os.pathsep) if x]
    return env + [Path(root) / ".claude" / "skills", Path.cwd() / ".claude" / "skills", Path.home() / ".claude" / "skills",
                  *sorted(Path(__file__).resolve().parents[3].glob("*/skills"))]


def find_skill(root, name, calls=False):
    """A skill style's skill: found, the first sentence of its description and, with calls, the other skills its
    SKILL.md names (hyphenated or in backticks, and only names of skills that exist here)."""
    dirs = skill_dirs(root)
    name = name.split(":")[-1]   # a plugin skill (plugin:skill) lives in <plugin>/skills/<skill>
    md = next((d / name / "SKILL.md" for d in dirs if (d / name / "SKILL.md").is_file()), None)
    if not md:
        return {"found": False, "description": "", "calls": [], "path": None}
    text = md.read_text(encoding="utf-8", errors="replace")
    head, body = (text.split("---", 2)[1:] + [""])[:2] if text.startswith("---") else ("", text)
    m = re.search(r"^description:[ \t]*(.*(?:\n[ \t]+.*)*)", head, re.M)
    desc = re.sub(r"^[>|][-+]?\s*", "", " ".join((m.group(1) if m else "").split())).strip("'\"")
    out = {"found": True, "description": re.split(r"(?<=[.!?])\s", desc, maxsplit=1)[0][:240], "calls": [], "path": str(md)}
    if calls:
        known = {q.parent.name for d in dirs for q in d.glob("*/SKILL.md")} - {name}
        said = re.findall(r"`/?([a-z0-9][a-z0-9-]*)`|\b([a-z0-9]+(?:-[a-z0-9]+)+)\b", body)
        out["calls"] = [x for x in dict.fromkeys(a or b for a, b in said) if x in known][:8]
    return out


def editing_skills(root):
    """The skills a style can run ("Use your own skill"): every skill folder the page can see outside this plugin whose
    name or description is about editing video. [{"name", "description"}], first found wins."""
    plugins = Path(__file__).resolve().parents[3]
    _, ss = _taste()
    ours = {ss.MINE} | {v.get("skill") for v in ss.names().values()}   # the editor's own thin style skills
    out = {}
    for d in skill_dirs(root):
        if d.resolve().parent.parent == plugins.resolve():   # the editor's own skills are not styles
            continue
        for md in sorted(d.glob("*/SKILL.md")):
            n = md.parent.name
            if n not in out and n not in ours and not n.startswith("edit-like-") and skill_of({"skill": n}):
                f = find_skill(root, n)
                # ponytail: keyword match on name + first sentence; a skill can opt in by naming "video" or "edit"
                if re.search(r"\b(edits?|editing|editor|videos?|reels?|footage|clips?)\b", n.replace("-", " ") + " " + f["description"], re.I):
                    out[n] = {"name": n, "description": f["description"]}
    return list(out.values())


def style_cards(root):
    """My style first (the profile's first copied creator, else the default style, with the user's
    corrections), then every creator studied in the library (with that style's corrections), then the
    plugin's sample styles. Each card
    names its skill."""
    taste, ss = _taste()
    out = []
    studied = sorted(library.styles(root), key=lambda hs: bool(hs[1].get("sample")))   # the plugin's samples last
    for sid in ["mine"] + ["@" + h for h, _ in studied if not h.endswith("-mine")]:
        try:
            sp, st, base = style_of(root, sid)
        except (KeyError, OSError, ValueError):
            continue
        sc = ss.scope(sp)
        mine = [r for r in taste.load_rules() if r["scope"] == sc] if taste.rules_path().exists() else []
        n = len(mine)
        vids = len({c["edit"] for r in mine for c in r["corrections"] if c.get("edit")})   # edits it learned from
        if sid == "mine":
            line = (f"Your saved style. Started from @{base}." if base else "The default style.") + \
                (f" {n} change{'s' if n != 1 else ''} from your feedback." if n else " Change it any time.")
        else:
            line = (st.get("about") if st.get("sample") else f"Measured from {st['videos']} of their videos." if st.get("videos")
                    else "Copied by the editor.") + (f" {n} change{'s' if n != 1 else ''} from your feedback." if n else "")
        # when it was made (a creator: their listing, the first thing a copy writes) and last changed (feedback rules)
        made = next((f.stat().st_mtime for f in (sp.parent / "videos.json", sp) if f.is_file()), None) if sp else None
        changed = taste.rules_path().stat().st_mtime if n and taste.rules_path().exists() else None
        sk = skill_of(st) if sp else None   # a skill style: making a video runs that skill, no settings of ours
        out.append({"id": sid, "name": ss.display(sp), "facts": [] if sk else facts(st), "look": look(st), "base": base,
                    "videos": st.get("videos"), "line": line, "skill": ss.name(sp), "changes": n, "learned_from": vids, "made": made, "changed": changed, "cover": cover_of(root, sid), "face": face_of(root, base),
                    "own": sid == "mine" or bool(st.get("own")),
                    "captions_off": sid == "mine" and (_profile().get("captions") or {}).get("on") is False,
                    "sample": bool(st.get("sample")), **naming(sid, sp, base), "kind": "skill" if sk else "settings"})
        if sk:
            f = find_skill(root, sk)
            out[-1].update(skill=sk, found=f["found"], description=f["description"])
    return out


# The style page's controls: the settings the scripts read (taste skill's table), never a look of their own. The card
# look is not one: it is part of the style (measured, or built by the user), so no page default can override it.
# (section, type, low, high); "choice" keys take one of the names presets.py lists.
CONTROLS = {"captions.size_pct": ("captions", float, 2.5, 9), "captions.words_per_caption": ("captions", int, 1, 6),
            "captions.y_pct": ("captions", float, 40, 85), "captions.preset": ("captions", "choice", None, None),
            "zoom.per_min": ("visuals", float, 0, 20), "zoom.preset": ("visuals", "choice", None, None),
            "cut.max_pause": ("cut", float, 0.05, 0.6), "cards.on": ("visuals", bool, None, None),
            "cards.per_min": ("visuals", float, 1, 20),
            "sfx": ("sound", bool, None, None), "music": ("sound", bool, None, None),
            "motion.amount": ("visuals", float, 0, 1), "motion.look": ("visuals", "choice", None, None),
            **{f"motion.kinds.{k}": ("visuals", bool, None, None) for k in ("counter", "list", "flow", "before_after", "ui_demo", "curve", "logo")}}
MOTION_KINDS = {"counter": "Counters", "list": "Lists and steps", "flow": "Flows", "before_after": "Before and after",
                "ui_demo": "The app in use", "curve": "Curves", "logo": "Logos together"}
# Reset per section: every setting under these prefixes goes back to the style's own (or the default).
SECTIONS = {"captions": ("captions.",), "zoom": ("zoom.",), "cut": ("cut.",), "cards": ("cards.", "look."), "sound": ("sfx", "music"),
            "motion": ("motion.",)}   # look.: clears an old pick


def choices(key):
    return {"captions.preset": [""] + list(presets.captions()), "zoom.preset": list(presets.ZOOM),
            "motion.look": ["default", *motion_mod().PRESETS]}[key]


def control_rule(key, v):
    if key == "captions.preset":
        r = f"Captions: the {presets.label(v)} preset" if v else "Captions: the creator's own"
    elif key == "zoom.preset":
        r = f"Zooms: {presets.ZOOM[v]['label']} ({presets.ZOOM[v]['sub'].lower()})"
    elif key == "cards.per_min":
        r = f"Cards {presets.CARDS[presets.nearest(presets.CARDS, 'per_min', v)]['label'].lower()}, at most {v:g} a minute"
    else:
        r = {"captions.size_pct": lambda: f"Captions {v:g}% of the frame tall",
             "captions.words_per_caption": lambda: f"{v} word{'s' if v != 1 else ''} a caption",
             "captions.y_pct": lambda: f"Captions {v:g}% down the frame",
             "zoom.per_min": lambda: "No zooms" if not v else f"About {v:g} zooms a minute",
             "cut.max_pause": lambda: f"Pauses cut down to {v:g} s", "sfx": lambda: "Sound effects on" if v else "No sound effects",
             "cards.on": lambda: "Logos and screenshots on" if v else "No logos or screenshots",
             "music": lambda: "Background music on" if v else "No background music",
             "motion.amount": lambda: "No animations" if not v else f"Animations: {'lots' if v >= 0.75 else 'some'} of the beats",
             "motion.look": lambda: f"Animations in the {v} look"}.get(key, lambda: (
                 f"{MOTION_KINDS[key.rsplit('.', 1)[1]]} animated" if v else f"No {MOTION_KINDS[key.rsplit('.', 1)[1]].lower()} animations"))()
    return r + " (set on the style page)"


def motion_mod():
    """The motion skill's motion.py (its config, looks and library)."""
    import importlib.util
    if "motion" not in sys.modules:
        f = Path(__file__).resolve().parents[2] / "skills" / "motion" / "scripts" / "motion.py"
        spec = importlib.util.spec_from_file_location("motion", f)
        m = importlib.util.module_from_spec(spec)
        sys.modules["motion"] = m
        spec.loader.exec_module(m)
    return sys.modules["motion"]


def motion_state(st, changed):
    """The style page's Motion section: how many beats become clips, which kinds, the look (palette and font, with
    what a preview tile needs), and the saved clips (the library)."""
    mm = motion_mod()
    cfg = mm.motion_cfg(st)
    looks = [{"id": k, **{t: v for t, v in {**mm.DEFAULT_LOOK, **({} if k == "default" else mm.PRESETS[k])}.items()
                          if t in ("ground", "ink", "accent", "font")}} for k in ["default", *mm.PRESETS]]
    lib = [{**{k: e.get(k) for k in ("id", "kind", "line", "style", "look", "made", "archived", "use_next")},
            "kind_name": MOTION_KINDS.get(e.get("kind"), e.get("kind")), "thumb": f"/motion/thumb?id={e['id']}"}
           for e in mm.lib_entries()]
    return {"amount": cfg["amount"], "kinds": cfg["kinds"], "look": cfg["look"], "engine": cfg["engine"], "looks": looks,
            "names": MOTION_KINDS, "changed": any(k.startswith("motion.") and dig(changed, k) is not None for k in CONTROLS),
            "library": lib}


def dig(d, key):
    for k in key.split("."):
        d = d.get(k) if isinstance(d, dict) else None
    return d


def newest_edit(need=("words.json",), entries=None):
    """The newest edit folder in the library holding all of `need`, else None."""
    return next((Path(e["dir"]) for e in (library.entries() if entries is None else entries)
                 if all((Path(e["dir"]) / f).is_file() for f in need)), None)


def preview_source(root, sid):
    """(picture for the previews, what it is): the style's chosen image, else the user's newest raw take (a video, never
    a finished render or an audio file), else a demo photo."""
    d = style_dir(root, sid)
    if d and (d / "cover.jpg").is_file():
        return d / "cover.jpg", "Your image"
    eds = [Path(e).resolve() for e in library.edit_dirs()]
    raw = next((Path(r["path"]) for r in recent_list(read()) if Path(r["path"]).suffix.lower() in links.VIDEO_EXT
                and Path(r["path"]).is_file() and not is_output(Path(r["path"]).resolve(), eds)), None)
    return (raw, "Your video") if raw else (DEMO / "person-1.jpg", "Example photo")


def preset_state(root, sid, st, base, changed):
    """The style page's preset sections: which tile is on in each, where it came from, and the caption stills
    (real renders, made in the background on the first ask). The tiles are rendered at each preset's own height, so
    dragging the caption re-renders only the big preview."""
    prof = _profile()
    tag = lambda prefixes, measured: "you changed this" if any(k.startswith(prefixes) and dig(changed, k) is not None for k in CONTROLS) \
        else f"from {creator.get('name') or '@' + base}" if base and measured else "default"
    creator = {}
    if base:
        try:
            creator = json.loads((Path(root) / "creator-teardowns" / base / "style.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            creator = {}
    from ai_editor.profile import DEFAULT_STYLE
    caps = {k: v for k, v in (st.get("captions") or {}).items() if k != "preset"}
    own_y = (creator.get("captions") or DEFAULT_STYLE["captions"]).get("y_pct") or 66   # the style's height, before the user's
    size = dig(changed, "captions.size_pct")
    user_y = dig(changed, "captions.y_pct")
    src, what = preview_source(root, sid)
    entries = library.entries()
    ws, real = presets.phrase(newest_edit(entries=entries))
    blocks = [(name, presets.label(name), {**caps, **block}) for name, block in presets.captions().items()]
    if base and (creator.get("captions") or {}).get("present") is not False and creator.get("captions"):
        blocks.insert(0, ("", "Theirs" if sid != "mine" else creator.get("name") or f"@{base}'s", dict(caps)))
    tiles = []
    for tid, name, full in blocks:
        full = {**full, "y_pct": full.get("y_pct") if tid else own_y, **({"size_pct": size} if size is not None else {}), "present": True}
        if tid and "y_pct" not in presets.caption(tid):
            full["y_pct"] = own_y
        _, img, err = presets.tile(full, ws, real, src)
        tiles.append({"id": tid, "name": name, "note": presets.note(full, caps.get("words_per_caption")), "img": img,
                      "err": bool(err), "block": {k: full.get(k) for k in ("size_pct", "y_pct")}, "_full": full})
    ids = [t["id"] for t in tiles]
    cp = dig(changed, "captions.preset")
    answer = (prof.get("captions") or {}).get("style")
    sel = cp if cp in ids else answer if sid == "mine" and answer in ids and answer != "creator" else "" if "" in ids else "plain"
    pick = next(t for t in tiles if t["id"] == sel)
    y = user_y or pick["block"]["y_pct"] or 66
    _, big, big_err = presets.tile({**pick["_full"], "y_pct": y}, ws, real, src) if user_y else (None, pick["img"], pick["err"])
    for t in tiles:
        t.pop("_full")
    zp = dig(changed, "zoom.preset")
    zsel = zp if zp in presets.ZOOM else presets.closest_zoom(st.get("zoom"))
    cut = dig(st, "cut.max_pause") or 0.15
    cpm = dig(st, "cards.per_min")   # what caps the cards (plan.py thin_cards): the user's pick, or the default style's 4
    rate = cpm if cpm is not None else dig(st, "graphics.per_min")   # shown: the cap, else the style's measured rate
    sfx = (prof.get("sound") or {}).get("sfx", True) if sid == "mine" and st.get("sfx") is None else st.get("sfx", True)
    music = st["music"] if "music" in st else bool((prof.get("sound") or {}).get("music"))
    clips = newest_edit(("report.json", "words.raw.json"), entries)
    return {"pic": what,
            "captions": {"tiles": tiles, "sel": sel, "tag": tag(("captions.",), sel == ""), "size": pick["block"]["size_pct"] or 4.2,
                         "y": y, "big": big, "big_err": bool(big_err), "off": caps.get("present") is False},
            "zoom": {"opts": [{"id": k, "label": v["label"], "sub": v["sub"]} for k, v in presets.ZOOM.items()], "sel": zsel,
                     "tag": tag(("zoom.",), bool(creator.get("zoom")))},
            "cut": {"opts": [{"id": k, "label": v["label"], "sub": v["sub"], "value": v["max_pause"]} for k, v in presets.CUT.items()],
                    "sel": presets.nearest(presets.CUT, "max_pause", cut), "value": cut, "tag": tag(("cut.",), False),
                    "clips": bool(clips)},
            "cards": {"on": dig(st, "cards.on") is not False, "opts": [{"id": k, "label": v["label"], "value": v["per_min"]} for k, v in presets.CARDS.items()],
                      "sel": presets.nearest(presets.CARDS, "per_min", rate) if rate is not None else "",
                      "tag": tag(("cards.",), False)},
            "sound": {"sfx": bool(sfx), "music": bool(music), "tag": tag(("sfx", "music"), False)}}


def style_page(root, sid):
    """Everything the style page shows: the settings now (measured < corrections), which ones the user
    changed, what the style copies, its skill and the rules learned at its scope."""
    taste, ss = _taste()
    sp, st, base = style_of(root, sid)
    changed = ss.load_overrides(sp) if sp else taste.read_json()
    sc = ss.scope(sp)
    rules = [{"id": r["id"], "rule": r["rule"], "setting": r.get("setting"), "value": r.get("value"),
              "said": len(r["corrections"]), "last": (r["corrections"][-1]["at"][:10] if r["corrections"] else None),
              "applied": r.get("applied", 0), "regressions": len(r.get("regressions") or [])}
             for r in (taste.load_rules() if taste.rules_path().exists() else []) if r["scope"] == sc]
    sk = skill_of(st) if sp else None
    if sk:   # a skill style: its skill does the edit, so the page shows the skill and no controls
        return {"id": sid, "kind": "skill", "name": ss.display(sp), "base": base, "rules": rules, "style_path": str(sp),
                "skill": {"name": sk, **find_skill(root, sk, calls=True)}, "cover": cover_of(root, sid), "face": face_of(root, base),
                "own": bool(st.get("own")), "videos": st.get("videos"), "sample": bool(st.get("sample")),
                "references": references(sp.parent) if not st.get("own") else {"videos": [], "profile": None},
                "made": next((f.stat().st_mtime for f in (sp.parent / "videos.json", sp) if f.is_file()), None),
                "job": None, **naming(sid, sp, base)}
    md = ss.skills_dir() / ss.name(sp) / "SKILL.md"
    prof = _profile()
    vals = {k: dig(st, k) for k in CONTROLS}
    if vals["cut.max_pause"] is None:
        vals["cut.max_pause"] = 0.15   # the cut's default (taste skill's table)
    if vals["sfx"] is None:
        vals["sfx"] = (prof.get("sound") or {}).get("sfx", True) if sp is None else st.get("sfx", True)
    copies = ss.copies(st) if sp else [f"@{base}'s style" if base else "the default style", "your corrections"]
    job = next((j for j in reversed(read()["jobs"]) if j["type"] == "style-change" and j["args"].get("style") == sid), None)
    return {"id": sid, "name": ss.display(sp), "use_for": ss.use_for(sp), "base": base, "values": vals, "sample": bool(st.get("sample")),
            "changed": [k for k in CONTROLS if dig(changed, k) is not None], "look": look(st), "facts": facts(st),
            "motion": motion_state(st, changed),
            "copies": copies, "rules": rules, "style_path": str(sp) if sp else None, "presets": preset_state(root, sid, st, base, changed),
            "skill": {"name": ss.name(sp), "path": str(md), "exists": md.exists()}, "job": job, "cover": cover_of(root, sid), "face": face_of(root, base),
            "own": sid == "mine" or bool(st.get("own")), "videos": st.get("videos"),
            "references": references(sp.parent) if sp and not st.get("own") else {"videos": [], "profile": None},
            "made": next((f.stat().st_mtime for f in (sp.parent / "videos.json", sp) if f.is_file()), None) if sp else None,
            "delete_note": f"My style started from @{base}. It goes back to the default style." if sp and base and style_of(root, "mine")[2] == base else None,
            **naming(sid, sp, base)}


def forget_where(sp, keep):
    """Forget every setting rule at this style's scope whose setting `keep` rejects (taste.forget unsets it too)."""
    taste, ss = _taste()
    if taste.rules_path().exists():
        for r in [r for r in taste.load_rules() if r["scope"] == ss.scope(sp) and r.get("setting") and not keep(r["setting"])]:
            taste.forget(r["id"])


def set_control(root, sid, key, value):
    """One change on the style page: a correction at that style's scope, through styleskill/taste, so the
    overrides file (or taste.json for My style) and the skill's "Learned from your feedback" both update."""
    taste, ss = _taste()
    sp, _, _ = style_of(root, sid)
    section, typ, lo, hi = CONTROLS[key]
    if typ == "choice":
        v = str(value if value is not None else "")
        if v not in choices(key):
            raise ValueError(f"{key} must be one of {', '.join(c or '(the creator own)' for c in choices(key))}")
    else:
        v = bool(value) if typ is bool else typ(value)
    if lo is not None and not lo <= v <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    # a preset replaces the older single settings it covers, so they cannot quietly win over it
    if key == "zoom.preset":
        forget_where(sp, lambda k: not (k.startswith("zoom.") and k != "zoom.preset"))
    if key == "captions.preset":
        forget_where(sp, lambda k: k != "captions.words_per_caption")
    return ss.learn(sp, section, control_rule(key, v), key, v)


def reset_section(root, sid, section):
    """Every setting of one section back to the style's own: its rules forgotten, any loose key unset."""
    taste, ss = _taste()
    sp, _, _ = style_of(root, sid)
    pre = SECTIONS[section]
    forget_where(sp, lambda k: not k.startswith(pre))
    for k in CONTROLS:
        if k.startswith(pre):
            if sp:
                ss.set_override(sp, k, drop=True)
            else:
                taste.save_json(taste.unset_key(taste.read_json(), k))
    ss.write(str(sp) if sp else None)


def reset_mine():
    """My style back to the default: every feedback rule at its scope forgotten, taste.json and taste.md
    emptied, the my-style skill rewritten. My style itself stays (every video uses it)."""
    taste, ss = _taste()
    if taste.rules_path().exists():
        for r in [r for r in taste.load_rules() if r["scope"] == ss.scope(None)]:
            taste.forget(r["id"])
    taste.save_json({})
    taste.save_md({s: [] for s in taste.SECTIONS})
    ss.write(None)


_UPD = {"at": 0.0, "latest": None, "notes": [], "busy": False}


def vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:3])


def fetch_latest():
    """The version on GitHub main and the first lines of its CHANGELOG entry. (None, []) offline."""
    try:
        v = json.loads(urllib.request.urlopen(RAW + "plugins/ai-editor/.claude-plugin/plugin.json", timeout=5).read())["version"]
        log = urllib.request.urlopen(RAW + "CHANGELOG.md", timeout=5).read().decode("utf-8", "replace")
    except (OSError, ValueError, KeyError, TypeError):
        return None, []
    secs = re.split(r"(?m)^## ", log)
    sec = next((x for x in secs if re.match(rf"ai-editor {re.escape(str(v))}\b", x)), secs[1] if len(secs) > 1 else "")
    bullets = [" ".join(b.split()) for b in re.findall(r"(?ms)^- (.*?)(?=^- |^\S|\Z)", sec)]
    notes = [re.split(r"(?<=\.) ", re.sub(r"[*`]", "", b))[0] for b in bullets][:3]   # each one's first sentence
    return v, notes


def update_info():
    """Installed vs latest. The GitHub check runs in the background at most once an hour; offline it says nothing."""
    if time.time() - _UPD["at"] > 3600 and not _UPD["busy"]:
        _UPD["busy"] = True

        def go():
            v, notes = fetch_latest()
            _UPD.update(at=time.time(), busy=False, **({"latest": v, "notes": notes} if v else {}))
        threading.Thread(target=go, daemon=True).start()
    try:
        mine = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    except (OSError, ValueError, KeyError):
        mine = None
    newer = bool(mine and _UPD["latest"] and vtuple(_UPD["latest"]) > vtuple(mine))
    return {"installed": mine, "latest": _UPD["latest"], "newer": newer, "notes": _UPD["notes"] if newer else [],
            "checking": _UPD["busy"], "checked": _UPD["at"] or None}


def trash(p):
    """To the Trash (Finder, Recycle Bin, gio). True when the file is gone from where it was."""
    r = subprocess.run(library.trash_cmd(p), capture_output=True, timeout=60)
    return r.returncode == 0 and not Path(p).exists()


def recent_list(st):
    """The clips the page lists: the library's videos/, picked files, the opt-in scan; minus hidden ones."""
    hidden = set(st.get("hidden") or [])
    return [r for r in library.recent(st.get("picked") or [], bool(st.get("scan_home"))) if r["path"] not in hidden]


def is_output(p, edits=()):
    """A finished render, not a raw take: its burned-in captions would clash with a style's sample caption."""
    p = Path(p)
    return bool(p.name in ("render.mp4", "cut.mp4", "final.mp4") or re.match(r"v-.*\.mp4$", p.name)
                or ("edits" in p.parts and "review" in p.parts) or any(d in p.parents for d in edits))


def style_dir(root, sid):
    """A style's own folder: the creator's study folder, or <library>/my-style. None for anything else."""
    if sid == "mine":
        return Path(root) / "my-style"
    h = str(sid)[1:] if str(sid).startswith("@") else ""
    if not HANDLE.fullmatch(h) or h.endswith("-mine") or ".." in h:
        return None
    return Path(root) / "creator-teardowns" / h


def cover_of(root, sid):
    d = style_dir(root, sid)
    f = d / "cover.jpg" if d else None
    return f"/style/cover?id={quote(sid)}&v={int(f.stat().st_mtime)}" if f and f.is_file() else None


def references(d):
    """What a style was analysed from: references.json ({"videos": [{url, title}]}) when there is one, else the
    videos.json entries that have a transcript (the ones actually measured). http(s) links only."""
    ok = lambda u: isinstance(u, str) and re.match(r"https?://", u)
    try:
        r = json.loads((d / "references.json").read_text(encoding="utf-8"))
        vids = [{"url": v["url"], "title": v.get("title") or "", "thumbnail": v.get("thumbnail") if ok(v.get("thumbnail")) else None}
                for v in r.get("videos") or [] if ok(v.get("url"))]
        return {"videos": vids, "profile": r.get("profile") if ok(r.get("profile")) else None}
    except (OSError, ValueError, AttributeError, KeyError, TypeError):
        pass
    try:
        vj = json.loads((d / "videos.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"videos": [], "profile": None}
    have = {f.name.split(".")[0] for f in (d / "transcripts").glob("*.txt")} if (d / "transcripts").is_dir() else set()
    vids = [{"url": v.get("webpage_url"), "title": v.get("title") or "", "thumbnail": v.get("thumbnail") if ok(v.get("thumbnail")) else None}
            for v in vj.get("videos") or [] if ok(v.get("webpage_url")) and str(v.get("id")) in have]
    h, plat = vj.get("handle") or d.name, vj.get("platform")
    prof = None if h.startswith("links-") or plat not in ("tiktok", "youtube") else \
        (f"https://www.tiktok.com/@{h}" if plat == "tiktok" else f"https://www.youtube.com/@{h}")
    return {"videos": vids, "profile": prof}


def write_references(d):
    """At the end of an analysis: references.json from what was measured, so the style keeps its sources."""
    if (d / "references.json").exists() or not (d / "videos.json").is_file():
        return
    r = references(d)
    if r["videos"]:
        (d / "references.json").write_text(json.dumps(r, indent=2), encoding="utf-8")


def make_face(d):
    """creator-teardowns/<h>/face.jpg, once: the brightest of 1, 2 and 4 s into their top downloaded video
    (videos.json order), else the first cell of a contact sheet (visual.py's 3 x 3). False when neither is there."""
    try:
        ids = [v.get("id") for v in json.loads((d / "videos.json").read_text(encoding="utf-8")).get("videos") or []]
    except (OSError, ValueError, AttributeError):
        ids = []
    fit = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"
    mp4 = next((d / "video" / f"{i}.mp4" for i in ids if i and (d / "video" / f"{i}.mp4").is_file()), None)
    sheet = next((d / "sheets" / f"{i}.jpg" for i in ids if i and (d / "sheets" / f"{i}.jpg").is_file()), None)
    if mp4:
        def light(t):   # mean brightness of a 16x16 grey thumbnail; 0 when the frame cannot be read
            px = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(mp4), "-frames:v", "1", "-vf",
                                 "scale=16:16,format=gray", "-f", "rawvideo", "-"], capture_output=True, timeout=30).stdout
            return sum(px) / len(px) if px else 0
        dur = library.meta(mp4).get("duration") or 0   # the middle third shows the style (captions, cards), not an intro
        src, pre = ["-ss", str(max([round(dur * f, 2) for f in (0.35, 0.5, 0.65)] if dur > 6 else [1, 2, 4], key=light)), "-i", str(mp4)], fit
    elif sheet:
        src, pre = ["-i", str(sheet)], "crop=iw/3:ih/3:0:0," + fit
    else:
        return False
    tmp = d / "face.tmp.jpg"
    tmp.unlink(missing_ok=True)
    r = subprocess.run(["ffmpeg", "-v", "error", "-n", *src, "-frames:v", "1", "-vf", pre, "-q:v", "4", str(tmp)],
                       capture_output=True, timeout=60)
    if r.returncode or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        return False
    os.replace(tmp, d / "face.jpg")
    return True


def face_pic(d):
    """A creator's picture: their account picture (avatar.jpg, fetch.py list saves it) over a frame (face.jpg)."""
    return next((f for f in (d / "avatar.jpg", d / "face.jpg") if f.is_file()), None) if d else None


def face_of(root, h):
    """The URL of a creator's picture: avatar.jpg as soon as the listing saved it, else their face frame (make_face),
    made the first time it is asked for once they are studied. &a=1 marks an account picture. None without one."""
    d = style_dir(root, "@" + h) if h else None
    if d and (d / "avatar.jpg").is_file():
        return f"/style/face?id={quote('@' + h)}&v={int((d / 'avatar.jpg').stat().st_mtime)}&a=1"
    if d is None or not (d / "style.json").is_file():
        return None
    f = d / "face.jpg"
    if not f.is_file():
        try:
            make_face(d)
        except (OSError, subprocess.SubprocessError):
            return None
    return f"/style/face?id={quote('@' + h)}&v={int(f.stat().st_mtime)}" if f.is_file() else None


def save_cover(root, sid, src):
    """The image (or a clip's first frame) the user picked for a style's preview, cropped to 9:16 at 720x1280."""
    d = style_dir(root, sid)
    if d is None or (sid != "mine" and not (d / "style.json").is_file()):
        raise KeyError(sid)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "cover.tmp.jpg"
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-frames:v", "1", "-vf",
                        "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280", "-q:v", "4", str(tmp)],
                       capture_output=True, timeout=60)
    if r.returncode or not tmp.is_file():
        tmp.unlink(missing_ok=True)
        return False
    os.replace(tmp, d / "cover.jpg")
    return True


def new_own_style(root, name, skill=None):
    """A style the user builds themselves ("Build it yourself"): stored like a studied creator's, in
    creator-teardowns/own-<slug>/style.json (the default style, own: true), named, with its skill written.
    Returns its id ("@own-<slug>")."""
    from ai_editor.profile import DEFAULT_STYLE
    _, ss = _taste()
    name = " ".join(str(name or "").split())
    base = "own-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40].rstrip("-") or "style")
    h, i = base, 2
    while (Path(root) / "creator-teardowns" / h).exists():
        h, i = f"{base}-{i}", i + 1
    d = Path(root) / "creator-teardowns" / h
    d.mkdir(parents=True)
    p = d / "style.json"
    p.write_text(json.dumps({**DEFAULT_STYLE, "handle": h, "own": True, **({"skill": skill} if skill else {})}, indent=2), encoding="utf-8")
    try:
        ss.rename(p, name)
    except ValueError:
        shutil.rmtree(d)
        raise
    ss.write(str(p))
    return "@" + h


def default_style(cards):
    """The style New video starts on: the one the user made default while it exists, else My style."""
    d = read().get("default_style")
    return d if any(c["id"] == d for c in cards) else "mine"


def delete_style(root, sid, measured=False):
    """A style goes, to the Trash (never a permanent delete): its picks (<h>-mine), its style.json, corrections
    and cover, its skill folder, and its place in profile.json. A studied creator's measured videos stay unless
    `measured`; a style the user built (own-*) goes with its whole folder. My style is never deleted. Returns
    the paths removed ([] when it was already gone)."""
    d = style_dir(root, sid)
    if d is None or sid == "mine":
        raise ValueError("not a creator style")
    try:
        st = json.loads((d / "style.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    if st.get("sample"):
        raise ValueError("a sample style ships with the editor and cannot be deleted")
    _, ss = _taste()
    gone = []
    if (d / "style.json").is_file():
        sk = ss.remove(d / "style.json", rm=trash)   # its skill folder and its name; the other style skills stop naming it
        gone.append(str(sk))
    whole = measured or st.get("own") or d.name.startswith("own-")
    parts = [d.with_name(d.name + "-mine")] + ([d] if whole else [d / f for f in ("style.json", ss.OVERRIDES, "cover.jpg")])
    for p in parts:
        if p.resolve().parent in (d.parent.resolve(), d.resolve()) and p.exists() and trash(p):
            gone.append(str(p))
    update(lambda x: x.__setitem__("archived_styles", [a for a in x.get("archived_styles") or [] if a != sid]))
    pf, prof = home().parent / "profile.json", _profile()
    h = d.name
    if any(c.get("handle") == h for c in prof.get("creators") or []):
        prof["creators"] = [c for c in prof["creators"] if c.get("handle") != h]
        pf.write_text(json.dumps(prof, indent=2), encoding="utf-8")
        gone.append(str(pf))
    return gone


# ---------------------------------------------------------------- setup checks
def doctor_rows(text):
    """setup.py doctor's lines -> [{id, ok, name, what, detail}] for the tools only (keys are their own rows)."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^(ok |FIX|--)\s+(.+?)\s{2,}(.*)$", line)
        if m and m[2].strip() in TOOLS:
            name, what = TOOLS[m[2].strip()]
            v = re.search(r"(?:version )?v?(\d+\.\d+)", m[3]) if m[2].strip() in ("python", "ffmpeg", "node") else None
            out.append({"id": m[2].strip(), "ok": m[1] == "ok ", "name": f"{name} {v[1]}" if v else name, "what": what})
    return out


def run_doctor():
    try:
        r = subprocess.run([sys.executable, str(SETUP), "doctor"], capture_output=True, text=True, timeout=180)
        rows = doctor_rows(r.stdout)
    except (OSError, subprocess.TimeoutExpired):
        rows = []
    update(lambda d: d.__setitem__("doctor", {"rows": rows, "at": now()}))


def key_rows(d=None):
    """Per key: set, from the environment, its last 4 characters and the last check of that same key.
    Never the key itself."""
    checks = (d if d is not None else read()).get("keychecks") or {}
    out = {}
    for n in KEY_NAMES:
        k, where = keys.get(n)
        c = checks.get(n) or {}
        out[n] = {"set": bool(k), "env": bool(k) and not str(where).endswith(".env"),
                  "last4": k[-4:] if k and len(k) >= 12 else None,
                  "check": {x: c[x] for x in ("ok", "msg", "at")} if k and c.get("last4") == k[-4:] else None}
    return out


def note_check(name, key, ok, msg):
    """Remember a key check by the key's last 4 characters, so a key changed elsewhere shows as not checked."""
    update(lambda d: d.setdefault("keychecks", {}).__setitem__(name, {"ok": ok, "msg": msg, "at": now(), "last4": key[-4:]}))


def remove_key(name):
    """Drop VAR= from the key file, keeping every other line. False when the key comes from the environment."""
    k, where = keys.get(name)
    if k and not str(where).endswith(".env"):
        return False
    if keys.key_file().exists():
        keys.remove(name)
    update(lambda d: (d.get("keychecks") or {}).pop(name, None))
    return True


def modal_state():
    """For the page, read from files only (no network, cheap enough for every /state): installed in the
    editor's venv, and logged in (`modal setup` saved a token; setup.py modal_config)."""
    venv = home().parent / "venv"
    inst = (venv / "bin" / "modal").exists() or (venv / "Scripts" / "modal.exe").exists()
    try:
        cfg = Path(os.environ.get("MODAL_CONFIG_PATH") or Path.home() / ".modal.toml").read_text(encoding="utf-8")
    except OSError:
        cfg = ""
    return {"installed": inst, "logged_in": inst and ("token_id" in cfg or bool(os.environ.get("MODAL_TOKEN_ID")))}


def run_modal(kind, jid):
    """Connect Modal (setup.py modal-connect) or Test render (modal-test) for job jid: each STEP line becomes a
    step, LOGIN/CREDIT/TIME go into the result, FAIL fails the job. Modal's CLI keeps the token itself; no
    line here carries it."""
    def edit(fn):
        def f(d):
            j = next(x for x in d["jobs"] if x["id"] == jid)
            t = time.time()
            j["u"] = t
            fn(j, t)
        update(f)

    def step(text):
        def f(j, t):
            for s in j["steps"]:
                if not s.get("done"):
                    s["done"], s["secs"] = True, round(t - s["t"])
            j["steps"].append({"text": text, "detail": None, "progress": None, "t": t, "done": False})
        edit(f)

    def finish(err):
        def f(j, t):
            for s in j["steps"]:
                if not s.get("done"):
                    s["done"], s["secs"] = True, round(t - s["t"])
            j["state"], j["error"], j["finished"] = ("failed" if err else "done"), err, t
        edit(f)
    err = "The setup step stopped without saying why. Try again."
    try:
        p = subprocess.Popen([sys.executable, str(SETUP), "modal-connect" if kind == "modal" else "modal-test"],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace")
        for line in p.stdout:
            word, _, rest = line.strip().partition(" ")
            if word == "STEP":
                step(rest)
            elif word in ("LOGIN", "CREDIT", "TIME"):
                key = {"LOGIN": "login", "CREDIT": "credit", "TIME": "time"}[word]
                edit(lambda j, t, k=key, v=rest: j["result"].__setitem__(k, v))
            elif word == "FAIL":
                err = rest
            elif word in ("Connected.", "Tested."):
                err = None
        p.wait()
    except OSError as e:
        err = f"Could not start the setup step: {e}"
    finish(err)


def drop_modal_runs(d):
    """A server starting: a Connect or Test left running by the last one has no thread watching it now."""
    for j in d["jobs"]:
        if j["type"] in ("modal", "modal-test") and j["state"] == "taken" and j.get("owner") == "home":
            j.update(state="failed", error="Stopped when the window restarted. Press it again.", finished=time.time())


def start_modal(kind):
    """One Connect (or Test) at a time: a running one is returned instead of a second."""
    def f(d):
        j = next((x for x in reversed(d["jobs"]) if x["type"] in ("modal", "modal-test") and x["state"] == "taken"), None)
        if j:
            return j, False
        j = queue(d, kind, {})
        j.update(state="taken", owner="home", started=time.time(), u=time.time())
        return j, True
    j, new = update(f)
    if new:
        threading.Thread(target=run_modal, args=(kind, j["id"]), daemon=True).start()
    return j


# ---------------------------------------------------------------- queueing jobs
def queue(d, kind, args):
    j = {"id": f"{kind}-{sum(x['type'] == kind for x in d['jobs']) + 1}", "type": kind, "args": args,
         "state": "queued", "at": now(), "steps": [], "result": {}}
    d["jobs"].append(j)
    d["jobs"] = d["jobs"][-40:]
    return j


def job_line(j, root):
    a = j["args"]
    if j["type"] == "teardown":
        src = f"urls={a['urls']}" if a.get("urls") else f"platform={a['platform']}"
        return f"JOB teardown @{a['handle']} {src} take={','.join(a.get('take') or [])} root={root}"
    if j["type"] == "skill":
        return f"JOB skill @{a['handle']} take={','.join(x for t in a.get('take') or [] for x in EDITPLAN_PARTS.get(t, (t,)))} root={root}"
    if j["type"] == "video":
        src = f"takes={a['takes']}" if a.get("takes") else a["path"]
        return f"JOB video {src} as={a['as']} aspect={a['aspect']}" + (f" edit={a['edit']}" if a.get("edit") else "") + \
            (f" script={a['script']}" if a.get("script") else "") + (f" skill={a['skill']}" if a.get("skill") else "") + \
            (f" assets={Path(a['edit']) / 'assets.json'}" if a.get("edit") and (Path(a["edit"]) / "assets.json").exists() else "") + f" root={root}"
    if j["type"] == "style-change":
        return f"JOB style-change {'my-style' if a['style'] == 'mine' else a['style']}" + \
            (f" style={a['path']}" if a.get("path") else "") + f" text={a['text']}"
    return f"JOB {j['type']}"


def getting_started(d):
    """The sidebar's Getting started block, from the real state (so it holds across reloads): the tools set up,
    a format analysed (or the default style chosen instead), the first video made (a cut ready or done)."""
    rows = (d.get("doctor") or {}).get("rows") or []
    setup = any(j["type"] == "setup" and j["state"] == "done" for j in d["jobs"]) or bool(rows) and all(r.get("ok") for r in rows)
    style = bool(d.get("style_skipped")) or any(j["type"] == "teardown" and j["state"] == "done" for j in d["jobs"]) \
        or any(not h.startswith(("own-", "sample-")) for h, _ in library.styles(d["root"]))
    es = library.entries()
    video = any(e["status"] in ("ready", "done") for e in es)
    # a user with a made video set up long ago (before the doctor rows were checked): never back to step 1
    return {"setup": setup or video, "style": style or video, "video": video}


def claude_for(jid):
    """A new terminal with a Claude that takes video job jid only ("take job <id>", `wait --job`): reserved first, so
    the listening Claude leaves it alone. {opened, command} (also kept on the job as "claude", so the page shows it, or
    the command to run where no terminal opened), or None when the job is not queued any more."""
    def reserve(d):
        j = next((j for j in d["jobs"] if j["id"] == jid and j["state"] == "queued"), None)
        if j:
            j["reserved"] = time.time()
        return d["root"] if j else None
    root = update(reserve)
    if not root:
        return None
    import launcher
    opened, cmd = launcher.open_claude(root, f"/ai-editor:start take job {jid}")
    r = {"opened": opened, "command": cmd}
    update(lambda d: [j.__setitem__("claude", r) for j in d["jobs"] if j["id"] == jid])
    return r


def busy_with(d, sid):
    """The video job session sid is making now, or None."""
    return next((j for j in reversed(d["jobs"]) if j["type"] == "video" and j["state"] == "taken"
                 and sid and j.get("owner") == sid), None)


def takeable(j, sid, d, job=None):
    """A queued job this wait may take: --job names it, else one not reserved for a new Claude. One video at
    a time per session: the next one waits for it, and meanwhile the page offers a new Claude for it."""
    if j["state"] != "queued":
        return False
    if job:
        return j["id"] == job
    if j.get("reserved") and time.time() - j["reserved"] < (OPENED_S if (j.get("claude") or {}).get("opened") else RESERVE_S):
        return False
    return not (j["type"] == "video" and busy_with(d, sid))


def cmd_wait(poll=1.0, job=None):
    sid = askhook.session()
    if job:   # the Claude the page opened for this job: it takes that job and nothing else
        return _take_one(job, sid, poll)
    beat = home() / ".listening"
    beat.parent.mkdir(parents=True, exist_ok=True)
    if listening():   # one waiter at a time, so each job reaches exactly one background task
        print("ALREADY: another `home.py wait` is running; the next job comes out of that one.", flush=True)
        return "ALREADY"
    update(lambda d: d.update(claude_seen=time.time()) or d.pop("connect", None))   # a pending "Connect Claude" has connected
    beat.write_text(sid, encoding="utf-8")   # who listens: the page's "busy" and askhook read it
    try:
        while True:
            beat.touch()
            d = read()
            if any(takeable(j, sid, d) or (j.get("cancel") and not j.get("cancel_sent") and may_write(j, sid)) for j in d["jobs"]):
                break
            time.sleep(poll)
    finally:
        beat.unlink(missing_ok=True)

    def take(d):
        for j in d["jobs"]:
            if j.get("cancel") and not j.get("cancel_sent") and may_write(j, sid):
                j["cancel_sent"] = True
                return f"JOB cancel {j['type']}"
        j = next((j for j in d["jobs"] if takeable(j, sid, d)), None)
        if not j:   # a --job Claude took it between the read and the lock: wait again
            return None
        j["state"], j["u"] = "taken", time.time()
        if sid:
            j["owner"] = sid
        d["claude_seen"] = j["u"]
        return job_line(j, d["root"])
    line = update(take)
    if line is None:
        return cmd_wait(poll)
    print(line, flush=True)
    return line


def _take_one(job, sid, poll):
    for _ in range(int(60 / poll) + 1):   # the job is there by the time the new Claude starts; a minute at most
        def take(d):
            j = next((j for j in d["jobs"] if j["id"] == job), None)
            if not j or j["state"] in ("done", "failed", "cancelled") or not may_write(j, sid):
                return "GONE"
            if j["state"] == "taken" and (j.get("owner") == sid or stale(j)):
                note = adopt(j, sid)
                j["u"] = time.time()
                return (note + "\n" if note else "") + job_line(j, d["root"])   # asked twice: the same job again
            if not takeable(j, sid, d, job):
                return "GONE"
            j["state"], j["u"] = "taken", time.time()
            if sid:
                j["owner"] = sid
            d["claude_seen"] = j["u"]
            return job_line(j, d["root"])
        line = update(take)
        if line != "GONE" or any(j["id"] == job for j in read()["jobs"]):
            break
        time.sleep(poll)
    if line == "GONE":
        line = f"GONE: {job} is finished, cancelled or another Claude has it. Nothing to do."
    print(line, flush=True)
    return line


def same_key(j, key):
    """A script's key names its job: a video's edit folder (the key is that folder or a file inside it),
    a teardown's handle."""
    try:
        if j["type"] == "video":
            e, k = Path(j["args"]["edit"]).resolve(), Path(key).resolve()
            return k == e or e in k.parents
        slug = lambda h: re.sub(r"[^a-z0-9._-]", "", str(h).lstrip("@").lower())
        return slug(j["args"]["handle"]) == slug(key)
    except (KeyError, TypeError, OSError):
        return False


def mine(d, kind, key=None, sid=""):
    """The taken job a script's report belongs to: of the ones its session may write to (may_write), the one its
    key names; with no key, the only one. Key and session must both hold."""
    taken = [j for j in d["jobs"] if j["type"] == kind and j["state"] == "taken" and may_write(j, sid)]
    if key:
        return next((j for j in reversed(taken) if same_key(j, key)), None)
    return taken[0] if len(taken) == 1 else None


def cmd_status(a):
    def f(d):
        t = time.time()
        d["claude_seen"] = t   # the page's heartbeat: Claude (or a script it runs) is still at work
        sid = askhook.session()
        if if_active:   # a script's own report (progress.py): only onto a taken job of this session, never makes or takes one
            j = mine(d, a.job, getattr(a, "key", None), sid)
            if not j:
                return None
        else:   # this session's own taken job first, then one nobody owns, then a stopped session's (adopted)
            every = [j for j in d["jobs"] if j["type"] == a.job and j["state"] in ("queued", "taken")]
            taken = [j for j in reversed(every) if j["state"] == "taken"]
            j = next((j for j in taken if sid and j.get("owner") == sid), None) or next((j for j in taken if may_write(j, sid)), None)
            if not j and not sid:   # outside Claude only: inside, `wait` hands queued jobs out
                j = next((j for j in reversed(every) if j["state"] == "queued"), None)
            if not j and every:
                return None
            j = j or next((j for j in reversed(d["jobs"]) if j["type"] == a.job and may_write(j, sid)), None)
            if not j and a.job != "setup" and any(x["type"] == a.job for x in d["jobs"]):   # only another session's
                return None
        if not j or (a.job == "setup" and j["state"] in ("done", "failed")):
            j = queue(d, a.job, {})
        if j["state"] == "queued":   # the first step takes the job
            j["state"] = "taken"
            if sid:
                j["owner"] = sid
        note = adopt(j, sid) if j["state"] == "taken" else None
        if note:
            print(note)
        j["u"] = t
        same = j["steps"][-1] if a.step and j["steps"] and j["steps"][-1]["text"] == a.step else None   # only the newest
        if a.step and not same:
            for s in j["steps"]:
                if not s.get("done"):
                    s["done"], s["secs"] = True, round(t - s["t"])
            j["steps"].append({"text": a.step, "detail": a.detail, "progress": a.progress, "t": t, "done": False})
        elif (same or j["steps"]) and (a.detail is not None or a.progress is not None):   # same text: in place
            (same or j["steps"][-1]).update({k: v for k, v in (("detail", a.detail), ("progress", a.progress)) if v is not None})
        if a.result:
            r = json.loads(Path(a.result[1:]).read_text(encoding="utf-8") if a.result.startswith("@") else a.result)
            j["result"].update(r)
        if a.done and j["type"] == "teardown" and j["args"].get("handle"):
            try:
                write_references(Path(d["root"]) / "creator-teardowns" / j["args"]["handle"])
            except OSError:
                pass
        if a.done or a.failed:
            for s in j["steps"]:
                if not s.get("done"):
                    s["done"], s["secs"] = True, round(t - s["t"])
            j["state"] = "failed" if a.failed else "done"
            j["error"] = a.failed
            j["finished"] = t
        j.setdefault("started", t)
        return j
    if_active = getattr(a, "if_active", False)
    try:   # a script's report gives up after 2 s, well before progress.py's 5 s kill could strand the lock
        j = update(f, wait=2 if if_active else 10)
    except TimeoutError:
        if not if_active:
            raise
        j = None
    if j is None:
        print(f"no {a.job} job on the page" + (" for this Claude session" if askhook.session() else ""))
        return 1   # progress.py reads the exit code: nothing was written
    if a.job == "setup" and (a.step or a.done or a.failed):   # a step finished: the tool rows tick as it goes
        run_doctor()
    print(f"{j['id']}: {j['state']}" + (f", {a.step}" if a.step else ""))


# ---------------------------------------------------------------- server
def make_handler(token):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):   # never log a request: a key travels in one
            pass

        def cookie_name(self):
            return f"ave-home-{self.server.server_address[1]}"

        def authorized(self):
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):   # DNS rebinding: another name pointing here
                return False
            q = parse_qs(urlsplit(self.path).query).get("t", [""])[0]
            c = SimpleCookie()
            try:
                c.load(self.headers.get("Cookie") or "")
            except Exception:
                pass
            got = c[self.cookie_name()].value if self.cookie_name() in c else ""
            return any(hmac.compare_digest(v.encode(), token.encode()) for v in (q, got) if v)

        def send(self, code, body, ctype="application/json", cookie=False, cache="no-store"):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            if cookie:
                self.send_header("Set-Cookie", f"{self.cookie_name()}={token}; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.authorized():
                return self.send(403, {"error": "open the page with the URL home.py printed"})
            path = unquote(urlsplit(self.path).path)
            if askhook.route(self, "GET", path, {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}):   # Claude's questions
                return
            if path == "/":
                return self.send(200, themed(HTML.read_bytes()), "text/html; charset=utf-8", cookie=True)
            if path == "/state":
                d = read()
                d["listening"], d["keys"], d["theme"] = listening(), key_rows(d), theme()
                d["modal"] = modal_state()
                d.pop("keychecks", None)
                bz = busy_with(d, listener())   # the listening Claude is making a video: the next one can get its own
                d["busy"] = bz and {"id": bz["id"], "name": job_name(bz)}
                d["profile"] = (home().parent / "profile.json").exists()   # set up before: a returning user
                d["update"] = update_info()
                d["getting_started"] = getting_started(d)
                import launcher   # a Claude of its own per video only where a terminal window can be opened
                d["new_claude"] = bool(launcher.terminal(d["root"], ["claude"], platform.system()))
                d["page"] = live.page_version(HTML, HTML.with_name("ask.js"))   # the tab reloads when this changes
                lk = d.get("lookup") or {}
                if lk.get("handle"):
                    lk["listing"] = listing(d["root"], lk["handle"])
                td = latest(d, "teardown")
                if td:
                    td["measured"] = measured(d["root"], td["args"]["handle"])
                    td["listing"] = listing(d["root"], td["args"]["handle"])
                    td["face"] = face_of(d["root"], td["args"]["handle"])   # the account picture, once the listing saved it
                    try:   # creator-teardown's format check, when it ran: which links it left out
                        td["format"] = json.loads((Path(d["root"]) / "creator-teardowns" / td["args"]["handle"] / "format.json").read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        td["format"] = None
                return self.send(200, d)
            q = parse_qs(urlsplit(self.path).query)
            if path == "/library":
                root, jobs = read()["root"], read()["jobs"]
                raw = {str(Path(j["args"]["edit"]).resolve()): j for j in jobs if j["type"] == "video" and j["args"].get("edit")}
                # no cut yet: the raw clip's frame (as New video shows it), and the job the card follows
                vids = [dict(e, thumb=f"/thumb?p={quote(e['video'] or raw[e['dir']]['args']['path'])}&u={int(e['updated'])}"
                             if e["video"] or e["dir"] in raw else None,
                             media=e["video"] and "/media?p=" + quote(e["video"]), job=(raw.get(e["dir"]) or {}).get("id"))
                        for e in library.entries() if e["dir"] not in set(read().get("hidden_edits") or [])]   # Remove from this list
                with ThreadPoolExecutor(8) as ex:   # its length and shape (cached ffprobe): "Done. 2:14 long", a wide or tall frame
                    ms = list(ex.map(lambda v: library.meta(v["video"]) if v["video"] else {}, vids))
                for v, m in zip(vids, ms):
                    a = (raw.get(v["dir"]) or {}).get("args", {}).get("aspect")
                    v["duration"] = m.get("duration") if v["status"] in ("ready", "done") else None
                    v["wide"] = a == "16:9" if a and v["status"] == "cutting" or not m.get("h") else m["w"] > m["h"]   # the raw take is not the shape it is cut to
                cards = style_cards(root)
                for j in read()["jobs"]:   # a creator still being studied: a card the user can already pick
                    h = j["args"].get("handle")
                    if j["type"] == "teardown" and j["state"] in ("queued", "taken") and "@" + str(h) not in [c["id"] for c in cards]:
                        cards.append({"id": "@" + h, "name": "@" + h, "facts": [], "look": None, "studying": True, "face": face_of(root, h),
                                      "line": "Being analysed now. Your cut starts first; the style waits for it."})
                arch = set(read().get("archived_styles") or [])   # Archive: off Styles, Home and Pick a style; files untouched
                return self.send(200, {"path": root, "place": place(root), "env": bool(os.environ.get("AI_EDITOR_LIBRARY", "").strip()),
                                       "custom": str(custom.path(False)), "videos": vids, "folders": folders(),
                                       "styles": [c for c in cards if c["id"] not in arch], "archived_styles": [c for c in cards if c["id"] in arch],
                                       "default_style": default_style(cards)})
            if path == "/skills":   # New style, "Use your own skill"
                return self.send(200, {"skills": editing_skills(read()["root"])})
            if path == "/recent":
                st = read()
                rs = recent_list(st)
                with ThreadPoolExecutor(8) as ex:
                    ms = list(ex.map(lambda r: library.meta(r["path"]), rs))
                eds = [Path(e).resolve() for e in library.edit_dirs()]
                return self.send(200, {"scan_home": bool(st.get("scan_home")), "videos": [
                    dict(r, duration=m["duration"], vertical=bool(m["h"] and m["w"] and m["h"] > m["w"]),
                         raw=not is_output(Path(r["path"]).resolve(), eds),
                         thumb=f"/thumb?p={quote(r['path'])}&m={int(r['mtime'])}") for r, m in zip(rs, ms)]})
            if path == "/motion/thumb":   # a saved motion clip's thumbnail
                try:
                    mid = q.get("id", [""])[0]
                    f = motion_mod().library_dir() / mid / "thumb.jpg"
                    ok = re.fullmatch(r"[a-z_]+-[0-9a-f]{10}", mid) and f.is_file()
                except Exception:   # noqa: BLE001
                    ok = False
                return self.send(200, f.read_bytes(), "image/jpeg", cache="private, max-age=86400") if ok else self.send(404, {})
            if path == "/thumb":
                f = Path(q.get("p", [""])[0])
                t = library.thumb(f) if f.suffix.lower() in links.VIDEO_EXT and f.is_file() else None
                return self.send(200, t.read_bytes(), "image/jpeg", cache="private, max-age=86400") if t else self.send(404, {})
            if path == "/media":   # a library video's file, to watch or download
                f = Path(q.get("p", [""])[0]).resolve()
                if not any(d in f.parents for d in library.edit_dirs()) or not f.is_file() or f.suffix.lower() not in links.VIDEO_EXT:
                    return self.send(404, {})
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(f.stat().st_size))
                self.send_header("Content-Disposition", f'attachment; filename="{quote(f.name)}"')
                self.end_headers()
                with open(f, "rb") as fh:
                    shutil.copyfileobj(fh, self.wfile)
                return
            if path.startswith("/demo/"):   # the stock photos behind empty previews
                name = path[len("/demo/"):]
                f = DEMO / name
                ok = re.fullmatch(r"person-\d\.jpg", name) and f.is_file()
                return self.send(200, f.read_bytes(), "image/jpeg", cache="private, max-age=86400") if ok else self.send(404, {})
            if path in ("/style/cover", "/style/face"):
                d = style_dir(read()["root"], q.get("id", [""])[0])
                f = face_pic(d) if path.endswith("face") else d / "cover.jpg" if d else None
                return self.send(200, f.read_bytes(), "image/jpeg", cache="private, max-age=86400") if f and f.is_file() else self.send(404, {})
            if path in ("/style/tile", "/style/clip"):   # a preset's rendered still, a cut preview clip (presets.py cache)
                k = q.get("k", [""])[0]
                ok = re.fullmatch(r"[0-9a-f]{20}", k) if path == "/style/tile" else re.fullmatch(r"[0-9a-f]{20}-(before|[0-9.]+)\.mp4", k)
                f = presets.cache_dir() / (k + ".jpg" if path == "/style/tile" else k) if ok else None
                if not f or not f.is_file():
                    return self.send(404, {})
                return self.send(200, f.read_bytes(), "image/jpeg" if path == "/style/tile" else "video/mp4", cache="private, max-age=86400")
            if path == "/style":
                try:
                    return self.send(200, style_page(read()["root"], q.get("id", ["mine"])[0]))
                except KeyError:
                    return self.send(404, {"error": "no such style"})
            if path == "/disk":
                return self.send(200, library.disk())
            if path == "/review":   # a library video's review page: its own server, started if it is not running
                d = Path(q.get("dir", [""])[0]).resolve()
                if d not in library.edit_dirs() or not (d / "review.json").exists():
                    return self.send(404, {"error": "no review page for that video yet"})
                url = review_url(d)
                if not url:
                    return self.send(500, {"error": "the review page did not start"})
                self.send_response(302)
                self.send_header("Location", url)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            m = re.match(r"^/td/([a-z0-9._-]+)/(.+)$", path)   # the teardown page and its own files
            if m:
                base = (Path(read()["root"]) / "creator-teardowns" / m[1]).resolve()
                p = (base / m[2]).resolve()
                if base in p.parents and p.is_file():
                    ct = {".html": "text/html; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
                          ".json": "application/json", ".mp4": "video/mp4", ".gif": "image/gif"}.get(p.suffix, "application/octet-stream")
                    return self.send(200, p.read_bytes(), ct)
            self.send(404, {})

        def do_POST(self):
            if not self.authorized():
                return self.send(403, {"error": "open the page with the URL home.py printed"})
            if not askhook.same_origin(self):   # a POST from another site's page, even with the cookie
                return self.send(403, {"error": "not from this page"})
            path = urlsplit(self.path).path
            if path == "/script":
                return self.script()
            if path in ("/upload", "/style/cover"):
                with live.busy():   # no live-reload restart under an upload
                    return self.upload() if path == "/upload" else self.cover()
            try:
                cap = askhook.MAX_BODY if path in ("/ask/answer", "/job") else 1 << 20   # an answer or a new video can carry images
                body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), cap)) or b"{}")
            except ValueError:
                return self.send(400, {"error": "not json"})
            if askhook.route(self, "POST", path, {}, body):
                return
            if path == "/theme":   # the toggle: one choice for the window and every review page (review.py)
                try:
                    set_theme(body.get("theme"))
                except ValueError:
                    return self.send(400, {"error": "light, dark or system"})
                return self.send(200, {"theme": theme()})
            if path == "/key":
                name, val = body.get("name"), str(body.get("value") or "").strip()
                if name not in KEY_NAMES or not val or len(val) > 400 or re.search(r"\s", val):
                    return self.send(400, {"ok": False, "msg": "Paste only the key: one line, no spaces."})
                ok, msg = keys.verify(name, val)
                if ok:
                    keys.save(name, val)
                    note_check(name, val, True, msg)
                return self.send(200, {"ok": ok, "msg": "Verified. The key works." if ok else f"Not saved. {msg[0].upper()}{msg[1:]}"})
            if path in ("/key/check", "/key/remove"):   # Settings: test the saved key again, or delete it
                name = body.get("name")
                if name not in KEY_NAMES:
                    return self.send(400, {"msg": "no such key"})
                if path == "/key/remove":
                    return self.send(200, {}) if remove_key(name) else self.send(409, {"msg": "This key comes from an environment variable. Remove it there."})
                k = keys.get(name)[0]
                if not k:
                    return self.send(404, {"msg": "No key saved."})
                ok, msg = keys.verify(name, k)
                note_check(name, k, ok, msg)
                return self.send(200, {"ok": ok, "msg": msg})
            if path == "/update/check":   # Settings: ask GitHub now instead of within the hour
                if not _UPD["busy"] and time.time() - _UPD["at"] > 60:   # a minute between checks, however often it is pressed
                    _UPD["at"] = 0.0
                update_info()
                return self.send(200, {})
            if path == "/later":
                if body.get("names") == ["style"]:   # first run: "Skip, use the default style"
                    update(lambda d: d.__setitem__("style_skipped", True))
                    return self.send(200, {})
                for n in body.get("names") or []:
                    if n in KEY_NAMES + ("modal",):
                        subprocess.run([sys.executable, str(SETUP), "later", n], capture_output=True, timeout=30)
                update(lambda d: d.__setitem__("setup_skipped", True))
                return self.send(200, {})
            if path in ("/modal/connect", "/modal/test"):   # Settings and onboarding: Connect Modal, Test render
                return self.send(200, {"id": start_modal("modal" if path == "/modal/connect" else "modal-test")["id"]})
            if path == "/recheck":
                threading.Thread(target=run_doctor, daemon=True).start()
                return self.send(200, {})
            if path == "/lookup":
                return self.lookup(str(body.get("text") or "").strip(), body.get("platform"))
            if path == "/probe":
                return self.send(200, probe(body.get("path") or ""))
            if path == "/job":
                return self.job(body)
            if path == "/reveal":   # Show in Finder / Explorer: the library, one of its videos, or the custom folder
                p = custom.path().resolve() if body.get("custom") else Path(str(body.get("dir") or read()["root"])).resolve()
                if p not in (Path(read()["root"]).resolve(), custom.path(False).resolve()) and p not in library.edit_dirs():
                    return self.send(400, {})
                subprocess.Popen(library.reveal_cmd(p), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return self.send(200, {})
            if path == "/clean":   # the page asks first; without yes it only counts
                fs, n = library.clean(yes=body.get("yes") is True)
                return self.send(200, {"files": len(fs), "bytes": n, "deleted": body.get("yes") is True})
            if path in ("/style/rename", "/style/usefor"):   # the pencils on the style page and cards
                root = read()["root"]
                try:
                    sid = str(body.get("id") or "mine")
                    taste, ss = _taste()
                    sp, _, base = style_of(root, sid)
                    if path == "/style/rename":   # empty: the suggestion, so a style never ends up nameless
                        ss.rename(sp, " ".join(str(body.get("name") or "").split()) or suggest_name(sid, base))
                    else:
                        ss.set_use_for(sp, body.get("text"))
                    return self.send(200, style_page(root, sid))
                except KeyError:
                    return self.send(404, {"error": "No such style."})
                except (ValueError, OSError) as e:
                    return self.send(400, {"error": str(e)})
            if path == "/style/cutclips":   # the Cut section's "Play 5 s of your video, before and after"
                c = presets.CUT.get(str(body.get("preset")))
                ed = newest_edit(("report.json", "words.raw.json"))
                got = c and ed and presets.cut_clips(ed, c["max_pause"])
                if not got:
                    return self.send(404, {"error": "No cut to play yet. Make a video first."})
                return self.send(200, {"before": f"/style/clip?k={got[0].name}", "after": f"/style/clip?k={got[1].name}"})
            if path in ("/motion/archive", "/motion/use", "/motion/delete"):   # the motion library's menu
                mm, mid = motion_mod(), str(body.get("id") or "")
                try:
                    if path == "/motion/delete":
                        mm.lib_set(mid)   # KeyError when it is not a saved clip
                        if not trash(mm.library_dir() / mid):
                            return self.send(200, {"ok": False, "msg": "It could not be moved to the Trash."})
                    else:
                        mm.lib_set(mid, archived=bool(body.get("on", True)) if path == "/motion/archive" else None,
                                   use=True if path == "/motion/use" else None)
                except KeyError:
                    return self.send(404, {"error": "no such saved animation"})
                return self.send(200, {"ok": True, "library": motion_state({}, {})["library"]})
            if path in ("/style/set", "/style/forget", "/style/reveal", "/style/reset"):
                root = read()["root"]
                try:
                    sid = str(body.get("id") or "mine")
                    if path == "/style/reset":
                        if body.get("section") not in SECTIONS:
                            return self.send(400, {"error": "not a section"})
                        reset_section(root, sid, body["section"])
                    elif path == "/style/set":
                        if body.get("key") not in CONTROLS:
                            return self.send(400, {"error": "not a setting"})
                        set_control(root, sid, body["key"], body.get("value"))
                    elif path == "/style/forget":
                        taste, ss = _taste()
                        page = style_page(root, sid)
                        if int(body.get("rule", -1)) not in [r["id"] for r in page["rules"]]:
                            return self.send(400, {"error": "not a rule of this style"})
                        taste.forget(int(body["rule"]))
                    else:
                        taste, ss = _taste()
                        sp, _, _ = style_of(root, sid)
                        md = ss.skills_dir() / ss.name(sp) / "SKILL.md"
                        if not md.exists():
                            ss.write(sp)
                        subprocess.Popen(library.reveal_cmd(md), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return self.send(200, style_page(root, sid))
                except KeyError:
                    return self.send(404, {"error": "no such style"})
                except (ValueError, TypeError) as e:
                    return self.send(400, {"error": str(e)})
            if path in ("/folder/create", "/folder/rename", "/folder/delete", "/folder/move"):
                kind, root = str(body.get("kind") or ""), read()["root"]
                listed = set()
                if path == "/folder/move":
                    listed = {str(d) for d in library.edit_dirs()} if kind == "videos" \
                        else {c["id"] for c in style_cards(root)}
                try:
                    return self.send(200, folder_change(kind, path.rsplit("/", 1)[1], body, listed))
                except ValueError as e:
                    return self.send(400, {"ok": False, "error": str(e)})
            if path == "/style/new":   # Build it yourself: a new own style, then its page
                try:
                    sk = body.get("skill")   # "Use your own skill": a skill style
                    if sk is not None and not skill_of({"skill": sk}):
                        raise ValueError("That is not a skill name.")
                    return self.send(200, {"ok": True, "id": new_own_style(read()["root"], body.get("name"), sk)})
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
            if path == "/style/default":   # Make default: the style New video starts on
                sid = str(body.get("id") or "mine")
                if sid != "mine" and not any(c["id"] == sid for c in style_cards(read()["root"])):
                    return self.send(404, {"error": "No such style."})
                update(lambda d: d.__setitem__("default_style", sid))
                return self.send(200, {"ok": True, "default_style": sid})
            if path == "/style/archive":   # {id, on}: on (the default) archives, on=false brings it back
                sid, on, cards = str(body.get("id") or ""), body.get("on") is not False, style_cards(read()["root"])
                if not any(c["id"] == sid for c in cards):
                    return self.send(404, {"error": "No such style."})
                if on and sid == default_style(cards):
                    return self.send(400, {"error": "This is your default style. Make another style the default first."})
                update(lambda d: d.__setitem__("archived_styles", [x for x in d.get("archived_styles") or [] if x != sid] + ([sid] if on else [])))
                return self.send(200, {"ok": True})
            if path == "/style/reset-all":
                reset_mine()
                return self.send(200, {"ok": True})
            if path in ("/style/delete", "/style/cover/remove"):
                root, sid = read()["root"], str(body.get("id") or "")
                if path == "/style/cover/remove":
                    d = style_dir(root, sid)
                    if d is None:
                        return self.send(400, {"error": "not a style"})
                    (d / "cover.jpg").unlink(missing_ok=True)
                    return self.send(200, {"ok": True})
                try:
                    return self.send(200, {"ok": True, "removed": delete_style(root, sid, body.get("measured") is True)})
                except ValueError as e:
                    return self.send(400, {"error": str(e)})
            if path in ("/clip/hide", "/clip/trash", "/edit/trash", "/edit/hide"):   # only what the page listed; Trash, never a delete
                st = read()
                if path in ("/edit/trash", "/edit/hide"):
                    d = Path(str(body.get("dir") or "")).resolve()
                    if d not in library.edit_dirs() or Path(st["root"]).resolve() not in d.parents:
                        return self.send(400, {"ok": False, "msg": "That is not one of your videos."})
                    if path == "/edit/hide":   # off the window only: its folder stays as it is
                        update(lambda x: x.__setitem__("hidden_edits", (x.get("hidden_edits") or []) + [str(d)]))
                        return self.send(200, {"ok": True})
                    ok = trash(d)
                    return self.send(200, {"ok": True} if ok else {"ok": False, "msg": "It could not be moved to the Trash."})
                f = Path(str(body.get("path") or "")).resolve()
                listed = next((r for r in recent_list(st) if r["path"] == str(f)), None)
                if not listed or not f.is_file() or f.suffix.lower() not in links.VIDEO_EXT:
                    return self.send(400, {"ok": False, "msg": "That clip is not on your list."})
                if path == "/clip/hide":
                    update(lambda d: d.__setitem__("hidden", (d.get("hidden") or []) + [str(f)]))
                    return self.send(200, {"ok": True})
                if not trash(f):
                    return self.send(200, {"ok": False, "msg": "It could not be moved to the Trash."})
                update(lambda d: d.__setitem__("picked", [x for x in d.get("picked") or [] if Path(x).resolve() != f]))
                return self.send(200, {"ok": True})
            if path == "/scanhome":   # the one-time opt-in: recent clips from Movies, Desktop and Downloads
                update(lambda d: d.__setitem__("scan_home", body.get("on") is True))
                return self.send(200, {})
            if path == "/setlib":
                if os.environ.get("AI_EDITOR_LIBRARY", "").strip():
                    return self.send(409, {"msg": "AI_EDITOR_LIBRARY is set, so the folder comes from there."})
                try:
                    p = library.set_path(str(body.get("path") or ""))
                except OSError as e:
                    return self.send(400, {"msg": f"Could not use that folder: {e.strerror or e}"})
                update(lambda d: d.__setitem__("root", str(p)))
                return self.send(200, {"path": str(p)})
            if path == "/connect":   # "Connect Claude": a new terminal with a listening Claude, one at a time
                import launcher
                st, cmd = launcher.connect(read()["root"])
                return self.send(200, {"status": st, "command": cmd})
            if path == "/cancel":
                def c(d):
                    j = latest(d, body.get("type") or "teardown")
                    if j and j["state"] in ("queued", "taken"):
                        j["cancel"], j["state"] = True, "cancelled"
                update(c)
                return self.send(200, {})
            self.send(404, {})

        def lookup(self, text, platform=None):
            parts = text.split()
            if not parts:
                return self.send(200, {"kind": "empty"})
            if len(parts) == 1 and not re.search(r"[./]", parts[0]):
                parts = ["@" + parts[0].lstrip("@")]           # a bare name is a handle, never a website
            items = [links.classify(p) for p in parts]
            vids = [i for i in items if i["kind"] in ("video", "own", "long")]
            if vids and len(vids) == len(items):               # pasted video links (any app): study just those
                return self.send(200, link_set(vids))
            it = items[0]
            if it["kind"] != "creator":
                return self.send(200, {"kind": it["kind"], "note": "Paste a creator's handle or profile link, like "
                                                                   "@name or tiktok.com/@name."})
            handle, plat = re.sub(r"[^a-z0-9._-]", "", it["handle"].lower()), it.get("platform") or platform or "tiktok"
            out = {"kind": "creator", "handle": handle, "platform": plat, "note": None, "ask_platform": not it.get("platform")}
            if plat in ("tiktok", "youtube", "instagram"):
                d = read()
                if (d.get("lookup") or {}).get("handle") != handle or (d.get("lookup") or {}).get("platform") != plat \
                        or d["lookup"].get("status") == "failed":
                    update(lambda d: d.__setitem__("lookup", {"handle": handle, "platform": plat, "status": "listing"}))
                    threading.Thread(target=run_listing, args=(d["root"], handle, plat), daemon=True).start()
            return self.send(200, out)

        def job(self, b):
            kind = b.get("type")
            take = [t for t in b.get("take") or [] if t in TAKE]
            d = read()
            if kind == "teardown":
                handle = re.sub(r"[^a-z0-9._-]", "", str(b.get("handle") or "").lower())
                urls = [u for u in b.get("urls") or [] if links.classify(u)["kind"] != "unsupported"][:40]
                if urls and not handle:
                    handle = link_set([links.classify(u) for u in urls])["handle"] or "links-" + time.strftime("%m%d%H%M")
                if not handle:
                    return self.send(400, {"error": "no creator"})
                args = {"handle": handle, "platform": b.get("platform") if b.get("platform") in ("tiktok", "youtube", "instagram") else "tiktok",
                        "take": take}
                if urls:
                    f = Path(d["root"]) / "creator-teardowns" / handle / "picks.txt"
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text("\n".join(urls) + "\n", encoding="utf-8")
                    args.update(urls=str(f), platform=link_set([links.classify(u) for u in urls])["platform"])
            elif kind == "skill":
                td = latest(d, "teardown")
                if not td or td["state"] != "done":
                    return self.send(409, {"error": "the copy of their style is not finished"})
                args = {"handle": td["args"]["handle"], "take": take}
            elif kind == "video":
                return self.videos(b, d)
            elif kind in ("update", "setup"):   # setup: the first run's Start setup (JOB setup, install-all --report)
                if kind == "setup" and any(j["type"] == "setup" and j["state"] in ("queued", "taken") for j in d["jobs"]):
                    return self.send(200, {"id": latest(d, "setup")["id"]})
                args = {}
            elif kind == "style-change":   # "Tell it what to change": Claude turns the words into rules at that style's scope
                text = re.sub(r"\s+", " ", str(b.get("text") or "")).strip()[:400]
                try:
                    sp, _, _ = style_of(d["root"], b.get("style") or "mine")
                except KeyError:
                    return self.send(400, {"error": "no such style"})
                if not text:
                    return self.send(400, {"error": "say what to change"})
                args = {"style": b.get("style") or "mine", "path": str(sp) if sp else "", "text": text}
            else:
                return self.send(400, {"error": "unknown job"})
            j = update(lambda d: queue(d, kind, args))
            return self.send(200, {"id": j["id"], "handle": j["args"].get("handle")})

        def videos(self, b, d):
            """One clip, several joined into one video (takes), or several separate videos: one JOB each."""
            ps = [probe(x) for x in (b.get("paths") or [b.get("path") or ""])[:20]]
            bad = next((p for p in ps if not p.get("ok")), None)
            if bad:
                return self.send(400, bad)
            want, td = str(b.get("as") or "creator"), latest(d, "teardown")
            skill = None
            if want.startswith("@"):
                h = re.sub(r"[^a-z0-9._-]", "", want[1:].lower())
                studying = any(j["type"] == "teardown" and j["args"].get("handle") == h and j["state"] in ("queued", "taken")
                               for j in d["jobs"])   # the cut starts now; the styled pass waits for the study
                if not studying and not (Path(d["root"]) / "creator-teardowns" / h / "style.json").exists():
                    return self.send(400, {"ok": False, "msg": f"@{h}'s style has not been copied yet."})
                style = "@" + h
                try:   # a skill style: the edit stage is that skill (start skill, JOB video skill=)
                    skill = skill_of(json.loads((Path(d["root"]) / "creator-teardowns" / h / "style.json").read_text(encoding="utf-8")))
                except (OSError, ValueError):
                    pass
            elif want in ("mine", "defaults"):
                style = want
            else:   # the first-run page: its creator once the study is done
                style = f"@{td['args']['handle']}" if td and td["state"] == "done" else "defaults"
            aspect = "16:9" if b.get("aspect") == "16:9" else "9:16"
            script = str(b.get("script") or "").strip()
            if len(script.encode("utf-8")) > SCRIPT_MAX:
                return self.send(400, {"ok": False, "msg": "That script is over 200 KB. Paste only the lines you film."})
            adds = [x for x in b.get("assets") or [] if isinstance(x, dict)][:askhook.MAX_IMAGES + assets.MAX_LINKS]
            if sum(bool(x.get("image")) for x in adds) > askhook.MAX_IMAGES:
                return self.send(400, {"ok": False, "msg": f"{askhook.MAX_IMAGES} images at most."})
            groups = [ps] if b.get("join") and len(ps) > 1 else [[p] for p in ps]
            ids, edits = [], []

            def remember(d):
                d["picked"] = list(dict.fromkeys([p["path"] for p in ps] + (d.get("picked") or [])))[:60]
            update(remember)
            for g in groups:
                edit = new_edit(Path(d["root"]), g[0]["name"])
                args = {"path": g[0]["path"], "paths": [p["path"] for p in g], "as": style, "aspect": aspect, "edit": str(edit),
                        **({"skill": skill} if skill else {})}
                if len(g) > 1:
                    t = edit / "takes.txt"
                    t.write_text("\n".join(p["path"] for p in g) + "\n", encoding="utf-8")
                    args["takes"] = str(t)
                if script:   # the text only, in the edit folder; the cut's script_match.py reads it there
                    (edit / "script.md").write_text(script + "\n", encoding="utf-8")
                    args["script"] = str(edit / "script.md")
                assets.add(edit, adds)   # "Add images or links": edit/assets/ + assets.json, before the job line is read
                ids.append(update(lambda d: queue(d, "video", args))["id"])
                edits.append(str(edit))
                # the editor opens at once on the clip (round 0, "The cut / your clip"); Claude's cut lands there
                subprocess.run([sys.executable, str(REVIEW), str(edit), "start", "--raw", "--video", g[0]["path"], "--stage", "cut"],
                               capture_output=True, timeout=60)
            # every video gets a Claude of its own in a new Terminal window, unless an idle listening Claude takes it now
            lis, d = listener(), read()
            idle = lis is not None and not busy_with(d, lis) and not any(takeable(j, lis, d) for j in d["jobs"] if j["id"] not in ids)
            for jid in ids[1:] if idle else ids:
                claude_for(jid)
            return self.send(200, {"id": ids[0], "ids": ids, "edits": edits})

        def cover(self):
            """A style's preview image: an image or a short clip, streamed to a temp file, then one 720x1280 frame."""
            sid = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
            n = body_len(self.headers)
            kind = (self.headers.get("Content-Type") or "").split("/")[0]
            if kind not in ("image", "video") or not 0 < n <= COVER_MAX:
                return self.send(400, {"ok": False, "msg": "Pick an image or a short video under 50 MB."})
            tmp = home() / f"cover-{secrets.token_hex(4)}.part"
            tmp.parent.mkdir(parents=True, exist_ok=True)
            try:
                with open(tmp, "wb") as f:
                    left = n
                    while left > 0:
                        b = self.rfile.read(min(1 << 20, left))
                        if not b:
                            return self.send(400, {"ok": False, "msg": "The upload stopped part way. Try again."})
                        f.write(b)
                        left -= len(b)
                if not save_cover(read()["root"], sid, tmp):
                    return self.send(400, {"ok": False, "msg": "That file could not be read as an image."})
                return self.send(200, {"ok": True, "cover": cover_of(read()["root"], sid)})
            except KeyError:
                return self.send(404, {"ok": False, "msg": "No such style."})
            finally:
                tmp.unlink(missing_ok=True)

        def script(self):
            """A chosen script file: read in memory (1 MB at most), only its text goes back. Nothing is saved until
            the video is made."""
            name = Path(unquote(parse_qs(urlsplit(self.path).query).get("name", [""])[0])).name
            n = body_len(self.headers)
            if not name.lower().endswith(SCRIPT_EXT):
                return self.send(400, {"ok": False, "msg": "Add a .txt, .md, .docx or .pdf file, or paste the script."})
            if not 0 < n <= 5 * SCRIPT_MAX:
                while n > 0 and self.rfile.read(min(1 << 20, n)):   # drained, so the browser reads the answer
                    n -= 1 << 20
                return self.send(400, {"ok": False, "msg": "That file is too big. Paste the script instead."})
            text = script_text(name, self.rfile.read(n))
            if not text:
                return self.send(400, {"ok": False, "msg": "Could not read the words in that file. Paste the script instead."})
            if len(text.encode("utf-8")) > SCRIPT_MAX:
                return self.send(400, {"ok": False, "msg": "That script is over 200 KB. Paste only the lines you film."})
            return self.send(200, {"ok": True, "text": text})

        def upload(self):
            """A dropped video: streamed to <root>/videos/<name>, never held in memory."""
            name = Path(unquote(parse_qs(urlsplit(self.path).query).get("name", [""])[0])).name
            n = body_len(self.headers)
            if not name.lower().endswith(links.VIDEO_EXT) or n <= 0:
                return self.send(400, {"ok": False, "msg": "Drop a .mov, .mp4 or .mkv video."})
            out = Path(read()["root"]) / "videos" / name
            out.parent.mkdir(parents=True, exist_ok=True)
            k = 1
            while out.exists() and out.stat().st_size != n:   # another file with this name: never overwrite it
                k += 1
                out = out.with_name(f"{Path(name).stem}-{k}{Path(name).suffix}")
            tmp = out.with_suffix(out.suffix + ".part")
            with open(tmp, "wb") as f:
                left = n
                while left > 0:
                    b = self.rfile.read(min(1 << 20, left))
                    if not b:
                        break
                    f.write(b)
                    left -= len(b)
            if left:
                tmp.unlink(missing_ok=True)
                return self.send(400, {"ok": False, "msg": "The upload stopped part way. Drop it again."})
            os.replace(tmp, out)
            return self.send(200, probe(str(out)))
    return H


def new_edit(root, name):
    """<root>/edits/<slug of the file name>, -2, -3 ... when taken; made now and listed in the library."""
    slug = links._slug(Path(name).stem)
    edit, n = root / "edits" / slug, 1
    while edit.exists():
        n += 1
        edit = root / "edits" / f"{slug}-{n}"
    edit.mkdir(parents=True)
    library.record(edit)
    return edit


def probe(path):
    """{ok, path, name, duration, size, vertical, label} for a video file on this computer."""
    p = Path(os.path.expanduser(str(path).strip().strip('"').strip("'")))
    if not p.is_file():
        return {"ok": False, "msg": "No video at that path."}
    if not p.name.lower().endswith(links.VIDEO_EXT):
        return {"ok": False, "msg": "That is not a video file (.mov, .mp4, .mkv)."}
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:stream_tags=rotate:format=duration", "-of", "json", str(p)],
                           capture_output=True, text=True, timeout=30)
        j = json.loads(r.stdout or "{}")
        s = (j.get("streams") or [{}])[0]
        w, h, dur = s.get("width"), s.get("height"), float((j.get("format") or {}).get("duration") or 0)
        if str((s.get("tags") or {}).get("rotate", "0")) in ("90", "-90", "270"):
            w, h = h, w
    except (OSError, ValueError, subprocess.TimeoutExpired):
        w = h = None
        dur = 0
    if not w:
        return {"ok": False, "msg": "That file has no video in it that ffprobe can read."}
    size = p.stat().st_size
    gb = f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{size / 1e6:.0f} MB"
    res = "4K" if max(w, h) >= 3840 else "1080p" if max(w, h) >= 1920 else f"{min(w, h)}p"
    return {"ok": True, "path": str(p.resolve()), "name": p.name, "duration": round(dur, 1),
            "vertical": h > w, "label": f"{int(dur // 60)}:{int(dur % 60):02d} · {'vertical' if h > w else 'wide'} {res} · {gb}"}


_spawned = []   # review servers this page started (the demo stops its own)


def review_url(d):
    """The URL of review.py's page for edit folder d, starting its server (detached) when none answers."""
    sys.path.insert(0, str(REVIEW.parent))
    import review
    url = review.running(d)
    if url:
        return url
    p = subprocess.Popen([sys.executable, str(REVIEW), str(d), "serve", "--no-open"], stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, encoding="utf-8", start_new_session=True)
    _spawned.append(p)
    line = p.stdout.readline().strip()
    return line if line.startswith("http") else None


def running():
    try:
        url = (home() / ".server").read_text(encoding="utf-8").strip()
        urllib.request.urlopen(url.replace("/?t=", "/state?t="), timeout=2).read()
        return url
    except (OSError, ValueError, http.client.HTTPException):   # a stale port another program answers on
        return None


def stable_token():
    """One random token per install, kept 0600 beside state.json, so the address never changes."""
    p = home() / "token"
    try:
        t = p.read_text(encoding="utf-8").strip()
    except OSError:
        t = ""
    if not t:
        p.parent.mkdir(parents=True, exist_ok=True)
        t = secrets.token_urlsafe(32)
        with os.fdopen(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(t)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    return t


class Server(ThreadingHTTPServer):
    # Windows lets a second socket bind a port already in use when SO_REUSEADDR is on (Python's default for
    # HTTPServer), so two homes could share one address and answer each other's requests: bind exclusively there.
    allow_reuse_address = os.name != "nt"


def bind(handler, port=None):
    """PORT, or the port remembered from last time, else the next free one. port=0: any free port (tests)."""
    if port == 0:
        return Server(("127.0.0.1", 0), handler)
    first = port or read().get("port") or PORT
    for p in dict.fromkeys([first] + list(range(PORT, PORT + 20))):
        try:
            return Server(("127.0.0.1", p), handler)
        except OSError:
            continue
    return Server(("127.0.0.1", 0), handler)


def serve(root=None, port=None, open_browser=True):
    """(server, url). A server already running is reused: (None, its url), with the root moved to this one.
    root defaults to the library, so the page works on the same videos wherever Claude Code was started."""
    root = Path(root or library.path()).resolve()
    update(lambda d: d.__setitem__("root", str(root)))
    url = running()
    if url:
        if open_browser:
            webbrowser.open(url)
        return None, url
    token = stable_token()
    srv = bind(make_handler(token), port)
    if port != 0:
        update(lambda d: d.__setitem__("port", srv.server_address[1]))
    url = f"http://127.0.0.1:{srv.server_address[1]}/?t={token}"
    (home() / ".server").write_text(url, encoding="utf-8")
    update(drop_modal_runs)
    askhook.register(srv, url, "home", asks_for, asks_dir)
    threading.Thread(target=run_doctor, daemon=True).start()
    try:   # an update adds a new plugin folder: point nextwork-editor at this one
        import launcher
        launcher.refresh()
    except Exception:
        pass
    if open_browser:
        webbrowser.open(url)
    return srv, url


# ---------------------------------------------------------------- demo
def demo():
    import contextlib
    import io
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="home_demo_"))
    import atexit
    atexit.register(lambda: [p.terminate() for p in _spawned])   # a failed check leaves no review server behind
    os.environ["AI_EDITOR_HOME"] = str(tmp / "home")
    # the self-check never touches the real Trash: "trashed" files are removed from the temp folder instead
    globals()["trash"] = lambda p: (shutil.rmtree(p) if Path(p).is_dir() else Path(p).unlink(missing_ok=True)) or not Path(p).exists()
    for v in keys.VARS.values():
        os.environ.pop(v, None)
    root = tmp / "work"
    os.environ["AI_EDITOR_LIBRARY"] = str(root)          # never the real ~/Movies
    os.environ["AI_EDITOR_SCAN_DIRS"] = str(tmp / "Desktop")
    os.environ["AI_EDITOR_SKILLS"] = str(tmp / "skills")   # style skills land here, never in ~/.claude/skills
    td = root / "creator-teardowns" / "sample.creator"
    td.mkdir(parents=True)
    vids = [{"id": str(i), "title": f"v{i}", "view_count": v, "duration": 30, "thumbnail": f"https://x/{i}.jpg"}
            for i, v in enumerate([1600000, 820000, 410000, 302000, 188000, 151000, 97000, 76000, 49000, 47000, 1000])]
    (td / "videos.json").write_text(json.dumps({"handle": "sample.creator", "platform": "tiktok", "count": 11,
                                                "median_views": 151000, "ranked_by": "views", "videos": vids}), encoding="utf-8")
    calls = []
    globals()["run_doctor"] = lambda: update(lambda d: d.__setitem__("doctor", {"rows": doctor_rows(
        "ok   python               3.12.1 (need 3.10+)\nFIX  renderer             missing\n     run: x\n"
        "--   gemini key           Recommended"), "at": now()}))
    globals()["run_listing"] = lambda *a: calls.append(a)
    globals()["fetch_latest"] = lambda: ("99.0.0", ["Dark mode in the window"])   # never the network in a self-check
    os.environ.pop("CLAUDE_CODE_SESSION_ID", None)   # run from inside Claude Code: never that session
    real_claude_for, own = claude_for, []
    globals()["claude_for"] = own.append   # no Terminal opens here: the plain `wait` below takes each video (sessions part: the real one)
    srv, url = serve(root, 0, open_browser=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base, tok = url.split("/?t=")

    def get(p):
        return json.loads(urllib.request.urlopen(base + p + ("&" if "?" in p else "?") + "t=" + tok).read())

    def post(p, b):
        return json.loads(urllib.request.urlopen(urllib.request.Request(
            base + p + "?t=" + tok, json.dumps(b).encode(), method="POST")).read())

    def out(fn, *a):
        o = io.StringIO()
        with contextlib.redirect_stdout(o):
            fn(*a)
        return o.getvalue().strip()

    # no token, or another Host name pointing here: nothing reads or writes
    for p, data, hdr in (("/state", None, {}), ("/key", b'{"name":"gemini","value":"AIzaX"}', {}),
                         ("/state?t=" + tok, None, {"Host": "evil.example"})):
        try:
            urllib.request.urlopen(urllib.request.Request(base + p, data, hdr, method="POST" if data else "GET"))
            raise AssertionError(f"{p} answered without the token")
        except urllib.error.HTTPError as e:
            assert e.code == 403, (p, e.code)
    r = urllib.request.urlopen(base + "/?t=" + tok)
    page = r.read()   # the brand: pan tile, "cooked." and the pan favicon
    assert b'class="mark"' in page and b"<b>cooked.</b>" in page and b'rel="icon" href="data:image/svg+xml' in page, "brand"
    assert "SameSite=Strict" in r.headers["Set-Cookie"]
    # one theme for the window and every review page: saved in $AI_EDITOR_HOME/ui.json, on <html> before first paint
    assert get("/state")["theme"] == "system" and b"<html data-theme" not in urllib.request.urlopen(base + "/?t=" + tok).read()
    assert post("/theme", {"theme": "light"})["theme"] == "light" and get("/state")["theme"] == "light"
    assert b'<html data-theme="light" lang="en">' in urllib.request.urlopen(base + "/?t=" + tok).read()
    try:
        post("/theme", {"theme": "pink"})
        raise AssertionError("a made-up theme was saved")
    except urllib.error.HTTPError as e:
        assert e.code == 400
    post("/theme", {"theme": "system"})
    # Claude's question (askhook.py): the hook posts it, the showing page polls it and answers, the hook clears it
    assert b'src="/ask.js"' in HTML.read_bytes() and b"askq" in urllib.request.urlopen(base + "/ask.js?t=" + tok).read()
    assert askhook.live() == []   # served, but no page has polled yet
    qs = [{"question": "Tight or loose?", "header": "Cut", "options": [{"label": "Tight", "description": ""}], "multiSelect": False}]
    # no session known anywhere (Claude Code without CLAUDE_CODE_SESSION_ID): any question, as before
    post("/ask", {"id": "q-any", "questions": qs, "session": ""})
    assert get("/ask?v=1")["ask"]["id"] == "q-any"
    post("/ask/clear", {"id": "q-any"})
    (home() / ".listening").write_text("sess-A", encoding="utf-8")   # session A's `wait` is listening
    try:   # a session with no job here and not listening: its question stays in its own terminal
        post("/ask", {"id": "q0", "questions": qs, "session": "sess-Z"})
        raise AssertionError("took another session's question")
    except urllib.error.HTTPError as e:
        assert e.code == 409
    post("/ask", {"id": "q1", "questions": qs, "session": "sess-A"})
    assert get("/ask?v=1")["ask"] == {"id": "q1", "questions": qs, "about": ""} and askhook.live("home") == [url]
    post("/ask/answer", {"id": "q1", "answers": ["Tight"]})
    assert get("/ask?id=q1")["result"] == {"answers": ["Tight"]} and get("/ask")["ask"] is None
    post("/ask/clear", {"id": "q1"})
    assert not srv.asks
    (home() / ".listening").unlink()
    try:   # a POST from another site's page, even with the cookie
        urllib.request.urlopen(urllib.request.Request(base + "/theme?t=" + tok, b'{"theme":"dark"}', {"Origin": "http://evil.example"}, method="POST"))
        raise AssertionError("took a cross-site POST")
    except urllib.error.HTTPError as e:
        assert e.code == 403
    assert post("/theme", {"theme": "system"}) and urllib.request.urlopen(urllib.request.Request(
        base + "/theme?t=" + tok, b'{"theme":"system"}', {"Origin": base}, method="POST")).status == 200
    for _ in range(50):
        if read().get("doctor"):
            break
        time.sleep(0.02)
    st = get("/state")
    assert [(x["name"], x["ok"]) for x in st["doctor"]["rows"]] == [("Python 3.12", True), ("The renderer", False)], st["doctor"]
    assert st["keys"]["gemini"] == {"set": False, "env": False, "last4": None, "check": None} and not st["listening"] and st["profile"] is False
    # keys: a wrong prefix is refused before any request; a good one is verified once, saved, never echoed
    sent = []
    real = keys._get
    keys._get = lambda *a: sent.append(a) or (200, "")
    try:
        r = post("/key", {"name": "gemini", "value": "not-a-key-123"})
        assert not r["ok"] and "AIza" in r["msg"] and "Nothing was sent" in r["msg"] and not sent, r
        secret = "AIza" + "s" * 35
        r = post("/key", {"name": "gemini", "value": secret})
        assert r["ok"] and len(sent) == 1 and secret not in json.dumps(r), r
        keys._get = lambda *a: (401, "bad")
        r = post("/key", {"name": "elevenlabs", "value": "x" * 32})
        assert not r["ok"] and r["msg"].startswith("Not saved. The service rejected"), r
    finally:
        keys._get = real
    st = get("/state")
    assert st["keys"]["gemini"]["set"] and not st["keys"]["elevenlabs"]["set"] and "typesafe" not in st["keys"]
    assert secret not in state_path().read_text(encoding="utf-8") and secret not in json.dumps(st), "a key reached state.json"
    assert keys.get("gemini")[0] == secret
    # Settings: the saved key shows its last 4 and its check, a recheck can fail with the reason, remove keeps the others
    assert st["keys"]["gemini"]["last4"] == "ssss" and st["keys"]["gemini"]["check"]["ok"] and "keychecks" not in st
    keys.save("elevenlabs", "sk_" + "e" * 30)
    assert get("/state")["keys"]["elevenlabs"]["check"] is None   # saved elsewhere: never checked here
    keys._get = lambda *a: (401, "bad")
    try:
        r = post("/key/check", {"name": "gemini"})
    finally:
        keys._get = real
    g = get("/state")["keys"]["gemini"]
    assert not r["ok"] and not g["check"]["ok"] and "rejected" in g["check"]["msg"] and secret not in json.dumps(r), (r, g)
    post("/key/remove", {"name": "gemini"})
    st = get("/state")
    assert not st["keys"]["gemini"]["set"] and st["keys"]["elevenlabs"]["set"] and keys.get("gemini") == (None, None)
    os.environ["GEMINI_API_KEY"] = "AIza" + "v" * 35
    try:
        post("/key/remove", {"name": "gemini"})
        raise AssertionError("removed a key that comes from the environment")
    except urllib.error.HTTPError as e:
        assert e.code == 409
    finally:
        os.environ.pop("GEMINI_API_KEY")
    post("/update/check", {})
    for _ in range(50):
        if get("/state")["update"]["latest"]:
            break
        time.sleep(0.02)
    u = get("/state")["update"]
    assert u["latest"] == "99.0.0" and u["newer"] and u["checked"] and u["notes"] == ["Dark mode in the window"], u
    seen = []
    globals()["fetch_latest"] = lambda: seen.append(1) or ("99.0.0", ["Dark mode in the window"])
    for _ in range(5):   # pressed again and again within the minute: no new GitHub check
        post("/update/check", {})
    time.sleep(0.1)
    assert not seen and get("/state")["update"]["checked"] == u["checked"], seen
    _UPD["at"] -= 61   # a minute later: one press checks again
    post("/update/check", {})
    for _ in range(50):
        if seen:
            break
        time.sleep(0.02)
    assert seen == [1], seen
    # the paste: a profile link names the platform; a bare name is a handle; a website is not a creator
    r = post("/lookup", {"text": "https://www.tiktok.com/@sample.creator"})
    assert r["kind"] == "creator" and r["handle"] == "sample.creator" and r["platform"] == "tiktok" and calls, r
    assert post("/lookup", {"text": "someone"})["ask_platform"] is True
    assert post("/lookup", {"text": "https://example.com/pricing"})["kind"] != "creator"
    one = post("/lookup", {"text": "https://www.tiktok.com/@some.one/video/7412345678901234567"})
    assert one["urls"] and one["handle"] == "some.one" and one["platform"] == "tiktok" and "6 or more" in one["note"], one
    mix = post("/lookup", {"text": "https://youtu.be/abcDEF12345 https://www.youtube.com/shorts/xyzXYZ12345"})
    assert len(mix["urls"]) == 2 and mix["platform"] == "youtube" and mix["handle"] is None, mix
    n = len(calls)
    ig = post("/lookup", {"text": "https://www.instagram.com/someone/"})
    assert ig["platform"] == "instagram" and ig["handle"] == "someone" and len(calls) == n + 1, ig   # listed like TikTok
    post("/lookup", {"text": "@sample.creator"})
    lk = get("/state")["lookup"]["listing"]
    assert [v["role"] for v in lk["videos"]] == ["Top 1", "Top 2", "Top 3", "Top 4", "Top 5", "Top 6, spare", "Top 7, spare",
                                                 "Control, median", "Control, median"], lk["videos"]
    # one format, as fetch.py pick: a 200 s clip among 30 s videos is not studied; pasted links all are
    assert "0" not in [v["id"] for v in picks([{**vids[0], "duration": 200}] + vids[1:], 151000)]
    assert len(picks(vids, 151000, pasted=True)) == len(vids)
    small = picks(vids[:7], 151000)   # 7 listed: 5 top and 2 control, no spare
    assert [v["role"] for v in small][-2:] == ["Control, median", "Control, median"] and len(small) == 7, small
    assert lk["videos"][7]["view_count"] == 151000 and lk["videos"][0]["thumbnail"] == "https://x/0.jpg", lk
    # Start: a teardown job; `wait` prints it as one line and marks it taken
    try:   # Build my skill before the study is done: refused
        post("/job", {"type": "skill"})
        raise AssertionError("skill job before the teardown")
    except urllib.error.HTTPError as e:
        assert e.code == 409
    post("/job", {"type": "teardown", "handle": "sample.creator", "platform": "tiktok", "take": ["captions", "pace", "evil"]})
    assert out(cmd_wait, 0.01) == f"JOB teardown @sample.creator platform=tiktok take=captions,pace root={root.resolve()}"
    assert latest(read(), "teardown")["state"] == "taken"
    # Claude reports steps; the page reads measured numbers from style.json only, as they appear
    ns = argparse.Namespace
    st = lambda **k: out(cmd_status, ns(**{"job": "teardown", "step": None, "detail": None, "progress": None,
                                           "result": None, "done": False, "failed": None, **k}))
    st(step="Listed 40 videos, ranked by views")
    st(step="Reading captions and faces", detail="video 3 of 7", progress=0.4)
    st(step="Reading captions and faces", detail="video 4 of 7")   # a script's self-report, same text: in place
    S = get("/state")
    t = S["jobs"][-1]
    assert t["measured"]["tiles"] == [] and [s["done"] for s in t["steps"]] == [True, False]
    assert t["steps"][-1]["detail"] == "video 4 of 7" and S["claude_seen"] >= t["u"] - 1, "heartbeat recorded"
    assert out(cmd_status, ns(job="modal", step="x", detail=None, progress=None, result=None, done=False, failed=None,
                              if_active=True)).startswith("no modal job on the page") and latest(read(), "modal") is None, "--if-active never makes a job"
    # Connect Modal runs in this server, not through Claude: setup.py's lines become steps, the result, a failure
    fake = tmp / "fake_setup.py"
    fake.write_text("import sys\nif sys.argv[1] == 'modal-connect':\n    print('STEP Installing Modal')\n"
                    "    print('LOGIN https://modal.com/token-flow/tf-x', flush=True)\n    print('STEP Building')\n"
                    "    print('CREDIT $0.10 used this month')\n    print('Connected.')\nelse:\n    print('STEP Rendering')\n"
                    "    print('FAIL Your Modal account is out of credit.')\n", encoding="utf-8")
    real_setup, globals()["SETUP"] = SETUP, fake
    try:
        assert post("/modal/connect", {})["id"] == "modal-1"
        for _ in range(100):
            if latest(read(), "modal")["state"] != "taken":
                break
            time.sleep(0.05)
        c = latest(read(), "modal")
        assert c["state"] == "done" and c["owner"] == "home" and [x["text"] for x in c["steps"]] == ["Installing Modal", "Building"], c
        assert c["result"] == {"login": "https://modal.com/token-flow/tf-x", "credit": "$0.10 used this month"}, c["result"]
        run_modal("modal-test", update(lambda d: queue(d, "modal-test", {}))["id"])
        t = latest(read(), "modal-test")
        assert t["state"] == "failed" and t["error"] == "Your Modal account is out of credit.", t
        assert get("/state")["modal"] == {"installed": False, "logged_in": False}
    finally:
        globals()["SETUP"] = real_setup
        update(lambda d: d.__setitem__("jobs", [j for j in d["jobs"] if j["type"] not in ("modal", "modal-test")]))
    (td / "style.json").write_text(json.dumps({"videos": 7, "pace": {"wpm": 186, "median_shot_s": 1.9, "max_pause_s": 0.3},
                                               "zoom": {"kind": "punch", "per_min": 6.0, "scale": 1.12},
                                               "captions": {"present": True, "words_per_caption": 2, "y_pct": 62.4,
                                                            "case": "upper", "color": "#FFFFFF", "box": True,
                                                            "box_color": "#000000", "entrance": "pop"}}), encoding="utf-8")
    m = get("/state")["jobs"][-1]["measured"]
    assert [x["big"] for x in m["tiles"]] == ["1.9 s", "1.12x", "186"], m["tiles"]
    assert m["rows"]["captions"].startswith("2 words at a time, 62% down") and "graphics" not in m["rows"], m["rows"]
    ns_dir = td.parent / "no.scale"   # a zoom with no scale measured: no "to Nonex"
    ns_dir.mkdir()
    (ns_dir / "style.json").write_text(json.dumps({"zoom": {"kind": "punch", "per_min": 6, "ease": "none"}}), encoding="utf-8")
    nm = measured(td.parent.parent, "no.scale")
    assert nm["rows"]["zooms"] == "Punch zooms, 6 a minute." and nm["tiles"][0]["big"] == "6", nm
    shutil.rmtree(ns_dir)
    st(result=json.dumps({"reject": ["The follow-for-part-2 close."]}), done=True)
    t = get("/state")["jobs"][-1]
    assert t["state"] == "done" and t["result"]["reject"] and all(s["done"] for s in t["steps"])
    # the teardown page and its files only, never outside the creator's folder
    (td / "teardown.html").write_text("<h1>t</h1>", encoding="utf-8")
    assert urllib.request.urlopen(base + "/td/sample.creator/teardown.html?t=" + tok).read() == b"<h1>t</h1>"
    for bad in ("/td/sample.creator/../videos.json", "/td/sample.creator/%2e%2e/%2e%2e/x"):
        try:
            urllib.request.urlopen(base + bad + "?t=" + tok)
            raise AssertionError(bad)
        except urllib.error.HTTPError as e:
            assert e.code == 404
    # Build my skill, then a video: the path is probed, the job names the creator
    post("/job", {"type": "skill", "take": ["captions", "graphics"]})
    assert out(cmd_wait, 0.01) == f"JOB skill @sample.creator take=captions,graphics,entrances,layout root={root.resolve()}"
    vid = tmp / "take.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=30:duration=2",
                    "-pix_fmt", "yuv420p", str(vid)], check=True)
    p = post("/probe", {"path": str(vid)})
    assert p["ok"] and p["vertical"] and p["label"].startswith("0:02 · vertical"), p
    assert not post("/probe", {"path": str(tmp / "nope.mp4")})["ok"]
    r = urllib.request.urlopen(urllib.request.Request(base + "/upload?name=drop.mp4&t=" + tok, vid.read_bytes(), method="POST"))
    assert json.loads(r.read())["ok"] and (root / "videos" / "drop.mp4").stat().st_size == vid.stat().st_size
    post("/job", {"type": "video", "path": str(vid), "aspect": "9:16"})
    ed = root.resolve() / "edits" / "take"
    assert out(cmd_wait, 0.01) == f"JOB video {vid.resolve()} as=@sample.creator aspect=9:16 edit={ed} root={root.resolve()}"
    assert ed.is_dir() and str(ed) in json.loads((tmp / "home" / "library.json").read_text(encoding="utf-8"))["edits"]
    (home() / ".listening").touch()
    assert out(cmd_wait, 0.01).startswith("ALREADY")
    (home() / ".listening").unlink()
    # cancel reaches `wait` as its own line
    post("/job", {"type": "teardown", "handle": "other", "platform": "youtube"})
    out(cmd_wait, 0.01)
    post("/cancel", {"type": "teardown"})
    assert out(cmd_wait, 0.01) == "JOB cancel teardown" and latest(read(), "teardown")["state"] == "cancelled"
    assert running() == url and serve(root, 0, open_browser=False) == (None, url)
    # sessions: a job sticks to the Claude Code session that took it
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-A"
    held = update(lambda d: [j.update(state="done") or j["id"] for j in d["jobs"] if j["state"] in ("queued", "taken")])
    import launcher
    opened = []
    launcher.open_claude = lambda folder, prompt: (opened.append((folder, prompt)) or True, "claude '" + prompt + "'")
    globals()["claude_for"] = real_claude_for
    vs = lambda **k: out(cmd_status, ns(**{"job": "video", "step": None, "detail": None, "progress": None, "result": None,
                                         "done": False, "failed": None, "if_active": False, **k}))
    ja = update(lambda d: queue(d, "video", {"path": str(vid), "as": "mine", "aspect": "9:16", "edit": str(root / "edits" / "team-250k-general")}))
    assert out(cmd_wait, 0.01).startswith("JOB video ")
    ja = next(j for j in read()["jobs"] if j["id"] == ja["id"])
    assert ja["state"] == "taken" and ja["owner"] == "sess-A", ja
    (home() / ".listening").write_text("sess-A", encoding="utf-8")   # A listens again while it works: still busy
    assert get("/state")["busy"] == {"id": ja["id"], "name": "team-250k-general"}
    # Make my video while the listening Claude is busy: a Claude of its own opens for the new one at once (claude_for)
    jx = post("/job", {"type": "video", "path": str(vid), "aspect": "9:16", "as": "mine"})["id"]
    jxj = next(j for j in read()["jobs"] if j["id"] == jx)
    assert opened == [(str(root.resolve()), f"/ai-editor:start take job {jx}")] and jxj["claude"]["opened"] and jxj["reserved"], (opened, jxj)
    assert not takeable(jxj, "sess-A", read()), "the busy Claude never takes it"
    update(lambda d: next(j for j in d["jobs"] if j["id"] == jx).update(state="done"))
    opened.clear()
    (home() / ".listening").unlink()
    post("/ask", {"id": "qa", "questions": qs, "session": "sess-A"})
    assert get("/ask?v=1")["ask"]["about"] == "team-250k-general"
    # an image on the answer (over the 1 MB cap of other POSTs): saved beside A's edit, its path in the answer
    edir = root / "edits" / "team-250k-general"
    assert srv.asks["qa"]["dir"] == str((edir / "asks") if edir.is_dir() else home().parent / "asks")
    big = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + bytes(2 << 20)).decode()
    post("/ask/answer", {"id": "qa", "answers": ["like this"], "images": [[big]]})
    shot = Path(srv.asks["qa"]["dir"]) / "ask-qa-q1-img1.png"
    assert get("/ask?id=qa")["result"] == {"answers": [f"like this\nAttached images: {shot} (Read each one to see it)"]}
    assert shot.stat().st_size == (2 << 20) + 8
    post("/ask/clear", {"id": "qa"})
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-B"   # a second terminal: never writes to A's job
    assert vs(step="B was here", done=True).startswith("no video job")
    assert next(j for j in read()["jobs"] if j["id"] == ja["id"]) == ja, "A's job untouched"
    # Make my video with nobody listening: a Claude of its own opens with that one job, which no other wait takes
    opened.clear()
    jb = post("/job", {"type": "video", "path": str(vid), "aspect": "9:16", "as": "mine"})["id"]
    assert opened == [(str(root.resolve()), f"/ai-editor:start take job {jb}")], opened
    assert next(j for j in read()["jobs"] if j["id"] == jb)["claude"] == {"opened": True, "command": f"claude '/ai-editor:start take job {jb}'"}
    assert vs(step="B grabs it").startswith("no video job"), "status never takes a queued job inside Claude"
    os.environ.pop("CLAUDE_CODE_SESSION_ID")
    vs(step="a script", if_active=True)   # nor a script's own report (it lands on the taken job, A's, outside Claude)
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-B"
    assert next(j for j in read()["jobs"] if j["id"] == jb)["state"] == "queued"
    assert not takeable(next(j for j in read()["jobs"] if j["id"] == jb), "sess-A", read()), "A makes one video at a time"
    assert not takeable(next(j for j in read()["jobs"] if j["id"] == jb), "sess-B", read()), "reserved for the new Claude"
    update(lambda d: next(j for j in d["jobs"] if j["id"] == jb).__setitem__("reserved", time.time() - RESERVE_S - 1))
    assert not takeable(next(j for j in read()["jobs"] if j["id"] == jb), "sess-B", read()), "its window opened: held longer"
    update(lambda d: next(j for j in d["jobs"] if j["id"] == jb).__setitem__("reserved", time.time()))
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-C"
    assert out(cmd_wait, 0.01, jb).startswith("JOB video ") and next(j for j in read()["jobs"] if j["id"] == jb)["owner"] == "sess-C"
    (home() / ".listening").write_text("sess-A", encoding="utf-8")   # A's wait still listens while C works
    assert get("/state")["busy"]["id"] == ja["id"], "busy follows the listening session, not the newest Claude"
    (home() / ".listening").unlink()
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-B"
    assert out(cmd_wait, 0.01, jb).startswith("GONE"), "another Claude has it"
    assert claude_for(jb) is None, "never a second Claude for a taken job"
    # "Connect Claude" (nobody listening): one terminal with the listen prompt, none while it is pending or once one listens
    opened.clear()
    assert post("/connect", {})["status"] == "opened" and opened == [(str(root.resolve()), launcher.LISTEN)], opened
    assert post("/connect", {})["status"] == "pending" and len(opened) == 1, "no second terminal while one is pending"
    assert get("/state")["connect"] > time.time() - 5, "the page knows a connect is pending"
    (home() / ".listening").touch()
    assert post("/connect", {})["status"] == "listening" and len(opened) == 1
    (home() / ".listening").unlink()
    tj = post("/job", {"type": "teardown", "handle": "listen", "platform": "youtube"})["id"]
    assert out(cmd_wait, 0.01).startswith("JOB teardown") and "connect" not in read(), "the new Claude's wait ends the pending connect"
    update(lambda d: next(j for j in d["jobs"] if j["id"] == tj).__setitem__("state", "done"))
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-A"   # A's status goes to A's job, never C's
    vs(step="Cutting out the mistakes", done=True)
    j1, j2 = (next(j for j in read()["jobs"] if j["id"] == i) for i in (ja["id"], jb))
    assert j1["state"] == "done" and j1["steps"][-1]["text"] == "Cutting out the mistakes" and j2["state"] == "taken" and not j2["steps"]
    # C stops (closed its terminal): after STALE_S with no report, another session adopts the job
    update(lambda d: next(j for j in d["jobs"] if j["id"] == jb).__setitem__("u", time.time() - STALE_S - 1))
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-B"
    assert vs(step="Picked up where it stopped").startswith("adopted " + jb)
    assert next(j for j in read()["jobs"] if j["id"] == jb)["owner"] == "sess-B"
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-C"
    assert vs(done=True).startswith("no video job"), "the stopped session's job is B's now"
    os.environ["CLAUDE_CODE_SESSION_ID"] = "sess-B"
    vs(done=True)
    assert next(j for j in read()["jobs"] if j["id"] == jb)["state"] == "done" and get("/state")["busy"] is None
    os.environ.pop("CLAUDE_CODE_SESSION_ID")
    # one Make, two separate videos: an idle listening Claude takes the first now, the second gets a Claude of its own;
    # with nobody listening, each gets its own. Images and links go with each video (assets.json, the job line)
    opened.clear()
    (home() / ".listening").write_text("sess-L", encoding="utf-8")
    png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJ"
           "RU5ErkJggg==")
    big = "data:image/png;base64," + base64.b64encode(bytes(2 << 20)).decode()   # over the 1 MB cap of other POSTs
    adds = [{"url": "https://github.com/a/b", "say": "the repo"}, {"image": png, "name": "chart.png", "say": "the chart"},
            {"image": big, "name": "big.png"}]
    r2 = post("/job", {"type": "video", "paths": [str(vid), str(vid)], "join": False, "aspect": "9:16", "as": "mine", "assets": adds})
    assert [p for _, p in opened] == [f"/ai-editor:start take job {r2['ids'][1]}"], opened
    for jid, e in zip(r2["ids"], r2["edits"]):
        items = json.loads((Path(e) / "assets.json").read_text(encoding="utf-8"))["items"]
        assert [x.get("url") or x["name"] for x in items] == ["https://github.com/a/b", "chart.png", "big.png"], items
        assert items[0]["site"] == "github" and items[1]["say"] == "the chart" and (Path(e) / items[1]["src"]).is_file()
        assert f" assets={Path(e) / 'assets.json'} " in job_line(next(j for j in read()["jobs"] if j["id"] == jid), root)
    assert takeable(next(j for j in read()["jobs"] if j["id"] == r2["ids"][0]), "sess-L", read())
    assert not takeable(next(j for j in read()["jobs"] if j["id"] == r2["ids"][1]), "sess-L", read()), "the other Claude's job"
    try:
        post("/job", {"type": "video", "path": str(vid), "as": "mine", "assets": [{"image": png}] * (askhook.MAX_IMAGES + 1)})
        raise AssertionError("13 images were taken")
    except urllib.error.HTTPError as e:
        assert e.code == 400
    (home() / ".listening").unlink()
    opened.clear()
    r3 = post("/job", {"type": "video", "paths": [str(vid), str(vid)], "join": False, "aspect": "9:16", "as": "mine"})
    assert [p for _, p in opened] == [f"/ai-editor:start take job {i}" for i in r3["ids"]], opened
    assert not (Path(r3["edits"][0]) / "assets.json").exists() and " assets=" not in job_line(latest(read(), "video"), root)
    # no terminal can open (Linux without x-terminal-emulator): the job keeps the command, the editor shows it to run
    rec = launcher.open_claude
    launcher.open_claude = lambda folder, prompt: (False, "claude '" + prompt + "'")
    r4 = post("/job", {"type": "video", "path": str(vid), "aspect": "9:16", "as": "mine"})["id"]
    assert next(j for j in read()["jobs"] if j["id"] == r4)["claude"] == {"opened": False, "command": f"claude '/ai-editor:start take job {r4}'"}
    launcher.open_claude = rec
    update(lambda d: [j.update(state="done") for j in d["jobs"] if j["id"] in r2["ids"] + r3["ids"] + [r4]])
    globals()["claude_for"] = own.append
    update(lambda d: [j.update(state="taken") for j in d["jobs"] if j["id"] in held])   # the earlier checks' jobs, as they were

    # New video: several picked clips as one video -> one JOB with the takes file, in the order picked
    (tmp / "Desktop").mkdir()
    clips = []
    for i, n in enumerate(("b.mov", "a.mov", "c.mp4")):
        c = tmp / "Desktop" / n
        shutil.copyfile(vid, c)
        os.utime(c, (1000 + i, 1000 + i))
        clips.append(c)
    rec = get("/recent")
    assert not rec["scan_home"] and [r["name"] for r in rec["videos"]] == ["drop.mp4", "take.mp4"], rec   # videos/ and picked before
    post("/scanhome", {"on": True})
    rec = get("/recent")["videos"]
    assert [r["name"] for r in rec] == ["drop.mp4", "take.mp4", "c.mp4", "a.mov", "b.mov"] and rec[2]["duration"] == 2.0 and rec[2]["vertical"], rec
    assert urllib.request.urlopen(base + rec[0]["thumb"] + "&t=" + tok).read()[:2] == b"\xff\xd8", "thumbnail is a jpeg"
    order = [str(clips[1]), str(clips[0]), str(clips[2])]
    r = post("/job", {"type": "video", "paths": order, "join": True, "as": "mine", "aspect": "16:9"})
    assert len(r["ids"]) == 1
    ea = root.resolve() / "edits" / "a"
    assert out(cmd_wait, 0.01) == f"JOB video takes={ea / 'takes.txt'} as=mine aspect=16:9 edit={ea} root={root.resolve()}"
    assert (ea / "takes.txt").read_text(encoding="utf-8").split() == [str(Path(x).resolve()) for x in order]
    # ...or separate videos: one JOB each, each with its own edit folder
    own.clear()
    r = post("/job", {"type": "video", "paths": order, "join": False, "as": "@sample.creator", "aspect": "9:16"})
    assert len(r["ids"]) == 3 and own == r["ids"], (own, r)   # nobody listening: a Claude of its own for each
    lines = [out(cmd_wait, 0.01) for _ in range(3)]
    assert [ln.split(" edit=")[1].split()[0] for ln in lines] == [str(root.resolve() / "edits" / n) for n in ("a-2", "b", "c")], lines

    # Add your script: a file's text only comes back (token, 1 MB file, 200 KB text); it is saved with the job
    def send_script(name, data, t=tok):
        try:
            r = urllib.request.urlopen(urllib.request.Request(base + "/script?name=" + quote(name) + (t and "&t=" + t), data, method="POST"))
            return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")
    sk = "MANAGER: We need to scale this.\nINTERN: We need to scale this.\n"
    assert send_script("s.txt", sk.encode(), "")[0] == 403
    assert send_script("s.txt", ("\ufeff" + sk).encode()) == (200, {"ok": True, "text": sk.strip()})
    dx = io.BytesIO()
    import zipfile
    with zipfile.ZipFile(dx, "w") as z:
        W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        z.writestr("word/document.xml", f'<w:document {W}><w:body><w:p><w:r><w:t>MANAGER:</w:t><w:tab/><w:t>Hi.</w:t></w:r></w:p>'
                                        '<w:p><w:r><w:t>INTERN: Hi.</w:t></w:r></w:p></w:body></w:document>')
    assert send_script("s.docx", dx.getvalue())[1]["text"] == "MANAGER: Hi.\nINTERN: Hi."
    pdf = b"%PDF-1.4\n1 0 obj<<>>stream\nBT /F1 12 Tf 72 700 Td (MANAGER: We need to scale.) Tj 0 -14 Td [(INTERN:) -300 (Why?)] TJ ET\nendstream"
    assert send_script("s.pdf", pdf)[1]["text"] == "MANAGER: We need to scale.\nINTERN: Why?"
    for name, data in (("s.pdf", b"%PDF-1.4\nstream\nBT <00120034> Tj ET\nendstream"), ("s.docx", b"not a zip"),
                       ("s.exe", b"MZ"), ("big.txt", b"a " * 600_000), ("long.txt", b"line\n" * 50_000)):
        code, r = send_script(name, data)
        assert code == 400 and not r["ok"] and r["msg"] and "\u2014" not in r["msg"], (name, code, r)
    # the readers stay bounded: a long kerned PDF line, a Flate bomb, a .docx unpacking past the limit, entities
    import zlib
    t0 = time.time()
    tj = b"stream\nBT " + b"[(MANAGER: I don) 20 ('t know) -300 (why.)] TJ " * 30000 + b"ET\nendstream"
    assert pdf_text(tj).startswith("MANAGER: I don't know why.") and time.time() - t0 < 2, time.time() - t0
    assert pdf_text(b"stream\n" + zlib.compress(b"BT (one two three) Tj ET" + b" " * (SCRIPT_UNPACK + 10)) + b"\nendstream") == ""

    def docx(xml):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", xml)
        return b.getvalue()
    big = docx(f'<w:document {W}><w:body><w:p><w:r><w:t>Hi there you.</w:t></w:r></w:p></w:body></w:document>'.encode()
               + b" " * (SCRIPT_UNPACK + 1))
    assert len(big) < 5 * SCRIPT_MAX and script_text("s.docx", big) == ""
    assert script_text("s.docx", docx('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa">]>'
                                      f'<w:document {W}><w:body><w:p><w:r><w:t>&a;&a;</w:t></w:r></w:p></w:body></w:document>')) == ""
    c = http.client.HTTPConnection(base.split("//")[1])
    c.request("POST", "/script?name=s.txt&t=" + tok, b"hi", {"Content-Length": "nope"})
    assert c.getresponse().status == 400
    c.close()
    post("/job", {"type": "video", "path": str(vid), "aspect": "9:16", "as": "mine", "script": sk})
    got = out(cmd_wait, 0.01)
    es = Path(re.search(r" edit=(\S+) script=", got).group(1))   # the next free take-N name
    assert es.parent == root.resolve() / "edits" and got.endswith(f"edit={es} script={es / 'script.md'} root={root.resolve()}"), got
    assert (es / "script.md").read_text(encoding="utf-8") == sk and not list(home().rglob("s.*"))
    try:
        post("/job", {"type": "video", "path": str(vid), "script": "x" * (SCRIPT_MAX + 1)})
        raise AssertionError("a script over 200 KB was queued")
    except urllib.error.HTTPError as e:
        assert e.code == 400 and not (es.parent / f"take-{int(es.name.split('-')[1]) + 1}").exists()
    assert all("takes=" not in ln and "as=@sample.creator" in ln for ln in lines)
    # a script's report (progress.py) lands on the video its edit folder names, never on the newest one
    sr = lambda step, key=None: out(cmd_status, ns(job="video", step=step, detail=None, progress=None, result=None,
                                                   done=False, failed=None, if_active=True, key=key))
    ev = {n: root.resolve() / "edits" / n for n in ("a-2", "b", "c")}
    sr("Wrote down every word you said", ev["a-2"] / "transcript.json")
    sr("Cutting out the mistakes", ev["a-2"])
    sr("Wrote down every word you said", ev["a-2"])   # a step from before, said again: a new row, not the old one
    sr("Cutting out the mistakes", ev["c"])
    assert sr("Cutting out the mistakes") == "no video job on the page", "several taken, no key: skipped"
    assert sr("Cutting out the mistakes", root.resolve() / "edits" / "zzz") == "no video job on the page", "no match: skipped"
    vj = {Path(j["args"]["edit"]).name: [s["text"] for s in j["steps"]] for j in read()["jobs"] if j["type"] == "video"}
    assert vj["a-2"] == ["Wrote down every word you said", "Cutting out the mistakes", "Wrote down every word you said"], vj
    assert vj["c"] == ["Cutting out the mistakes"] and vj["b"] == [], vj
    try:   # a creator not studied is refused
        post("/job", {"type": "video", "paths": order[:1], "as": "@nobody"})
        raise AssertionError("an unstudied creator was queued")
    except urllib.error.HTTPError as e:
        assert e.code == 400
    # Copy a creator does not block: while @newbie is studied, a video in that style can already be queued
    post("/job", {"type": "teardown", "handle": "newbie", "platform": "tiktok"})
    assert out(cmd_wait, 0.01).startswith("JOB teardown @newbie")
    assert {"id": "@newbie", "studying": True}.items() <= next(c for c in get("/library")["styles"] if c["id"] == "@newbie").items()
    post("/job", {"type": "video", "paths": order[:1], "as": "@newbie"})
    q = latest(read(), "video")
    assert sr("Wrote down every word you said", q["args"]["edit"]) == "no video job on the page"   # never takes it
    assert latest(read(), "video")["state"] == "queued" and not latest(read(), "video")["steps"]
    assert " as=@newbie " in out(cmd_wait, 0.01)
    # Getting started: from the real state, so it holds across reloads
    gs = get("/state")["getting_started"]
    assert gs["style"] and not gs["video"], gs   # @sample.creator was analysed above; no cut is ready yet
    sid0 = post("/job", {"type": "setup"})["id"]
    assert post("/job", {"type": "setup"})["id"] == sid0 and latest(read(), "setup")["state"] == "queued"   # one at a time
    update(lambda d: d.__setitem__("jobs", [j for j in d["jobs"] if j["id"] != sid0]))
    post("/later", {"names": ["style"]})
    assert read()["style_skipped"] and get("/state")["getting_started"]["style"]
    assert getting_started({**read(), "style_skipped": False, "jobs": [], "doctor": {"rows": [{"ok": False}]}, "root": str(tmp)}) == \
        {"setup": False, "style": False, "video": False}   # a fresh library: nothing done yet
    # Analyse a format: links from several creators are one job, every link in its picks.txt
    fl = ["https://www.tiktok.com/@a.one/video/7412345678901234567", "https://www.tiktok.com/@b.two/video/7412345678901234568",
          "https://www.tiktok.com/@c.three/video/7412345678901234569"]
    post("/job", {"type": "teardown", "urls": fl, "take": ["captions", "pace"]})
    ft = latest(read(), "teardown")
    assert ft["args"]["handle"].startswith("links-") and ft["args"]["platform"] == "tiktok" and ft["args"]["take"] == ["captions", "pace"], ft
    assert Path(ft["args"]["urls"]).read_text(encoding="utf-8").split() == fl
    update(lambda d: d.__setitem__("jobs", [j for j in d["jobs"] if j["id"] != ft["id"]]))   # not Claude's to take here
    # Analysed from: references.json first, else the measured videos (a transcript each); http(s) only
    rd = tmp / "refs"
    (rd / "transcripts").mkdir(parents=True)
    (rd / "videos.json").write_text(json.dumps({"handle": "refs", "platform": "tiktok", "videos": [
        {"id": "1", "webpage_url": "https://www.tiktok.com/@refs/video/1", "title": "one"},
        {"id": "2", "webpage_url": "https://www.tiktok.com/@refs/video/2", "title": "not measured"},
        {"id": "3", "webpage_url": "javascript:alert(1)", "title": "bad"}]}), encoding="utf-8")
    (rd / "transcripts" / "1.txt").write_text("x", encoding="utf-8")
    (rd / "transcripts" / "3.txt").write_text("x", encoding="utf-8")
    assert references(rd) == {"videos": [{"url": "https://www.tiktok.com/@refs/video/1", "title": "one", "thumbnail": None}],
                              "profile": "https://www.tiktok.com/@refs"}, references(rd)
    write_references(rd)
    (rd / "transcripts" / "1.txt").unlink()
    assert references(rd)["videos"][0]["title"] == "one"   # kept in references.json once the analysis ended
    # Build it yourself: a second own style, listed as own, can be the default, and goes when deleted
    oid = post("/style/new", {"name": "Clean talking head"})["id"]
    own = next(c for c in get("/library")["styles"] if c["id"] == oid)
    assert oid.startswith("@own-") and own["own"] and own["name"] == "Clean talking head" and not own.get("studying"), own
    assert next(c for c in get("/library")["styles"] if c["id"] == "mine")["own"]
    assert post("/style/default", {"id": oid})["default_style"] == oid and get("/library")["default_style"] == oid
    assert post("/style/set", {"id": oid, "key": "zoom.preset", "value": "none"})["presets"]["zoom"]["sel"] == "none"
    # Motion: amount and a kind on the style page reach the style the plan reads; the library's menu archives
    pg = post("/style/set", {"id": oid, "key": "motion.amount", "value": 0.5})
    pg = post("/style/set", {"id": oid, "key": "motion.kinds.counter", "value": False})
    assert pg["motion"]["amount"] == 0.5 and pg["motion"]["kinds"]["counter"] is False and pg["motion"]["changed"], pg["motion"]
    assert post("/style/set", {"id": oid, "key": "motion.look", "value": "mono"})["motion"]["look"] == "mono"
    lid = "likes-0123456789"
    (Path(os.environ["AI_EDITOR_HOME"]) / "motion-library" / lid).mkdir(parents=True)
    (Path(os.environ["AI_EDITOR_HOME"]) / "motion-library" / lid / "meta.json").write_text(json.dumps(
        {"id": lid, "type": "likes", "kind": "counter", "line": "over 10,000 people", "made": 1, "archived": False}), encoding="utf-8")
    assert [e["id"] for e in post("/style/set", {"id": oid, "key": "motion.amount", "value": 1})["motion"]["library"]] == [lid]
    assert post("/motion/archive", {"id": lid})["library"][0]["archived"] and motion_mod().starting_point("likes") is None
    try:
        post("/motion/use", {"id": "../etc"})
        raise AssertionError("a path for an id was accepted")
    except urllib.error.HTTPError as e:
        assert e.code == 404, e.code
    assert post("/motion/delete", {"id": lid})["ok"] and not motion_mod().lib_entries()
    assert post("/style/delete", {"id": oid})["ok"] and get("/library")["default_style"] == "mine"
    assert oid not in [c["id"] for c in get("/library")["styles"]] and not (root / "creator-teardowns" / oid[1:]).exists()
    # Archive: off the styles list (Styles, Home and Pick a style all read it), files kept; Unarchive brings it back
    aid = post("/style/new", {"name": "Old reels"})["id"]
    assert post("/style/archive", {"id": aid})["ok"]
    lib = get("/library")
    assert aid not in [c["id"] for c in lib["styles"]] and [c["id"] for c in lib["archived_styles"]] == [aid]
    assert (root / "creator-teardowns" / aid[1:] / "style.json").is_file(), "archive leaves the files alone"
    assert post("/style/archive", {"id": aid, "on": False})["ok"] and aid in [c["id"] for c in get("/library")["styles"]]
    assert get("/library")["archived_styles"] == []
    for bad, code in (("mine", 400), ("@nope", 404)):   # the default style stays until another is made default
        try:
            post("/style/archive", {"id": bad})
            raise AssertionError(bad)
        except urllib.error.HTTPError as e:
            assert e.code == code, (bad, e.code)
    post("/style/default", {"id": aid})
    try:
        post("/style/archive", {"id": aid})
        raise AssertionError("default archived")
    except urllib.error.HTTPError as e:
        assert e.code == 400
    post("/style/default", {"id": "mine"})
    assert post("/style/archive", {"id": aid})["ok"] and post("/style/delete", {"id": aid})["ok"]
    assert get("/library")["archived_styles"] == [] and not (root / "creator-teardowns" / aid[1:]).exists()
    # a skill style: style.json names a skill, the card and page show the skill and no controls, the job carries it
    kd, sks = root / "creator-teardowns" / "own-skilled", root / ".claude" / "skills"
    kd.mkdir()
    (kd / "style.json").write_text(json.dumps({"handle": "own-skilled", "own": True, "skill": "my-edit-style",
                                               "zoom": {"kind": "punch", "per_min": 6}}), encoding="utf-8")
    for n_, md_ in (("my-edit-style", "---\nname: my-edit-style\ndescription: >\n  Cuts a reel in my look. Then more.\n---\n"
                                      "Runs `my-captions` on the cut, then `nope-skill`.\n"), ("my-captions", "---\nname: my-captions\n---\n")):
        (sks / n_).mkdir(parents=True)
        (sks / n_ / "SKILL.md").write_text(md_, encoding="utf-8")
    kc = next(c for c in get("/library")["styles"] if c["id"] == "@own-skilled")
    assert kc["kind"] == "skill" and kc["skill"] == "my-edit-style" and kc["found"] and kc["facts"] == [] \
        and kc["description"] == "Cuts a reel in my look.", kc
    assert next(c for c in get("/library")["styles"] if c["id"] == "mine")["kind"] == "settings"
    kp = get("/style?id=@own-skilled")
    assert kp["kind"] == "skill" and not {"presets", "values", "changed"} & set(kp) and kp["skill"]["calls"] == ["my-captions"], kp
    kj = post("/job", {"type": "video", "path": str(vid), "as": "@own-skilled", "aspect": "9:16"})["id"]
    kj = next(j for j in read()["jobs"] if j["id"] == kj)
    assert kj["args"]["skill"] == "my-edit-style" and " skill=my-edit-style root=" in job_line(kj, root), kj
    update(lambda d: d.__setitem__("jobs", [j for j in d["jobs"] if j["id"] != kj["id"]]))   # not Claude's to take here
    shutil.rmtree(kj["args"]["edit"])
    (kd / "style.json").write_text(json.dumps({"own": True, "skill": "gone-skill"}), encoding="utf-8")
    kc = next(c for c in get("/library")["styles"] if c["id"] == "@own-skilled")
    assert kc["kind"] == "skill" and kc["found"] is False and kc["description"] == "", kc
    (kd / "style.json").write_text(json.dumps({"own": True, "skill": "my-edit-style"}), encoding="utf-8")
    assert post("/style/delete", {"id": "@own-skilled"})["ok"] and (sks / "my-edit-style" / "SKILL.md").is_file()   # never its skill
    shutil.rmtree(kd, ignore_errors=True)
    ks = {x["name"]: x["description"] for x in get("/skills")["skills"]}
    assert ks.get("my-edit-style") == "Cuts a reel in my look." and "my-captions" not in ks and "start" not in ks and "my-style" not in ks, ks   # no edit words; the editor's own
    nid = post("/style/new", {"name": "Reel edit", "skill": "my-edit-style"})["id"]
    nc = next(c for c in get("/library")["styles"] if c["id"] == nid)
    assert nc["kind"] == "skill" and nc["own"] and nc["found"] and nc["name"] == "Reel edit", nc
    try:
        post("/style/new", {"name": "Bad", "skill": "../x"})
        raise AssertionError("a bad skill name made a style")
    except urllib.error.HTTPError as e:
        assert e.code == 400
    assert post("/style/archive", {"id": nid})["ok"] and nid in [c["id"] for c in get("/library")["archived_styles"]]   # skill styles archive too
    assert post("/style/delete", {"id": nid})["ok"] and (sks / "my-edit-style" / "SKILL.md").is_file()
    assert not (root / "creator-teardowns" / nid[1:]).exists() and get("/library")["archived_styles"] == []
    post("/scanhome", {"on": False})   # picked files stay listed without the opt-in
    assert {r["name"] for r in get("/recent")["videos"]} == {"a.mov", "b.mov", "c.mp4", "drop.mp4", "take.mp4"}

    # the library: an edit made from another folder (A) is listed when the page serves the library (B)
    other = tmp / "folderA" / "edits" / "away"
    subprocess.run([sys.executable, str(REVIEW), str(other), "start", "--video", str(vid), "--stage", "cut"],
                   check=True, capture_output=True, cwd=tmp / "Desktop")
    lib = get("/library")
    names = {v["name"]: v for v in lib["videos"]}
    assert names["away"]["status"] == "ready" and names["away"]["dir"] == str(other.resolve()), names
    assert names["a"]["status"] == "cutting" and names["a"]["thumb"], names["a"]
    assert lib["path"] == str(root.resolve())
    # its review page: started on demand, the home page redirects to it; a folder outside the library is refused
    rq = urllib.request.Request(base + "/review?dir=" + quote(str(other)) + "&t=" + tok)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a):
            return None
    try:
        urllib.request.build_opener(NoRedirect).open(rq)
        raise AssertionError("no redirect")
    except urllib.error.HTTPError as e:
        assert e.code == 302 and e.headers["Location"].startswith("http://127.0.0.1:"), e.code
        rurl = e.headers["Location"]
    assert json.loads(urllib.request.urlopen(rurl.replace("/?t=", "/state?t=")).read())["folder"] == str(other.resolve())
    try:
        urllib.request.urlopen(base + "/review?dir=" + quote(str(tmp)) + "&t=" + tok)
        raise AssertionError("a folder outside the library")
    except urllib.error.HTTPError as e:
        assert e.code == 404
    # style cards: My style (the default style until a creator is saved), then each studied creator, measured only
    st = {c["id"]: c for c in lib["styles"]}
    assert list(st) == ["mine", "@sample.creator", "@sample-card-edit", "@newbie"], list(st)
    sm = st["@sample-card-edit"]   # the sample style the plugin ships: listed for every user, by its own name
    assert sm["sample"] and sm["name"] == "Card edit" and sm["line"].startswith("A sample style") and sm["cover"], sm
    assert sm["facts"][1] == ["Captions", "1 word at a time"] and not st["@sample.creator"]["sample"], sm["facts"]
    assert st["@sample.creator"]["facts"] == [["Cut every", "1.9 s"], ["Zooms", "Punch, 6 a minute"], ["Captions", "2 words at a time"]]
    assert st["@sample.creator"]["look"]["box_color"] == "#000000" and st["mine"]["line"].startswith("The default")
    assert st["mine"]["learned_from"] == 0, "no feedback yet: no \"Learned from N videos\""
    (tmp / "home" / "profile.json").write_text(json.dumps({"creators": [{"handle": "sample.creator"}]}), encoding="utf-8")
    mine = get("/library")["styles"][0]
    assert mine["line"].startswith("Your saved style. Started from @sample.creator.") and mine["facts"][0] == ["Cut every", "1.9 s"]
    assert mine["skill"] == "my-style" and st["@sample.creator"]["skill"] == "edit-like-sample-creator"
    assert mine["base"] == "sample.creator" and st["@sample.creator"]["base"] == "sample.creator", "the style page names where My style started"
    assert mine["named"] is False and mine["suggest"] == "Like @sample.creator", "a new style offers a name from its source"
    assert st["@sample.creator"]["suggest"] == "@sample.creator style" and suggest_name("mine", None).endswith(" style")
    assert place(Path.home() / "Movies" / "AI Editor") == "Movies › AI Editor" and (lambda lb: lb["place"] == place(lb["path"]))(get("/library"))
    # My styles: a style's page; a change there is a correction at that style's scope (overrides + its skill)
    pg = get("/style?id=@sample.creator")
    assert pg["values"]["captions.words_per_caption"] == 2 and pg["changed"] == [] and pg["rules"] == [], pg
    assert any(c.startswith("pace (a cut every 1.9 s") for c in pg["copies"]), pg["copies"]
    pg = post("/style/set", {"id": "@sample.creator", "key": "captions.words_per_caption", "value": 3})
    ov = json.loads((td / "style.overrides.json").read_text(encoding="utf-8"))
    assert ov == {"captions": {"words_per_caption": 3}} and pg["values"]["captions.words_per_caption"] == 3, (ov, pg["values"])
    assert pg["changed"] == ["captions.words_per_caption"] and pg["rules"][0]["said"] == 1 and pg["skill"]["exists"]
    md = Path(pg["skill"]["path"])
    assert md == tmp / "skills" / "edit-like-sample-creator" / "SKILL.md" and "3 words a caption (set on the style page)" in md.read_text(encoding="utf-8")
    assert json.loads((td / "style.json").read_text(encoding="utf-8"))["captions"]["words_per_caption"] == 2, "measured style untouched"
    assert get("/library")["styles"][1]["facts"][2] == ["Captions", "3 words at a time"]
    try:
        post("/style/set", {"id": "@sample.creator", "key": "zoom.per_min", "value": 99})
        raise AssertionError("out of range accepted")
    except urllib.error.HTTPError as e:
        assert e.code == 400
    pg = post("/style/set", {"id": "mine", "key": "zoom.per_min", "value": 0})   # My style: taste.json, the my-style skill
    assert json.loads((tmp / "home" / "taste.json").read_text(encoding="utf-8"))["zoom"]["per_min"] == 0 and pg["skill"]["name"] == "my-style"
    assert "No zooms (set on the style page)" in (tmp / "skills" / "my-style" / "SKILL.md").read_text(encoding="utf-8")
    # forget from the page: the rule, its override and its skill bullet go; a rule of another style is refused
    rid = get("/style?id=@sample.creator")["rules"][0]["id"]
    try:
        post("/style/forget", {"id": "mine", "rule": rid})
        raise AssertionError("forgot another style's rule")
    except urllib.error.HTTPError as e:
        assert e.code == 400
    pg = post("/style/forget", {"id": "@sample.creator", "rule": rid})
    assert pg["rules"] == [] and json.loads((td / "style.overrides.json").read_text(encoding="utf-8")) == {"captions": {}}, (pg["rules"], (td / "style.overrides.json").read_text(encoding="utf-8"))
    assert "3 words a caption" not in md.read_text(encoding="utf-8")
    # presets: a creator's page offers its measured captions first, pre-selected, then the built-in tiles;
    # picking a tile saves captions.preset at that style's scope, Reset puts the section back
    pz = get("/style?id=@sample.creator")["presets"]
    assert [t["id"] for t in pz["captions"]["tiles"]][:5] == ["", "plain", "bold", "karaoke", "minimal"] and pz["captions"]["sel"] == ""
    assert pz["captions"]["tiles"][0]["name"] == "Theirs" and pz["captions"]["tag"] == "from @sample.creator"
    assert pz["cut"]["sel"] == "natural" and pz["cards"]["on"] and "look" not in pz["cards"] and pz["sound"]["sfx"]
    assert pz["cards"]["sel"] == "", "a creator's style has no card cap until the user picks one"
    tb = [t["block"] for t in pz["captions"]["tiles"]]
    pg = post("/style/set", {"id": "@sample.creator", "key": "captions.y_pct", "value": 50})
    assert [t["block"] for t in pg["presets"]["captions"]["tiles"]] == tb and pg["presets"]["captions"]["y"] == 50, "a drag leaves the tiles alone"
    post("/style/reset", {"id": "@sample.creator", "section": "captions"})
    pg = post("/style/set", {"id": "@sample.creator", "key": "captions.preset", "value": "karaoke"})
    assert pg["presets"]["captions"]["sel"] == "karaoke" and pg["presets"]["captions"]["tag"] == "you changed this"
    assert json.loads((td / "style.overrides.json").read_text(encoding="utf-8"))["captions"]["preset"] == "karaoke"
    pg = post("/style/set", {"id": "@sample.creator", "key": "zoom.preset", "value": "punchy"})
    assert pg["presets"]["zoom"]["sel"] == "punchy" and "Zooms: Punchy (snaps in fast) (set on the style page)" in md.read_text(encoding="utf-8")
    for key, v in (("cards.per_min", 2), ("cards.on", False), ("music", True), ("cut.max_pause", 0.25)):
        pg = post("/style/set", {"id": "@sample.creator", "key": key, "value": v})
    c = pg["presets"]
    assert (c["cards"]["sel"], c["cards"]["on"], c["sound"]["music"], c["cut"]["sel"]) == ("rarely", False, True, "relaxed"), c
    for bad in (("captions.preset", "../x"), ("zoom.preset", "wobbly"), ("look.preset", "poster-red")):
        try:
            post("/style/set", {"id": "@sample.creator", "key": bad[0], "value": bad[1]})
            raise AssertionError(f"{bad} accepted")
        except urllib.error.HTTPError as e:
            assert e.code == 400
    pg = post("/style/reset", {"id": "@sample.creator", "section": "captions"})
    assert pg["presets"]["captions"]["sel"] == "" and "captions" not in [r["setting"].split(".")[0] for r in pg["rules"]]
    assert "preset" not in (json.loads((td / "style.overrides.json").read_text(encoding="utf-8")).get("captions") or {})
    for sec in ("zoom", "cut", "cards", "sound"):
        pg = post("/style/reset", {"id": "@sample.creator", "section": sec})
    assert pg["rules"] == [] and pg["presets"]["cards"]["on"] and pg["presets"]["zoom"]["tag"] != "you changed this", pg["rules"]
    pm = get("/style?id=mine")["presets"]   # My style started from the creator: the creator's captions on, zooms closest to measured
    assert pm["captions"]["sel"] == "" and pm["zoom"]["sel"] == "none", pm["zoom"]   # taste.json zoom.per_min 0 above
    # Tell it what to change: one JOB line for Claude, at that style's scope
    post("/job", {"type": "style-change", "style": "@sample.creator", "text": "captions a bit bigger,\n fewer zooms"})
    assert out(cmd_wait, 0.01) == f"JOB style-change @sample.creator style={(td / 'style.json').resolve()} text=captions a bit bigger, fewer zooms"
    post("/job", {"type": "style-change", "style": "mine", "text": "no whooshes"})
    assert out(cmd_wait, 0.01) == "JOB style-change my-style text=no whooshes"
    out(cmd_status, argparse.Namespace(job="style-change", step="Saved: no sound effects", detail=None, progress=None,
                                       result=json.dumps({"changed": ["No sound effects"]}), done=True, failed=None))
    assert get("/style?id=mine")["job"]["result"]["changed"] == ["No sound effects"]
    # previews prefer raw takes: a finished render (its own captions) is never used behind a sample caption
    assert is_output(root / "edits" / "x" / "render.mp4") and is_output(tmp / "v-edit2.mp4") and is_output(root / "edits" / "x" / "review" / "a.mp4")
    assert is_output(ed / "anything.mp4", [ed]) and not is_output(root / "videos" / "drop.mp4", [ed])
    assert all("raw" in v for v in get("/recent")["videos"])
    assert get("/state")["page"] == live.page_version(HTML, HTML.with_name("ask.js")), "the tab's reload key"
    # hide or trash a listed clip, remove an edit: only what the page listed, to the Trash (faked here), never a folder
    assert library.trash_cmd("/a b/x.mov", "Darwin")[-1] == "/a b/x.mov" and "item 1 of argv" in " ".join(library.trash_cmd("x", "Darwin"))
    assert library.trash_cmd("C:/x y.mp4", "Windows")[-1] == "C:/x y.mp4" and library.trash_cmd("/x", "Linux") == ["gio", "trash", "/x"]
    real_trash, binned = globals()["trash"], []
    globals()["trash"] = lambda p: (binned.append(Path(p).name), shutil.rmtree(p) if Path(p).is_dir() else Path(p).unlink()) and True
    try:
        desk = tmp / "Desktop"
        desk.mkdir(exist_ok=True)
        for n in ("keep.mov", "bye.mp4"):
            shutil.copyfile(vid, desk / n)
        post("/scanhome", {"on": True})
        names = lambda: [v["name"] for v in get("/recent")["videos"]]
        assert {"keep.mov", "bye.mp4"} <= set(names())
        assert post("/clip/hide", {"path": str(desk / "keep.mov")})["ok"] and "keep.mov" not in names() and (desk / "keep.mov").exists()
        assert post("/clip/trash", {"path": str(desk / "bye.mp4")})["ok"] and not (desk / "bye.mp4").exists() and "bye.mp4" not in names()
        for bad in ({"path": str(tmp / "home" / "state.json")}, {"path": str(desk)}, {"path": str(desk / "keep.mov")}, {}):
            try:   # not listed, a folder, hidden (no longer listed), nothing
                post("/clip/trash", bad)
                raise AssertionError(bad)
            except urllib.error.HTTPError as e:
                assert e.code == 400, bad
        yv = root / "videos" / "yours.mp4"   # a clip in the library's videos/ ("Your videos"): hidden, never moved
        yv.parent.mkdir(exist_ok=True)
        shutil.copyfile(vid, yv)
        assert next(v for v in get("/recent")["videos"] if v["name"] == "yours.mp4")["where"] == "Your videos"
        assert post("/clip/hide", {"path": str(yv)})["ok"] and "yours.mp4" not in names() and yv.exists()
        hid = root / "edits" / "hide-me"
        hid.mkdir(parents=True)
        shutil.copyfile(vid, hid / "cut.mp4")
        assert str(hid.resolve()) in [v["dir"] for v in get("/library")["videos"]]
        assert post("/edit/hide", {"dir": str(hid)})["ok"] and (hid / "cut.mp4").exists() and str(hid.resolve()) not in [v["dir"] for v in get("/library")["videos"]]
        gone = root / "edits" / "old-edit"
        gone.mkdir(parents=True)
        shutil.copyfile(vid, gone / "cut.mp4")
        assert post("/edit/trash", {"dir": str(gone)})["ok"] and not gone.exists() and binned == ["bye.mp4", "old-edit"]
        for bad in (str(tmp), str(root), str(root / "edits" / "nope")):
            try:
                post("/edit/trash", {"dir": bad})
                raise AssertionError(bad)
            except urllib.error.HTTPError as e:
                assert e.code == 400, bad
        post("/scanhome", {"on": False})
    finally:
        globals()["trash"] = real_trash
    # demo photos for empty previews: served behind the token, nothing else from that folder
    assert urllib.request.urlopen(base + "/demo/person-1.jpg?t=" + tok).read()[:2] == b"\xff\xd8"
    for bad, code in (("/demo/../home.py?t=" + tok, 404), ("/demo/person-9.jpg?t=" + tok, 404), ("/demo/person-1.jpg", 403)):
        try:
            urllib.request.urlopen(base + bad)
            raise AssertionError(bad)
        except urllib.error.HTTPError as e:
            assert e.code == code, (bad, e.code)
    # a creator's card shows the creator: a bright frame of their top downloaded video (black first second skipped)
    (td / "video").mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-n", "-f", "lavfi", "-i", "color=black:size=320x568:duration=1.5", "-f", "lavfi",
                    "-i", "color=white:size=320x568:duration=4", "-filter_complex", "[0][1]concat=n=2:v=1", "-pix_fmt", "yuv420p",
                    str(td / "video" / "1.mp4")], check=True)   # "0" has no download: the next one in videos.json order
    fc = next(c for c in get("/library")["styles"] if c["id"] == "@sample.creator")["face"]
    m = library.meta(td / "face.jpg")
    assert fc.startswith("/style/face?id=%40sample.creator") and (m["w"], m["h"]) == (720, 1280), (fc, m)
    px = subprocess.run(["ffmpeg", "-v", "error", "-i", str(td / "face.jpg"), "-vf", "scale=4:4,format=gray", "-f", "rawvideo", "-"],
                        capture_output=True, check=True).stdout
    assert min(px) > 200, "the face frame is the bright one"
    assert urllib.request.urlopen(base + fc + "&t=" + tok).read()[:2] == b"\xff\xd8"
    # their account picture (fetch.py list's avatar.jpg) wins over the frame wherever the creator shows; gone, the frame is back
    subprocess.run(["ffmpeg", "-v", "error", "-n", "-f", "lavfi", "-i", "color=red:size=720x1280", "-frames:v", "1",
                    str(td / "avatar.jpg")], check=True)
    fa = next(c for c in get("/library")["styles"] if c["id"] == "@sample.creator")["face"]
    assert fa.endswith("&a=1") and fa == face_of(root, "sample.creator"), fa
    assert urllib.request.urlopen(base + fa + "&t=" + tok).read() == (td / "avatar.jpg").read_bytes()
    (td / "avatar.jpg").unlink()
    fb = next(c for c in get("/library")["styles"] if c["id"] == "@sample.creator")["face"]
    assert "&a=1" not in fb and urllib.request.urlopen(base + fb + "&t=" + tok).read() == (td / "face.jpg").read_bytes(), fb
    # a style's own preview image: an image (or a clip's frame) cropped to 720x1280 in the style's folder
    img = tmp / "pick.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=red:size=400x300", "-frames:v", "1", str(img)], check=True)

    def put_cover(sid, data, ctype):
        return json.loads(urllib.request.urlopen(urllib.request.Request(
            base + "/style/cover?id=" + quote(sid) + "&t=" + tok, data, {"Content-Type": ctype}, method="POST")).read())
    for sid, f in (("@sample.creator", td / "cover.jpg"), ("mine", root / "my-style" / "cover.jpg")):
        r = put_cover(sid, img.read_bytes(), "image/png")
        m = library.meta(f)
        assert r["ok"] and r["cover"].startswith("/style/cover?id=") and (m["w"], m["h"]) == (720, 1280), (r, m)
        assert next(c for c in get("/library")["styles"] if c["id"] == sid)["cover"] == r["cover"] == get("/style?id=" + quote(sid))["cover"]
        assert urllib.request.urlopen(base + r["cover"] + "&t=" + tok).read()[:2] == b"\xff\xd8"
    for data, ctype in ((b"not an image", "image/png"), (img.read_bytes(), "text/plain")):
        try:
            put_cover("@sample.creator", data, ctype)
            raise AssertionError(ctype)
        except urllib.error.HTTPError as e:
            assert e.code == 400
    assert post("/style/cover/remove", {"id": "mine"})["ok"] and get("/style?id=mine")["cover"] is None
    # naming My style right away: empty falls back to the suggestion; the skill moves and its description
    # (what Claude matches on) carries the new name; keeping "My style" on purpose is a name too
    pg = post("/style/rename", {"id": "mine", "name": "  "})
    assert pg["name"] == "Like @sample.creator" and pg["named"] and pg["skill"]["name"] == "like-sample-creator", pg["name"]
    t = (tmp / "skills" / "like-sample-creator" / "SKILL.md").read_text(encoding="utf-8")
    assert t.startswith("---\nname: like-sample-creator\n") and '"/like-sample-creator"' in t and "# Like @sample.creator\n" in t, t[:600]
    assert not (tmp / "skills" / "my-style").exists() and "Like @sample.creator (/like-sample-creator)" in \
        (tmp / "skills" / "edit-like-sample-creator" / "SKILL.md").read_text(encoding="utf-8"), "the other styles' Not for names it"
    assert get("/library")["styles"][0]["name"] == "Like @sample.creator" and get("/style?id=mine")["named"]
    pg = post("/style/rename", {"id": "mine", "name": "My style"})
    assert pg["name"] == "My style" and pg["named"] and pg["skill"]["name"] == "my-style" and (tmp / "skills" / "my-style" / "SKILL.md").exists()
    # rename from the page: the name and the skill; a taken name or bad characters are refused in plain words
    pg = post("/style/rename", {"id": "@sample.creator", "name": "Fast cuts"})
    assert pg["name"] == "Fast cuts" and pg["skill"]["name"] == "fast-cuts" and pg["skill"]["exists"], pg["skill"]
    assert (tmp / "skills" / "fast-cuts" / "SKILL.md").read_text(encoding="utf-8").startswith("---\nname: fast-cuts\n")
    assert next(c for c in get("/library")["styles"] if c["id"] == "@sample.creator")["name"] == "Fast cuts"
    for bad in ("my style", "a/b", "x"):
        try:
            post("/style/rename", {"id": "@sample.creator", "name": bad})
            raise AssertionError(bad)
        except urllib.error.HTTPError as e:
            assert e.code == 400 and json.loads(e.read())["error"], bad
    assert post("/style/usefor", {"id": "@sample.creator", "text": "talking-head reels about AI tools"})["use_for"] == "talking-head reels about AI tools"
    assert "Use for: talking-head reels about AI tools." in (tmp / "skills" / "fast-cuts" / "SKILL.md").read_text(encoding="utf-8")
    # delete a studied creator's style: its picks, style.json, skill and profile entry go; the measured videos
    # stay unless asked; deleting again is a no-op; My style and anything that is not a handle are refused
    cts = root / "creator-teardowns"
    for f in ("gone.creator/style.json", "gone.creator/videos.json", "gone.creator-mine/style.json"):
        (cts / f).parent.mkdir(exist_ok=True)
        (cts / f).write_text("{}", encoding="utf-8")
    gsk = tmp / "skills" / "edit-like-gone-creator"
    gsk.mkdir(parents=True)
    (gsk / "SKILL.md").write_text("x", encoding="utf-8")
    pf = tmp / "home" / "profile.json"
    pf.write_text(json.dumps({"creators": [{"handle": "gone.creator"}, {"handle": "sample.creator"}]}), encoding="utf-8")
    assert get("/style?id=@gone.creator")["delete_note"].startswith("My style started from @gone.creator.")
    r = post("/style/delete", {"id": "@gone.creator"})
    assert r["ok"] and not gsk.exists() and not (cts / "gone.creator-mine").exists() and not (cts / "gone.creator/style.json").exists(), r
    assert (cts / "gone.creator/videos.json").exists(), "measured videos kept"
    assert "@gone.creator" not in [c["id"] for c in get("/library")["styles"]]
    assert json.loads(pf.read_text(encoding="utf-8"))["creators"] == [{"handle": "sample.creator"}]
    assert post("/style/delete", {"id": "@gone.creator"})["removed"] == []
    post("/style/delete", {"id": "@gone.creator", "measured": True})
    assert not (cts / "gone.creator").exists() and td.exists() and (root / "my-style").exists()
    for bad in ("mine", "@../work", "@a/b", "sample.creator", "@sample.creator-mine", "@sample-card-edit", ""):
        try:
            post("/style/delete", {"id": bad})
            raise AssertionError(bad)
        except urllib.error.HTTPError as e:
            assert e.code == 400, bad
    # the sample style cannot be deleted (above), stays listed with its file, and is pickable for a video
    assert (cts / "sample-card-edit" / "style.json").is_file() and "@sample-card-edit" in [c["id"] for c in get("/library")["styles"]]
    assert get("/style?id=@sample-card-edit")["sample"]
    post("/job", {"type": "video", "path": str(vid), "as": "@sample-card-edit", "aspect": "9:16"})
    assert " as=@sample-card-edit aspect=9:16 " in out(cmd_wait, 0.01)
    # folders: create, move an edit and a style in, rename, delete (items go back to All, files untouched); bad ids refused
    reels = post("/folder/create", {"kind": "videos", "name": "  Reels  "})["id"]
    assert get("/library")["folders"]["videos"]["folders"] == [{"id": reels, "name": "Reels"}]
    edir = get("/library")["videos"][0]["dir"]
    assert post("/folder/move", {"kind": "videos", "item": edir, "folder": reels})["ok"]
    assert get("/library")["folders"]["videos"]["items"] == {edir: reels}
    sf = post("/folder/create", {"kind": "styles", "name": "Fast"})["id"]
    post("/folder/move", {"kind": "styles", "item": "@sample.creator", "folder": sf})
    assert post("/folder/rename", {"kind": "videos", "id": reels, "name": "Shorts"})["folders"]["videos"]["folders"][0]["name"] == "Shorts"
    for path_, bad in (("/folder/create", {"kind": "videos", "name": "shorts"}), ("/folder/create", {"kind": "videos", "name": " "}),
                       ("/folder/create", {"kind": "videos", "name": "x" * 41}), ("/folder/create", {"kind": "nope", "name": "a"}),
                       ("/folder/rename", {"kind": "videos", "id": "nope", "name": "a"}), ("/folder/delete", {"kind": "videos", "id": "nope"}),
                       ("/folder/move", {"kind": "videos", "item": str(tmp), "folder": reels}), ("/folder/move", {"kind": "videos", "item": edir, "folder": "nope"}),
                       ("/folder/move", {"kind": "styles", "item": "@nobody", "folder": sf})):
        try:
            post(path_, bad)
            raise AssertionError(bad)
        except urllib.error.HTTPError as e:
            assert e.code == 400, bad
    post("/folder/delete", {"kind": "videos", "id": reels})
    f = get("/library")["folders"]
    assert f["videos"] == {"folders": [], "items": {}} and Path(edir).is_dir() and edir in [v["dir"] for v in get("/library")["videos"]]
    assert f["styles"]["items"] == {"@sample.creator": sf}
    post("/folder/move", {"kind": "styles", "item": "@sample.creator", "folder": None})
    assert get("/library")["folders"]["styles"]["items"] == {}
    # My style can't be deleted, only reset: feedback rules, taste.json and taste.md go, the style stays
    assert json.loads((tmp / "home" / "taste.json").read_text(encoding="utf-8")), "set above: zoom.per_min 0"
    assert post("/style/reset-all", {})["ok"]
    assert json.loads((tmp / "home" / "taste.json").read_text(encoding="utf-8")) == {} and get("/style?id=mine")["rules"] == []
    assert "No zooms" not in (tmp / "skills" / "my-style" / "SKILL.md").read_text(encoding="utf-8")
    assert "mine" in [c["id"] for c in get("/library")["styles"]]
    # updates: the version on GitHub (faked here) against plugin.json; Update queues one JOB line
    t0 = time.time()
    while not get("/state")["update"]["latest"] and time.time() - t0 < 5:
        time.sleep(0.05)
    u = get("/state")["update"]
    assert u["newer"] and u["latest"] == "99.0.0" and u["installed"] == json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"], u
    assert u["notes"] == ["Dark mode in the window"] and vtuple("2.10.0") > vtuple("2.9.1")
    post("/job", {"type": "update"})
    assert out(cmd_wait, 0.01) == "JOB update"
    # clear working files: counted first, then deleted; the cut, the render and review.json stay
    (other / "chunks").mkdir()
    (other / "chunks" / "chunk-0.mkv").write_bytes(b"x" * 2000)
    shutil.copyfile(vid, other / "cut.mp4")
    c = post("/clean", {})
    assert c["files"] == 1 and c["bytes"] == 2000 and not c["deleted"] and (other / "chunks" / "chunk-0.mkv").exists()
    c = post("/clean", {"yes": True})
    assert c["deleted"] and not (other / "chunks").exists()
    assert (other / "cut.mp4").exists() and (other / "review.json").exists() and vid.exists()
    assert get("/disk")["library"] > 0
    assert urllib.request.urlopen(base + "/media?p=" + quote(str(other / "cut.mp4")) + "&t=" + tok).read() == vid.read_bytes()
    try:
        urllib.request.urlopen(base + "/media?p=" + quote(str(vid)) + "&t=" + tok)
        raise AssertionError("served a file outside the library's videos")
    except urllib.error.HTTPError as e:
        assert e.code == 404
    assert get("/library")["custom"] == str(custom.path(False)) and not custom.path(False).exists()   # made on first open
    try:   # Show in Finder only opens the library or one of its videos
        post("/reveal", {"dir": str(tmp)})
        raise AssertionError("revealed a folder outside the library")
    except urllib.error.HTTPError as e:
        assert e.code == 400
    for pr in _spawned:
        pr.terminate()
        pr.wait()
    srv.shutdown()
    srv.server_close()

    # same address every time: the fixed port (the next one when taken, then remembered) and a stable token
    import socket
    global PORT
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        PORT = sk.getsockname()[1]
    (home() / ".server").unlink()
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", PORT))
        busy.listen()
        s1, u1 = serve(root, open_browser=False)
    assert s1.server_address[1] != PORT and read()["port"] == s1.server_address[1], (PORT, s1.server_address)
    s1.server_close()
    s2, u2 = serve(tmp / "Desktop", open_browser=False)   # started from another folder: same address
    assert u2 == u1 and u1.split("?t=")[1] == (home() / "token").read_text(encoding="utf-8").strip()
    if os.name != "nt":
        assert (home() / "token").stat().st_mode & 0o777 == 0o600
    s2.server_close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("home demo ok")


def main():
    if sys.argv[1:2] == ["demo"]:
        return demo()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("serve")
    s.add_argument("--root", help="the folder creator-teardowns/ and edits/ live in (default: the library)")
    s.add_argument("--port", type=int, help=f"default {PORT}, or the port it had last time")
    s.add_argument("--no-open", action="store_true")
    s = sp.add_parser("wait")
    s.add_argument("--job", help="take this job only (the page opened a new Claude for it)")
    s = sp.add_parser("status")
    s.add_argument("job", choices=JOBS)
    s.add_argument("--step")
    s.add_argument("--detail")
    s.add_argument("--progress", type=float)
    s.add_argument("--result", help="JSON, or @file.json")
    s.add_argument("--done", action="store_true")
    s.add_argument("--failed", help="a plain sentence for the page")
    s.add_argument("--if-active", action="store_true", help="only onto a taken job, never taking one (progress.py)")
    s.add_argument("--key", help="with --if-active: the edit folder or handle the job is for")
    a = ap.parse_args()
    if a.cmd == "serve":
        srv, url = serve(a.root, a.port, open_browser=not a.no_open)
        print(url, flush=True)
        if srv:
            threading.Thread(target=live.reexec_on_change, args=(srv,), daemon=True).start()   # newest code, same port
            try:
                srv.serve_forever()
            except KeyboardInterrupt:
                pass
        else:
            print("already serving" + ("" if a.no_open else "; opened it"))
    elif a.cmd == "wait":
        cmd_wait(job=a.job)
    elif a.cmd == "status":
        sys.exit(cmd_status(a))


if __name__ == "__main__":
    main()
