#!/usr/bin/env python3
"""nextwork-editor: open the AI editor's window, with Claude Code behind it. Stdlib only.

    nextwork-editor                 open the window (the same address every time)
    nextwork-editor --dry-run [--os Darwin|Linux|Windows]   print what it would run, and where
    python3 launcher.py demo        self-check

`setup.py install-launcher` puts the `nextwork-editor` command on the PATH; `uninstall-launcher` takes
it off. Inside Claude Code the same thing is `/ai-editor:start`.

When the home page is already served (a Claude Code session has it open), its address opens again in
the browser and nothing else starts. Otherwise Claude Code starts in the library folder (library.py)
with one first prompt, PROMPT, and opens the page there, so Claude is listening behind it. The user's
permission settings apply as always: nothing is bypassed.
"""
import os
import platform
import shlex
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import home  # noqa: E402
import library  # noqa: E402

PLUGIN = Path(__file__).resolve().parents[2]          # plugins/ai-editor
PROMPT = "/ai-editor:start open the home page"


def plan(os_name=None, plugin=PLUGIN, claude=None):
    """The Claude Code command. A plugin installed through /plugin lives under ~/.claude/plugins and loads
    by itself; a checkout of this repo loads for this session with --plugin-dir (its plugins/ folder, so
    creator-teardown comes too)."""
    os_name = os_name or platform.system()
    claude = claude or shutil.which("claude") or ("claude.exe" if os_name == "Windows" else "claude")
    cmd = [claude]
    if "/.claude/plugins/" not in Path(plugin).as_posix() + "/":
        cmd += ["--plugin-dir", str(Path(plugin).parent)]
    return cmd + [PROMPT]


def show(cmd, os_name):
    return subprocess.list2cmdline(cmd) if os_name == "Windows" else shlex.join(cmd)


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)
    dry = "--dry-run" in a
    os_name = a[a.index("--os") + 1] if "--os" in a else platform.system()
    lib = library.path(make=not dry)
    url = home.running()
    if url:
        print(f"Opening your editor...\n  Already open at {url.split('?')[0]}")
        if not dry:
            webbrowser.open(url)
        return 0
    cmd = plan(os_name)
    if dry:
        print(f"cd {show([str(lib)], os_name)}\n{show(cmd, os_name)}")
        return 0
    if not shutil.which(cmd[0]) and not Path(cmd[0]).is_file():
        print("Claude Code is not installed yet. Install it from https://claude.com/claude-code, then run this again.")
        return 1
    print(f"Opening your editor...\n  Your videos: {lib}\n  Claude Code is starting. The window opens in a moment.", flush=True)
    os.chdir(lib)
    if os_name == "Windows":
        return subprocess.call(cmd)
    os.execvp(cmd[0], cmd)   # hand the terminal to Claude Code


def demo():
    import contextlib
    import io
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="launcher_demo_"))
    os.environ["AI_EDITOR_HOME"] = str(tmp / "home")
    os.environ["AI_EDITOR_LIBRARY"] = str(tmp / "My Videos")
    for os_name in ("Darwin", "Linux", "Windows"):
        cmd = plan(os_name, plugin=Path("/repo/plugins/ai-editor"), claude="claude")
        assert cmd == ["claude", "--plugin-dir", str(Path("/repo/plugins")), PROMPT], cmd
        assert not any("dangerously" in c or "bypass" in c.lower() for c in cmd), "never bypass permissions"
        cached = plan(os_name, plugin=Path.home() / ".claude/plugins/cache/nextwork/ai-editor/2.3.0", claude="claude")
        assert "--plugin-dir" not in cached, cached
        o = io.StringIO()
        with contextlib.redirect_stdout(o):
            assert main(["--dry-run", "--os", os_name]) == 0
        lines = o.getvalue().splitlines()
        q = '"' if os_name == "Windows" else "'"
        assert lines[0] == f"cd {q}{library.path(make=False)}{q}" and lines[1].endswith(f"{q}{PROMPT}{q}"), lines
        print(f"{os_name:<8} {lines[0]}  &&  {lines[1]}")
    assert not (tmp / "My Videos").exists(), "a dry run makes nothing"
    # a page already served: its address opens again, Claude Code is not started a second time
    opened, real = [], (home.running, webbrowser.open)
    home.running, webbrowser.open = (lambda: "http://127.0.0.1:47821/?t=x"), opened.append
    try:
        o = io.StringIO()
        with contextlib.redirect_stdout(o):
            assert main([]) == 0
        assert opened == ["http://127.0.0.1:47821/?t=x"] and "Already open at http://127.0.0.1:47821/" in o.getvalue()
        assert "t=x" not in o.getvalue(), "the token is not printed"
    finally:
        home.running, webbrowser.open = real
    shutil.rmtree(tmp, ignore_errors=True)
    print("launcher demo ok")


if __name__ == "__main__":
    if sys.argv[1:2] == ["demo"]:
        demo()
    else:
        sys.exit(main())
