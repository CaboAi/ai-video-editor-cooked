# The Motion design style

The sample style `Motion design` (`presets/styles/sample-motion-design`, 16:9). It runs through style-edit's board
layout (`plan.py` `board_layout`, rendered by `remotion/src/Board.tsx`) and this skill. The rules here are on top of
`visual-system.md`.

## What a beat becomes

| beat | on screen |
|---|---|
| `logo`, `logo_cluster`, `flow` | dark tiles with the real logos on one board, each landing on its word, joined by dashed lines; the label is the word said |
| a question (a clause ending in "?" that starts with a question word) followed within 2.5 s by a tile | the heading of that board: centred first, then the camera pans down and it rises |
| a question with no board after | centred over the speaker, big, two lines at most |
| any other card (a clip, a capture, a template) | an overlay beside the speaker, sides alternating; the speaker is zoomed in and moved to the other side first |

A board's heading is the beat's `"heading"` (their words, in order), else the six words of the sentence that end on
the first tile. Tiles under 4 s apart share a board, four at most.

## The rules

- **One canvas, a moving camera.** Boards sit on one canvas; the camera drags from one to the next. Boards under
  1.5 s apart: no speaker between. From the speaker: a 0.6 s crossfade while the camera is already dragging in.
- **Never an empty board.** A board arrives on its heading's first word, at most 3 s before its first tile.
- **Few overlays, both sides.** An overlay that falls while a board is up is dropped (plan.py warns).
- **The speaker never sits still for long.** A slow zoom (1.06 to 1.24, top-anchored so the hair is never cut)
  at least every 5 s.
- **Sound:** a whoosh when the camera arrives (two a minute at most), a short rise into a thump on the pan up, a
  pop on a tile. The editor's own synthesised cues (`sfx.py`), never a third-party pack.

## A clip in this style

Pick sizes it to the overlay box (1200x964) in the `board` look: near-black ground, silver-white ink, dark
silver-rimmed tiles, real logos, lowercase labels in the speaker's words. It pops in beside the speaker.

## Vertical

There is no 9:16 variant. The side overlays and the speaker's left-right moves need a wide frame, and a board's row of
tiles and its one-line heading would shrink to half size across 1080 px. `plan.py` stops with an error on a vertical take.
