// The four proofs, frame by frame, on one clip: drives the clip's real GSAP timeline in Chrome and reads every
// element's real getBoundingClientRect(). Nothing is re-derived. Ported from the 50/50 edit's verify_composition.mjs.
//
//   node verify.mjs <clip dir> [--rails]      prints one JSON line: {clipped, rails, neighbour, reading, pass, notes}
//   node verify.mjs demo                      self-check on a tiny clip it writes itself
//
//   1 CLIPPED    a visible .node partly inside and partly outside the stage, while it is not moving (a slide that
//                runs content through the edge on purpose moves more than 3 px a frame, so it is exempt)
//   2 RAILS      (--rails, a whole-frame clip) anything visible left of x 60 or right of x 940 of 1080
//   3 NEIGHBOUR  two different .beat elements both visible and still on the same frame (a push or a dissolve moves)
//   4 READING    each element holding words needs 0.25 s to arrive + 0.30 s a word of frames where it is fully opaque,
//                fully inside the stage and under 3 px of motion
//
// Uses the puppeteer-core that the HyperFrames CLI installs ($AI_EDITOR_HOME/motion/node_modules) and the Chrome that
// `hyperframes browser path` names. Exit 0 when all four are 0, 1 otherwise, 2 on a setup problem.
import { createRequire } from "node:module";
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, writeFileSync, copyFileSync, rmSync, mkdirSync } from "node:fs";
import { join, resolve } from "node:path";
import { homedir, tmpdir } from "node:os";
import { pathToFileURL } from "node:url";

const HOME = process.env.AI_EDITOR_HOME || join(homedir(), ".ai-video-editor");
const MOTION = join(HOME, "motion");
const FPS = 30;
const STILL_PX = 3;

function tools() {
  const req = createRequire(join(MOTION, "package.json"));
  let puppeteer;
  try { puppeteer = req("puppeteer-core"); } catch { console.error("ERROR: the motion tools are not installed (setup.py motion)"); process.exit(2); }
  const bin = join(MOTION, "node_modules", ".bin", process.platform === "win32" ? "hyperframes.cmd" : "hyperframes");
  const chrome = execFileSync(bin, ["browser", "path"], { encoding: "utf8" }).trim().split("\n").pop();
  return { puppeteer, chrome };
}

export async function verify(dir, { rails = false } = {}) {
  const { puppeteer, chrome } = tools();
  const b = await puppeteer.launch({ executablePath: chrome, headless: true, args: ["--allow-file-access-from-files"] });
  try {
    const p = await b.newPage();
    const html = resolve(dir, "index.html");
    await p.goto(pathToFileURL(html).href);
    await p.waitForFunction(() => window.__timelines && Object.keys(window.__timelines).length, { timeout: 15000 });
    return await p.evaluate(({ FPS, STILL_PX, rails }) => {
      const root = document.querySelector("[data-hyperframes-root]");
      const W = +root.dataset.width, H = +root.dataset.height, dur = +root.dataset.duration;
      document.body.style.margin = "0";
      const tl = Object.values(window.__timelines)[0];
      const stage = root.getBoundingClientRect();
      const nodes = [...root.querySelectorAll(".node")];
      const beats = [...root.querySelectorAll(".beat")];
      const texty = [...root.querySelectorAll("*")].filter((e) => [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim().length > 1));
      const op = (e) => { let o = 1; for (let x = e; x && x !== document.body; x = x.parentElement) { const s = getComputedStyle(x); o *= +s.opacity; if (s.visibility === "hidden" || s.display === "none") return 0; } return o; };
      const inside = (r) => r.left >= stage.left - 1 && r.right <= stage.right + 1 && r.top >= stage.top - 1 && r.bottom <= stage.bottom + 1;
      const overlaps = (r) => r.right > stage.left && r.left < stage.right && r.bottom > stage.top && r.top < stage.bottom;
      const prev = new Map(), read = new Map(), out = { clipped: 0, rails: 0, neighbour: 0, reading: 0, notes: [] };
      const n = Math.round(dur * FPS);
      for (let i = 0; i < n; i++) {
        tl.seek(i / FPS, false);
        const moved = (e, r) => { const q = prev.get(e); prev.set(e, r); return q ? Math.hypot(r.left - q.left, r.top - q.top) > STILL_PX : false; };
        const still = new Map();
        for (const e of new Set([...nodes, ...beats, ...texty])) { const r = e.getBoundingClientRect(); still.set(e, [r, !moved(e, r)]); }
        for (const e of nodes) {
          const [r, s] = still.get(e);
          if (op(e) < 0.05 || r.width < 2) continue;
          if (s && overlaps(r) && !inside(r)) { out.clipped++; if (out.notes.length < 6) out.notes.push(`clipped ${e.id || e.className} at ${(i / FPS).toFixed(2)} s`); }
          const sx = (v) => (v - stage.left) * (1080 / W);
          if (rails && overlaps(r) && (sx(r.left) < 60 || sx(r.right) > 940)) out.rails++;
        }
        const up = beats.filter((e) => { const [r, s] = still.get(e); return s && op(e) > 0.05 && overlaps(r); });
        if (up.length > 1) { out.neighbour++; if (out.notes.length < 6) out.notes.push(`two beats at ${(i / FPS).toFixed(2)} s`); }
        for (const e of texty) {
          const [r, s] = still.get(e);
          if (s && op(e) > 0.98 && inside(r)) read.set(e, (read.get(e) || 0) + 1);
        }
      }
      for (const e of texty) {
        const words = e.textContent.trim().split(/\s+/).length, need = 0.25 + 0.3 * words, got = (read.get(e) || 0) / FPS;
        if (got < need) { out.reading++; if (out.notes.length < 6) out.notes.push(`"${e.textContent.trim().slice(0, 30)}" readable ${got.toFixed(2)} s of ${need.toFixed(2)} s`); }
      }
      out.pass = !out.clipped && !out.rails && !out.neighbour && !out.reading;
      return out;
    }, { FPS, STILL_PX, rails });
  } finally { await b.close(); }
}

async function demo() {
  const d = mkdtempSync(join(tmpdir(), "verify-demo-"));
  try {
    mkdirSync(join(d, "assets"));
    const gsap = join(MOTION, "node_modules", "gsap", "dist", "gsap.min.js");
    if (!existsSync(gsap)) { console.log("demo skipped: the motion tools are not installed"); return; }
    copyFileSync(gsap, join(d, "assets", "gsap.min.js"));
    const page = (left, words) => `<!doctype html><html><head><script src="assets/gsap.min.js"></script><style>body{margin:0}
      #root{position:relative;width:400px;height:200px;overflow:hidden}.node{position:absolute;top:60px;width:160px;height:60px;background:#ddd}</style></head>
      <body><div id="root" data-hyperframes-root data-composition-id="v" data-start="0" data-duration="2" data-width="400" data-height="200">
      <div class="node" id="a" style="left:${left}px">${words}</div></div>
      <script>var tl=gsap.timeline({paused:true});tl.to("#a",{y:4,duration:2,ease:"none"},0);window.__timelines={v:tl};</script></body></html>`;
    writeFileSync(join(d, "index.html"), page(40, "a short line"));
    const ok = await verify(d);
    if (!ok.pass) throw new Error("clean clip failed: " + JSON.stringify(ok));
    writeFileSync(join(d, "index.html"), page(320, "one two three four five six seven eight"));
    const bad = await verify(d);
    if (!(bad.clipped > 0 && bad.reading > 0)) throw new Error("clipped clip passed: " + JSON.stringify(bad));
    console.log("demo ok");
  } finally { rmSync(d, { recursive: true, force: true }); }
}

const [arg, ...rest] = process.argv.slice(2);
if (arg === "demo") await demo();
else if (arg) { const r = await verify(arg, { rails: rest.includes("--rails") }); console.log(JSON.stringify(r)); process.exit(r.pass ? 0 : 1); }
else { console.error("usage: node verify.mjs <clip dir> [--rails] | demo"); process.exit(2); }
