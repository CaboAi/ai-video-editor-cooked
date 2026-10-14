# Shapes

The files Claude writes in style-edit, and the rules plan.py holds them to. More on each format:
`visuals.md`.

visuals.json is one JSON array, one object per beat, in spoken order. A whole two-beat file:

```json
[{"word": "notion", "nth": 1, "kind": "logo", "brand": "Notion", "domain": "notion.com"},
 {"word": "faster", "nth": 1, "kind": "capture", "url": "https://example.com/benchmarks", "format": "sticker",
  "marks": [{"kind": "highlight", "find": "10x faster", "at_word": "faster"}], "hold_s": 2.5}]
```

## images.json (the user's own pictures, first on screen)

`[{"src": "images/dashboard.png", "word": "dashboard", "nth": 1}]`, optional `layout`, `hold_s`,
`entrance`, `box`.

## visuals.json

One beat per visual, in this order of preference: the user's own assets (images.json), the real
thing captured (`capture`, `post`, `app`, `youtube`, `github`), UI rebuilt from real parts (`chat`,
`terminal`, `toasts`), real logos (`logo`, `logo_cluster`, `flow`).

Every beat: `word` (as captions.json spells it), `nth`, `hold_s` so the card is still up on its last
word (omit it for the creator's measured hold), a `word` on each part that lands on its own word.
A card on each beats.json `want`, never two at once, none in the first second unless it is the
hook's subject. A capture on the hook (first 3 s) needs a `find` mark on what is said, not the headline.

| pick | beat |
|---|---|
| `capture:shot` | `{"kind": "capture", "url", "format": "shot", "clip"?, "marks": [{"kind": "highlight" or "ring", "find": "exact page text", "at_word"}], "props": {"label"?}}` |
| `capture:browser` | the same with `"format": "browser"` (`props.url` defaults to the beat's) |
| `capture:sticker` | `{"kind": "capture", "url", "format": "sticker", "marks": [{"kind": "highlight", "find": "the said words as the page writes them", "at_word"}]}`. No `crop`: capture.mjs re-sets that sentence |
| `post` | `{"kind": "post", "url", "highlight": "the phrase said, as the post writes it"}` |
| `logo` | `{"kind": "logo", "brand", "domain"?}` |
| `logo_cluster` | `{"kind": "anim", "type": "logo_cluster", "props": {"logos": [{"logo", "domain"?, "word"?}]}}` |
| `chat` | `{"kind": "anim", "type": "chat", "props": {"app", "logo": {"logo"}, "model"?, "messages": [{"from": "user" or "app", "text", "word"}]}}` |
| `terminal` | `{"kind": "anim", "type": "terminal", "props": {"title"?, "lines": [{"text", "kind": "cmd" or "out", "word"?}]}}` |
| `toasts` | `{"kind": "anim", "type": "toasts", "props": {"items": [{"app", "logo": {"logo"}, "title", "body"?, "word"}]}}` |
| `side_by_side` | `{"kind": "anim", "type": "side_by_side", "props": {"a": {"src", "size", "crop"?, "label"?}, "b": {...}, "arrow"?: true}}` (`src`: a capture's file; `arrow`: before -> after) |
| `likes` | `{"kind": "anim", "type": "likes", "props": {"logo": "<platform>", "counts": [{"n": 0}, {"n": <said number>, "word"}], "comments"?: [{"user", "text", "word"}]}}` |
| `progress` | `{"kind": "anim", "type": "progress", "props": {"label", "title"?, "thumbs"?: ["images/<own frame>"], "word"?}}` |
| `curve` | `{"kind": "anim", "type": "curve", "props": {"label"?, "src"?: "images/<own still>", "bezier"?: [x1, y1, x2, y2]}}` |
| `notes` | `{"kind": "anim", "type": "notes", "props": {"title"?, "lines": [{"text", "sub"?, "time"?, "word"}]}}` |
| `video_card` | `{"kind": "anim", "type": "video_card", "props": {"src", "start_s"?, "label"?}}` |
| `flow` | `{"kind": "anim", "type": "flow", "props": {"nodes": [{"logo", "label", "word"}], "split"?: [{"logo", "label" or "lines": [{"text", "word"}], "word"}], "tasks"?: [{"text", "word", "heavy"?}], "accent"?}}`: one beat for the whole explanation, `hold_s` to its last word |
| `app` | `{"kind": "app", "url" or "app_id", "brand"?}`: the App Store icon and first screenshot |
| `youtube` / `github` | `{"kind": "youtube", "url"}` / `{"kind": "github", "repo": "owner/name"}`: the thumbnail / the repo's social card |

Capture options: `clip` `[x, y, w, h]` in page px or `selector` (CSS); `width` (viewport, default
1000; 400-550 reflows docs to read on a phone); `highlight` (an exact sentence on the page, `hold_s`
3+); `page_text: true` to pick the sentence later with `route.py highlight`; `wait_ms`. Each mark adds
something the capture does not already show.

## Card edit

style.json `card_kinds` without `"text"`: pictures and animations only (`motion-design.md`). Most beats
are animations built from the words (`likes`, `progress`, `notes`, `terminal`, `chat`, `toasts`, `curve`,
`side_by_side` with `arrow`, `flow`); a `capture` only where the real page is the point, once per page
(plan.py drops a second capture of the same URL), `shot` with a `find` mark or `browser` to scroll to it;
the user's own app screenshots cropped to about 2:1; a big `logo`. No sticker. A line with nothing to show
gets no card. Plus `title` (at the open), `cta` (on the ask), `list`, `stamp` beats (`visuals.md` "Card edit").

## Rules (plan.py enforces them and warns)

- Only the speaker's words and real names. Never invent a figure, quote, like count or date.
- Pictures are `{"logo": "brand"}` or a file in `images/`. Never `{"icon": ...}` alone, never emoji.
- No type cards (words on a plain ground). A stat is shown inside the page that published it, marked.
- No stock photos, purple-blue accent, dark glass with neon or Inter-style display face.
- Labels of a few words; `find` text exactly as the page writes it.
