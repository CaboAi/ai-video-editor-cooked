"""Long scripts put their own progress on the home page, so the window moves without Claude remembering to.
Stdlib only. creator-teardown keeps a byte-identical copy as scripts/_progress.py (check_plugins.py checks).

    import progress; progress.step("Transcribed every word", detail="video 3 of 7", progress=3 / 7, key=handle)
    python3 progress.py demo     self-check

Only when the page has a job of that type taken by Claude ($AI_EDITOR_HOME/home/state.json): then it is
one `home.py status <job> --if-active --step ...` call, the same write Claude makes, so a step Claude also
reports (same text as the newest step) is updated in place, never listed twice. A script never takes a
queued job: only Claude's `wait` or its own `status` does. `key` names the job (a video's edit folder, a
teardown's handle) so separate videos each get their own steps; with no key, only a lone taken job gets
them. No page, no job, a broken state file or a slow lock: nothing happens, and the script never stops
or waits more than a few seconds for it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

def state_file():
    h = os.environ.get("AI_EDITOR_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".ai-video-editor")
    return Path(h) / "home" / "state.json"


def step(text, detail=None, progress=None, job="teardown", key=None, timeout=5):
    """True when the page's taken `job` (the one `key` names) got the step. Never raises."""
    try:
        d = json.loads(state_file().read_text(encoding="utf-8"))
        if not any(j.get("type") == job and j.get("state") == "taken" for j in d.get("jobs", [])):
            return False
        home_py = Path(__file__).with_name("home.py")   # ai-editor: the home.py beside this file
        if not home_py.is_file():   # creator-teardown's copy: the path home.py writes into state.json
            home_py = Path(d.get("home_py") or "")
        if not home_py.is_file():
            return False
        cmd = [sys.executable, home_py.as_posix(), "status", job, "--if-active", "--step", text] + \
            (["--detail", detail] if detail else []) + (["--progress", f"{progress:.3f}"] if progress is not None else []) + \
            (["--key", str(key)] if key else [])
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode == 0
    except Exception:   # the page is a nicety: nothing here may fail the script
        return False


def ticker(text, n, job="teardown", key=None):
    """A callback for one finished item of n: "video 3 of 7" under the step, the bar at 3/7."""
    return lambda i: step(text, detail=f"video {i} of {n}", progress=i / n, job=job, key=key) if n > 1 else None


def demo():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AI_EDITOR_HOME"], os.environ["AI_EDITOR_SKILLS"] = tmp, os.path.join(tmp, "skills")
        f = state_file()
        assert step("Listed their videos") is False   # no state file: a no-op
        f.parent.mkdir(parents=True)
        f.write_text("{not json", encoding="utf-8")
        assert step("Listed their videos") is False   # broken state file: a no-op, no exception
        here = Path(__file__).resolve()
        home_py = next((p for p in (here.with_name("home.py"), here.parents[4] / "ai-editor/lib/ai_editor/home.py")
                        if p.is_file()), None)
        assert home_py, "home.py not found next to this file or in the repo"
        job = {"id": "teardown-1", "type": "teardown", "args": {"handle": "x"}, "state": "queued", "at": "", "steps": [], "result": {}}
        f.write_text(json.dumps({"root": tmp, "jobs": [job], "home_py": home_py.as_posix()}), encoding="utf-8")
        assert step("Cutting out the mistakes", job="video") is False   # no video job: nothing written
        assert json.loads(f.read_text(encoding="utf-8"))["jobs"][0]["steps"] == []
        assert step("Listed their videos", key="x") is False   # a queued job: a script never takes it
        assert json.loads(f.read_text(encoding="utf-8"))["jobs"][0]["state"] == "queued", "wait still hands it out"
        job["state"] = "taken"
        f.write_text(json.dumps({"root": tmp, "jobs": [job], "home_py": home_py.as_posix()}), encoding="utf-8")
        assert step("Listed their videos", key="@Other") is False   # another creator's key: skipped
        assert step("Listed their videos", key="@X") and ticker("Transcribed every word", 7, key="x")(3)
        assert ticker("Transcribed every word", 7)(4) and step("Transcribed every word")   # keyless, lone job; in place
        d = json.loads(f.read_text(encoding="utf-8"))
        j = d["jobs"][0]
        assert j["state"] == "taken" and d["claude_seen"]
        assert [s["text"] for s in j["steps"]] == ["Listed their videos", "Transcribed every word"], j["steps"]
        assert j["steps"][0]["done"] and j["steps"][1]["detail"] == "video 4 of 7" and abs(j["steps"][1]["progress"] - 4 / 7) < 0.01
        # separate videos: two taken video jobs, each script's report lands on its own edit folder
        ed = [Path(tmp) / "edits" / n for n in ("a", "b")]
        vids = [{"id": f"video-{i + 1}", "type": "video", "args": {"edit": e.as_posix()}, "state": "taken", "at": "",
                 "steps": [], "result": {}} for i, e in enumerate(ed)]
        d["jobs"] += vids
        j["state"] = "done"
        f.write_text(json.dumps(d), encoding="utf-8")
        assert step("Writing the style") is False   # a finished job takes no more steps
        assert step("Cutting out the mistakes", job="video") is False   # two taken, no key: nowhere safe to go
        assert step("Wrote down every word you said", job="video", key=ed[0] / "transcript.json")
        assert step("Cutting out the mistakes", job="video", key=ed[1])
        assert step("Cutting out the mistakes", job="video", key=Path(tmp) / "edits" / "c") is False   # no match: skip
        v = json.loads(f.read_text(encoding="utf-8"))["jobs"][1:]
        assert [[s["text"] for s in x["steps"]] for x in v] == [["Wrote down every word you said"], ["Cutting out the mistakes"]], v
    print("progress demo ok")


if __name__ == "__main__" and sys.argv[1:2] == ["demo"]:
    demo()
