#!/usr/bin/env python3
"""Stop and the skills a video used. Stdlib only.

A PreToolUse hook on every tool (hooks/hooks.json) runs this with the hook JSON on stdin.

Stop: the window's Stop (home.py POST /stop) writes $AI_EDITOR_HOME/stop/<session>. While that file is under STOP_S
old, every tool call of that Claude Code session is denied with a reason that tells Claude to stop, so a Claude deep in
a long run stops at its next step. After STOP_S the file goes and the session works again (the user can type in it).

Skills: each Skill call is added to the video it is about (askhook.asking_edit) as a line of
<edit>/review/skills.jsonl, {"skill", "at"}; Your videos and the video's editor show them.

Claude needs you in the Terminal: a Notification hook (a permission prompt, a question no page took, Claude idle and
waiting) from a Claude the editor knows (one holding a job, listening, or on a video) writes $AI_EDITOR_HOME/terminal.json,
{"session", "message", "at"}; every open page shows it and chimes once (ask.js). That session's next tool call or the
user typing in it (UserPromptSubmit) clears it.

    python3 toolhook.py          the hook (stdin: hook JSON); prints nothing unless it denies; always exits 0
    python3 toolhook.py notify   the Notification hook          python3 toolhook.py clear   the UserPromptSubmit hook
    python3 toolhook.py demo     self-check
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import askhook  # noqa: E402

STOP_S = 120
REASON = ("The user pressed Stop in the cooked. window. Stop now: run nothing else. Say in one line what was left "
          "unfinished, then wait for the user to type in this Terminal.")


def stop_file(sid):
    return askhook.pages().parent / "stop" / sid


def stopped(sid, now=None):
    """True while the window's Stop holds this session; an old Stop file is removed."""
    if not sid:
        return False
    f = stop_file(sid)
    try:
        if (now or time.time()) - f.stat().st_mtime < STOP_S:
            return True
        f.unlink()
    except OSError:
        pass
    return False


def terminal_file():
    return askhook.pages().parent / "terminal.json"


def ours(payload, sid):
    """A Claude the editor knows: it holds or held a job, listens for jobs, or works on a video."""
    if any(j.get("owner") == sid for j in askhook.home_jobs()):
        return True
    try:
        if (askhook.pages().parent / "home" / ".listening").read_text(encoding="utf-8").strip() == sid:
            return True
    except OSError:
        pass
    return bool(askhook.asking_edit(payload, sid))


def notify(payload):
    sid = payload.get("session_id") or ""
    if not sid or not ours(payload, sid):
        return None
    f = terminal_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"session": sid, "message": str(payload.get("message") or "")[:300], "at": round(time.time(), 2)}),
                 encoding="utf-8")
    return f


def clear(sid):
    """The Terminal notice goes once that session moves on."""
    f = terminal_file()
    try:
        if sid and json.loads(f.read_text(encoding="utf-8")).get("session") == sid:
            f.unlink()
    except (OSError, ValueError, AttributeError):
        pass


def record(payload):
    """A Skill call: the skill on its video's skills.jsonl. Nothing when no video is known."""
    name = str((payload.get("tool_input") or {}).get("skill") or "").strip().lstrip("/")
    if not name:
        return None
    edit = askhook.asking_edit(payload, payload.get("session_id") or "")
    if not edit:
        return None
    d = Path(edit) / "review"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / "skills.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"skill": name, "at": round(time.time(), 2)}) + "\n")
    return d / "skills.jsonl"


def used(edit):
    """The skills a video ran, in first-run order, with the last one run: [{"skill", "at"}]."""
    out = {}
    try:
        for ln in (Path(edit) / "review" / "skills.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
                out.setdefault(r["skill"], r)["last"] = r["at"]
            except (ValueError, KeyError, TypeError):
                pass
    except OSError:
        pass
    return list(out.values())


def main(mode=None):
    try:
        payload = json.load(sys.stdin)
        if mode == "notify":
            return notify(payload) and 0
        clear(payload.get("session_id") or "")
        if mode == "clear":
            return 0
        if stopped(payload.get("session_id") or ""):
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                     "permissionDecisionReason": REASON}}))
            return 0
        if payload.get("tool_name") == "Skill":
            record(payload)
    except Exception:
        pass
    return 0


def demo():
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="toolhook_demo_"))
    os.environ["AI_EDITOR_HOME"] = str(tmp)
    assert not stopped("s1")
    stop_file("s1").parent.mkdir(parents=True)
    stop_file("s1").write_text("x", encoding="utf-8")
    assert stopped("s1") and not stopped("s2"), "only that session stops"
    assert not stopped("s1", now=time.time() + STOP_S + 1) and not stop_file("s1").exists(), "Stop lifts after STOP_S"
    edit = tmp / "edits" / "take"
    (tmp / "home").mkdir()
    (tmp / "home" / "state.json").write_text(json.dumps({"jobs": [{"type": "video", "state": "taken", "owner": "s3",
                                                                    "args": {"edit": str(edit)}}]}), encoding="utf-8")
    for sk in ("ai-editor:cut", "my-card-edit", "ai-editor:cut"):
        assert record({"tool_name": "Skill", "tool_input": {"skill": sk}, "session_id": "s3"})
    assert [u["skill"] for u in used(edit)] == ["ai-editor:cut", "my-card-edit"], used(edit)
    assert record({"tool_name": "Skill", "tool_input": {"skill": "x"}, "session_id": "other"}) is None, "no video: nothing"
    # Claude waits in the Terminal: a notice for a known session only, gone once it moves on
    assert notify({"session_id": "stranger", "message": "Claude needs your permission"}) is None, "not the editor's Claude"
    assert notify({"session_id": "s3", "message": "Claude needs your permission to use Bash"})
    assert json.loads(terminal_file().read_text(encoding="utf-8"))["message"].startswith("Claude needs")
    clear("other")
    assert terminal_file().exists()
    clear("s3")
    assert not terminal_file().exists()
    print("toolhook demo ok")


if __name__ == "__main__":
    sys.exit(demo() if sys.argv[1:] == ["demo"] else main((sys.argv[1:] or [None])[0]))
