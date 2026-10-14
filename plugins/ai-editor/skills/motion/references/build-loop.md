# The build loop and the gotchas

Every gotcha here cost at least one render. They are ordered by how often they
bite, not by severity.

## The loop

```bash
python3 "<skill>/scripts/motion.py" check edits/<name>/motion/<id>
# runs, in order: check_clip.py (costs nothing), hyperframes lint,
# hyperframes check --at-transitions --samples 24, hyperframes render,
# check_motion.py, check_composition.py (advisory), measure_ink.py,
# then writes renders/sheet.png (1 fps) and renders/strip.png (10 fps). LOOK at both.
```

**Half the gotchas below are now gated.** The ones marked GATED are checked by
`check_clip.py` (source) or `check_motion.py` (render) and you do not have to
carry them in your head. They are still written out because a gate says *what*
broke and the prose says *why*, and because fixing one usually means redesigning
the beat rather than nudging a number.

`check_motion.py` pulls frame 0 itself. `fps=1/1` does **not** emit t=0: its
first panel is roughly t=0.6, so the contact sheet alone never settles gotcha 12.

The CLI is pinned (`motion.py` HF_VERSION, installed by setup). 0.7.x used other flags.

**`lint` and `check` are not the same tool.** `lint` validates the timeline ,
layout-prop animation, illegal repeats, missing font-face. `check` validates the
LAYOUT: occlusion, overlap, elements off the stage, and transient overlaps that
exist only mid-transition. A composition can lint clean and be visibly broken:
two shipped clips had 6 and 5 layout errors that nobody caught by eye.

**Neither sees a beat that is simply wrong.** Three things shipped past a clean
lint on one video and were caught only by looking at frames: a malware shape that
read as a cartoon explosion sticker, an opened box that read as an easel, and an
open padlock that read unmistakably as a handbag. If `check` reports an error,
fixing it usually means redesigning the beat, not nudging 10px.

---

## The gotchas

### 1. `check --at-transitions` is mandatory
Not optional, not "if it looks off". Layout failures are invisible in code.

### 2. Traffic passes BEHIND nodes, never over them
Give travelling elements a lower z-index than every node they pass. Better still:
don't move a dot along a path at all: **dimming the paths not taken teaches
better than animating the path that was**.

**Split the path from the traveller when the nodes are filled.** Once a container
gets a paper fill it occludes everything under it, and on one clip that hid the
cursor and dots for **74% of their journey**, including a dot's entire grow-in,
so it emerged from under a card edge already at full size and full speed, which
is exactly the snap the grow-in exists to prevent. Two SVGs on the same grid:
the **persistent route behind** the nodes (z-index 1), the **moving pointer or
dot in front** (z-index 5). The rule protects marks from being crossed by a
standing line; a pointer hidden behind the thing it points at reads as broken,
not as depth.

### 3. Never animate through occupied space
A tier flying 190px upward passed straight through another row and occluded it.
Dissolve near where it sits instead.

### 4. Two elements in the same slot SWAP, they do not crossfade
Overlapping marks are unreadable however brief. Fade one fully out before the
other starts. Best implementation: keep a persistent outline and un-draw the
first mark to zero length before the second lays down a pixel.

### 5. `fromTo` defaults to `immediateRender: true`   *(GATED: check_clip)*
The from-state paints at time 0, not at the tween's position, so an element you
intended to appear at t=8 sits visible from the first frame. Set
`immediateRender: false` on every `fromTo` positioned later than 0.

### 6. Never use a function for `delay`   *(GATED: check_clip)*
GSAP supports function-based values for most properties but **not** `delay`. The
tween silently fails to schedule and nothing moves. Measured 0.22px of travel on
a shipped clip. Offset with the **position parameter**, the last argument to
`tl.to`.

### 7. `repeat: -1` is rejected   *(GATED: check_clip)*
Use a finite repeat sized to the composition duration.

### 8. `autoAlpha: 1` clobbers a designed sub-1 opacity
It forced a 5%-opacity ring to solid black. Anything drawn faint animates with an
explicit `opacity` target.

### 9. Animate transforms, never `top`/`left`   *(GATED: lint)*
Layout props snap to integer pixels and stutter under the frame-by-frame capture
engine. The linter enforces this.

### 10. A clip's window must outlast its exit tween
Or the element vanishes mid-fade. And beats must **overlap**: one empty frame
between beats reads as a glitch. This one has bitten three times.

### 11. Verify motion by measuring, not by looking   *(GATED: check_motion --track)*
Sample the same crop at two timestamps and compare ink centroids. Assuming drift
works is how a 0.22px clip shipped.

**Downsample by AREA, never by decimation.** Subsampling frames with numpy
`[::20]` manufactures spikes that are not in the clip: at a 6px stroke a 1px
drift flips sampled pixels between paper and ink, and the delta series shows
single-frame jumps. One clip reported 9 spikes that all vanished under a correct
downscale. `ffmpeg -vf "scale=54:96"` averages and is safe: it is in the recipe
for this reason. **If a smoothness pass reports spikes, check the downsampler
before touching a single tween.**

### 12. A late `fromTo` must carry its from-state in CSS   *(GATED: check_motion)*
Gotcha 5 forces `immediateRender: false`, but that leaves the element painting its
**authored** state for every frame between the clip window opening and the tween
firing. Measured: a 3-frame full-size card flash at t=2.60, and 10 frames of six
fully-drawn grid slots on another clip.

Put `opacity: 0` (or the real from-state) in the stylesheet **and** keep
`immediateRender: false`. Both, not either. The linter cannot see this: only
sampling frames at each element's entrance time catches it.

**The contact sheet does not sample t=0.** `-vf "fps=1/1"` starts its first
output frame around t=0.6, so a clip can flash its authored state for the first
half-second and the sheet will never show it. Pull frame 0 explicitly with
`select=eq(n\,0)` and **count ink pixels at an ~80% threshold**, rather than
eyeballing a thumbnail. Sample frames 1, 2 and 3 as well: ink should be rising
from zero, not arriving fully formed. Measured on one clean clip:
0 / 96 / 135 / 186 px, a stroke mid-draw, which is what clean looks like.

### 12b. A contact sheet cannot be trusted for stroke WEIGHT

Tiling 1080px frames down to 250px thins a 6px stroke to 1.4px while solid-fill
logos stay just as dense. Correctly-weighted containers then look like hairlines
next to their own contents. Measured: cards authored at
6px were called a hairline off the sheet and were fine at full res; the sibling
clip whose slab genuinely WAS a hairline had authored 2.5px.

Judge weight on a full-resolution crop, never on the tile. Cheaper still, read
the number out of the file:

```bash
grep -oE 'stroke-width[:="]+[0-9.]+' index.html | sort | uniq -c
```

The sheet is for reading the BEAT: what happens, in what order, does it make
sense. Weight, alignment and spacing need `-vframes 1` at full size.

### 12c. A symmetric `stroke-dasharray` wraps and paints a dot at the far end   *(GATED: check_clip)*

`stroke-dasharray: LEN` with `LEN` equal to the path length implies a gap of
`LEN` too, and the browser wraps the pattern, so at full `strokeDashoffset`, a
`stroke-linecap: round` puts a visible dot on the path's **end** while the line
itself is still hidden. It shows on frame 1 as a stray speck with no explanation.

Give the gap twice the dash: `stroke-dasharray: LEN 2*LEN`.

### 12d. `tl.set()` is a `fromTo` in disguise   *(GATED: check_clip)*: it defaults to `immediateRender: true`

Same trap as gotcha 5, in a form the template does not warn about. A `tl.set()`
placed late in the timeline paints at time 0 anyway. If you reach for one to fix
a phantom on an early frame, you have most likely just created a real one.

### 13. The drift recipe must scale with clip duration   *(GATED: check_clip)*
The fixed-offset recipe (`i * 0.21`, `repeat: 15`) is sized for a 14s
composition. In a 4.6s clip it parks elements on the slow turnaround of their
sine and they travel **2px across their entire visible life**. Derive the period
from the element's remaining time, not from its index:

A yoyo tween runs for `duration * (1 + repeat)`. Solve for the period so the
motion lands **exactly** on the element's remaining life: `ceil(live / period)`
overshoots the clip window by ~25% (a 4.0s clip ran to 4.74s):

```js
var live   = Math.max(0.8, DURATION - enter);
var reps   = Math.max(1, Math.round(live / 1.5));
var period = live / (reps + 1);
var amp    = 16 + (i % 4) * 7;
tl.to(sel + " .hov", {
  y: -amp, duration: period, ease: "sine.inOut",
  yoyo: true, repeat: reps
}, enter);
```

The template (`template/index.html`) has this built in.

**Derive from the element's VISIBLE life, not `DURATION - enter`.** When an
element exits early, its remaining clip time is not its remaining life. On one
4.0s clip four elements left at ~1.9s, so a period sized to `DURATION - enter`
gave them a fraction of one cycle before they vanished. Use
`min(exit, DURATION) - enter`.

**Measure travel on a rigid landmark, not a centroid.** A centroid inside a crop
under-reads by roughly half, because ink entering and leaving the crop moves it
against the element's own motion. Track a fixed edge (an element's bottom
stroke) and compare it at a sine peak and a trough.

### 13d. Two DERIVED drift periods freeze a long hold   *(GATED: check_motion)*
Gotcha 13 says derive the period from the element's remaining life. Derive **both**
axes that way and they share a denominator, so the ratio is rational and their
turnarounds line up on a fixed cycle. Where both sines turn on the same frame the
whole picture stops, and in a settled hold there is nothing else moving to cover it.

Three clips hit this: one stalled 5 frames at 6.70 s, another froze across its 3.6 s hold.

**Derive one axis, let the other run free at an unrelated period.** Measured: two derived periods reached 0.245 s of best turnaround separation,
inside the 0.3 s where the first one stalled. A free-running x at 1.610 s against a
derived y at 1.471 s gives 0.44 s at worst and 0.48 s inside the hold, and the
hold's 108 frames then had zero frames under the 0.02 dead threshold.

The same fix stated the other way: give each element its own period, none a simple
multiple of another (one fixed stack: 1.256 / 1.506 / 1.606 / 1.205 s).

### 13e. An eased tween on a quiet background reads as a spike   *(GATED: check_motion)*
A paper tint washing in eased over 0.70 s peaked at 1.181 against a 0.289 local
median, 4.09x, and `check_motion` failed it. The peak landed exactly where the y
drift turned, which is where background delta is lowest, so the tween had the frame
to itself. Linear over 0.85 s spreads the same change across more frames: peak 0.741,
gate passes. **A big area changing opacity wants a linear, slightly longer tween;
save the ease for things that travel.**

### 14. The root div needs all five data attrs   *(GATED: check_clip)*
`data-composition-id`, `data-start`, `data-duration`, `data-width`, `data-height`.
The template shipped without them for a while and failed lint with 4 errors on an
untouched copy. `data-duration` must match the `DURATION` const.

### 15. Restoring a clip from git reintroduces every gotcha fixed since that commit   *(GATED: check_clip)*
An isometric restyle was rejected and eight clips were restored with
`git checkout HEAD -- .../index.html`. The restored files predate gotchas 12 and
13, and **every one of them came back carrying the bugs**: unguarded late
`fromTo`s (clip 6 had 1 `fromTo` and 0 `immediateRender`) and the old fixed drift
table (`repeat: 5`, `i * 0.21`). Three of them had already been re-rendered and
staged before anyone checked.

After any git restore of a clip, run the gate before trusting the render:

```bash
python3 <skill>/scripts/check_clip.py edits/<name>/motion/<id>/
```

That is what the hand-written greps here used to be. **They were a smell test,
not a verdict, because grep cannot tell code from a comment**: on one clip they
reported `1 fromTo / 0 immediateRender` and the `fromTo` was inside a comment
reading *"so there is no `fromTo` anywhere"*. `check_clip.py` blanks every
comment first, keeping line numbers, so its counts mean what they say.

The real gotcha-12 exposure is often invisible to that grep anyway: a clip whose
from-states live **only** in JS (`gsap.set`, a `dash()` helper) and not in the
stylesheet still paints its authored state for the frames before the timeline
runs. The check that actually settles it is **sampling frame 1** and confirming
it is bare ground.

### 16. A `pathLength="1"` draw-on snaps on in one frame
A draw-on normalised with `pathLength="1"` appeared whole in a single frame on two
clips (a folder outline fully drawn at 0.67s). GSAP rounds px
values, so a dashoffset tweened from 1 to 0 has two states and jumps at the
midpoint. Each clip cost a re-render.

Dash with the path's real length. Put a hide-everything from-state in the markup
(`stroke-dasharray="4000 8000" stroke-dashoffset="4000"`) so nothing paints
before the script runs, then:

```js
document.querySelectorAll(".draw").forEach(function (el) {
  var L = Math.ceil(el.getTotalLength()) + 2;
  el.setAttribute("stroke-dasharray", L + " " + 2 * L);   // gap 2x, gotcha 12c
  el.setAttribute("stroke-dashoffset", L);
});
```

`pathLength="1000"` also works.

### 17. A `fromTo` that scales an SVG child needs `svgOrigin` in BOTH vars
With `svgOrigin` given only in the `to` vars,
GSAP's smoothOrigin left a residual translate and the Markdown mark landed 51px
left and 30px high of its slot. One re-render.

```js
tl.fromTo("#md", { scale: 0.4, opacity: 0, svgOrigin: "110 226" },
                 { scale: 1, opacity: 1, svgOrigin: "110 226", duration: 0.7,
                   ease: SLIDE, immediateRender: false }, 26.14);
```

### 17b. `svgOrigin` plus BOTH `y` and `scale` parks the object off-frame   *(GATED: check --at-transitions)*
Gotcha 17 fixes a `fromTo` that only scales. Add a `y` travel to the same tween
and putting `svgOrigin` in both vars is not enough: on one clip a
file rode its download arrow straight out of the frame, because the
residual translate compounds with the `y`. Wrap it instead. A static
`translate()` parent, the children drawn centred on `0,0`, and **no `svgOrigin`
anywhere on the tween**.

`check --at-transitions` is what catches it, as a `container_overflow`. A contact
sheet does not, because the object is simply absent and an absent object reads as
"has not arrived yet".

### 17c. Fine repeated strokes merge at the composite scale
Eleven waveform bars at 20px pitch are separate bars at 1920 and a solid black
blob at the 0.72 the compositor shows the card at. Eight at 28px reads. The rule
generalises: **a repeated stroke needs about 28px of pitch at 1920**, and the
check is to look at the render scaled to 0.72, not at full size.

Same arithmetic for the transcript-row motif: the spec says eight rows 26px
apart, and 26px of *pitch* under a 24px pill is one stripe. It is 26px of **gap**,
so 50px of pitch.

### 18. A beat that carries the meaning must HOLD for 1.5s
The blind read (`agent-fanout.md`) looks at one frame per second, so it misses
any motion shorter than a second. On one video a 0.3s "bounce out" fell
between samples and the reader took the sequence to mean the opposite. No gate
catches this; it cost a redesign and a re-render.

Make the meaning a held state of at least 1.5s, so a still shows it. Motion can
lead into it; it cannot be it. One good clip holds its failure state
(bubble crammed, outline dashed, red cross) from 2.45s to 4.00s before anything
spills out.

---

## Smoothness: what "jumpy" actually is

The note on a full video: "the animation slides were a bit jumpy."
Traced to four mechanical causes, all fixable without changing a design:

1. **A direction reversed at maximum velocity.** The worst offender by far. A
   travel arc chained as two tweens (`x:310,y:500` on `power1.in`, then
   `x:180,y:824` on `power1.out`) reverses x at peak speed: a hard
   discontinuity in the middle of a move that reads as a kink. Fix: split the
   axes. Let y run as ONE uninterrupted `power1.inOut` descent, and bulge x out
   on `sine.out` and back on `sine.inOut` so the reversal happens at **zero
   x-velocity** while y keeps flowing. One curve, not two.

   **The threshold, measured:** on a clip that reads smooth, every direction
   reversal happens at **2-7% of that element's peak velocity** (0.027-0.096
   px/frame against a 1.324 px/frame peak). Anything reversing above ~10% of
   peak is the fault. Sample an element's per-frame displacement, find the sign
   changes, and check the speed at each one.
2. **An opacity jump on a long stroke.** `opacity 0 -> 0.95` on a 760px path is
   a global brightness step. Draw it with `strokeDashoffset` instead, and put
   the from-state (`opacity` + full dashoffset) in the markup.
3. **A scale/opacity pop on a small solid shape.** A 0.35s opacity fade on a
   13px dot is the most visible snap in a composition. Grow it: `fromTo` scale
   0.2 -> 1 over ~0.5s with an explicit `svgOrigin`.
4. **Gaps between moves.** Any frame where nothing is mid-tween, followed by
   something starting, reads as a jolt. Overlap everything: a new element
   begins while the previous is still settling.

Standing rules that fall out: entrances get **0.7-0.9s**, not 0.3s. Eases are
`power2.out` / `power3.out` / `sine.inOut`; **never** `back`, `elastic` or
`expo`. Path draws use `sine.inOut`, not `power2.inOut`, which bursts mid-stroke.

**Entrance velocity is the thing, not the ease name.** `power3.out` on an 820px
drop leaves the gate at ~1697 px/s; `power2.out` over 1.45s starts at ~1131 px/s
and decelerates the whole way. Same shape of curve, completely different read ,
the first is dropped, the second is lowered in. On a long travel, lengthen the
duration and soften the ease together.

### Measure smoothness, don't eyeball it   *(GATED: check_motion)*

Jumpy has a signature you can compute: a **zero-motion frame next to a spike**.

```bash
python3 <skill>/scripts/check_motion.py renders/clip.mp4 index.html
```

That runs the recipe below and applies the thresholds. Calibrated across 16
renders: a stall is **three consecutive frames** under 0.02 mean delta (one
near-still frame is invisible, 0.1s is the jolt), and the lead-in and the last
four frames are exempt: a clip opening on bare ground has nothing to move, and
a drift period sized to land on a sine turnaround ends at zero velocity on
purpose. By hand it is:

```bash
ffmpeg -i renders/<file>.mp4 -vf "fps=30,scale=54:96,format=gray" -f rawvideo -
# then per-frame abs(diff).mean() over consecutive frames
```

A clip that reads smooth has **no zero-motion frame and no spike**: measured on
a good 5.8s clip, frame-to-frame mean delta ran **min 0.134, max 1.823**, a
13x spread with no gaps. A zero in that series is a dead frame; a value several
times the local median is a snap. Both are findable before anyone watches it.

Landing matters too: size each drift period so it divides its element's own
remaining life exactly, and the element lands on the last frame **at a sine
turnaround** (zero velocity) instead of being cut off mid-travel.

---

## Two more, from the SVG side

**`transform-box: fill-box` double-applies with GSAP's SVG transform-origin.**
GSAP bakes the bbox offset into the matrix and the browser then offsets again: a
6° rotation rendered as a 35px sideways slide. Drop `transform-box` and let GSAP
own the origin.

**A padlock opens by lifting the shackle OUT, not by rotating it.** Rotating the
shackle alone drags its free leg down across the body and the whole thing reads
as a handbag. Needs a `y` lift plus a shallow rotation about an `svgOrigin` at
the shackle's hinge.

---

## Scale, learned the hard way

First renders come out too small more often than too large. One clip needed its
whole composition scaled up (card 320→520, crag 480→700, machine 280→420) after
the first look. A 1080x1920 frame is a phone screen: an element under ~200px
reads as a footnote.
