#!/usr/bin/env python3
"""Pull a creator's videos + word-level transcripts, via yt-dlp and Whisper or Scribe.

Commands:

  doctor      checks the tools, and prints the exact fix for anything missing.
              Run it first.

  setkey      optional. Saves an ElevenLabs key once, for every folder. Paste it
              when asked (it stays hidden), or pipe it in: pbpaste | ... setkey
              `setkey --gemini` saves a Gemini key the same way (the look pass).

  list        yt-dlp --flat-playlist over a profile -> videos.json + a ranked
              table on stdout. No downloads, no API key, no cost.

  transcribe  transcribes the picked videos through transcribe.py next to this
              file: the N most-viewed plus the 2 videos closest to the median as
              a control group, plus 2 spares, all of the creator's usual length
              (pasted links: every one). Whisper (free, local) by default; Scribe v2 when
              an ElevenLabs key exists. Uses video/<id>.mp4 when visual.py
              downloaded it, else downloads the audio (needs ffmpeg).

  demo        self-check of the ranking and control-group logic.

Whisper is free and drops some fillers. Scribe keeps every filler and false
start, which makes the voice profile better. --engine picks one.

Usage:
  python3 scripts/fetch.py doctor
  python3 scripts/fetch.py setkey [--gemini]
  python3 scripts/fetch.py list <handle> [--platform tiktok] [--limit 40]
  python3 scripts/fetch.py transcribe <handle> [--top 8] [--control 2] [--spare 2] [--ids ID,ID]
                                                [--engine whisper|scribe]

Output lands in ./creator-teardowns/<handle>/, under the folder you run it from.

Cost: list and Whisper are free. Scribe is ~0.37c per minute of audio ($0.22/hour).

Exit codes: 0 ok - 1 error, or doctor found something to fix - 2 usage
"""
import argparse
import datetime
import glob
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException
from pathlib import Path

import _net  # noqa: E402
import _progress  # noqa: E402
from transcribe import KEY_FILE, VENV_PY, find_key

OUT_ROOT = Path.cwd() / "creator-teardowns"
SELF = Path(__file__).resolve()
TRANSCRIBE = SELF.with_name("transcribe.py")
LOCK = SELF.parents[1] / "requirements.lock"   # ai-editor's pins for what this skill uses (check_plugins.py keeps it in sync)
OS = platform.system()  # Darwin, Windows, Linux
PY = "py" if OS == "Windows" else "python3"

PROFILE_URL = {
    "tiktok": "https://www.tiktok.com/@{h}",
    "youtube": "https://www.youtube.com/@{h}/shorts",
    "instagram": "https://www.instagram.com/{h}/reels/",
}

AVATAR_MAX = 2_000_000   # bytes: an account picture is ~50-300 KB
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
# the picture square in the middle of a 720x1280 frame over a blurred copy: a 9:16 card shows it whole, a round crop shows only it
AVATAR_VF = ("[0]split[a][b];[a]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,boxblur=40,eq=brightness=-0.12[bg];"
             "[b]crop='min(iw,ih)':'min(iw,ih)',scale=720:720[fg];[bg][fg]overlay=0:280")

# Fields we keep off the flat-playlist dump. Anything else is noise.
KEEP = ("id", "title", "description", "duration", "view_count", "like_count",
        "comment_count", "repost_count", "save_count", "timestamp", "webpage_url")


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def ytdlp():
    """The pinned yt-dlp in the tool venv (the version CI checks weekly), else one on PATH."""
    if VENV_PY.exists() and run([str(VENV_PY), "-m", "yt_dlp", "--version"]).returncode == 0:
        return [str(VENV_PY), "-m", "yt_dlp"]
    if shutil.which("yt-dlp"):
        return [shutil.which("yt-dlp")]
    return None


def ai_editor_setup():
    """ai-editor's setup.py when that plugin is installed (this repo's copy, or the plugin cache), else None."""
    cands = [SELF.parents[4] / "ai-editor" / "skills" / "setup" / "scripts" / "setup.py"]
    cands += [Path(p) for p in sorted(glob.glob(str(Path.home() / ".claude" / "plugins" / "**" / "skills" / "setup"
                                                    / "scripts" / "setup.py"), recursive=True), reverse=True)]
    return next((p for p in cands if p.is_file() and (p.parents[3] / "lib" / "ai_editor").is_dir()), None)


def install_fixes(setup, os_name=None):
    """The fix lines for the tool venv, all from pinned, hash-checked versions.
    With ai-editor: its bootstrap (first install) and repair (drift). Without: this skill's lock."""
    if setup:
        return {"venv": f'{PY} "{setup}" bootstrap',
                "update": f'claude plugin update ai-editor@nextwork, then {PY} "{setup}" repair',
                "ocr": f'{PY} "{setup}" repair'}
    o = os_name or OS
    # The real folder (AI_EDITOR_HOME moves it), quoted: Git Bash does not expand %USERPROFILE%.
    vdir = VENV_PY.parents[1]
    py, pip = ("py", vdir / "Scripts" / "pip") if o == "Windows" else ("python3", vdir / "bin" / "pip")
    venv = f'{py} -m venv "{vdir}"'
    pip = f'"{pip}"'
    pin = f'{pip} install --no-deps --require-hashes -r "{LOCK}"'
    tail = "   (needs: sudo apt install -y python3-venv)" if o == "Linux" and linux_pm() == "apt" else ""
    return {"venv": f"{venv} && {pin}{tail}",
            "update": f"claude plugin update creator-teardown@nextwork, then {pin}",
            "ocr": pin}


def tool_python():
    """The venv python when it exists: faster-whisper, numpy and pillow live there."""
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


def slug(handle):
    return re.sub(r"[^a-z0-9._-]", "", handle.lstrip("@").lower())


def linux_pm(which=shutil.which):
    """apt, dnf or pacman: the first one this Linux has (apt when none is found)."""
    return next((pm for pm, exe in (("apt", "apt-get"), ("dnf", "dnf"), ("pacman", "pacman")) if which(exe)), "apt")


def fix(mac, win, linux, os_name=None, which=shutil.which):
    """linux: {"apt": ..., "dnf": ..., "pacman": ...}, picked by the package manager this computer has."""
    return {"Darwin": mac, "Windows": win}.get(os_name or OS) or linux[linux_pm(which)]


def reach(v):
    """Views, or likes where the platform hides views (single Instagram reels via yt-dlp)."""
    return v.get("view_count") or v.get("like_count") or 0


def platform_of(urls):
    hosts = {("instagram" if "instagram.com" in u else "youtube" if "youtu" in u else "tiktok") for u in urls}
    return hosts.pop() if len(hosts) == 1 else "mixed"


def count_line(top, control, spare=()):
    """The first line download and transcribe print: --top N takes N plus the control group."""
    extra = f" + {len(spare)} spare (in case the format check leaves some out)" if spare else ""
    return (f"{len(top)} most-viewed + {len(control)} control (closest to the median){extra} = "
            f"{len(top) + len(control) + len(spare)} videos")


def usual_length(videos):
    """(lo, hi) seconds: within 2x of the median listed length, the creator's usual video. None when
    under half the listing has a length (YouTube listings often have none)."""
    ds = [v["duration"] for v in videos if v.get("duration")]
    if len(ds) < max(3, len(videos) / 2):
        return None
    m = statistics.median(ds)
    return m / 2, m * 2


def pick(videos, median, top, control, spare=0):
    """(top, control, spare): the `top` most-viewed, the `control` videos closest to the median, and
    the next `spare` most-viewed.

    The control group is what separates a creator's habits from the reason a
    video won: a move in the winner AND the median videos is a habit.
    A style copies one format, so only videos of the creator's usual length are picked when enough
    are (a 3-minute podcast clip is not their 40-second talking head). The spares are measured too, so
    the format check (format_check.py) can leave a mismatch out and still keep the sample.
    """
    band = usual_length(videos)
    if band:
        usual = [v for v in videos if v.get("duration") and band[0] <= v["duration"] <= band[1]]
        if len(usual) >= top + control + spare:
            videos = usual
    ranked = sorted(videos, key=reach, reverse=True)
    rest = [v for v in ranked[top:] if reach(v)]
    rest.sort(key=lambda v: abs(reach(v) - median))
    ctrl = rest[:control]   # the control comes first: on a small profile the spares give way, not the control
    return ranked[:top], ctrl, [v for v in ranked[top:] if v not in ctrl][:spare]


def cmd_doctor(args):
    required_missing = 0
    print(f"creator-teardown doctor ({ {'Darwin': 'macOS'}.get(OS, OS) })\n")

    def row(state, what, how=None):
        print(f"  {state:<9} {what}")
        if how:
            print(f"  {'':<9} fix: {how}")

    v = sys.version_info
    if v >= (3, 9):
        row("ok", f"Python {v.major}.{v.minor}.{v.micro}")
    else:
        required_missing += 1
        row("FIX", f"Python {v.major}.{v.minor} is too old (need 3.9+)",
            fix("brew install python", "winget install -e --id Python.Python.3.13",
                {"apt": "sudo apt install -y python3", "dnf": "sudo dnf install -y python3",
                 "pacman": "sudo pacman -S --needed --noconfirm python"}))

    fixes = install_fixes(ai_editor_setup())
    venv_fix = fixes["venv"]
    if not VENV_PY.exists():
        required_missing += 1
        row("FIX", f"no tool venv at {VENV_PY.parent.parent} (it holds faster-whisper, numpy, pillow)",
            venv_fix)
    else:
        mods = {m: run([str(VENV_PY), "-c", f"import {m}"]).returncode == 0
                for m in ("faster_whisper", "numpy", "PIL")}
        if all(mods.values()):
            row("ok", f"venv {VENV_PY.parent.parent}: faster-whisper, numpy, pillow")
        else:
            required_missing += 1
            gone = ", ".join(m for m, ok in mods.items() if not ok)
            row("FIX", f"the venv is missing {gone}", venv_fix)

    yt = ytdlp()
    if yt:
        ver = run(yt + ["--version"]).stdout.strip()
        row("ok", f"yt-dlp {ver}")
        try:
            age = (datetime.date.today() - datetime.date(*map(int, ver.split(".")[:3]))).days
            if age > 90:
                row("update", f"yt-dlp is {age} days old. TikTok changes often.", fixes["update"])
        except ValueError:
            pass
    else:
        required_missing += 1
        row("FIX", "yt-dlp is not installed (it lists the videos)", venv_fix)

    if shutil.which("ffmpeg"):
        row("ok", "ffmpeg")
    else:
        required_missing += 1
        row("FIX", "ffmpeg is not installed (it reads the audio and the frames)",
            fix("brew install ffmpeg", "winget install -e --id Gyan.FFmpeg",
                {"apt": "sudo apt install -y ffmpeg", "dnf": "sudo dnf install -y ffmpeg-free",
                 "pacman": "sudo pacman -S --needed --noconfirm ffmpeg"}))

    key, where = find_key()
    if key:
        row("ok", f"ElevenLabs key, from {where}. Transcripts use Scribe v2.")
    else:
        row("optional", "no ElevenLabs key. Transcripts use Whisper (free). A key gives "
            "Scribe v2, which keeps every filler.",
            f"{PY} \"{SELF}\" setkey   (run it in a terminal window, not in the Claude chat)")

    # The look pass: OCR reads the captions, Gemini names the font and graphics style.
    if VENV_PY.exists():
        ocr = "ocrmac" if OS == "Darwin" else "rapidocr_onnxruntime"
        alt = "" if OS == "Darwin" else " or rapidocr"
        has = run([str(VENV_PY), "-c", f"import {ocr}"]).returncode == 0 or (
            OS != "Darwin" and run([str(VENV_PY), "-c", "import rapidocr"]).returncode == 0)
        if has:
            row("ok", f"{ocr}{alt}: captions are measured, not read off images")
        else:
            row("optional", f"no {ocr}{alt}. Without it captions fall back to frame sheets.",
                fixes["ocr"])
    gkey, gwhere = find_key("GEMINI_API_KEY")
    if gkey:
        row("ok", f"Gemini key, from {gwhere}. The look pass runs on Gemini Flash-Lite.")
    else:
        row("optional", "no Gemini key. The look pass falls back to one frame sheet per "
            "video. A free key: aistudio.google.com/apikey",
            f"{PY} \"{SELF}\" setkey --gemini   (in a terminal window, not in the Claude chat)")

    print()
    if required_missing:
        print(f"Not ready: {required_missing} thing{'s' if required_missing > 1 else ''} to fix.")
        sys.exit(1)
    print("Ready.")


def save_key(var, key, path=None):
    """Writes var=key into the key file, keeping the other keys in it."""
    path = path or KEY_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = [ln for ln in (path.read_text(encoding="utf-8").splitlines() if path.exists() else [])
            if ln.strip() and not ln.startswith(f"{var}=")]
    # readable by you only from the moment it exists: created 0600, never written and then chmodded
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.chmod(path, 0o600)   # a file made by an older version
    except OSError:
        pass
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(keep + [f"{var}={key}"]) + "\n")


def cmd_setkey(args):
    import getpass
    name, var, where = (("Gemini", "GEMINI_API_KEY", "aistudio.google.com/apikey") if args.gemini
                        else ("ElevenLabs", "ELEVENLABS_API_KEY",
                              "Developers > API Keys on elevenlabs.io"))
    if sys.stdin.isatty():
        key = getpass.getpass(f"Paste your {name} API key and press Enter (it stays hidden): ")
    else:
        key = sys.stdin.readline()
    key = key.strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,}", key):
        sys.exit(f"That doesn't look like a {name} key. Copy it again from {where}.")
    save_key(var, key)
    print(f"Saved to {KEY_FILE}. It works from any folder now.")
    print(f"Check everything with: {PY} \"{SELF}\" doctor")


def cmd_list(args):
    _progress.step("Listed their videos", key=args.handle)
    handle = slug(args.handle)
    outdir = OUT_ROOT / handle
    outdir.mkdir(parents=True, exist_ok=True)

    if args.urls:
        # Hand-picked mode: a file of one URL per line. This is how the existing
        # teardowns were actually built -- specific videos, not whole profiles.
        targets = [u.strip() for u in Path(args.urls).read_text(encoding="utf-8").splitlines()
                   if u.strip() and not u.strip().startswith("#")]
        if not targets:
            sys.exit(f"no urls in {args.urls}")
        url = f"{len(targets)} hand-picked urls"
        print(f"pulling {url} ...", file=sys.stderr)
        # -i: one private or removed post must not stop the rest.
        r = run(ytdlp() + ["--dump-json", "--no-warnings", "-i", *targets])
        args.platform = platform_of(targets)
        bad = [ln for ln in r.stderr.splitlines() if ln.startswith("ERROR")]
        if bad:
            print(f"{len(bad)} link(s) failed (private, removed, or login-walled):", file=sys.stderr)
            for ln in bad[:10]:
                print("  " + ln[:200], file=sys.stderr)
    else:
        url = PROFILE_URL[args.platform].format(h=handle)
        print(f"pulling {args.limit} from {url} ...", file=sys.stderr)
        r = None
        if args.platform != "instagram":
            r = run(ytdlp() + ["--flat-playlist", "--dump-json", "--no-warnings",
                     "--playlist-end", str(args.limit), url])

    vids, first = [], None
    if r is None:   # an Instagram profile: yt-dlp's instagram:user is broken, the logged-in web API is not
        try:
            vids = ig_videos(handle, args.limit)
        except IgError as e:
            sys.exit(str(e))
    elif not r.stdout.strip():
        hint = list_hint(args.platform, r.stderr)
        sys.exit(f"yt-dlp returned nothing.\n{r.stderr.strip()[:800]}{hint}")

    for line in (r.stdout if r else "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        first = first or d
        v = {k: d.get(k) for k in KEEP}
        if not v.get("webpage_url"):
            v["webpage_url"] = d.get("url")
        # one picture per video for ai-editor's home page; flat listings give a list, full ones one url
        v["thumbnail"] = d.get("thumbnail") or next((t.get("url") for t in d.get("thumbnails") or [] if t.get("url")), None)
        vids.append(v)

    if not vids:
        sys.exit(f"parsed 0 videos.\n{r.stderr.strip()[:800]}")

    by = "views" if any(v.get("view_count") for v in vids) else "likes"
    views = [reach(v) for v in vids if reach(v)]
    median = statistics.median(views) if views else 0
    for v in vids:
        vc = reach(v)
        v["vs_median"] = round(vc / median, 2) if median else None
        v["outlier"] = bool(median and vc >= 3 * median)

    vids.sort(key=reach, reverse=True)
    payload = {"handle": handle, "platform": args.platform, "profile_url": url,
               "count": len(vids), "median_views": median, "ranked_by": by, "videos": vids}
    if args.urls:
        payload["pasted"] = True   # pasted links: every one is measured, the format check keeps one format
    if by == "likes":
        print("No view counts on these links (Instagram hides them from yt-dlp): ranked by likes.",
              file=sys.stderr)
    (outdir / "videos.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(f"\n{len(vids)} videos - median {median:,.0f} {by}\n")
    print(f"{by:>10}  {'xmed':>5}  {'sec':>4}  id")
    for v in vids[:25]:
        flag = " *" if v["outlier"] else "  "
        print(f"{reach(v):>10,}  {str(v['vs_median'] or '-'):>5}"
              f"  {str(v.get('duration') or '-'):>4}  {v['id']}{flag}")
    print(f"\n-> {outdir / 'videos.json'}   (* = >=3x median)")
    # the account picture for ai-editor's cards: a profile's own page, or the first pasted video's account
    prof = (first or {}).get("uploader_url") or (first or {}).get("channel_url") if args.urls else url
    if save_avatar(outdir, prof):
        print(f"-> {outdir / 'avatar.jpg'}")


IG_APP_ID = "936619743392459"   # instagram.com's own web app id: its API answers only with it
IG_BROWSERS = ("chrome", "safari", "firefox", "edge", "brave")
IG_LOGIN = ("To list an Instagram profile, log in to instagram.com in Chrome, then paste it again. "
            "Or paste links to 3-6 reels.")
IG_WAIT = "Instagram asked to wait a few minutes. Paste reel links, or try again soon."
# Run under yt-dlp's Python: the browser's instagram.com login cookies, read locally by yt-dlp's cookie loader,
# as one JSON line on stdout for ig_cookies. Its logger is silenced; nothing else is printed or saved.
IG_COOKIE_CODE = r"""
import json, sys
from yt_dlp.cookies import YDLLogger, extract_cookies_from_browser
class Quiet(YDLLogger):
    def debug(self, *a, **k): pass
    info = warning = error = debug
    def progress_bar(self): return None
for b in sys.argv[1:]:
    try:
        jar = extract_cookies_from_browser(b, logger=Quiet())
    except Exception:
        continue
    c = {k.name: k.value for k in jar if k.name in ("sessionid", "csrftoken", "ds_user_id")
         and (k.domain.lstrip(".") == "instagram.com" or k.domain.endswith(".instagram.com"))}
    if c.get("sessionid"):
        print(json.dumps(c))
        break
"""


class IgError(Exception):
    """A plain sentence the window shows as is."""


def ytdlp_python():
    """A Python that imports yt_dlp: the tool venv's, this one, or the one in the shebang of the yt-dlp on PATH."""
    cands, exe = [str(VENV_PY), sys.executable], shutil.which("yt-dlp")
    try:
        first = open(exe, "rb").readline().decode("utf-8", "replace") if exe else ""
    except OSError:
        first = ""
    if first.startswith("#!") and first[2:].split():
        cands.append(first[2:].split()[0])
    return next((c for c in cands if Path(c).is_file() and run([c, "-c", "import yt_dlp"]).returncode == 0), None)


def ig_cookies():
    """This computer's instagram.com login (sessionid, csrftoken, ds_user_id) from Chrome, Safari, Firefox, Edge
    or Brave, else None. Kept in memory only: never printed, logged or written, and sent only to instagram.com."""
    py = ytdlp_python()
    if not py:
        return None
    try:
        c = json.loads(run([py, "-c", IG_COOKIE_CODE, *IG_BROWSERS], timeout=120).stdout or "null")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return c if isinstance(c, dict) and c.get("sessionid") else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None   # a redirect would carry the login elsewhere: stop it (a 302 reads as logged out)


def ig_get(url, cookies):
    """GET one instagram.com API address with the login. -> (status, parsed JSON or None)."""
    if not url.startswith("https://www.instagram.com/api/"):
        raise ValueError("instagram.com only")
    h = {**UA, "Accept": "application/json", "X-IG-App-ID": IG_APP_ID, "Referer": "https://www.instagram.com/",
         "X-CSRFToken": cookies.get("csrftoken") or "", "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items())}
    op = urllib.request.build_opener(urllib.request.HTTPSHandler(context=_net.context()), _NoRedirect)
    try:
        with op.open(urllib.request.Request(url, headers=h), timeout=20) as r:
            st, body = r.status, r.read(8_000_000)
    except urllib.error.HTTPError as e:
        return e.code, None
    try:
        return st, json.loads(body or b"null")
    except ValueError:
        return st, None


def ig_video(n):
    """One post from web_profile_info (a GraphQL node) or the feed (an item) -> a KEEP row, None when not a video."""
    if "shortcode" in n:
        if not n.get("is_video"):
            return None
        cap = ((((n.get("edge_media_to_caption") or {}).get("edges") or [{}])[0]).get("node") or {}).get("text") or ""
        sc, views, likes = n["shortcode"], n.get("video_view_count"), (n.get("edge_liked_by") or n.get("edge_media_preview_like") or {}).get("count")
        comments, ts, thumb = (n.get("edge_media_to_comment") or {}).get("count"), n.get("taken_at_timestamp"), n.get("display_url") or n.get("thumbnail_src")
    else:
        if n.get("media_type") != 2 or not n.get("code"):
            return None
        cap = (n.get("caption") or {}).get("text") or ""
        sc, views, likes = n["code"], n.get("play_count") or n.get("ig_play_count") or n.get("view_count"), n.get("like_count")
        comments, ts = n.get("comment_count"), n.get("taken_at")
        thumb = next((c.get("url") for c in (n.get("image_versions2") or {}).get("candidates") or []), None)
    return {"id": sc, "title": cap.split("\n")[0][:100], "description": cap, "duration": n.get("video_duration"),
            "view_count": views, "like_count": likes, "comment_count": comments, "repost_count": None, "save_count": None,
            "timestamp": ts, "webpage_url": f"https://www.instagram.com/reel/{sc}/", "thumbnail": thumb}


def ig_check(status, data):
    if status == 429:
        raise IgError(IG_WAIT)
    if status in (301, 302, 303, 307, 308, 401, 403) or isinstance(data, dict) and data.get("require_login"):
        raise IgError(IG_LOGIN)


def ig_videos(handle, limit, cookies=None, get=ig_get):
    """A profile's newest reels, newest first, through the browser's own instagram.com login: web_profile_info for
    the account and its first 12 posts, then the feed endpoint for more (best effort: a refused page keeps what came)."""
    cookies = ig_cookies() if cookies is None else cookies
    if not cookies or not cookies.get("sessionid"):
        raise IgError(IG_LOGIN)
    api = "https://www.instagram.com/api/v1/"
    st, d = get(f"{api}users/web_profile_info/?username={urllib.parse.quote(handle)}", cookies)
    ig_check(st, d)
    user = ((d or {}).get("data") or {}).get("user") if isinstance(d, dict) else None
    if not user:
        raise IgError(f"No Instagram account called @{handle}. Check the spelling.")
    tl = user.get("edge_owner_to_timeline_media") or {}
    if user.get("is_private") and not tl.get("edges"):
        raise IgError(f"@{handle} is private. Paste links to 3-6 of their reels.")
    seen, vids = set(), []

    def add(rows):
        for v in filter(None, map(ig_video, rows)):
            if v["id"] not in seen:
                seen.add(v["id"])
                vids.append(v)
    add(e.get("node") or {} for e in tl.get("edges") or [])
    more, max_id, pages = (tl.get("page_info") or {}).get("has_next_page") and user.get("id"), None, 0
    while more and len(vids) < limit and pages < 8:   # ponytail: 8 pages of 12 posts, a reels-only listing if this is short
        st, f = get(f"{api}feed/user/{urllib.parse.quote(str(user['id']))}/?count=12"
                    + (f"&max_id={urllib.parse.quote(str(max_id))}" if max_id else ""), cookies)
        if st != 200 or not isinstance(f, dict):
            break
        add(f.get("items") or [])
        more, max_id, pages = f.get("more_available") and f.get("next_max_id"), f.get("next_max_id"), pages + 1
    if not vids:
        raise IgError(f"No reels in @{handle}'s newest posts. Paste links to 3-6 reels.")
    return sorted(vids, key=lambda v: v.get("timestamp") or 0, reverse=True)[:limit]


def avatar_url(info=None, page=""):
    """The account picture's address: YouTube's channel JSON (yt-dlp -J, thumbnail id avatar_uncropped), else
    TikTok's profile page (avatarLarger). http(s) only; None when there is none (Instagram, private, changed page)."""
    u = next((t.get("url") for t in (info or {}).get("thumbnails") or [] if t.get("id") == "avatar_uncropped"), None)
    m = None if u else re.search(r'"avatarLarger":"((?:[^"\\]|\\.)+)"', page or "")
    try:
        u = u or (json.loads(f'"{m[1]}"') if m else None)
    except ValueError:
        u = None
    return u if isinstance(u, str) and re.match(r"https?://[^\s\"'<>]+$", u) else None


def save_avatar(outdir, prof):
    """creator-teardowns/<h>/avatar.jpg from a YouTube or TikTok account address (from PROFILE_URL or yt-dlp's JSON,
    never typed input): one cheap call for the picture's address, one capped download, ffmpeg to AVATAR_VF.
    Best effort: False (and any old avatar.jpg kept) when anything is missing."""
    prof = str(prof or "")
    try:
        if re.match(r"https://(www\.)?youtube\.com/", prof) and ytdlp():
            r = run(ytdlp() + ["-J", "--flat-playlist", "--playlist-items", "0", "--no-warnings", prof], timeout=90)
            src = avatar_url(json.loads(r.stdout or "{}"))
        elif re.match(r"https://(www\.)?tiktok\.com/@[\w.-]+/?$", prof):
            with _net.urlopen(urllib.request.Request(prof, headers=UA), timeout=20) as r:
                src = avatar_url(page=r.read(4_000_000).decode("utf-8", "replace"))
        else:
            return False
        if not src:
            return False
        with _net.urlopen(urllib.request.Request(src, headers=UA), timeout=20) as r:
            if not r.headers.get_content_type().startswith("image/"):
                return False
            data = r.read(AVATAR_MAX + 1)
        if len(data) > AVATAR_MAX:
            return False
        raw, tmp = outdir / "avatar.src", outdir / "avatar.tmp.jpg"
        raw.write_bytes(data)
        tmp.unlink(missing_ok=True)
        ok = run(["ffmpeg", "-v", "error", "-n", "-i", str(raw), "-frames:v", "1", "-filter_complex", AVATAR_VF,
                  "-q:v", "3", str(tmp)], timeout=60).returncode == 0 and tmp.is_file()
        if ok:
            os.replace(tmp, outdir / "avatar.jpg")
        return ok
    except (OSError, ValueError, HTTPException, subprocess.SubprocessError) as e:
        print(f"no profile picture: {str(e)[:120]}", file=sys.stderr)
        return False
    finally:
        for f in ("avatar.src", "avatar.tmp.jpg"):
            (outdir / f).unlink(missing_ok=True)


def download_audio(v, audio_dir):
    """Fallback for links Scribe can't fetch. yt-dlp -x needs ffmpeg."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    existing = list(audio_dir.glob(f"{v['id']}.*"))
    if existing:
        return existing[0]
    if not shutil.which("ffmpeg"):
        print("  downloading needs ffmpeg, which isn't installed (doctor has the fix)",
              file=sys.stderr)
        return None
    # -x forces an ffmpeg audio extraction, so a video-only stream fails loudly
    # here instead of producing a silent file. TikTok serves HEVC variants with
    # no audio track under a plain "ba/b" selector.
    r = run(ytdlp() + ["-f", "ba/b", "-x", "--audio-format", "mp3", "--no-warnings",
             "-o", str(audio_dir / "%(id)s.%(ext)s"), v["webpage_url"]])
    existing = list(audio_dir.glob(f"{v['id']}.*"))
    if not existing:
        print(f"  download failed: {r.stderr.strip()[:200]}", file=sys.stderr)
        return None
    return existing[0]


def list_hint(platform, err):
    """What to do when a profile listing comes back empty, read off yt-dlp's error."""
    e = err.lower()
    if platform == "instagram" or "instagram" in e and "login" in e:
        return ("\n\nInstagram profiles are login-walled for yt-dlp. Single reel links "
                "work: paste them one per line into a file and run `list <name> --urls "
                "file`. Or use the creator's TikTok or YouTube handle.")
    if "private" in e:
        return ("\n\nThe platform says this account is private (TikTok also says this when it hides a "
                "profile from yt-dlp). Check the handle, or paste links to single videos (one a line in a "
                "file) and run `list <name> --urls file`.")
    if "429" in e or "too many requests" in e or "rate" in e and "limit" in e:
        return "\n\nRate-limited by the platform. Wait 10-15 minutes and rerun, or use --urls with single links."
    if "does not exist" in e or "404" in e or "not found" in e or "unable to find" in e \
            or "secondary user id" in e:
        return "\n\nNo account by that name, or the platform hid it. Check the handle's spelling (no @ needed) and the --platform."
    return ("\n\nUpdate yt-dlp and try again, or pass links to single videos "
            "with --urls. Run doctor for the update command.")


def flat_text(v, role, data):
    """transcripts/<id>.txt: a stats header and the words. The duration comes from the transcript when
    the listing has none (a YouTube flat listing carries no durations)."""
    text = data.get("text") or " ".join(w.get("text", "") for w in data.get("words", []))
    text = re.sub(r"\s+", " ", text).strip()
    dur = v.get("duration") or data.get("audio_duration_secs") or max(
        (w.get("end", 0) for w in data.get("words", [])), default=0)
    dur = round(dur, 1)
    wpm = round(len(text.split()) / dur * 60) if dur else 0
    return (f"# {v['id']} - {role} - {v.get('view_count') or 0:,} views - {dur}s - {wpm} wpm\n"
            f"# {v['webpage_url']}\n\n{text}\n")


def timed_text(data, gap=0.6):
    """transcripts/<id>.timed.txt: one line a sentence (or a pause over `gap` s), "[m:ss.s] words".
    What a beat map is built from: second marks without reading the word-level JSON."""
    words = [w for w in data.get("words", []) if w.get("type", "word") == "word" and w.get("text", "").strip()]
    lines, cur = [], []
    for k, w in enumerate(words):
        cur.append(w)
        nxt = words[k + 1] if k + 1 < len(words) else None
        if nxt is None or w["text"].rstrip()[-1:] in ".?!" or nxt["start"] - w["end"] > gap:
            t = cur[0]["start"]
            lines.append(f"[{int(t // 60)}:{t % 60:04.1f}] " + " ".join(x["text"].strip() for x in cur))
            cur = []
    return "\n".join(lines) + "\n"


def cmd_transcribe(args):
    handle = slug(args.handle)
    outdir = OUT_ROOT / handle
    meta_path = outdir / "videos.json"
    if not meta_path.exists():
        sys.exit(f"no {meta_path}. Run `list {handle}` first.")
    if not TRANSCRIBE.exists():
        sys.exit(f"missing {TRANSCRIBE}")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    by_id = {v["id"]: v for v in meta["videos"]}
    median = meta.get("median_views") or 0

    if args.ids:
        ids = args.ids.split(",")
        missing = [i for i in ids if i not in by_id]
        if missing:
            sys.exit(f"unknown ids: {', '.join(missing)}")
        todo = [(by_id[i], "picked") for i in ids]
    elif meta.get("pasted"):
        todo = [(v, "pasted") for v in meta["videos"]]
        print(f"{len(todo)} pasted links, all of them")
    else:
        top, control, spare = pick(meta["videos"], median, args.top, args.control, args.spare)
        todo = [(v, "top") for v in top + spare] + [(v, "control") for v in control]
        print(count_line(top, control, spare) + f", median {median:,.0f} views")
    if not todo:
        sys.exit("nothing to transcribe")

    tdir = outdir / "transcripts"
    tdir.mkdir(exist_ok=True)
    engine = args.engine or ("scribe" if find_key()[0] else "whisper")
    total_sec = sum(v.get("duration") or 0 for v, _ in todo)
    cost = f"~{total_sec/60*0.37:.1f}c" if engine == "scribe" else "free"
    length = f"~{total_sec}s audio" if total_sec else "length not listed"
    if engine == "scribe" and not total_sec:
        cost = f"about {len(todo) * 0.4:.0f}c at 60 s a video"
    print(f"{len(todo)} videos, {length}, {engine}, {cost}\n", file=sys.stderr)

    def one(job):
        """(vid, ok) for one video. Runs in a thread: whisper is a subprocess, several run at once."""
        i, (v, role) = job
        vid = v["id"]
        tag = f"[{i}/{len(todo)}] {vid} ({role}, {v.get('view_count') or 0:,} views)"
        tj = tdir / f"{vid}.json"
        if tj.exists() and not args.force:
            print(f"{tag} cached", file=sys.stderr)
            if not (tdir / f"{vid}.timed.txt").exists():   # a teardown from before the timed file
                (tdir / f"{vid}.timed.txt").write_text(timed_text(json.loads(tj.read_text(encoding="utf-8"))), encoding="utf-8")
            return vid, True

        print(f"{tag} transcribing", file=sys.stderr)
        eng = ["--engine", engine]
        local = sorted((outdir / "video").glob(f"{vid}.mp4"))
        if engine == "whisper":
            media = local[0] if local else download_audio(v, outdir / "audio")
            if not media:
                return vid, False
            r = run([tool_python(), str(TRANSCRIBE), str(media), str(tj)] + eng)
            if r.returncode != 0 or not tj.exists():
                print(f"  {vid}: {r.stderr.strip()[-300:]}", file=sys.stderr)
                return vid, False
        else:
            # Scribe fetches TikTok and YouTube links itself: no download, no ffmpeg.
            r = run([sys.executable, str(TRANSCRIBE), v["webpage_url"], str(tj)] + eng)
        if r.returncode != 0 or not tj.exists():
            err = r.stderr.strip()
            if r.returncode == 2 or "HTTP 401" in err or "HTTP 403" in err:
                # A key problem fails every video the same way, so stop here.
                sys.exit(f"{err[:400]}\n\nFix the key, then rerun. "
                         f"Check it with: {PY} \"{SELF}\" doctor")
            print(f"  {vid}: ElevenLabs couldn't fetch the link, downloading the audio instead",
                  file=sys.stderr)
            audio = download_audio(v, outdir / "audio")
            if not audio:
                return vid, False
            r = run([sys.executable, str(TRANSCRIBE), str(audio), str(tj)] + eng)
            if r.returncode != 0 or not tj.exists():
                print(f"  {vid}: {r.stderr.strip()[:300]}", file=sys.stderr)
                return vid, False

        # Flat text + words-per-minute, the two things the analysis reads.
        data = json.loads(tj.read_text(encoding="utf-8"))
        (tdir / f"{vid}.txt").write_text(flat_text(v, role, data), encoding="utf-8")
        (tdir / f"{vid}.timed.txt").write_text(timed_text(data), encoding="utf-8")
        return vid, True

    from parallel import pmap
    _progress.step("Transcribed every word", detail=f"{len(todo)} videos", progress=0, key=handle)
    res = pmap(one, enumerate(todo, 1), threads=True, tick=_progress.ticker("Transcribed every word", len(todo), key=handle))
    done = [vid for vid, ok in res if ok]
    failed = [vid for vid, ok in res if not ok]

    print(f"\n{len(done)} ok, {len(failed)} failed -> {tdir}")
    if failed:
        print(f"failed: {','.join(failed)}  (retry: --ids {','.join(failed)})")


def demo():
    """Self-check: the things that silently corrupt a teardown."""
    # Ranking must survive missing view counts rather than crashing.
    vids = [{"id": "a", "view_count": 900}, {"id": "b", "view_count": None},
            {"id": "c", "view_count": 100}]
    vids.sort(key=lambda v: v.get("view_count") or 0, reverse=True)
    assert [v["id"] for v in vids] == ["a", "c", "b"], vids

    # Outlier flag is relative to THIS creator's median, not an absolute.
    views = [100, 100, 100, 900]
    med = statistics.median(views)
    assert med == 100 and (900 >= 3 * med) and not (100 >= 3 * med)

    import parallel
    parallel.demo()
    # An Instagram profile, off a recorded web API answer (no network): reels only, newest first, the feed's
    # repeat dropped; no login, a 401 and a 429 each give their plain sentence
    node = lambda sc, vid, views, ts: {"shortcode": sc, "is_video": vid, "video_view_count": views, "video_duration": 30.5,
                                       "taken_at_timestamp": ts, "display_url": f"https://cdn.example/{sc}.jpg",
                                       "edge_liked_by": {"count": 10}, "edge_media_to_caption": {"edges": [{"node": {"text": "hook\nmore"}}]}}
    rec = {"web_profile_info": (200, {"data": {"user": {"id": "42", "is_private": False, "edge_owner_to_timeline_media": {
               "page_info": {"has_next_page": True, "end_cursor": "c1"},
               "edges": [{"node": node("R3", True, 900, 300)}, {"node": node("P2", False, None, 200)}, {"node": node("R1", True, 50, 100)}]}}}}),
           "feed/user/42/": (200, {"more_available": False, "next_max_id": None, "items": [
               {"code": "R1", "media_type": 2, "play_count": 50, "taken_at": 100},
               {"code": "R0", "media_type": 2, "play_count": 7000, "taken_at": 50, "video_duration": 12.0,
                "image_versions2": {"candidates": [{"url": "https://cdn.example/R0.jpg"}]}},
               {"code": "C9", "media_type": 8, "taken_at": 40}]})}
    asked = []

    def fake(url, cookies, answers=rec):
        assert url.startswith("https://www.instagram.com/api/v1/") and cookies["sessionid"] == "fake-session", url
        asked.append(url)
        return next(v for k, v in answers.items() if k in url)
    got = ig_videos("sample.creator", 40, {"sessionid": "fake-session", "csrftoken": "t"}, fake)
    assert [v["id"] for v in got] == ["R3", "R1", "R0"], got
    assert got[0]["view_count"] == 900 and got[0]["title"] == "hook" and got[0]["webpage_url"] == "https://www.instagram.com/reel/R3/"
    assert got[2]["thumbnail"] == "https://cdn.example/R0.jpg" and set(got[0]) >= set(KEEP) | {"thumbnail"}
    assert "username=sample.creator" in asked[0] and len(asked) == 2, asked
    for answers, cookies, want in (({}, {}, IG_LOGIN),
                                   ({"web_profile_info": (401, {"require_login": True})}, None, IG_LOGIN),
                                   ({"web_profile_info": (302, None)}, None, IG_LOGIN),
                                   ({"web_profile_info": (429, None)}, None, IG_WAIT)):
        try:
            ig_videos("sample.creator", 40, cookies if cookies is not None else {"sessionid": "fake-session"},
                      lambda u, c, a=answers: fake(u, c, a))
            raise AssertionError("no error")
        except IgError as e:
            assert str(e) == want, (e, want)
    assert "\u2014" not in IG_LOGIN + IG_WAIT
    try:
        ig_get("https://example.com/api/", {"sessionid": "x"})
        raise AssertionError("cookies sent off instagram.com")
    except ValueError:
        pass
    # An empty listing names its cause: a private account is not "update yt-dlp"
    assert "private" in list_hint("tiktok", "ERROR: [tiktok:user] x: This user's account is private.")
    assert "No account" in list_hint("tiktok", "ERROR: [tiktok:user] zz: Unable to extract secondary user ID.")
    assert "--urls" in list_hint("instagram", "") and "update" not in list_hint("youtube", "HTTP Error 429").lower()
    # A YouTube listing has no duration: the transcript's own length goes in the header
    hdr = flat_text({"id": "a", "webpage_url": "u", "view_count": 5}, "top",
                    {"text": "one two three", "words": [{"text": "three", "end": 30.0}]})
    assert "30.0s - 6 wpm" in hdr, hdr

    # The timed transcript: a line per sentence or pause, with its start
    tw = [{"text": "Stop.", "start": 0.0, "end": 0.4}, {"text": " ", "start": 0.4, "end": 0.5, "type": "spacing"},
          {"text": "Do", "start": 0.5, "end": 0.7}, {"text": "this", "start": 0.7, "end": 1.0},
          {"text": "now", "start": 1.9, "end": 2.2}, {"text": "today", "start": 62.3, "end": 62.6}]
    assert timed_text({"words": tw}) == "[0:00.0] Stop.\n[0:00.5] Do this\n[0:01.9] now\n[1:02.3] today\n", timed_text({"words": tw})

    # A Linux fix line uses the package manager this computer has
    lin = {"apt": "A", "dnf": "D", "pacman": "P"}
    assert fix("m", "w", lin, "Linux", lambda e: e == "dnf") == "D" and fix("m", "w", lin, "Linux", lambda e: False) == "A"
    assert fix("m", "w", lin, "Darwin", lambda e: True) == "m"

    # Control group: the videos closest to the median, never a top pick again.
    vids = [{"id": str(i), "view_count": n}
            for i, n in enumerate([9000, 5000, 4800, 4700, 1000, 300])]
    top, control, _ = pick(vids, 4750, 1, 2)
    assert [v["id"] for v in top] == ["0"], top
    assert [v["id"] for v in control] == ["2", "3"], control
    assert count_line(top, control).startswith("1 most-viewed + 2 control") and "= 3 videos" in count_line(top, control)
    # Spares: the next most-viewed, never also a control video.
    top, control, spare = pick(vids, 4750, 1, 2, spare=1)
    assert [v["id"] for v in spare] == ["1"] and [v["id"] for v in control] == ["2", "3"], (spare, control)
    assert "+ 1 spare" in count_line(top, control, spare) and "= 4 videos" in count_line(top, control, spare)
    # A small profile: 7 listed, 5 + 2 control + 2 spare wanted. The control is kept, the spares shrink.
    seven = [{"id": str(i), "view_count": n} for i, n in enumerate([9, 8, 7, 6, 5, 4, 3])]
    top, control, spare = pick(seven, 6, 5, 2, 2)
    assert len(top) == 5 and [v["id"] for v in control] == ["5", "6"] and spare == [], (control, spare)
    # One format: a 200 s podcast clip is not picked from a profile of 30-40 s videos, unless too few are usual.
    lens = [{"id": str(i), "view_count": n, "duration": d}
            for i, (n, d) in enumerate([(9000, 200), (5000, 35), (4800, 30), (4700, 40), (1000, 38), (300, 31)])]
    assert usual_length(lens) == (18.25, 73.0), usual_length(lens)
    top, control, spare = pick(lens, 4750, 2, 2, 1)
    assert "0" not in [v["id"] for v in top + control + spare], (top, control, spare)
    assert [v["id"] for v in pick(lens, 4750, 3, 2, 1)[0]] == ["0", "1", "2"]   # 5 usual, 6 wanted: no filter
    assert usual_length([{"id": "a"}, {"id": "b"}, {"id": "c", "duration": 30}]) is None

    assert slug("@Some.Creator") == "some.creator"

    # The account picture: YouTube's avatar_uncropped, TikTok's avatarLarger (JSON-escaped in the page), http(s) only
    yt = {"thumbnails": [{"id": "banner_uncropped", "url": "https://yt3.example/banner"},
                         {"id": "avatar_uncropped", "url": "https://yt3.example/avatar=s0"}]}
    assert avatar_url(yt) == "https://yt3.example/avatar=s0"
    page = '<script>{"user":{"avatarThumb":"https:\\u002F\\u002Fp16.example\\u002Fs.jpg","avatarLarger":"https:\\u002F\\u002Fp19.example\\u002Fa~1080.jpeg?x=1&y=2"}}</script>'
    assert avatar_url(page=page) == "https://p19.example/a~1080.jpeg?x=1&y=2", avatar_url(page=page)
    assert avatar_url({"thumbnails": [{"id": "0", "url": "https://yt3.example/banner"}]}) is None
    assert avatar_url(page='"avatarLarger":"javascript:alert(1)"') is None and avatar_url(page="") is None
    assert avatar_url({"thumbnails": [{"id": "avatar_uncropped", "url": "file:///etc/passwd"}]}) is None
    assert not save_avatar(Path("/nonexistent"), "https://www.instagram.com/x/") and not save_avatar(Path("/nonexistent"), "")

    # Instagram links carry likes, not views: rank and pick on likes.
    ig = [{"id": "a", "like_count": 900}, {"id": "b", "like_count": 100}, {"id": "c", "like_count": 120}]
    top, control, _ = pick(ig, 120, 1, 1)
    assert [v["id"] for v in top] == ["a"] and [v["id"] for v in control] == ["c"], (top, control)
    assert platform_of(["https://www.instagram.com/reel/X/", "https://instagram.com/p/Y"]) == "instagram"
    assert platform_of(["https://www.tiktok.com/@a/video/1", "https://youtu.be/x"]) == "mixed"

    # Every install line is pinned: ai-editor's bootstrap / repair when it is there, else this
    # skill's hash-checked lock, which holds ai-editor's exact pins for everything it lists.
    for setup in (None, Path("/x/ai-editor/skills/setup/scripts/setup.py")):
        for o in ("Darwin", "Windows", "Linux"):
            for k, line in install_fixes(setup, o).items():
                assert "brew" not in line and "pipx" not in line and "winget" not in line, line
                assert "pip install" not in line or "--require-hashes" in line, line
                assert (("bootstrap" if k == "venv" else "repair") in line) == bool(setup), (k, line)
    pins = {m[1]: m[2] for m in re.finditer(r"^([a-z0-9._-]+)==(\S+)", LOCK.read_text(encoding="utf-8"), re.M)}
    assert {"yt-dlp", "faster-whisper", "numpy", "pillow", "opencv-python-headless"} <= set(pins), pins
    assert LOCK.read_text(encoding="utf-8").count("--hash=sha256:") > len(pins)

    # Saving one key keeps the other.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / ".env"
        save_key("ELEVENLABS_API_KEY", "a" * 24, f)
        save_key("GEMINI_API_KEY", "b" * 24, f)
        save_key("GEMINI_API_KEY", "c" * 24, f)
        assert f.read_text(encoding="utf-8") == f"ELEVENLABS_API_KEY={'a' * 24}\nGEMINI_API_KEY={'c' * 24}\n"
        # a new key file is 0600 from its first byte: with chmod doing nothing, the mode is still 0600
        if os.name != "nt":
            real, mask = os.chmod, os.umask(0)
            os.chmod = lambda *a, **k: None
            try:
                save_key("GEMINI_API_KEY", "d" * 24, Path(d) / "new.env")
            finally:
                os.chmod, _ = real, os.umask(mask)
            assert (Path(d) / "new.env").stat().st_mode & 0o777 == 0o600, oct((Path(d) / "new.env").stat().st_mode)
        # the working folder's .env is another project's: never read
        (Path(d) / ".env").write_text(f"GEMINI_API_KEY={'e' * 24}\n", encoding="utf-8")
        cwd, saved = os.getcwd(), os.environ.pop("GEMINI_API_KEY", None)
        os.chdir(d)
        try:
            assert find_key("GEMINI_API_KEY")[1] != str(Path.cwd() / ".env"), "read the working folder's .env"
        finally:
            os.chdir(cwd)
            if saved is not None:
                os.environ["GEMINI_API_KEY"] = saved
    print("ok")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("setkey")
    p.add_argument("--gemini", action="store_true", help="save a Gemini key instead")
    p.set_defaults(fn=cmd_setkey)

    p = sub.add_parser("list")
    p.add_argument("handle", help="used to name the output folder")
    p.add_argument("--platform", default="tiktok", choices=sorted(PROFILE_URL))
    p.add_argument("--limit", type=int, default=40)
    p.add_argument("--urls", default=None,
                   help="file of hand-picked video URLs, one per line "
                        "(ignores --platform/--limit)")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("transcribe")
    p.add_argument("handle")
    p.add_argument("--top", type=int, default=8, help="most-viewed videos to take; --control more are added (default 2)")
    p.add_argument("--control", type=int, default=2,
                   help="videos closest to the median, as a control group")
    p.add_argument("--spare", type=int, default=2,
                   help="next most-viewed videos, in case the format check leaves some out")
    p.add_argument("--ids", default=None,
                   help="comma-separated video ids (replaces --top/--control)")
    p.add_argument("--engine", choices=["whisper", "scribe"], default=None,
                   help="default: scribe if an ElevenLabs key exists, else whisper")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_transcribe)

    sub.add_parser("demo").set_defaults(fn=lambda a: demo())

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
