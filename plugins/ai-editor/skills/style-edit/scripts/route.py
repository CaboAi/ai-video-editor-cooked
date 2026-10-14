#!/usr/bin/env python3
"""Prepare the visuals cheaply: the video's sentences with the names in each, so Claude only fills small template JSON.

    python3 route.py beats edits/NAME            captions.json -> edits/NAME/beats.json
    python3 route.py highlight edits/NAME images/capture-2-x.text.json "what the speaker says there"
    python3 route.py demo                        self-check (offline)

beats: code splits captions.json into sentences (times kept in code), marks the profile's named
products, tools and people found in each, plus any word written with a capital inside a sentence (a
product or person the profile does not list: "Claude", "Haiku"). Every "pick" is null: Claude (or the
template-filler agent) routes each sentence and writes visuals.json. When edits/NAME/style.json carries the
creator's graphics.kinds, her mix is printed for Claude to lean towards. With her card rate (graphics.per_min,
under the user's own cards cap), her count for this length is printed and that many sentences get "want": true
(want()): named ones first, then the longest, spread out. Claude gives each a real card at her place in the frame.

logo-pack: when edits/NAME/logos.json exists, its names count as named things and each sentence gets "logos"
(the brands whose files logo-pack checked, for a `logo` beat's "brand"); two or more in one sentence print a
`logo_cluster` hint.

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
# A text card is never built (type cards were removed): it routes to a sticker of the real sentence, or, in a
# card edit (style.json "card_kinds" without "text"), to a shot of the real page.
KIND_ROUTES = {"real screenshot": ("capture:shot", "capture:sticker"), "UI recording": ("capture:browser", "video_card"),
               "text card": ("capture:sticker",), "chart": ("capture:sticker",), "photo": ("capture:shot",),
               "meme": ("capture:shot",), "logo": ("logo", "logo_cluster"), "b-roll": ("video_card",),
               "animation": ("likes", "progress", "notes", "terminal", "chat", "toasts", "curve", "side_by_side", "flow")}
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


def logo_hits(text, logos):
    """logo-pack's logos (logos.json) named in a sentence -> their brand slugs, for a logo beat's "brand"."""
    return [l.get("brand") or l["name"] for l in logos if named(text, [l])]


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
    from plan import graphics, pictures_only
    out = {}
    for k, v in (graphics(style or {}, "graphics").get("kinds") or {}).items():
        rs = KIND_ROUTES.get(k, ())
        if pictures_only(style or {}):   # a card edit: no sentence on white, the page itself instead
            rs = tuple(dict.fromkeys("capture:shot" if r == "capture:sticker" else r for r in rs))
        for r in rs:
            out[r] = round(out.get(r, 0) + v / 100 / len(rs), 3)
    return out


def want(beats, style):
    """Marks "want": true on as many beats as the creator shows cards in a video this long (plan.card_rate: her
    graphics.per_min under the user's own cards cap),
    named sentences first, then the longest, never two closer than half her mean gap, none ending in the first
    second. Returns (n wanted, her per_min); (0, None) when the style copies no card rate."""
    from plan import card_rate
    pm = card_rate(style or {})
    if not pm or not beats:
        return 0, None
    n = max(1, round(beats[-1]["end"] / 60 * pm))
    gap = 30 / pm
    picked = []
    for b in sorted(beats, key=lambda b: (not b.get("names"), -(b["end"] - b["start"]), b["start"])):
        if len(picked) < n and b["end"] > 1.0 and all(abs(b["start"] - x["start"]) >= gap for x in picked):
            picked.append(b)
    for b in beats:
        b["want"] = b in picked
    return n, pm


def where(style):
    """Her place in the frame, one phrase: the zone she uses most (graphics.layout.zones_pct)."""
    from plan import graphics
    z = (graphics(style or {}, "layout").get("layout") or {}).get("zones_pct") or {}
    top = max(z, key=z.get) if z else None
    return f"{top} of the frame ({z[top]}% of hers)" if top else None


def rank_highlight(blocks, said):
    """A captured page's sentences, the ones sharing the most words with `said` first."""
    # one sentence per option: the highlight sweeps a sentence, not a paragraph
    blocks = [x for b in blocks for x in re.split(r"(?<=[.!?])\s+", b) if len(x) > 15]
    toks = set(re.findall(r"[a-z0-9]+", said.lower()))
    return sorted(blocks, key=lambda b: -len(toks & set(re.findall(r"[a-z0-9]+", b.lower()))))[:MAX_LINES]


NUM = re.compile(r"\b\d[\d,.]*\b|\b(?:hundred|thousand|million|billion|percent|half|double|twice|times)\b", re.I)
STEPS = re.compile(r"\b(?:first|second|third|then|next|finally|step)\b", re.I)
VERSUS = re.compile(r"\b(?:before|after|instead|versus|vs|used to|compared|better than|worse than|faster than)\b", re.I)
FLOWS = re.compile(r"\b(?:sends?|goes to|passes|hands?|from \w+ to|into|pipeline|workflow|connects?)\b", re.I)
UI = re.compile(r"\b(?:drag|click|type|upload|render(?:s|ing)?|cut(?:s|ting)?|open|install|run(?:s|ning)?|press)\b", re.I)


def motion_kind(text):
    """What a sentence is doing, for the motion skill (style.json "motion" kinds): a number said, steps or a list of
    three or more, a comparison, things handing work along, an app being used. None: nothing to animate."""
    if VERSUS.search(text):
        return "before_after"
    if NUM.search(text):
        return "counter"
    if len(STEPS.findall(text)) >= 2 or text.count(",") >= 2:
        return "list"
    if FLOWS.search(text):
        return "flow"
    if UI.search(text):
        return "ui_demo"
    return None


def demo():
    ws = [{"text": t, "start": i * 0.4, "end": i * 0.4 + 0.3} for i, t in
          enumerate("I built a router with Jev. It is 40 times faster than Claude. Follow me.".split())]
    bs = sentences(ws)
    assert [b["text"] for b in bs] == ["I built a router with Jev.", "It is 40 times faster than Claude.", "Follow me."]
    assert [motion_kind(b["text"]) for b in bs] == [None, "before_after", None]
    assert motion_kind("over 10,000 people asked for it") == "counter" and motion_kind("first cut it, then render it") == "list"
    assert motion_kind("you can drag in a video") == "ui_demo" and motion_kind("it sends the take to the judge") == "flow"
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
    # logo-pack's logos.json: names map to the brand slug its files are saved under
    assert logo_hits("Jev picks my Claude model", [{"name": "Jev", "brand": "typesafe"}, {"name": "Claude"}]) == ["typesafe", "Claude"]
    # her mix: text cards route to the sticker, split by her shares
    pr = prefer({"graphics": {"kinds": {"text card": 55, "real screenshot": 40, "chart": 3}}})
    assert pr == {"capture:sticker": 0.78, "capture:shot": 0.2}, pr
    assert prefer({"graphics": {"kinds": {"chart": 100}}, "blend": {"take": ["captions"]}}) == {}
    # a card edit (card_kinds without "text"): the text card's share goes to a shot of the real page
    pc = prefer({"graphics": {"kinds": {"text card": 50, "real screenshot": 50}}, "card_kinds": ["capture", "logo"]})
    assert pc == {"capture:shot": 1.0}, pc
    # her rate: 6 a minute over 30 s = 3 cards; the named sentence first, never two within 5 s, none in the first second
    wb = [{"start": t, "end": t + d, "names": nm} for t, d, nm in
          ((0.0, 0.9, ["Jev"]), (2.0, 2.0, []), (4.0, 3.0, []), (9.0, 1.0, ["Claude"]), (14.0, 4.0, []), (25.0, 5.0, []))]
    assert want(wb, {"graphics": {"per_min": 6}}) == (3, 6)
    assert [b["start"] for b in wb if b["want"]] == [9.0, 14.0, 25.0], wb
    assert want(wb, {"graphics": {"per_min": 6}, "blend": {"take": ["captions"]}}) == (0, None)
    assert want(wb, {"graphics": {"per_min": 6}, "cards": {"per_min": 2}}) == (1, 2)   # the user's cap wins
    assert where({"graphics": {"layout": {"zones_pct": {"top": 84, "bottom": 12}}}}) == "top of the frame (84% of hers)"
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
        from ai_editor import review   # the review page's "Making the edit" bar, when it waits on this edit
        review.step(edit, "visuals")
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
        lp = edit / "logos.json"   # the logo-pack skill's checked logos
        logos = json.loads(lp.read_text(encoding="utf-8")).get("logos", []) if lp.exists() else []
        extra += [l["name"] for l in logos if l["name"].lower() not in have and l["name"] not in extra]
        for b in beats:
            b["names"] = named(b["text"], names + [{"name": w} for w in extra])
            if logos:
                b["logos"] = logo_hits(b["text"], logos)
        st = edit / "style.json"
        from plan import effective_style   # the style the plan will use: the user's choices over the creator's
        style = effective_style(st, prof, edit, count=False) if st.exists() else {}
        from plan import pictures_only
        if pictures_only(style):
            print(f"this style's cards ({', '.join(style['card_kinds'])}): mostly animations of what is said (likes, "
                  "progress, notes, terminal, chat, toasts, curve, side_by_side with arrow, flow), a capture of a real page "
                  "only where the page is the point (one capture per page; browser to scroll it), a logo, the user's photo. "
                  "A title card at the open, a cta on the ask. Never a sticker or words on a ground (shapes.md \"Card edit\")")
        pref = prefer(style)
        if pref:
            print("the creator's mix: " + ", ".join(f"{k} {v:.0%}" for k, v in sorted(pref.items(), key=lambda kv: -kv[1])))
        n, pm = want(beats, style)
        if n:
            print(f"the creator's rate: {pm:g} cards a minute, so {n} in this video; \"want\" marks the sentences to give one"
                  + (f", placed at the {where(style)}" if where(style) else "") + " (SKILL.md step 3, \"Pacing\")")
        for b in beats:
            b["pick"] = None   # Claude routes each sentence
            b["motion"] = motion_kind(b["text"])   # a hint: the motion skill builds a clip where the pick is a moving format
        (edit / "beats.json").write_text(json.dumps(beats, indent=1), encoding="utf-8")
        for b in beats:
            print(f"{b['start']:6.2f} {'*' if b.get('want') else ' '} {str(b.get('motion') or ''):12}  {b['text']}"
                  + (f"   [{', '.join(b['names'])}]" if b["names"] else "")
                  + (f"   logo_cluster? {', '.join(b['logos'])}" if len(b.get("logos") or []) > 1 else ""))
        print(f"-> {edit / 'beats.json'}")
    elif len(a) == 4 and a[0] == "highlight":
        blocks = json.loads((Path(a[1]) / a[2]).read_text(encoding="utf-8") if not Path(a[2]).exists() else Path(a[2]).read_text(encoding="utf-8"))
        print("pick the evidence line, closest first:\n" + "\n".join(f"- {b}" for b in rank_highlight(blocks, a[3])))
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
