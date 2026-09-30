# video-ai-dub

Replace the speech in any video with a natural, human-like AI voice-over, timed to the original video, using only your OpenAI API key.

```
python3 ai_dub.py my_video.mp4
```

Produces `my_video_ai.mp4` next to the original. The original file is never modified.

## What it does

Every step prints its progress in the terminal.

| Step | What happens | Tool / model |
|------|--------------|--------------|
| 1 | Checks `ffmpeg`, the API key and that the video has audio | local |
| 2 | Extracts the audio (mono 16 kHz wav) | `ffmpeg` |
| 3 | Transcribes the speech with segment timestamps, writes `*_transcript.md` | OpenAI `whisper-1` |
| 4 | Rewrites the transcript into polished narration that fits each time slot, writes `*_transcript_improved.md` | OpenAI text model (`gpt-4.1`) |
| 5 | Generates speech for each line; any line too long for its slot is auto-shortened and regenerated so every sentence finishes | OpenAI `gpt-4o-mini-tts` |
| 6 | Places each line at its original timestamp, denoises, normalises loudness | `ffmpeg` |
| 7 | Muxes the new audio with the untouched video stream, writes `*_ai_report.md` | `ffmpeg` |

## Requirements

- macOS/Linux with `python3` (3.8+). No pip packages needed (standard library only).
- `ffmpeg` and `ffprobe` (`brew install ffmpeg`).
- `OPENAI_API_KEY` in your environment:

```bash
echo 'export OPENAI_API_KEY="sk-..."' >> ~/.zshrc
source ~/.zshrc
```

The key is read from the environment only. It is never printed, logged or written to any file; API error text is redacted.

## Usage

```bash
python3 ai_dub.py VIDEO [options]
```

Example with terminology rules:

```bash
python3 ai_dub.py ../online_delivery.mp4 \
  --context "Say 'QR code' instead of 'fuel code'. The app is a fuel delivery attendant app." \
  --terms "QR code, Emirates ID, Confirm and Proceed"
```

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--voice` | `nova` | OpenAI TTS voice |
| `--tts-model` | `gpt-4o-mini-tts` | speech model |
| `--text-model` | `gpt-4.1` | model that rewrites and shortens the script |
| `--transcribe-model` | `whisper-1` | must return segment timestamps |
| `--language` | `en` | spoken language (ISO-639-1); empty string for auto-detect |
| `--context` | none | glossary and terminology rules enforced on the rewrite |
| `--terms` | none | comma-separated names to help transcription accuracy |
| `--style` | warm, conversational | speaking-style instructions for the voice |
| `--max-tempo` | `1.3` | maximum speed-up to fit a line into its slot |
| `--no-snap` | off | disable snapping line starts to detected speech onset |
| `--no-improve` | off | voice the raw transcript without AI rewriting |
| `--use-md` | off | reuse your edited `*_transcript_improved.md` (skips transcription and rewrite cost) |
| `--review` | off | pause after the rewrite so you can edit the improved `.md` |
| `--fresh` | off | ignore the cached transcription |
| `--clean` | off | delete the work folder when finished |
| `--out-dir` | video folder | where outputs are written |

## Outputs

Given `demo.mp4`, next to the video:

| File | Purpose |
|------|---------|
| `demo_ai.mp4` | final video with AI voice |
| `demo_transcript.md` | raw transcript with timings |
| `demo_transcript_improved.md` | polished narration that the voice reads |
| `demo_ai_report.md` | per-line slot, speech length, speed and OK/OVERFLOW status |
| `demo_ai_work/` | cache: extracted audio, per-line audio, transcription JSON |

## Editing the script

1. Run once (add `--review` to pause after the rewrite).
2. Edit the **Text** column in `demo_transcript_improved.md`. Keep the `#` numbers and the 4-column table.
3. Re-run with `--use-md`. Only edited lines call the TTS again; unchanged lines are cached.

Leave a line's text empty to skip it (its meaning should be merged into the previous line).

## How timing works

- Each line gets a time slot: from its start to the next line's start.
- Line starts are snapped to real speech onsets (silence detection) because Whisper timestamps are coarse.
- If the generated speech is longer than its slot, it is sped up by at most `--max-tempo`. If it still does not fit, the text model shortens the sentence and it is regenerated (up to 3 rounds), so sentences are never cut off.
- Any line that still overflows is marked `OVERFLOW` in the report.

## Cost

Roughly a few cents per minute of video: transcription (~$0.006/min), a few text-model calls, and TTS billed per character. A 5 minute walkthrough costs about $0.15. Re-runs reuse cached transcription and per-line audio.

## Limitations

- Only speech is regenerated. Background music and sound effects from the original audio are dropped.
- One voice for the whole video; multiple speakers are not separated.
- Output container keeps the input extension; use `.mp4`, `.mov`, `.m4v` or `.mkv` (the audio is AAC).
- Whisper timestamps are segment-level. If a line still starts slightly early or late, tweak the improved `.md` wording or split the line.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `OPENAI_API_KEY is not set` | add it to `~/.zshrc` and `source ~/.zshrc` |
| `ffmpeg not found` | `brew install ffmpeg` |
| HTTP 401 | key is invalid or revoked |
| HTTP 429 | rate limit or quota; the script retries automatically, then stops with the message |
| Voice too fast | shorten lines in the improved `.md`, or lower `--max-tempo` |
