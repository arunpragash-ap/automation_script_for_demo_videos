import importlib.util
import json
import os
import shutil
from dataclasses import dataclass
from typing import Callable

from . import cards, config, logs, media, openai_api, review, script, speech, transcript, validate
from .ui import die, fmt_time, info, ok, status, warn


class Job:
    def __init__(self, video, out_dir, opts):
        self.opts = opts
        self.video = os.path.abspath(video)
        self.name, self.ext = os.path.splitext(os.path.basename(self.video))
        base = os.path.abspath(out_dir) if out_dir else os.path.dirname(self.video)
        self.folder = os.path.join(base, self.name)
        self.work = os.path.join(self.folder, "work")
        self.title = ""
        self.context = ""
        self.terms = ""
        self.mode = "auto"

    def at(self, *parts):
        return os.path.join(self.folder, *parts)

    @property
    def wav(self):
        return os.path.join(self.work, "original.wav")

    @property
    def raw_json(self):
        return os.path.join(self.work, "transcript.json")

    @property
    def speech_json(self):
        return os.path.join(self.work, "speech.json")

    @property
    def title_png(self):
        return os.path.join(self.work, "title_card.png")

    @property
    def end_png(self):
        return os.path.join(self.work, "end_card.png")

    @property
    def validation_json(self):
        return os.path.join(self.work, "validation.json")

    @property
    def reference_txt(self):
        return os.path.join(self.work, "second_transcription.txt")

    def reference_text(self):
        if os.path.exists(self.reference_txt):
            with open(self.reference_txt) as f:
                return f.read().strip()
        return ""

    @property
    def validation_md(self):
        return self.at("validation.md")

    @property
    def title_voice_json(self):
        return os.path.join(self.work, "title_voice.json")

    @property
    def settings_json(self):
        return os.path.join(self.work, "settings.json")

    @property
    def md_original(self):
        return self.at("transcript.md")

    @property
    def md_improved(self):
        return self.at("transcript_improved.md")

    @property
    def report(self):
        return self.at("report.md")

    @property
    def out_video(self):
        return self.at("%s_ai%s" % (self.name, self.ext))

    def file_context(self):
        path = self.opts.context_file
        if path and os.path.exists(path):
            with open(path) as f:
                return f.read().strip()
        return ""

    def llm_context(self):
        parts = [self.file_context(), self.context, "Video title: " + self.title if self.title else ""]
        return "\n".join(p for p in parts if p)

    def whisper_terms(self):
        from_file = [line.split(":", 1)[1].strip() for line in self.file_context().splitlines() if line.lower().startswith("terms:")]
        return ", ".join(t for t in [self.terms] + from_file + [self.title] if t)

    def load_settings(self):
        if os.path.exists(self.settings_json):
            with open(self.settings_json) as f:
                data = json.load(f)
            self.title = data.get("title", "")
            self.context = data.get("context", "")
            self.terms = data.get("terms", "")

    def save_settings(self):
        os.makedirs(self.work, exist_ok=True)
        with open(self.settings_json, "w") as f:
            json.dump({"title": self.title, "context": self.context, "terms": self.terms}, f, indent=2)


def run_extract(job):
    if not media.has_audio(job.video):
        die("the video has no audio track")
    media.extract_audio(job.video, job.wav)
    ok("saved %s" % os.path.relpath(job.wav, job.folder))


def remove_title_intro(job, segs):
    if not job.title or not segs:
        return segs
    result = openai_api.detect_title_intro(job.title, [s["text"] for s in segs[:3]], job.opts.text_model)
    sentence = str(result.get("text") or "").strip()
    if not result.get("remove") or not sentence or sentence not in segs[0]["text"]:
        info("opening sentence kept (it does not just repeat the title)", title=job.title)
        return segs
    rest = segs[0]["text"].replace(sentence, "", 1).strip()
    if rest:
        segs[0]["text"] = rest
    else:
        segs = segs[1:]
        for i, s in enumerate(segs):
            s["id"] = i
    info("removed the opening sentence that repeats the title", sentence=sentence, title=job.title)
    return segs


def remove_closing_thanks(job, segs):
    if not segs:
        return segs
    tail = segs[-2:]
    result = openai_api.detect_closing_thanks([s["text"] for s in tail], job.opts.text_model)
    removed = []
    for sentence in [str(t).strip() for t in result.get("texts") or [] if str(t).strip()]:
        for s in tail:
            if sentence in s["text"]:
                s["text"] = s["text"].replace(sentence, "", 1).strip()
                removed.append(sentence)
                break
    if removed:
        segs = [s for s in segs if s["text"]]
        for i, s in enumerate(segs):
            s["id"] = i
        info("removed closing thank-you from the transcript, the end card says it", sentences=removed)
    return segs


def run_transcribe(job):
    prompt = config.DEFAULT_TRANSCRIBE_PROMPT
    extras = job.whisper_terms()
    if extras:
        prompt += " Terms: %s." % extras
    segs = openai_api.transcribe(job.wav, job.opts.transcribe_model, job.opts.language, prompt)
    segs = remove_title_intro(job, segs)
    segs = remove_closing_thanks(job, segs)
    segs, dropped = media.drop_hallucinations(segs, media.detect_silences(job.wav))
    if dropped:
        info("removed %d repeated or silent line(s) Whisper invented" % len(dropped), dropped=dropped)
    with open(job.raw_json, "w") as f:
        json.dump({"segments": segs}, f, indent=2)
    transcript.write_md(
        job.md_original, "Transcript: " + os.path.basename(job.video),
        "Source: OpenAI `%s`. Raw machine transcript with segment timings." % job.opts.transcribe_model, segs)
    ok("%d timestamped segments" % len(segs))
    ok("wrote %s" % os.path.basename(job.md_original))


def run_validate(job):
    segs = script.timeline(job)
    silences = media.detect_silences(job.wav)
    reference = None
    try:
        compressed = os.path.join(job.work, "validate.mp3")
        media.run("ffmpeg", "-y", "-i", job.wav, "-ac", "1", "-b:a", "48k", compressed)
        reference = openai_api.transcribe_text(compressed, job.opts.validate_model, job.opts.language)
        with open(job.reference_txt, "w") as f:
            f.write(reference)
        info("second transcription done", model=job.opts.validate_model, words=len(validate.words(reference)))
    except Exception as e:
        warn("second opinion unavailable, validating timing only: %s" % e)
    results = validate.check_lines(segs, silences, reference)
    validate.save_json(job.validation_json, results)
    validate.write_report(job.validation_md, os.path.basename(job.video), results, reference is not None, job.opts.validate_model)
    flagged = [r for r in results if r["verdict"] == "check"]
    ok("%d line(s) checked, %d need a look" % (len(results), len(flagged)), flagged=[{"id": r["id"], "issues": r["issues"]} for r in flagged])
    ok("wrote %s" % os.path.basename(job.validation_md))


def run_improve(job):
    segs = script.timeline(job)
    if job.opts.no_improve:
        info("--no-improve: voicing the raw transcript")
    else:
        openai_api.improve_segments(segs, job.opts.text_model, job.llm_context(), reference=job.reference_text())
    script.write_improved(job, segs)
    ok("wrote %s" % os.path.basename(job.md_improved))


def run_speech(job):
    segs = script.timeline(job)
    script.apply_texts(job, segs)
    speech.synth_and_fit(segs, job.opts, job.work, job.llm_context())
    script.write_improved(job, segs)
    thanks_wav, thanks_dur = speech.make_thanks(job.opts, job.work)
    ok("end card voice %r (%.1fs)" % (config.THANKS_TEXT, thanks_dur))
    keys = ("id", "start", "end", "place", "slot", "text", "wav", "dur", "over")
    with open(job.speech_json, "w") as f:
        json.dump({"segments": [{k: s.get(k) for k in keys} for s in segs], "thanks": {"wav": thanks_wav, "dur": thanks_dur}}, f, indent=2)


def run_cards(job):
    width, height, _ = media.probe_video(job.video)
    if job.title:
        lines = cards.render_title_card(config.BLANK_SCREEN, job.title, (width, height), job.title_png, cards.find_font(job.opts.font))
        ok("title card %dx%d, %d line(s): %s" % (width, height, len(lines), " / ".join(lines)))
        text = config.TITLE_VOICE_TEMPLATE.format(title=job.title)
        wav, dur = speech.make_voice(text, job.opts, job.work, "title")
        seconds = max(config.INTRO_SECONDS, config.TITLE_VOICE_DELAY + dur + config.TITLE_VOICE_TAIL)
        with open(job.title_voice_json, "w") as f:
            json.dump({"wav": wav, "dur": dur, "seconds": seconds, "text": text}, f, indent=2)
        ok("title voice %r (%.1fs), intro lasts %.1fs" % (text, dur, seconds))
    else:
        for stale in (job.title_png, job.title_voice_json):
            if os.path.exists(stale):
                os.remove(stale)
                info("no title given, removed old title card files")
    cards.fit_end_card(config.END_CARD, (width, height), job.end_png)
    ok("end card %dx%d" % (width, height))


def run_assemble(job):
    with open(job.speech_json) as f:
        data = json.load(f)
    segs = data["segments"]
    total = media.probe_duration(job.video)
    track = media.build_track(segs, total, job.work, job.opts.max_tempo)
    ok("mixed %d lines, denoised, loudness-normalised" % sum(1 for s in segs if s["text"].strip()))
    _, _, fps = media.probe_video(job.video)
    intro = None
    extra = config.END_SECONDS
    if job.title:
        with open(job.title_voice_json) as f:
            voice = json.load(f)
        intro = {"png": job.title_png, "wav": voice["wav"], "seconds": voice["seconds"]}
        extra += voice["seconds"]
    media.assemble(job.video, track, intro, job.end_png, data["thanks"]["wav"], job.out_video, fps)
    ok("wrote %s (%.1fs = %.1fs video + %.1fs cards)" % (os.path.basename(job.out_video), media.probe_duration(job.out_video), total, extra))
    write_report(job, segs, total)
    ok("wrote %s" % os.path.basename(job.report))


def write_report(job, segs, total):
    o = job.opts
    lines = [
        "# AI Dub Report: %s" % os.path.basename(job.video), "",
        "- Output: `%s`" % os.path.basename(job.out_video),
        "- Source duration: %s" % fmt_time(total),
        "- Title card: %s" % (job.title or "none"),
        "- Title voice: %s" % (config.TITLE_VOICE_TEMPLATE.format(title=job.title) if job.title else "none"),
        "- End card: Thank You (%ds, voice `%s`)" % (config.END_SECONDS, config.THANKS_TEXT),
        "- Transcription model: `%s`" % o.transcribe_model,
        "- Text model: `%s`" % o.text_model,
        "- TTS model / voice: `%s` / `%s`" % (o.tts_model, o.voice),
        "- Max speed-up: %.2fx" % o.max_tempo,
        "- Lead (AI starts earlier than the original): %.1fs, end grace: %.1fs" % (o.lead, o.end_grace), "",
        "| # | Original start | AI plays at | Slot (s) | Speech (s) | Speed | Status | Text |",
        "|---|----------------|-------------|----------|------------|-------|--------|------|",
    ]
    for s in segs:
        if not s["text"].strip():
            lines.append("| %d | %s | %s | %.2f | - | - | merged | (empty) |" % (s["id"], fmt_time(s["start"]), fmt_time(s["place"]), s["slot"]))
            continue
        tempo = media.tempo_for(s, o.max_tempo)
        lines.append("| %d | %s | %s | %.2f | %.2f | %.2fx | %s | %s |" % (
            s["id"], fmt_time(s["start"]), fmt_time(s["place"]), s["slot"], s["dur"] / tempo, tempo,
            "OVERFLOW" if s["over"] else "ok", s["text"].replace("|", "\\|")))
    with open(job.report, "w") as f:
        f.write("\n".join(lines) + "\n")


@dataclass
class Step:
    key: str
    title: str
    run: Callable
    needs: Callable
    outputs: Callable


FFMPEG = ["ffmpeg", "ffprobe"]

STEPS = [
    Step("extract", "Extract audio", run_extract,
         lambda j: {"tools": FFMPEG, "files": [(j.video, None)]},
         lambda j: [j.wav]),
    Step("transcribe", "Transcribe with OpenAI", run_transcribe,
         lambda j: {"api": True, "files": [(j.wav, "extract")]},
         lambda j: [j.raw_json, j.md_original]),
    Step("validate", "Validate transcript and timing", run_validate,
         lambda j: {"api": True, "tools": FFMPEG, "files": [(j.raw_json, "transcribe"), (j.wav, "extract")]},
         lambda j: [j.validation_md]),
    Step("improve", "Improve the script (grammar + context)", run_improve,
         lambda j: {"api": not j.opts.no_improve, "tools": FFMPEG, "files": [(j.raw_json, "transcribe"), (j.wav, "extract")]},
         lambda j: [j.md_improved]),
    Step("speech", "Generate AI speech and fit timings", run_speech,
         lambda j: {"api": True, "tools": FFMPEG, "files": [(j.raw_json, "transcribe"), (j.wav, "extract"), (j.md_improved, "improve")]},
         lambda j: [j.speech_json]),
    Step("cards", "Build title and thank-you cards", run_cards,
         lambda j: {"api": bool(j.title), "tools": FFMPEG, "pillow": True, "font": True, "files": [(config.BLANK_SCREEN, None), (config.END_CARD, None)]},
         lambda j: [j.end_png] + ([j.title_png, j.title_voice_json] if j.title else [])),
    Step("assemble", "Mix audio and assemble the final video", run_assemble,
         lambda j: {"tools": FFMPEG, "files": [(j.speech_json, "speech"), (j.end_png, "cards")] + ([(j.title_png, "cards"), (j.title_voice_json, "cards")] if j.title else [])},
         lambda j: [j.out_video, j.report]),
]

INSTALL_HINTS = {
    "ffmpeg": "brew install ffmpeg",
    "ffprobe": "brew install ffmpeg",
}


def step_index(key):
    return next(i for i, s in enumerate(STEPS, 1) if s.key == key)


def is_done(job, step):
    return all(os.path.exists(p) for p in step.outputs(job))


def find_problems(job, steps):
    found, produced = [], set()
    for step in steps:
        label = "Step %d (%s)" % (step_index(step.key), step.title)
        need = step.needs(job)
        for tool in need.get("tools", []):
            if not shutil.which(tool):
                found.append("%s needs '%s'. Install it with: %s" % (label, tool, INSTALL_HINTS[tool]))
        if need.get("api") and not os.environ.get("OPENAI_API_KEY", "").strip():
            found.append("%s needs OPENAI_API_KEY. Add `export OPENAI_API_KEY=...` to ~/.zshrc, then: source ~/.zshrc" % label)
        if need.get("pillow") and importlib.util.find_spec("PIL") is None:
            found.append("%s needs Pillow for text rendering. Install it with: python3 -m pip install pillow" % label)
        if need.get("font") and cards.find_font(job.opts.font) is None:
            found.append("%s needs a bold font but none was found. Pass --font /path/to/Bold.ttf" % label)
        for path, producer in need.get("files", []):
            if path in produced or os.path.exists(path):
                continue
            hint = "run step %d (%s) first" % (step_index(producer), next(s.title for s in STEPS if s.key == producer)) if producer else "restore this file"
            found.append("%s is missing %s. %s" % (label, os.path.relpath(path, job.folder) if path.startswith(job.folder) else path, hint))
        produced.update(step.outputs(job))
    return found


CHECKPOINTS = {
    "validate": review.transcript_checkpoint,
    "improve": review.script_checkpoint,
}


def run_steps(job, steps):
    total = len(STEPS)
    for step in steps:
        index = step_index(step.key)
        logs.current.step_start(step.key, step.title, index)
        status.start("[%d/%d] %s" % (index, total, step.title))
        try:
            step.run(job)
        except BaseException as e:
            status.stop()
            logs.current.step_end("interrupted" if isinstance(e, KeyboardInterrupt) else "failed", str(e) or type(e).__name__)
            raise
        status.stop()
        logs.current.step_end("success")
        if job.mode == "human" and step.key in CHECKPOINTS:
            logs.current.step_key = step.key
            CHECKPOINTS[step.key](job)
            logs.current.step_key = None
