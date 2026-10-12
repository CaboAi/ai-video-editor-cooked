# Shapes

The one file Claude writes in the cut skill. Rules for what to cut: `retake-detection.md`.

## spans.json

```json
[
  {"text": "so the thing about, um,", "kind": "false_start", "note": "restarts at 5.8s"},
  {"text": "wait, sorry", "kind": "meta"},
  {"text": "the second thing is that you", "kind": "retake", "occurrence": 1,
   "confidence": "medium", "note": "keeper is the take at 70.4s"}
]
```

| field | rule |
|---|---|
| `text` | Words copied from the transcript. Punctuation and case do not matter. |
| `kind` | `retake`, `false_start`, `filler`, `meta`, `audio_event`, `redundant`, `alt_hook` (a take of the opening line after the body), `manual` (the user cut it on the review page's transcript; keep it) |
| `occurrence` / `after` | Required when the phrase appears more than once. `occurrence` is 1-based. `after` takes the first match starting after that many seconds: use the phrase's start time from `transcript.txt`. |
| `confidence` | `high` (default), `medium`, `low`. `low` cuts are highlighted for the user. |
| `note` | Why, in a few words. For a retake, where the keeper is. |

Two spans must not overlap. Merge them into one.

## keep.json (written by the review page)

```json
[{"start": 4.5, "end": 5.4}, {"start": 12.31, "end": 13.05}]
```

Raw-take seconds that no pause trim may remove: words the pause trim took that the user restored, and removed
pauses the user put back whole, on the review page's transcript. `build_timeline.py` takes each range (plus
0.05 s either side) out of every pause, lead-in, tail and splice cut. A cut the user makes later takes its words
out of it again. Never write it by hand, never delete it; `keep.prev.json` is the one before the last Apply.

## lead.json (hook variants only)

`retakes.py hook <edit_dir> <n>` writes it into `<edit_dir>-hook<n>/`: one quoted range, the same
fields as a span, that `build_timeline.py` plays first. Never write it by hand.
