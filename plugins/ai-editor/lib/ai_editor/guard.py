#!/usr/bin/env python3
"""Keep a working Claude out of the plugin's own files. Stdlib only.

A PreToolUse hook on Edit, Write, MultiEdit and NotebookEdit (hooks/hooks.json) runs this with the hook JSON on
stdin. When the plugin runs from the installed copy (under ~/.claude/plugins/) and the file is inside it, the
call is denied with MESSAGE: an update replaces those files, and until then the change alters every later video.
Jobs from the window are blocked too, checkout or not: the launcher's settings layer sets AI_EDITOR_WINDOW=1 and
an Edit deny rule on the plugin folder (launcher.settings). Otherwise a checkout or fork the user owns is left
alone (the improve skill, their own development).

    python3 guard.py          the hook (stdin: hook JSON). Prints nothing and exits 0 unless it denies
    python3 guard.py demo     self-check
"""
import json
import os
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[2]          # plugins/ai-editor
MESSAGE = ("This file is part of the cooked. plugin, so it is not edited during a video: an update would replace "
           "the change, and until then it would change every later video. Fix it through the video's own files "
           "(style.json, plan flags, notes, custom/ overrides), or tell the user what is wrong with the script.")


def guarded(plugin=PLUGIN):
    """The installed copy, or a job from the window."""
    return "/.claude/plugins/" in Path(plugin).as_posix() + "/" or os.environ.get("AI_EDITOR_WINDOW") == "1"


def blocked(path, plugin=PLUGIN):
    """True when path is inside the plugin and the plugin is guarded."""
    if not path or not guarded(plugin):
        return False
    root = Path(plugin).resolve()
    p = Path(path).expanduser().resolve()
    return p == root or root in p.parents


def decide(event, plugin=PLUGIN):
    """The hook's output for one event, or None to let it through."""
    ti = event.get("tool_input") or {}
    if not blocked(ti.get("file_path") or ti.get("notebook_path"), plugin):
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": MESSAGE}}


def demo():
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="guard_demo_"))
    cache = tmp / ".claude" / "plugins" / "cache" / "nextwork" / "ai-editor" / "2.3.0"
    checkout = tmp / "fork" / "plugins" / "ai-editor"
    for root in (cache, checkout):
        (root / "skills" / "style-edit" / "scripts").mkdir(parents=True)
    plan = "skills/style-edit/scripts/plan.py"
    out = decide({"tool_name": "Edit", "tool_input": {"file_path": str(cache / plan)}}, cache)
    assert out and out["hookSpecificOutput"]["permissionDecision"] == "deny", out
    assert decide({"tool_input": {"notebook_path": str(cache / "x.ipynb")}}, cache), "NotebookEdit too"
    assert decide({"tool_input": {"file_path": str(cache / "skills/../PRINCIPLES.md")}}, cache), "no .. escape"
    os.environ.pop("AI_EDITOR_WINDOW", None)
    assert decide({"tool_input": {"file_path": str(checkout / plan)}}, checkout) is None, "a checkout is the user's"
    os.environ["AI_EDITOR_WINDOW"] = "1"
    assert decide({"tool_input": {"file_path": str(checkout / plan)}}, checkout), "a window job, even on a checkout"
    os.environ.pop("AI_EDITOR_WINDOW")
    assert decide({"tool_input": {"file_path": str(tmp / "edits/v/plan.json")}}, cache) is None, "the video's files"
    assert decide({"tool_input": {}}, cache) is None
    print("guard demo ok")


def main():
    try:
        out = decide(json.load(sys.stdin))
    except Exception:   # a broken hook must never stop the edit
        return 0
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(demo() if sys.argv[1:] == ["demo"] else main())
