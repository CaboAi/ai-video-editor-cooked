# Cutting with a script

Read this when `edits/<name>/script.md` exists (the home window's "Add your script", or a script the user
gave in the chat: save it there first). Skits are the reason it matters: a Manager vs Intern skit repeats
lines on purpose, and one person often plays both characters in the same voice, so neither the
transcript nor the voice says which repeat is a retake. The script does.

```bash
python3 "$S/script_match.py" edits/<name>     # writes edits/<name>/script-match.md
```

It matches the transcript (`words.raw.json`) to the script line by line, fuzzy and in script order, and
writes, per script line:

- every take that says it, with its times and the transcript's own words (quote those in `spans.json`);
- `KEEPER` on the last complete take, `complete, earlier take` on the others, `partial` on false starts;
- `say-twice (also line N)` when the script itself has the line twice, or nearly, by any character;
- `ambiguous: decide by order or voice (line N or M)` on a say-twice take that could be either copy's
  (more complete takes than copies). A copy's only take is never called an earlier take.

It never writes `spans.json`. Then decide the cut as usual (`retake-detection.md`), with these rules:

1. A take marked `complete, earlier take` or `partial` is a retake: cut it (quote it in a span).
   `ambiguous` takes are not covered by this rule.
2. `say-twice`: each copy of the line keeps its own `KEEPER`. Never cut one as a retake of the other.
   If `spans.json` already cuts a say-twice keeper, remove that span.
3. `ambiguous`: work out which copy it belongs to from what comes before and after it in the transcript,
   or by the voice. A retake of a copy that has a later take: cut it. Still unsure: keep it and say so.
4. A one-word line ("Yes.") is too short to match by words: find it by ear in `transcript.txt`.
5. Off-script stretches are listed so you can look at them, not to cut them. Ad-libs that land stay.
   Cut one only for the usual reasons (a flub, "wait, sorry", a dead stretch).
6. A line `not found`: the take may have dropped or reworded it. Never invent a cut to make it fit.
