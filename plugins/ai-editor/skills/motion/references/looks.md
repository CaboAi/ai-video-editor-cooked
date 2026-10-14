# Looks

A clip's look is five tokens. `motion.py pick` writes the resolved look into every brief and the
template, so all clips in one video share it.

## default

The house look, neutral: ink on the ground, colour only from real marks.

| token | value | use |
|---|---|---|
| ground | `#FFFFFF` | a card's surface (every card in the edit is white) |
| paper | `#FDF8EC` | the ground of a whole-frame or panel clip, only where a style asks for it; never in a card (`check` fails a cream card ground) |
| ink | `#171614` | everything authored: strokes, type, dark notes cards |
| accent | none | one at most, only where the colour IS the information |
| stop / ok | `#FF3040` / `#3DDC84` | a strike or like badge / a fixed tick |
| font | TikTok Sans | only where text is the mechanism |
| radius | 28 px | cards and captured screens |
| strokes | 6 / 3 / 1.2 px | containers / connectors / texture, at 1920 tall |

## How a style changes it

In order, each overriding the last (`motion.py look_for`):

1. style.json `motion.look` (the style page's Motion look), then `look.preset`: `editorial`, `editorial-dark`, `mono`, `poster-red`, `poster-green`,
   `poster-blue` set ground, ink, accent and font as the renderer's presets do; `paper` (`#FDF8EC` on
   `#171614`) is the 50/50 style's, `board` (`#F4F6FA` on `#0B0B0D`) Motion design's. A split-layout style's panel ground always wins in the panel.
2. style.json `look.ink`, `look.accent`, `look.font`, `look.ground`.
3. The user's brand kit in the profile (`brand.ink`, `brand.accent`, `brand.font`).

Only palette and type change. The motion rules, sizes and gates are the same for every look.
