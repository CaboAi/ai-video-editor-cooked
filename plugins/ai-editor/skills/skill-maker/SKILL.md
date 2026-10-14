---
name: skill-maker
description: Helps the user make their own Claude Code skill for editing videos, step by step. Interviews them briefly (what the skill does, an example video, what good looks like), runs the task once without a skill to see what goes wrong, writes three test scenarios, drafts the SKILL.md to the skill-authoring guide (clear name, a description that says what and when, a checklist, a check-and-fix loop, one example), saves it to their skills folder, tests it against the scenarios in a fresh agent, and improves it with them. Skills made here plug into cooked. (the edit folder, the review page, the safe zone) and can run as a style. Use when the user says "make a skill", "create a skill", "turn this into a skill", "help me write a skill" or "build my own editing skill". Not for adding rules to a skill that exists from a video's notes (skill-improver), and not for copying a creator's style from their videos (creator-teardown).
license: MIT
compatibility: Python 3.9+, standard library only. Runs from the full ai-editor plugin folder (uses its lib/). Testing in a fresh agent needs the Agent tool.
---

# Skill maker

Paths: `${CLAUDE_SKILL_DIR}` means the folder containing this SKILL.md, and `${CLAUDE_PLUGIN_ROOT}` the plugin folder two levels above it.

Every question to the user goes in the question box: call the AskUserQuestion tool (2-4 options, the recommended one first). Only in an agent without that tool, ask numbered questions in text.

The process follows Anthropic's public skill-creator and its skill-authoring best practices, cut down for
video skills. `M="${CLAUDE_SKILL_DIR}/scripts/skill_maker.py"`. Run it with `python3` on Mac and Linux, `py` on Windows.

Copy this checklist and tick it off:

```
- [ ] 1. Interview done
- [ ] 2. Task run once without a skill, failures noted
- [ ] 3. Three scenarios in evals/evals.json
- [ ] 4. SKILL.md drafted, `check` passes
- [ ] 5. Saved and shown to the user
- [ ] 6. Tested in a fresh agent, pass/fail reported
- [ ] 7. Improved with the user
- [ ] 8. Offered as a style; skill-improver named for later notes
```

## 1. Interview

If this chat already shows the workflow ("turn this into a skill"), take the steps, tools and the user's
corrections from it and only ask for the gaps. From the Skills page ("Make a new skill", `JOB skill-new
text=...`), the text answers question 1; report each checklist step with `home.py status skill-new --step "..."`
(`start/references/home.md`). Otherwise ask, one question box at a time:

1. What it does: `Edits a whole video in a look (Recommended)` / `Adds one kind of visual` / `Cuts or tightens` / `Something else`.
2. An example: a video in the library (`edits/<name>/`), a file path, or a link to a video in the look they want.
3. What good looks like: two or three things they would check on the result ("captions never cover my face").
4. The name: offer two short hyphenated names from their words (`caption-pop`, `quick-cuts`).

## 2. Run it once without a skill

Do the task on the example the plain way, without writing a skill. Note each place you guessed, asked, or got
it wrong, and anything the user corrected. These notes are what the skill must say; skip what you already did right.

## 3. Write three scenarios

```bash
python3 "$M" new <name>
```

That makes `<skills folder>/<name>/SKILL.md` (a template) and `evals/evals.json`. Fill the evals first, one
ordinary request, one edge case, one where the skill should not do something:

```json
[{"query": "make my captions pop on edits/demo", "files": ["edits/demo/cut.mp4"],
  "expected_behavior": ["Reads the user's taste first", "Key word of each caption in yellow", "Nothing outside the safe zone", "Render on the review page"]}]
```

## 4. Draft the SKILL.md

Write it from step 2's notes. Read `references/cooked-hooks.md` for where the edit's files are, how to hand a
render to the review page, the safe zone and how to reuse the editor's renderer. Rules:

- `name`: lowercase, digits and hyphens, at most 64, no "claude" or "anthropic", same as the folder.
- `description`: third person, what it does then "Use when the user says ..." with their own phrases, at most
  1024 characters. Name video, edit, reel or clip in the first sentence so cooked.'s style list shows it.
- Body: only what Claude would not know already, a copyable checklist, a check step with the fix (check,
  fix, check again), one real example. Under 500 lines; longer detail in `references/<topic>.md`, linked from
  SKILL.md, one level deep. A step that is the same every time is a script in `scripts/`: write "Run X".
- End with an empty `## Learned rules` section (skill-improver fills it).

Then check it and fix every FAIL:

```bash
python3 "$M" check <skills folder>/<name>
```

## 5. Save and show

The skill is already in its folder (`python3 "$M" where` prints it). Show the user the whole SKILL.md and
the scenarios, and the path.

## 6. Test in a fresh agent

For each scenario, launch an agent with no memory of this chat (the Agent tool, general-purpose), give it the
skill's full SKILL.md path and the scenario's query, and have it do the task (or, with no footage, write each
command and what it expects). Grade every `expected_behavior` line yourself as pass or fail from what it did.
Report a table: scenario, pass/fail, why.

## 7. Improve with the user

Show the failures and what the user thinks of the result. Change the SKILL.md for each failure (a missing step,
a vague rule, a check that would have caught it), run `check`, then test the failing scenarios again. Stop when
they all pass and the user is happy, or after three rounds, saying what is left.

## 8. Hand over

Ask in the question box: `Make it a style (Recommended)` / `Not now`. Make it a style: cooked.'s window,
Skills > `/<name>` > Use as a style (or Styles > New style > Use your own skill); every video made with that
style runs the skill.
Say in one line that notes on later videos go into the skill with skill-improver.

## Example

"Make a skill that adds a yellow underline under the word I stress." Interview: adds one visual; example
`edits/demo`; good = underline lands on the word, never over the face. The plain run guessed the stressed word
and drew it below the safe zone, so the draft says how to pick the word (asked once per video when the script
marks none) and checks a still with the safe-zone box. Scenarios: a normal video, a video with no clear stress,
"underline every word" (should push back). Saved as `stress-underline`, 3 of 3 passing after one round.

## Files

- `scripts/skill_maker.py`: `where`, `new`, `check`, `demo`.
- `references/cooked-hooks.md`: what a video skill can read and call in cooked.
