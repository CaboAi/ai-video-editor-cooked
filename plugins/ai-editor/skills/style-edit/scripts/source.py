#!/usr/bin/env python3
"""Sourced proof for the edit: the second a public figure said a line, that clip, and the credits.

    source.py search "<query>" [--n 8]                       YouTube results: id, length, views, channel, title
    source.py quote <youtube url | .json3 file> "<phrase>" [--top 3] [--lang en]
                                                            where in the video the phrase is said
    source.py clip edits/NAME <youtube url> <start> <end>   only that section, 1080p max, into images/
    source.py credits edits/NAME                            credits.txt from every clip and post
    source.py demo                                          offline self-check
    (search, quote and clip take --cookies-from-browser <browser>, only after the user said yes)

All free, no keys. `quote` reads YouTube's auto-captions (yt-dlp, json3), which time every word:
good to about a quarter second, not verbatim, so the clip is padded and checked by ear. It prints
the `clip` line to run. `clip` writes images/clip-<id>-<start>.mp4 (a `video_card` src) and adds
the video's url at that second, channel, title and times to credits.json. X posts are a `post`
beat (capture.mjs, X's public embed endpoint, no login); `credits` adds each images/post-*.json.
Exit 3: YouTube wants a login or is rate limiting; ask before re-running with --cookies-from-browser.
"""
import json
import re
import subprocess
import sys
import tempfile
from difflib import SequenceMatcher
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "lib"))
from ai_editor.links import LOGIN, ONE, video_format, ytdlp  # noqa: E402

TIMEOUT = 300
PAD = 0.4     # caption word times run about a quarter second off: pad both ends
RATE = re.compile(r"\b429\b|too many requests|rate.?limit", re.I)


def norm(s):
    return re.sub(r"[^a-z0-9' ]+", " ", s.lower().replace("’", "'")).split()


def words_of(j):
    """[(start_s, word)] from a json3 caption file: auto-captions time each word (tOffsetMs)."""
    out = []
    for e in j.get("events", []):
        for s in e.get("segs") or []:
            for w in norm(s.get("utf8", "")):
                out.append(((e.get("tStartMs", 0) + s.get("tOffsetMs", 0)) / 1000, w))
    return out


def find(words, phrase, top=3):
    """Best windows of len(phrase) words by token similarity: [(score, start, end, text)], one per place."""
    p = norm(phrase)
    n = len(p)
    hits = []
    for i in range(max(1, len(words) - n + 1)):
        win = words[i:i + n]
        score = SequenceMatcher(None, [w for _, w in win], p).ratio()
        if score >= 0.6:
            end = words[i + n][0] if i + n < len(words) else win[-1][0] + 0.5
            hits.append((round(score, 2), win[0][0], end, " ".join(w for _, w in win)))
    hits.sort(key=lambda h: (-h[0], h[1]))
    picked = []
    for h in hits:
        if all(abs(h[1] - k[1]) > 3 for k in picked):
            picked.append(h)
        if len(picked) == top:
            break
    return picked


def ts(s):
    return f"{int(s // 60)}:{s % 60:05.2f}"


def die(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


def run_ytdlp(args, cookies=None):
    """yt-dlp with a hard timeout; a login or rate limit stops with exit 3 and the fix in plain words."""
    y = ytdlp() or die("yt-dlp is not installed. Run the setup skill, then try again.")
    cmd = y + ["--no-warnings", *args]
    if cookies:
        cmd[-1:-1] = ["--cookies-from-browser", cookies]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        die(f"yt-dlp did not finish in {TIMEOUT // 60} minutes. Try again, or pick a shorter video.")
    if r.returncode:
        err = (r.stderr.strip().splitlines() or ["no output"])[-1][:300]
        if RATE.search(r.stderr):
            die("YouTube is rate limiting this computer. Wait 10 minutes and run it once more. If it still fails, "
                "ask the user before re-running with --cookies-from-browser chrome (or their browser). " + err, 3)
        if LOGIN.search(r.stderr) or "403" in r.stderr:
            die("YouTube wants a login for this video (a bot check, private or age-gated). Ask the user before "
                "re-running with --cookies-from-browser chrome (or their browser). " + err, 3)
        die(f"yt-dlp failed: {err}")
    return r.stdout


def caption_words(src, lang="en", cookies=None):
    if Path(src).is_file():
        return words_of(json.loads(Path(src).read_text(encoding="utf-8")))
    with tempfile.TemporaryDirectory() as d:
        # <lang>-orig is the caption in the spoken language; plain <lang> can be a machine translation
        run_ytdlp(["--skip-download", "--write-auto-subs", "--sub-langs", f"{lang}-orig,{lang}", "--sub-format",
                   "json3", *ONE, "-o", (Path(d) / "c.%(ext)s").as_posix(), src], cookies)
        files = sorted(Path(d).glob("*.json3"), key=lambda f: "-orig" not in f.name)
        if not files:
            die(f"This video has no '{lang}' auto-captions. Pass --lang for the language spoken, or pick another upload.")
        return words_of(json.loads(files[0].read_text(encoding="utf-8")))


def add_credit(edit, entry):
    p = Path(edit) / "credits.json"
    items = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    items = [i for i in items if i.get("file") != entry["file"]] + [entry]
    p.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")


def clip(edit, url, start, end, cookies=None):
    img = Path(edit) / "images"
    img.mkdir(parents=True, exist_ok=True)
    tag = int(start)            # the same second as the credit url (?t=)
    out = run_ytdlp(["-f", video_format(1080), "--merge-output-format", "mp4", *ONE,
                     "--download-sections", f"*{start}-{end}", "--force-keyframes-at-cuts",
                     "-o", (img / f"clip-%(id)s-{tag}.%(ext)s").as_posix(),
                     "--print", "after_move:%(filepath)s\t%(id)s\t%(channel)s\t%(title)s", url], cookies)
    path, vid, channel, title = [ln for ln in out.splitlines() if ln.strip()][-1].split("\t", 3)
    entry = {"kind": "clip", "file": f"images/{Path(path).name}", "url": f"https://youtu.be/{vid}?t={int(start)}",
             "channel": channel, "title": title, "start_s": start, "end_s": end, "at": f"{ts(start)}-{ts(end)}"}
    add_credit(edit, entry)
    return entry


def credits(edit):
    """credits.txt: one line per clip (credits.json) and per post (images/post-*.json)."""
    edit = Path(edit)
    p = edit / "credits.json"
    lines = [f'{c["channel"]}, "{c["title"]}" ({c["at"].split("-")[0][:-3]}): {c["url"]}'
             for c in (json.loads(p.read_text(encoding="utf-8")) if p.exists() else [])]
    for f in sorted((edit / "images").glob("post-*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        lines.append(f'{d.get("name")} ({d.get("handle")}) on X, {d.get("time")}: {d.get("url")}')
    text = "Sources:\n" + "".join(f"- {ln}\n" for ln in lines) if lines else ""
    (edit / "credits.txt").write_text(text, encoding="utf-8")
    return text


def demo():
    j = {"events": [{"tStartMs": 1000, "segs": [{"utf8": "so"}, {"utf8": " what", "tOffsetMs": 400}]},
                    {"tStartMs": 1800, "segs": [{"utf8": "we found"}, {"utf8": " is that", "tOffsetMs": 800}]},
                    {"tStartMs": 3400, "segs": [{"utf8": "skills are just"}, {"utf8": " folders of", "tOffsetMs": 1200},
                                                {"utf8": " instructions", "tOffsetMs": 2000}, {"utf8": " that claude loads", "tOffsetMs": 2400}]}]}
    with tempfile.TemporaryDirectory() as d:
        sub = Path(d) / "talk.en.json3"
        sub.write_text(json.dumps(j), encoding="utf-8")
        words = caption_words(sub.as_posix())       # a local caption file: no network
        assert words[:2] == [(1.0, "so"), (1.4, "what")], words[:2]
        hit = find(words, "Skills are just folders of instructions")
        assert hit and hit[0][0] == 1.0 and hit[0][1] == 3.4 and hit[0][2] == 5.8, hit
        assert find(words, "completely unrelated sentence about bananas") == []
        near = find(words, "skills are folders of instruction")      # a misheard caption still lands
        assert near and near[0][1] == 3.4, near
        assert find(words + [(30 + t, w) for t, w in words], "skills are just folders")[1][1] == 33.4  # two places, two hits

        edit = Path(d) / "edit"
        (edit / "images").mkdir(parents=True)
        add_credit(edit, {"kind": "clip", "file": "images/clip-abc-3.mp4", "url": "https://youtu.be/abc?t=3",
                          "channel": "Some Lab", "title": "A talk", "start_s": 3.0, "end_s": 6.2, "at": "0:03.00-0:06.20"})
        add_credit(edit, {"kind": "clip", "file": "images/clip-abc-3.mp4", "url": "https://youtu.be/abc?t=3",
                          "channel": "Some Lab", "title": "A talk", "start_s": 3.0, "end_s": 6.6, "at": "0:03.00-0:06.60"})
        assert len(json.loads((edit / "credits.json").read_text(encoding="utf-8"))) == 1   # a re-clip replaces, not duplicates
        (edit / "images" / "post-20.json").write_text(json.dumps(
            {"platform": "x", "name": "jack", "handle": "@jack", "text": "just setting up my twttr",
             "time": "2006-03-21", "url": "https://x.com/jack/status/20"}), encoding="utf-8")
        text = credits(edit)
        assert text == ('Sources:\n- Some Lab, "A talk" (0:03): https://youtu.be/abc?t=3\n'
                        "- jack (@jack) on X, 2006-03-21: https://x.com/jack/status/20\n"), text
    assert RATE.search("ERROR: HTTP Error 429: Too Many Requests") and LOGIN.search("Sign in to confirm you're not a bot")
    print("demo ok", hit[0])


def opt(a, name, default=None):
    if name in a:
        i = a.index(name)
        v = a[i + 1]
        del a[i:i + 2]
        return v
    return default


def main():
    a = sys.argv[1:]
    cookies = opt(a, "--cookies-from-browser")
    if not a:
        die(__doc__)
    cmd = a[0]
    if cmd == "demo":
        demo()
    elif cmd == "search" and len(a) >= 2:
        n = int(opt(a, "--n", 8))
        print(run_ytdlp(["--flat-playlist", "--print", "%(id)s | %(duration_string)s | %(view_count)s views | "
                         "%(channel)s | %(title)s", f"ytsearch{n}:{a[1]}"], cookies).strip())
    elif cmd == "quote" and len(a) >= 3:
        top, lang = int(opt(a, "--top", 3)), opt(a, "--lang", "en")
        hits = find(caption_words(a[1], lang, cookies), a[2], top)
        if not hits:
            die("Not found in the captions. Try 4-8 of the most distinctive words, or check it is the right video.")
        for score, s, e, text in hits:
            print(f'{score:.2f}  {ts(s)}-{ts(e)}  "{text}"')
            print(f'      clip: source.py clip edits/<name> "{a[1]}" {max(0, s - PAD):.2f} {e + PAD:.2f}')
    elif cmd == "clip" and len(a) >= 5:
        c = clip(a[1], a[2], float(a[3]), float(a[4]), cookies)
        print(f'{c["file"]}  <- {c["channel"]}, {c["url"]} ({c["at"]})')
    elif cmd == "credits" and len(a) >= 2:
        print(credits(a[1]) or "no clips or posts in this edit yet")
    else:
        die(__doc__)


if __name__ == "__main__":
    main()
