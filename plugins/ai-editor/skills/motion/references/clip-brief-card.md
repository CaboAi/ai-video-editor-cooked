# Clip brief card

Everything a clip-builder needs: what the gates check, the approved values, the beat-to-picture table.
The long references are for a failed gate or an unusual beat, read only then: `visual-system.md` (the full
rules, captured screens, composition), `build-loop.md` (the why behind each gotcha), `scenes.md` (several
ideas, one camera). Do not read the check scripts' source: this card is what they check.

## The job

One clip, one beat, one idea. It plays as a card about 800 px wide over the footage, in the band above the
speaker's head (or the whole frame when the brief says 1080x1920). Its job is to make the viewer understand
what the line says, landing each part on the word that names it.

1. **Real things first.** The real app, page, logo (brand colours), the speaker's own screenshots and
   frames from the brief's picture list. Draw only a mechanism no screen shows (a flow, a verdict, a count,
   a before and after). Never a stock picture, an icon tile, or words on a plain ground.
2. **On the word.** `WORDS` in index.html holds each word's time in clip seconds. A part lands 0.1 s before
   its word. The clip's first 0.25 s plays under the card's own pop-in: nothing important lands there.
3. **The bottom fifth may be covered.** The card tucks behind the speaker's hair; keep what must be read
   in its top four fifths.
4. **Full at every moment.** The card is full from frame one: edge to edge, no margin of ground round a
   small drawing. A clip that lands several pictures replaces rather than accumulates (one object at a
   time, each filling the card). Accumulate only when that IS the mechanism (a counter, a list).
5. **Always moving.** Nothing settles into a still: a slow push or drift through every hold.
6. **Finishes.** Every draw, count or scroll ends at least 0.25 s before the clip does; the final state holds.
7. **No authored text.** Text only where it IS the mechanism (a counter to the number said, a typed
   command, a comment, a note line read out, words inside a real capture), marked with a `real-text:`
   comment, from the speaker's words only. No labels, no captions, no pills, no eyebrow lines.

## What a beat becomes

| the beat was (template) | build it as |
|---|---|
| `likes` (a count of people, likes, views) | the platform's real mark; a counter from 0 to the number said, tabular figures, ticking on its word; comments sliding in and pushing older ones down |
| `progress` (something made, rendered, uploaded) | a real app window; the speaker's own frames lighting up as a bar passes them; the percent counting |
| `curve` (smooth, motion, easing) | an easing curve drawing itself, a dot riding it, the speaker's still moving on the same curve |
| `notes` (feedback, fixes, steps listed) | a dark notes card, each line landing on its word, a green tick drawing under a fixed one |
| `terminal` (a command, a tool run) | the command typing in, output landing under it |
| `chat` (a question to an AI, a reply) | messages popping on their words, the real app's mark |
| `toasts` (alerts, sign-ups) | each sliding in from the top and stacking |
| `side_by_side` (before and after) | a then b, the arrow drawing from a to b on the turning word |
| `flow` (how work moves between real things) | real logos as nodes landing on their words, connectors drawing ON their edges |
| `logo_cluster` (brands named together) | the real marks landing one per name, no container |

Numbers only as the speaker said them. A counter counts up to the said number.

## Approved values

| what | value |
|---|---|
| canvas | as the brief says (1200x630 card, 1080x1920 frame), 30 fps |
| ground | the look's `ground` (white by default) or the real surface (an app's own window, a dark terminal). Never cream or ruled paper in a card |
| look | the brief's look block: ground, ink, one accent at most, font. Colour only from real marks, or where the colour IS the information (stop red, fixed green) |
| shadow | `drop-shadow(0 14px 18px rgba(60,45,20,0.16))` on the outer `.node`; drift on the inner `.hov` |
| strokes | containers 6 px, connectors 3 px, one texture weight under 3 px (scale to the canvas height / 1920) |
| size | the subject at least 1/9 of the canvas height x 1.2 (card: about 130 px, logo subject about 160 px) |
| type | the look's font, bold, sentence case, at least 45 px on a 630-tall card (30 px on screen) |
| entrance | 0.5-0.9 s, `power3.out` or `power2.out`; idle `sine.inOut`; travel `power2.inOut`; draws `sine.inOut` |
| banned | `back`, `elastic`, `expo` eases, bounce, shake, 3D flips, neon, glow, gradients, motion blur, emoji |
| float | 16-37 px on a 1920 frame (the template scales it); swing a subset, never a grid member |
| captures | crop to the thing; upscale 1.5x at most; body text 24 px+ on the card canvas; no nav, sidebars, banners |

## Gates, in loop order (`motion.py check <dir>`)

| gate | fails when |
|---|---|
| beat clock | `beat.json` lacks `"clock": "cut"` |
| root attrs | any of the 5 `data-*` attrs missing, or `data-duration` != `DURATION` |
| timeline key | `__timelines["<id>"]` does not match `data-composition-id` |
| delay / repeat | a function `delay:`; `repeat: -1` |
| drift | `i * 0.21`, `repeat: 5/15`, `Math.ceil(live` |
| dasharray | symmetric dash >= 60 on a draw-on path (gap = 2x dash) |
| origin / ease | any `transform-box`; `back`, `elastic`, `expo` |
| text | `<text>`/`<tspan>` without a `real-text:` comment |
| lint | any `hyperframes lint` error |
| layout | any `hyperframes check --at-transitions --samples 24` error |
| stall | 3+ frames in a row with delta < 0.02 (lead-in and last 4 frames exempt) |
| spike | delta > 4x local median AND > 3x global (warn only with `<img>`/`<video>`) |
| frame-0 flash | frame 0 ink > 15% of mid-clip ink |
| ground | over half the frame at 3/4 of the clip is a cream paper colour (a banned tell in this editor), unless the look's ground is one |
| composition | advisory: dead band > 0.25, dead strip > 0.30, coverage < 6%, < 3 stroke weights |

Warn only: `fromTo` count > `immediateRender` count, `fromTo` with no CSS from-state, any `.set()`, numeric
`delay:`, entrance `fromTo` under 0.5 s, font plumbing with no `real-text:`.

## The five gotchas that cost the most renders

1. A late `fromTo` needs `immediateRender: false` AND its from-state in CSS (`.enter { opacity: 0 }`).
2. Offset with the position parameter, never `delay`. Animate transforms, never `top`/`left`.
3. Drift periods come from the element's visible life (the template does it); give two axes unrelated periods.
4. Draw a path with its real length (`getTotalLength()`), dasharray `L 2L`, never `pathLength="1"`.
5. A `fromTo` that scales an SVG child puts `svgOrigin` in BOTH vars; with a `y` travel too, wrap it instead.

## Look before you report

After each render, Read `renders/sheet.png` (1 fps) and `renders/strip.png` (10 fps, first 1.2 s). Ask:
does each panel show what its line says? Is anything tiny, empty, cut off, flickering (a part on one frame
and gone), or still? A real mark you cannot name stays. A beat that reads wrong is redesigned, not nudged.
