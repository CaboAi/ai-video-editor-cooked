#!/usr/bin/env python3
"""Style check: does the plan look like the copied creator? plan.py runs it after every plan.

    python3 style_check.py edits/NAME [--plan plan.json] [--style edits/NAME/style.json]
    python3 style_check.py demo      self-check

Compares plan.json with the style it was planned from (style.json with the user's taste.json, this style's
corrections and this video's settings laid over, as plan.py reads it, so a change the user asked for is the
target, not a mismatch): caption size, place, words a caption, case, font and plain look; zooms a minute, kind and
scale; cards a minute, their kinds and where they sit; sound cues and camera pans a minute. A part the blend did
not take is not compared. Prints one `style:` line per mismatch with the fix, then a count. Exit 0 always: the
lines are for Claude to fix before the stills (SKILL.md step 4). Stdlib only.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from plan import SAFE, card_rate, graphics, took  # noqa: E402

# a plan card -> the creator-teardown graphics.kinds it counts as
CARD_KIND = {"shot": ("photo", "real screenshot", "meme"), "browser": ("UI recording", "real screenshot"),
             "sticker": ("text card", "chart", "real screenshot"), "plain": ("photo",), "logo": ("logo",),
             "logo_cluster": ("logo",), "logo_sting": ("logo",), "video_card": ("b-roll", "UI recording"),
             "social_post": ("real screenshot",), "post": ("real screenshot",)}


def card_kind(c):
    a = c.get("anim") or {}
    return c.get("format") or a.get("format") or a.get("type") or "shot"


def zone(c):
    if c.get("layout") == "scene":
        return "full"
    x, y, w, h = c.get("box") or [0, 0, 100, 100]
    mid = y + h / 2
    return "top" if mid < 100 / 3 else "bottom" if mid > 200 / 3 else "middle"


def per_min(n, dur):
    return round(n / dur * 60, 1) if dur else 0


def off(got, want, dur):
    """True when a count over `dur` s is clearly off the creator's rate: outside half to double and at least
    one whole item away (a 20 s clip cannot show 1.3 a minute exactly)."""
    exp = want * dur / 60
    return abs(got - exp) >= 1 and not (exp / 2 <= got <= exp * 2 + 0.5)


def check(plan, style):
    """[(what, fix)] mismatches between the plan and the style it was planned from."""
    out = []
    h = "@" + str(style.get("handle") or "the creator")
    dur = plan["durationInFrames"] / plan["fps"]
    cs, chunks = plan["captions"]["style"], plan["captions"]["chunks"]
    want = style.get("captions") or {}
    if want and want.get("present") is not False and took(style, "captions"):
        if want.get("size_pct") and abs((cs.get("size_pct") or 6) - want["size_pct"]) > 0.3:
            out.append((f"caption size {cs.get('size_pct')}%, {h} {want['size_pct']}%", "set captions.size_pct in style.json"))
        _, top, _, bottom = SAFE["9:16" if plan.get("height", 1920) > plan.get("width", 1080) else "16:9"]
        y = min(max(want.get("y_pct") or 70, top + 5), 100 - bottom - 4)   # plan.py keeps them clear of the app's UI
        if abs((cs.get("y_pct") or 70) - y) > 3 and plan.get("layout", {}).get("mode") != "split":
            out.append((f"captions {cs.get('y_pct')}% down, {h} {want.get('y_pct')}%", "set captions.y_pct"))
        if want.get("words_per_caption") and chunks:
            n = sorted(len(c["text"].split()) for c in chunks)[len(chunks) // 2]
            if abs(n - want["words_per_caption"]) > 1:
                out.append((f"{n} words a caption, {h} {want['words_per_caption']}", "set captions.words_per_caption"))
        if want.get("case") and cs.get("case") != want["case"]:
            out.append((f"caption case {cs.get('case')}, {h} {want['case']}", "set captions.case"))
        if not want.get("font_match"):
            out.append((f"{h}'s caption font is not named, so captions use the default font",
                        "run creator-teardown's look pass (gemini.py look) or set captions.font_match to the Google Font nearest hers"))
        elif cs.get("font_match") != want["font_match"]:
            out.append((f"caption font {cs.get('font_match')}, {h} {want['font_match']}", "set captions.font_match"))
        if want.get("stroke") is False and want.get("box") is False and chunks:
            heavy = sum(c.get("treat") in ("stroke", "backing") for c in chunks)
            if heavy > len(chunks) / 4:
                out.append((f"{heavy} of {len(chunks)} caption pages got a stroke or backing for contrast; {h}'s are plain",
                            "the footage behind them is bright; keep it unless a darker spot near her y_pct exists"))
    z = style.get("zoom") or {}
    zs = plan.get("zooms") or []
    if z.get("per_min") is not None and off(len(zs), z["per_min"], dur):
        out.append((f"{per_min(len(zs), dur)} zooms a minute, {h} {z['per_min']}", "plan again; zoom.per_min sets it"))
    if zs and z.get("kind") and any(x.get("kind") != z["kind"] for x in zs):
        out.append((f"zoom kind {zs[0].get('kind')}, {h} {z['kind']}", "set zoom.kind"))
    g = graphics(style, "graphics")
    cards = plan.get("cards") or []
    rate = card_rate(style)   # her rate under the user's own cap
    if rate and off(len(cards), rate, dur):
        out.append((f"{len(cards)} cards ({per_min(len(cards), dur)} a minute), {h} {rate:g} a minute "
                    f"(about {round(rate * dur / 60)} here)",
                    "give a card to each beats.json sentence marked want (route.py), from real sources" if len(cards) <
                    rate * dur / 60 else "drop the weakest cards"))
    kinds = {k: v for k, v in (g.get("kinds") or {}).items() if v}
    if kinds and cards:
        have = {k for c in cards for k in CARD_KIND.get(card_kind(c), ())}
        miss = [k for k, v in kinds.items() if v >= 20 and k not in have]
        if miss:
            out.append((f"no {' or '.join(miss)} cards; {h}: " + ", ".join(f"{k} {v}%" for k, v in kinds.items()),
                        "show those kinds: the user's own photos (images.json) or a capture shot of the real thing said"))
        alien = sorted({card_kind(c) for c in cards if not any(kinds.get(k, 0) >= 5 for k in CARD_KIND.get(card_kind(c), ()))})
        if alien:
            out.append((f"{', '.join(alien)} cards, a kind {h} hardly uses", "swap them for her kinds where a real source exists"))
    zp = (graphics(style, "layout").get("layout") or {}).get("zones_pct") or {}
    if zp and cards:
        top = max(zp, key=zp.get)
        here = sum(zone(c) == top for c in cards)
        if zp[top] >= 50 and here < len(cards) / 2:
            out.append((f"{here} of {len(cards)} cards at the {top} of the frame, {h} puts {zp[top]}% there",
                        f"give those cards a box at the {top} (images.json / visuals.json \"box\"), clear of the head"))
    snd = (style.get("sound") or {}) if took(style, "sound") else {}
    if snd.get("sfx_per_min") is not None and style.get("sfx") is not False and "sfx" in plan \
            and off(len(plan["sfx"]), snd["sfx_per_min"], dur):
        out.append((f"{per_min(len(plan['sfx']), dur)} sound cues a minute, {h} {snd['sfx_per_min']}",
                    "fewer or more cues: sound.sfx_per_min in style.json"))
    cam = (style.get("camera") or {}) if took(style, "pace") else {}
    if cam.get("pan_per_min") and plan.get("layout", {}).get("mode") != "split" and off(len(plan.get("pans") or []), cam["pan_per_min"], dur):
        out.append((f"{per_min(len(plan.get('pans') or []), dur)} camera pans a minute, {h} {cam['pan_per_min']}", ""))
    return out


def report(found, style):
    h = "@" + str(style.get("handle") or "the creator")
    if style.get("handle") in (None, "default"):
        return "style check: the default style (no creator to compare with)"
    lines = [f"style: {w}" + (f" -> {f}" if f else "") for w, f in found]
    return "\n".join(lines + [f"style check: {len(found)} way(s) the plan differs from {h}; fix them before the stills"
                              if found else f"style check: the plan matches {h}"])


def demo():
    plan = {"durationInFrames": 600, "fps": 30, "captions": {"style": {"size_pct": 3.7, "y_pct": 51, "case": "lower",
            "font_match": "Oswald"}, "chunks": [{"text": "one two three"}] * 8},
            "zooms": [{"kind": "punch"}], "cards": [{"anim": {"type": "logo"}, "box": [40, 35, 20, 11]}], "sfx": [{}] * 2}
    style = {"handle": "h", "captions": {"size_pct": 3.7, "y_pct": 51, "case": "lower", "words_per_caption": 3,
             "font_match": "Oswald", "stroke": False, "box": False}, "zoom": {"per_min": 3, "kind": "punch"},
             "graphics": {"per_min": 10, "kinds": {"photo": 69, "b-roll": 29}, "layout": {"zones_pct": {"top": 84}}},
             "sound": {"sfx_per_min": 1.8}}
    found = [w for w, _ in check(plan, style)]
    assert found[0].startswith("1 cards (3.0 a minute), @h 10") and "about 3 here" in found[0], found
    assert any(w.startswith("no photo or b-roll cards") for w in found), found
    assert any(w.startswith("logo cards, a kind @h hardly uses") for w in found), found
    assert any(w.startswith("0 of 1 cards at the top") for w in found), found
    assert any(w.startswith("6.0 sound cues a minute") for w in found), found
    assert not any("caption" in w or "zoom" in w for w in found), found
    # the right plan: 3 photo shots at the top, plain captions, a cue: nothing to say
    plan["cards"] = [{"format": "shot", "box": [10, 12, 80, 20]}] * 2 + [{"anim": {"type": "video_card"}, "box": [10, 12, 80, 20]}]
    plan["sfx"] = [{}]
    assert check(plan, style) == [], check(plan, style)
    # a part the blend did not take is not compared; the user's change (in the merged style) is the target
    capped = check({**plan, "cards": plan["cards"][:1]}, {**style, "cards": {"per_min": 3}})
    assert not any(" cards (" in w for w, _ in capped), capped   # 1 card in 20 s is right under the user's 3 a minute
    assert check({**plan, "cards": []}, {**style, "blend": {"take": ["captions"]}}) == []
    plan["captions"]["chunks"] = [{"text": "a b c", "treat": "backing"}] * 8
    assert "plain" in check(plan, style)[0][0], check(plan, style)
    assert "matches @h" in report([], style) and "default style" in report([], {"handle": "default"})
    print("style_check ok")


def main():
    a = sys.argv[1:]
    if a == ["demo"]:
        return demo()
    if not a or a[0].startswith("-"):
        print(__doc__)
        sys.exit(2)
    edit = Path(a[0])
    flag = lambda k, d: a[a.index(k) + 1] if k in a and a.index(k) + 1 < len(a) else d
    plan = json.loads((edit / flag("--plan", "plan.json")).read_text(encoding="utf-8"))
    sys.path.insert(0, str(HERE.parents[2] / "lib"))
    from ai_editor import profile
    from plan import effective_style
    style = effective_style(Path(flag("--style", edit / "style.json")), profile.load(), edit, count=False)
    print(report(check(plan, style), style))


if __name__ == "__main__":
    main()
