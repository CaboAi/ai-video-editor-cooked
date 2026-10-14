// The board layout (plan.json layout.mode "board", the Motion design sample style, 16:9). One near-black canvas
// with a faint dot grid that a camera drags across. Logos land as dark silver-rimmed tiles on their words, joined by
// dashed lines, under a heading in the speaker's words; a question sits centred, then the camera pans down so it
// rises into the heading. Between boards the speaker has the whole frame: slow eased zooms, top-anchored, moved left
// or right so each overlay sits on the other side. Every time comes from plan.py board_layout. Units: px of 1920x1080.
import React from "react";
import { AbsoluteFill, Easing, Img, OffthreadVideo, spring, staticFile, useVideoConfig } from "remotion";

type W = [string, number][];
type Tile = { src: string; label: string; at: number };
type BoardT = { start: number; end: number; x: number; y: number; heading: W; tiles: Tile[]; question?: boolean; rise?: number };
export type BoardLayout = {
  mode: "board";
  ground: string;
  boards: BoardT[];
  camera: [number, number, number, number, number][]; // at, centre x, centre y, zoom, move seconds
  views: [number, number][]; // when the canvas has the frame
  questions: { start: number; end: number; words: W }[]; // asked with no board after: centred over the speaker
  face: [number, number, number, number][]; // at, zoom, side (-1 left, 0 centre, +1 right), move seconds
  head_x: number; // the head's centre, % of the width
  fade: number; // speaker <-> canvas crossfade, seconds
};

const ease = Easing.bezier(0.45, 0, 0.2, 1);
const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
const ramp = (t: number, a: number, d = 0.5) => ease(clamp01((t - a) / d));
const lerp = (a: number, b: number, k: number) => a + (b - a) * k;
const INK = "#F4F6FA";
const GLOW_T = "drop-shadow(0 2px 8px rgba(0,0,0,0.55))";
const RIM = "1.75px solid rgba(226,230,236,0.88)";
const TILE_BG = "linear-gradient(180deg, rgba(34,36,42,.96), rgba(14,15,18,.96))";
const HEAD_TOP = 96;
const ROW_Y = 610;
const TILE_W = [400, 350, 300, 260]; // boards are big, never sparse: one tile fills the middle

/** Keyframes [at, ...values, move seconds]: each eases from wherever the last left off. */
const keyed = (t: number, kf: number[][], n: number) => {
  const v = kf[0].slice(1, 1 + n);
  for (const k of kf) {
    if (t < k[0]) break;
    const p = ramp(t, k[0], Math.max(0.01, k[k.length - 1]));
    for (let i = 0; i < n; i++) v[i] = lerp(v[i], k[1 + i], p);
  }
  return v;
};

/** How much of the frame the canvas has, 0 (the speaker) to 1. */
export const boardOpacity = (t: number, L: BoardLayout) =>
  Math.max(0, ...L.views.map(([a, b]) => Math.min(ramp(t, a, L.fade), 1 - ramp(t, b, L.fade))));

/** Words, each fading up on its time. size shrinks so they fit `lines` lines (a question is never cut). */
const fit = (words: W, max: number, width = 1760, lines = 1) =>
  Math.min(max, (width * lines) / Math.max(1, words.map((w) => w[0]).join(" ").length * 0.53 + (lines > 1 ? 4 : 0)));
const Typed: React.FC<{ t: number; words: W; size: number; weight: number; wrap?: boolean; style?: React.CSSProperties }> = ({ t, words, size, weight, wrap, style }) => (
  <div style={{ textAlign: "center", fontWeight: weight, fontSize: size, lineHeight: 1.15, letterSpacing: "-0.015em", color: INK, filter: GLOW_T,
    whiteSpace: wrap ? "normal" : "nowrap", textWrap: wrap ? "balance" : undefined, ...style } as React.CSSProperties}>
    {words.map(([w, at], i) => (
      <span key={i} style={{ display: "inline-block", marginRight: "0.26em", opacity: ramp(t, at - 0.1, 0.22),
        transform: `translateY(${(1 - ramp(t, at - 0.1, 0.35)) * 10}px)`, filter: `blur(${(1 - ramp(t, at - 0.1, 0.3)) * 6}px)` }}>{w}</span>
    ))}
  </div>
);

const TileView: React.FC<{ t: number; fps: number; x: number; y: number; w: number; at: number; src: string }> = ({ t, fps, x, y, w, at, src }) => {
  const f = Math.round(t * fps), f0 = Math.round(at * fps);
  if (f < f0) return null;
  const sp = spring({ frame: f - f0, fps, config: { damping: 20, stiffness: 120 } });
  const glow = clamp01(1 - Math.max(0, (t - at - 0.6) / 0.6)); // lit as it lands, then it settles
  return (
    <div style={{ position: "absolute", left: x - w / 2, top: y - w / 2, width: w, height: w, borderRadius: w * 0.155, background: TILE_BG,
      border: RIM, boxSizing: "border-box", display: "flex", alignItems: "center", justifyContent: "center",
      boxShadow: `0 16px 36px rgba(0,0,0,.6), 0 4px 10px rgba(0,0,0,.4), inset 0 1px 0 rgba(255,255,255,.14), 0 0 26px rgba(255,255,255,${0.11 * glow})`,
      opacity: Math.min(1, sp * 1.5), transform: `translateY(${(1 - sp) * 24}px) scale(${lerp(0.84, 1, sp)})` }}>
      <Img src={staticFile(src)} style={{ width: w * 0.58, height: w * 0.58, objectFit: "contain", filter: "drop-shadow(0 1px 2px rgba(0,0,0,.45))" }} />
    </div>
  );
};

/** A 2 px dashed connector drawn on from a to b, then two dots flowing along it. */
const Dash: React.FC<{ t: number; x1: number; x2: number; y: number; at: number }> = ({ t, x1, x2, y, at }) => {
  const k = ramp(t, at, 0.6);
  if (k <= 0) return null;
  const dk = ramp(t, at + 0.7, 0.4);
  return (
    <svg width={1920} height={1080} style={{ position: "absolute", inset: 0, overflow: "visible" }}>
      <line x1={x1} y1={y} x2={lerp(x1, x2, k)} y2={y} stroke="rgba(222,226,234,0.62)" strokeWidth={2} strokeDasharray="10 9" />
      {dk > 0 && [0, 0.5].map((o) => {
        const u = ((t - at - 0.7) * 0.55 + o) % 1;
        return <circle key={o} cx={lerp(x1, x2, u)} cy={y} r={6} fill="#E6E9EF" opacity={dk * Math.sin(Math.PI * u) * 0.9} />;
      })}
    </svg>
  );
};

const BoardView: React.FC<{ b: BoardT; t: number; fps: number }> = ({ b, t, fps }) => {
  const n = b.tiles.length, w = TILE_W[Math.min(n, 4) - 1], gap = w * 0.62;
  const x0 = 960 - (n * w + (n - 1) * gap) / 2 + w / 2;
  const xs = b.tiles.map((_, i) => x0 + i * (w + gap));
  return (
    <div style={{ position: "absolute", left: b.x, top: b.y, width: 1920, height: 1080 }}>
      {/* a heading is one line; a question two at most, so it stays big */}
      <Typed t={t} words={b.heading} size={b.heading.length > 7 ? fit(b.heading, 64, 1500, 2) : fit(b.heading, 72)} weight={700} wrap={b.heading.length > 7}
        style={{ position: "absolute", top: HEAD_TOP, left: 210, width: 1500 }} />
      {b.tiles.slice(1).map((tl, i) => <Dash key={i} t={t} x1={xs[i] + w / 2 + 14} x2={xs[i + 1] - w / 2 - 14} y={ROW_Y} at={tl.at + 0.15} />)}
      {b.tiles.map((tl, i) => {
        const next = b.tiles[i + 1];
        const dim = next ? lerp(1, 0.5, ramp(t, next.at, 0.4)) : 1; // the active label bright, earlier ones dimmed
        return (
          <React.Fragment key={i}>
            <div style={{ position: "absolute", left: xs[i] - 300, top: ROW_Y - w / 2 - 82, width: 600, opacity: dim }}>
              <Typed t={t} words={[[tl.label, tl.at + 0.1]]} size={fit([[tl.label, 0]], 56, w + gap - 20)} weight={500} />
            </div>
            <TileView t={t} fps={fps} x={xs[i]} y={ROW_Y} w={w} at={tl.at} src={tl.src} />
          </React.Fragment>
        );
      })}
    </div>
  );
};

/** The speaker full frame: zoom about the top edge (the hair is never cut), the head moved left, centre or right. */
const faceRect = (t: number, L: BoardLayout) => {
  const [z, s] = keyed(t, L.face, 2);
  const slack = 1920 * (1 - 1 / z);
  const c0 = Math.min(slack, Math.max(0, (L.head_x / 100) * 1920 - 960 / z));
  const start = s >= 0 ? lerp(c0, 0, s) : lerp(c0, slack, -s);
  return { left: -start * z, top: 0, width: 1920 * z, height: 1080 * z };
};

export const BoardFrame: React.FC<{ layout: BoardLayout; video: string; t: number; font: string | null; captions: React.ReactNode;
  children?: React.ReactNode }> = ({ layout: L, video, t, font, captions, children }) => {
  const { fps, width } = useVideoConfig();
  const op = boardOpacity(t, L);
  const [cx, cy, cz] = L.camera.length ? keyed(t, L.camera, 3) : [960, 540, 1];
  const hw = 960 / cz + 300, hh = 540 / cz + 300;
  const q = L.questions.find((x) => t >= x.start - 0.3 && t < x.end + 0.4);
  const qk = q ? ramp(t, q.start - 0.3, 0.4) * (1 - ramp(t, q.end, 0.4)) : 0;
  return (
    <AbsoluteFill style={{ backgroundColor: L.ground, fontFamily: font ?? "sans-serif" }}>
      <div style={{ position: "absolute", left: 0, top: 0, width: 1920, height: 1080, transform: `scale(${width / 1920})`, transformOrigin: "0 0", overflow: "hidden" }}>
        {/* the speaker: always mounted, so the voice plays under the canvas too */}
        <div style={{ position: "absolute", inset: 0, opacity: 1 - op }}>
          {video ? <OffthreadVideo src={staticFile(video)} style={{ position: "absolute", ...faceRect(t, L), objectFit: "cover" }} /> : null}
        </div>
        {op > 0.001 ? (
          <div style={{ position: "absolute", inset: 0, opacity: op }}>
            <div style={{ position: "absolute", inset: 0, background: L.ground }} />
            <div style={{ position: "absolute", inset: 0, opacity: 0.5, background:
              "radial-gradient(ellipse 45% 38% at 8% 108%, rgba(225,231,240,0.42), transparent 70%)," +
              "radial-gradient(ellipse 45% 38% at 92% 108%, rgba(225,231,240,0.42), transparent 70%)," +
              "radial-gradient(ellipse 70% 30% at 50% 112%, rgba(200,208,220,0.22), transparent 75%)" }} />
            <div style={{ position: "absolute", left: 0, top: 0, transformOrigin: "0 0", transform: `translate(960px, 540px) scale(${cz}) translate(${-cx}px, ${-cy}px)` }}>
              {/* a faint dot grid on the canvas, so every pan reads as the camera moving over one surface */}
              <div style={{ position: "absolute", left: -3000, top: -3000, width: 9000, height: 6000 + 1350 * L.boards.length,
                backgroundImage: "radial-gradient(rgba(255,255,255,.075) 1.4px, transparent 1.9px)", backgroundSize: "56px 56px" }} />
              {L.boards.filter((b) => t >= b.start - 0.1 && b.x < cx + hw && b.x + 1920 > cx - hw && b.y < cy + hh && b.y + 1080 > cy - hh)
                .map((b, i) => <BoardView key={i} b={b} t={t} fps={fps} />)}
            </div>
            <div style={{ position: "absolute", inset: 0, background: "radial-gradient(ellipse 80% 70% at 50% 50%, transparent 60%, rgba(0,0,0,0.35))" }} />
          </div>
        ) : null}
        {children /* overlays on the speaker, sides alternating (plan.py board_layout) */}
        {q && qk > 0 ? (
          <div style={{ position: "absolute", inset: 0, opacity: qk, background: "radial-gradient(ellipse 70% 45% at 50% 47%, rgba(6,7,10,0.62), rgba(6,7,10,0.18) 80%)" }}>
            <div style={{ position: "absolute", left: 260, width: 1400, top: 0, bottom: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Typed t={t} words={q.words} size={fit(q.words, 84, 1400, 2)} weight={700} wrap />
            </div>
          </div>
        ) : null}
      </div>
      <AbsoluteFill style={{ opacity: (1 - op) * (1 - qk) }}>{captions}</AbsoluteFill>
    </AbsoluteFill>
  );
};
