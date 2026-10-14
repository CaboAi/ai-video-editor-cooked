// Overlay formats: real pictures floating over the footage, never covering the face. Each is a template
// (JSON props, real assets only) that draws its own neutral chrome: a white card or the real app's
// surface, a soft realistic shadow, depth. Text on any card is a label of a few words at most; the
// picture carries the beat. Every format runs one word-labelled GSAP timeline (motion.ts wordTl): it
// drops in just before its word, punches on each hit word, rides a slow push and leaves upward
// on a faster .in ease (cardLife). Shared primitives: cursor + click ripple, zoom into a region (Capture's `zoom`).
import React from "react";
import { Img, OffthreadVideo, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { CARD_LEAD_S, Motion, OVERLAY_LEAD_S, cardLife, dropIn, exitUp, fitIn, fitOut, gsap, prog, punch, rng, useTl, wordTl, landAt, camera } from "./motion";
import { Look } from "./look";
import { Capture, Mark } from "./Capture";
import type { TP } from "./Templates";

const src_ = (s?: string) => (!s ? undefined : /^(https?:|data:)/.test(s) ? s : staticFile(s));
const fill: React.CSSProperties = { position: "absolute", inset: 0 };
const center: React.CSSProperties = { ...fill, display: "flex", alignItems: "center", justifyContent: "center" };
const MONO = "ui-monospace, 'SF Mono', Menlo, Consolas, 'DejaVu Sans Mono', monospace";
const useT = () => { const f = useCurrentFrame(); const { fps } = useVideoConfig(); return f / fps; };
/** 1% of the frame's short side, and the scale against a 1080 frame (type sizes are set for phones). */
const useFrame = () => { const { width, height } = useVideoConfig(); const U = Math.min(width, height) / 100; return { U, s: U / 10.8 }; };

/** A soft realistic shadow: contact, mid and a long ambient one. */
export const depth = (u: number) =>
  `0 ${u * 0.15}px ${u * 0.4}px rgba(0,0,0,0.22), 0 ${u * 1.4}px ${u * 3}px -${u * 0.6}px rgba(0,0,0,0.32), 0 ${u * 4}px ${u * 9}px -${u * 2}px rgba(0,0,0,0.38)`;

/** The card size that fits an image of `size` in w x h. */
const fitBox = (size: [number, number] | undefined, w: number, h: number, ratio = 16 / 10) => {
  const r = size ? size[0] / size[1] : ratio;
  return r > w / h ? [w, w / r] : [h * r, h];
};

/** A small dark label pill, a few words only. */
const Tag: React.FC<{ text?: string; u: number; look: Look; font: string; cls?: string; style?: React.CSSProperties }> = ({ text, u, look, font, cls, style }) =>
  text ? <div className={cls} style={{ position: "absolute", padding: `${u * 0.9}px ${u * 2}px`, borderRadius: u * 5, background: look.ink, color: look.ground,
    fontFamily: font, fontWeight: 600, fontSize: Math.max(26, u * 4.6), whiteSpace: "nowrap", boxShadow: depth(u * 0.5), ...style }}>{text}</div> : null;

/** The cursor primitive: a real arrow that travels to (x, y), clicks with a ripple at `at`. */
const Cursor: React.FC<{ s: number }> = ({ s }) => (
  <>
    <div className="cur-ripple" style={{ position: "absolute", left: -s * 0.9, top: -s * 0.9, width: s * 1.8, height: s * 1.8, borderRadius: "50%",
      border: `${Math.max(2, s * 0.08)}px solid rgba(255,255,255,0.95)`, boxShadow: "0 0 0 2px rgba(0,0,0,0.25)", opacity: 0 }} />
    <svg width={s} height={s * 1.3} viewBox="0 0 20 26" className="cur-arrow" style={{ position: "absolute", left: 0, top: 0, overflow: "visible",
      filter: "drop-shadow(0 2px 3px rgba(0,0,0,0.35))" }}>
      <path d="M1 1 L1 20 L6 15.5 L9.5 23.5 L13 22 L9.6 14.2 L16 14.2 Z" fill="#fff" stroke="#111" strokeWidth={1.4} strokeLinejoin="round" />
    </svg>
  </>
);
const cursorTo = (tl: gsap.core.Timeline, q: (s: string) => Element[], from: [number, number], to: [number, number], at: number, m: Motion) => {
  const c = q(".cursor")[0];
  if (!c) return;
  tl.fromTo(c, { x: from[0], y: from[1], autoAlpha: 0 }, { autoAlpha: 1, duration: 0.2 }, Math.max(0, at - 1.0));
  tl.fromTo(c, { x: from[0], y: from[1] }, { x: to[0], y: to[1], duration: 0.8 * m.k, ease: "power2.inOut", immediateRender: false }, Math.max(0, at - 1.0));
  tl.to(q(".cur-arrow")[0], { scale: 0.86, duration: 0.08, yoyo: true, repeat: 1, transformOrigin: "0 0" }, at - 0.04);
  tl.fromTo(q(".cur-ripple")[0], { scale: 0.2, opacity: 0.9 }, { scale: 1.4, opacity: 0, duration: 0.5, ease: "power2.out" }, at);
};

/** The source row on a white evidence card: the site's real logo and its name (props logo_src, label).
 *  Sizes are for a phone: 36 px type on a 1080 frame. */
const sourceRow = (s: number) => ({ h: 48 * s, gap: 12 * s, pad: 22 * s, fs: 36 * s });
const Source: React.FC<{ p: Record<string, any>; s: number; font: string }> = ({ p, s, font }) => {
  const r = sourceRow(s);
  return (
    <div className="cv-src" style={{ display: "flex", alignItems: "center", gap: 14 * s, height: r.h, marginBottom: r.gap }}>
      {p.logo_src ? <Img className="cv-src-logo" src={src_(p.logo_src)!} style={{ width: r.h * 0.92, height: r.h * 0.92, objectFit: "contain", borderRadius: "50%" }} /> : null}
      {p.label ? <div className="cv-src-name" style={{ fontFamily: font, fontWeight: 600, fontSize: r.fs, color: "#1B1B1F", letterSpacing: "-0.01em", whiteSpace: "nowrap" }}>{p.label}</div> : null}
      <div className="cv-rule" style={{ flex: 1, height: 2 * s, background: "#ECECEF", marginLeft: 10 * s, transformOrigin: "0 50%" }} />
    </div>
  );
};
/** The source row follows the card in: the logo spins up, the name slides, the rule draws (follow-through). */
const sourceIn = (tl: gsap.core.Timeline, q: (s: string) => Element[], at: number) => {
  const logo = q(".cv-src-logo")[0], name = q(".cv-src-name")[0], rule = q(".cv-rule")[0];
  if (logo) tl.fromTo(logo, { scale: 0, rotate: -90 }, { scale: 1, rotate: 0, duration: 0.45, ease: "power3.out" }, at + 0.2);
  if (name) tl.fromTo(name, { autoAlpha: 0, x: -16 }, { autoAlpha: 1, x: 0, duration: 0.35, ease: "power3.out" }, at + 0.26);
  if (rule) tl.fromTo(rule, { scaleX: 0 }, { scaleX: 1, duration: 0.7, ease: "power2.inOut" }, at + 0.32);
};
const headed = (p: Record<string, any>) => !!(p.label || p.logo_src);
/** Hits: the marks' landings (a highlight's too), so the card punches as the marked word is said. */
const markHits = (p: Record<string, any>) => [...(p.marks ?? []), ...(p.highlight?.rects?.length ? [1.6] : [])].slice(0, 3);

// =====================================================================================================
// shot: a real capture drops in just before its word, the camera pushes into the marked region as the
// mark lands, the card punches on that word, then it leaves upward. A label / logo_src puts the source
// row on a white card above the capture. props: src, size, marks?, highlight?, zoom? (1.45), label?, logo_src?
const Shot: React.FC<TP> = ({ p, w, h, look, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100;
  const { U, s } = useFrame();
  const r = sourceRow(s), head = headed(p);
  const [cw, ch] = head ? fitBox(p.size, w * 0.98 - 2 * r.pad, h * 0.98 - 2 * r.pad - r.h - r.gap) : fitBox(p.size, w * 0.96, h * 0.92);
  const ref = useTl((tl, q, root) => {
    const { land } = cardLife(tl, q, root, p, m, dur, u, U, markHits(p));
    sourceIn(tl, q, Math.max(0, land - CARD_LEAD_S - OVERLAY_LEAD_S * m.k));   // with the drop-in
  }, [p.src, w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  const cap = <Capture src={p.src} size={p.size} marks={p.marks} highlight={p.highlight} bw={cw} bh={ch} dur={dur} look={look} m={m} font={fonts.body}
    light zoom={p.zoom ?? 1.45} chrome={false} />;
  return (
    <div ref={ref} style={center}>
      <div className="cv-in" style={{ opacity: 0 }}><div className="cv-hit">
        {head ? (
          <div style={{ background: "#fff", borderRadius: 30 * s, padding: r.pad, boxShadow: depth(U * 0.8) }}>
            <Source p={p} s={s} font={fonts.body} />
            <div style={{ position: "relative", width: cw, height: ch, overflow: "hidden", borderRadius: 8 * s }}>{cap}</div>
          </div>
        ) : (
          <div style={{ position: "relative", width: cw, height: ch, borderRadius: (look.radius / 100) * Math.min(w, h), boxShadow: depth(u) }}>{cap}</div>
        )}
      </div></div>
    </div>
  );
};

// =====================================================================================================
// browser: the real page inside a browser window, scrolling to the marked sentence while a cursor
// travels there and clicks as the highlight lands. props: src, size, url, marks?, highlight?, cursor? (true)
const Browser: React.FC<TP> = ({ p, w, h, look, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100;
  const { U } = useFrame();
  const bar = Math.max(28, h * 0.09);
  const cw = Math.min(w * 0.96, (h * 0.94 - bar) * 1.7), ch = h * 0.94;
  const vh = ch - bar;
  const iw = p.size?.[0] ?? 1, f = cw / iw;
  const marks: Mark[] = p.marks ?? [];
  const first = marks[0] ?? (p.highlight?.rects?.[0] ? { rect: [p.highlight.rects[0][0] * iw, p.highlight.rects[0][1] * (p.size?.[1] ?? 1),
    p.highlight.rects[0][2] * iw, p.highlight.rects[0][3] * (p.size?.[1] ?? 1)], at: 1.6 } as any : undefined);
  const dh = (p.size?.[1] ?? 0) * f;
  const target: [number, number] | undefined = first ? [(first.rect[0] + first.rect[2] * 0.7) * f,
    bar + Math.min((first.rect[1] + first.rect[3] / 2) * f, dh > vh ? vh * 0.45 : (first.rect[1] + first.rect[3] / 2) * f)] : undefined;
  const ref = useTl((timeline, q, root) => {
    const { tl } = cardLife(timeline, q, root, p, m, dur, u, U, markHits(p));
    if (target && p.cursor !== false) {
      const at = first.at ?? first.at_s ?? 0.8;
      cursorTo(tl, q, [cw * 0.82, ch * 0.96], target, at, m);
      // after the click the hand rests and drifts along the line it clicked, as a reader's does
      if (dur - at > 1.2) tl.to(q(".cursor")[0], { x: target[0] + u * 5, y: target[1] + u * 1.2, duration: dur - at - 0.6, ease: "sine.inOut" }, at + 0.5);
    }
  }, [p.src, p.url, w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  const host = String(p.url ?? "").replace(/^https?:\/\//, "").replace(/\/$/, "");
  return (
    <div ref={ref} style={center}>
      <div className="cv-in" style={{ opacity: 0 }}><div className="cv-hit">
      <div className="br-win" style={{ position: "relative", width: cw, height: ch, borderRadius: u * 2.2, overflow: "hidden", background: "#fff",
        boxShadow: depth(u) }}>
        <div style={{ height: bar, background: "#F1F2F4", borderBottom: "1px solid rgba(0,0,0,0.08)", display: "flex", alignItems: "center", gap: bar * 0.18,
          padding: `0 ${bar * 0.4}px` }}>
          {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => <div key={c} style={{ width: bar * 0.26, height: bar * 0.26, borderRadius: "50%", background: c }} />)}
          <div style={{ flex: 1, marginLeft: bar * 0.4, height: bar * 0.58, borderRadius: bar * 0.3, background: "#fff", border: "1px solid rgba(0,0,0,0.08)",
            display: "flex", alignItems: "center", padding: `0 ${bar * 0.35}px`, fontFamily: fonts.body, fontSize: bar * 0.32, color: "#3C4043",
            overflow: "hidden", whiteSpace: "nowrap" }}>{host}</div>
        </div>
        <div style={{ position: "relative", width: cw, height: vh }}>
          <Capture src={p.src} size={p.size} marks={marks} highlight={p.highlight} bw={cw} bh={vh} dur={dur} look={look} m={m} font={fonts.body}
            light chrome={false} zoom={p.zoom ?? 1} />
        </div>
        {target && p.cursor !== false ? <div className="cursor" style={{ position: "absolute", left: 0, top: 0, opacity: 0 }}><Cursor s={Math.max(22, u * 4)} /></div> : null}
      </div>
      </div></div>
    </div>
  );
};

// =====================================================================================================
// sticker: the screenshot cropped to the evidence on a white card, the source row (logo_src, label) on
// top, dropped in just before its word, punched as the marked word is said, out upward.
// props: src, size, crop? [x, y, w, h] image px, rotate? (0), marks? (image px), label?, logo_src?
const Sticker: React.FC<TP> = ({ p, w, h, look, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100;
  const { U, s } = useFrame();
  // capture.mjs sets the sentence on white with 0.7 em of padding; the card has its own, so trim most of it
  const [W0, H0] = [p.size?.[0] ?? 1, p.size?.[1] ?? 1], trim = p.crop ? 0 : Math.round(Math.min(H0 * 0.08, W0 * 0.035));
  const [ix, iy, iw, ih] = p.crop ?? [trim, trim, W0 - 2 * trim, H0 - 2 * trim];
  const r = sourceRow(s), head = headed(p);
  const edge = head ? r.pad : Math.max(12 * s, u * 1.6);
  const [cw, ch] = fitBox([iw, ih], w * 0.98 - edge * 2, h * 0.98 - edge * 2 - (head ? r.h + r.gap : 0));
  const f = cw / iw;
  const rot = Number(p.rotate ?? 0);
  const ref = useTl((tl, q, root) => {
    const { land } = cardLife(tl, q, root, p, m, dur, u, U, markHits(p));
    sourceIn(tl, q, Math.max(0, land - CARD_LEAD_S - OVERLAY_LEAD_S * m.k));   // with the drop-in
  }, [p.src, JSON.stringify(p.crop), w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  return (
    <div ref={ref} style={center}>
      <div className="cv-in" style={{ opacity: 0 }}><div className="cv-hit">
        <div style={{ position: "relative", padding: edge, background: "#fff", borderRadius: 30 * s, boxShadow: depth(U * 0.8), transform: rot ? `rotate(${rot}deg)` : undefined }}>
          {head ? <Source p={p} s={s} font={fonts.body} /> : null}
          <div style={{ position: "relative", width: cw, height: ch, overflow: "hidden", borderRadius: 8 * s }}>
            <div style={{ position: "absolute", left: -ix * f, top: -iy * f }}>
              <Capture src={p.src} size={p.size} marks={p.marks}
                bw={(p.size?.[0] ?? iw) * f} bh={(p.size?.[1] ?? ih) * f} dur={dur} look={look} m={m} font={fonts.body} light chrome={false} />
            </div>
          </div>
        </div>
      </div></div>
    </div>
  );
};

// =====================================================================================================
// chat: an app's chat thread recreated from its real parts (name, logo): the user's message pops, the
// app types its reply. props: app, logo? (src), model? (subtitle), messages: [{from: user|app, text, at?}]
const Chat: React.FC<TP> = ({ p, w, h, look, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100, t = useT();
  const msgs: { from: string; text: string; at?: number }[] = (p.messages ?? []).slice(0, 4);
  const head = Math.max(34, Math.min(h * 0.17, w * 0.08));
  // a message is its line (1.3) + padding (0.64) + the gap (0.3): tight, so the words stay phone-sized
  const fs = Math.max(22, Math.min(w * 0.05, (h * 0.94 - head) / (msgs.length * 2.24 + 1.2)));
  const cw = Math.min(w * 0.96, Math.max(w * 0.6, h * 1.9));
  const chH = Math.min(h * 0.98, head + msgs.length * fs * 2.24 + fs * 1.2);
  const at = (x: { at?: number }, i: number) => x.at ?? 0.5 + i * 1.1;
  const { U } = useFrame();
  const ref = useTl((timeline, q, root) => {
    const { tl } = cardLife(timeline, q, root, p, m, dur, u, U, msgs.filter((x) => x.from !== "user").map((x) => x.at).slice(0, 3));
    msgs.forEach((x, i) => tl.fromTo(q(`.ch-m-${i}`)[0], { autoAlpha: 0, y: fs * 0.8, scale: 0.92 }, { autoAlpha: 1, y: 0, scale: 1, duration: 0.4 * m.k,
      ease: m.pop, transformOrigin: x.from === "user" ? "100% 100%" : "0% 100%" }, at(x, i) - (x.from === "user" ? 0 : 0.45)));
  }, [JSON.stringify(msgs), w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  return (
    <div ref={ref} style={center}>
      <div className="cv-in" style={{ opacity: 0 }}><div className="cv-hit">
      <div className="ch-win" style={{ width: cw, height: chH, borderRadius: u * 2.6, background: "#fff", boxShadow: depth(u), overflow: "hidden",
        fontFamily: fonts.body, display: "flex", flexDirection: "column" }}>
        <div style={{ height: head, display: "flex", alignItems: "center", gap: head * 0.25, padding: `0 ${head * 0.35}px`, borderBottom: "1px solid rgba(0,0,0,0.07)" }}>
          {p.logo ? <Img src={src_(p.logo)!} style={{ height: head * 0.56, width: head * 0.56, objectFit: "contain" }} /> : null}
          <div style={{ fontWeight: 700, fontSize: head * 0.36, color: "#111" }}>{p.app}</div>
          {p.model ? <div style={{ fontSize: head * 0.3, color: "#6B6F76", marginLeft: "auto" }}>{p.model}</div> : null}
        </div>
        <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "flex-end", gap: fs * 0.3, padding: fs * 0.6 }}>
          {msgs.map((x, i) => {
            const user = x.from === "user";
            const a = at(x, i);
            const typed = user ? x.text : x.text.slice(0, Math.max(0, Math.floor((t - a) * 42)));
            const thinking = !user && t < a;
            return (
              <div key={i} className={`ch-m-${i}`} style={{ alignSelf: user ? "flex-end" : "flex-start", maxWidth: "82%", padding: `${fs * 0.32}px ${fs * 0.65}px`,
                borderRadius: fs * 0.9, background: user ? "#111" : "#F1F2F4", color: user ? "#fff" : "#111", fontSize: fs, lineHeight: 1.3, opacity: 0 }}>
                {thinking ? <span style={{ letterSpacing: fs * 0.15, color: "#8A8F98" }}>{"•••".slice(0, 1 + (Math.floor(t * 4) % 3))}</span>
                  : <>{typed}<span style={{ opacity: typed.length < x.text.length ? 1 : 0 }}>▍</span></>}
              </div>
            );
          })}
        </div>
      </div>
      </div></div>
    </div>
  );
};

// =====================================================================================================
// terminal: a real terminal window; commands type in with a caret, their output lands under them.
// props: title?, lines: [{text, kind: cmd|out, at?}]
const Terminal: React.FC<TP> = ({ p, w, h, m, dur }) => {
  const u = Math.min(w, h) / 100, t = useT();
  const lines: { text: string; kind?: string; at?: number }[] = (p.lines ?? []).slice(0, 8);
  const bar = Math.max(26, h * 0.1);
  // as wide as the band (references/motion-design.md: cards fill the band, never a small window in its middle)
  const cw = w * 0.96, ch = h * 0.94;
  const longest = Math.max(...lines.map((l) => l.text.length + 2), 10);
  const fs0 = Math.max(18, Math.min((ch - bar) / (lines.length * 1.6 + 1), (cw * 0.9) / (longest * 0.66), h * 0.16));
  const fs = fs0;
  const tH = Math.min(ch, bar + lines.length * fs * 1.55 + fs * 1.6);
  let clock = 0.5;
  const times = lines.map((l) => {
    const a = l.at ?? clock;
    clock = a + (l.kind === "out" ? 0.25 : l.text.length / 30 + 0.35);
    return a;
  });
  const { U } = useFrame();
  const ref = useTl((timeline, q, root) => {
    // the hit: each command's output landing
    cardLife(timeline, q, root, p, m, dur, u, U, lines.map((l, i) => (l.kind === "out" ? times[i] : undefined)).slice(0, 3));
  }, [JSON.stringify(lines), w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  return (
    <div ref={ref} style={center}>
      <div className="cv-in" style={{ opacity: 0 }}><div className="cv-hit">
      <div className="tm-win" style={{ width: cw, height: tH, borderRadius: u * 2, background: "#0E0F12", boxShadow: depth(u), overflow: "hidden",
        border: "1px solid rgba(255,255,255,0.08)" }}>
        <div style={{ height: bar, display: "flex", alignItems: "center", gap: bar * 0.2, padding: `0 ${bar * 0.4}px`, background: "#1A1B1F" }}>
          {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => <div key={c} style={{ width: bar * 0.28, height: bar * 0.28, borderRadius: "50%", background: c }} />)}
          <div style={{ flex: 1, textAlign: "center", color: "#8B8E97", fontFamily: MONO, fontSize: bar * 0.36 }}>{p.title ?? ""}</div>
        </div>
        <div style={{ padding: fs * 0.7, fontFamily: MONO, fontSize: fs, lineHeight: 1.55, color: "#E6E6E6", whiteSpace: "pre" }}>
          {lines.map((l, i) => {
            const a = times[i];
            if (t < a) return null;
            const cmd = l.kind !== "out";
            const shown = cmd ? l.text.slice(0, Math.floor((t - a) * 30)) : l.text;
            const live = cmd && shown.length < l.text.length;
            return <div key={i} style={{ color: cmd ? "#F2F2F2" : "#9DA3AE", opacity: cmd ? 1 : prog(t, a, 0.2, "power1.out") }}>
              {cmd ? <span style={{ color: "#5FD68A" }}>$ </span> : null}{shown}
              {live || (cmd && i === lines.length - 1 && Math.floor(t * 2) % 2 === 0) ? <span style={{ background: "#E6E6E6", color: "#0E0F12" }}> </span> : null}
            </div>;
          })}
        </div>
      </div>
      </div></div>
    </div>
  );
};

// =====================================================================================================
// toasts: real notifications (the app's own logo and name) slide in from the top and stack.
// props: items: [{app, src, title, body?, at?}]
const Toasts: React.FC<TP> = ({ p, w, h, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100;
  const items: { app: string; src?: string; title: string; body?: string; at?: number }[] = (p.items ?? []).slice(0, 3);
  const th = Math.min(h / (items.length + 0.4), w * 0.24), cw = Math.min(w * 0.94, th * 4.6);
  const at = (x: { at?: number }, i: number) => x.at ?? 0.3 + i * 0.7;
  const { U } = useFrame();
  const ref = useTl((timeline, q, root) => {
    const { tl, L } = wordTl(timeline, p, landAt(p, m), m);
    camera(tl, q, root, m, dur, u, p.ambient);
    items.forEach((x, i) => {
      // each drops in from above just before its word; the older ones are pushed down a slot on it
      const pos = x.at === undefined ? at(x, i) : L(x, -0.3 * m.k);
      if (p.enter) fitIn(tl, q(`.to-${i}`)[0], x.at === undefined ? at(x, i) : L(x), p.enter, U);   // the creator's measured entrance
      else dropIn(tl, q(`.to-${i}`)[0], pos, m, th * 0.9);
      for (let j = 0; j < i; j++) tl.to(q(`.to-${j}`)[0], { y: (i - j) * th * 1.08, duration: 0.5 * m.k, ease: "power3.out" }, pos);
    });
    if (p.exit) fitOut(tl, q(".to-all")[0], dur, p.exit, U);
    else exitUp(tl, q(".to-all")[0], m, dur, U * 4.6);
  }, [JSON.stringify(items), w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  return (
    <div ref={ref} style={{ ...fill, display: "flex", justifyContent: "center" }}>
      <div className="to-all" style={{ position: "relative", width: cw, height: h }}>
        {items.map((x, i) => (
          <div key={i} className={`to-${i}`} style={{ position: "absolute", left: 0, top: h * 0.03, width: cw, height: th, borderRadius: th * 0.24,
            background: "rgba(250,250,252,0.97)", boxShadow: depth(u * 0.8), display: "flex", alignItems: "center", gap: th * 0.16, padding: `0 ${th * 0.18}px`,
            fontFamily: fonts.body, boxSizing: "border-box", opacity: 0 }}>
            {x.src ? <Img src={src_(x.src)!} style={{ width: th * 0.46, height: th * 0.46, objectFit: "contain", borderRadius: th * 0.1 }} /> : null}
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", fontSize: th * 0.15, color: "#6B6F76", fontWeight: 600, letterSpacing: "0.02em" }}>
                <span style={{ textTransform: "uppercase" }}>{x.app}</span><span style={{ marginLeft: "auto" }}>now</span></div>
              <div style={{ fontSize: th * 0.21, fontWeight: 700, color: "#111", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{x.title}</div>
              {x.body ? <div style={{ fontSize: th * 0.18, color: "#3C4043", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{x.body}</div> : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

// =====================================================================================================
// logo_cluster: real logos pop around the speaker's head, bare with a soft shadow, staggered, bobbing.
// props: logos: [{src, at?}], head? [x, y, w, h] % of the card box (plan.py fills it from face.json)
const LogoCluster: React.FC<TP> = ({ p, w, h, m, dur }) => {
  const u = Math.min(w, h) / 100;
  const logos: { src: string; at?: number }[] = (p.logos ?? []).slice(0, 6);
  const [hx, hy, hw, hh] = p.head ?? [32, 33, 28, 23];
  const cx = ((hx + hw / 2) / 100) * w, cy = ((hy + hh / 2) / 100) * h;
  const R = Math.max((hw / 100) * w, (hh / 100) * h) * 0.92;
  // vertical (plan.py sets band and size): readable logos, their outer edges on a band centred on the frame,
  // so the cluster is as wide as check.py expects; otherwise sized and placed by the head
  const band: [number, number] | undefined = p.band;
  const S = band ? (w * (p.size ?? 20)) / 100 : Math.min(w * 0.15, R * 0.62);
  const n = logos.length;
  const r = rng(Number(p.seed ?? 3));
  // spread over the upper arc, from left of the head over the top to the right
  const spots = logos.map((_, i) => {
    const a = (-180 + 20 + (n === 1 ? 70 : (i * 140) / Math.max(1, n - 1))) * (Math.PI / 180);
    const x = band
      ? (n === 1 ? w / 2 : (band[0] / 100) * w + S / 2 + ((i / (n - 1)) * ((band[1] - band[0]) / 100) * w - (i / (n - 1)) * S))
      : Math.min(w * 0.84 - S / 2, Math.max(S * 0.6, cx + Math.cos(a) * R * 1.15)); // clear of the frame edge and the app rail
    // in the band the big logos stay above the head: never lower than just over its top
    const y = band ? Math.min(cy + Math.sin(a) * R * 1.05, (hy / 100) * h - S * 0.55) : cy + Math.sin(a) * R * 1.05;
    return [x, Math.max(h * (h > w ? 0.131 : 0.12) + S / 2, y), (r() - 0.5) * 16, r() * 6.28];   // vertical: the reels safe zone's top (plan.py SAFE)
  });
  const { U } = useFrame();
  const ref = useTl((timeline, q) => {
    const { tl, L } = wordTl(timeline, p, landAt(p, m), m);
    q(".lc-logo").forEach((el, i) => {
      const a = logos[i].at ?? 0.15 + i * 0.16;
      // drops in just before its word, lands softly, punches on the word, bobs, leaves upward
      if (p.enter) fitIn(tl, el, L(logos[i]), p.enter, U);   // the creator's measured entrance
      else tl.fromTo(el, { scale: 0.6, y: -U * 4, rotate: spots[i][2] - 12, autoAlpha: 0 },
        { scale: 1, y: 0, rotate: spots[i][2], autoAlpha: 1, duration: 0.5 * m.k, ease: "power3.out" }, L(logos[i], -0.25 * m.k));
      punch(tl, q(`.lc-hit-${i}`)[0], L(logos[i]), 1.08);
      if (dur > 1.5) tl.fromTo(q(`.lc-bob-${i}`)[0], { y: 0 }, { y: -u * 1.4, duration: 1.1 + (i % 3) * 0.2, ease: "sine.inOut", yoyo: true,
        repeat: Math.max(0, Math.floor((dur - a - 0.6) / 1.2)) }, a + 0.5);
      if (p.exit) fitOut(tl, el, dur, p.exit, U);
      else if (dur > m.out * 3) tl.to(el, { y: -U * 4, autoAlpha: 0, duration: Math.max(0.24, m.out * 0.7), ease: m.exit }, dur - m.out + i * 0.03);
    });
  }, [JSON.stringify(logos), w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  return (
    <div ref={ref} style={fill}>
      {logos.map((l, i) => (
        <div key={i} className="lc-logo" style={{ position: "absolute", left: spots[i][0] - S / 2, top: spots[i][1] - S / 2, width: S, height: S, opacity: 0 }}>
          <div className={`lc-bob-${i}`} style={fill}><div className={`lc-hit-${i}`} style={fill}>
            <Img src={src_(l.src)!} style={{ width: "100%", height: "100%", objectFit: "contain", filter: `drop-shadow(0 0 ${u * 1.4}px rgba(255,255,255,0.6)) drop-shadow(0 ${u * 0.8}px ${u * 1.6}px rgba(0,0,0,0.35))` }} />
          </div></div>
        </div>
      ))}
    </div>
  );
};

// =====================================================================================================
// side_by_side: two real screenshots slide in from both edges over the footage and settle tilted
// toward each other. props: a: {src, size, crop?, label?, marks?}, b: {...}
const SideBySide: React.FC<TP> = ({ p, w, h, look, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100;
  const gap = w * (p.arrow ? 0.13 : 0.04), colW = (w - gap) / 2;
  const sides = [p.a ?? {}, p.b ?? {}];
  // each card at its own ratio, the pair centred as one group (a wide and a tall shot stay balanced)
  // a side may crop its capture to the evidence: crop [x, y, w, h] in image px
  const fits = sides.map((x) => fitBox(x.crop ? [x.crop[2], x.crop[3]] : x.size, colW, h * 0.8));
  const { U } = useFrame();
  const ref = useTl((timeline, q, root) => {
    const { tl, L } = wordTl(timeline, p, landAt(p, m), m);
    camera(tl, q, root, m, dur, u, p.ambient);
    sides.forEach((x, i) => {
      const el = q(`.sb-${i}`)[0];
      // a then b drop in just before the word, each settling a little toward the other; marks punch their side
      if (p.enter) fitIn(tl, el, L("in", i * 0.14), p.enter, U);   // the creator's measured entrance
      else dropIn(tl, el, L("in", -(CARD_LEAD_S + OVERLAY_LEAD_S * m.k) + i * 0.14), m, U * 6.5);
      (x.marks ?? []).slice(0, 2).forEach((mk: object) => punch(tl, q(`.sb-hit-${i}`)[0], L(mk)));
      const tag = q(`.sb-tag-${i}`)[0];
      if (tag) tl.fromTo(tag, { autoAlpha: 0, y: u * 2 }, { autoAlpha: 1, y: 0, duration: 0.4, ease: m.pop }, L("in", 0.15 + i * 0.14));
      if (p.exit) fitOut(tl, el, dur, p.exit, U);
      else exitUp(tl, el, m, dur, U * 4.6);
    });
    // arrow: before -> after, drawn from a to b once b has landed
    const ar = q(".sb-arrow")[0];
    if (ar) {
      tl.fromTo(ar, { autoAlpha: 0, scaleX: 0 }, { autoAlpha: 1, scaleX: 1, duration: 0.45, ease: "power3.out", transformOrigin: "0% 50%" }, L("in", 0.3));
      if (p.exit) fitOut(tl, ar, dur, p.exit, U);
      else exitUp(tl, ar, m, dur, U * 4.6);
    }
  }, [JSON.stringify(sides.map((x) => x.src)), w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  return (
    <div ref={ref} style={{ ...fill, display: "flex", alignItems: "center", justifyContent: "center", gap }}>
      {p.arrow ? <svg className="sb-arrow" viewBox="0 0 100 40" width={gap * 0.84} height={gap * 0.34} style={{ position: "absolute", left: w / 2 - gap * 0.42,
        top: h / 2 - gap * 0.17, zIndex: 2, opacity: 0, overflow: "visible", filter: "drop-shadow(0 2px 4px rgba(0,0,0,0.35))" }}>
        <path d="M4 20 H84 M66 6 L88 20 L66 34" fill="none" stroke="#141414" strokeWidth={9} strokeLinecap="round" strokeLinejoin="round" />
      </svg> : null}
      {sides.map((x, i) => {
        const [cw, ch] = fits[i];
        return (
          <div key={i} style={{ width: cw, display: "flex", justifyContent: "center" }}>
            <div className={`sb-${i}`} style={{ opacity: 0 }}><div className={`sb-hit-${i}`}>
            <div style={{ position: "relative", width: cw, height: ch, borderRadius: u * 2, boxShadow: depth(u), transform: `rotate(${i ? 1.2 : -1.2}deg)` }}>
              {x.src && x.crop ? (() => {
                const f = cw / x.crop[2];
                return <div style={{ ...fill, overflow: "hidden", borderRadius: u * 2, background: "#fff" }}>
                  <div style={{ position: "absolute", left: -x.crop[0] * f, top: -x.crop[1] * f }}>
                    <Capture src={x.src} size={x.size} marks={x.marks} bw={x.size[0] * f} bh={x.size[1] * f} dur={dur} look={look} m={m} font={fonts.body} light chrome={false} />
                  </div></div>;
              })() : x.src ? <Capture src={x.src} size={x.size} marks={x.marks} bw={cw} bh={ch} dur={dur} look={look} m={m} font={fonts.body} light chrome={false} /> : null}
              <Tag cls={`sb-tag-${i}`} text={x.label} u={u} look={look} font={fonts.body} style={{ left: u * 1.5, top: -u * 3.5 }} />
            </div>
            </div></div>
          </div>
        );
      })}
    </div>
  );
};

// =====================================================================================================
// video_card: a short real clip (the user's own, or a recorded page) in a rounded card over the
// footage. props: src (a video in images/), size? [w, h], start_s? (0), label?, rate? (1), bare? (no card: a 50/50 panel clip)
const VideoCard: React.FC<TP> = ({ p, w, h, look, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100;
  const { fps } = useVideoConfig();
  // bare (a 50/50 panel clip, motion skill): the clip is the panel, edge to edge, no card, radius or shadow
  const [cw, ch] = p.bare ? fitBox(p.size, w, h, 16 / 9) : fitBox(p.size, w * 0.96, h * 0.92, 16 / 9);
  const { U } = useFrame();
  const ref = useTl((timeline, q, root) => {
    const { tl, L } = cardLife(timeline, q, root, p, m, dur, u, U);
    const tag = q(".vc-tag")[0];
    if (tag) tl.fromTo(tag, { autoAlpha: 0, y: u * 2 }, { autoAlpha: 1, y: 0, duration: 0.4, ease: m.pop }, L("in", 0.1));
  }, [p.src, w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  return (
    <div ref={ref} style={center}>
      <div className="cv-in" style={{ opacity: 0 }}><div className="cv-hit">
      <div className="vc-card" style={{ position: "relative", width: cw, height: ch, borderRadius: p.bare ? 0 : u * 2.4, boxShadow: p.bare ? undefined : depth(u) }}>
        <div style={{ ...fill, borderRadius: p.bare ? 0 : u * 2.4, overflow: "hidden", background: p.bare ? undefined : "#000" }}>
          {p.src ? <OffthreadVideo src={src_(p.src)!} muted startFrom={Math.round(Number(p.start_s ?? 0) * fps)} playbackRate={Number(p.rate ?? 1)}
            style={{ width: "100%", height: "100%", objectFit: "cover" }} /> : null}
        </div>
        <Tag cls="vc-tag" text={p.label} u={u} look={look} font={fonts.body} style={{ left: u * 2, top: -u * 3.5 }} />
      </div>
      </div></div>
    </div>
  );
};


// =====================================================================================================
// The moving cards of a card edit (references/motion-design.md): each is built from what the speaker says and
// keeps moving while it is up, so the band above the head never holds a still picture.
const fmt = (n: number) => Math.round(n).toLocaleString("en-US");
type Said = { at?: number; word?: string };
/** Each item's time: its word (plan.py "at"), else staggered from `first` every `step` s. */
const stagger = <T extends Said>(xs: T[], first: number, step: number) => xs.map((x, i) => (typeof x.at === "number" ? x.at : first + i * step));
/** A white app window: traffic lights and a title, the real app's surface. */
const Win: React.FC<{ w: number; h: number; u: number; title?: string; dark?: boolean; font: string; children: React.ReactNode }> = ({ w, h, u, title, dark, font, children }) => {
  const bar = Math.max(30, h * 0.11);
  return (
    <div style={{ width: w, height: h, borderRadius: u * 2.2, background: dark ? "#111214" : "#FFFFFF", boxShadow: depth(u), overflow: "hidden", position: "relative" }}>
      <div style={{ height: bar, display: "flex", alignItems: "center", gap: bar * 0.22, padding: `0 ${bar * 0.42}px`, background: dark ? "#1B1C20" : "#F4F4F6",
        borderBottom: dark ? "1px solid rgba(255,255,255,0.06)" : "1px solid #E7E7EA" }}>
        {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => <div key={c} style={{ width: bar * 0.3, height: bar * 0.3, borderRadius: "50%", background: c }} />)}
        <div style={{ flex: 1, textAlign: "center", color: dark ? "#8B8E97" : "#7A7D85", fontFamily: font, fontWeight: 600, fontSize: bar * 0.42, marginRight: bar * 1.2 }}>{title ?? ""}</div>
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: bar, bottom: 0 }}>{children}</div>
    </div>
  );
};

// likes: the app's real logo with a red heart badge whose count ticks up on each word, and comments stacking in
// beside it as dark pills. props: src (the app logo: {"logo": "instagram"}), counts: [{n, word?}],
// comments?: [{user, text, src?, word?}] (at most 4). Numbers only as the speaker said them or the user gave them.
const Likes: React.FC<TP> = ({ p, w, h, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100, t = useT();
  const { U, s } = useFrame();
  const counts: ({ n: number } & Said)[] = (p.counts ?? []).slice(0, 6);
  const comments: ({ user: string; text: string; src?: string } & Said)[] = (p.comments ?? []).slice(0, 4);
  const ct = stagger(counts, 0.35, Math.max(0.45, (dur - 0.9) / Math.max(1, counts.length)));
  const mt = stagger(comments, 0.55, Math.max(0.35, (dur - 1.0) / Math.max(1, comments.length)));
  // the number now: eased from the last count to the one landing
  let n = counts[0]?.n ?? 0;
  counts.forEach((c, i) => { if (i && t >= ct[i]) n = counts[i - 1].n + (c.n - counts[i - 1].n) * prog(t, ct[i], 0.5, "power2.out"); });
  const L = Math.min(h * 0.78, w * 0.32);
  const pillH = Math.min(h / 3.6, 88 * s), cw = Math.min(w - L * 1.2 - u * 6, pillH * 8);
  const ref = useTl((timeline, q, root) => {
    const { tl } = cardLife(timeline, q, root, p, m, dur, u, U);
    tl.fromTo(q(".lk-badge")[0], { scale: 0.2, autoAlpha: 0 }, { scale: 1, autoAlpha: 1, duration: 0.45, ease: "power3.out" }, 0.25);
    ct.slice(1).forEach((a) => punch(tl, q(".lk-badge")[0], a, 1.18));
    comments.forEach((_, i) => {
      tl.fromTo(q(`.lk-c-${i}`)[0], { autoAlpha: 0, x: u * 6, scale: 0.92 }, { autoAlpha: 1, x: 0, scale: 1, duration: 0.4, ease: "power3.out" }, mt[i]);
      for (let j = 0; j < i; j++) tl.to(q(`.lk-c-${j}`)[0], { y: (i - j) * pillH * 1.18, duration: 0.4, ease: "power3.out" }, mt[i]);
    });
    tl.fromTo(q(".lk-heart")[0], { scale: 1 }, { scale: 1.12, duration: 0.5, ease: "sine.inOut", yoyo: true, repeat: Math.max(0, Math.floor(dur / 0.5) - 1) }, 0.6);
  }, [JSON.stringify([counts, comments]), w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  return (
    <div ref={ref} style={center}>
      <div className="cv-in" style={{ opacity: 0 }}><div className="cv-hit" style={{ display: "flex", alignItems: "center", gap: u * 5 }}>
        <div style={{ position: "relative", width: L, height: L, flexShrink: 0, marginRight: L * 0.15 }}>
          {p.src ? <Img src={src_(p.src)!} style={{ width: "100%", height: "100%", objectFit: "contain", filter: `drop-shadow(0 ${u}px ${u * 2}px rgba(0,0,0,0.3))` }} /> : null}
          <div className="lk-badge" style={{ position: "absolute", right: -L * 0.36, top: 0, display: "flex", alignItems: "center", gap: L * 0.05,
            padding: `${L * 0.05}px ${L * 0.11}px`, borderRadius: L, background: "#FF3040", color: "#fff", fontFamily: fonts.body, fontWeight: 800,
            fontSize: Math.max(30 * s, L * 0.2), boxShadow: depth(u * 0.5), whiteSpace: "nowrap", transformOrigin: "20% 80%", fontVariantNumeric: "tabular-nums" }}>
            <svg className="lk-heart" viewBox="0 0 24 24" width={L * 0.2} height={L * 0.2}><path fill="#fff" d="M12 21s-7.5-4.6-10-9.3C.4 8.4 2.3 4.5 6 4.5c2.2 0 3.6 1.2 4.4 2.4h3.2c.8-1.2 2.2-2.4 4.4-2.4 3.7 0 5.6 3.9 4 7.2C19.5 16.4 12 21 12 21z" /></svg>
            {fmt(n)}
          </div>
        </div>
        {comments.length ? <div style={{ position: "relative", width: cw, height: Math.min(h * 0.9, comments.length * pillH * 1.18) }}>
          {comments.map((c, i) => (
            <div key={i} className={`lk-c-${i}`} style={{ position: "absolute", left: 0, top: 0, height: pillH, width: cw, display: "flex", alignItems: "center",
              gap: pillH * 0.22, padding: `0 ${pillH * 0.3}px`, borderRadius: pillH * 0.3, background: "rgba(22,22,24,0.92)", boxSizing: "border-box", opacity: 0,
              fontFamily: fonts.body, fontSize: pillH * 0.4, color: "#fff", whiteSpace: "nowrap", overflow: "hidden", boxShadow: depth(u * 0.5) }}>
              {c.src ? <Img src={src_(c.src)!} style={{ width: pillH * 0.62, height: pillH * 0.62, borderRadius: "50%", objectFit: "cover" }} />
                : <div style={{ width: pillH * 0.62, height: pillH * 0.62, borderRadius: "50%", background: "linear-gradient(135deg,#FEDA75,#D62976 55%,#4F5BD5)", flexShrink: 0 }} />}
              <b style={{ fontWeight: 700 }}>{c.user}</b><span style={{ opacity: 0.85, overflow: "hidden", textOverflow: "ellipsis" }}>{c.text}</span>
            </div>
          ))}
        </div> : null}
      </div></div>
    </div>
  );
};

// progress: an app window where a bar fills while the work runs: the label, the frames (the user's own pictures,
// optional) lighting up as the bar passes them, the percent counting. props: label ("Rendering"), title?,
// thumbs?: [src] (at most 6), to? (100), word? (when it starts filling)
const Progress: React.FC<TP> = ({ p, w, h, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100, t = useT();
  const { U, s } = useFrame();
  const thumbs: string[] = (p.thumbs ?? []).slice(0, 6);
  const cw = Math.min(w * 0.98, h * 2.3), ch = Math.min(h * 0.96, cw / 1.9);
  const start = typeof p.at === "number" ? p.at : 0.35, run = Math.max(0.8, dur - start - 0.45);
  const k = prog(t, start, run, "power1.inOut") * (Number(p.to ?? 100) / 100);
  const fs = Math.max(30 * s, ch * 0.09);
  const ref = useTl((timeline, q, root) => {
    cardLife(timeline, q, root, p, m, dur, u, U, [start + run]);
    q(".pg-th").forEach((el, i) => timeline.fromTo(el, { autoAlpha: 0, y: u * 3 }, { autoAlpha: 1, y: 0, duration: 0.35, ease: "power3.out" }, 0.2 + i * 0.07));
  }, [JSON.stringify(thumbs), w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  const tw = thumbs.length ? Math.min((cw * 0.88) / thumbs.length - u, ch * 0.42 * 0.7) : 0;
  return (
    <div ref={ref} style={center}>
      <div className="cv-in" style={{ opacity: 0 }}><div className="cv-hit">
        <Win w={cw} h={ch} u={u} title={p.title} font={fonts.body}>
          <div style={{ padding: `${ch * 0.07}px ${cw * 0.06}px`, display: "flex", flexDirection: "column", gap: ch * 0.06, height: "100%", boxSizing: "border-box" }}>
            <div style={{ display: "flex", fontFamily: fonts.body, fontWeight: 700, fontSize: fs, color: "#141414" }}>
              <span>{p.label ?? ""}</span><span style={{ marginLeft: "auto", fontVariantNumeric: "tabular-nums" }}>{Math.round(k * 100)}%</span>
            </div>
            <div style={{ height: ch * 0.07, borderRadius: ch, background: "#ECECEF", overflow: "hidden", flexShrink: 0 }}>
              <div style={{ width: `${k * 100}%`, height: "100%", background: "#141414", borderRadius: ch }} />
            </div>
            {thumbs.length ? <div style={{ display: "flex", gap: u, justifyContent: "center" }}>
              {thumbs.map((src, i) => <div key={i} className="pg-th" style={{ width: tw, height: tw / 0.7, borderRadius: u, overflow: "hidden", opacity: 0,
                filter: `grayscale(${k * thumbs.length > i + 0.5 ? 0 : 1})`, boxShadow: depth(u * 0.3) }}>
                <Img src={src_(src)!} style={{ width: "100%", height: "100%", objectFit: "cover" }} /></div>)}
            </div> : null}
          </div>
        </Win>
      </div></div>
    </div>
  );
};

// curve: an easing curve draws itself on a white board while a dot rides it and the picture beside it moves on the
// same curve (motion design shown, not told). props: label?, src? (a still of the user's video), bezier? [x1, y1, x2, y2]
const Curve: React.FC<TP> = ({ p, w, h, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100, t = useT();
  const { U, s } = useFrame();
  const [x1, y1, x2, y2] = p.bezier ?? [0.65, 0, 0.35, 1];
  const cw = Math.min(w * 0.98, h * 2.1), ch = Math.min(h * 0.96, cw / 1.8);
  const gw = ch * 0.72, pad = ch * 0.12;
  const bz = (k: number, a: number, b: number) => 3 * a * k * (1 - k) ** 2 + 3 * b * k * k * (1 - k) + k ** 3;
  const k = prog(t, 0.15, Math.min(1.2, Math.max(0.5, dur - 0.35)), "sine.inOut");
  const len = gw * 2;
  const ref = useTl((timeline, q, root) => { cardLife(timeline, q, root, p, m, dur, u, U); },
    [w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  const d = `M0 ${gw} C ${x1 * gw} ${gw - y1 * gw}, ${x2 * gw} ${gw - y2 * gw}, ${gw} 0`;
  const px = bz(k, x1, x2) * gw, py = gw - bz(k, y1, y2) * gw;
  const iw = p.src ? ch * 0.5 : 0;
  return (
    <div ref={ref} style={center}>
      <div className="cv-in" style={{ opacity: 0 }}><div className="cv-hit">
        <Win w={cw} h={ch} u={u} title={p.label} font={fonts.body}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-evenly", height: "100%" }}>
            {p.src ? <div style={{ width: iw, height: iw * 1.5, borderRadius: u * 1.4, overflow: "hidden", boxShadow: depth(u * 0.5),
              transform: `translateY(${(0.5 - bz(k, y1, y2)) * ch * 0.18}px) scale(${0.9 + 0.1 * bz(k, y1, y2)})` }}>
              <Img src={src_(p.src)!} style={{ width: "100%", height: "100%", objectFit: "cover" }} /></div> : null}
            <svg width={gw + pad * 2} height={gw + pad * 2} viewBox={`${-pad} ${-pad} ${gw + pad * 2} ${gw + pad * 2}`} style={{ overflow: "visible" }}>
              <path d={`M0 0 V${gw} H${gw}`} fill="none" stroke="#D9D9DE" strokeWidth={Math.max(2, 3 * s)} />
              <path d={d} fill="none" stroke="#141414" strokeWidth={Math.max(4, 7 * s)} strokeLinecap="round" strokeDasharray={len} strokeDashoffset={len * (1 - k)} />
              <circle cx={px} cy={py} r={Math.max(8, 14 * s)} fill="#FF3B30" />
            </svg>
          </div>
        </Win>
      </div></div>
    </div>
  );
};

// notes: a real notes panel (a review page's notes), never words alone: the speaker's avatar and the title in its
// header; each line lands on its word beside the frame of the take where it is said, and a green tick draws in on it
// ("fixed"), its sub line ("Fixed in round 2: ...") under it. plan.py take_frames fills avatar and each line's src from
// the take. props: title?, avatar?, lines: [{text, sub?, time?, src?, word?}] (at most 4), the speaker's or user's words.
const Notes: React.FC<TP> = ({ p, w, h, m, fonts, dur }) => {
  const u = Math.min(w, h) / 100, t = useT();
  const { U, s } = useFrame();
  const lines: ({ text: string; sub?: string; time?: string; src?: string } & Said)[] = (p.lines ?? []).slice(0, 4);
  const lt = stagger(lines, 0.35, Math.max(0.5, (dur - 0.9) / Math.max(1, lines.length)));
  const cw = Math.min(w * 0.98, h * 2.6), ch = h * 0.96;
  const head = p.avatar || p.title ? ch * 0.2 : 0;
  const n = Math.max(1, lines.length);
  const row = Math.min((ch - head - ch * 0.08) / n, ch * 0.36);   // one line: a row, not a poster
  const longest = Math.max(10, ...lines.map((l) => l.text.length));
  const thumb = lines.some((l) => l.src) ? row * 0.84 : 0;   // a portrait frame of the take, 3:4
  const fs = Math.max(26 * s, Math.min(row * (lines.some((l) => l.sub) ? 0.36 : 0.46), (cw * 0.62 - thumb * 0.75) / (longest * 0.54)));
  const tick = row * 0.42, len = 30;
  const ref = useTl((timeline, q, root) => {
    const { tl } = cardLife(timeline, q, root, p, m, dur, u, U, lt.slice(1));
    const av = q(".nt-av")[0];
    if (av) tl.fromTo(av, { scale: 0.4, autoAlpha: 0 }, { scale: 1, autoAlpha: 1, duration: 0.4, ease: "back.out(2)" }, 0.12);
    lines.forEach((l, i) => {
      tl.fromTo(q(`.nt-${i}`)[0], { autoAlpha: 0, y: u * 3 }, { autoAlpha: 1, y: 0, duration: 0.35, ease: "power3.out" }, lt[i]);
      const th = q(`.nt-th-${i}`)[0];
      if (th) tl.fromTo(th, { scale: 0.7 }, { scale: 1, duration: 0.4, ease: "back.out(1.8)" }, lt[i]);
      tl.fromTo(q(`.nt-tk-${i}`)[0], { scale: 0 }, { scale: 1, duration: 0.3, ease: "back.out(2.4)" }, lt[i] + 0.3);
      if (l.sub) tl.fromTo(q(`.nt-s-${i}`)[0], { autoAlpha: 0, x: -u * 2 }, { autoAlpha: 1, x: 0, duration: 0.3, ease: "power2.out" }, lt[i] + 0.4);
    });
  }, [JSON.stringify(lines), p.avatar, p.title, w, h, dur, m.name, JSON.stringify([p.enter, p.exit, p.ambient, p.word_at])]);
  return (
    <div ref={ref} style={center}>
      <div className="cv-in" style={{ opacity: 0 }}><div className="cv-hit">
        <div style={{ width: cw, height: ch, borderRadius: u * 2.2, background: "#111214", boxShadow: depth(u), boxSizing: "border-box",
          border: "1px solid rgba(255,255,255,0.08)", overflow: "hidden", fontFamily: fonts.body, display: "flex", flexDirection: "column" }}>
          {head ? <div style={{ height: head, flexShrink: 0, display: "flex", alignItems: "center", gap: head * 0.3, padding: `0 ${cw * 0.04}px`,
            background: "#1A1B1F", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
            {p.avatar ? <Img className="nt-av" src={src_(p.avatar)!} style={{ width: head * 0.66, height: head * 0.66, borderRadius: "50%", objectFit: "cover",
              border: `${Math.max(2, head * 0.03)}px solid #2B2C31`, opacity: 0 }} /> : null}
            {p.title ? <div style={{ color: "#F4F4F5", fontSize: Math.min(head * 0.38, fs * 0.9), fontWeight: 700, whiteSpace: "nowrap", overflow: "hidden" }}>{p.title}</div> : null}
          </div> : null}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", padding: `${ch * 0.04}px ${cw * 0.04}px` }}>
            {lines.map((l, i) => {
              const k = prog(t, lt[i] + 0.4, 0.35, "power2.out");   // the tick's stroke drawing
              return (
                <div key={i} className={`nt-${i}`} style={{ opacity: 0, height: row, display: "flex", gap: row * 0.22, alignItems: "center",
                  borderTop: i ? "1px solid rgba(255,255,255,0.06)" : "none" }}>
                  {l.src ? <Img className={`nt-th-${i}`} src={src_(l.src)!} style={{ width: thumb * 0.75, height: thumb, borderRadius: row * 0.1, objectFit: "cover", flexShrink: 0 }} /> : null}
                  <div style={{ minWidth: 0, flex: 1 }}>
                    {l.time ? <div style={{ color: "#8B8E97", fontSize: fs * 0.62, fontWeight: 600 }}>{l.time}</div> : null}
                    <div style={{ color: "#F4F4F5", fontSize: fs, fontWeight: 700, lineHeight: 1.15, whiteSpace: "nowrap", overflow: "hidden" }}>{l.text}</div>
                    {l.sub ? <div className={`nt-s-${i}`} style={{ color: "#3DDC84", fontSize: fs * 0.7, fontWeight: 600, marginTop: fs * 0.15, whiteSpace: "nowrap" }}>{l.sub}</div> : null}
                  </div>
                  <div className={`nt-tk-${i}`} style={{ width: tick, height: tick, borderRadius: "50%", background: "rgba(61,220,132,0.16)", flexShrink: 0,
                    display: "flex", alignItems: "center", justifyContent: "center", transform: "scale(0)" }}>
                    <svg viewBox="0 0 24 24" width={tick * 0.62} height={tick * 0.62}>
                      <path d="M4 12.5l5 5L20 6.5" fill="none" stroke="#3DDC84" strokeWidth={3.4} strokeLinecap="round" strokeLinejoin="round"
                        strokeDasharray={len} strokeDashoffset={len * (1 - k)} />
                    </svg>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div></div>
    </div>
  );
};

export const OVERLAYS: Record<string, React.FC<TP>> = {
  shot: Shot, browser: Browser, sticker: Sticker, chat: Chat, terminal: Terminal, toasts: Toasts, logo_cluster: LogoCluster,
  side_by_side: SideBySide, video_card: VideoCard, likes: Likes, progress: Progress, curve: Curve, notes: Notes,
};
