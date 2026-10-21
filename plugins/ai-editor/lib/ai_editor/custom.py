#!/usr/bin/env python3
"""The user's own layer: <library>/custom/. A plugin update replaces the plugin folder, never this one,
so every change a user makes to how the editor behaves lives here (or in taste.json). Stdlib only.

    python3 custom.py path                          print the folder (made, with its README, on first use)
    python3 custom.py rule "<rule>" [--file NAME]   add a rule to rules/NAME.md (default: feedback)
    python3 custom.py list                          every file the user added, by kind
    python3 custom.py demo                          self-check

Layout. Only what the code reads:
  rules/*.md               extra rules. `taste.py show` prints them after taste.md, and every skill runs
                           it before an edit, so every skill follows them.
  captions/<name>.json     a caption style (the captions block of style.json: size_pct, y_pct, color, ...).
                           On when taste.json (or style.json) says "captions": {"preset": "<name>"}.
                           It wins over the creator's measured captions; taste.json's own caption keys win over it.
  templates/<type>.json    default props for one motion template (flow, social_post, arrow_callout, ...).
                           A beat's own props win.
  sounds/                  the sound library (the video page's Sounds panel, style-edit sfx.py): your own sound
                           files, and choices.json, which sound plays for each moment (default, none, or a file).
  prompts/judge-rubric.md  replaces the cut critic's rubric (copy the plugin's skills/cut/references/judge-rubric.md
                           first and keep its output format).

`find(kind, name, default)` returns the user's file when there is one, else `default` (the plugin's).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import library  # noqa: E402

KINDS = ("rules", "captions", "templates", "prompts", "sounds")
README = """# Your changes to cooked.

Everything in this folder is yours. Updating the editor replaces the editor's own files, never this
folder, so what you put here keeps working after every update. The editor reads this folder first.

- `rules/*.md`: extra rules in plain words, one per line. Every edit follows them.
- `captions/<name>.json`: a caption style, like `{"size_pct": 5, "y_pct": 72, "color": "#FFFFFF"}`.
  It shows up as a tile on the style page, rendered on your own video. Pick it there, or run
  `taste.py set captions.preset <name>`.
- `templates/<type>.json`: default settings for one animation template, like `flow.json`.
- `sounds/`: your own sound effects, and which sound plays where (pick them in a video's Sounds panel).
- `prompts/judge-rubric.md`: your own version of the cut critic's rubric. Copy the editor's first.

Easiest is to tell Claude what you want changed. It writes the file here for you.
"""


def path(make=True):
    p = library.path(make) / "custom"
    if make:
        for k in KINDS:
            (p / k).mkdir(parents=True, exist_ok=True)
        if not (p / "README.md").exists():
            (p / "README.md").write_text(README, encoding="utf-8")
    return p


def find(kind, name, default=None):
    """The user's custom/<kind>/<name> when it is a file, else default (the plugin's own file, or None)."""
    assert kind in KINDS, kind
    p = path(False) / kind / name
    return p if p.is_file() else (Path(default) if default else None)


def load_json(kind, name):
    """custom/<kind>/<name>.json as a dict; {} when missing or broken (a broken file never stops an edit)."""
    f = find(kind, f"{name}.json")
    try:
        d = json.loads(f.read_text(encoding="utf-8")) if f else {}
    except (OSError, ValueError) as e:
        print(f"warning: {f} is not valid JSON ({e}); ignored", file=sys.stderr)
        return {}
    return d if isinstance(d, dict) else {}


def rules():
    """The user's rule files, by name."""
    return sorted((path(False) / "rules").glob("*.md"))


def rules_text():
    """Every rule file as one markdown block, or "" when there are none."""
    return "\n".join(f"## Your rules ({f.name}, in {f.parent})\n{f.read_text(encoding='utf-8').strip()}\n" for f in rules())


def add_rule(rule, file="feedback"):
    """One "- rule" line in rules/<file>.md; the same line twice is kept once. Returns the file."""
    f = path() / "rules" / f"{Path(file).stem}.md"
    have = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    line = "- " + rule.strip().lstrip("- ")
    if line not in have:
        f.write_text("\n".join(have + [line]).strip() + "\n", encoding="utf-8")
    return f


def listing():
    base = path(False)
    return {k: sorted(x.relative_to(base).as_posix() for x in (base / k).glob("*") if x.is_file()) for k in KINDS}


def demo():
    import os
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="custom_demo_"))
    os.environ["AI_EDITOR_HOME"] = str(tmp / "home")
    os.environ["AI_EDITOR_LIBRARY"] = str(tmp / "lib")
    # a stand-in plugin with its own rubric
    plugin = tmp / "plugin-v1"
    (plugin / "references").mkdir(parents=True)
    rubric = plugin / "references" / "judge-rubric.md"
    rubric.write_text("plugin rubric v1", encoding="utf-8")
    assert find("prompts", "judge-rubric.md", rubric) == rubric, "no user file: the plugin's"
    assert not (tmp / "lib" / "custom").exists(), "reading never makes the folder"
    assert rules() == [] and rules_text() == "" and load_json("captions", "bold") == {}
    # first use: the folder, its README and one folder per kind
    c = path()
    assert c == (tmp / "lib").resolve() / "custom" and (c / "README.md").exists()
    assert all((c / k).is_dir() for k in KINDS)
    # the user's files
    (c / "prompts" / "judge-rubric.md").write_text("my rubric", encoding="utf-8")
    (c / "captions" / "bold.json").write_text('{"size_pct": 8, "color": "#FFE600"}', encoding="utf-8")
    (c / "captions" / "broken.json").write_text("{nope", encoding="utf-8")
    f = add_rule("Never cut the first breath")
    add_rule("- Never cut the first breath")
    assert f.read_text(encoding="utf-8") == "- Never cut the first breath\n", "the same rule is kept once"
    assert find("prompts", "judge-rubric.md", rubric).read_text(encoding="utf-8") == "my rubric", "the user's wins"
    assert load_json("captions", "bold") == {"size_pct": 8, "color": "#FFE600"} and load_json("captions", "broken") == {}
    assert "Never cut the first breath" in rules_text() and listing()["captions"] == ["captions/bold.json", "captions/broken.json"]
    # an update: the plugin folder is deleted and a new version put in its place. The user's layer stays and still wins.
    shutil.rmtree(plugin)
    plugin = tmp / "plugin-v2"
    (plugin / "references").mkdir(parents=True)
    rubric = plugin / "references" / "judge-rubric.md"
    rubric.write_text("plugin rubric v2", encoding="utf-8")
    assert find("prompts", "judge-rubric.md", rubric).read_text(encoding="utf-8") == "my rubric"
    assert load_json("captions", "bold")["size_pct"] == 8 and [r.name for r in rules()] == ["feedback.md"]
    # the real plugin folder holds none of it: custom/ is never inside the plugin an update replaces
    real_plugin = Path(__file__).resolve().parents[2]
    assert real_plugin not in c.parents and c not in real_plugin.parents
    shutil.rmtree(tmp, ignore_errors=True)
    print("custom demo ok")


def main():
    a = sys.argv[1:]
    if a[:1] == ["demo"]:
        return demo()
    if a[:1] == ["path"]:
        return print(path())
    if a[:1] == ["list"]:
        return print(json.dumps(listing(), indent=1))
    if a[:1] == ["rule"] and len(a) in (2, 4) and (len(a) == 2 or a[2] == "--file"):
        return print(add_rule(a[1], a[3] if len(a) == 4 else "feedback"))
    print(__doc__)
    sys.exit(2)


if __name__ == "__main__":
    main()
