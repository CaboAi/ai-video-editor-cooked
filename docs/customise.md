# Customise the editor

Updating the editor replaces the plugin's own folder. Your changes never live there, so they survive
every update. The editor reads your files first and falls back to its own.

## What lives where

| What | Where | Survives updates |
|---|---|---|
| The plugin: skills, scripts, references, the renderer | the plugin folder Claude Code installed | no, replaced on update |
| Your videos and edits | the library: `~/Movies/AI Editor` (macOS) or `~/Videos/AI Editor` (Windows, Linux) | yes |
| Your own rules, caption styles, template defaults, prompts | `<library>/custom/` | yes |
| Your corrections and settings | `~/.ai-video-editor/taste.md`, `taste.json`, `rules.json` | yes |
| Your brand kit and answers from the first run | `~/.ai-video-editor/profile.json` | yes |
| Keys | `~/.config/creator-teardown/.env` (or `$AI_EDITOR_HOME/.env` when that is set) | yes |
| Corrections to a creator's style | `<library>/creator-teardowns/<handle>/style.overrides.json` | yes |
| Style skills (`my-style`, `edit-like-<handle>`) | `~/.claude/skills/` | yes |

`AI_EDITOR_LIBRARY` moves the library and `AI_EDITOR_HOME` moves `~/.ai-video-editor`. Style skills go to
`<project>/.claude/skills/` instead when you work in a project that has a `.claude` folder.

The easiest way to change anything is to tell Claude in plain words ("captions bigger", "never cut my
first breath"). It writes the change into the files above, never into the plugin.

## The custom folder

Made the first time you open it: Settings, then "Your changes", then Show in Finder (or Explorer).
Or run `python3 <plugin>/lib/ai_editor/custom.py path`.

```
custom/
  README.md                 what this folder is
  rules/*.md                extra rules, one per line. Every edit follows them.
  captions/<name>.json      a caption style
  templates/<type>.json     default settings for one animation template
  prompts/judge-rubric.md   your own version of the cut critic's rubric
```

Only these four are read. Other files in the folder are ignored.

## Add a rule

Write a line in any `.md` file in `custom/rules/`, or run:

```bash
python3 <plugin>/lib/ai_editor/custom.py rule "Never cut the breath before the first word"
```

Every skill reads your taste before an edit (`taste.py show`), and that prints these files too.

## Add a caption style

1. Save a file like `custom/captions/bold.json`. It takes the same keys as the `captions` block of a
   style.json:

   ```json
   {"size_pct": 6, "y_pct": 72, "color": "#FFFFFF", "highlight_color": "#FFE600", "words_per_caption": 2}
   ```

2. Turn it on for every video:

   ```bash
   python3 <plugin>/skills/taste/scripts/taste.py set captions.preset bold
   ```

It replaces the creator's caption settings. A caption setting you saved in your taste (like
`captions.size_pct`) still wins over it, and so do corrections saved on one creator's style.

## Template defaults

`custom/templates/flow.json` holds props every `flow` card starts from. A card's own props win. The
template names are in `plugins/ai-editor/skills/style-edit/references/motion.md`.

## The cut critic's rubric

Copy `plugins/ai-editor/skills/cut/references/judge-rubric.md` to `custom/prompts/judge-rubric.md`
and edit it. Keep its output format: the cut loop reads the critic's answer in that shape.

## What cannot be overridden

- **Remotion components.** The renderer bundles the plugin's own `remotion/src`. A component of your
  own would have to be copied into the plugin folder, which an update deletes. Use template defaults
  and caption styles instead, or ask for the component to be added to the editor.
- **Look presets.** Colours and fonts come from your brand kit (`profile.json`, set in the first run),
  which already wins over the creator's look.
- **The plugin's skills and references.** Add a rule in `custom/rules/` instead of editing a SKILL.md.

## Feedback

When you correct an edit, the taste skill saves it in your taste files. The improve skill, run
outside a maintainer's checkout of the repo, saves to `custom/` and `taste.json` only. A fix that
would help every user can go to the maintainers as a suggestion; it reaches you as a normal update.
