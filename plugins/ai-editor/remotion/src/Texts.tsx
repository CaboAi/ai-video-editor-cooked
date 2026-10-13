import React from "react";
import { prog } from "./motion";
import { useFamily } from "./Captions";

// The card edit's words on screen besides the captions (plan.json "texts", plan.py text_items). Seconds on the cut,
// boxes in % of the frame, all inside the reels safe zone (plan.py keeps them there).
//   title: the hook's title (and the CTA at the end): each line its own off-white pill, bold near-black type, centred
//   list:  a running numbered list; each line lands on its word and stays until the list ends
//   stamp: a red circled X drawn over the card that is up, when the speaker says it does not apply
export type TextItem =
  | { kind: "title"; text: string; start: number; end: number; y_pct: number; size_pct: number }
  | { kind: "list"; items: { text: string; at: number }[]; start: number; end: number; x_pct: number; y_pct: number; size_pct: number }
  | { kind: "stamp"; start: number; end: number; box: [number, number, number, number] };

const POP_S = 0.18;

export const Texts: React.FC<{ items: TextItem[]; t: number; width: number; height: number; font?: string }> = ({ items, t, width, height, font }) => {
  const family = useFamily(font ?? "TikTok Sans", 700) ?? "sans-serif";
  return (
    <>
      {items.filter((x) => t >= x.start && t < x.end).map((x, i) => {
        if (x.kind === "title") {
          const fs = (x.size_pct / 100) * height;
          return (
            <div key={i} style={{ position: "absolute", left: "13%", width: "74%", top: `${x.y_pct}%`, display: "flex", flexDirection: "column",
              alignItems: "center", gap: fs * 0.08 }}>
              {x.text.split("\n").map((ln, j) => (
                <div key={j} style={{ background: "#FDFDF3", color: "#030100", fontFamily: family, fontWeight: 700, fontSize: fs, lineHeight: 1.12,
                  padding: `${fs * 0.06}px ${fs * 0.22}px`, borderRadius: fs * 0.14, textAlign: "center" }}>{ln}</div>
              ))}
            </div>
          );
        }
        if (x.kind === "list") {
          const fs = (x.size_pct / 100) * height;
          return (
            <div key={i} style={{ position: "absolute", left: `${x.x_pct}%`, top: `${x.y_pct}%`, maxWidth: "60%" }}>
              {x.items.filter((it) => t >= it.at).map((it, j) => {
                const p = prog(t, it.at, POP_S, "power2.out");
                return (
                  <div key={j} style={{ fontFamily: family, fontWeight: 700, fontSize: fs, lineHeight: 1.3, color: "#FFFFFF", whiteSpace: "nowrap",
                    WebkitTextStroke: `${fs * 0.06}px #000`, paintOrder: "stroke fill", textShadow: "0 2px 10px rgba(0,0,0,.35)",
                    opacity: p, transform: `translateY(${(1 - p) * fs * 0.4}px)` } as React.CSSProperties}>{`${j + 1}. ${it.text}`}</div>
                );
              })}
            </div>
          );
        }
        const [bx, by, bw, bh] = x.box;
        const d = Math.min((bw / 100) * width, (bh / 100) * height) * 0.62;
        const p = prog(t, x.start, POP_S * 1.6, "power2.out");
        const len = d * Math.SQRT2 * 0.5;
        return (
          <svg key={i} width={d} height={d} viewBox={`0 0 ${d} ${d}`} style={{ position: "absolute", left: (bx + bw / 2) / 100 * width - d / 2,
            top: (by + bh / 2) / 100 * height - d / 2 }}>
            <circle cx={d / 2} cy={d / 2} r={d * 0.44} fill="none" stroke="#E5322D" strokeWidth={d * 0.06}
              strokeDasharray={Math.PI * d * 0.88} strokeDashoffset={(1 - p) * Math.PI * d * 0.88} />
            {[[0.3, 0.3, 0.7, 0.7], [0.7, 0.3, 0.3, 0.7]].map(([a, b, c, e], k) => (
              <line key={k} x1={a * d} y1={b * d} x2={c * d} y2={e * d} stroke="#E5322D" strokeWidth={d * 0.06} strokeLinecap="round"
                strokeDasharray={len} strokeDashoffset={(1 - Math.min(1, Math.max(0, p * 2 - k))) * len} />
            ))}
          </svg>
        );
      })}
    </>
  );
};
