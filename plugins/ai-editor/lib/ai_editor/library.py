#!/usr/bin/env python3
"""The library: one folder for every video, wherever Claude Code was started. Stdlib only.

    python3 library.py path                 print the library folder (made if missing)
    python3 library.py set <folder>         move it: new videos land there, older ones stay listed
    python3 library.py record <edit dir>    list an edit folder made somewhere else (review.py start does this)
    python3 library.py list                 every video, newest first, as JSON
    python3 library.py clean [--yes]        the working files that can go and their size; --yes deletes them
    python3 library.py demo                 self-check

The folder: ~/Movies/AI Editor on macOS, ~/Videos/AI Editor on Windows and Linux. AI_EDITOR_LIBRARY
overrides it, then `set` (saved in $AI_EDITOR_HOME/library.json with every recorded edit folder).
Inside: videos/ (dropped files), edits/<name>/, clips/<name>/, product/<name>/, creator-teardowns/<handle>/.

`clean` deletes only what a step rebuilds: render chunks, the sound-effect wav cache, unfinished
uploads, and the review page's copies of each round older than the last two. Never a source, cut.mp4,
the render, joined.mp4 or review.json.
"""
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from links import VIDEO_EXT  # noqa: E402

KINDS = ("edits", "clips", "product")
PROTECT = {"review.json", "cut.mp4", "render.mp4", "joined.mp4", "words.json", "takes.txt"}
RECENT_N = 24


def home():
    return Path(os.environ.get("AI_EDITOR_HOME", "").strip() or Path.home() / ".ai-video-editor")


def _index():
    try:
        return json.loads((home() / "library.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(d):
    p = home() / "library.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def default_path(os_name=None):
    return Path.home() / ("Movies" if (os_name or platform.system()) == "Darwin" else "Videos") / "AI Editor"


def path(make=True):
    p = Path(os.path.expanduser(os.environ.get("AI_EDITOR_LIBRARY", "").strip() or _index().get("path") or default_path()))
    if make:
        p.mkdir(parents=True, exist_ok=True)
    return p.resolve() if p.exists() else p


def set_path(p):
    p = Path(os.path.expanduser(str(p).strip())).resolve()
    p.mkdir(parents=True, exist_ok=True)
    _save({**_index(), "path": str(p)})
    return p


def record(d):
    """Remember an edit folder (any place on disk), so the library lists it from wherever it is opened."""
    d = str(Path(d).resolve())
    ix = _index()
    ix["edits"] = [x for x in ix.get("edits", []) if x != d][-499:] + [d]
    _save(ix)


def edit_dirs():
    lib = path()
    found = [p for k in KINDS for p in sorted((lib / k).glob("*")) if p.is_dir()]
    found += [Path(x) for x in _index().get("edits", []) if Path(x).is_dir()]
    return list({str(p.resolve()): p.resolve() for p in found}.values())


def _review(d):
    try:
        return json.loads((d / "review.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def status(r):
    """cutting (no review page yet), making (Claude has the notes), ready (to review), done (approved)."""
    if not r:
        return "cutting"
    last = (r.get("events") or [{}])[-1]
    if r.get("sent") and last.get("type") == "approve":
        return "making" if r.get("stage") == "cut" else "done"
    return "making" if r.get("sent") else "ready"


def entry(d):
    r = _review(d)
    video = (r or {}).get("video") or next((str(d / n) for n in ("render.mp4", "cut.mp4", "joined.mp4") if (d / n).exists()), None)
    if not video and (d / "takes.txt").exists():
        video = next(iter((d / "takes.txt").read_text(encoding="utf-8").split("\n")), None)
    t = max([q.stat().st_mtime for q in (d / "review.json", d) if q.exists()])
    return {"name": (r or {}).get("label") or (r or {}).get("name") or d.name, "dir": str(d), "kind": d.parent.name,
            "status": status(r), "stage": (r or {}).get("stage"), "round": (r or {}).get("round"),
            "video": video if video and Path(video).is_file() else None, "review": bool(r), "updated": t}


def entries():
    return sorted((entry(d) for d in edit_dirs()), key=lambda e: e["updated"], reverse=True)


def styles(lib=None):
    """Every studied creator in the library with a style.json: [(handle, style dict)]."""
    out = []
    for f in sorted((Path(lib or path()) / "creator-teardowns").glob("*/style.json")):
        try:
            out.append((f.parent.name, json.loads(f.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            pass
    return out


# ---------------------------------------------------------------- recent videos and thumbnails
def home_dirs():
    """The usual folders, scanned only once the user turns it on (so macOS asks for folder access only then)."""
    env = os.environ.get("AI_EDITOR_SCAN_DIRS")
    if env is not None:
        return [Path(x) for x in env.split(os.pathsep) if x]
    h = Path.home()
    return [h / "Movies", h / "Desktop", h / "Downloads"]


def recent(picked=(), scan_home=False, n=RECENT_N):
    """The newest videos: the library's videos/ (dropped and chosen files), files picked before, and with
    scan_home the usual folders (one level down at most). Newest first."""
    seen, out = set(), []

    def add(p, where):
        try:
            if p.suffix.lower() in VIDEO_EXT and p.is_file() and not p.name.startswith(".") and p.resolve() not in seen:
                seen.add(p.resolve())
                out.append((p.stat().st_mtime, p, where))
        except OSError:
            pass
    for base in [path() / "videos"] + (home_dirs() if scan_home else []):
        for p in list(base.glob("*")) + list(base.glob("*/*")):
            add(p, "Your videos" if base == path() / "videos" else base.name)
    for x in picked:
        add(Path(x), Path(x).parent.name)
    out.sort(key=lambda x: x[0], reverse=True)
    return [{"path": str(p.resolve()), "name": p.name, "where": where, "mtime": t} for t, p, where in out[:n]]


def _key(p):
    s = p.stat()
    return hashlib.sha1(f"{p.resolve()}|{s.st_mtime_ns}|{s.st_size}".encode()).hexdigest()[:20]


def meta(p):
    """{duration, w, h} from ffprobe, cached by path + mtime + size."""
    p = Path(p)
    c = home() / "thumbs" / (_key(p) + ".json")
    try:
        return json.loads(c.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height:stream_tags=rotate:stream_side_data=rotation:format=duration", "-of", "json", str(p)],
                           capture_output=True, text=True, timeout=30)
        j = json.loads(r.stdout or "{}")
        s = (j.get("streams") or [{}])[0]
        w, h = s.get("width"), s.get("height")
        rot = str((s.get("tags") or {}).get("rotate") or next((x.get("rotation") for x in s.get("side_data_list") or [] if "rotation" in x), 0))
        if rot.lstrip("-") in ("90", "270"):
            w, h = h, w
        m = {"duration": round(float((j.get("format") or {}).get("duration") or 0), 1), "w": w, "h": h}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {"duration": 0, "w": None, "h": None}
    c.parent.mkdir(parents=True, exist_ok=True)
    c.write_text(json.dumps(m), encoding="utf-8")
    return m


def thumb(p):
    """A JPEG of the frame at 1 s (or the middle of a shorter clip), 540 px wide at most, cached. None if ffmpeg fails."""
    p = Path(p)
    out = home() / "thumbs" / (_key(p) + ".jpg")
    if out.exists():
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    t = min(1.0, (meta(p).get("duration") or 0) / 2)
    tmp = out.with_name(out.stem + ".tmp.jpg")
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.2f}", "-i", str(p), "-frames:v", "1",
                        "-vf", "scale='min(540,iw)':-2", "-q:v", "4", str(tmp)], capture_output=True, timeout=60)
    if r.returncode or not tmp.exists():
        tmp.unlink(missing_ok=True)
        return None
    os.replace(tmp, out)
    return out


# ---------------------------------------------------------------- disk use and working files
def size(p):
    p = Path(p)
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file() and not f.is_symlink())


def working_files(dirs=None):
    """Files a step rebuilds. Each edit folder keeps its review copies of the last two rounds and the current one."""
    out = []
    for d in dirs if dirs is not None else edit_dirs():
        d = Path(d)
        for f in d.rglob("*"):
            if not f.is_file() or f.is_symlink() or f.name in PROTECT:
                continue
            rel = f.relative_to(d).parts
            if "chunks" in rel[:-1] or f.name.endswith(".part") or (rel[0] == ".sfx" and f.suffix == ".wav"):
                out.append(f)
        cur = str(Path((_review(d) or {}).get("video") or "-").resolve())
        copies = sorted((f for f in (d / "review").glob("v-*") if f.suffix.lower() in VIDEO_EXT), key=lambda f: f.stat().st_mtime)
        out += [f for f in copies[:-2] if str(f.resolve()) != cur]
    return out


def clean(dirs=None, yes=False):
    """(files, bytes). Deletes them only with yes=True."""
    fs = working_files(dirs)
    n = sum(f.stat().st_size for f in fs)
    if yes:
        for f in fs:
            assert f.name not in PROTECT, f
            f.unlink(missing_ok=True)
        for d in dirs if dirs is not None else edit_dirs():
            for c in sorted(Path(d).rglob("chunks"), reverse=True):
                if c.is_dir() and not any(c.iterdir()):
                    c.rmdir()
    return fs, n


def disk():
    lib = path()
    return {"library": size(lib), "working": sum(f.stat().st_size for f in working_files()),
            "free": shutil.disk_usage(lib).free}


def reveal_cmd(p, os_name=None):
    os_name = os_name or platform.system()
    if os_name == "Darwin":
        return ["open", "-R", str(p)] if Path(p).is_file() else ["open", str(p)]
    if os_name == "Windows":
        return ["explorer", f"/select,{p}"] if Path(p).is_file() else ["explorer", str(p)]
    return ["xdg-open", str(p if Path(p).is_dir() else Path(p).parent)]


# ---------------------------------------------------------------- demo
def demo():
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="library_demo_"))
    os.environ["AI_EDITOR_HOME"] = str(tmp / "home")
    os.environ.pop("AI_EDITOR_LIBRARY", None)
    assert default_path("Darwin") == Path.home() / "Movies" / "AI Editor"
    assert default_path("Windows") == default_path("Linux") == Path.home() / "Videos" / "AI Editor"
    os.environ["AI_EDITOR_LIBRARY"] = str(tmp / "lib")
    lib = path()
    assert lib == (tmp / "lib").resolve() and lib.is_dir()
    os.environ.pop("AI_EDITOR_LIBRARY")
    set_path(tmp / "lib2")
    assert path() == (tmp / "lib2").resolve(), "set is remembered"
    set_path(lib)
    # an edit made elsewhere (a project folder) is listed once recorded; one in the library is found by itself
    away = tmp / "project" / "edits" / "away"
    away.mkdir(parents=True)
    record(away)
    record(away)
    assert _index()["edits"] == [str(away.resolve())]
    mine = lib / "edits" / "mine"
    (mine / "review").mkdir(parents=True)
    vid = tmp / "take.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=360x640:rate=30:duration=2",
                    "-pix_fmt", "yuv420p", str(vid)], check=True)
    for n in ("cut.mp4", "render.mp4", "joined.mp4"):
        shutil.copyfile(vid, mine / n)
    copies = []
    for i in range(1, 5):
        c = mine / "review" / f"v-cut{i}.mp4"
        shutil.copyfile(vid, c)
        os.utime(c, (time.time() - 100 + i, time.time() - 100 + i))
        copies.append(c)
    (mine / "review.json").write_text(json.dumps({"name": "mine", "stage": "cut", "round": 4, "video": str(copies[1]),
                                                  "sent": False, "events": []}), encoding="utf-8")
    (mine / "chunks").mkdir()
    (mine / "chunks" / "chunk-0.mkv").write_bytes(b"x" * 1000)
    (mine / ".sfx").mkdir()
    (mine / ".sfx" / "pop.wav").write_bytes(b"x" * 10)
    (mine / ".sfx" / "kit.json").write_text("{}", encoding="utf-8")
    (mine / "words.json").write_text("{}", encoding="utf-8")
    es = {e["name"]: e for e in entries()}
    assert es["mine"]["status"] == "ready" and es["away"]["status"] == "cutting", es
    assert es["mine"]["video"] == str(copies[1]) and es["away"]["video"] is None
    # status follows review.json
    for r, want in (({"sent": True, "events": [{"type": "send"}]}, "making"),
                    ({"sent": True, "stage": "cut", "events": [{"type": "approve"}]}, "making"),
                    ({"sent": True, "stage": "edit", "events": [{"type": "approve"}]}, "done")):
        assert status(r) == want, (r, want)
    # clean: chunks, the wav cache, round copies older than the last two (but the current one), never protected files
    fs = {f.relative_to(mine).as_posix() for f in working_files([mine])}
    assert fs == {"chunks/chunk-0.mkv", ".sfx/pop.wav", "review/v-cut1.mp4"}, fs
    _, n = clean([mine], yes=True)
    assert n > 1000 and not (mine / "chunks").exists() and (mine / ".sfx" / "kit.json").exists()
    for keep in ("cut.mp4", "render.mp4", "joined.mp4", "review.json", "words.json", "review/v-cut2.mp4",
                 "review/v-cut3.mp4", "review/v-cut4.mp4"):
        assert (mine / keep).exists(), keep
    assert vid.exists() and not working_files([mine])
    # recent videos: newest first; the usual folders only once turned on; files picked before always
    os.environ["AI_EDITOR_SCAN_DIRS"] = str(tmp / "Desktop")
    (lib / "videos").mkdir()
    (tmp / "Desktop" / "sub").mkdir(parents=True)
    shutil.copyfile(vid, lib / "videos" / "old.mp4")
    os.utime(lib / "videos" / "old.mp4", (1, 1))
    shutil.copyfile(vid, tmp / "Desktop" / "sub" / "new.MOV")
    (tmp / "Desktop" / "notes.txt").write_text("x", encoding="utf-8")
    assert [r["name"] for r in recent()] == ["old.mp4"]
    assert [r["name"] for r in recent(picked=[str(vid)])] == ["take.mp4", "old.mp4"]
    rs = recent(scan_home=True)
    assert [r["name"] for r in rs] == ["new.MOV", "old.mp4"] and rs[0]["where"] == "Desktop", rs
    m = meta(rs[0]["path"])
    assert m["duration"] == 2.0 and (m["w"], m["h"]) == (360, 640), m
    t = thumb(rs[0]["path"])
    assert t and t.stat().st_size > 500 and thumb(rs[0]["path"]) == t
    assert reveal_cmd(mine, "Darwin") == ["open", str(mine)] and reveal_cmd(vid, "Windows")[0] == "explorer"
    assert disk()["library"] > 0
    shutil.rmtree(tmp, ignore_errors=True)
    print("library demo ok")


def main():
    a = sys.argv[1:]
    if a[:1] == ["demo"]:
        return demo()
    if a[:1] == ["path"]:
        return print(path())
    if a[:1] == ["set"] and len(a) == 2:
        return print(set_path(a[1]))
    if a[:1] == ["record"] and len(a) == 2:
        return record(a[1])
    if a[:1] == ["list"]:
        return print(json.dumps(entries(), indent=1))
    if a[:1] == ["clean"]:
        fs, n = clean(yes="--yes" in a)
        return print(f"{'deleted' if '--yes' in a else 'would delete'} {len(fs)} working files, {n / 1e6:.0f} MB")
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
