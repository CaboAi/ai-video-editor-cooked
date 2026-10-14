# What a teardown writes

Everything lands under the folder Claude Code was started in (the editor's launcher and home page start it in the library, `~/Movies/AI Editor` on a Mac). Claude reads `look.md` and
`transcripts/<id>.txt`; the JSON files, frames and sheets are for the scripts.

```
creator-teardowns/<handle>/
  videos.json                 stats for every video pulled
  transcripts/<id>.json       verbatim, word-level timings
  transcripts/<id>.txt        flat text + role/views/duration/wpm header
  transcripts/<id>.timed.txt  a line a sentence or pause, "[m:ss.s] words" (beat maps, video-analyst)
  metrics.json                measured pace, pauses, fillers per video
  video/<id>.mp4              the picked videos (visual pass)
  video/<id>.visual.json      cut times, shot lengths, zoom events per video
  video/<id>.look.json        OCR lines, faces and graphics per sample (look.py)
  video/<id>.look-ai.json     the look pass's words per video (Gemini or fallback)
  video/<id>.graphics.json    every graphic: box, kind, entrance, exit, hold, motion
  video/<id>.sound.json       sound effects and music
  video/left-out/             every file of the videos the format check left out
  format.json                 the format check: groups, kept, left out and why (format_check.py)
  graphics/                   one crop per graphic + heatmap.png
  sheets/<id>.jpg             one 3 x 3 sheet per video, read only in the fallback
  style.json                  every measured number, for editing in this style
  look.md                     the look in under 48 lines: what Claude reads
  teardown.html               THE PAGE: what the user looks at (report.py)
  report.json                 hook, winners against control, AI tells
  teardown.md                 THE ANALYSIS
  events.json                 card events, when the card pass ran
edit-plans/<name>/
  plan.md, plan.srt           the user's video, planned in the creator's style
```

## Match check

`scripts/match.py <teardown dir> <render.mp4>` runs this skill's own measures (visual.py, look.py, graphics.py,
sound.py) on a rendered edit, as a one-video teardown in a temp folder, and prints each feature with the
creator's value (median or majority of the kept videos), ours, and pass or FAIL against a tolerance: cut rate,
longest hold, zooms a minute and their size, captions (words a line, font, weight, case, colour, box, height,
side of the face, highlight), card time and main kind, b-roll (speaker off screen), a title at the open, the
safe zone, pace, grade (mean luma and saturation), music and sound effects. Exit 1 when any feature fails.
The font and, on a cut with no pauses, the music bed come from the render's plan.json (marked "(plan)").
