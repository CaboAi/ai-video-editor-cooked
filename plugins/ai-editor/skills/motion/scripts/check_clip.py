#!/usr/bin/env python3
"""Static gate on a clip's index.html. Runs BEFORE lint, costs no render.

About half the gotchas in references/build-loop.md are patterns in the source,
not properties of the video, so they are decided here without spending a render.
It does NOT replace `lint` (timeline), `check` (layout), check_motion.py (the
render) or someone looking at the frames.

COMMENTS ARE STRIPPED FIRST: a bare grep once counted a `fromTo` that lived inside
a comment saying there was none. Newlines are kept so line numbers stay true.

Usage:
  python3 check_clip.py <clip-dir or index.html> [...]
  python3 check_clip.py demo

Exit codes: 0 no errors - 1 an error - 2 usage
Warnings never fail the run: they are smells that need a read.

Thresholds live in references/clip-brief-card.md. Change one here, change it there.
"""
import glob
import json
import os
import re
import sys


# Text is banned inside a clip (hard rule 1, escalated twice). A clip whose text
# IS the mechanism -- a real counter, a real balance, real code -- opts out with
# this marker in the HTML, naming what the text is.
OPT_OUT = "real-text:"

ATTRS = ["data-composition-id", "data-start", "data-duration",
         "data-width", "data-height"]


def strip_comments(src):
    """Blank out HTML, CSS/JS block and JS line comments, keeping line numbers."""
    def blank(m):
        return "\n" * m.group(0).count("\n")
    src = re.sub(r"<!--.*?-->", blank, src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", blank, src, flags=re.S)
    src = re.sub(r"(?<!:)//[^\n]*", "", src)      # (?<!:) spares https:// urls
    return src


def lines_of(src, pattern, flags=0):
    """[(lineno, matched text)] for a pattern."""
    return [(src[:m.start()].count("\n") + 1, m.group(0).strip())
            for m in re.finditer(pattern, src, flags)]


def check(raw, is_template=False, clip_id=None):
    """-> (errors, warnings), each a list of (line, message)."""
    src = strip_comments(raw)
    err, warn = [], []
    style = "".join(re.findall(r"<style>(.*?)</style>", src, flags=re.S))
    # A CSS attribute selector ([data-composition-id="root"]) is not the root
    # div. Look for attrs in the markup only.
    markup = re.sub(r"<style>.*?</style>", "", src, flags=re.S)

    # --- gotcha 14: the root needs all five data attrs, and duration must match
    for a in ATTRS:
        if f'{a}="' not in markup:
            err.append((0, f"root is missing {a} (lint fails with 4 errors)"))
    dur_attr = re.search(r'data-duration="([0-9.]+)"', markup)
    dur_var = re.search(r"var\s+DURATION\s*=\s*([0-9.]+)", src)
    if dur_attr and dur_var and abs(float(dur_attr[1]) - float(dur_var[1])) > 1e-6:
        err.append((0, f"data-duration {dur_attr[1]} != DURATION {dur_var[1]}"))

    cid = re.search(r'data-composition-id="([^"]+)"', markup)
    if cid:
        if cid[1] == "__ID__":
            if not is_template:
                err.append((0, "composition id is still the template's __ID__"))
        elif f'__timelines["{cid[1]}"]' not in src:
            err.append((0, f'__timelines key does not match id "{cid[1]}"'))

    # --- gotchas 5 + 12: a late fromTo needs immediateRender:false AND a CSS
    # from-state. Counts are a smell test, not a verdict -- hence a warning.
    ft = len(re.findall(r"\bfromTo\s*\(", src))
    ir = len(re.findall(r"immediateRender", src))
    if ft > ir:
        warn.append((0, f"{ft} fromTo vs {ir} immediateRender -- an unguarded "
                        "fromTo paints its from-state at t=0 (gotcha 5)"))
    if ft and not re.search(r"(opacity\s*:\s*0|visibility\s*:\s*hidden)", style):
        warn.append((0, "fromTo used but no from-state in the stylesheet -- the "
                        "element paints its authored state until the tween fires "
                        "(gotcha 12). Sample frame 1 to settle it."))

    # --- gotcha 12d: tl.set() is a fromTo in disguise, immediateRender defaults true
    for ln, txt in lines_of(src, r"\b(?:tl|gsap)\.set\s*\("):
        warn.append((ln, f"{txt} -- .set() paints at t=0 wherever it sits "
                         "(gotcha 12d)"))

    # --- gotcha 6: a function delay silently fails to schedule
    for ln, txt in lines_of(src, r"delay\s*:\s*(?:function|\(|[A-Za-z_$][\w$]*\s*=>)"):
        err.append((ln, f"{txt} -- GSAP has no function-based delay, the tween "
                        "never schedules (gotcha 6). Use the position parameter."))
    for ln, txt in lines_of(src, r"delay\s*:\s*[0-9.]+"):
        warn.append((ln, f"{txt} -- offset with the position parameter instead"))

    # --- gotcha 7: an infinite repeat is rejected
    for ln, txt in lines_of(src, r"repeat\s*:\s*-1"):
        err.append((ln, f"{txt} -- rejected, size the repeat to DURATION"))

    # --- gotcha 13: the retired fixed-offset drift table
    for ln, txt in lines_of(src, r"i\s*\*\s*0\.21|repeat\s*:\s*(?:5|15)\b(?=\s*[,}\n])"):
        err.append((ln, f"{txt} -- retired drift recipe, sized for a 14s clip. "
                        "Derive the period from the element's visible life "
                        "(gotcha 13)."))
    for ln, txt in lines_of(src, r"Math\.ceil\(\s*live"):
        err.append((ln, f"{txt} -- ceil overshoots the clip window by ~25%, "
                        "use round (gotcha 13)"))

    # --- gotcha 12c: a symmetric dasharray wraps and paints a dot at the far end.
    # Only a DRAW-ON path is affected. A decorative dashed line is symmetric on
    # purpose, so this stays quiet unless the file animates a dashoffset at all.
    draws = re.search(r"strokeDashoffset|stroke-dashoffset", src)
    if draws:
        for ln, txt in lines_of(src, r"stroke-dasharray\s*[:=]\s*[\"']?\s*([0-9.]+)"
                                     r"(?:[\s,]+([0-9.]+))?"):
            nums = re.findall(r"[0-9.]+", txt)
            sym = len(nums) == 1 or abs(float(nums[0]) - float(nums[-1])) < 1e-6
            if not sym:
                continue
            msg = (f"{txt} -- symmetric dasharray wraps and a round cap paints a "
                   "dot on the path end (gotcha 12c). Gap = 2x dash.")
            (err if float(nums[0]) >= 60 else warn).append((ln, msg))

    # --- the SVG transform-origin double-apply
    for ln, txt in lines_of(src, r"transform-box"):
        err.append((ln, f"{txt} -- double-applies with GSAP's SVG origin, a 6deg "
                        "rotation renders as a 35px slide. Let GSAP own the origin."))

    # --- eases: never back / elastic / expo
    for ln, txt in lines_of(src, r"ease\s*:\s*[\"'](back|elastic|expo)[^\"']*[\"']"):
        err.append((ln, f"{txt} -- banned ease. power2.out / power3.out / sine.inOut."))

    # --- entrance velocity: 0.7-0.9s, not 0.3s. Scoped to fromTo (entrances):
    # a 0.3s duration on a draw or a dissolve is not the rule this protects.
    for m in re.finditer(r"\bfromTo\s*\(", src):
        head = src[m.start():m.start() + 320]
        d = re.search(r"duration\s*:\s*(0\.[0-4]\d*)", head)
        if d:
            ln = src[:m.start()].count("\n") + 1
            warn.append((ln, f"entrance duration {d[1]}s -- under 0.5s reads as a "
                             "pop, entrances get 0.7-0.9s"))

    # --- hard rule 1: zero text inside a clip. A drawn glyph is the violation;
    # a leftover font link is only the plumbing for one, so it warns.
    if OPT_OUT not in raw:
        for ln, txt in lines_of(src, r"<(?:text|tspan)[\s>]"):
            err.append((ln, f"{txt} -- NO TEXT INSIDE A CLIP (hard rule 1). If the "
                            f"text IS the mechanism, mark it: <!-- {OPT_OUT} what -->"))
    if OPT_OUT not in raw:
        for ln, txt in lines_of(src, r"fonts\.googleapis|font-family\s*:"):
            warn.append((ln, f"{txt} -- font plumbing with no `real-text:` marker. Text is allowed only "
                             "where it IS the mechanism; mark it or drop the font."))
    return err, warn


def beat_clock(clip_dir):
    """Clock of the clip's beat.json (motion.py pick writes it): "cut" is the only legal one. Raw-take
    times in a brief cost rebuilds. None = no beat.json."""
    f = os.path.join(clip_dir, "beat.json")
    if not os.path.exists(f):
        return None
    try:
        return json.load(open(f, encoding="utf-8")).get("clock") or "missing"
    except (OSError, ValueError):
        return "missing"


def report(path):
    raw = open(path, encoding="utf-8").read()
    clip = os.path.basename(os.path.dirname(os.path.abspath(path)))
    err, warn = check(raw, is_template=clip.startswith("_"))
    if not clip.startswith("_"):
        clock = beat_clock(os.path.dirname(os.path.abspath(path)))
        if clock is None:
            warn.append((0, "no beat.json beside this clip -- its timings are unverified"))
        elif clock != "cut":
            err.append((0, f'beat clock is "{clock}", not "cut" -- these may be '
                           "RAW-TAKE times. Re-time to the rendered cut and set "
                           '"clock": "cut" on the beat before building.'))
    head = f"{clip}"
    if not err and not warn:
        print(f"  ok    {head}")
    else:
        print(f"  {'FAIL ' if err else 'warn '} {head}")
        for ln, msg in err:
            print(f"          ERROR {f'line {ln}' if ln else '       '}  {msg}")
        for ln, msg in warn:
            print(f"          warn  {f'line {ln}' if ln else '       '}  {msg}")
    return len(err)


def main(argv):
    if len(argv) < 2:
        sys.exit(__doc__)
    paths = []
    for a in argv[1:]:
        if os.path.isdir(a):
            paths += sorted(glob.glob(os.path.join(a, "index.html")))
        else:
            paths.append(a)
    if not paths:
        sys.exit("no index.html found")

    bad = 0
    for p in paths:
        bad += 1 if report(p) else 0
    print()
    print(f"{len(paths)} clip(s), {bad} with errors")
    if bad:
        print("Errors are render-wasters: fix before lint. Warnings need a read.")
    return 1 if bad else 0


def demo():
    """Self-check: the template passes clean, a planted bug is caught."""
    good = '''<div id="root" data-composition-id="x" data-start="0"
      data-duration="6.0" data-width="1080" data-height="1920">
      <style>.enter { opacity: 0; }</style>
      <script>var DURATION = 6.0;
      // fromTo without immediateRender, but only in a comment
      tl.fromTo("#a", {y: 40}, {y: 0, duration: 0.8, immediateRender: false}, 0.2);
      window.__timelines["x"] = tl;</script></div>'''
    err, warn = check(good)
    assert not err, err
    assert not warn, warn

    err, _ = check(good.replace("<style>", "<style>/*x*/") + "<text>hi</text>")
    assert any("NO TEXT" in m for _, m in err), "svg text missed"

    err, _ = check(good.replace("var DURATION = 6.0;", "var DURATION = 4.0;"))
    assert any("DURATION" in m for _, m in err), "duration mismatch missed"

    err, _ = check(good.replace("}, 0.2);", "}, {delay: function(i){return i}});"))
    assert any("function-based delay" in m for _, m in err), "delay missed"

    _, warn = check(good.replace("<style>", "<style>body { font-family: x; }"))
    assert any("font plumbing" in m for _, m in warn), "font link missed"

    _, warn = check(good.replace("immediateRender: false", "autoAlpha: 1"))
    assert any("immediateRender" in m for _, m in warn), "parity missed"

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert beat_clock(d) is None
        for doc, want in (({"clock": "cut"}, "cut"), ({}, "missing"), ({"clock": "raw"}, "raw")):
            json.dump(doc, open(os.path.join(d, "beat.json"), "w", encoding="utf-8"))
            assert beat_clock(d) == want, (doc, beat_clock(d))
    _, warn = check(good.replace("<style>", "<style>/* real-text: a counter */ body { font-family: x; }"))
    assert not any("font" in m for _, m in warn), warn
    print("demo ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] in ("demo", "--demo"):
        demo()
    else:
        sys.exit(main(sys.argv))
