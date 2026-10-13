# The keys, one by one

Moved out of SKILL.md step 5. Say each key's "What for" and "Cost" lines to the user before its
click path. Every key is optional.

## Gemini (recommended): reads a creator's look

- **What for:** creator-teardown sends a few frames of each video to Google's Gemini to name the
  font, colours and graphics style. Without it the teardown shows Claude one picture per video
  instead, which uses more Claude usage.
- **Cost:** free. Google AI Studio's free tier needs no card. It has a daily limit that a teardown
  stays inside.
- **Get it:** https://aistudio.google.com/apikey : sign in with a Google account, click
  **Create API key** (pick or create a project if it asks), copy the key.
- **Save:** `setkey gemini`.

## ElevenLabs (optional): better transcripts

- **What for:** the free transcription on this computer tidies speech and drops some "um"s and
  false starts. ElevenLabs Scribe keeps every one, and the cut uses them to find retakes.
- **Cost:** the free ElevenLabs plan includes a few hours of transcription a month. A take
  transcribed with a script's names as keyterms costs about 20% more.
- **Get it:** https://elevenlabs.io/app/settings/api-keys (sign up first at
  https://elevenlabs.io/app/sign-up), create a key. Keep **Restrict Key** on, allow **Speech to
  Text** only, and set a credit limit.
- **Save:** `setkey elevenlabs`.

## CrisperWhisper (optional, free verbatim transcripts, no key)

Only offer it to a user without an ElevenLabs key who wants better cuts. It runs on this computer,
keeps every "um", repeat and false start like Scribe does, and costs nothing per video. It is about
a 1 GB download and needs Python 3.10 or newer. **Its model weights are licensed for
non-commercial use only**: say this before installing, and skip it for anyone making videos for a
business or paid work.

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" crisper
```

Once installed, the cut uses it automatically when there is no ElevenLabs key. If it stops with
`CrisperWhisper needs Python 3.10 or newer`, say so and keep the free Whisper model.
