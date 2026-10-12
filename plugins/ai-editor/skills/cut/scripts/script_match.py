#!/usr/bin/env python3
"""Line the transcript up with the user's script, for Claude to read before deciding spans.

    python3 script_match.py <edit_dir> [--script <file>]   writes <edit_dir>/script-match.md
    python3 script_match.py demo                            self-check

Reads <edit_dir>/words.raw.json (timed words) and <edit_dir>/script.md (or --script). For every script
line: each take that says it (times and the transcript's own words), complete or partial, and the LAST
complete take marked keeper. Lines the script itself says twice (or near enough, by any character) are
say-twice: every take that matches a separate script line is kept. Transcript stretches no line matches
are listed as off-script, which is never a reason to cut. It never writes spans.json: Claude decides.
"""
import difflib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import textnorm  # noqa: E402

SAME_LINE = 0.9      # two script lines this alike (token ratio) are one line said twice
COMPLETE = 0.75      # share of a line's words a take must say, with its last word, to count as complete
PARTIAL = 0.4        # below this a window is noise, not a false start
MIN_WORDS = 2        # a one-word line ("Why?") matches everywhere: listed, not matched
OFF_MIN = 4          # off-script stretches shorter than this are not listed
SPEAKER = re.compile(r"^([A-Za-z][\w .'-]{0,30}?)\s*:\s*(.+)$")


def parse_script(text):
    """[{n, speaker, text, toks}] for every spoken line. Headings, rules, and lines that are only a stage
    direction ([...] or (...)) are skipped; markdown and inline directions are stripped."""
    out = []
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if not s or s.startswith("#") or re.fullmatch(r"[-=*_ ]{3,}", s):
            continue
        s = re.sub(r"[*_`>]+", "", s).strip()
        s = re.sub(r"^(?:[-+]\s+|\d+[.)]\s+)", "", s)
        s = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", s).strip()
        speaker = ""
        m = SPEAKER.match(s)
        if m and len(m.group(1).split()) <= 3:
            speaker, s = m.group(1).strip(), m.group(2).strip()
        toks = textnorm.words(s)
        if toks:
            out.append({"n": n, "speaker": speaker, "text": s, "toks": toks})
    return out


def timed(words):
    """[(token, start, end, word index)] over the transcript's spoken words."""
    toks, owner = [], []
    for i, w in enumerate(words):
        if w.get("type", "word") != "word":
            continue
        for x in textnorm.norm(w.get("text", "")).split():
            toks.append(x)
            owner.append(i)
    return [(t, words[owner[k]]["start"], words[owner[k]]["end"], owner[k]) for t, k in textnorm.canon_src(toks)]


def say_twice(lines):
    """{line index: [other line indexes it repeats]} for lines the script says more than once."""
    out = {}
    for i, a in enumerate(lines):
        for j, b in enumerate(lines):
            if i != j and len(a["toks"]) >= MIN_WORDS and \
                    difflib.SequenceMatcher(a=a["toks"], b=b["toks"], autojunk=False).ratio() >= SAME_LINE:
                out.setdefault(i, []).append(j)
    return out


def candidates(line, toks, at):
    """Every window of the transcript that says this line or a start of it: (cov, matched, first, last, complete)."""
    L, out, seen = line["toks"], [], set()
    slack = max(2, len(L) // 4)
    for k, t in enumerate(L):
        for p in at.get(t, ()):
            s = max(0, p - k)
            if s in seen:
                continue
            seen.add(s)
            win = [x[0] for x in toks[s:s + len(L) + slack]]
            blocks = [b for b in difflib.SequenceMatcher(a=L, b=win, autojunk=False).get_matching_blocks() if b.size]
            got = sum(b.size for b in blocks)
            if got < MIN_WORDS or got < PARTIAL * len(L):
                continue
            first, last = s + blocks[0].b, s + blocks[-1].b + blocks[-1].size - 1
            ends = blocks[-1].a + blocks[-1].size == len(L)
            out.append((got / len(L), got, first, last, got >= COMPLETE * len(L) and ends))
    return out


def match(script_text, words):
    """{"lines", "takes", "short", "off", "twice"}: takes are dicts {line, alt, first, last, start, end, quote,
    complete, keeper}, line being the script line index the take was given to."""
    lines, toks = parse_script(script_text), timed(words)
    at = {}
    for i, t in enumerate(toks):
        at.setdefault(t[0], []).append(i)
    twice = say_twice(lines)
    cands = [(c, li) for li, line in enumerate(lines) if len(line["toks"]) >= MIN_WORDS for c in candidates(line, toks, at)]
    cands.sort(key=lambda x: (-x[0][0], -x[0][1], x[0][3] - x[0][2]))   # best cover, then most words, then tightest
    takes = []
    for (cov, got, first, last, complete), li in cands:
        hit = next((t for t in takes if first <= t["last"] and t["first"] <= last), None)
        if hit:   # the same stretch: an identical script line is another home for that take, anything else loses
            if li in twice.get(hit["line"], ()) and li not in hit["alt"] and abs(cov - hit["cov"]) < 1e-9:
                hit["alt"].append(li)
            continue
        takes.append({"line": li, "alt": [li], "first": first, "last": last, "cov": cov, "complete": complete})
    takes.sort(key=lambda t: t["first"])
    # A take of a line the script says twice: first the copies whose script neighbours bracket it in time.
    # Then, in a run of such takes, walking back from the end, each complete take moves to the copy before:
    # the last copy gets the last take, the first copy every earlier take (its retakes).
    # ponytail: retakes of a LATER copy land on the copy before it; every copy still keeps a take of its own
    uniq = [o for o in takes if len(o["alt"]) == 1]

    def fit(t, li):
        prev = [o for o in uniq if li > o["line"] and o["line"] not in t["alt"]]
        nxt = [o for o in uniq if li < o["line"] and o["line"] not in t["alt"]]
        p, q = max((o["line"] for o in prev), default=None), min((o["line"] for o in nxt), default=None)
        return ((p is None or any(o["line"] == p and o["last"] < t["first"] for o in prev))
                + (q is None or any(o["line"] == q and o["first"] > t["last"] for o in nxt)))
    amb = [t for t in takes if len(t["alt"]) > 1]
    for t in amb:
        best = max(fit(t, li) for li in t["alt"])
        t["alt"] = sorted(li for li in t["alt"] if fit(t, li) == best)
    runs = []
    for t in amb:
        if runs and runs[-1][-1]["alt"] == t["alt"] and not any(runs[-1][-1]["first"] < o["first"] < t["first"] for o in uniq):
            runs[-1].append(t)
        else:
            runs.append([t])
    for r in runs:
        cur = len(r[0]["alt"]) - 1
        for t in reversed(r):
            t["line"] = t["alt"][cur]
            if t["complete"] and cur:
                cur -= 1
    for t in takes:
        a, b = toks[t["first"]], toks[t["last"]]
        t.update(start=a[1], end=b[2], quote=" ".join(words[i]["text"].strip() for i in range(a[3], b[3] + 1)
                                                     if words[i].get("type", "word") == "word"), keeper=False)
        del t["alt"]
    for li in range(len(lines)):
        done = [t for t in takes if t["line"] == li and t["complete"]]
        if done:
            done[-1]["keeper"] = True
    covered = set()
    for t in takes:
        covered.update(range(t["first"], t["last"] + 1))
    off, run = [], []
    for i in range(len(toks) + 1):
        if i < len(toks) and i not in covered:
            run.append(i)
            continue
        if len(run) >= OFF_MIN:
            a, b = toks[run[0]], toks[run[-1]]
            off.append({"start": a[1], "end": b[2], "quote": " ".join(words[k]["text"].strip() for k in range(a[3], b[3] + 1)
                                                                       if words[k].get("type", "word") == "word")})
        run = []
    short = [li for li, line in enumerate(lines) if len(line["toks"]) < MIN_WORDS]
    return {"lines": lines, "takes": takes, "short": short, "off": off, "twice": twice}


def report(m, name):
    lines, takes = m["lines"], m["takes"]
    out = [f"# Script match: {name}", "",
           f"{len(lines)} script lines, {len(takes)} takes found, {len(m['twice'])} lines the script says twice.",
           "Keeper = the last complete take of a line. A say-twice line keeps its own keeper: the script says it",
           "again on purpose, so a take that matches a separate script line is never a retake. Off-script words are",
           "never cut for being off-script. This file writes nothing else: spans.json is still yours.", ""]
    for li, line in enumerate(lines):
        who = f"{line['speaker']}: " if line["speaker"] else ""
        out.append(f"## {line['n']}. {who}{line['text']}")
        if li in m["twice"]:
            out.append("say-twice (also line " + ", ".join(str(lines[j]["n"]) for j in m["twice"][li]) +
                       "): keep every take that matches a separate script line")
        if li in m["short"]:
            out.append("too short to match by words: find it by ear in transcript.txt")
        mine = [t for t in takes if t["line"] == li]
        for t in mine:
            tag = "KEEPER" if t["keeper"] else "complete, earlier take" if t["complete"] else "partial"
            out.append(f"- {t['start']:.2f}-{t['end']:.2f} \"{t['quote']}\" {tag}")
        if li not in m["short"] and not mine:
            out.append("- not found in the transcript")
        elif mine and not any(t["keeper"] for t in mine):
            out.append("- no complete take: check the last partial by ear")
        out.append("")
    out.append("## Off-script (not cut for that reason)")
    out += [f"- {o['start']:.2f}-{o['end']:.2f} \"{o['quote']}\"" for o in m["off"]] or ["- none"]
    return "\n".join(out) + "\n"


def run(d, script=None):
    d = Path(d)
    sp = Path(script) if script else d / "script.md"
    m = match(sp.read_text(encoding="utf-8"), textnorm.load_words(d / "words.raw.json"))
    (d / "script-match.md").write_text(report(m, d.name), encoding="utf-8")
    print(f"{d / 'script-match.md'}: {len(m['lines'])} lines, {len(m['takes'])} takes, "
          f"{len(m['twice'])} say-twice, {len(m['off'])} off-script")
    return m


def demo():
    script = """# Manager vs Intern: the database

**MANAGER:** We need to scale this to a million users.
INTERN: Okay so what do we do first?
[Manager stares]
MANAGER: Add a cache in front of the database.
INTERN (whispering): Add a cache in front of the database.
MANAGER: Exactly. Now ship it.
INTERN: Yes.
"""
    said = [("we need to scale this to a million users", 0.0),
            ("okay so what do we do first", 4.0),
            ("add a cache in front of the", 8.0),             # false start
            ("add a cache in front of the database", 11.0),   # earlier complete take: not the keeper
            ("um hang on let me get the coffee", 15.0),       # off-script
            ("add a cache in front of the database", 19.0),   # manager's keeper
            ("add a cache in front of the database", 23.0),   # the intern repeats it: a separate line, kept
            ("exactly now ship it", 27.0), ("yes", 30.0)]
    words = []
    for text, t in said:
        for k, w in enumerate(text.split()):
            words.append({"text": w, "start": round(t + 0.3 * k, 2), "end": round(t + 0.3 * k + 0.25, 2), "type": "word"})
            words.append({"text": " ", "start": 0, "end": 0, "type": "spacing"})
    m = match(script, words)
    L = m["lines"]
    assert [x["speaker"] for x in L] == ["MANAGER", "INTERN", "MANAGER", "INTERN", "MANAGER", "INTERN"], L
    assert L[3]["text"] == "Add a cache in front of the database." and m["twice"] == {2: [3], 3: [2]}, m["twice"]
    by = {}
    for t in m["takes"]:
        by.setdefault(t["line"], []).append(t)
    cache = by[2]
    assert [round(t["start"]) for t in cache] == [8, 11, 19], [(t["start"], t["complete"]) for t in cache]
    assert not cache[0]["complete"] and cache[1]["complete"] and not cache[1]["keeper"] and cache[2]["keeper"], cache
    assert len(by[3]) == 1 and round(by[3][0]["start"]) == 23 and by[3][0]["keeper"], by.get(3)
    assert by[0][0]["keeper"] and by[4][0]["keeper"] and 5 not in by and m["short"] == [5], (by, m["short"])
    assert [o["quote"] for o in m["off"]] == ["um hang on let me get the coffee"], m["off"]
    md = report(m, "skit")
    assert "say-twice (also line 7): keep every take that matches a separate script line" in md, md
    assert '- 19.00-21.35 "add a cache in front of the database" KEEPER' in md, md
    assert '- 11.00-13.35 "add a cache in front of the database" complete, earlier take' in md, md
    assert '- 23.00-25.35 "add a cache in front of the database" KEEPER' in md, md
    assert "## Off-script (not cut for that reason)" in md and "spans.json is still yours" in md and "\u2014" not in md
    print("script_match demo ok")


def main():
    a = sys.argv[1:]
    if a == ["demo"]:
        return demo()
    if len(a) == 1:
        return run(a[0])
    if len(a) == 3 and a[1] == "--script":
        return run(a[0], a[2])
    sys.exit(__doc__)


if __name__ == "__main__":
    main()
