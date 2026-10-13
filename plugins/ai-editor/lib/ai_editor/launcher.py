#!/usr/bin/env python3
"""nextwork-editor: open the Cooked Editor window, with Claude Code behind it. Stdlib only.

    nextwork-editor                 open the window (the same address every time)
    nextwork-editor --dry-run [--os Darwin|Linux|Windows]   print what it would run, and where
    open_claude(folder, prompt)     the home page's "Open a new Claude for this video": a new terminal window
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


def plan(os_name=None, plugin=PLUGIN, claude=None, prompt=PROMPT):
    """The Claude Code command. A plugin installed through /plugin lives under ~/.claude/plugins and loads
    by itself; a checkout of this repo loads for this session with --plugin-dir (its plugins/ folder, so
    creator-teardown comes too)."""
    os_name = os_name or platform.system()
    claude = claude or shutil.which("claude") or ("claude.exe" if os_name == "Windows" else "claude")
    cmd = [claude]
    if "/.claude/plugins/" not in Path(plugin).as_posix() + "/":
        cmd += ["--plugin-dir", str(Path(plugin).parent)]
    return cmd + [prompt]


def show(cmd, os_name):
    return subprocess.list2cmdline(cmd) if os_name == "Windows" else shlex.join(cmd)


def terminal(folder, cmd, os_name):
    """The command that opens a new terminal window running cmd in folder, or None when this computer has no
    terminal to open (Linux without x-terminal-emulator): the page then shows the command to copy."""
    if os_name == "Darwin":
        line = f"cd {shlex.quote(str(folder))} && {shlex.join(cmd)}"
        esc = line.replace("\\", "\\\\").replace('"', '\\"')   # inside an AppleScript string
        return ["osascript", "-e", f'tell application "Terminal" to do script "{esc}"',
                "-e", 'tell application "Terminal" to activate']
    if os_name == "Windows":
        # a console of its own (CREATE_NEW_CONSOLE) started in folder, so the folder is never quoted for cmd. One
        # string, not a list: a list would escape the inner quotes as \" (cmd reads them literally); cmd /k
        # strips the outer pair and runs what is inside as typed.
        return f'cmd /k "{subprocess.list2cmdline(cmd)}"'
    xte = shutil.which("x-terminal-emulator")
    if not xte:
        return None
    return [xte, "-e", "sh", "-c", f'cd {shlex.quote(str(folder))} && {shlex.join(cmd)}; exec "${{SHELL:-sh}}"']


def start(argv, folder, os_name):
    """Run terminal()'s command. True only when the window opened (as far as the OS says)."""
    try:
        if os_name == "Darwin":   # osascript returns once Terminal has the window, non-zero when it is refused
            return subprocess.run(argv, capture_output=True, timeout=60).returncode == 0
        if os_name == "Windows":
            subprocess.Popen(argv, cwd=str(folder), creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10))
            return True
        p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:   # a terminal that cannot start exits at once
            return p.wait(timeout=2) == 0
        except subprocess.TimeoutExpired:
            return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def open_claude(folder, prompt, os_name=None, run=start):
    """(opened, the command as the user would type it, for the page to show when nothing opened).
    run(argv, folder, os_name) -> bool; the demos pass a recorder so no real window opens."""
    os_name = os_name or platform.system()
    cmd = plan(os_name, prompt=prompt)
    text = (f"cd /d {show([str(folder)], os_name)} && " if os_name == "Windows" else f"cd {show([str(folder)], os_name)} && ") \
        + show(cmd, os_name)
    argv = terminal(folder, cmd, os_name)
    return (bool(argv) and run(argv, folder, os_name)), text


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
    print(f"Opening your editor...\n  Your videos: {lib}\n  Claude Code is starting. The window opens in a moment.\n"
          "  If Claude Code asks \"Do you trust the files in this folder?\", pick \"Yes, proceed\": it is your videos folder.",
          flush=True)
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
    # "Open a new Claude for this video": the window it would open, per OS (recorded, never run)
    for os_name in ("Darwin", "Windows", "Linux"):
        ran = []
        opened, text = open_claude(Path("/lib/My Videos"), "/ai-editor:start take job video-2", os_name,
                                   run=lambda w, f, o: ran.append((w, f)) or True)
        assert "take job video-2" in text and "My Videos" in text, text
        if ran:
            assert opened and len(ran) == 1
            w, f = ran[0]
            flat = w if isinstance(w, str) else " ".join(w)
            assert "take job video-2" in flat and (os_name == "Windows" or "My Videos" in flat), flat
            assert os_name != "Darwin" or w[0] == "osascript", w
            assert os_name != "Windows" or (w.startswith('cmd /k "') and "My Videos" not in w and text.startswith("cd /d ")), (w, text)
        else:
            assert os_name == "Linux" and not opened and not shutil.which("x-terminal-emulator"), "no terminal: copy it"
        print(f"{os_name:<8} new Claude: {text}")
        assert open_claude(Path("/lib/My Videos"), "x", os_name, run=lambda w, f, o: False)[0] is False, "nothing opened"
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
