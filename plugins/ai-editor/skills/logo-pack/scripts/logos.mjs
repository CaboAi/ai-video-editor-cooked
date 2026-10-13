// Fetches, trims and checks the logo of every brand in edits/NAME/logos.json, with the style-edit engine's own
// logo code (capture.mjs fetchLogos), into the files plan.py already reads: images/logo-<brand>.svg|png.
//
//   node logos.mjs edits/NAME [--fresh]    fetch, trim, 512 px PNG, check, contact sheet
//   node logos.mjs demo                    self-check (offline)
//
// logos.json, written by Claude first (the logo-pack skill), then filled in here:
//   {"logos": [{"name": "Notion", "brand"?: "notion", "domain"?: "notion.so", "url"?: "https://.../logo.svg"}],
//    "together": [["Notion", "Linear"]]}
// "keep": "why" on a logo accepts its problems (the brand publishes nothing better, e.g. only an app icon).
// Per logo this adds "file" (the SVG, else the PNG plan.py picks), "png" (512 px, trimmed, transparent), "source"
// (the URL it came from; null when the file was already there) and "check" {"ok", "problems", "colours"}.
// Source order: the logo's "url" (the brand's own SVG or press kit), then capture.mjs's order (Simple Icons in the
// brand colour, Simple Icons in black, the site's own SVG / apple-touch icon, Google's favicon service).
// A file already in images/ is kept (the user's own, or an earlier run); "url" or --fresh fetches it again.
// images/logos-sheet.png shows every logo on white and on black with its problems, for Claude to look at.
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const capture = () => import(pathToFileURL(path.join(HERE, "../../style-edit/scripts/capture.mjs")).href);
const PX = 512;
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "beat";   // = capture.mjs slug

// What is wrong with one logo, from what the page measured. Empty = keep it.
function problems(m, source, srcPx) {
  const out = [];
  if (!m) return ["no file: add a \"url\" (the brand's own SVG or press-kit PNG) or a \"domain\""];
  if (m.kind === "png" && srcPx < PX) out.push(`small: the PNG is ${srcPx} px, under ${PX}; find an SVG or a bigger PNG`);
  if (/google\.com\/s2\/favicons/.test(source || "")) out.push("a favicon-service icon, not the brand's file");
  if (!m.transparent) out.push("solid background: find the transparent version");
  if (m.tone === "black" || m.tone === "white")
    out.push(`${m.tone} only: use the brand-colour version unless the brand's own logo is ${m.tone}`);
  return out;
}

// An SVG with only a viewBox has no size of its own in an <img>; give it one so it rasterises sharp.
function sizeSvg(text, long = 2048) {
  const vb = text.match(/<svg\b[^>]*\bviewBox\s*=\s*["']\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)/i);
  if (!vb) return text;
  const [w, h] = [+vb[1], +vb[2]], k = long / Math.max(w, h);
  return text.replace(/<svg\b([^>]*)>/i, (all, attrs) =>
    `<svg${attrs.replace(/\s(width|height)\s*=\s*["'][^"']*["']/gi, "")} width="${Math.round(w * k)}" height="${Math.round(h * k)}">`);
}

// Runs in the page: trim to the drawn pixels (an SVG also gets a tight viewBox), fit PX, measure.
const PAGE = (src, isSvg, px) => `(async () => {
  let svgOut = null, url = ${JSON.stringify(src)};
  if (${isSvg}) {
    const doc = new DOMParser().parseFromString(atob(url.split(',')[1]), 'image/svg+xml');
    const el = document.importNode(doc.documentElement, true);
    el.style.position = 'absolute'; document.body.appendChild(el);
    try {
      const b = el.getBBox();
      if (b.width > 0 && b.height > 0) {
        el.setAttribute('viewBox', [b.x, b.y, b.width, b.height].join(' '));
        const k = 2048 / Math.max(b.width, b.height);
        el.setAttribute('width', Math.round(b.width * k)); el.setAttribute('height', Math.round(b.height * k));
      }
    } catch (e) {}
    el.removeAttribute('style');
    svgOut = new XMLSerializer().serializeToString(el);
    el.remove();
    url = 'data:image/svg+xml;base64,' + btoa(unescape(encodeURIComponent(svgOut)));
  }
  const img = new Image(); img.src = url; await img.decode();
  const W = img.naturalWidth, H = img.naturalHeight;
  const c = document.createElement('canvas'); c.width = W; c.height = H;
  const g = c.getContext('2d'); g.drawImage(img, 0, 0);
  const d = g.getImageData(0, 0, W, H).data;
  let x0 = W, y0 = H, x1 = -1, y1 = -1;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (d[(y * W + x) * 4 + 3] > 8) {
    if (x < x0) x0 = x; if (x > x1) x1 = x; if (y < y0) y0 = y; if (y > y1) y1 = y; }
  if (x1 < 0) return null;
  const tw = x1 - x0 + 1, th = y1 - y0 + 1, k = ${px} / Math.max(tw, th);
  const o = document.createElement('canvas'); o.width = Math.max(1, Math.round(tw * k)); o.height = Math.max(1, Math.round(th * k));
  const og = o.getContext('2d'); og.imageSmoothingQuality = 'high';
  og.drawImage(c, x0, y0, tw, th, 0, 0, o.width, o.height);
  const p = og.getImageData(0, 0, o.width, o.height).data;
  let clear = 0, solid = 0, sat = 0, lum = 0; const bins = {};
  for (let i = 0; i < p.length; i += 4) {
    if (p[i + 3] < 16) { clear++; continue; }
    if (p[i + 3] < 128) continue;
    solid++;
    const [r, gg, b] = [p[i], p[i + 1], p[i + 2]], mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
    lum += (0.2126 * r + 0.7152 * gg + 0.0722 * b) / 255;
    const colourful = mx - mn > 40;
    if (colourful) sat++;
    const key = '#' + [r, gg, b].map((v) => (v >> 4).toString(16).repeat(2)).join('');
    bins[key] = (bins[key] || 0) + (colourful ? 4 : 1);
  }
  const mono = sat < solid * 0.03, mean = solid ? lum / solid : 0;
  return { png: o.toDataURL('image/png'), svg: svgOut, transparent: clear > 0,
           tone: mono ? (mean < 0.2 ? 'black' : mean > 0.85 ? 'white' : 'grey') : 'colour',
           colours: Object.entries(bins).sort((a, b) => b[1] - a[1]).slice(0, 3).map((x) => x[0]) };
})()`;

// Every logo on a white and a black tile, its name and its problems under it.
const SHEET = (items) => `(async () => {
  const T = 220, cols = 3, rows = Math.ceil(${items.length} / cols), c = document.createElement('canvas');
  c.width = cols * T * 2; c.height = rows * (T + 70); const g = c.getContext('2d');
  g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height);
  const items = ${JSON.stringify(items)};
  for (let i = 0; i < items.length; i++) {
    const x = (i % cols) * T * 2, y = Math.floor(i / cols) * (T + 70), it = items[i];
    g.fillStyle = '#f2f2f2'; g.fillRect(x, y, T, T); g.fillStyle = '#111'; g.fillRect(x + T, y, T, T);
    if (it.png) {
      const im = new Image(); im.src = it.png; await im.decode();
      const k = (T - 40) / Math.max(im.width, im.height), w = im.width * k, h = im.height * k;
      for (const dx of [0, T]) g.drawImage(im, x + dx + (T - w) / 2, y + (T - h) / 2, w, h);
    }
    g.fillStyle = it.ok ? '#111' : '#c00'; g.font = '600 18px sans-serif'; g.fillText(it.name, x + 8, y + T + 24);
    g.font = '13px sans-serif'; g.fillText((it.problems[0] || 'ok').slice(0, 58), x + 8, y + T + 46);
  }
  return c.toDataURL('image/png');
})()`;

const fromDataUrl = (u) => Buffer.from(u.split(",")[1], "base64");
const pngWidth = (b) => (b.readUInt32BE(0) === 0x89504e47 ? Math.max(b.readUInt32BE(16), b.readUInt32BE(20)) : 0);

async function main(edit, fresh) {
  const listPath = path.join(edit, "logos.json");
  if (!fs.existsSync(listPath)) {
    console.error(`no ${listPath}: write it first ({"logos": [{"name": "..."}], "together": []}, the logo-pack skill)`);
    process.exit(2);
  }
  const pack = JSON.parse(fs.readFileSync(listPath, "utf8"));
  const logos = pack.logos || [];
  const names = new Set(logos.map((l) => l.name));
  for (const grp of pack.together || []) for (const n of grp) if (!names.has(n)) console.error(`  together: '${n}' is not in logos`);
  const { fetchLogos, launch, findBinary, SHELLS, logoPath } = await capture();
  fs.mkdirSync(path.join(edit, "images"), { recursive: true });
  for (const l of logos) {
    l.brand = l.brand || slug(l.name);
    if (l.url || fresh) for (const e of ["svg", "png"]) fs.rmSync(path.join(edit, logoPath(l.brand, e)), { force: true });
  }
  const got = {};
  await fetchLogos(edit, logos.map((l) => ({ brand: l.brand, domain: l.domain, url: l.url })), got);
  const bin = findBinary(SHELLS);
  if (!bin) {
    console.error(`ERROR: no Chrome Headless Shell under ${SHELLS}. Run the setup skill (setup.py remotion) once.`);
    process.exit(1);
  }
  const cdp = launch(bin);
  const sheet = [];
  try {
    const { targetId } = await cdp.send("Target.createTarget", { url: "about:blank" });
    const { sessionId } = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
    const run = async (expression) => (await cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true }, sessionId)).result.value;
    for (const l of logos) {
      const hit = got[slug(l.brand)];
      let m = null, srcPx = 0;
      if (hit) {
        const file = path.join(edit, hit.file), isSvg = hit.file.endsWith(".svg");
        let buf = fs.readFileSync(file);
        if (isSvg) buf = Buffer.from(sizeSvg(buf.toString("utf8")));
        else srcPx = pngWidth(buf);
        m = await run(PAGE(`data:image/${isSvg ? "svg+xml" : "png"};base64,${buf.toString("base64")}`, isSvg, PX)).catch(() => null);
        if (m) {
          m.kind = isSvg ? "svg" : "png";
          if (m.svg) fs.writeFileSync(file, m.svg);
          fs.writeFileSync(path.join(edit, logoPath(l.brand, "png")), fromDataUrl(m.png));
          l.file = hit.file;
          l.png = logoPath(l.brand, "png");
        }
        if (hit.from !== null || l.source === undefined) l.source = hit.from;
      }
      const probs = problems(m, l.source, srcPx);
      const pass = !probs.length || (!!m && !!l.keep);
      l.check = { ok: pass, problems: probs, colours: m ? m.colours : [] };
      sheet.push({ name: l.name, png: m && m.png, ok: pass, problems: probs });
      console.log(`${pass ? (probs.length ? "kept" : "ok  ") : "FIX "} ${l.name.padEnd(18)} ${l.file || "-"}  ${probs.join("; ")}${pass && probs.length ? ` (keep: ${l.keep})` : ""}`);
    }
    if (sheet.length) {
      fs.writeFileSync(path.join(edit, "images", "logos-sheet.png"), fromDataUrl(await run(SHEET(sheet))));
      console.log(`${path.join(edit, "images", "logos-sheet.png")}: look at it`);
    }
  } finally {
    cdp.close();
  }
  fs.writeFileSync(listPath, JSON.stringify(pack, null, 1));
  const bad = logos.filter((l) => !l.check.ok).length;
  console.log(`${listPath}: ${logos.length - bad} ok, ${bad} to fix`);
  if (bad) process.exit(1);
}

function demo() {
  const ok = (c, msg) => { if (!c) { console.error("FAIL", msg); process.exit(1); } };
  ok(problems({ kind: "svg", transparent: true, tone: "colour" }, "https://cdn.simpleicons.org/notion", 0).length === 0, "a clean svg");
  ok(problems({ kind: "png", transparent: true, tone: "colour" }, "x", 180)[0].startsWith("small"), "a 180 px png is small");
  ok(problems({ kind: "svg", transparent: true, tone: "black" }, "x", 0)[0].startsWith("black only"), "black-only flagged");
  ok(problems({ kind: "png", transparent: false, tone: "colour" }, "https://www.google.com/s2/favicons?domain=a.b&sz=256", 512).length === 2, "favicon + solid");
  ok(problems(null, null, 0)[0].startsWith("no file"), "missing");
  const s = sizeSvg('<svg xmlns="http://www.w3.org/2000/svg" width="24" viewBox="0 0 24 12"><path/></svg>');
  ok(/width="2048" height="1024"/.test(s) && !/width="24"/.test(s), s);
  ok(sizeSvg("<svg><path/></svg>") === "<svg><path/></svg>", "no viewBox: unchanged");
  ok(slug("Claude Code") === "claude-code", "slug matches capture.mjs");
  console.log("logos ok");
}

const a = process.argv.slice(2);
if (a[0] === "demo") demo();
else if (a[0]) await main(a[0], a.includes("--fresh"));
else { console.error("usage: node logos.mjs edits/NAME [--fresh] | demo"); process.exit(2); }
