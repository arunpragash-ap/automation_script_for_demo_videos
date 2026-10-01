#!/usr/bin/env python3
import argparse
import os
import platform
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dubber import config, logs, pipeline
from dubber.openai_api import ApiError
from dubber.ui import Abort, ask, choose, die, say, status


def parse_args():
    p = argparse.ArgumentParser(description="Replace a video's speech with natural AI voice-over using OpenAI. Details such as title, mode and context are asked interactively.")
    p.add_argument("video", nargs="?", help="input video containing an audio track (asked if omitted)")
    p.add_argument("--out-dir", help="parent folder for the per-video output folder (default: next to the video)")
    p.add_argument("--voice", default="nova")
    p.add_argument("--tts-model", default="gpt-4o-mini-tts")
    p.add_argument("--text-model", default="gpt-4.1")
    p.add_argument("--transcribe-model", default="whisper-1")
    p.add_argument("--validate-model", default="gpt-4o-transcribe", help="independent model used to cross-check the transcript")
    p.add_argument("--context-file", help="text file with project-specific glossary and notes, reused on every run (a line starting with 'Terms:' also helps transcription)")
    p.add_argument("--language", default="en", help="ISO-639-1 code of the spoken language, empty for auto")
    p.add_argument("--style", default=config.DEFAULT_STYLE, help="speaking style instructions for the TTS voice")
    p.add_argument("--max-tempo", type=float, default=1.3, help="max speed-up applied to fit a line into its slot")
    p.add_argument("--lead", type=float, default=1.0, help="seconds each AI line starts before the original speech started")
    p.add_argument("--end-grace", type=float, default=0.5, help="seconds an AI line may run past the end of the original speech")
    p.add_argument("--font", help="bold .ttf/.otf font for the title card (auto-detected by default)")
    p.add_argument("--no-snap", action="store_true", help="do not align line starts and ends to the measured speech in the original audio")
    p.add_argument("--no-improve", action="store_true", help="skip the AI rewrite and voice the raw transcript")
    return p.parse_args()


def has_previous_run(job):
    return os.path.isdir(job.work) and any(pipeline.is_done(job, s) for s in pipeline.STEPS)


def pick_steps(job):
    say("")
    say("Folder already exists: %s" % job.folder)
    for i, step in enumerate(pipeline.STEPS, 1):
        say("  %d. %-45s %s" % (i, step.title, "done" if pipeline.is_done(job, step) else "not done"))
    say("")
    say("What do you want to run?")
    say("  a    all steps again")
    say("  N    only step N (for example 4)")
    say("  N+   step N and every step after it (for example 4+)")
    say("  q    quit")
    while True:
        choice = ask("Choose", "q").lower()
        if choice == "q":
            return None
        if choice == "a":
            return list(pipeline.STEPS)
        digits = choice.rstrip("+")
        if digits.isdigit() and 1 <= int(digits) <= len(pipeline.STEPS):
            n = int(digits)
            return pipeline.STEPS[n - 1:] if choice.endswith("+") else [pipeline.STEPS[n - 1]]
        say("  enter a, q, N or N+ (N from 1 to %d)" % len(pipeline.STEPS))


def ask_title(job):
    if job.title:
        value = ask("Video title for the intro card [current: %s] (Enter keeps, '-' removes)" % job.title, show_default=False)
        job.title = "" if value == "-" else (value or job.title)
    else:
        job.title = ask("Video title for the intro card (Enter to skip the intro)")


def ask_mode(job):
    say("")
    mode = choose(
        "Which mode do you want?",
        {
            "a": "Auto - runs start to end with no questions",
            "h": "Human-in-the-loop - you review the transcript, guide the AI and approve the script before audio is generated",
        },
        "a",
    )
    job.mode = "human" if mode == "h" else "auto"
    if job.mode == "human":
        say("")
        if job.context:
            say("  current context: %s" % job.context)
        job.context = ask("Context and glossary rules for the script, e.g. Say 'QR code' not 'fuel code' (Enter keeps current)", job.context, show_default=False)
        job.terms = ask("Key terms or names to help transcription, comma-separated (Enter keeps current)", job.terms, show_default=False)


def start_log(job, opts, selected):
    logs.current.open(
        job.folder,
        video=job.video, folder=job.folder, mode=job.mode, title=job.title, context=job.context, terms=job.terms,
        selected_steps=[s.key for s in selected],
        options={k: v for k, v in vars(opts).items() if k != "video"},
        host=platform.node(),
    )


def run(job, opts, selected):
    start_log(job, opts, selected)
    outcome, error = "success", None
    try:
        pipeline.run_steps(job, selected)
    except KeyboardInterrupt:
        outcome, error = "interrupted", "interrupted by user"
        raise
    except (Abort, ApiError) as e:
        outcome, error = "failed", str(e)
        raise
    except Exception as e:
        outcome, error = "failed", "%s: %s" % (type(e).__name__, e)
        raise
    finally:
        status.stop()
        logs.current.finish(outcome, error)


def main():
    opts = parse_args()
    video = opts.video or ask("Path to the video")
    if not video or not os.path.isfile(os.path.abspath(os.path.expanduser(video))):
        die("video not found: %s" % video)
    if opts.context_file:
        opts.context_file = os.path.abspath(os.path.expanduser(opts.context_file))
        if not os.path.isfile(opts.context_file):
            die("context file not found: %s" % opts.context_file)
    job = pipeline.Job(os.path.expanduser(video), opts.out_dir, opts)
    job.load_settings()

    selected = list(pipeline.STEPS)
    if has_previous_run(job):
        selected = pick_steps(job)
        if selected is None:
            say("Nothing to do.")
            return
    say("")
    ask_title(job)
    ask_mode(job)

    problems = pipeline.find_problems(job, selected)
    if problems:
        say("")
        say("Cannot start. Fix these first:")
        for p in problems:
            say("  - " + p)
        sys.exit(1)

    started = time.time()
    os.makedirs(job.work, exist_ok=True)
    job.save_settings()
    say("")
    say("Output folder: %s" % job.folder)
    say("Running in %s mode. Detailed logs: %s" % ("human-in-the-loop" if job.mode == "human" else "auto", os.path.relpath(os.path.join(job.folder, "logs"), os.path.dirname(job.folder)) + "/"))
    run(job, opts, selected)

    say("")
    say("✓ Done in %.0fs. Original video untouched." % (time.time() - started))
    say("  Folder: %s" % job.folder)
    for path in (job.out_video, job.md_original, job.md_improved, job.report, logs.current.path):
        if path and os.path.exists(path):
            say("  " + os.path.relpath(path, job.folder))


if __name__ == "__main__":
    try:
        main()
    except (Abort, ApiError) as e:
        status.stop()
        say("")
        say("✗ Failed: %s" % e)
        if logs.current.path:
            say("  Details: %s" % logs.current.path)
        sys.exit(1)
    except KeyboardInterrupt:
        status.stop()
        say("")
        say("Interrupted.")
        if logs.current.path:
            say("  Details: %s" % logs.current.path)
        sys.exit(1)
