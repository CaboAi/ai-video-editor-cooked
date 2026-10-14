# The 50/50 style

The sample style `50/50` (`presets/styles/sample-50-50`). The visual owns the top of the frame, the speaker the
bottom, beat by beat, on a paper ground. It runs through style-edit's split layout and this skill: every beat that
needs motion is a clip built for the panel. The rules here are on top of `visual-system.md`.

## Two variants

| variant (style.json `layout.variant`) | the speaker is | the visual is |
|---|---|---|
| `panel` (default) | a window filling the lower half | the panel above the seam, plus full-face beats |
| `cutout` | a window whose top edge the head breaks out of | the top two thirds (`seam` about 64) |

The cutout needs the speaker cut out on every frame: `matte.py` cuts the whole take when the plan says
`"variant": "cutout"` (say its `--estimate` first). The renderer draws the cut-out frames in a window
`breakout` (10.6% of the frame) higher than the footage, registered to the same move, so the hair and shoulders
rise out of the card onto the paper. Keep what must be read out of the panel's bottom 200 px there.

## The panel is the clip

- `motion.py pick` sizes every clip to the panel's art box (x 13-87%, y 13.1% to 2% above the seam; 1200x1006 at
  seam 50) and gives it the panel's ground (`#FDF8EC`). It plays edge to edge with no card, radius or shadow
  (`video_card` `bare`), so the panel has no second edge inside it.
- **Ground:** the clip's corners are the panel's ground, within 10 levels. Any other ground draws a rectangle
  inside the panel (`check` gate `panel`).
- **Fill:** the ink band covers at least 55% of the panel (the text-safe area is about 58% of it, so this still
  fails a small drawing floating in the middle).
- **No container, no border, no window** round an exhibit, built or captured. The one exception is a platform's
  own UI when the platform is the point (a real post, a real watch page, a real job posting): that chrome is the
  evidence.
- **Reuse is the default failure.** A clip lifted from a wide video is laid out for a box that does not exist
  here. Rebuild it for the panel.

## The rules

- **The face comes back within 8 seconds** (`layout.face_every_s`). plan.py moves a card that would keep the panel
  open longer to 1.5 s after the run ends, so the panel closes and the face has the frame, or drops it when too
  little is left.
- **A number becomes a counter climbing to the figure said**, never a live count that argues with the words.
- **Real material first, and only as evidence.** A real posting, pay band, product page or the real page the line
  names; everything that explains is built. The test: swap the product for another. If the frame still works,
  it was explanatory and should be a clip.
- **Two posts per video at most.** On the ones kept, a marker sweep under the key line, never a ring or a box.
- **The hook earns the flashiest thing in the video**, as long as every value in it is real. The intro takes two
  or three exhibits of 1.4-2.5 s, at least two of them moving, not one held capture.
- **A tool's logo is its real colour logo**, never a black redraw.
- **Nothing appears twice.** A long hold gets a second exhibit, not a longer hold of the first.
- **The space above the speaker is never empty.** A beat with nothing to show is a full-face beat.
- **Sound:** one cue per framing change, nothing else; no riser on an emphasis word.

## The four proofs (`verify.mjs`, part of `motion.py check`)

Drives the clip's real timeline in Chrome and reads every element's real rectangle on every frame:

1. **Clipped:** a visible element crossing the stage edge while it is not moving (a slide moves more than
   3 px a frame and is exempt).
2. **Rails:** on a whole-frame clip, anything left of x 60 or right of x 940.
3. **Neighbour:** two `.beat` elements visible and still on the same frame (a push or a dissolve moves).
4. **Reading:** each element holding words gets 0.25 s to arrive plus 0.30 s a word of frames where it is fully
   opaque, fully inside and still.

A panel clip ships at 0 / 0 / 0 / 0; on a card (a 1-2 s beat in the card edit) the proofs are reported, not
failed. Then `hyperframes check --at-transitions`, which samples the moments in between.

## The paper ground and the AI-tell check

This editor bans a cream paper ground as a generated-look tell (`style-edit/references/ai-tells.md`). The 50/50
asks for it by name (style.json `look.allow: ["cream-paper-ground"]`, carried into the plan as `allow_tells`), so
the check reports it as a warning for this style only.
