#!/usr/bin/env python3
"""Start and check a user's own skill (a video-editing skill that plugs into cooked.).

    python3 skill_maker.py where                  the folder new skills go in
    python3 skill_maker.py new <name>             <folder>/<name>/SKILL.md and evals/evals.json from the template
    python3 skill_maker.py check <skill folder>   what breaks the skill-authoring rules; exit 1 on a FAIL
    python3 skill_maker.py demo                   self-check

Where: the same folder cooked. writes style skills to (styleskill.skills_dir(): AI_EDITOR_SKILLS, else the
project's .claude/skills when it has a .claude folder, else ~/.claude/skills), so a skill made here shows up in
the home page's "Use your own skill" list. Stdlib only.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
RESERVED = ("claude", "anthropic")
MAX_LINES = 500
from ai_editor.skillrules import for_editing  # noqa: E402  the test home.py editing_skills() lists a skill by

TEMPLATE = """---
name: {name}
description: [What it does, third person, e.g. "Edits a talking-head video into ..."]. Use when the user says "[phrase]", "[phrase]" or [a situation].
---

# {title}

[One line: what goes in, what comes out.]

Copy this checklist and tick it off:

```
- [ ] 1. [step]
- [ ] 2. [step]
- [ ] 3. Check the result, fix, check again
- [ ] 4. Put the render on the review page
```

## 1. [step]

## Check

[The check, what a failure looks like, the fix. Run it again until it passes.]

## Example

[One real input and the output it should give.]

## Learned rules

Follow every rule here. skill-improver adds them from the notes on your videos.
"""


def skills_dir():
    from ai_editor import styleskill
    return styleskill.skills_dir()


def front(text):
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return {}, text
    head, body = text[4:].split("\n---\n", 1)
    return {k.strip(): v.strip() for k, v in (ln.split(":", 1) for ln in head.splitlines() if re.match(r"^[a-z-]+:", ln))}, body


def check(folder):
    """[(level, message)]: FAIL breaks a rule, WARN is worth fixing."""
    folder = Path(folder)
    md = folder / "SKILL.md"
    if not md.is_file():
        return [("FAIL", f"no {md}")]
    text = md.read_text(encoding="utf-8")
    fm, body = front(text)
    out = []
    name, desc = fm.get("name", ""), fm.get("description", "")
    if not NAME.match(name) or len(name) > 64:
        out.append(("FAIL", f"name {name!r}: lowercase letters, digits and hyphens, at most 64"))
    if any(w in name for w in RESERVED):
        out.append(("FAIL", f"name {name!r} must not contain {' or '.join(RESERVED)}"))
    if name and name != folder.name:
        out.append(("FAIL", f"name {name!r} must match the folder {folder.name!r}"))
    if not 0 < len(desc) <= 1024:
        out.append(("FAIL", f"description must be 1-1024 characters (is {len(desc)})"))
    if re.match(r"(I|You|We)\b", desc) or re.search(r"\b(I can|you can|I will)\b", desc):
        out.append(("FAIL", "description in the third person: \"Edits ...\", not \"I can\" or \"You can\""))
    if "use when" not in desc.lower():
        out.append(("FAIL", "description must say when to use it (\"Use when the user says ...\")"))
    if "[" in desc or "[step]" in body or "[One" in body:
        out.append(("FAIL", "template placeholders left in"))
    if text.count("\n") > MAX_LINES:
        out.append(("FAIL", f"{text.count(chr(10))} lines, over {MAX_LINES}: move detail into references/"))
    if "- [ ]" not in body:
        out.append(("WARN", "no copyable checklist (- [ ] steps)"))
    if not re.search(r"\b(check|verify|validate)\b", body, re.I):
        out.append(("WARN", "no check step: say how to tell the result is right and what to fix"))
    if "\\" in re.sub(r"```.*?```", "", body, flags=re.S):
        out.append(("WARN", "backslash outside a code block: write paths with forward slashes"))
    if not for_editing(name, desc):
        out.append(("WARN", "name and description never say video, captions, reel, footage or thumbnail: cooked.'s "
                            "\"Use your own skill\" list will not show it"))
    for ref in re.findall(r"\]\(((?:references|scripts)/[^)]+)\)|`((?:references|scripts)/[\w./-]+)`", body):
        ref = ref[0] or ref[1]
        if not (folder / ref).exists():
            out.append(("FAIL", f"points at {ref}, which does not exist"))
    ev = folder / "evals" / "evals.json"
    try:
        cases = json.loads(ev.read_text(encoding="utf-8")) if ev.is_file() else []
    except ValueError as e:
        cases = []
        out.append(("FAIL", f"{ev}: {e}"))
    good = [c for c in cases if isinstance(c, dict) and c.get("query") and c.get("expected_behavior")]
    if len(good) < 3:
        out.append(("FAIL", f"evals/evals.json needs 3 scenarios with query and expected_behavior (has {len(good)})"))
    return out


def new(name):
    if not NAME.match(name) or len(name) > 64 or any(w in name for w in RESERVED):
        sys.exit(f"name {name!r}: lowercase letters, digits and hyphens, at most 64, no {' or '.join(RESERVED)}")
    d = skills_dir() / name
    if (d / "SKILL.md").exists():
        sys.exit(f"{d / 'SKILL.md'} already exists")
    (d / "evals").mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(TEMPLATE.format(name=name, title=name.replace("-", " ").capitalize()), encoding="utf-8")
    (d / "evals" / "evals.json").write_text("[]\n", encoding="utf-8")
    return d


def demo():
    import os
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["AI_EDITOR_SKILLS"] = tmp
        d = new("caption-pop")
        assert d == Path(tmp) / "caption-pop" and (d / "SKILL.md").exists()
        msgs = " | ".join(m for _, m in check(d))
        assert "placeholders" in msgs and "3 scenarios" in msgs, msgs
        (d / "SKILL.md").write_text(
            "---\nname: caption-pop\ndescription: Edits a talking-head video so the key word of each caption pops "
            "in yellow. Use when the user says \"make my captions pop\".\n---\n\n# Caption pop\n\n"
            "- [ ] 1. Find the key words\n- [ ] 2. Check a still\n\nSee `references/colours.md`.\n", encoding="utf-8")
        (d / "evals" / "evals.json").write_text(json.dumps(
            [{"query": f"q{i}", "expected_behavior": ["does x"]} for i in range(3)]), encoding="utf-8")
        assert check(d) == [("FAIL", "points at references/colours.md, which does not exist")], check(d)
        (d / "references").mkdir()
        (d / "references" / "colours.md").write_text("yellow\n", encoding="utf-8")
        assert check(d) == [], check(d)
        (d / "SKILL.md").write_text("---\nname: claude-helper\ndescription: I can help.\n---\n", encoding="utf-8")
        levels = [m for lv, m in check(d) if lv == "FAIL"]
        assert any("must not contain" in m for m in levels) and any("third person" in m for m in levels) \
            and any("Use when" in m for m in levels) and any("match the folder" in m for m in levels), levels
        try:
            new("caption-pop")
            raise AssertionError("an existing skill is never overwritten")
        except SystemExit:
            pass
    print("skill_maker ok")


def main():
    a = sys.argv[1:]
    if a == ["demo"]:
        return demo()
    if a == ["where"]:
        print(skills_dir())
    elif len(a) == 2 and a[0] == "new":
        d = new(a[1])
        print(f"{d / 'SKILL.md'}\n{d / 'evals' / 'evals.json'}")
    elif len(a) == 2 and a[0] == "check":
        res = check(a[1])
        for lv, m in res:
            print(f"{lv} {m}")
        print("skill ok" if not res else f"{len(res)} to fix")
        sys.exit(1 if any(lv == "FAIL" for lv, _ in res) else 0)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
