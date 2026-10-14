# Scenes: one clip, several ideas, one camera

The problem this solves: **ideas feel disconnected**
and each one is **too static**. Eight clips read as eight unrelated pictures, and
inside each one, everything arrives at 0.8s and then floats for four seconds.

The fix is not more clips. It is a wider canvas and a camera that never stops.

```
the canvas                       a SHOT is {cx, cy, z, at}
┌───────────────────────────┐
│  ┌────┐        ┌────┐     │    z = 1    one idea fills the frame
│  │ 1  │───────▶│ 2  │     │    z > 1    pushed in on a detail
│  └────┘        └──┬─┘     │    z < 1    pulled back, several at once
│        ┌────┐     │       │
│        │ 3  │◀────┘       │    the scene is fixed, the camera moves
│        └────┘             │
└───────────────────────────┘
```

## The five decisions

| | decision |
|---|---|
| what moves | the **camera**, over a wide fixed scene. Not elements sliding out of a fixed stage. |
| axes | **x, y and zoom.** Ideas sit anywhere on the canvas, not in a row. Push in on a detail, pull back to reveal that separate things were always in one place. |
| where it lives | **baked into the clip.** Root stays the canvas size (1200x630 card, 1080x1920 frame), and so does the render. Nothing downstream changes. |
| does it stop | **never.** Crawl while on an idea, faster between them, never zero. |
| what is ahead | **nothing.** Each idea builds in as the camera arrives. |

Scene length is per video: group beats into a scene when they are one argument,
leave a beat as a single-idea clip when it stands alone. Both shapes coexist.

## Structure

Four nested nodes, because two transforms on one element fight and one silently
wins: the same reason `.node` and `.hov` are separate.

```html
<div id="root" data-width="1080" data-height="1920" data-duration="15.0">
  <div id="viewport">                <!-- the ZOOM, about the frame centre -->
    <div id="rail">                  <!-- the camera POSITION, x and y -->
      <div id="scene">               <!-- the constant crawl -->
        <div class="idea" id="i1"> … </div>
        <div class="idea" id="i2"> … </div>
        <div class="idea" id="i3"> … </div>
      </div>
    </div>
  </div>
</div>
```

`root` keeps `data-width="1080"`. The linter's five required attrs describe the
**viewport**, not the canvas. `#scene` is the wide one, and it is sized in CSS.

## The camera

A camera position is `{cx, cy, z, at}`: put canvas point `(cx, cy)` in the middle
of the frame at zoom `z`, arriving at `at`.

The maths that makes zoom and position separable: `#viewport` scales about the
frame centre `C`, `#rail` translates inside it, so a canvas point `p` lands at
`C + z*((p + t) - C)`. Setting that to `C` gives `t = C - p`, **independent of
z**. That is why they are two nodes: the position never has to be recomputed when
the zoom changes.

```js
function railX(c) { return 540 - c.cx; }
function railY(c) { return 960 - c.cy; }
```

Then two layers of motion that compose instead of one doing everything:

```js
var SHOTS = [
  { cx: 540,  cy: 960,  z: 1,    at: 0.0  },
  { cx: 1634, cy: 960,  z: 1,    at: 4.4  },
  { cx: 1087, cy: 1470, z: 0.55, at: 8.8  },   // pull back, both visible
];
var TRAVEL = 1.2;   // seconds per move. SHORT: the dwell takes the rest.
var CRAWL  = 18;    // px/s, runs the whole clip, never stops

// crawl: ONE linear tween across the whole duration. This is what keeps velocity
// off zero while the camera is parked.
tl.to("#scene", { x: -CRAWL * DURATION, duration: DURATION, ease: "none" }, 0);

// Each move is its own sine.inOut, so every axis starts and ends a move at ZERO
// velocity. A direction reversal between two moves therefore happens at zero
// velocity, which is exactly what build-loop's smoothness rule demands of a
// diagonal path.
SHOTS.forEach(function (c, i) {
  if (i === 0) return;
  var from = Math.max(SHOTS[i - 1].at + 0.4, c.at - TRAVEL);
  var dur  = c.at - from;
  tl.to("#rail",     { x: railX(c), y: railY(c), duration: dur, ease: "sine.inOut" }, from);
  tl.to("#viewport", { scale: c.z,               duration: dur, ease: "sine.inOut" }, from);
});
```

Velocity never touches zero, because the rail's stops happen under a crawl that
never stops. That is the whole trick, and it is why they are two nodes.

**Travel is the SHORT part.** The dwell takes the remainder, however long it is.
Measured on the camera smoke test, 3 ideas over 12s: a 3.0s travel against a 1.4s
dwell put **25 zero-motion frames and 2 blank seconds of empty paper** into a 12s
clip, because most of the clip was spent looking at the seam between two panels.
That is gotcha 10 at scene scale. Dropping travel to a fixed 1.2s took it to
**0 zero-motion frames and 1 blank frame out of 360**, with the same ideas,
same crawl and same layout. Keep travel at 1.0-1.4s and let the dwell absorb the
rest.

**The crawl walks content leftward**, so a shot's `cx` must account for how far
the crawl has travelled by the time the camera gets there:

```js
cx = panelCentreX - CRAWL * at        // MINUS. derived below.
```

The sign is easy to get backwards and the first scene caught it in review. `#scene`
translates to `-CRAWL*t`, so a node authored at canvas `A` sits at `A - CRAWL*t`;
after `railX = 540 - cx` and the scale about the frame centre it lands at
`540 + z*(A - CRAWL*t - cx)`. Centring means `A - CRAWL*t - cx = 0`, so
`cx = A - CRAWL*t`. The `+` form is wrong by `2*CRAWL*at`: over a 37.5s clip at
18px/s that is 1350px, more than a frame width.

Leave margin: the crawl keeps walking after the camera lands, which is the point.

**Zoom changes what a stroke is worth.** At `z = 0.55` a 6px container stroke
renders at 3.3px and starts dissolving into the paper, and a 240px mark reads at
132. Anything meant to be legible at a pulled-back shot needs authoring bigger,
or the shot needs to be closer.

## Inside one idea

What makes a clip static: elements arriving all at once, and then nothing
changing. Three requirements, all three:

1. **Sequential arrivals.** Three things landing across 4s, not three things
   landing at 0.8s. Stagger by 0.6-1.0s.
2. **Something changes state.** A thing turns, fills, breaks, gets crossed out, a
   verdict lands. An arrangement being presented is not a beat.
3. **The camera keeps creeping.** Free, from the crawl.

An idea that is only an arrangement with no state change is the beat that should
have been a shot of the real thing instead. Send it back to the gather step.

## Ideas build as the camera arrives, and the entrance runs DURING the travel

Each idea's elements are `opacity: 0` in CSS and entrance in about 0.8s **before**
the camera reaches them.

**"Nothing is drawn ahead of the camera" stops applying once the camera starts
moving toward it.** That is the correction the first real scene forced, and it
cost 5.7 seconds of empty paper in a 37.5s clip:

```
0.00 - 3.07s   93 frames   the clip cold-opened on nothing
12.43 - 13.73s 40 frames   the travel from idea 1 to idea 2
24.87 - 26.10s 38 frames   the travel from idea 2 to idea 3
```

Both travels landed on an empty panel. The rule as first written said the idea
builds "as the camera arrives", and an agent reasonably read that as *at* arrival.

The rule that actually works:

> An idea's entrance must **complete before** the camera arrives, so it **starts
> during the travel**. With `TRAVEL = 1.2` and a 0.8s entrance, idea `i` begins at
> about `LAND[i] - 1.4`, as the camera starts moving. The camera never arrives on
> an empty panel.

Ahead of a camera that is not yet moving toward it: still nothing.

**And idea 1 must be on screen at t=0.** A scene is overlaid onto a beat that
starts immediately, and a card never lands on a blank clip frame 0. Idea 1's primary object is
already present at t=0 or entrances inside ~0.4s. Note this is the one place where
gotcha 12's `opacity: 0` from-state must NOT be applied.

Two more things fall out of building ahead of the camera only during a travel:

- The entrance craft that already exists keeps working. An entrance played
  offscreen is wasted.
- **`check --at-transitions` does not flag the offscreen ideas**, because they are
  not painted yet. Verified on a 3-idea smoke test: 0 layout issues across 48
  samples, 0 errors, 0 warnings. Pre-drawing the scene would break this as well as
  wasting every entrance.

## An idea can be a REAL SCREEN

> "There should be the real product scenes in here too: a mix of animations and real UI."

A scene is where a drawn mechanism and the real product finally sit in one
continuous shot instead of two cut-apart beats. The camera explains, then pans to
the thing that proves it.

Reach for this whenever a line **cites** something. "all eight of those official
use cases stop at a review list" points at a page that exists. Draw the mechanism,
then show the page saying it.

**How it sits on the canvas.** The captured screen goes in an `<img>` inside the
same ink card as everything else: `rx: 28`, 6px ink stroke, the same drop-shadow
, so it reads as part of the composition rather than pasted on top. It floats and
drifts like any other node. Give it the two-node structure: entrance on `.node`,
drift on `.hov`.

**This is the one exception to ZERO TEXT, and it is not really one.** The words in
a screenshot are the mechanism: they are the evidence, the whole reason it is on
screen. A drawn label naming a shape is still banned. The test is unchanged: if
you wrote the words, they do not belong; if the product wrote them and you are
showing that it did, they do.

**It has to be legible.** A crop of 620-820px wide, placed at ~700px on a
1080-wide frame, holds a few lines of real body text. A whole page or a browser
window shrunk to fit is unreadable and worse than nothing. Crop to the sentence
that carries the point, and check the zoom: a screen only shown at `z < 1` is
decoration.

**Fetch it, never redraw it.** Gathered stills come from step 1a and land in
the edit's `images/`. Copy the one you use into the clip's own `assets/`.
A redrawn mock of a real product is the failure this rule exists to prevent.

**Check what the screen actually says before building around it.** On the first
scene the VO said a claim was "xai's line, not mine" and the vendor's docs never
used those words: they stated the same fact in different language. The crop is
the authority; the script bends to it, not the other way round.

## Internal cut points

One framing per clip, because resizing the panel mid-clip
cuts the animation off. A 15s scene cannot hold one framing without going flat.

The clip therefore **declares where a framing change is safe** and the overlay may
only switch on those. Safe means: camera mid-dwell (slowest), nothing mid-entrance,
no state change in flight.

Emit `cuts.json` in the clip folder:

```json
{"clip": "3", "duration": 15.0, "cuts": [5.0, 10.2]}
```

A later beat can resume the clip where it left off with the video card's `start_s`.

Anywhere not in `cuts` is a hold. That is deliberate.

## Scene joins

The speaker's face between every scene. Scene, cut to the face, next scene. The face is the
punctuation and it covers the canvas reset, which is the same reason the existing
overlay puts a `close` between clips.

Scenes do **not** chain camera positions across a cut. Each scene starts at x=0.

## What this changes downstream

| | |
|---|---|
| `measure_ink.py` | **nothing.** The render keeps its canvas size. |
| `beats.json` | gains rows: one per internal cut point, same clip, `from` set. |
| Remotion compositor | nothing. It receives a normal clip. |
| smoothness check | still works for dead frames, and the crawl raises the floor off zero. Measured on the smoke test at `scale=54:96`: min frame delta **0.0484**, max 3.760, no zero frames. A sub-pixel crawl (18px/s is 0.6px/frame) registers through antialiasing. |
| the SPIKE half of that check | **does not transfer.** During a travel the whole frame moves, so the delta series is dominated by the pan: the first real scene showed 36 "spikes above 8x median", all clustered inside its two camera moves. A spike during a travel is the travel. Judge snapping on the dwells only, or you will flatten the camera to make a number look better. |
| the blank-frame check | **this is the one that matters for a scene.** Count frames with almost no ink and report them as runs, not a total: the runs say whether the fault is the cold open or a travel. Target is near zero. |
| fan-out | one agent owns a whole scene. See `agent-fanout.md`. |

## Gotcha 13b: an element's life is its IDEA, not the clip

The drift recipe sizes a float period from the element's remaining visible life.
In a scene, an element that lives only during idea 2 has a life of ~4s, not the
clip's 15s. Sizing its period off `DURATION - enter` gives it a fraction of one
cycle and it travels a couple of pixels, which is gotcha 13 in a new costume.

Pass each idea's own window:

```js
var live = Math.max(0.8, Math.min(ideaExit, DURATION) - enter);
```

This is the single most likely thing to go wrong on the first scene.

## Verified so far

A 3-idea, 12s scene built from `_template` and rendered:

| | |
|---|---|
| `lint` | 0 errors, 0 warnings |
| `check --at-transitions --samples 24` | 0 layout issues across 48 samples |
| zero-motion frames | 0 |
| blank frames | 1 of 360 |
| frame delta at 54x96 | min 0.0484, max 3.760 |

What is still unproven: a real scene with real marks and real state changes, the
`cuts.json` handshake into `beats.json`, and whether 18px/s reads as a creep or as
a drift on a phone. That is what the pilot is for.
