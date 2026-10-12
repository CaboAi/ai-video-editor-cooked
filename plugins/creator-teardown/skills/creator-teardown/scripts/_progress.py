"""Long scripts put their own progress on the home page, so the window moves without Claude remembering to.
Stdlib only. creator-teardown keeps a byte-identical copy as scripts/_progress.py (check_plugins.py checks).

    import progress; progress.step("Transcribed every word", detail="video 3 of 7", progress=3 / 7)
    python3 progress.py demo     self-check

Only when the page has a job of that type queued or taken ($AI_EDITOR_HOME/home/state.json): then it is
one `home.py status <job> --if-active --step ...` call, the same write Claude makes, so a step Claude also
reports (same text) is updated in place, never listed twice. No page, no job, a broken state file or a
slow lock: nothing happens, and the script never stops or waits more than a few seconds for it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ACTIVE = ("queued", "taken")


def state_file():
    h = os.environ.get("AI_EDITOR_HOME", "").strip() or os.path.join(os.path.expanduser("~"), ".ai-video-editor")
    return Path(h) / "home" / "state.json"


def step(text, detail=None, progress=None, job="teardown", timeout=5):
    """True when the page's active `job` got the step. Never raises."""
    try:
        d = json.loads(state_file().read_text(encoding="utf-8"))
        if not any(j.get("type") == job and j.get("state") in ACTIVE for j in d.get("jobs", [])):
            return False
        home_py = Path(d.get("home_py") or Path(__file__).with_name("home.py"))   # home.py writes its own path
        if not home_py.is_file():
            return False
        cmd = [sys.executable, home_py.as_posix(), "status", job, "--if-active", "--step", text] + \
            (["--detail", detail] if detail else []) + (["--progress", f"{progress:.3f}"] if progress is not None else [])
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode == 0
    except Exception:   # the page is a nicety: nothing here may fail the script
        return False


def ticker(text, n, job="teardown"):
    """A callback for one finished item of n: "video 3 of 7" under the step, the bar at 3/7."""
    return lambda i: step(text, detail=f"video {i} of {n}", progress=i / n, job=job) if n > 1 else None


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
        assert step("Listed their videos") and ticker("Transcribed every word", 7)(3)
        assert ticker("Transcribed every word", 7)(4) and step("Transcribed every word")   # same text: in place
        d = json.loads(f.read_text(encoding="utf-8"))
        j = d["jobs"][0]
        assert j["state"] == "taken" and d["claude_seen"], "the first step takes the job"
        assert [s["text"] for s in j["steps"]] == ["Listed their videos", "Transcribed every word"], j["steps"]
        assert j["steps"][0]["done"] and j["steps"][1]["detail"] == "video 4 of 7" and abs(j["steps"][1]["progress"] - 4 / 7) < 0.01
        j["state"] = "done"
        f.write_text(json.dumps(d), encoding="utf-8")
        assert step("Writing the style") is False   # a finished job takes no more steps
    print("progress demo ok")


if __name__ == "__main__" and sys.argv[1:2] == ["demo"]:
    demo()
