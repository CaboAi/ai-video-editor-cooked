#!/usr/bin/env python3
"""Claude's questions in the editor's window. Stdlib only.

A PreToolUse hook on AskUserQuestion (hooks/hooks.json) runs this with the hook JSON on stdin. When the
home page or a review page is open and showing right now, the question appears there and the user answers
in the page: the hook returns the answers as the tool's input (permissionDecision "allow" + updatedInput
with "answers", the documented way to answer AskUserQuestion from a hook), so the terminal box never shows.

    python3 askhook.py           the hook (stdin: hook JSON). Prints nothing and exits 0 when no page is
                                 showing, on "Answer in Claude Code instead", on timeout and on any error:
                                 Claude Code then shows its own question box
    python3 askhook.py demo      self-check against a throwaway server

How a page is found: home.py and review.py each write $AI_EDITOR_HOME/pages/<port> (0600) when they start,
JSON {"url": the page's URL with its token, "kind": "home" | "review", "session": the Claude Code session
that started it}, and touch it each time an open page polls GET /ask (ask.js: every second, less often in a
background tab, which the browser slows to about once a minute). A file touched in the last 90 s is an open page,
showing or not: a question for a video in a background tab waits there, the tab's title says "(1) Claude is asking"
and the page shows it the moment the tab is looked at. A closed tab says so (POST /ask/gone). No port is ever
scanned. An old file holding only the URL belongs to no session, so it never gets a question.

Whose question: the hook sends its session_id with the question, and each server takes it only when that
session is working there (srv.ask_owner, set by register): a review page, the session that last ran a
command on that edit or holds that edit's video job on the home page (a session holding a job on the home page asks
only on that job's edit page, never another's); the home page, a session holding one of its jobs or listening for
one. Every open page that takes it gets it, and the first answer wins. When the video's editor took it, the home page
gets it marked "elsewhere": Home and its sidebar job card show a row "Claude is asking about <video>" with Open, not
the card, so a question about a video shows in that video's editor tab. A server that
knows no session at all (a Claude Code without CLAUDE_CODE_SESSION_ID) takes any question, as before. The
card says what it is about ("Claude (team-250k-general) asks"). No server takes it: the terminal box shows.

The servers (route() below, called from both):
    GET  /ask.js                 the card's script
    GET  /ask[?v=1]              the page's poll: the oldest open question or null (v=1: the page is showing)
                                 {id, questions, about, elsewhere}: elsewhere, the editor page has it too
    GET  /ask?id=X               the hook's poll: {"result": null | {"answers": [...]} | {"terminal": true}}
    POST /ask {id, questions, elsewhere?}   the hook asks (elsewhere: the video's editor page has it too)
    POST /ask/answer {id, answers | terminal, images?}   the page answers (one string per question; images: one
                                 list of image data urls per question, saved where the server keeps them, see below)
    POST /ask/clear {id}         the hook is done: every page drops it
    POST /ask/gone               the tab closed (pagehide beacon): not showing any more, so no question waits on it

Images on an answer (the paperclip, a paste or a drop on the card): png, jpeg, gif or webp, under 15 MB each, at most 6
per answer, the review notes' limits. Saved on this computer only: a review page keeps them in <edit>/review/ beside its
notes' pictures, the home page in the asking job's <edit>/asks/ (or $AI_EDITOR_HOME/asks with no edit folder yet). The
answer Claude gets ends with a line "Attached images: /abs/a.png (Read each one to see it)".
"""
import base64
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

JS = Path(__file__).with_name("ask.js")
LIVE_S = 90          # a page polled this recently is open (a background tab polls about once a minute)
TIMEOUT_S = 600      # then the terminal box shows (hooks.json gives the hook 900 s)
KEEP_S = TIMEOUT_S + 60   # a question whose hook was stopped (Esc in Claude Code) leaves the pages by then
MAX_IMAGE = 15 << 20      # an attached image (review notes and answers)
MAX_IMAGES = 6
MAX_BODY = MAX_IMAGES * MAX_IMAGE * 4 // 3 + (1 << 20)   # a POST carrying that many, base64


def save_images(folder, imgs, prefix, keep=()):
    """Attached pictures: names already kept (keep) and pasted, dropped or picked data urls (saved into folder).
    Only png, jpeg, gif and webp, under MAX_IMAGE each, at most MAX_IMAGES. The saved names, in order."""
    out = []
    for im in imgs:
        if len(out) == MAX_IMAGES or not isinstance(im, str):
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
        folder.mkdir(parents=True, exist_ok=True)
        k = 1
        while any((folder / f"{prefix}-img{k}.{x}").exists() for x in ("jpg", "png", "gif", "webp")):
            k += 1
        name = f"{prefix}-img{k}.{'jpg' if m[1] == 'jpeg' else m[1]}"
        (folder / name).write_bytes(raw)
        out.append(name)
    return out


def with_images(text, paths):
    """An answer and the images attached to it, as the text Claude reads."""
    if not paths:
        return text
    line = "Attached images: " + ", ".join(str(p) for p in paths) + " (Read each one to see it)"
    return text + "\n" + line if text else line


def pages():
    return Path(os.environ.get("AI_EDITOR_HOME", "").strip() or Path.home() / ".ai-video-editor") / "pages"


def session():
    """The Claude Code session this process runs in (Bash commands Claude runs carry it), or ""."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()


def register(srv, url, kind=None, owner=None, where=None):
    """A server's page file, touched while a page is showing. owner(session_id) -> what the question is
    about ("" for nothing to name), or None when that session's questions do not belong on this page.
    where(session_id) -> the folder an answer's images are saved in (default $AI_EDITOR_HOME/asks)."""
    srv.ask_url, srv.asks = url, {}
    srv.ask_kind = kind or getattr(srv, "ask_kind", None)
    srv.ask_owner = owner or getattr(srv, "ask_owner", None)
    srv.ask_where = where or getattr(srv, "ask_where", None)
    p = pages() / str(srv.server_address[1])
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with os.fdopen(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            json.dump({"url": url, "kind": srv.ask_kind, "session": session()}, f)
        os.utime(p, (0, 0))   # not showing until a page polls
    except OSError:
        pass


def same_origin(h):
    """A POST from the page itself: no Origin header (curl, the hook) or this server's own address."""
    o = h.headers.get("Origin")
    return not o or o == "http://" + (h.headers.get("Host") or "")


def _seen(srv):
    p = pages() / str(srv.server_address[1])
    try:
        os.utime(p)
    except FileNotFoundError:
        register(srv, srv.ask_url)
        os.utime(p)
    except OSError:
        pass


def route(h, method, path, query, body=None):
    """Serves /ask* for a home or review handler h (already authorized). True when it answered."""
    if not (path == "/ask.js" or path == "/ask" or path.startswith("/ask/")):
        return False
    srv = h.server
    asks = getattr(srv, "asks", None)
    if asks is None:
        srv.asks = asks = {}
    for k in [k for k, a in asks.items() if time.time() - a["at"] > KEEP_S]:
        asks.pop(k, None)
    if method == "GET" and path == "/ask.js":
        h.send(200, JS.read_bytes(), "text/javascript; charset=utf-8")
    elif method == "GET" and path == "/ask":
        if query.get("id"):
            a = asks.get(query["id"])
            h.send(200 if a else 404, {"result": a and a.get("result")})
        else:
            if getattr(srv, "ask_url", None):   # open, showing or in a background tab
                _seen(srv)
            a = next((dict(id=k, questions=a["questions"], about=a.get("about") or "", **({"elsewhere": True} if a.get("elsewhere") else {}))
                      for k, a in sorted(asks.items(), key=lambda x: x[1]["at"])
                      if not a.get("result")), None)
            h.send(200, {"ask": a})
    elif method == "POST" and path == "/ask":
        qs = body.get("questions")
        if not (isinstance(body.get("id"), str) and isinstance(qs, list) and 0 < len(qs) <= 10):
            return h.send(400, {}) or True
        owner, sid = getattr(srv, "ask_owner", None), body.get("session")
        about = owner(sid if isinstance(sid, str) else "") if owner else None
        if about is None:   # another session's question: not for this page
            return h.send(409, {}) or True
        where = getattr(srv, "ask_where", None)
        folder = where(sid if isinstance(sid, str) else "") if where else None
        asks[body["id"]] = {"questions": qs, "at": time.time(), "about": about, "elsewhere": body.get("elsewhere") is True,
                            "dir": str(folder or pages().parent / "asks")}
        h.send(200, {})
    elif method == "POST" and path == "/ask/answer":
        a = asks.get(body.get("id"))
        ans = body.get("answers")
        if not a or a.get("result"):
            return h.send(409, {}) or True
        imgs = body.get("images") or [[] for _ in a["questions"]]
        if body.get("terminal") is True:
            a["result"] = {"terminal": True}
        elif isinstance(ans, list) and len(ans) == len(a["questions"]) and isinstance(imgs, list) \
                and len(imgs) == len(ans) and all(isinstance(x, str) and len(x) <= 4000 and isinstance(m, list)
                                                  and (x.strip() or m) for x, m in zip(ans, imgs)):
            folder, tag = Path(a["dir"]), re.sub(r"[^\w-]", "", body["id"])[:16]
            out = [with_images(x.strip(), [folder / n for n in save_images(folder, m, f"ask-{tag}-q{k + 1}")])
                   for k, (x, m) in enumerate(zip(ans, imgs))]
            if not all(out):   # images only, and none of them an image
                return h.send(400, {}) or True
            a["result"] = {"answers": out}
        else:
            return h.send(400, {}) or True
        h.send(200, {})
    elif method == "POST" and path == "/ask/gone":
        try:
            os.utime(pages() / str(srv.server_address[1]), (0, 0))
        except OSError:
            pass
        h.send(200, {})
    elif method == "POST" and path == "/ask/clear":
        asks.pop(body.get("id"), None)
        h.send(200, {})
    else:
        h.send(404, {})
    return True


# ---------------------------------------------------------------- the hook
def live(kind=None):
    """The URLs of pages showing now (of that kind). Old plain-URL files are left out: no session owns them."""
    out = []
    try:
        for f in pages().iterdir():
            if time.time() - f.stat().st_mtime < LIVE_S:
                try:
                    d = json.loads(f.read_text(encoding="utf-8"))
                except ValueError:
                    continue
                url = d.get("url") if isinstance(d, dict) else None
                if isinstance(url, str) and url.startswith(("http://127.0.0.1:", "http://localhost:")) and "/?t=" in url \
                        and (kind is None or d.get("kind") == kind):
                    out.append(url)
    except OSError:
        pass
    return out


def _call(url, path, body=None, timeout=3):
    base, tok = url.split("/?t=", 1)
    sep = "&" if "?" in path else "?"
    req = urllib.request.Request(base + path + sep + "t=" + tok, json.dumps(body).encode() if body is not None else None,
                                 method="POST" if body is not None else "GET")
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read() or b"{}")


def relay(payload, timeout=None, poll=0.5):
    """The hook's stdout (a JSON string), or None for Claude Code's own question box."""
    if payload.get("tool_name") != "AskUserQuestion":
        return None
    ti = payload.get("tool_input") or {}
    qs = ti.get("questions")
    if not isinstance(qs, list) or not qs:
        return None
    sid = payload.get("session_id")
    sid = sid if isinstance(sid, str) else ""
    qid = secrets.token_urlsafe(12)
    sent = []
    edit = live("review")
    for u in edit + [u for u in live() if u not in edit]:   # the video's editor first; Home marked when an editor has it
        try:
            _call(u, "/ask", {"id": qid, "questions": qs, "session": sid, "elsewhere": bool(sent) and u not in edit})
            sent.append(u)
        except Exception:   # 409: another session's page
            pass
    if not sent:
        return None
    result = None
    end = time.time() + (timeout or float(os.environ.get("AI_EDITOR_ASK_TIMEOUT") or TIMEOUT_S))
    try:
        while result is None and time.time() < end:
            for u in list(sent):
                try:
                    result = _call(u, "/ask?id=" + qid).get("result")
                except Exception:   # that page's server stopped: the others may still answer
                    sent.remove(u)
                if result:
                    break
            if not sent:
                break
            if result is None:
                time.sleep(poll)
    finally:
        for u in sent:
            try:
                _call(u, "/ask/clear", {"id": qid})
            except Exception:
                pass
    if not result or not result.get("answers"):
        return None
    answers = {q.get("question", f"Question {i + 1}"): a for i, (q, a) in enumerate(zip(qs, result["answers"]))}
    return json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "permissionDecisionReason": "The user answered in the editor window.",
        "updatedInput": dict(ti, answers=answers)}})


def main():
    try:
        out = relay(json.loads(sys.stdin.read() or "{}"))
        if out:
            print(out)
    except Exception:
        pass   # never break Claude Code: its own box shows
    sys.exit(0)


# ---------------------------------------------------------------- demo
def demo():
    import shutil
    import subprocess
    import tempfile
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    tmp = Path(tempfile.mkdtemp(prefix="askhook_demo_"))
    os.environ["AI_EDITOR_HOME"] = str(tmp)
    tok = "demo-token"

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, code, body, ctype="application/json"):
            b = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def q(self):
            from urllib.parse import parse_qs, urlsplit
            s = urlsplit(self.path)
            return s.path, {k: v[0] for k, v in parse_qs(s.query).items()}

        def do_GET(self):
            path, q = self.q()
            assert q.get("t") == tok
            route(self, "GET", path, q) or self.send(404, {})

        def do_POST(self):
            path, q = self.q()
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            route(self, "POST", path, q, body) or self.send(404, {})

    def mk(sid, about, kind="review"):   # a page owned by one session
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/?t={tok}"
        register(srv, url, kind, owner=lambda s: about if s == sid else None)
        return srv, url
    srv, url = mk("sess-A", "team-250k-general")
    qs = [{"question": "Which cut?", "header": "Cut", "multiSelect": False,
           "options": [{"label": "Tight (Recommended)", "description": "Every pause out"}, {"label": "Loose", "description": ""}]},
          {"question": "Which parts?", "header": "Parts", "multiSelect": True,
           "options": [{"label": "Captions", "description": ""}, {"label": "Zooms", "description": ""}]}]
    payload = {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion", "tool_input": {"questions": qs},
               "session_id": "sess-A"}
    me = [sys.executable, str(Path(__file__).resolve())]

    # no page showing (registered, never polled): exits 0 at once, prints nothing
    t0 = time.time()
    r = subprocess.run(me, input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0 and r.stdout == "" and time.time() - t0 < 5, r
    # another tool, or garbage on stdin: silent
    for s in (json.dumps(dict(payload, tool_name="Bash")), "not json"):
        r = subprocess.run(me, input=s, capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert r.returncode == 0 and r.stdout == "", r

    def page(url=url):   # a showing page: polls with v=1 until a question appears
        for _ in range(200):
            a = _call(url, "/ask?v=1")["ask"]
            if a:
                return a
            time.sleep(0.05)
        raise AssertionError("the question never reached the page")

    def run_hook(payload=payload):
        box = {}
        th = threading.Thread(target=lambda: box.update(r=subprocess.run(
            me, input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", timeout=60)))
        th.start()
        return th, box

    _call(url, "/ask?v=1")
    assert [u for u in live()] == [url]
    # the answer path: one option, and the user's own words for a multi-select
    th, box = run_hook()
    a = page()
    assert a["questions"] == qs and a["about"] == "team-250k-general", a
    for bad in ({"id": a["id"], "answers": ["only one"]}, {"id": a["id"], "answers": ["x", ""]}, {"id": "nope", "answers": ["a", "b"]}):
        try:
            _call(url, "/ask/answer", bad)
            raise AssertionError(f"accepted {bad}")
        except urllib.error.HTTPError as e:
            assert e.code in (400, 409), e.code
    _call(url, "/ask/answer", {"id": a["id"], "answers": ["Tight (Recommended)", "Captions, Zooms"]})
    th.join()
    r = box["r"]
    assert r.returncode == 0, r
    o = json.loads(r.stdout)["hookSpecificOutput"]
    assert o["hookEventName"] == "PreToolUse" and o["permissionDecision"] == "allow", o
    assert o["updatedInput"] == {"questions": qs, "answers": {"Which cut?": "Tight (Recommended)", "Which parts?": "Captions, Zooms"}}, o
    assert _call(url, "/ask?v=1")["ask"] is None and not srv.asks, "answered: it leaves every page"
    # images on an answer: saved where the server keeps them, their absolute paths in the answer Claude gets
    png = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJ"
           "RU5ErkJggg==")
    srv.ask_where = lambda s: tmp / "edit" / "review"
    th, box = run_hook()
    a = page()
    for bad in ({"id": a["id"], "answers": ["", "x"], "images": [["data:text/html;base64,PGI+"], []]},
                {"id": a["id"], "answers": ["x", "y"], "images": [[png]]}):
        try:
            _call(url, "/ask/answer", bad)
            raise AssertionError(f"accepted {bad}")
        except urllib.error.HTTPError as e:
            assert e.code == 400, e.code
    _call(url, "/ask/answer", {"id": a["id"], "answers": ["Loose", ""], "images": [[png, "data:text/html;base64,PGI+"], [png]]})
    th.join()
    got = json.loads(box["r"].stdout)["hookSpecificOutput"]["updatedInput"]["answers"]
    saved = sorted((tmp / "edit" / "review").iterdir())
    assert [f.name for f in saved] == [f"ask-{a['id'][:16]}-q1-img1.png", f"ask-{a['id'][:16]}-q2-img1.png"], saved
    assert got == {"Which cut?": f"Loose\nAttached images: {saved[0]} (Read each one to see it)",
                   "Which parts?": f"Attached images: {saved[1]} (Read each one to see it)"}, got
    assert saved[0].read_bytes()[:4] == b"\x89PNG" and saved[0].is_absolute()
    srv.ask_where = None
    # "Answer in Claude Code instead": silent, cleared
    th, box = run_hook()
    _call(url, "/ask/answer", {"id": page()["id"], "terminal": True})
    th.join()
    assert box["r"].returncode == 0 and box["r"].stdout == "" and not srv.asks, box["r"]
    # timeout: silent, cleared
    os.environ["AI_EDITOR_ASK_TIMEOUT"] = "1"
    th, box = run_hook()
    page()
    th.join()
    assert box["r"].returncode == 0 and box["r"].stdout == "" and not srv.asks, box["r"]
    os.environ.pop("AI_EDITOR_ASK_TIMEOUT")
    # two sessions, two pages: each question lands only on its own session's page
    srv2, url2 = mk("sess-B", "")
    _call(url2, "/ask?v=1")
    assert sorted(live()) == sorted([url, url2]) and live("home") == []
    th, box = run_hook(dict(payload, session_id="sess-B"))
    b = page(url2)
    assert b["about"] == "" and _call(url, "/ask?v=1")["ask"] is None, "B's question stays off A's page"
    _call(url2, "/ask/answer", {"id": b["id"], "answers": ["Loose", "Zooms"]})
    th.join()
    assert json.loads(box["r"].stdout)["hookSpecificOutput"]["updatedInput"]["answers"]["Which cut?"] == "Loose"
    th, box = run_hook()
    assert page()["about"] == "team-250k-general" and _call(url2, "/ask?v=1")["ask"] is None, "A's stays off B's"
    _call(url, "/ask/answer", {"id": page()["id"], "terminal": True})
    th.join()
    # the home page open too, also A's: A's question shows as a card in A's editor, and on Home marked "elsewhere"
    # (Home shows a row with Open); B's (no editor of its own) on Home as the card
    srvH, urlH = mk("sess-A", "", "home")
    srvH.ask_owner = lambda s: "" if s in ("sess-A", "sess-D") else None
    _call(urlH, "/ask?v=1")
    th, box = run_hook()
    a = page()
    assert a["about"] == "team-250k-general" and "elsewhere" not in a and page(urlH)["elsewhere"] is True, "the editor, Home marked"
    _call(urlH, "/ask/answer", {"id": a["id"], "answers": ["Loose", "Zooms"]})   # answered on Home: the editor drops it too
    th.join()
    assert json.loads(box["r"].stdout)["hookSpecificOutput"]["updatedInput"]["answers"]["Which cut?"] == "Loose"
    assert not srv.asks and not srvH.asks
    # a background tab (polls without v=1, about once a minute): still open, so the question waits there, not the terminal
    os.utime(pages() / str(srvH.server_address[1]), (0, 0))
    os.utime(pages() / str(srv.server_address[1]), (time.time() - 60,) * 2)
    th, box = run_hook()
    for _ in range(100):
        if srv.asks:
            break
        time.sleep(0.05)
    assert srv.asks and not srvH.asks, "a tab polled 60 s ago still gets it"
    _call(url, "/ask")   # the hidden tab's next poll delivers it
    _call(url, "/ask/answer", {"id": next(iter(srv.asks)), "terminal": True})
    th.join()
    _call(urlH, "/ask?v=1")
    th, box = run_hook(dict(payload, session_id="sess-D"))
    d = page(urlH)
    assert not srv.asks and not srv2.asks, "no editor takes D's: Home does"
    _call(urlH, "/ask/answer", {"id": d["id"], "terminal": True})
    th.join()
    srvH.shutdown()
    srvH.server_close()
    (pages() / str(srvH.server_address[1])).unlink()
    # a third session with no page of its own, or no session id: the terminal box, at once
    for p in (dict(payload, session_id="sess-C"), {k: v for k, v in payload.items() if k != "session_id"}):
        t0 = time.time()
        r = subprocess.run(me, input=json.dumps(p), capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert r.returncode == 0 and r.stdout == "" and time.time() - t0 < 5 and not srv.asks and not srv2.asks, r
    # a server that knows no session (Claude Code without the env var): any session's question, as before
    srv3, url3 = mk("", "")
    srv3.ask_owner = lambda s: ""
    _call(url3, "/ask?v=1")
    th, box = run_hook(dict(payload, session_id="sess-C"))
    c = page(url3)
    _call(url3, "/ask/answer", {"id": c["id"], "terminal": True})
    th.join()
    # the tab closed (pagehide beacon): that page is not showing any more
    _call(url3, "/ask/gone", {})
    assert url3 not in live()
    srv3.shutdown()
    srv3.server_close()
    (pages() / str(srv3.server_address[1])).unlink()
    # an old page file with only a URL: no session owns it
    old = pages() / "1"
    old.write_text(url, encoding="utf-8")
    assert old.stat().st_mtime > time.time() - LIVE_S and url in live() and live().count(url) == 1
    old.unlink()
    srv2.shutdown()
    srv2.server_close()
    (pages() / str(srv2.server_address[1])).unlink()
    # the page went away (not polled for LIVE_S): silent again
    os.utime(pages() / str(srv.server_address[1]), (time.time() - LIVE_S - 1,) * 2)
    assert live() == [] and relay(payload) is None
    srv.shutdown()
    srv.server_close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("askhook demo ok")


if __name__ == "__main__":
    demo() if sys.argv[1:2] == ["demo"] else main()
