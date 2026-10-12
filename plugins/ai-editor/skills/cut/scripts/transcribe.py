#!/usr/bin/env python3
"""Verbatim word-level transcription of a take. Writes a list of words.

    python transcribe.py <media> <out.json> [--engine auto|whisper|crisper|scribe] [--lang en]
                         [--script script.md] [--keyterm TERM ...]
    python transcribe.py demo     self-check of the keyterm picker

Output (docs/CONTRACTS.md, Transcription):
    [{"text": "so", "start": 0.12, "end": 0.31, "type": "word"},
     {"text": " ", "start": 0.31, "end": 0.40, "type": "spacing"}, ...]

Engines:
  whisper  faster-whisper, free and local. Needs ~/.ai-video-editor/venv.
           Model from AI_EDITOR_WHISPER_MODEL (default "small").
  crisper  CrisperWhisper 2.0, free and local, verbatim: keeps ums, repeats and false
           starts, which plain Whisper tidies away. `setup.py crisper` installs it
           (about 1 GB). Model from AI_EDITOR_CRISPER_MODEL (default "small"). Its
           weights are licensed for NON-COMMERCIAL use only.
  scribe   ElevenLabs Scribe v2, verbatim. Needs a key in ELEVENLABS_API_KEY or
           ~/.config/creator-teardown/.env ($AI_EDITOR_HOME/.env when that is set).
           Keeps every filler and false start.
  auto     scribe when a key exists, else crisper when installed, else whisper (default).

Keyterms: --script picks the names, product names and jargon from the user's script (capitalised
words and words with digits that are not common words); --keyterm adds one by hand. Scribe gets
them as `keyterms` (about 20% more on that request), Whisper as part of its initial prompt,
CrisperWhisper skips them.

Audio is pulled out with ffmpeg first (16 kHz mono WAV), so a multi-GB 4K file
costs one fast audio demux, never a video decode.

Exit codes: 0 ok, 1 error, 2 usage or missing key, 3 HTTP error
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from ai_editor import keys, models, net, progress  # noqa: E402

HOME = models.HOME
# Whisper tidies speech by default. A prompt full of hesitations makes it keep
# more of them, and the fillers are the evidence the cut acts on.
FILLER_PROMPT = "Umm, uh, so, like, you know, I mean, uh, so, um, wait, sorry, let me start again."

# Scribe's keyterms limits (ElevenLabs speech-to-text reference): under 50 characters, at most 5 words,
# none of <>{}[]\. The API takes 1000, but over 100 a request bills at least 20 s, so 100 here.
MAX_KEYTERMS, MAX_KEYTERM_CHARS, WHISPER_TERMS = 100, 49, 40
COMMON = set("""a an and are as at be but by can do for from have he her his how i if in is it its just
let me my no not now of on or our out so that the their then there these they this to up us was we
what when where which who why will with you your yes okay ok hey hi here today first next last one two
three four five six seven eight nine ten monday tuesday wednesday thursday friday saturday sunday""".split())


def keyterms_from(text):
    """Distinctive words of a script, in order: capitalised or with a digit, 2+ characters, not a common word."""
    out, seen = [], set()
    for w in re.findall(r"[A-Za-z0-9][\w.+#'-]*[\w+#]|[A-Za-z0-9]", text):
        w = re.sub(r"'s$", "", w)
        if (len(w) < 2 or not (w[0].isupper() or any(c.isdigit() for c in w)) or w.lower() in COMMON
                or w.lower() in seen):
            continue
        seen.add(w.lower())
        out.append(w)
    return out


def clean_keyterms(terms):
    """Deduplicated terms inside Scribe's limits, at most MAX_KEYTERMS."""
    out, seen = [], set()
    for t in (" ".join(t.split()) for t in terms):
        if (t and t.lower() not in seen and len(t) <= MAX_KEYTERM_CHARS and len(t.split()) <= 5
                and not set(t) & set("<>{}[]\\")):
            seen.add(t.lower())
            out.append(t)
    return out[:MAX_KEYTERMS]


def whisper_prompt(terms):
    """Whisper reads its prompt as text said before the take; names in it come back spelled that way.
    Its prompt holds about 224 tokens, so only the first WHISPER_TERMS."""
    return FILLER_PROMPT + (" " + ", ".join(terms[:WHISPER_TERMS]) + "." if terms else "")


def find_key():
    return keys.get("elevenlabs")[0]


def extract_audio(src, dst):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-ac", "1",
                        "-ar", "16000", "-c:a", "pcm_s16le", str(dst)])
    if r.returncode:
        sys.exit(f"ERROR: ffmpeg could not read audio from {src}")


def with_spacing(words):
    """Insert spacing tokens for the gaps, so both engines give the same shape."""
    out = []
    for w in words:
        if out and w["start"] - out[-1]["end"] > 0.001:
            out.append({"text": " ", "start": out[-1]["end"], "end": w["start"], "type": "spacing"})
        out.append(w)
    return out


def run_whisper(wav, lang, terms=()):
    os.environ.setdefault("HF_HOME", str(HOME / "models"))
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("ERROR: faster-whisper missing. Run this with python3 <plugin>/lib/ai_editor/run.py, which uses "
                 "the editor's venv (the setup skill installs it).")
    name = os.environ.get("AI_EDITOR_WHISPER_MODEL", "small")
    print(f"whisper model {name} (cpu, int8)", flush=True)
    # The pinned model (setup.py model); a size with no pin downloads from the Hub as before.
    model = WhisperModel(str(models.whisper(name, check=False) or name), device="cpu", compute_type="int8",
                         download_root=str(HOME / "models"))
    prompt = whisper_prompt(list(terms))
    # condition_on_previous_text=False: with it on, whisper tends to collapse a
    # repeated line into one, and repeated lines are exactly the retakes.
    # vad_filter=True: without it, word times drift after a long silence (a line
    # placed 8 s after where it was said, on a real take), and raw takes are full
    # of long silences.
    segs, _ = model.transcribe(str(wav), language=lang, word_timestamps=True,
                               initial_prompt=prompt, beam_size=5,
                               condition_on_previous_text=False, vad_filter=True,
                               vad_parameters={"min_silence_duration_ms": 500})

    def words_of(segs):
        return [{"text": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3), "type": "word"}
                for s in segs for w in (s.words or []) if w.word.strip()]

    def again(start, end):
        """The words in one stretch, transcribed on their own (no VAD: the stretch is speech)."""
        segs, _ = model.transcribe(str(wav), language=lang, word_timestamps=True, initial_prompt=prompt,
                                   beam_size=5, condition_on_previous_text=False, clip_timestamps=[start, end])
        return [w for w in words_of(segs) if w["start"] >= start - 0.05 and w["end"] <= end + 0.05]

    return recheck(wav, with_spacing(words_of(segs)), again)


def recheck(wav, words, again=None):
    """A label holding far more speech than its word (build_timeline.unheard) is transcribed again on
    its own; the new words replace it when there are more of them, one word means a drawn-out word.
    With no words back (or no re-transcription, CrisperWhisper) the label keeps "unheard_s" (the voiced seconds), which transcript.txt shows so the cut can see the gap."""
    import build_timeline as B
    lvl = B.window_rms_db(wav)
    noise, _ = B.derive_noise_db(lvl)
    toks = [w for w in words if w["type"] != "spacing"]
    found = B.unheard(toks, lvl, noise) if noise is not None else []
    if not found:
        return words
    filled = marked = 0
    for i, voiced in reversed(found):
        w = toks[i]
        got = again(w["start"], w["end"]) if again else []
        if len(got) > 1:
            toks[i:i + 1] = got
            filled += 1
        elif not got:   # one word heard again: a drawn-out word, nothing hidden
            w["unheard_s"] = voiced
            marked += 1
    print(f"{len(found)} place(s) where one word held more speech: {filled} heard again"
          + (f", {marked} marked in transcript.txt" if marked else ""))
    return with_spacing(toks)


def has_crisper():
    import importlib.util
    return importlib.util.find_spec("crisperwhisper") is not None


def run_crisper(wav, lang, terms=()):   # no prompt option: keyterms skipped
    os.environ.setdefault("HF_HOME", str(HOME / "models"))
    try:
        from crisperwhisper import CrisperWhisperModel
    except ImportError:
        sys.exit("ERROR: CrisperWhisper missing. Install it with the setup skill: setup.py crisper")
    name = os.environ.get("AI_EDITOR_CRISPER_MODEL", "small")
    print(f"crisperwhisper {name} (verbatim; weights licensed for non-commercial use)", flush=True)
    r = CrisperWhisperModel(name).transcribe(str(wav), language=lang, word_timestamps=True)
    words = []
    for w in r.words or []:
        t = w.word.strip()
        if not t:
            continue
        # Verbatim mode writes fillers as [um] and sounds as [laughter].
        inner = t.strip("[]").strip()
        tag = t.startswith("[") and t.endswith("]")
        filler = inner.lower() in ("um", "uh", "umm", "uhh", "hmm", "mm", "er", "erm", "ah")
        words.append({"text": inner if tag and filler else t, "start": round(w.start, 3),
                      "end": round(w.end, 3), "type": "audio_event" if tag and not filler else "word"})
    return recheck(wav, with_spacing(words))


def run_scribe(wav, key, lang, terms=()):
    fields = [("model_id", "scribe_v2"), ("timestamps_granularity", "word"),
              ("tag_audio_events", "true"), ("no_verbatim", "false"), ("diarize", "false"),
              ("temperature", "0"), ("seed", "0")]
    if lang:
        fields.append(("language_code", lang))
    fields += [("keyterms", t) for t in terms]   # repeated form field, one per term
    b = "----cut" + os.urandom(12).hex()
    body = bytearray()
    for k, v in fields:
        body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="a.wav"\r\n'
             f"Content-Type: audio/wav\r\n\r\n").encode() + Path(wav).read_bytes() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request("https://api.elevenlabs.io/v1/speech-to-text", data=bytes(body),
                                 headers={"xi-api-key": key,
                                          "Content-Type": f"multipart/form-data; boundary={b}"})
    try:
        with net.urlopen(req, 900) as r:
            payload = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode(errors='replace')[:400]}", file=sys.stderr)
        sys.exit(3)
    except (urllib.error.URLError, OSError) as e:   # no network, DNS, a timeout
        print(f"ElevenLabs unreachable ({net.friendly(e)}). Check the connection and run again, "
              "or use --engine whisper (free, on this computer).", file=sys.stderr)
        sys.exit(3)
    return [{"text": w["text"], "start": w["start"], "end": w["end"], "type": w["type"]}
            for w in payload.get("words", []) if w.get("type") in ("word", "spacing", "audio_event")]


def demo():
    t = keyterms_from("# Intro\nToday I built it with Claude Code and GPT-4o. The API costs $20.\n"
                      "Then I asked Claude again. It's 3x faster than Jev's, in 2 s.")
    assert t == ["Intro", "Claude", "Code", "GPT-4o", "API", "20", "3x", "Jev"], t
    assert clean_keyterms(["a<b", "x" * 50, "one two three four five six", "Jev", "jev", " Claude  Code "]) == ["Jev", "Claude Code"]
    assert len(clean_keyterms([f"T{i}" for i in range(300)])) == MAX_KEYTERMS
    assert whisper_prompt([]) == FILLER_PROMPT and whisper_prompt(["Jev"]).endswith(" Jev.")
    print("transcribe demo ok")


def main():
    if sys.argv[1:] == ["demo"]:
        return demo()
    ap = argparse.ArgumentParser()
    ap.add_argument("media")
    ap.add_argument("out")
    ap.add_argument("--engine", choices=["auto", "whisper", "crisper", "scribe"], default="auto")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--script", help="the user's script: its names and jargon bias the transcription")
    ap.add_argument("--keyterm", action="append", default=[], help="one name or term to bias towards (repeatable)")
    a = ap.parse_args()
    progress.step("Wrote down every word you said", job="video", key=Path(a.out).parent)   # the home page's video job, if any

    if not Path(a.media).exists():
        sys.exit(f"ERROR: no such file: {a.media}")
    if a.script and not Path(a.script).exists():
        sys.exit(f"ERROR: no such file: {a.script}")
    terms = clean_keyterms(a.keyterm + (keyterms_from(Path(a.script).read_text(encoding="utf-8")) if a.script else []))
    key = find_key()
    engine = a.engine if a.engine != "auto" else (
        "scribe" if key else "crisper" if has_crisper() else "whisper")
    if engine == "scribe" and not key:
        print(f"ERROR: no ElevenLabs key (ELEVENLABS_API_KEY or {keys.key_file()})", file=sys.stderr)
        sys.exit(2)

    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        extract_audio(a.media, wav)
        print(f"engine {engine}", flush=True)
        if terms and engine != "crisper":
            print(f"keyterms   {len(terms)}" + (" (+20% on this Scribe request)" if engine == "scribe" else "")
                  + ": " + ", ".join(terms[:8]), flush=True)
        run = {"whisper": run_whisper, "crisper": run_crisper}.get(engine)
        words = run(wav, a.lang, terms) if run else run_scribe(wav, key, a.lang, terms)

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(words, indent=1), encoding="utf-8")
    n = sum(1 for w in words if w["type"] == "word")
    print(f"{n} words -> {out}")


if __name__ == "__main__":
    main()
