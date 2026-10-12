# The look pass: fields, sources, fallback

Graphics kinds, entrances, layout, cuts, camera, sound, hook, winners and the page:
`teardown-page.md`.

Four scripts write the look. Code writes every number; a model writes only words.

| Script | Writes | How |
|---|---|---|
| `visual.py measure` | `pace`, `zoom`, `sheets/<id>.jpg` | frame differencing for cuts (catches locked-off jump cuts), a scale search for zoom punches and pushes |
| `look.py measure` | `captions` (numbers), `face`, `graphics`, `motion`, `look.md` | OCR + transcript match, OpenCV k-means in the caption boxes, YuNet faces, frame diffs at the source frame rate |
| `format_check.py check` | `format.json`, `video/left-out/` (report.py copies the name into `format`) | groups the videos by measured features and keeps the largest group (below) |
| `gemini.py look` / `merge` | `look`, `captions.font_match` | Gemini Flash-Lite on the video, or the fallback sheet pass, into one fixed schema |

## style.json, new fields

Old keys keep their meaning, so style-edit reads an old or new file the same way.

```json
"captions": {
  "present": true, "words_per_caption": 1, "lines": 1, "case": "lower",
  "y_pct": 72.4, "x_pct": 49.6, "size_pct": 4.5,
  "color": "#FFFFFF", "stroke": false, "stroke_color": null,
  "box": false, "box_color": null, "highlight_color": null,
  "weight": 600, "font_match": "TikTok Sans",
  "entrance": "pop", "ease_s": 0.133, "animation": "pop",
  "source": {"numbers": "ocr", "font": "gemini-3.1-flash-lite"}
},
"face": {"present_pct": 89, "x_pct": 51.9, "y_pct": 45.0, "height_pct": 15.3, "framing": "medium"},
"graphics": {"share_pct": 75, "per_min": 5.0, "hold_s": 5.67, "ease_in_s": 0.43,
             "palette": [{"hex": "#000000", "pct": 14}]},
"motion": {"cuts_per_min": 12.6, "ease_in_s": 0.43, "hold_s": 5.67, "caption_ease_s": 0.133,
           "zoom_punch_per_min": 6.0, "personality": "smooth"},
"look": {"source": "gemini-3.1-flash-lite", "videos": 5, "font_class": "grotesk",
         "closest_google_font": "TikTok Sans", "weight_class": "semibold", "caption_case": "lower",
         "caption_animation": "pop", "graphics_style": ["real screenshots"],
         "motion_personality": "smooth", "transitions": ["jump cut"],
         "broll_types": ["screen recording"], "not_generic": ["..."]},
"blend": {"weights": {"alice": 0.5, "bob": 0.5}, "owners": {"captions": "alice", "pace": "weighted", "visuals": "bob"}}
```

- `size_pct`: the font size (em) as % of the frame's long side, the unit the renderer
  uses. Measured from the x-height (cap height for all-caps), the band most of the
  ink sits in, divided by 0.53 (0.70). It does not move with ascenders or the OCR
  engine. The OCR box height is the fallback when the colours cannot be read.
- `y_pct`, `x_pct`: the caption block's centre, % of height / width.
- `color`, `stroke_color`, `box_color`, `highlight_color`: k-means (6 colours) over
  the caption boxes of up to 60 samples a video. Fill and stroke sit inside the OCR
  box and not a line-height away; the stroke wraps the fill and contrasts with it
  (the anti-aliased edge never counts); a box colour fills the padding only; a
  highlight is a saturated text colour that comes and goes and is not a blend.
- `weight`: glyph stem (2 x area / perimeter of the fill's contours) over the em:
  0.09 = 400, 0.15 = 700, 0.20 = 900.
- `entrance`: `pop`, `slide` or `none`, from the fill mask frame by frame at the
  source rate around 8 caption changes a video. `ease_s`: frames to settle.
- `word_effect`: what a word looks like after its caption is up and before it is said, read
  from every source frame in the caption box of up to 8 captions a video, each word timed from
  the transcript (`timing_err_s`: how far the change lands from the word; over 0.35 s is not a
  word style). `reveal` (not there yet), `karaoke` (dimmer or another colour), `highlight` (the
  rest colour, another while said, back after), `pill` (the same, with the colour filling a box
  behind the word), `none` (no change).
- `effect`, in style-edit's `Captions.tsx` names: `karaoke` with `inactive_opacity` (the unsaid
  ink over its ground as an opacity) and `highlight_color` (the said colour); `reveal` with
  `inactive_opacity: 0`; `pill` is `word_highlight` with the box colour as `highlight_color`;
  `highlight` keeps the entrance as the effect with the said colour as `highlight_color`;
  `none` keeps the entrance, and a highlight colour that does not follow the words becomes
  `emphasis_color` (the stressed words) with `highlight_color` set to the fill.
- `animation`: the old name, kept: the effect when it is one of `pop`, `none`, `slide`,
  `word_highlight`; with no word style measured, `word_highlight` when the highlight moves.
- `shadow`: light text with no stroke or box: `true` when the ring just outside the letters is
  darker than the ring a little further out, caption after caption.
- Top level, for style-edit: `transitions` (visual.py: `whip` -> `push`, `match` -> `match`,
  `mask` -> `wipe`, `dissolve` -> `fade`, kinds on 5% of cuts or more, most used first; absent
  when they only cut) and `layout` (graphics.py: `{"mode": "split", "seam"}` when graphics sit in
  the top zone 60%+ of their time, end above 55% of the height, keep off the face, and the face
  is in the lower half; absent otherwise), or (look.py, wins over split) `{"mode": "letterbox", "band_pct"}`
  when most videos are a 16:9 band between black bars (rows over half near-black above and below, the
  band 15%+ from each edge).
- `look.font_votes`, `look.font_confident`: how many videos Gemini gave the same closest font,
  and whether that counts as measured (3+ videos, 75% agree, most `font_confidence: high`).
  look.md and the AI-tell scan call the font a guess otherwise.
- `face.framing`: face height of frame >= 22% `close`, >= 12% `medium`, else `wide`.
- `graphics.share_pct`: samples showing screen text that is not a caption and not set
  dressing (text on screen more than half the video), or no face in a video that
  mostly shows one. `per_min`: runs of those samples a minute. `hold_s`: median run.
- `motion.personality`: `smooth` if graphics take 0.3 s or more to land; `punchy`
  with 5+ zoom punches and 10+ cuts a minute; `snappy` with 15+ cuts a minute;
  `calm` under 8 cuts a minute with holds of 3 s or more.

Measured on a real creator (7 videos, checked against the frames): white fill
on all 7, no stroke, one word, lower case, centre 64-81% down (median 75%), em 4.7%
of the height (3.1% in one video, where the frames show smaller text), weight 600,
punchy. The old Claude-read style.json had `stroke: true`, `weight: 700`,
`y_pct: 68`; the frames show a soft shadow, no outline.

## The format check: one style is one format

People copy a creator for an editing style they like, and a creator often posts more than one: talking
heads most days, a podcast clip now and then. Measuring both and taking the medians gives a style that
looks like neither. So a style copies ONE format, and `format_check.py check <handle>` finds it after
`visual.py` and `look.py` measure, before anything else reads the numbers.

Each video gets five features, all already measured:

| Feature | Values |
|---|---|
| frame | `16:9` (landscape file), `letterbox` (a 16:9 band between black bars), `split` (the face below 62% of the height with graphics up 30%+ of the time), `full` |
| kind | `talking head` (a face 50%+ of the time), `two people` (the face sits left and right of centre, 20%+ of face time each: a podcast or skit cut), `screen recording` (a face 30% or less, graphics 50%+), `b-roll` |
| captions | none, or words per caption, height on screen, size, upper case or not |
| shot | median shot length |
| length | duration |

Two videos are one format when frame and kind are the same, both or neither have captions, and with
captions the words per caption are within 1 (or 2x), the heights within 15 points, the sizes within
1.5x, upper case on both or neither; shots within 2.5x (or both 4 s or more: a median of a few
shots is noise); lengths within 3x. Most-viewed first, each video
joins the first group whose first video it matches. The largest group is kept; on a tie, the one with
the higher median view multiple. The rest move to `video/left-out/`, so graphics, sound and the report
see only the kept format, and style.json and look.md are merged again from the kept videos.
`style.json` `format` names it, and the style's skill says so ("copying their talking-head videos with
word captions").

`format.json` (`consistent` is true when the kept group is more than half):

```json
{"consistent": true,
 "groups": [{"name": "talking-head videos with word captions", "videos": ["a", "b", "c", "d"], "views_x": 1.05},
            {"name": "two-person videos in 16:9 with sentence captions", "videos": ["p1", "p2"], "views_x": 3.0}],
 "kept": ["a", "b", "c", "d"],
 "left_out": [{"id": "p1", "url": "https://...", "why": "two-person videos in 16:9 with sentence captions (16:9 frame, not full; two people, not a talking head; ...)"}],
 "line": "Using 4 of 6: 2 look like a different format. The style copies their talking-head videos with word captions."}
```

What to tell the user: the `line`, once. When it says the videos do not share one format, the best video
is still used; ask for more videos like it. A profile run picks videos of the creator's usual length
(within 2x of the median listed length) and measures 2 spares, so the check can drop a mismatch and keep
the sample; pasted links are all measured. Ceilings: there is no speaker count (the transcripts are not
diarized), so two people is read from where the face sits; two formats that differ only in graphics
style or sound are one format here.

## Fallback: no Gemini key

One Agent call, `model: "haiku"`, so the images never enter the main conversation:

> For each video id in `<ids>`, read `creator-teardowns/<handle>/sheets/<id>.jpg`
> (9 frames of one video, time top left). Fill the JSON schema printed by
> `python3 "$VPY" <skill>/scripts/gemini.py prompt`, judging only what the frames show. Write it
> to `creator-teardowns/<handle>/video/<id>.look-ai.json` with `"_source": "sheet"`.
> Reply with one line per video: id, font_class, closest_google_font.

Then `gemini.py merge <handle>`. One sheet is 720 x 1281 px, about 1,200 image tokens,
read by the small model.

## Token cost per teardown (7 videos)

| | Images the main model reads | Tokens in the main model |
|---|---|---|
| Before: 2-3 of 62 sheets a video (1200 x 1704, about 1,550 tokens each) + a full frame or two | 14-23 | 22,000-35,000 |
| After, Gemini key | 0 (look.md, 26 lines) | about 350 |
| After, no key | 0 (haiku reads 7 sheets of 720 x 1281, about 8,600 tokens there) | about 350 + the haiku reply |

Gemini side: about 5,600 input tokens a 60 s video at 1 fps and low resolution (measured:
39,565 for 7 videos), $0.0014 a video at $0.25 per 1M on the paid tier, $0 on the free tier.
