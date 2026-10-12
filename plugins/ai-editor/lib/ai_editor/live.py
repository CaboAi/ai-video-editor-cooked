#!/usr/bin/env python3
"""Live reload for the local pages (home.py, review.py): an open tab always shows the newest UI.

The page HTML is read per request, but the Python is not. reexec_on_change() watches every .py in this
folder and, when one changes and still compiles, re-executes the server on the same port. page_version()
goes into each /state answer; the page reloads itself when it changes (never while the user is typing or
an upload is running). Uploads in flight hold off the restart (busy()).

    python3 live.py demo     self-check
Stdlib only.
"""
import contextlib
import hashlib
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_busy = [0]
_lock = threading.Lock()


def page_version(*pages):
    """Changes when a page file or any Python file here changes."""
    files = [Path(p) for p in pages] + sorted(HERE.glob("*.py"))
    stamp = "|".join(f"{f.name}:{f.stat().st_mtime_ns}" for f in files if f.exists())
    return hashlib.sha1(stamp.encode()).hexdigest()[:12]


@contextlib.contextmanager
def busy():
    """Around an upload: no restart under it."""
    with _lock:
        _busy[0] += 1
    try:
        yield
    finally:
        with _lock:
            _busy[0] -= 1


def argv_with_port(argv, port):
    a = list(argv)
    a[0] = os.path.abspath(a[0])
    if "--port" in a and a.index("--port") + 1 < len(a):
        a[a.index("--port") + 1] = str(port)
    else:
        a += ["--port", str(port)]
    return a


def reexec_on_change(srv, poll=1.0):
    """Run in a daemon thread beside serve_forever()."""
    snap = lambda: {p: p.stat().st_mtime_ns for p in HERE.glob("*.py")}
    s0 = snap()
    while True:
        time.sleep(poll)
        s = snap()
        if s == s0 or _busy[0]:
            continue
        changed, s0 = [p for p in s if s0.get(p) != s[p]], s
        try:
            for p in changed:
                compile(p.read_text(encoding="utf-8"), str(p), "exec")
        except SyntaxError as e:
            print(f"{Path(e.filename or '').name} changed but does not compile, still on the old code: {e}", flush=True)
            continue
        print(f"{', '.join(p.name for p in changed)} changed: restarting on the same port", flush=True)
        port = srv.server_address[1]
        srv.server_close()
        os.execv(sys.executable, [sys.executable] + argv_with_port(sys.argv, port))


def demo():
    with tempfile.TemporaryDirectory() as d:
        page = Path(d) / "home.html"
        page.write_text("<p>v1</p>", encoding="utf-8")
        v1 = page_version(page)
        assert page_version(page) == v1, "stable while nothing changes"
        st = page.stat()
        os.utime(page, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))
        assert page_version(page) != v1, "an edit to the page changes the version"
    assert argv_with_port(["home.py", "serve"], 47821)[-2:] == ["--port", "47821"]
    assert argv_with_port(["review.py", "x", "serve", "--port", "0", "--no-open"], 51234)[3:5] == ["--port", "51234"]
    with busy():
        assert _busy[0] == 1
    assert _busy[0] == 0
    print("live demo ok")


if __name__ == "__main__":
    demo() if sys.argv[1:] == ["demo"] else print(__doc__)
