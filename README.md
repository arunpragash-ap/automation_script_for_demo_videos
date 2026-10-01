# video-ai-dub

Replace the speech in a video with a natural, human-like AI voice-over timed to the original, add an optional title intro and a "Thank You" end card, using only your OpenAI API key.

```
python3 ai_dub.py my_video.mp4
```

You are asked for everything else interactively. The original video is never modified.

Already have a voice-over and want to polish the video (cut, fix timing, change a sentence)? Use `python3 demo.py` — see "Demo studio" below.

## Interactive flow

```
Video title for the intro card (Enter to skip the intro): My Fuel Delivery Demo
Which mode do you want?
  a) Auto - runs start to end with no questions
  h) Human-in-the-loop - you review the transcript, guide the AI and approve the script
```

- **Auto**: runs every step from start to end with no further questions.
- **Human-in-the-loop**: first asks for context/glossary rules (for example `Say 'QR code' instead of 'fuel code'`) and key terms, then pauses twice:
  1. **After transcription**: the lines are listed with their numbers and times.
  2. **After the script is written**: the lines are listed again, with one extra option, `s`.

  At both pauses you choose:
  - `e` **full-screen editor in the terminal** (like nano): all lines are shown with their text wrapped. `Up`/`Down` (or `j`/`k`) move, `Enter` edits the selected line with its current text pre-filled (arrows, `Ctrl-A`/`Ctrl-E` start/end, `Ctrl-K` delete to end, `Ctrl-U` clear; `Enter` keeps the change, `Esc` cancels it), `s` saves everything and exits, `q` cancels (asks before discarding). Edited lines are marked `*`. Without a real terminal it falls back to a line-by-line editor,
  - `o` **open the file in your text editor** (`transcript.md` or `transcript_improved.md`), edit and save it, then press Enter back in the terminal,
  - `v` show the lines again,
  - `s` (script pause only) give the AI a suggestion for the whole script, for example "use a formal tone and avoid contractions", then review the result,
  - `p` proceed.

  Edits to the raw transcript feed the AI rewrite. Edits to the script are what the voice reads.

To redo only the audio after editing the script by hand, run again and pick `5+` in the "folder already exists" menu.

If you omit the video path it is asked too.

## What you see while it runs

Just one line that shows the current step with a spinner and a timer, replaced in place as the next step starts:

```
⠹ [5/7] Generate AI speech and fit timings 3/6  5s
```

Nothing else is printed during the run. All details go to the log file.

## Logs

Every run writes `logs/log_<timestamp>.json` in the video's folder, for example `logs/log_20261001_114111.json`:

```json
{
  "run":   { "id", "mode", "title", "context", "status", "duration_seconds", "selected_steps", "options", "started", "finished", "error", ... },
  "steps": [ { "index", "key", "title", "status", "seconds", "started", "ended", "error" } ],
  "api_usage": { "/audio/transcriptions": 1, "/chat/completions": 12, "/audio/speech": 9 },
  "events": [ { "time", "level", "step", "message", ...details } ]
}
```

Event levels: `success`, `info`, `warning`, `error`, `debug` (every OpenAI request with status, timing and retries, and every ffmpeg command). Human actions (reviews, suggestions, approvals) are recorded too. The file is rewritten after each event, so it exists even if the run crashes or you press Ctrl+C. The API key is redacted everywhere.

## Timing: how the AI voice is kept in sync

- Each line is anchored to the **real speech onset measured from the original audio**, not to Whisper's timestamps, which can run 0.1 to 0.7 seconds late.
- Each AI line starts `--lead` seconds before the original speaker started (default **1.0**), so the explanation arrives with or before the on-screen action, never after.
- Each AI line must finish within `--end-grace` seconds (default **0.5**) after the original speech ended, so it never keeps explaining the previous step after the screen has moved on. Lines that do not fit are sped up (up to `--max-tempo`) or shortened by the AI.
- Silence at the start and end of every generated voice clip is trimmed, so it plays exactly where it is placed.
- `report.md` shows, per line, the original start, when the AI plays, the slot and the speed-up.

## Title card, voice and thank-you card

- If you give a title, the intro card shows it on the blank screen and a voice says **"This video demonstrates <title>."** The card stays up until the sentence has been spoken (voice length + 0.4s lead-in + 0.6s tail, at least 2s), so a short title gets a short intro and a long title a longer one.
- If the narrator's first sentence only announces the same topic as the title (for example "In this flow, we are going to see online fueling"), it is **removed from the raw transcript** so the topic is not said twice. A first sentence that contains a real step is kept.
- The **"Thank you."** voice is spoken on the Thank You card (3s), not at the end of the video. A closing "Thank you" or goodbye in the narration (for example "The delivery is completed. Thank you.") is removed from the transcript, while the completion statement is kept. The AI rewrite is also told never to add greetings or thank-yous.
- Both removals use the text model and are listed in the log; the title one runs when the title is known, so re-run step 2 if you add a title later.

## Pauses in the narration

Write `[pause 2s]` inside any narration line to make the voice stop for that long (`[pause]` = 1 second, longest 10 seconds):

```
Click Next and confirm to proceed. [pause 2s] Then click Start Fueling.
```

- In human mode, type it while editing a line in the review editor, or in `transcript_improved.md` before re-running the audio step.
- The AI rewrite keeps markers exactly where they are; it does not add or remove them.
- The pause counts toward the line's time slot, so a long pause leaves less room for the words.
- Works the same in `video_edit.py narrate` and `demo.py` (`narrate 04.00 "Line one. [pause 2s] Line two."`; the older `"Line one." pause 2s "Line two."` form still works).

## Repeated or invented lines

During long silent stretches (the speaker is doing something on screen), Whisper sometimes invents lines or repeats the previous sentence. These rules remove them after transcription. None of them depend on a fixed gap between steps, and none contain project-specific words:

- the same sentence repeated right after itself,
- a repeat of one of the last three lines in a quiet stretch,
- a line with no speech in the audio around it (checked with one extra second on each side, because Whisper timestamps can be off),
- a line Whisper itself marked as probably not speech.

Lines that really repeat, spoken at different times with sound, are kept. Every removed line and the reason are written to the log.

## Validating the transcript and timing

Step 3 cross-checks every line and writes `validation.md`:

| Check | What it catches |
|-------|-----------------|
| **Speech present**: the line's time range must contain speech in the original audio | invented lines, lines placed in silence |
| **Starts too early**: a line must not start inside a silence before the speaker begins | timestamps that are too early |
| **Overlap**: a line must not run into the next line | broken timings |
| **Second opinion**: the whole audio is transcribed again by an independent model (`--validate-model`, default `gpt-4o-transcribe`) and each line's words must appear in it | mishearings and hallucinations that only one model produced |

Lines that fail any check are marked `CHECK` with the reason. In human-in-the-loop mode they are listed at the first review pause, so you can fix them with `e` or `o`. In both modes the second transcription is also given to the AI rewrite as evidence, so a clearly better wording can replace a mishearing. The checks are heuristics: a clean report is strong evidence, not proof, so listen to the first and last lines of the result when it matters.

## Project-specific knowledge stays out of the code

Terms, naming rules and how your app works belong in a plain text file that you keep with your videos and pass in:

```bash
python3 ai_dub.py demo.mp4 --context-file ~/videos/project_context.txt
```

Example `project_context.txt`:

```
Terms: QR code, Emirates ID, Confirm and Proceed
Say "QR code", never "fuel code".
The app is used by attendants to record fuel deliveries. "Capture" means take a photo with the camera.
```

A line starting with `Terms:` also helps the transcription. The whole file is given to the AI rewrite as context on every run, in both modes. You can still add one-off context at the human-in-the-loop prompt.

## Pipeline

| Step | What happens | Tool |
|------|--------------|------|
| 1 | Extract audio | `ffmpeg` |
| 2 | Transcribe with timestamps, writes `transcript.md` | OpenAI `whisper-1` |
| 3 | Validate the transcript and timing, writes `validation.md` | audio analysis + `gpt-4o-transcribe` |
| 4 | Rewrite the script: grammar, spelling and terminology checked against your title and context, sized to each time slot, writes `transcript_improved.md` | OpenAI text model (`gpt-4.1`) |
| 5 | Generate speech per line (auto-shortens lines that do not fit so sentences finish), plus the "Thank you." end-card voice | OpenAI `gpt-4o-mini-tts` |
| 6 | Build the title card (your title on the blank screen) and the end card, sized to the video | Pillow |
| 7 | Mix, denoise, normalise loudness, then assemble: **[title 3s] + video + Thank You 3s**, writes `report.md` | `ffmpeg` |

The title card is only added if you give a title. The "Thank You" card (3 seconds, with the spoken "Thank you.") is always added.

## Output folder

Everything for a video goes into a folder named after the video, next to it:

```
my_video/
  my_video_ai.mp4           final video
  transcript.md             raw transcript with timings
  validation.md             per-line validation report
  transcript_improved.md    narration script (edit this)
  report.md                 per-line timing report
  logs/                     log_<timestamp>.json for every run
  work/                     cache: audio, cards, settings
```

Use `--out-dir DIR` to create the folder somewhere else.

## Running again (folder already exists)

The tool shows which steps are done and asks what to run:

| Input | Meaning |
|-------|---------|
| `a` | all steps again |
| `5` | only step 5 |
| `5+` | step 5 and everything after it |
| `q` | quit (default) |

Title and context are remembered from the last run (Enter keeps them, `-` removes the title). Unchanged speech lines are cached, so re-running the audio only pays for lines you edited.

## Dependency checks

Before anything starts, the steps you chose are checked and missing items are listed with the fix:

- missing `ffmpeg`/`ffprobe`: `brew install ffmpeg`
- missing `OPENAI_API_KEY`: add `export OPENAI_API_KEY=...` to `~/.zshrc`, then `source ~/.zshrc`
- missing Pillow (step 5): `python3 -m pip install pillow`
- no bold font found (step 5): pass `--font /path/to/Bold.ttf`
- output of an earlier step missing, for example running step 6 without step 4: the message tells you which step to run first

The key is read from the environment only and never printed or written to a file.

## Options

All optional; the title and context are asked interactively instead.

| Option | Default | Description |
|--------|---------|-------------|
| `--out-dir` | next to the video | parent folder for the per-video folder |
| `--voice` | `nova` | OpenAI TTS voice |
| `--tts-model` | `gpt-4o-mini-tts` | speech model |
| `--text-model` | `gpt-4.1` | rewrite and shorten model |
| `--transcribe-model` | `whisper-1` | must return segment timestamps |
| `--language` | `en` | spoken language (ISO-639-1), empty for auto |
| `--style` | warm, conversational | speaking-style instructions |
| `--max-tempo` | `1.3` | max speed-up to fit a line into its slot |
| `--font` | auto | bold font for the title card |
| `--lead` | `1.0` | seconds each AI line starts before the original speech |
| `--end-grace` | `0.5` | seconds an AI line may run past the end of the original speech |
| `--context-file` | none | text file with project-specific terms and notes |
| `--validate-model` | `gpt-4o-transcribe` | independent model for the second transcription |
| `--no-snap` | off | do not align lines to the measured speech in the original audio |
| `--no-improve` | off | voice the raw transcript without rewriting |

## Assets

`assets/blank_screen.png` is the background for the title card and `assets/thank_you_card.png` is the end card. Replace either file to change the look. Both are scaled and cropped to fit the video size (portrait or landscape).

## Notes

- The final video is re-encoded (H.264, CRF 18) because the cards must be joined to it. The audio is AAC.
- Only speech is regenerated. Background music and sound effects from the original are dropped.
- Whisper timestamps are coarse; line starts and ends are aligned to the measured speech in the original audio.
- Cost is a few cents per minute of video.

## Code layout

```
ai_dub.py            interactive entry point
dubber/config.py     constants and prompts
dubber/ui.py         prompts and the single-line status
dubber/logs.py       JSON run log
dubber/review.py     human-in-the-loop checkpoints
dubber/script.py     transcript and script helpers
dubber/openai_api.py OpenAI calls
dubber/media.py      ffmpeg helpers and final assembly
dubber/speech.py     speech generation and fitting
dubber/cards.py      title and end cards
dubber/validate.py   transcript and timing validation
dubber/edit_ops.py   cut and narrate operations (video_edit.py)
dubber/timecode.py   mm.ss time parsing
dubber/edits.py      edit words (cut, mute, shift-audio, narrate, replace-voice)
dubber/project.py    project folder, versions, undo, pending edits
dubber/render.py     applies a batch of edits in one pass
dubber/pauses.py     [pause 2s] markers in narration text
dubber/speechmap.py  finds speech blocks from silences
dubber/plan.py       checks edits before rendering (clipped words, overlaps, time map)
dubber/deps.py       ffmpeg and API key checks
dubber/transcript.py transcript markdown files
dubber/pipeline.py   steps, dependency checks, output folder
```

# Editing a finished video: `video_edit.py`

A separate command-line tool for quick edits after the video is done. It does not touch the dubbing tool above and needs no pipeline: each edit is one command, and only what you ask for runs. Cutting uses no AI and costs nothing. Narrating makes one speech call (plus a short text-model call only if your text does not fit its window).

```bash
python3 video_edit.py cut demo.mp4 --range 00.10-00.17
python3 video_edit.py cut demo.mp4 --range 00.10-00.17 --range 01.05-01.20
python3 video_edit.py narrate demo.mp4 --start 00.30 --end 00.40 --text "Tap Continue to move on."
python3 video_edit.py narrate demo.mp4 --script narration.txt
python3 video_edit.py info demo.mp4
python3 video_edit.py                       # interactive: pick cut or narrate, repeat, then save
```

Times are `mm.ss`: `00.10` is 10 seconds and `01.05` is 1 minute 5 seconds (`mm:ss`, `h:mm:ss` and `75s` also work).

## Cut: remove part of the video

`--range START-END` removes that part from the picture and the sound, and the rest is joined together. Repeat `--range` for several parts (overlapping ranges are merged). An end past the end of the video is clipped. A short fade at each cut point avoids clicks. Works on videos with or without audio. The video is re-encoded (H.264, CRF 18) so cuts are frame-accurate.

## Narrate: add AI voice at a time

Generates the voice for your text and adds it at `--start`.

- With `--end` the narration must fit that window: it is sped up (up to `--max-tempo`, default 1.3) and, if it still does not fit, the text model shortens the sentence. Without `--end` it just plays from the start time.
- By default the voice is **mixed over** the original audio. Add `--replace` to mute the original audio inside each narration window instead.
- Several narrations at once: `--script FILE` with one per line, `START-END | text` or `START | text` (`#` starts a comment line).
- The picture is not re-encoded, only the audio changes. A video with no audio gets a new audio track.
- Options like `--voice`, `--tts-model` and `--style` work as in the dubbing tool.

## Interactive mode

Run `python3 video_edit.py` with no command. Choose cut or narrate, answer the prompts, and repeat as many times as you like; each edit applies to the result of the previous one. Choose `d` to save.

## Output

Next to the video, in a folder named after it:

```
demo_edits/
  demo_edited.mp4     the result (demo_edited_2.mp4, ... if it already exists; use --out to choose)
  logs/               log_<timestamp>.json for every run
  work/               intermediate files and cached voices
```

The original is never modified. Like the dubbing tool, the screen shows only one status line and the details go to the JSON log. You can edit the output of the dubbing tool directly, for example `online_delivery/online_delivery_ai.mp4`.


# Demo studio: `demo.py`

One front door for finishing a client demo video. No coding, nothing to remember: everything is saved, so you can close the terminal and continue later.

```
python3 demo.py
```

The menu lets you continue a recent project, start one from a video, or create the AI voice-over first (the `ai_dub.py` flow).

## How it keeps your work

Each video gets a project folder next to it, `my_video.demo/`:

```
project.json     source, settings, version history, pending edits
source.mp4       copy of your video, never modified
versions/        v1.mp4, v2.mp4 ... each applied batch of edits
cache/           generated voices, reused so re-applying costs nothing
logs/            one JSON log per run
```

- Add edits any time; they wait in a to-do list until you apply them.
- Each apply saves a new version. `undo` and `redo` move between versions.
- `export` copies the version you are on to `my_video_final.mp4` next to the original.
- Run `python3 demo.py` again, or any command with the project folder or the video path, to carry on.

## Edit words

```
cut 02.58-03.06                          remove that part of the video
mute 02.05-02.08                         silence the audio in that part
shift-audio 00.58 earlier 1s             move the speech that starts at 00.58 one second earlier
shift-audio 01.17 by -1s gap 2s          'gap' = silence that ends the speech block (default 2s)
narrate 00.48-00.52 "Click Next."        add AI voice at a time (window end optional)
replace-voice 02.05-02.08 "New text."    mute that part and speak new text there
narrate 04.00 "Line one. [pause 2s] Line two."   a pause inside the text (or: "Line one." pause 2s "Line two.")
```

Times: `00.10` is 10 seconds, `01.05` is 1 minute 5 seconds; `1:05` and `65s` also work.

**All times in one batch refer to the video as it is now** (the version marked `>`). When you apply, the tool places audio edits first and cuts last, so earlier cuts never shift later times. After applying, times refer to the new version.

`shift-audio` finds the speech that starts nearest the time you give (within 1.5 s) and moves it together with the following speech until a silence of `gap` seconds or longer. Run `map` first to see where speech starts and ends.

## Commands

```
python3 demo.py                                  guided menu
python3 demo.py new video.mp4                    start a project
python3 demo.py map video.demo                   show where the speech is
python3 demo.py add video.demo "cut 02.58-03.06" "shift-audio 00.58 earlier 1s"
python3 demo.py script video.demo edits.txt      add edits written in a file (one per line, # comments)
python3 demo.py status video.demo                history and pending edits
python3 demo.py remove video.demo 2              drop pending edit 2
python3 demo.py plan video.demo                  check pending edits, nothing rendered
python3 demo.py where video.demo 03.49           where that time lands after the pending cuts
python3 demo.py preview video.demo               quick low-quality try, nothing saved
python3 demo.py apply video.demo [--snap]        apply all pending edits
python3 demo.py undo video.demo                  and redo
python3 demo.py export video.demo --out final.mp4
python3 demo.py dub raw_recording.mp4            run the AI voice-over pipeline
python3 demo.py guide                            explain the edit words
```

`apply` shows what will happen and how many AI speech requests it makes, then asks to continue (`--yes` skips). Cut, mute and shift-audio are free; narrate and replace-voice use one speech request per line.

## Checks before rendering

`plan` (shown automatically before every `apply`, and as "Check" in the menu) lists each edit with:

- where it will be in the result, after the cuts (`-> at 03:32 in the result`);
- a warning when a cut, mute or replace window starts or ends in the middle of speech, and the clean edge it can be extended to (`--snap`, or answer Y when asked);
- a warning when an added voice would play over existing speech, using the real length of the voice if it was generated before, otherwise an estimate;
- an error when a voice sits inside a cut and would be removed (nothing is applied until you fix it).

Put every edit in one batch: the video is re-encoded once, whatever the number of cuts. Apply in several rounds only when you want to check the result between rounds.

## Notes

- Audio edits copy the picture unchanged; only the audio track is re-encoded (AAC), once per apply. Cuts re-encode the video (H.264, quality 18) so they are frame-accurate.
- A failed or interrupted apply changes nothing: pending edits stay and no version is saved.
- Your API key is read from `OPENAI_API_KEY` and is never written to the project or logs.
- `video_edit.py` and `ai_dub.py` still work on their own.
