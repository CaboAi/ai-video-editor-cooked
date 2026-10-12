#!/usr/bin/env python3
"""Pull new versions of the editor into a fork, keeping the user's own changes. Stdlib only.

    python3 update.py where              which install this is, and how it updates
    python3 update.py add-upstream       add the `upstream` remote (ask the user first)
    python3 update.py check              fetch upstream: new commits, their CHANGELOG lines, files both sides changed
    python3 update.py merge              backup branch, then merge upstream/main (refuses a dirty tree)
    python3 update.py verify             the fast self-checks: tests/check_plugins.py and tests/demos.sh
    python3 update.py rollback           back to the backup branch the last merge made
    python3 update.py demo               self-check on throwaway git repos

`--repo DIR` picks the checkout (default: the git folder you are in). `py` instead of `python3` on Windows.
Never pushes, never force-pushes, never deletes a branch, never stashes or resets uncommitted work.
Exit codes: 0 ok, 1 checks failed, 2 refused (says why), 3 merge stopped on conflicts (lists the files).
"""
import datetime
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

UPSTREAM = "https://github.com/nextwork-projects/ai-video-editor.git"
LIB = Path(__file__).resolve().parents[3] / "lib"


def upstream_url():
    return os.environ.get("AI_EDITOR_UPSTREAM", "").strip() or UPSTREAM


def norm(url):
    return re.sub(r"(\.git)?/*$", "", url.strip().replace("\\", "/")).lower()


def is_upstream(url):
    u, up = norm(url), norm(upstream_url())
    return u == up or u.endswith("nextwork-projects/ai-video-editor")


def git(repo, *args, check=True):
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8")
    if check and p.returncode:
        sys.exit(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p


def out(repo, *args):
    return git(repo, *args).stdout.strip()


def find_repo(arg):
    for start in ([arg] if arg else [os.getcwd(), str(LIB.parent)]):
        p = git(start, "rev-parse", "--show-toplevel", check=False) if Path(start).is_dir() else None
        if p and p.returncode == 0:
            return Path(p.stdout.strip())
    return None


def where(repo):
    """('marketplace' | 'clone' | 'fork', upstream remote url or None)."""
    plugins = Path.home() / ".claude" / "plugins"
    if (repo is None or not (repo / "plugins/ai-editor/.claude-plugin/plugin.json").exists()
            or repo.resolve().as_posix().startswith(plugins.resolve().as_posix())):
        return "marketplace", None
    remotes = dict(l.split(None, 1) for l in out(repo, "remote", "-v").splitlines() if l.endswith("(fetch)"))
    remotes = {k: v.rsplit(" ", 1)[0] for k, v in remotes.items()}
    if is_upstream(remotes.get("origin", "")):
        return "clone", remotes["origin"]
    return "fork", remotes.get("upstream")


def say_where(repo):
    kind, up = where(repo)
    if kind == "marketplace":
        for m in sorted((Path.home() / ".claude/plugins/marketplaces").glob("*/plugins/ai-editor")):
            o = git(m.parents[1], "remote", "get-url", "origin", check=False).stdout.strip()
            if o and not is_upstream(o):
                print(f"Your plugin comes from {o}, a fork. Run this from your clone of it, or pass --repo <that folder>.")
                return "fork", None
        print("install: marketplace (the official plugin, no fork)\n"
              "update: type these in Claude Code:\n"
              "  /plugin marketplace update nextwork\n"
              "  /plugin install ai-editor@nextwork\n"
              "  /reload-plugins\n"
              "Your styles, skills and settings live in your own folder and survive the update (docs/customise.md).")
    elif kind == "clone":
        print(f"install: a plain clone of upstream ({repo.as_posix()})\n"
              "update: `git pull` here. To change the editor and keep your changes across updates, use a fork:\n"
              "  1. On GitHub, open nextwork-projects/ai-video-editor and press Fork.\n"
              "  2. In Claude Code: /plugin marketplace add <your-github-name>/ai-video-editor\n"
              "     then /plugin install ai-editor@nextwork\n"
              "  3. Clone your fork, change what you like, and say \"update the editor\" to pull new versions.")
    else:
        print(f"install: your fork ({repo.as_posix()})")
        print(f"upstream: {up}" if up else
              f"upstream: missing. Ask the user, then: update.py add-upstream  (adds {upstream_url()})")
    return kind, up


def need_fork(repo):
    kind, up = where(repo)
    if kind != "fork":
        say_where(repo)
        sys.exit(2)
    if not up:
        print(f"refused: no `upstream` remote. Ask the user, then run: update.py add-upstream")
        sys.exit(2)


def changed(repo, a, b):
    return set(out(repo, "diff", "--name-only", f"{a}..{b}").splitlines())


def check(repo):
    need_fork(repo)
    git(repo, "fetch", "upstream")
    n = int(out(repo, "rev-list", "--count", "HEAD..upstream/main"))
    if not n:
        print("up to date: no new commits upstream")
        return 0
    base = out(repo, "merge-base", "HEAD", "upstream/main")
    print(f"{n} new commit(s) upstream")
    log = git(repo, "diff", f"{base}..upstream/main", "--", "CHANGELOG.md").stdout
    news = [l[1:] for l in log.splitlines() if l.startswith("+") and not l.startswith("+++") and l[1:].strip()]
    if news:
        print("CHANGELOG, new lines:")
        print("\n".join("  " + l for l in news))
    mine, theirs = changed(repo, base, "HEAD"), changed(repo, base, "upstream/main")
    both = sorted(mine & theirs)
    print(f"files you changed: {len(mine)}; the update also changes {len(both)} of them"
          + "".join(f"\n  both: {f}" for f in both))
    return 0


def dirty(repo):
    return out(repo, "status", "--porcelain", "--untracked-files=no")


def merge(repo):
    need_fork(repo)
    if dirty(repo):
        print("refused: you have uncommitted changes:\n" + dirty(repo) + "\n"
              "Commit them first (git add -A && git commit -m \"my changes\"), or put them aside with git stash,\n"
              "then run merge again.")
        return 2
    git(repo, "fetch", "upstream")
    day = datetime.date.today().isoformat()
    names = set(out(repo, "branch", "--format=%(refname:short)").splitlines())
    backup, i = f"before-update-{day}", 2
    while backup in names:
        backup, i = f"before-update-{day}-{i}", i + 1
    git(repo, "branch", backup)
    (Path(out(repo, "rev-parse", "--absolute-git-dir")) / "ai-editor-backup").write_text(backup, encoding="utf-8")
    print(f"backup branch: {backup}")
    p = git(repo, "merge", "--no-edit", "upstream/main", check=False)
    conflicts = out(repo, "diff", "--name-only", "--diff-filter=U").splitlines()
    if conflicts:
        print("stopped: conflicts in\n" + "\n".join("  " + f for f in conflicts))
        print("Resolve each file, git add it, then git commit --no-edit. Or: update.py rollback")
        return 3
    if p.returncode:
        sys.exit(f"git merge failed: {p.stderr.strip()}")
    print("merged. Next: update.py verify")
    return 0


def verify(repo):
    if out(repo, "diff", "--name-only", "--diff-filter=U"):
        print("refused: the merge still has unresolved files")
        return 2
    py = os.environ.get("PY", "").strip()
    if not py:
        sys.path.insert(0, str(LIB))
        from ai_editor.run import venv_python
        py = str(venv_python()) if venv_python().exists() else sys.executable
    failed = [name for name, cmd in (("tests/check_plugins.py", [sys.executable, "tests/check_plugins.py"]),
                                     ("tests/demos.sh", ["bash", "tests/demos.sh"]))
              if subprocess.run(cmd, cwd=repo, env={**os.environ, "PY": py}).returncode]
    if failed:
        print("FAILED: " + ", ".join(failed) + "\nFix them, or go back with: update.py rollback")
        return 1
    print("checks ok. Type /reload-plugins in Claude Code.\n"
          "Installed from your fork on GitHub? When you want it there, push this branch to your fork,\n"
          "then /plugin marketplace update nextwork and /reload-plugins.")
    return 0


def rollback(repo):
    gitdir = Path(out(repo, "rev-parse", "--absolute-git-dir"))
    f = gitdir / "ai-editor-backup"
    if not f.exists():
        print("refused: no backup branch recorded; merge has not run here")
        return 2
    backup = f.read_text(encoding="utf-8").strip()
    if (gitdir / "MERGE_HEAD").exists():
        git(repo, "merge", "--abort")
    elif dirty(repo):
        print("refused: uncommitted changes would be lost. Commit or stash them first.")
        return 2
    git(repo, "reset", "--keep", backup)  # --keep refuses rather than lose work; the branches stay
    print(f"rolled back to {backup} (the branch is kept)")
    return 0


def demo():
    script = str(Path(__file__).resolve())
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        up, fork, clone, plain = tmp / "upstream", tmp / "fork", tmp / "clone", tmp / "plain"

        def g(repo, *a):
            return out(repo, *a)

        def setup_repo(repo):
            for k, v in (("user.name", "Demo"), ("user.email", "demo@example.com"), ("commit.gpgsign", "false")):
                g(repo, "config", k, v)

        def write(repo, name, text):
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(text, encoding="utf-8")

        def commit(repo, msg):
            g(repo, "add", "-A")
            g(repo, "commit", "-q", "-m", msg)

        def run(repo, *a):
            env = {**os.environ, "AI_EDITOR_UPSTREAM": up.as_posix(), "HOME": str(tmp), "USERPROFILE": str(tmp)}
            return subprocess.run([sys.executable, script, *a, "--repo", str(repo)], capture_output=True,
                                  text=True, encoding="utf-8", env=env)

        up.mkdir()
        g(up, "init", "-q", "-b", "main")
        setup_repo(up)
        write(up, "plugins/ai-editor/.claude-plugin/plugin.json", "{}\n")
        write(up, "CHANGELOG.md", "# Changelog\n\n## Unreleased\n")
        write(up, "a.txt", "one\ntwo\n")
        write(up, "tests/check_plugins.py", "print('plugins ok')\n")
        write(up, "tests/demos.sh", "exit 1\n")
        commit(up, "start")
        for d in (fork, clone):
            g(tmp, "clone", "-q", str(up), str(d))
            setup_repo(d)
        g(fork, "remote", "set-url", "origin", "https://github.com/someone/ai-video-editor.git")
        plain.mkdir()

        # which install
        assert "marketplace" in run(plain, "where").stdout
        assert "plain clone" in run(clone, "where").stdout
        r = run(fork, "where")
        assert "your fork" in r.stdout and "upstream: missing" in r.stdout, r.stdout
        assert run(fork, "check").returncode == 2
        assert run(fork, "add-upstream").returncode == 0
        assert "upstream: " + up.as_posix() in run(fork, "where").stdout
        # installed from a fork on GitHub, run outside the clone: points at the clone
        cache = tmp / ".claude/plugins/marketplaces/nextwork"
        g(tmp, "clone", "-q", str(fork), str(cache))
        g(cache, "remote", "set-url", "origin", "https://github.com/someone/ai-video-editor.git")
        assert "a fork" in run(plain, "where").stdout

        # the user's change, and a new upstream version that touches another file
        write(fork, "a.txt", "one\ntwo\nmine\n")
        commit(fork, "my change")
        write(up, "b.txt", "new\n")
        write(up, "CHANGELOG.md", "# Changelog\n\n## Unreleased\n\n- A new thing.\n")
        commit(up, "new thing")
        r = run(fork, "check")
        assert "1 new commit" in r.stdout and "- A new thing." in r.stdout and "also changes 0" in r.stdout, r.stdout

        # dirty tree: refused, no backup made
        write(fork, "a.txt", "one\ntwo\nmine\nhalf done\n")
        r = run(fork, "merge")
        assert r.returncode == 2 and "uncommitted" in r.stdout and "before-update" not in g(fork, "branch")
        g(fork, "checkout", "--", "a.txt")

        # clean merge keeps the user's change and takes upstream's
        r = run(fork, "merge")
        assert r.returncode == 0, r.stdout + r.stderr
        assert (fork / "b.txt").exists() and "mine" in (fork / "a.txt").read_text(encoding="utf-8")
        assert "before-update-" in g(fork, "branch")

        # verify runs both checks and points at rollback when one fails
        r = run(fork, "verify")
        assert r.returncode == 1 and "tests/demos.sh" in r.stdout and "rollback" in r.stdout, r.stdout

        # conflict: both sides change the same line
        write(up, "a.txt", "one\nTWO\n")
        commit(up, "fix two")
        write(fork, "a.txt", "one\nmy two\nmine\n")
        commit(fork, "my two")
        assert "both: a.txt" in run(fork, "check").stdout
        before = g(fork, "rev-parse", "HEAD")
        r = run(fork, "merge")
        assert r.returncode == 3 and "a.txt" in r.stdout, r.stdout
        assert len([b for b in g(fork, "branch").split() if b.startswith("before-update-")]) == 2

        # rollback aborts the merge, returns to the backup, keeps every branch
        r = run(fork, "rollback")
        assert r.returncode == 0, r.stdout + r.stderr
        assert g(fork, "rev-parse", "HEAD") == before and not dirty(fork)
        assert len([b for b in g(fork, "branch").split() if b.startswith("before-update-")]) == 2
    print("update demo ok")
    return 0


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "demo":
        return demo()
    repo = find_repo(rest[rest.index("--repo") + 1] if "--repo" in rest else None)
    if cmd == "where":
        say_where(repo)
        return 0
    if repo is None:
        say_where(repo)
        return 2
    if cmd == "add-upstream":
        if where(repo)[0] != "fork":
            say_where(repo)
            return 2
        if where(repo)[1]:
            print("upstream already set")
            return 0
        git(repo, "remote", "add", "upstream", upstream_url())
        print(f"added upstream: {upstream_url()}")
        return 0
    fn = {"check": check, "merge": merge, "verify": verify, "rollback": rollback}.get(cmd)
    if not fn:
        print(__doc__)
        return 2
    return fn(repo)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
