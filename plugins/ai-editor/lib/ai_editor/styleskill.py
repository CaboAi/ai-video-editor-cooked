#!/usr/bin/env python3
"""Every saved style is a small skill, and feedback on that style edits the skill.

    python3 styleskill.py write <style.json> [--format <skill-name>]   edit-like-<slug> for a creator, a blend or a
                                  -mine style (a "-mine" folder is named after its creator). --format links the
                                  bigger format skill when creator-teardown's skill-handoff gate passed.
    python3 styleskill.py write --mine          my-style: the user's own style (profile + taste)
    python3 styleskill.py learn <style.json> <section> "<rule>" [key=value] [--edit edits/NAME]
                                  a correction at the "this style" scope (taste.py add --style does the same)
    python3 styleskill.py demo                  self-check

Where: AI_EDITOR_SKILLS (a self-check that moves taste.HOME: <that folder>/skills), else <cwd>/.claude/skills when the user works in a project that has a .claude
folder, else ~/.claude/skills. The skill is thin: triggers, how to run the editor with that style, what it
copies, and "## Learned from your feedback", rebuilt from rules.json (taste.py) on every save, so a rule is
one bullet with its count, last date and any regression.

The measured style.json is never written. A correction's setting goes to style.overrides.json in the
creator's own folder (creator-teardowns/<slug>/; a re-study rewrites style.json, never this file).
taste.load_json() lays it over taste.json, so: measured style.json < taste.json (every video)
< style.overrides.json (this style) < this-video settings. The narrower, later correction wins.
Stdlib only.
"""
import datetime
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ai_editor import taste  # noqa: E402

MINE = "my-style"
OVERRIDES = "style.overrides.json"
HOME = Path(os.environ.get("AI_EDITOR_HOME", Path.home() / ".ai-video-editor"))   # the real taste folder
FORMAT_LINE = re.compile(r"Script format: the `([^`]+)` skill")


def skills_dir():
    env = os.environ.get("AI_EDITOR_SKILLS")
    if env:
        return Path(env)
    if taste.HOME != HOME:   # a self-check pointed the taste at a temp folder: its skills go there too
        return taste.HOME / "skills"
    proj = Path.cwd() / ".claude"
    return proj / "skills" if proj.is_dir() else Path.home() / ".claude" / "skills"


def slug(style_path):
    s = re.sub(r"-mine$", "", Path(style_path).resolve().parent.name.lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "creator"


def name(style_path=None):
    return MINE if style_path is None else f"edit-like-{slug(style_path)}"[:64].rstrip("-")


def scope(style_path=None):
    return "all" if style_path is None else f"style:{slug(style_path)}"


def overrides_path(style_path):
    """In the creator's own folder: <h>-mine and <h> share one file."""
    d = Path(style_path).resolve().parent
    return d.parent / re.sub(r"-mine$", "", d.name) / OVERRIDES


def load_overrides(style_path):
    p = overrides_path(style_path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def set_override(style_path, key, value=None, drop=False):
    p = overrides_path(style_path)
    data = load_overrides(style_path)
    data = taste.unset_key(data, key) if drop else taste.set_key(data, key, value)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return p


def sources(path):
    """The creator style.json(s) an edit used: its style.json's "source" (profile.py style writes it), or the
    file itself when it sits in creator-teardowns/<x>/. [] for the default style."""
    p = Path(path)
    f = p / "style.json" if p.is_dir() else p
    if not f.is_file():
        return []
    try:
        s = json.loads(f.read_text(encoding="utf-8") or "{}")
    except ValueError:
        return []
    if s.get("source"):
        return [Path(x) for x in s["source"]]
    return [f.resolve()] if f.resolve().parent.parent.name == "creator-teardowns" else []


def preferred(style_path):
    """<h>/style.json with a <h>-mine beside it (the parts the user ticked): the -mine one."""
    p = Path(style_path).resolve()
    mine = p.parent.parent / f"{p.parent.name}-mine" / "style.json"
    return mine if mine.is_file() else p


def copies(style):
    """What the style copies, in measured numbers only; parts left out of a --take blend are skipped."""
    take = (style.get("blend") or {}).get("take")
    has = lambda part: take is None or part in take
    out = []
    c = style.get("captions") or {}
    if has("captions") and c and c.get("present") is not False:
        bits = [f"{c['size_pct']}% tall" if c.get("size_pct") else "", f"{c['words_per_caption']} words a caption"
                if c.get("words_per_caption") else "", c.get("color") or "", c.get("font_match") or ""]
        out.append("captions (" + ", ".join(b for b in bits if b) + ")" if any(bits) else "captions")
    p, z = style.get("pace") or {}, style.get("zoom") or {}
    if has("pace") and (p or z):
        bits = [f"a cut every {p['median_shot_s']} s" if p.get("median_shot_s") else "",
                f"{z['per_min']} zooms a minute" if z.get("per_min") is not None else ""]
        out.append("pace (" + ", ".join(b for b in bits if b) + ")" if any(bits) else "pace")
    g = style.get("graphics") or {}
    if (has("graphics") or has("entrances") or has("visuals")) and g:
        out.append(f"visuals (graphics on {g['share_pct']}% of the video)" if g.get("share_pct") is not None else "visuals")
    if has("sound") and (style.get("sound") or style.get("sfx") is not None):
        out.append("sound" + (" (no sound effects)" if style.get("sfx") is False else ""))
    return out


def learned(sc):
    """One bullet per rule of this scope, from rules.json (deduplicated there, as taste rules are)."""
    rules = [r for r in taste.load_rules() if r["scope"] == sc] if taste.rules_path().exists() else []
    lines = []
    for r in rules:
        setting = f" Setting: `{r['setting']} = {json.dumps(r['value'])}`." if r.get("setting") else ""
        last = r["corrections"][-1]["at"][:10] if r["corrections"] else "never"
        reg = r["regressions"]
        lines.append(f"- {r['rule']}.{setting} Said {len(r['corrections'])}x, last {last}."
                     + (f" Came back after it was applied: {len(reg)}x, last {reg[-1]['at'][:10]}." if reg else ""))
    return lines


def body(style_path=None, fmt=None):
    if style_path is None:
        prof = json.loads((taste.HOME / "profile.json").read_text(encoding="utf-8")) if (taste.HOME / "profile.json").exists() else {}
        who = ", ".join("@" + c["handle"] + (f" ({', '.join(c['take'])})" if c.get("take") else "")
                        for c in prof.get("creators") or [] if c.get("handle")) or "no creator (the default style)"
        cap = (prof.get("captions") or {}).get("style") or "creator"
        desc = ("Edits the user's video in their own saved style: their editing profile (creators to copy, captions, "
                "sound) and every correction they saved as \"my style\". Use when the user says \"edit my video in my "
                "style\", \"use my style\", \"edit it the way I like\" or \"/my-style\". Not for copying one creator "
                "(the edit-like-<handle> skills), not for studying a creator (creator-teardown).")
        head = (f"# My style\n\nEdits the user's video in their own style with the ai-editor plugin.\n\n"
                f"- Profile: `{taste.HOME / 'profile.json'}` (copies {who}; captions: {cap})\n"
                f"- Taste: `{taste.HOME / 'taste.md'}` and `taste.json` (the settings the scripts read)\n\n"
                "## Run\n\n1. Run the ai-editor `start` skill on the user's video. It reads the profile and the taste by itself.\n"
                "2. Review notes that are preferences: scope `My style` saves them to the taste and to the section below.\n\n"
                "Follow every rule in \"Learned from your feedback\".\n")
        empty = "Nothing yet. Notes saved at \"My style\" land here."
    else:
        sp = Path(style_path).resolve()
        style = json.loads(sp.read_text(encoding="utf-8") or "{}")
        h = re.sub(r"-mine$", "", style.get("handle") or sp.parent.name)   # editplan --take names it <h>-mine
        n = name(sp)
        parts = copies(style)
        mix = list((style.get("blend") or {}).get("weights") or [])
        who, said = (f"the {h} blend of " + " and ".join("@" + x for x in mix), h) if len(mix) > 1 else (f"@{h}'s style", "@" + h)
        desc = (f"Edits the user's video in {who}, measured from their videos"
                + (f" ({', '.join(p.split(' (')[0] for p in parts)})" if parts else "")
                + f", with the corrections the user gave on that style. Use when the user says \"edit my video like {said}\", "
                f"\"in {said}'s style\", \"make it look like {h}'s videos\" or \"/{n}\". Not for studying a new creator "
                "(creator-teardown) and not for a correction meant for every video (taste).")
        h = said.lstrip("@") if len(mix) > 1 else h
        head = (f"# Edit like {said}\n\nEdits the user's video in {who} (measured) with the ai-editor plugin.\n\n"
                f"- Style: `{sp}` (measured by creator-teardown; never edit it by hand)\n"
                f"- Corrections: `{overrides_path(sp)}` (laid over the style; a re-study keeps them)\n"
                + (f"- Copies: {'; '.join(parts)}\n" if parts else "")
                + (f"- Script format: the `{fmt}` skill (it writes the script; this one edits the footage)\n" if fmt else "")
                + "\n## Run\n\n"
                "1. Run the ai-editor `start` skill on the user's video. At its \"Blend the style\" step use this style\n"
                "   instead of the profile's creators (`$P` is the plugin's `lib/ai_editor/profile.py`):\n"
                f"   `python3 \"$P\" style edits/<name> --from \"{sp}\"`\n"
                "2. Then cut and style-edit as start says. plan.py and the cut lay the corrections over the style themselves.\n"
                f"3. Review notes that are preferences: ask the scope with `This style (@{h}) (Recommended)` first, then\n"
                "   `review.py edits/<name> learn --scope this-style ...`. They land in the section below.\n\n"
                "Follow every rule in \"Learned from your feedback\". Where one disagrees with the user's general taste,\n"
                "the rule here wins on this style.\n")
        empty = f"Nothing yet. Notes saved at \"This style (@{h})\" land here."
    rules = learned(scope(style_path))
    front = f"---\nname: {name(style_path)}\ndescription: {desc}\n---\n\n"
    return front + head + "\n## Learned from your feedback\n\n" + ("\n".join(rules) if rules else empty) + "\n"


def write(style_path=None, fmt=None):
    """Write or refresh the thin skill; returns its SKILL.md path. A format skill linked before is kept."""
    if style_path is not None:
        style_path = preferred(style_path)
    out = skills_dir() / name(style_path) / "SKILL.md"
    if fmt is None and out.exists():
        m = FORMAT_LINE.search(out.read_text(encoding="utf-8"))
        fmt = m.group(1) if m else None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body(style_path, fmt), encoding="utf-8")
    return out


def learn(style_path, section, rule, setting=None, value=None, edit=None, source=None):
    """A correction at this style's scope: rules.json (counted, deduplicated), the overrides file and the
    skill's section. style_path None = the user's own style (taste.json, my-style)."""
    return taste.add(section, rule, setting, value, scope(style_path), edit, source, style=style_path)


def demo():
    old = os.environ.get("AI_EDITOR_SKILLS"), taste.HOME
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        os.environ["AI_EDITOR_SKILLS"] = str(d / "skills")
        taste.HOME = d / "home"
        sp = d / "creator-teardowns" / "some.creator" / "style.json"
        sp.parent.mkdir(parents=True)
        measured = {"handle": "some.creator", "captions": {"present": True, "size_pct": 6.0, "words_per_caption": 3,
                                                            "color": "#FFFFFF"}, "pace": {"median_shot_s": 2.1},
                    "zoom": {"per_min": 4}, "graphics": {"share_pct": 40}}
        sp.write_text(json.dumps(measured), encoding="utf-8")
        md = write(sp)
        assert md == d / "skills" / "edit-like-some-creator" / "SKILL.md", md
        t = md.read_text(encoding="utf-8")
        assert t.startswith("---\nname: edit-like-some-creator\ndescription: ") and "edit my video like @some.creator" in t
        assert "6.0% tall, 3 words a caption" in t and f"--from \"{sp.resolve()}\"" in t and "Nothing yet" in t, t
        assert len(t.split("description: ")[1].split("\n")[0]) <= 1024
        # an edit made with it records the source; the overrides apply over the measured style
        ed = d / "edits" / "vid"
        ed.mkdir(parents=True)
        (ed / "cut.mp4").write_bytes(b"")
        (ed / "style.json").write_text(json.dumps({**measured, "source": [str(sp.resolve())]}), encoding="utf-8")
        assert sources(ed) == [sp.resolve()] and sources(sp) == [sp.resolve()] and sources(d / "nope") == []
        st, r = learn(sp, "captions", "Captions smaller", "captions.size_pct", 5, edit=ed)
        assert st == "added" and r["scope"] == "style:some-creator"
        assert load_overrides(sp) == {"captions": {"size_pct": 5}} and json.loads(sp.read_text(encoding="utf-8")) == measured
        assert taste.read_json() == {} and "Captions smaller" not in taste.load_md()["Captions"]   # not every video
        # said again before an edit used it: one bullet, count 2
        st, _ = learn(sp, "captions", "Captions a bit smaller", "captions.size_pct", 4.5, edit=ed)
        t = md.read_text(encoding="utf-8")
        assert st == "updated" and t.split("## Learned")[1].count("\n- ") == 1 and "Said 2x" in t and "captions.size_pct = 4.5" in t, t
        # plan.py's call: taste.json < overrides; counted as applied to that edit
        taste.add("captions", "Captions bigger", "captions.size_pct", 7)   # every video
        old_argv, sys.argv = sys.argv, ["plan.py", str(ed / "style.json"), str(ed / "captions.json")]
        try:
            got = taste.merge(measured, taste.load_json(style=ed / "style.json"))
        finally:
            sys.argv = old_argv
        assert got["captions"]["size_pct"] == 4.5 and got["captions"]["words_per_caption"] == 3, got
        # said again after it was applied: a regression, shown in the skill
        st, _ = learn(sp, "captions", "Captions smaller still", "captions.size_pct", 4, edit=ed)
        t = md.read_text(encoding="utf-8")
        assert st == "regression" and "Said 3x" in t and "Came back after it was applied: 1x" in t, t
        assert "this style (@some-creator)" in taste.report(), taste.report()
        # a re-study rewrites style.json: the corrections stay
        sp.write_text(json.dumps({**measured, "captions": {**measured["captions"], "size_pct": 6.4}}), encoding="utf-8")
        assert write(sp) == md and load_overrides(sp) == {"captions": {"size_pct": 4}}
        assert "Said 3x" in md.read_text(encoding="utf-8")
        # the ticked parts (<h>-mine) take over the same skill and share the same corrections; a format link stays
        mine = sp.parent.parent / "some.creator-mine" / "style.json"
        mine.parent.mkdir()
        mine.write_text(json.dumps({**measured, "handle": "some.creator-mine", "blend": {"take": ["captions"]}}), encoding="utf-8")
        assert write(mine, fmt="numbered-list") == md and overrides_path(mine) == overrides_path(sp)
        assert write(sp) == md   # a later re-study of <h> keeps pointing at the -mine parts
        t = md.read_text(encoding="utf-8")
        assert "like @some.creator\"" in t and "Script format: the `numbered-list` skill" in t and str(mine.resolve()) in t and "pace (" not in t, t
        # "my style": taste as before, and the my-style skill
        my = write()
        assert "name: my-style" in my.read_text(encoding="utf-8") and "Captions bigger." in my.read_text(encoding="utf-8")
        taste.forget(r["id"])
        assert load_overrides(sp) == {"captions": {}} and "Nothing yet" in md.read_text(encoding="utf-8")
        # a temp taste folder keeps its skills beside it; a project with a .claude folder gets them there
        del os.environ["AI_EDITOR_SKILLS"]
        assert skills_dir() == taste.HOME / "skills"
        taste.HOME = HOME
        cwd = os.getcwd()
        (d / "proj" / ".claude").mkdir(parents=True)
        os.chdir(d / "proj")
        try:
            assert skills_dir().resolve() == (d / "proj" / ".claude" / "skills").resolve()
        finally:
            os.chdir(cwd)
    if old[0] is None:
        os.environ.pop("AI_EDITOR_SKILLS", None)
    else:
        os.environ["AI_EDITOR_SKILLS"] = old[0]
    taste.HOME = old[1]
    print("styleskill demo ok")


def main():
    a = sys.argv[1:]
    flag = lambda k: a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else None
    if a == ["demo"]:
        return demo()
    if a[:1] == ["write"] and len(a) >= 2:
        sp = None if a[1] == "--mine" else a[1]
        if sp and not Path(sp).is_file():
            sys.exit(f"no {sp}")
        out = write(sp, flag("--format"))
        print(f"skill {out.parent.name} -> {out}")
    elif a[:1] == ["learn"] and len(a) >= 4:
        kv = [x for x in a[4:] if "=" in x and not x.startswith("--")]
        setting, _, raw = kv[0].partition("=") if kv else (None, "", "")
        st, r = learn(a[1], a[2], a[3], setting, taste.parse(raw) if setting else None, flag("--edit"))
        print(f"{st}: #{r['id']} {r['section']}: {r['rule']} (this style: {name(a[1])})")
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
