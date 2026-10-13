---
name: update
description: Updates Cooked Editor to its newest version. Works out how it was installed and does the right thing for each. The official plugin from the marketplace gets the three plugin commands. A fork the user owns (the power-user route, where they change the editor's own code) gets its new versions pulled from nextwork-projects/ai-video-editor-cooked and merged with their changes kept, after a backup branch, then the fast self-checks, with a rollback if they fail. A plain clone gets the fork route in three steps. Use when the user says "update the editor", "get the latest version", "sync my fork", "pull the new version", or asks how to stay up to date after changing the editor's code. Not for changing a style or a setting (that is taste or the home page), and not for maintainers turning feedback into fixes (that is improve).
license: MIT
compatibility: Python 3.9+, standard library only, and git. Fork updates run in the user's git checkout of their fork; the self-checks need bash (Git Bash on Windows).
---

# Update

Paths: `${CLAUDE_SKILL_DIR}` means the folder containing this SKILL.md, and `${CLAUDE_PLUGIN_ROOT}` the plugin folder two levels above it.

Every question goes in the question box: the AskUserQuestion tool (2-4 options, recommended first). Only in an agent without it, ask numbered questions in text.

`U="${CLAUDE_SKILL_DIR}/scripts/update.py"`. Run it with `python3` on Mac and Linux, `py` on Windows.
Add `--repo <folder>` when the user's clone is not the folder Claude Code runs in.

Never push, never force-push, never delete a branch, never stash or reset the user's uncommitted
work. Pushing to their fork happens only when they ask for it.

## 1. Which install

```bash
python3 "$U" where
```

- **marketplace**: say the three lines it prints (`/plugin marketplace update nextwork`,
  `/plugin install ai-editor@nextwork`, `/reload-plugins`). Their styles, skills and settings live in
  their own folder and survive (docs/customise.md). Stop here.
- **plain clone**: say the three fork steps it prints. Stop here.
- **a fork, run outside its clone**: ask for the folder of their clone, then use `--repo`.
- **fork, upstream missing**: ask "Add nextwork-projects/ai-video-editor-cooked as `upstream` so updates
  can come from it?" (**Yes, add it (Recommended)** / **No**). On yes: `python3 "$U" add-upstream`.

## 2. Check

```bash
python3 "$U" check
```

Tell the user in a few lines: how many new commits, the CHANGELOG lines they bring (plain words, the
ones they would notice), and which of their changed files the update also touches. Then ask
**Update now (Recommended)** / **Not now**.

## 3. Merge

```bash
python3 "$U" merge
```

- Exit 2, uncommitted changes: show what it printed. Ask **Commit them as "my changes"
  (Recommended)** / **I'll handle it myself**. Commit only on a yes, then merge again. Never stash.
- Exit 0: go to step 4.
- Exit 3, conflicts: resolve them file by file. For each file, keep the user's change and take
  upstream's fix, and say what you did in one line ("captions.tsx: kept your font, took the new
  safe-zone fix"). When the two really clash and the choice is theirs (both changed the same
  setting to different values), ask in the question box with each side as an option, theirs first.
  Then `git add` each file and `git commit --no-edit`.

The backup branch is `before-update-<date>`; say its name once.

## 4. Verify

```bash
python3 "$U" verify
```

It runs `tests/check_plugins.py` and `tests/demos.sh`. Pass: tell them to type `/reload-plugins`
(and, if they installed from their fork on GitHub, that pushing to it and
`/plugin marketplace update nextwork` brings it there, when they want). Fail: show the failing
checks and ask **Fix them (Recommended)** / **Go back to before the update**. Going back:

```bash
python3 "$U" rollback
```

It aborts an unfinished merge or moves the branch back to the backup. No branch is deleted.
