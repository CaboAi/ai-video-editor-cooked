# The home page

The editor's window in the browser. A first run gets setup checks, optional keys, pick a creator, watch
their style get copied, see the style and the skill it became, drop a video. After that it opens on
Home (welcome back), New video (pick clips, then pick a style), Your videos, My style and Settings.
The page only queues jobs. Claude does every job with the usual skills and reports back, so the page
never decides anything itself.

The `nextwork-editor` command (`setup.py install-launcher`) starts Claude Code in the library with the
prompt "/ai-editor:start open the home page", or reopens the page when it is already served. Inside
Claude Code, `/ai-editor:start` and "open the home page" do the same.

`H="${CLAUDE_PLUGIN_ROOT}/lib/ai_editor/home.py"`. `python3` on Mac and Linux, `py` on Windows.

## Open it

Run both in the background (two Bash calls with `run_in_background`). The page always works in the
library (`library.py path`, `~/Movies/AI Editor` on a Mac), wherever Claude Code started, and every job
line names it as `root=`:

```bash
python3 "$H" serve          # same address every time (port 47821, one token per install); reuses a running server
python3 "$H" wait           # exits with the next job as one line
```

Say in one line: "The home page is open in your browser. Pick a creator there, or drop a video."
Then wait for `wait` to finish. Each time it prints a job, start a new `wait` in the background
straight away (so Cancel and the next job reach you), then do the job. `ALREADY` means a `wait` is
running: leave it.

## Questions

Ask with AskUserQuestion as usual. When the home page or a review page is open and showing, a hook
(`lib/ai_editor/askhook.py`) puts the question on that page: the options, an Other box, and "Answer in
Claude Code instead". The answer comes back as the tool's result, the same as a terminal answer. No page
showing, "Answer in Claude Code instead", or 10 minutes with no answer: the terminal box shows. Never ask
a question in plain chat text when AskUserQuestion fits: only the tool reaches the page. Permission
prompts (Allow or Deny for a command) stay in the terminal; a hook cannot answer them.

## Keys

The page saves keys itself through `keys.py` (prefix check, one free test request, then the key
file, readable by the user only). A key never reaches the chat, `state.json` or a job line. Never
ask for a key in the chat, and never read the key file. "Skip, add later" runs `setup.py later`.

## The jobs

Progress is automatic: once you have taken a job (your `wait` or your own `status`; a script never takes
a queued one), the long scripts (creator-teardown's, the cut's transcribe, join, build and render,
setup's install-all) post their own steps with a count ("video 3 of 7") onto the job their edit folder
or handle names, and the page shows a live strip with a clock, so it never looks stopped. You still
report the steps you own (the result, `--done`, `--failed`, steps no script covers) with `status`. The
same words as the newest step update that row, never a second one. A new `--step` closes the step before it; `--done` or
`--failed "<plain sentence>"` ends the job:

```bash
python3 "$H" status <teardown|skill|video|modal|setup|update> --step "..." [--detail "video 3 of 7"] [--progress 0.4]
python3 "$H" status <job> --result '<json>' --done
```

### `JOB teardown @handle platform=<p> take=<parts> root=<dir>`

The user pasted a creator and pressed "Copy this style". Run the creator-teardown skill's
**Quick mode** (its SKILL.md) on that handle and platform, in `root`. With `urls=<file>` instead of
a platform, use `list <handle> --urls <file>`. Do not ask the parts question: the page asks it.
No Gemini key: skip `gemini.py` (do not offer the key in the chat; the page has it).

The scripts report these steps themselves; use these words if you report one (the page lists the ones still to come):

1. `Listed their videos` (fetch.py list)
2. `Downloaded the top 5 and 2 median` (visual.py download --top 5)
3. `Transcribed every word` (fetch.py transcribe --top 5)
4. `Measured cuts, zooms and camera moves` (visual.py measure)
5. `Reading captions and faces` (look.py measure; `--detail "video N of 9"` as it goes; then format_check.py
   check, and when it leaves videos out its first line as `--detail`)
6. `Naming graphics` (graphics.py measure, gemini.py kinds)
7. `Sound effects and music` (sound.py measure)
8. `Writing the style` (report.py build)

The page reads every number itself from `creator-teardowns/<handle>/style.json` and `videos.json`,
so it fills in as each script writes. `report.py build` also prints the style's skill
(`skill edit-like-<handle> -> <path>`). Then read `look.md` and end the job with what not to copy,
one plain sentence per BAN-level AI tell and per move the teardown rejects:
`--result '{"reject": ["..."]}' --done`. Do not open `teardown.html`: the page links it.

A step that stops: say why with `--failed`, in the words of the skill's "If a script stops" table.

### `JOB skill @handle take=<parts> root=<dir>`

The user ticked what to copy and pressed "Build my skill".

1. `--step "Saving your style"`: `profile.py set 'creators=[{"handle":"<h>","take":[...]}]'`, the
   parts mapped `captions`->captions, `pace`->pace, `graphics`->visuals, `sound` kept for the
   sound answer (`sound.sfx=true`). Then creator-teardown's
   `editplan.py blend <h>-mine --from <h> --take <parts>`: it refreshes the style's skill
   `edit-like-<h>` on the ticked parts and prints its name.
2. `--step "Checking whether it is a format of its own"`: creator-teardown's
   `references/skill-handoff.md` gate, from the numbers in `look.md` and `videos.json`.
3. Pass: `--step "Writing the skill"`, build it as that file says, link it with
   `python3 "${CLAUDE_PLUGIN_ROOT}/lib/ai_editor/styleskill.py" write creator-teardowns/<h>-mine/style.json --format <name>`,
   then `--step "Testing it with one script"` (its "Finally"). Fail: no format skill; the style and its skill are saved.
4. `--result` then `--done`, the JSON the page shows:

```json
{"passed": true, "check": "5 of 5 top videos use this format, all above their median", "style_skill": "edit-like-handle",
 "name": "format-name", "what": "one sentence: what it edits and how, with measured numbers",
 "use_for": "...", "not_for": "...", "path": "~/.claude/skills/format-name",
 "prompts": [{"text": "edit my video like @handle", "what": "one line"}],
 "tools": [{"name": "Your photos folder", "why": "one line", "prompt": "use my photos folder for the cards"}],
 "tested": "Claude wrote one script with it: \"<title>\". 7 beats, 100 words."}
```

A fail sends `{"passed": false, "why": "<which gate question said no>", "style_skill": "edit-like-<h>", "prompts": [...], "tools": [...]}`.
Five prompts, each one the user can paste into Claude Code as it is. Every number in `what`,
`check` and `tested` comes from the teardown or the script; leave a field out rather than guess.

### `JOB video <path> as=<mine|@handle|defaults> aspect=<9:16|16:9> edit=<dir> root=<dir>`
### `JOB video takes=<dir>/takes.txt as=... aspect=... edit=<dir> root=<dir>`

The user picked clips and a style and pressed "Make my video" (or "Start the cut" on a first run).
`edit=` is the edit folder the page already made and listed in the library: use it, never make
another. `takes=` lists several clips, one path per line, in the order the user set: they are one
video, so join them first (`cut/references/takes.md`, `join.py <edit>/joined.mp4 <the paths in order>`,
no question: the page asked it). "Separate videos" arrives as one JOB per clip; take them one by one.

`as=mine`: keep the profile as it is. `as=defaults`: `profile.py set creators=[]`. `as=@handle`:
`profile.py set 'creators=[{"handle":"<h>","take":["captions","pace","visuals"]}]'`. When
`creator-teardowns/<h>/style.json` is not there yet, that creator is still being copied: cut now
(the cut needs no style) and start the styled pass once the teardown job is done.
`aspect`: `profile.py set aspect=<a>`.

The window turns into the review page as soon as it has a URL, so open it FIRST, on the raw clip
(after the join for `takes=`), before transcribing:

```bash
R="${CLAUDE_PLUGIN_ROOT}/lib/ai_editor/review.py"
python3 "$R" <edit> start --raw --video <the clip or joined.mp4> --stage cut   # round 0: "Your clip, before the cut"
python3 "$R" <edit> serve --no-open                                            # background; prints the URL
python3 "$H" status video --result '{"review_url": "<that URL>"}'
```

Round 0 has no Approve; notes left on it wait for the cut. Then the start skill from step 2 on, with
this file (or joined.mp4) as the video. The steps
`Joined your clips in order` (takes only), `Wrote down every word you said`, `Cutting out the mistakes`
reach the home page by themselves (join.py, transcribe.py, build_timeline.py, render.py); put each on the
review page with `python3 "$R" <edit> status "<step>"`. The cut's
own `start` (cut skill step 6) then opens round 1 on the same page, with the round 0 notes moved onto
the cut: run `wait` and carry on there. `status video --done` once round 1 is up.

### `JOB style-change <my-style|@handle> [style=<style.json>] text=<the user's words>`

The user typed what to change on a style's page (My styles, "Tell it what to change"). Turn the words
into corrections at that style's scope, one per thing they asked, with the taste skill's setting table
(`taste.py add <section> "<rule>" --set <key>=<value>`): `--style <style.json>` for a creator's style
(its `style.overrides.json` and its `edit-like-<slug>` skill), no `--style` for my-style (taste.json
and the `my-style` skill). A wish no setting covers is a word rule at the same scope. Do not ask the
scope question: the page named the style. Report what was saved, in plain words:
`status style-change --step "Saved: captions 6% tall" --result '{"changed": ["Captions 6% tall", "About 3 zooms a minute"]}' --done`.
The page shows `changed` and reloads the style's settings and its "Learned from your feedback" list.
The same change said in the chat goes through the same `taste.py add`, so both land in one skill.
The page's own controls (sliders, zooms, cut, sound effects) save through `styleskill.learn` directly
and need no job.

### `JOB modal`

The user pressed "Log in with your browser". Follow the setup skill's step 6 (Modal), reporting
each of its steps with `status modal --step "..."`, and `status modal --done` when it works.

### `JOB update`

The user pressed Update in Settings (the page saw a newer version on GitHub main than this plugin.json).
The update skill handles it when it is installed. Without it: for a marketplace install run
`claude plugin marketplace update nextwork` then `claude plugin update ai-editor@nextwork` from the shell,
each reported with `status update --step "..."`; if the `claude plugin` commands are missing, tell the user
to type `/plugin marketplace update nextwork` and `/plugin update ai-editor@nextwork`. Then
`status update --done` and ask them to type `/reload-plugins`. A git clone: `git pull` in the plugin folder.

### `JOB cancel teardown`

Stop the running teardown commands (TaskStop) and say so in one line.

## Setup from the page

A first run opens this page before anything is installed (setup skill step 0), then runs
`setup.py install-all --report` in the background. It reports each step itself
(`status setup --step "Installing the renderer" --progress 0.6`, the share of the whole bar) and ends
with `--done`, or `--failed "<the fixes>"`. Each step also refreshes the page's tool rows. Install
anything else (ffmpeg, Node) the same way: `status setup --step "..."`, then run `install-all --report`
again. A `JOB video` waits for `Ready.`; the page says so too.
