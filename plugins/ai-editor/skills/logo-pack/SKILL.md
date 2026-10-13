---
name: logo-pack
description: Builds clean brand-colour logos for every company, app or tool named in a video. Lists the names from the edit's transcript, fetches each brand's own logo (SVG first, else a large PNG) with the editor's logo code, trims it, makes a transparent 512 px PNG beside the SVG, checks each one (official, transparent, sharp, brand colours) and fixes the failures, then notes the logos named together so the edit shows them side by side. Writes the files style-edit already reads (images/logo-<brand>.svg|png) plus logos.json. Use when the user says "make a logo pack", "get the logos for this video", "official logos for the apps I mention", "the logos look fake" or "logos in their real colours", or before style-edit on a video that names two or more products. Not for moving or swapping one card in a rendered edit (overlays), and not for screenshots of a page (style-edit captures).
license: MIT
compatibility: Node 18+ and the Chrome Headless Shell the setup skill installs (setup.py remotion). Runs from the full ai-editor plugin folder (uses style-edit's capture.mjs).
---

# Logo pack

Paths: `${CLAUDE_SKILL_DIR}` means the folder containing this SKILL.md, and `${CLAUDE_PLUGIN_ROOT}` the plugin folder two levels above it.

Every question to the user goes in the question box: call the AskUserQuestion tool (2-4 options, the recommended one first). Only in an agent without that tool, ask numbered questions in text.

This skill drives the logo code style-edit already uses (`capture.mjs` `fetchLogos`) and writes to the
same files, so plan.py, `logo` beats, `logo_cluster` and flow diagrams pick the logos up with no extra step.
A logo already in `images/` is kept by every later `capture.mjs` run.

Copy this checklist and tick it off:

```
- [ ] 1. Names listed from the transcript
- [ ] 2. logos.json written
- [ ] 3. logos.mjs run
- [ ] 4. Sheet looked at, every FIX line fixed, run again until all ok or kept
- [ ] 5. Handed to style-edit
```

## 1. List the names

Read `edits/<name>/captions.json` (else `words.json`). List every company, app, website and tool the speaker
names, once each. A product's models or plans take the product's logo ("Haiku", "Opus" are Claude) unless
they have their own. People, places and generic words ("AI", "the cloud") are not logos.

## 2. Write `edits/<name>/logos.json`

```json
{"logos": [{"name": "Notion", "domain": "notion.so"},
           {"name": "Jev", "brand": "typesafe", "domain": "typesafe.ai"}],
 "together": [["Notion", "Linear"]]}
```

- `name`: the word as spoken (route.py matches it in the transcript). `brand`: the file slug when it is not the
  name (a product sold under its company's mark). `domain`: the brand's own site, the fallback source.
- `url` (optional): a direct link to the brand's own SVG or large PNG, found on its press, brand or media-kit
  page. Tried before every other source.
- `together`: names said in the same breath ("Notion or Linear"). The edit shows each group as one `logo_cluster`.

## 3. Fetch, trim, check

```bash
node "${CLAUDE_SKILL_DIR}/scripts/logos.mjs" edits/<name>
```

Source order: `url`, then Simple Icons in the brand colour, the site's own SVG or apple-touch icon, and Google's
favicon service last. Each logo is trimmed to its drawn pixels (an SVG gets a tight viewBox), saved as
`images/logo-<brand>.svg` (when the source is an SVG) and `images/logo-<brand>.png` (512 px on the long side,
transparent), and checked. logos.json gains `file`, `png`, `source` (the URL) and `check`. Exit 1 while any
logo fails; `--fresh` fetches every logo again.

## 4. Look, fix, run again

Open `edits/<name>/images/logos-sheet.png`: every logo on white and on black, with its first problem under it.
Look at each one yourself: the check cannot tell a chart or a lookalike from the real mark.

| FIX line | fix |
|---|---|
| `no file` | add `domain`, or a `url` from the brand's press or brand page |
| `small: the PNG is N px` | find the SVG (press kit, the site header's logo) and set `url` |
| `solid background` | the transparent version from the press kit; an app icon is a last resort |
| `black only` / `white only` | the colour version (`url`); keep the mono one only if the brand's own logo is mono |
| `a favicon-service icon` | the brand's own file (`url`) |
| the sheet shows the wrong picture | a better `url`; remove a wrong one |

Run step 3 again after each change. Never draw, trace, recolour or generate a logo, and never use a
lookalike or a fan-made version: only the brand's own file or Simple Icons. When the brand publishes nothing
better (only an app icon), add `"keep": "<why>"` to that logo; the run then reports it as `kept`.
After two rounds with no official file, ask in the question box: `Keep it (Recommended)` / `Leave this logo out`.

Example run on the sample take (names Jev and Claude):

```
ok   Claude   images/logo-claude.svg
kept Jev      images/logo-typesafe.png  small: the PNG is 400 px ...; solid background ... (keep: the site publishes only its app icon)
```

## 5. Hand to style-edit

Tell the user in one line how many logos are ready and which were kept with a problem. style-edit (or the
`visual-prep` agent) runs `route.py beats`, which reads logos.json: each sentence lists the brands named in it,
and a sentence with two or more prints a `logo_cluster` hint. Write a `logo` beat with `"brand"` set to the
logo's brand, and one `logo_cluster` per `together` group (`references/visuals.md` in style-edit).

## Files

- `scripts/logos.mjs`: fetch (capture.mjs `fetchLogos`), trim, 512 px PNG, check, sheet; `demo` self-check.
- `edits/<name>/logos.json`: the names, files, source URLs and checks.
- `edits/<name>/images/logo-<brand>.svg|png`: the logos, where plan.py looks for them.
