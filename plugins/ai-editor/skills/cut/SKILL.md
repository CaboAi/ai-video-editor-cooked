---
name: cut
description: Cuts raw talking-head footage into a clean jump cut. Takes one take or several joined (mp4 or mov, any aspect, 4K phone footage is fine), transcribes it word by word, removes retakes, false starts, fillers and dead pauses, and renders cut.mp4 at the source resolution plus words.json timed to the cut. The user approves the cut on a page with the whole transcript and every removed word struck through. Use when the user says "cut my video", "just cut it", "remove my mistakes", "cut out the retakes", "remove the pauses", "clean up this take", "jump cut this", "tighten this clip", or asks only for the cut of a raw take; start calls it for a full edit. Runs before style-edit. Not for a general request to edit a video or to make it look like a creator (that is start), not for adding captions, zooms or cards (style-edit), and not for analysing someone else's videos (creator-teardown).
license: MIT
compatibility: Python 3.9+, ffmpeg and the venv the setup skill installs (faster-whisper). Optional ElevenLabs key or CrisperWhisper. Mac, Windows or Linux. Runs from the full ai-editor plugin folder (uses its lib/).
---

# Cut

Paths: `${CLAUDE_SKILL_DIR}` means the folder containing this SKILL.md, and `${CLAUDE_PLUGIN_ROOT}` the plugin folder two levels above it.

Read `${CLAUDE_PLUGIN_ROOT}/PRINCIPLES.md` first: the rules every video in this plugin follows.

Every question goes in the question box: the AskUserQuestion tool (2-4 options, recommended first). Only in an agent without it, ask numbered questions in text.

```
edits/<name>/  transcript.txt (read this, never words.raw.json), spans.json (what to remove, quoted),
  candidates.md (what code flagged), paper-edit.md (the cut as text), cut-check.html (the
  transcript), review.json (notes), cut.mp4 + words.json (the render and its words).
```

`<name>` is a slug of the file name (`IMG_1234.MOV` -> `img-1234`), in the library
(PRINCIPLES.md "Where"). Never modify, move or copy the source file: pass its path.

`PY="${CLAUDE_PLUGIN_ROOT}/lib/ai_editor/run.py"`; `python3 "$PY"` runs the tool venv. On Windows, use
`py` wherever these commands say `python3`.
No venv yet: run the `setup` skill first. `S="${CLAUDE_SKILL_DIR}/scripts"`.

## No video yet?

Ask for their take's path. Several files: `references/takes.md`. None: offer the sample take (raw,
vertical, retakes, false starts and pauses kept) and download it to their folder:

```bash
curl -L -o sample-take.mp4 https://github.com/nextwork-projects/ai-video-editor-cooked/releases/download/sample-video/sample-take.mp4
```

## 0. Ask: the cut first, or everything at once

Before anything else, ask one question in the question box (start decides this for a whole edit):

> Do you want just the cut first, or the cut and the styled edit in one go?
> - **Just the cut first (Recommended).** You approve the cut before I add captions, zooms or
>   visuals, so a changed cut never rebuilds the visuals (fewer tokens).
> - **Everything at once.** I cut, then style without waiting. You still see the cut page and the
>   stills sheet before anything renders.

Skip it only when the user already answered ("just cut it", "cut and style it in one go") or start or
clips called this skill. **Just the cut first:** steps 1-6, then stop until the user approves the cut
**and** asks for the style. **Everything at once:** steps 1-5, open the cut page, go straight on to
style-edit; a later cut change re-runs style-edit from its plan step.

## 0b. Read the user's taste

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/taste/scripts/taste.py" show --edit edits/<name>
```

Follow every rule in it. Save a reaction to a result ("too slow") with the taste skill before redoing.

## 1. Transcribe

```bash
python3 "$PY" "$S/transcribe.py" <source> edits/<name>/words.raw.json [--script <their script>]
```

A script (or `--keyterm Name`, repeatable) gets names and jargon right (+20% on Scribe).

Say the time first: Whisper 1-4 min for a 3-min take, CrisperWhisper about the take's length. It picks
Scribe when a key is saved, else CrisperWhisper if installed, else the free local Whisper (`--engine whisper|crisper|scribe` overrides), which drops
some ums and false starts. With neither, say once that
setup can add one (CrisperWhisper's weights are non-commercial only). A take full of retakes:
`AI_EDITOR_WHISPER_MODEL=medium` hears them better (1.5 GB, twice as slow). Step 5 catches the rest.

**Never read `words.raw.json`**: step 2 writes `transcript.txt`, 1/12 the size.

## 2. Decide what to cut

```bash
python3 "$S/retakes.py" propose edits/<name>
```

Code finds every possible cut (a repeated line, an unfinished sentence, "wait, sorry", fillers)
into `candidates.md`. Each is a place to look, not a decision. Read `transcript.txt` and
`candidates.md`, follow `references/retake-detection.md`, and write `spans.json`
(`references/shapes.md`).

With a `script.md`: `python3 "$S/script_match.py" edits/<name>`, read `script-match.md` (`references/script.md`).

Last take wins, except alternate hooks. Never type a timestamp or span a
pause.

Only if the user wants it shorter, also read `references/editorial-rules.md` and add `redundant`
cuts (content calls).

A misheard name is one command, never a JSON rewrite: `python3 "$S/retakes.py" fix edits/<name>
cloud=Claude jiv=Jev` (whole words, kept in `fixes.json` for the captions).

## 3. Build

```bash
python3 "$S/build_timeline.py" <source> edits/<name> --dry-run
python3 "$S/build_timeline.py" <source> edits/<name>
```

Every pause (breaths too) over `--max-pause` (0.15 s) becomes 0.10 s; under music only word gaps
are trimmed. Fix any dry-run `ERROR`, then run it for real.

Then the `cut-judge` agent scores `paper-edit.md` (what plays, `<<n>>` at each join)
and fixes `spans.json` (a flub left in, a bad join, a good line lost). Launch, readers for takes over 5 min, and the approval score: `references/judge.md`.
- `CLIPPED` in `paper-edit.md`: a pause cut ate a word. Raise `--pad`, rebuild. Still there: a label
  is off; step 5 says if it plays.
- Joins inside speech move to a quiet frame. Any `still in speech`: listen in step 5.

Rebuild if spans changed: paper is free, renders are not.

## 4. Render

```bash
python3 "$S/render.py" <source> edits/<name>
```

4K takes minutes: run it in the background.

## 5. Verify

```bash
python3 "$PY" "$S/verify_cut.py" edits/<name> --engine <same engine as step 1>
```

Diffs cut.mp4's transcript against the words meant to be kept.
- **MISSING**: a kept word is not in the render. Raise `--pad` (or narrow the span), rebuild, re-render.
- **SURVIVED**: a span to add for a real repeat or stumble, never one that cuts a kept line.
- **HEARD DIFFERENTLY** ("jev -> jeff") and filler notes are transcriber variance: no rebuild.
- **DOUBLED**: words said twice in a row. Natural ("very, very"): keep. A stumble: add its span.
- **N join(s) in speech**: listen. Clipped: `splice_check.py edits/<name> --fix`, re-render.

One fix cycle, then show the user what is left.

## 6. The user approves

After every render, hand it over on the review page (`../style-edit/references/review.md`):

```bash
python3 "$S/preview_cut.py" edits/<name> --no-open
R="${CLAUDE_PLUGIN_ROOT}/lib/ai_editor/review.py"
python3 "$R" edits/<name> start --video edits/<name>/cut.mp4 --stage cut --transcript edits/<name>/words.json
```

Then `serve` and `wait` in the background. Say the seconds cut. `wait` exits with the notes. "keep <line>": narrow or delete that span; "cut <line>": add one; pauses
off: `--max-pause`. Then build, render, verify, `preview_cut.py`, `resolve`, `round`.

After approval: `python3 "$S/trend.py" log edits/<name>` (`tally`: trend), hook check, then hook variants if `hooks.json` exists (`references/retake-detection.md`).

Only approval finishes the cut; `cut.mp4` and `words.json` go to style-edit. On "just the
cut first", say they can ask for the style. Never start style-edit on your own. Called by start: as
the page opens, start the `visual-prep` agent in the background.

## If a script stops

| message | do |
|---|---|
| `no such file` / `ffmpeg could not read audio` | ask for the right path; never copy or convert the source |
| `faster-whisper missing` / `CrisperWhisper missing` | run with `python3 "$PY"`, or the setup skill's step 3 |
| `no ElevenLabs key` (exit 2) / `HTTP 4xx`, `unreachable` (3) | drop `--engine scribe`, or re-save the key with the setup skill |
| build `ERROR` on a quote | fix that span's quote or add `occurrence`, `--dry-run` again |
| `cannot derive a silence threshold` / `marked no silence` | speech and background are under 10 dB apart: `ffmpeg -i <source> -af volumedetect -vn -f null -`, pass `--noise` about 10 dB above the mean |
| render `ERROR ...` | say it; re-run render once |

## Rules

1. Every cut quotes the transcript. No quote, no cut.
2. A script breaks ties between takes and keeps every take of a line it says twice. Nothing is cut for
   being off-script.
3. Never stream-copy (`-c copy`) across cuts: it lands up to 2 s off.
4. Never hand over a render that has not been verified and approved.

## Self-check

`test_build_timeline.py`, `test_retakes.py`, `test_media.py` (ffmpeg); `demo` on `transcribe.py`, `trend.py`, `splice_check.py`, `script_match.py`.
