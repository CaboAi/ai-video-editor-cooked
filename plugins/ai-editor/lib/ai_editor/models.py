"""Every model the editor downloads, pinned by URL and sha256, and the one fetch that checks it.

Files land in AI_EDITOR_HOME/models (default ~/.ai-video-editor/models). A download that does not
match its sha256 is deleted, never used. Stdlib only.

    python models.py     self-check (no network)
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

try:
    from ai_editor import net
except ImportError:   # run as a script from this folder
    import net

HOME = Path(os.environ.get("AI_EDITOR_HOME", Path.home() / ".ai-video-editor"))
DIR = HOME / "models"

# faster-whisper models: the Hugging Face repo at a fixed commit, so a model update upstream never
# changes a transcript here. Shared files (tokenizer, vocabulary) are identical across sizes.
_TOK = "fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab"
_VOC = "34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913"
WHISPER = {  # size -> (commit, {file: sha256})
    "tiny": ("d90ca5fe260221311c53c58e660288d3deb8d356", {
        "config.json": "a73a28cdfe1c43ccc7202fa333d1f89c202477271407ae9a7f19afa52039cac8",
        "model.bin": "dcb76c6586fc06cbdac6dd21f14cfd129cc4cdd9dce19bf4ffa62e59cbe6e6d1",
        "tokenizer.json": _TOK, "vocabulary.txt": _VOC}),
    "small": ("536b0662742c02347bc0e980a01041f333bce120", {
        "config.json": "b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828",
        "model.bin": "3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671",
        "tokenizer.json": _TOK, "vocabulary.txt": _VOC}),
}

PINS = {  # path under models/ -> (url, sha256)
    # Robust Video Matting (GPL-3.0, downloaded at first use, never shipped): the speaker cutout.
    "rvm_mobilenetv3_fp32.onnx": (
        "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx",
        "88d4531297118f595bf2fd60f6f566aec2e559393802d1f436c380f0cbbd2828"),
    # OpenCV YuNet face detector (MIT), at a fixed opencv_zoo commit.
    "yunet.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/f12e12798e8314f7c074a6656816c048dcc95b7a/"
        "models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
}
for _size, (_rev, _files) in WHISPER.items():
    for _f, _sha in _files.items():
        PINS[f"faster-whisper-{_size}/{_f}"] = (
            f"https://huggingface.co/Systran/faster-whisper-{_size}/resolve/{_rev}/{_f}", _sha)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def name(rel):
    """What a pinned file is, in the words the page uses."""
    if rel.startswith("faster-whisper-"):
        return f"the {rel.split('/')[0].removeprefix('faster-whisper-')} transcription model"
    return {"rvm_mobilenetv3_fp32.onnx": "the cutout model", "yunet.onnx": "the face finder"}.get(rel, rel)


def _before(edit):
    """(the home page's newest step on edit's video job, the review page's open activity): what announce re-posts
    when the download is over, so it does not stay the current step."""
    step = act = None
    try:
        try:
            from ai_editor import home, progress
        except ImportError:
            import home
            import progress
        d = json.loads(progress.state_file().read_text(encoding="utf-8"))
        j = home.mine(d, "video", edit)
        step = j["steps"][-1]["text"] if j and j["steps"] and not j["steps"][-1].get("done") else None
    except Exception:   # noqa: BLE001  no page, no job: nothing to re-post
        pass
    try:
        a = json.loads((Path(edit) / "review.json").read_text(encoding="utf-8")).get("activity") or []
        act = a[-1] if a and not a[-1].get("done") else None
    except (OSError, ValueError, TypeError):
        pass
    return step, act


def announce(what, edit=None, run=None, say=None, bar=True):
    """A download that still happens during an edit (setup fetches everything it can), said where the user is
    looking: a step on the home page's video job for edit ("Downloading the cutout model, first time only",
    detail "12 of 15 MB", through progress.py), and Claude's activity on the edit's review page when it has one,
    its bar read from a log of "12/15" lines (bar=False: no bar, for a download with no progress). Returns a
    urlretrieve reporthook; call its .done() when the download is over: the step that was current before comes
    back on both pages. Best effort: never fails the edit."""
    here = Path(__file__).resolve().parent
    step = f"Downloading {what}, first time only"
    if say is None:
        try:
            from ai_editor import progress
        except ImportError:   # run as a script from this folder
            import progress
        say = lambda text=step, **k: progress.step(text, job="video", key=edit, **k)   # noqa: E731

    def call(script, *args):
        try:
            (run or subprocess.run)([sys.executable, str(here / script), *map(str, args)], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass
    page = bool(edit) and (Path(edit) / "review.json").exists()
    prev_step, prev_act = _before(edit) if edit else (None, None)
    log = Path(edit) / ".download.log" if page and bar else None
    say()
    if page:
        if log:
            log.write_text("", encoding="utf-8")
        call("review.py", edit, "status", step, *(["--log", log] if log else []))
    seen = [0.0]

    def hook(blocks, size, total):
        got, t = min(blocks * size, total), time.time()
        if total < 1e6 or (t - seen[0] < 1 and got < total):   # a small file needs no bar
            return
        seen[0] = t
        say(detail=f"{got // 10**6} of {total // 10**6} MB", progress=got / total)
        if log:
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{got // 10**6}/{total // 10**6} MB\n")

    def done():
        if prev_step:
            say(prev_step)
        if page:
            call("review.py", edit, "status", *([prev_act["text"]] + (["--log", prev_act["log"]] if prev_act.get("log") else [])
                                                if prev_act else ["--done"]))
    hook.done = done
    return hook


def fetch(rel, check=True, hook=None, edit=None):
    """The local path of a pinned file, downloaded and verified if missing (or wrong, when check).
    hook(blocks, block_size, total) is urlretrieve's reporthook: setup.py install-all's progress bar. Without
    one, a download at first use is announced on the home page and the review page of edit (announce)."""
    url, want = PINS[rel]
    dest = DIR / rel
    if dest.exists() and (not check or sha256(dest) == want):
        return dest
    said = None if hook else announce(name(rel), edit)
    hook = hook or said
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f"{dest.name}.{os.getpid()}.part")   # setup and a first edit can fetch the same file at once
    print(f"downloading {rel}", flush=True)
    net.retrieve(url, part, reporthook=hook)
    got = sha256(part)
    if got != want:
        part.unlink()
        sys.exit(f"ERROR: {rel} downloaded with sha256 {got}, expected {want}. Nothing kept. "
                 "Try again; if it repeats, the file changed upstream: report it as a bug.")
    part.replace(dest)
    if said:
        said.done()
    return dest


def whisper_dir(size):
    """The pinned faster-whisper folder if all its files are on disk, else None. No hashing."""
    if size not in WHISPER:
        return None
    d = DIR / f"faster-whisper-{size}"
    return d if all((d / f).exists() for f in WHISPER[size][1]) else None


def whisper(size, check=True, hook=None, edit=None):
    """Download (or verify) a pinned faster-whisper model; returns its folder, or None if unpinned.
    Reuses a copy at the same commit from faster-whisper's own cache (installs before the pins).
    Missing at first use (a size chosen after setup): announced once for the whole model."""
    if size not in WHISPER:
        return None
    rev, files = WHISPER[size]
    old = DIR / f"models--Systran--faster-whisper-{size}" / "snapshots" / rev
    for f, want in files.items():
        dest = DIR / f"faster-whisper-{size}" / f
        if not dest.exists() and (old / f).exists() and sha256(old / f) == want:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old / f, dest)
    said = None if hook or whisper_dir(size) else announce(name(f"faster-whisper-{size}/"), edit)   # still missing
    for f in files:
        fetch(f"faster-whisper-{size}/{f}", check, hook or said or (lambda *a: None))
    if said:
        said.done()
    return DIR / f"faster-whisper-{size}"


def demo():
    import tempfile
    global DIR
    for rel, (url, sha) in PINS.items():
        assert url.startswith("https://") and len(sha) == 64, rel
        assert "/main/" not in url and "/resolve/main" not in url, f"{rel} is not pinned to a commit"
    keep, keep_env = DIR, os.environ.get("AI_EDITOR_HOME")
    with tempfile.TemporaryDirectory() as d:
        DIR = Path(d)
        os.environ["AI_EDITOR_HOME"] = str(Path(d) / "h")   # never the real home: announce reads its state.json
        assert whisper_dir("small") is None and whisper_dir("medium") is None
        for f in WHISPER["tiny"][1]:
            (DIR / "faster-whisper-tiny").mkdir(exist_ok=True)
            (DIR / "faster-whisper-tiny" / f).write_text("x", encoding="utf-8")
        assert whisper_dir("tiny") == DIR / "faster-whisper-tiny"
        assert sha256(DIR / "faster-whisper-tiny" / "config.json") == hashlib.sha256(b"x").hexdigest()
        # a download says which file in plain words, never the pinned URL with its commit hash
        import contextlib
        import io
        PINS["t.bin"] = ("https://example.com/abc123def/t.bin", hashlib.sha256(b"x").hexdigest())
        real, out = net.retrieve, io.StringIO()
        try:
            net.retrieve = lambda u, dest, reporthook=None: Path(dest).write_bytes(b"x")
            with contextlib.redirect_stdout(out):
                fetch("t.bin")
        finally:
            net.retrieve = real
            PINS.pop("t.bin")
        assert out.getvalue() == "downloading t.bin\n", out.getvalue()
        # a first-use download: a step on the video job and the review page, "N of M MB" at most once a second;
        # done() brings back the step that was current (here the review page's render, with its log)
        edit = Path(d) / "e"
        edit.mkdir()
        (edit / "review.json").write_text(json.dumps({"activity": [{"text": "Rendering", "log": "r.log", "done": False}]}),
                                          encoding="utf-8")
        calls, said = [], []
        hook = announce(name("rvm_mobilenetv3_fp32.onnx"), edit, run=lambda cmd, **k: calls.append(cmd[2:]),
                        say=lambda *a, **k: said.append((a, k)))
        hook(0, 8192, 15_000_000)
        hook(1, 8192, 15_000_000)                  # under a second later: not said again
        hook(2000, 8192, 15_000_000)               # the last block: always said
        hook.done()
        step = "Downloading the cutout model, first time only"
        assert said == [((), {}), ((), {"detail": "0 of 15 MB", "progress": 0.0}),
                        ((), {"detail": "15 of 15 MB", "progress": 1.0})], said   # no home step before: none re-posted
        assert calls == [[str(edit), "status", step, "--log", str(edit / ".download.log")],
                         [str(edit), "status", "Rendering", "--log", "r.log"]], calls
        assert (edit / ".download.log").read_text(encoding="utf-8").splitlines() == ["0/15 MB", "15/15 MB"]
        calls.clear()   # no progress (npm, faster-whisper's own download): no bar, and nothing open before: --done
        (edit / "review.json").write_text("{}", encoding="utf-8")
        announce("the renderer", edit, run=lambda cmd, **k: calls.append(cmd[2:]), say=lambda *a, **k: None, bar=False).done()
        assert calls == [[str(edit), "status", "Downloading the renderer, first time only"], [str(edit), "status", "--done"]], calls
        silent = []
        announce("x", None, run=lambda cmd, **k: silent.append(cmd), say=lambda *a, **k: None)(1, 1, 5e6)   # no review page
        assert silent == [], silent
        assert name("faster-whisper-small/model.bin") == "the small transcription model" and name("yunet.onnx") == "the face finder"
    DIR = keep
    os.environ.pop("AI_EDITOR_HOME") if keep_env is None else os.environ.__setitem__("AI_EDITOR_HOME", keep_env)
    print("demo ok")


if __name__ == "__main__":
    demo()
