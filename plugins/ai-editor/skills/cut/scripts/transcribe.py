#!/usr/bin/env python3
"""Verbatim word-level transcription of a take. Writes a list of words.

    python transcribe.py <media> <out.json> [--engine auto|whisper|crisper|scribe] [--lang en]
                         [--script script.md] [--keyterm TERM ...]
    python transcribe.py <media> --pre   the home window, the moment a clip is added on New video (before Make):
                         the same transcription into the cache, free engines only (Scribe bills, so it waits for Make)
    python transcribe.py demo     self-check of the keyterm picker

Cache: every transcription is kept in $AI_EDITOR_HOME/cache/transcripts/ for CACHE_DAYS, keyed by the file
(path, size, modified time), the engine, its model, the language and the keyterms. The same take with the same
settings is never transcribed twice: the run after Make reuses what --pre made, or waits for it to finish.

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

Models never download here (an edit is no time for a 1.5 GB surprise): only what setup installed is used, and a
model that is not installed exits 4 (the re-check is skipped). setup.py model / crisper download, with the size
said and the free space checked first.

Exit codes: 0 ok, 1 error, 2 usage or missing key, 3 HTTP error, 4 that model is not installed
"""
import argparse
import hashlib
import json
import os
import shutil
import time
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


EDIT = None   # the edit folder main() writes into


def installed_whisper(name):
    """The folder of a faster-whisper model already on disk (setup.py model, or faster-whisper's own cache for a
    size with no pin). Never downloads: not installed exits 4."""
    if Path(name).is_dir():
        return name
    got = models.whisper_dir(name)
    if not got and name not in models.WHISPER:
        try:
            from faster_whisper.utils import download_model
            got = download_model(name, local_files_only=True, cache_dir=str(HOME / "models"))
        except Exception:   # noqa: BLE001  not in its cache (any repo name: distil-large-v3, an org/model id)
            got = None
    if not got:
        print(f"ERROR: the {name} transcription model is not installed, and models never download during an edit. "
              "Skip this re-check and say so, or use the installed model (no AI_EDITOR_WHISPER_MODEL) or Scribe "
              "(--engine scribe). A model is added only by setup (setup.py model), which says its size and checks "
              "free space first.", file=sys.stderr)
        sys.exit(4)
    return str(got)


def run_whisper(wav, lang, terms=()):
    os.environ.setdefault("HF_HOME", str(HOME / "models"))
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        sys.exit("ERROR: faster-whisper missing. Run this with python3 <plugin>/lib/ai_editor/run.py, which uses "
                 "the editor's venv (the setup skill installs it).")
    name = os.environ.get("AI_EDITOR_WHISPER_MODEL", "small")
    print(f"whisper model {name} (cpu, int8)", flush=True)
    model = WhisperModel(installed_whisper(name), device="cpu", compute_type="int8", download_root=str(HOME / "models"),
                         local_files_only=True)
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
    os.environ["HF_HUB_OFFLINE"] = "1"   # only the weights setup.py crisper downloaded: never a download mid-edit
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


CACHE_DAYS = 7
LOCK_STALE_S = 1800   # a --pre that died without removing its lock (Windows has no cheap is-it-alive check)


def cache_dir():
    return HOME / "cache" / "transcripts"


def cache_key(media, engine, lang, terms):
    """The take (path, size, modified time) and every setting that changes the words."""
    st = Path(media).stat()
    model = {"whisper": os.environ.get("AI_EDITOR_WHISPER_MODEL", "small"),
             "crisper": os.environ.get("AI_EDITOR_CRISPER_MODEL", "small")}.get(engine, "scribe_v2")
    raw = json.dumps([str(Path(media).resolve()), st.st_size, st.st_mtime_ns, engine, model, lang, list(terms)])
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def cached(key):
    """The words a run with these settings already wrote, while fresh; else None. Old entries are removed."""
    d, now = cache_dir(), time.time()
    for f in d.glob("*.json") if d.is_dir() else []:
        try:
            if now - f.stat().st_mtime > CACHE_DAYS * 86400:
                f.unlink()
        except OSError:
            pass
    try:
        return json.loads((d / f"{key}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def keep(key, words):
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / f"{key}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(words), encoding="utf-8")
    os.replace(tmp, d / f"{key}.json")


def running(lock):
    """A --pre is transcribing this take now (its lock names a live process)."""
    try:
        pid, age = int(lock.read_text(encoding="utf-8") or 0), time.time() - lock.stat().st_mtime
    except (OSError, ValueError):
        return False
    if age > LOCK_STALE_S:
        return False
    if os.name == "nt":   # os.kill(pid, 0) would end the process on Windows: the lock's age decides
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:   # alive, another user's
        return True


def demo_cache():
    import tempfile
    global HOME
    old = HOME
    with tempfile.TemporaryDirectory() as t:
        HOME = Path(t)
        take = Path(t) / "take.mov"
        take.write_bytes(b"x")
        k = cache_key(take, "whisper", "en", [])
        assert cached(k) is None
        keep(k, [{"text": "hi", "start": 0, "end": 0.2, "type": "word"}])
        assert cached(k)[0]["text"] == "hi"
        assert cache_key(take, "whisper", "en", ["Jev"]) != k, "keyterms change the words: another entry"
        assert cache_key(take, "crisper", "en", []) != k
        os.utime(take, (1, 1))
        assert cache_key(take, "whisper", "en", []) != k, "a changed file is another take"
        f = cache_dir() / f"{k}.json"
        os.utime(f, (time.time() - CACHE_DAYS * 86400 - 5,) * 2)
        assert cached(k) is None and not f.exists(), "an old entry is dropped"
        lock = cache_dir() / "x.lock"
        lock.write_text(str(os.getpid()), encoding="utf-8")
        assert running(lock)
        lock.write_text("999999999", encoding="utf-8")
        assert os.name == "nt" or not running(lock), "a dead --pre's lock is ignored"
        lock.write_text(str(os.getpid()), encoding="utf-8")
        os.utime(lock, (time.time() - LOCK_STALE_S - 5,) * 2)
        assert not running(lock), "a stale lock is ignored"
    HOME = old


def demo():
    demo_cache()
    t = keyterms_from("# Intro\nToday I built it with Claude Code and GPT-4o. The API costs $20.\n"
                      "Then I asked Claude again. It's 3x faster than Jev's, in 2 s.")
    assert t == ["Intro", "Claude", "Code", "GPT-4o", "API", "20", "3x", "Jev"], t
    assert clean_keyterms(["a<b", "x" * 50, "one two three four five six", "Jev", "jev", " Claude  Code "]) == ["Jev", "Claude Code"]
    assert len(clean_keyterms([f"T{i}" for i in range(300)])) == MAX_KEYTERMS
    assert whisper_prompt([]) == FILLER_PROMPT and whisper_prompt(["Jev"]).endswith(" Jev.")
    try:   # a model that is not installed: exit 4, never a download
        installed_whisper("no-such-size-demo")
        raise AssertionError("a missing model went on")
    except SystemExit as e:
        assert e.code == 4, e.code
    print("transcribe demo ok")


def main():
    if sys.argv[1:] == ["demo"]:
        return demo()
    ap = argparse.ArgumentParser()
    ap.add_argument("media")
    ap.add_argument("out", nargs="?")
    ap.add_argument("--pre", action="store_true", help="into the cache only, before Make (the home window)")
    ap.add_argument("--engine", choices=["auto", "whisper", "crisper", "scribe"], default="auto")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--script", help="the user's script: its names and jargon bias the transcription")
    ap.add_argument("--keyterm", action="append", default=[], help="one name or term to bias towards (repeatable)")
    a = ap.parse_args()
    if not a.pre and not a.out:
        ap.error("the output path is required (or --pre)")
    if not a.pre:
        progress.step("Writing down every word", job="video", key=Path(a.out).parent)   # the home page's video job, if any
    global EDIT
    EDIT = None if a.pre else Path(a.out).parent   # edits/<name>: a first-use model download shows on its review page

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
    if a.pre and engine == "scribe":
        print("scribe bills per minute: it starts when Make is pressed, not before")
        return
    ck = cache_key(a.media, engine, a.lang, terms)
    lock = cache_dir() / f"{ck}.lock"
    if a.pre:
        cache_dir().mkdir(parents=True, exist_ok=True)
        if cached(ck) is not None or running(lock):
            return
        lock.unlink(missing_ok=True)   # a dead run's
        try:   # one --pre per take: the clip added twice starts one transcription
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    else:
        if running(lock):
            print("the transcription started when the clip was added: waiting for it", flush=True)
            while running(lock):
                time.sleep(0.5)
        words = cached(ck)
        if words is not None:
            print(f"engine {engine}: reused the transcript made when the clip was added", flush=True)
            return write(a.out, words)
    try:
        words = transcribe(a, engine, terms)
        keep(ck, words)
    finally:
        if a.pre:
            lock.unlink(missing_ok=True)
    if not a.pre:
        write(a.out, words)


def transcribe(a, engine, terms):
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        extract_audio(a.media, wav)
        print(f"engine {engine}", flush=True)
        if terms and engine != "crisper":
            print(f"keyterms   {len(terms)}" + (" (+20% on this Scribe request)" if engine == "scribe" else "")
                  + ": " + ", ".join(terms[:8]), flush=True)
        run = {"whisper": run_whisper, "crisper": run_crisper}.get(engine)
        return run(wav, a.lang, terms) if run else run_scribe(wav, find_key(), a.lang, terms)


def write(out, words):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(words, indent=1), encoding="utf-8")
    n = sum(1 for w in words if w["type"] == "word")
    print(f"{n} words -> {out}")
    progress.step("Finding retakes and pauses", job="video", key=out.parent)   # the words are in: Claude decides the cuts next


if __name__ == "__main__":
    main()
