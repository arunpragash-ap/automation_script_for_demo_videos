import os
import shutil
import subprocess
import sys

from . import editor, openai_api, script, transcript, validate
from .ui import ask, choose, info, say, status, warn


def show_lines(path):
    say("")
    for seg_id, start, text in transcript.read_md_rows(path):
        say("  %3d  %s  %s" % (seg_id, start, text or "(merged)"))
    say("")


def open_in_editor(path):
    if sys.platform == "darwin":
        command = ["open", "-t", path]
    elif shutil.which("xdg-open"):
        command = ["xdg-open", path]
    else:
        warn("no editor launcher found, open the file manually: %s" % path)
        return
    subprocess.run(command, check=False)


def edit_in_terminal(path):
    texts = transcript.read_md_texts(path)
    while True:
        raw = ask("  Line number to edit (Enter to stop)", show_default=False)
        if not raw:
            return
        if not raw.isdigit() or int(raw) not in texts:
            warn("no such line: %s" % raw)
            continue
        seg_id = int(raw)
        say("  current: %s" % texts[seg_id])
        new = ask("  new text (Enter keeps it)", show_default=False)
        if new:
            transcript.update_md_text(path, seg_id, new)
            texts[seg_id] = new
            info("human edited line", segment=seg_id, text=new)
            say("  saved")


def edit_full_screen(path):
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        warn("no interactive terminal, using the line-by-line editor")
        edit_in_terminal(path)
        return
    rows = transcript.read_md_rows(path)
    changes = editor.edit_lines(rows, "Editing %s" % os.path.basename(path))
    if not changes:
        say("  no changes saved")
        return
    for seg_id, text in changes.items():
        transcript.update_md_text(path, seg_id, text)
        info("human edited line", segment=seg_id, text=text)
    say("  saved %d edited line(s)" % len(changes))


def review_loop(path, extra=None):
    options = {
        "p": "Proceed",
        "e": "Edit all lines in a full-screen editor (Enter edits a line, s saves)",
        "o": "Open the file in my text editor, then come back",
        "v": "Show the lines again",
    }
    if extra:
        options.update(extra)
    show_lines(path)
    while True:
        choice = choose("What next?", options, "p")
        if choice == "p":
            return
        if choice == "e":
            edit_full_screen(path)
            show_lines(path)
        elif choice == "o":
            open_in_editor(path)
            ask("  Save the file in your editor, then press Enter here", show_default=False)
            info("human edited the file in an editor", path=path)
            show_lines(path)
        elif choice == "v":
            show_lines(path)
        else:
            return choice


def show_flags(job):
    if not os.path.exists(job.validation_json):
        return
    flagged = [r for r in validate.load_json(job.validation_json) if r["verdict"] == "check"]
    if not flagged:
        say("  Validation: every line passed the timing and second-transcription checks.")
        return
    say("  Validation flagged %d line(s) (details in %s):" % (len(flagged), os.path.basename(job.validation_md)))
    for r in flagged:
        say("    line %d: %s" % (r["id"], "; ".join(r["issues"])))


def transcript_checkpoint(job):
    say("")
    say("Human review 1/2: raw transcript")
    say("  Fix wrongly heard words. Your edits feed the AI rewrite.")
    say("  File: %s" % job.md_original)
    show_flags(job)
    review_loop(job.md_original)
    info("human reviewed the raw transcript")


def apply_suggestion(job, suggestion):
    status.start("Applying your suggestion")
    segs = script.timeline(job)
    script.apply_texts(job, segs)
    openai_api.improve_segments(segs, job.opts.text_model, job.llm_context(), feedback=suggestion, reference=job.reference_text())
    script.write_improved(job, segs)
    status.stop()


def script_checkpoint(job):
    say("")
    say("Human review 2/2: narration script")
    say("  File: %s" % job.md_improved)
    while True:
        picked = review_loop(job.md_improved, {"s": "Give the AI a suggestion to change the whole script"})
        if picked != "s":
            info("human approved the script")
            return
        suggestion = ask("Your suggestion for the AI", show_default=False)
        if suggestion:
            info("human suggestion", suggestion=suggestion)
            apply_suggestion(job, suggestion)
            info("suggestion applied")
