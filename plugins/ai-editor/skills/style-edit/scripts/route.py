#!/usr/bin/env python3
"""Prepare the visuals cheaply: the video's sentences with the names in each, so Claude only fills small template JSON.

    python3 route.py beats edits/NAME            captions.json -> edits/NAME/beats.json
    python3 route.py highlight edits/NAME images/capture-2-x.text.json "what the speaker says there"
    python3 route.py demo                        self-check (offline)

beats: code splits captions.json into sentences (times kept in code), marks the profile's named
products, tools and people found in each, plus any word written with a capital inside a sentence (a
product or person the profile does not list: "Claude", "Haiku"). Every "pick" is null: Claude (or the
template-filler agent) routes each sentence and writes visuals.json. When edits/NAME/style.json carries the
creator's graphics.kinds, her mix is printed for Claude to lean towards.

highlight: the capture's text blocks (capture.mjs "page_text": true) and the spoken line in; its sentences
out, the ones sharing the most words with the spoken line first, for Claude to pick the evidence from.
Stdlib only.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

# graphics.kinds (creator-teardown style.json) -> the routes that show that kind of thing from a real source.
# A text card is never built (type cards were removed): it routes to a sticker of the real sentence.
KIND_ROUTES = {"real screenshot": ("capture:shot", "capture:sticker"), "UI recording": ("capture:browser", "video_card"),
               "text card": ("capture:sticker",), "chart": ("capture:sticker",), "photo": ("capture:shot",),
               "meme": ("capture:shot",), "logo": ("logo", "logo_cluster"), "b-roll": ("video_card",)}
MAX_LINES = 30   # sentences printed for a highlight, nearest the spoken words first


def sentences(words):
    """[{i, text, start, end, words}] split at . ? ! and at pauses over 0.8 s."""
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or w["text"].rstrip().endswith((".", "?", "!")) or nxt["start"] - w["end"] > 0.8:
            out.append({"i": len(out), "text": " ".join(x["text"] for x in cur).strip(),
                        "start": cur[0]["start"], "end": cur[-1]["end"]})
            cur = []
    return out


def named(text, names):
    """The profile's names that appear in a sentence, matched on whole words, case-insensitive."""
    low = f" {re.sub(r'[^a-z0-9 ]+', ' ', text.lower())} "
    return [n["name"] for n in names if f" {re.sub(r'[^a-z0-9 ]+', ' ', n['name'].lower()).strip()} " in low]


NOT_NAMES = {"i", "i'm", "i'll", "i've", "i'd", "ai", "ok"}
# Common words a transcriber capitalises where it heard a new sentence but wrote no full stop ("every day,
# This is", "hard Can you"). ponytail: a fixed stopword list; a name that is also a common word ("Notion",
# "Linear") is still found, a common word missing here still shows up.
COMMON = set("""a about after again all also am an and any are as at back be because been before being both but by
can can't could did didn't do does doesn't don't down each even every first for from get go going gonna got had has
have he her here hey him his how if in into is isn't it it's its just know let let's like look make maybe me more
most my new next no not now of oh okay on once one only or other our out over really right said say says see she
should so some still such sure take than thank thanks that that's the their them then there there's these they
they're thing things think this those though through to today too try two um uh up us use very want was way we
we're well were what what's when where which while who why will with would yeah yes yet you you'll you're your
check""".split())


def found_names(beats):
    """Words written with a capital letter anywhere but a sentence's first word: the products, tools and
    people said, when the profile's names question was left blank. ponytail: a capital-letter heuristic,
    so it relies on the transcriber's casing (Whisper and Scribe capitalise names); a lower-case
    transcript finds none."""
    out = []
    for b in beats:
        for w in re.findall(r"[A-Za-z][\w'-]*", b["text"])[1:]:
            w = w.strip("'-")
            if w[:1].isupper() and w.lower() not in NOT_NAMES | COMMON and w not in out:
                out.append(w)
    return out


def prefer(style):
    """{route: 0-1}: the creator's graphics.kinds mix, each kind's share split over its routes. {} when the
    style has none or the blend did not take "graphics"."""
    from plan import graphics
    out = {}
    for k, v in (graphics(style or {}, "graphics").get("kinds") or {}).items():
        rs = KIND_ROUTES.get(k, ())
        for r in rs:
            out[r] = round(out.get(r, 0) + v / 100 / len(rs), 3)
    return out


def rank_highlight(blocks, said):
    """A captured page's sentences, the ones sharing the most words with `said` first."""
    # one sentence per option: the highlight sweeps a sentence, not a paragraph
    blocks = [x for b in blocks for x in re.split(r"(?<=[.!?])\s+", b) if len(x) > 15]
    toks = set(re.findall(r"[a-z0-9]+", said.lower()))
    return sorted(blocks, key=lambda b: -len(toks & set(re.findall(r"[a-z0-9]+", b.lower()))))[:MAX_LINES]


def demo():
    ws = [{"text": t, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, t in
          enumerate("I built a router with Jev. It is 40 times faster than Claude. Follow me.".split())]
    bs = sentences(ws)
    assert [b["text"] for b in bs] == ["I built a router with Jev.", "It is 40 times faster than Claude.", "Follow me."]
    names = [{"name": "Jev"}, {"name": "Claude"}, {"name": "Claude Code"}]
    for b in bs:
        b["names"] = named(b["text"], names)
    assert bs[0]["names"] == ["Jev"] and bs[1]["names"] == ["Claude"] and bs[2]["names"] == []
    # the names question left blank: the sample take's own capitalised names are found, sentence starts are not
    sample = sentences([{"text": t, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, t in enumerate(
        "I built a router with Jev that picks my Claude model. Jev is good at different things. If it's a small "
        "task, it'll automatically use the Haiku model. If it's harder, it'll use Opus. I'm sure AI helps.".split())])
    assert found_names(sample) == ["Jev", "Claude", "Haiku", "Opus"], found_names(sample)
    # the real-footage test: Whisper capitalised sentence starts it wrote no full stop before, and every one
    # read as a name ("YouTube, This, Can, It, And, You, Just, So")
    run_on = sentences([{"text": t, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, t in enumerate(
        "I edit my YouTube videos. So because it is open source, This is a new view and be like Oh, I don't like "
        "it Can you change it and It saves and You can just see it So you can add reels. And Just send it to Claude".split())])
    assert found_names(run_on) == ["YouTube", "Claude"], found_names(run_on)
    assert named(sample[1]["text"], [{"name": n} for n in found_names(sample)]) == ["Jev"]
    # her mix: text cards route to the sticker, split by her shares
    pr = prefer({"graphics": {"kinds": {"text card": 55, "real screenshot": 40, "chart": 3}}})
    assert pr == {"capture:sticker": 0.78, "capture:shot": 0.2}, pr
    assert prefer({"graphics": {"kinds": {"chart": 100}}, "blend": {"take": ["captions"]}}) == {}
    hl = rank_highlight(["Pricing starts at $5.", "It answers in 40 ms, 200x faster than a frontier model."],
                        "it's 40 to 200 times faster")
    assert hl[0].startswith("It answers") and len(hl) == 2, hl
    print("route ok")


def main():
    a = sys.argv[1:]
    if a == ["demo"]:
        return demo()
    if len(a) == 2 and a[0] == "beats":
        edit = Path(a[1])
        src = edit / "captions.json" if (edit / "captions.json").exists() else edit / "words.json"
        words = [w for w in json.loads(src.read_text(encoding="utf-8")) if w.get("type", "word") == "word" and w["text"].strip()]
        from ai_editor import profile
        prof = profile.load()
        beats = sentences(words)
        names = [n if isinstance(n, dict) else {"name": n} for n in prof.get("names") or []]
        have = {n["name"].lower() for n in names}
        extra = [w for w in found_names(beats) if w.lower() not in have]
        if extra:
            print(f"names in the words, not in the profile: {', '.join(extra)} (add a domain for any that is not "
                  "famous: profile.py set names)")
        for b in beats:
            b["names"] = named(b["text"], names + [{"name": w} for w in extra])
        st = edit / "style.json"
        pref = prefer(json.loads(st.read_text(encoding="utf-8"))) if st.exists() else {}
        if pref:
            print("the creator's mix: " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(pref.items(), key=lambda kv: -kv[1])))
        for b in beats:
            b["pick"] = None   # Claude routes each sentence
        (edit / "beats.json").write_text(json.dumps(beats, indent=1), encoding="utf-8")
        for b in beats:
            print(f"{b['start']:6.2f}  {str(b.get('pick')):20}  {b['text']}" + (f"   [{', '.join(b['names'])}]" if b["names"] else ""))
        print(f"-> {edit / 'beats.json'}")
    elif len(a) == 4 and a[0] == "highlight":
        blocks = json.loads((Path(a[1]) / a[2]).read_text(encoding="utf-8") if not Path(a[2]).exists() else Path(a[2]).read_text(encoding="utf-8"))
        print("pick the evidence line, closest first:\n" + "\n".join(f"- {b}" for b in rank_highlight(blocks, a[3])))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
