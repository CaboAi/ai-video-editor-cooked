#!/usr/bin/env python3
"""Learned rules in a skill's SKILL.md: where a video's notes go, near-duplicates, and the write itself.

    python3 skillrules.py target edits/NAME                 where this edit's rules go (a skill style's SKILL.md,
                                                            else the settings style styleskill.learn writes)
    python3 skillrules.py similar <SKILL.md> "<rule>"       the learned rules that look like it (sharpen or conflict?)
    python3 skillrules.py add <SKILL.md> "<rule>" --why "<reason>" [--replace "<old rule>"] [--edit edits/NAME]
    python3 skillrules.py remove <SKILL.md> "<rule>"       take one learned rule out (the page's Remove and Undo)
    python3 skillrules.py list <SKILL.md>                  every learned rule as JSON: rule, why, date, the video it came from
    python3 skillrules.py demo                              self-check

A skill style is a style.json with "skill": "<name>": that skill makes the edit, so its rules go into its own
SKILL.md, one line each under "## Learned rules":

    - Logos sit beside the head, never over the face. Why: two notes said a logo covered the chin. (2026-10-13)

and one line per change under "## Log" at the very end (the last LOG_KEEP kept). The same rule again is not
written twice; --replace swaps an old rule for the sharper or winning one. A settings style (no "skill") keeps
using styleskill.learn / taste.py add, which deduplicate in rules.json and rebuild that style's skill.
Skills inside a plugin are never written (an update replaces them). Stdlib only.
"""
import datetime
import json
import os
import re
import sys
from pathlib import Path

RULES, LOG = "## Learned rules", "## Log"
LOG_KEEP = 20
SIMILAR = 0.5   # share of content words two rules have in common to count as alike
STOP = set("a an the to of in on at for and or but is are be it its this that with as by from no not never always "
           "every each more less than so".split())


def skill_dirs():
    """Where the user's own skills live (read-write): $AI_EDITOR_SKILLS, the project's, the user's."""
    env = [Path(x) for x in os.environ.get("AI_EDITOR_SKILLS", "").split(os.pathsep) if x]
    return env + [Path.cwd() / ".claude" / "skills", Path.home() / ".claude" / "skills"]


def find_skill(name):
    """SKILL.md of a skill the user owns, or None. A plugin skill ("plugin:skill") is not theirs to write."""
    if not name or ":" in name or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
        return None
    return next((d / name / "SKILL.md" for d in skill_dirs() if (d / name / "SKILL.md").is_file()), None)


def target(edit):
    """Where an edit's rules go: {"kind": "skill", "skill", "path"} for a skill style, else
    {"kind": "settings", "style"} (None = My style), what styleskill.learn takes."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ai_editor import styleskill
    src = styleskill.sources(edit)
    sp = src[0] if len(src) == 1 else None
    st = json.loads(Path(sp).read_text(encoding="utf-8") or "{}") if sp else {}
    if st.get("skill"):
        md = find_skill(st["skill"])
        return {"kind": "skill", "skill": st["skill"], "path": str(md) if md else None, "style": str(sp)}
    return {"kind": "settings", "style": str(sp) if sp else None}


def words(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP}


def rule_of(line):
    """'- Rule. Why: ... (date)' -> 'Rule'."""
    return re.sub(r"\s+Why:.*$", "", line[2:].strip()).rstrip(".").strip()


def section(text, head):
    """(start, end) line indexes of a '## ' section's body, or None."""
    lines = text.split("\n")
    if head not in lines:
        return None
    i = lines.index(head) + 1
    j = next((k for k in range(i, len(lines)) if lines[k].startswith("## ")), len(lines))
    return i, j


def learned(text):
    """The rule lines of the Learned rules section."""
    s = section(text, RULES)
    return [ln for ln in text.split("\n")[s[0]:s[1]] if ln.startswith("- ")] if s else []


def similar(text, rule):
    """[(score, line)] of learned rules sharing most of their words with `rule`, best first."""
    w = words(rule)
    out = []
    for ln in learned(text):
        o = words(rule_of(ln))
        score = len(w & o) / max(1, min(len(w), len(o)))
        if score >= SIMILAR:
            out.append((round(score, 2), ln))
    return sorted(out, reverse=True)


def add(md, rule, why, replace=None, edit=None, today=None):
    """Write one rule line (and a Log line). Returns (status, line): added, sharpened or duplicate."""
    md = Path(md)
    if "/plugins/" in md.resolve().as_posix() and (md.parent.parent.parent / ".claude-plugin").is_dir():
        sys.exit(f"{md} ships inside a plugin; an update would replace it. Save the rule with taste instead.")
    rule, why = rule.strip().rstrip("."), why.strip().rstrip(".")
    if not rule or not why or "\n" in rule + why:
        sys.exit("a rule and its reason are one line each")
    day = today or datetime.date.today().isoformat()
    line = f"- {rule}. Why: {why}. ({day})"
    text = md.read_text(encoding="utf-8")
    have = learned(text)
    if any(rule_of(ln).lower() == rule.lower() for ln in have):
        return "duplicate", next(ln for ln in have if rule_of(ln).lower() == rule.lower())
    lines = text.rstrip("\n").split("\n")
    old = next((ln for ln in have if rule_of(ln).lower() == (replace or "").strip().rstrip(".").lower()), None)
    if replace and not old:
        sys.exit(f"no learned rule reads {replace!r}; `similar` lists them")
    if old:
        lines[lines.index(old)] = line
        status, note = "sharpened", f'"{rule_of(old)}" -> "{rule}"'
    else:
        s = section("\n".join(lines), RULES)
        if s is None:   # a new section, before the Log when there is one
            at = lines.index(LOG) if LOG in lines else len(lines)
            lines[at:at] = ([""] if at and lines[at - 1].strip() else []) + [RULES, "", line, ""]
        else:
            last = max([k for k in range(s[0], s[1]) if lines[k].startswith("- ")], default=s[0])
            lines.insert(last + 1, line)
        status, note = "added", f'"{rule}"'
    lines = _write_log(lines, day, f"{status} {note}" + (f" (from {edit})" if edit else ""))
    md.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return status, line


def _write_log(lines, day, entry):
    if LOG not in lines:
        lines += ["", LOG, ""]
    s = section("\n".join(lines), LOG)
    log = [ln for ln in lines[s[0]:s[1]] if ln.startswith("- ")]
    lines[s[0]:s[1]] = (log + [f"- {day} {entry}"])[-LOG_KEEP:]
    return lines


def remove(md, rule, today=None):
    """Take one learned rule out, by its words (case and the final period ignored). True when one was there."""
    md = Path(md)
    if "/plugins/" in md.resolve().as_posix() and (md.parent.parent.parent / ".claude-plugin").is_dir():
        return False   # a plugin's own skill: never written
    want = rule.strip().rstrip(".").lower()
    text = md.read_text(encoding="utf-8")
    hit = next((ln for ln in learned(text) if rule_of(ln).lower() == want), None)
    if not hit:
        return False
    lines = text.rstrip("\n").split("\n")
    lines.remove(hit)
    lines = _write_log(lines, today or datetime.date.today().isoformat(), f'removed "{rule_of(hit)}"')
    md.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return True


def listing(text):
    """[{rule, why, date, video}] of the Learned rules, the video from the newest Log line that added it."""
    s = section(text, LOG)
    log = [ln for ln in text.split("\n")[s[0]:s[1]] if ln.startswith("- ")] if s else []
    out = []
    for ln in learned(text):
        r = rule_of(ln)
        m = re.search(r"\sWhy:\s*(.*?)\.?\s*(?:\((\d{4}-\d{2}-\d{2})\))?\s*$", ln)
        src = next((re.search(r"\(from (.+)\)$", g) for g in reversed(log) if f'"{r}"' in g and "(from " in g), None)
        out.append({"rule": r, "why": m.group(1) if m else "", "date": m.group(2) if m else None,
                    "video": Path(src.group(1)).name if src else None})
    return out


def demo():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        md = Path(tmp) / "my-edit" / "SKILL.md"
        md.parent.mkdir()
        md.write_text("---\nname: my-edit\ndescription: x\n---\n\n# My edit\n\nCut tight.\n", encoding="utf-8")
        st, ln = add(md, "Logos sit beside the head, never over the face.", "a logo covered the chin twice", today="2026-10-13")
        assert st == "added" and ln == "- Logos sit beside the head, never over the face. Why: a logo covered the chin twice. (2026-10-13)", ln
        t = md.read_text(encoding="utf-8")
        assert t.index(RULES) < t.index(LOG) and t.rstrip().endswith('added "Logos sit beside the head, never over the face"'), t
        assert add(md, "logos sit beside the head, never over the face", "again", today="2026-10-14")[0] == "duplicate"
        assert len(learned(md.read_text(encoding="utf-8"))) == 1
        # alike: listed, so Claude sharpens it instead of adding a second line
        sim = similar(md.read_text(encoding="utf-8"), "Logos beside the head")
        assert sim and sim[0][1].startswith("- Logos sit"), sim
        assert similar(md.read_text(encoding="utf-8"), "Captions one size smaller") == []
        st, ln = add(md, "Logos sit beside the head at 20% of the width, never over the face", "the first rule left them tiny",
                     replace="Logos sit beside the head, never over the face", edit="edits/demo", today="2026-10-15")
        t = md.read_text(encoding="utf-8")
        assert st == "sharpened" and len(learned(t)) == 1 and "20% of the width" in learned(t)[0], t
        add(md, "Captions one size smaller", "said on two videos", today="2026-10-15")
        t = md.read_text(encoding="utf-8")
        assert len(learned(t)) == 2 and t.rstrip().split("\n")[-1].startswith("- 2026-10-15 added \"Captions"), t
        assert t.count(RULES) == 1 and t.count(LOG) == 1 and t.strip().split("\n")[-4] == LOG, t
        # list: date and the video from the Log; remove: the line goes, a Log line says so
        ls = listing(md.read_text(encoding="utf-8"))
        assert ls[0]["video"] == "demo" and ls[0]["date"] == "2026-10-15" and ls[0]["why"] == "the first rule left them tiny", ls[0]
        assert remove(md, "captions one size smaller.", today="2026-10-17") and not remove(md, "Captions one size smaller")
        t = md.read_text(encoding="utf-8")
        assert "Captions one size smaller. Why" not in t and t.rstrip().endswith('removed "Captions one size smaller"'), t
        add(md, "Captions one size smaller", "said on two videos", today="2026-10-15")
        for i in range(LOG_KEEP + 5):
            add(md, f"Rule number {i} for pacing", "test", today="2026-10-16")
        t = md.read_text(encoding="utf-8")
        s = section(t, LOG)
        assert len([x for x in t.split("\n")[s[0]:s[1]] if x.startswith("- ")]) == LOG_KEEP
        try:
            add(md, "x", "y", replace="no such rule")
            raise AssertionError("replace of a missing rule must refuse")
        except SystemExit:
            pass
        # find_skill: the user's folders only, never a plugin skill
        os.environ["AI_EDITOR_SKILLS"] = tmp
        assert find_skill("my-edit") == md and find_skill("ai-editor:style-edit") is None and find_skill("../x") is None
    print("skillrules ok")


def main():
    a = sys.argv[1:]
    if a == ["demo"]:
        return demo()
    if len(a) == 2 and a[0] == "target":
        print(json.dumps(target(Path(a[1])), indent=1))
    elif len(a) == 3 and a[0] == "similar":
        hits = similar(Path(a[1]).read_text(encoding="utf-8"), a[2])
        print("\n".join(f"{s:.2f}  {ln}" for s, ln in hits) or "nothing alike: add it")
    elif len(a) == 3 and a[0] == "remove":
        print("removed" if remove(a[1], a[2]) else "no such learned rule")
    elif len(a) == 2 and a[0] == "list":
        print(json.dumps(listing(Path(a[1]).read_text(encoding="utf-8")), indent=1))
    elif len(a) >= 3 and a[0] == "add":
        opt = dict(zip(a[3::2], a[4::2]))
        if "--why" not in opt:
            sys.exit("add needs --why \"<reason>\"")
        st, ln = add(a[1], a[2], opt["--why"], opt.get("--replace"), opt.get("--edit"))
        print(f"{st}: {ln}  ({a[1]})")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
