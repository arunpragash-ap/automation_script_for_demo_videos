#!/usr/bin/env python3
import argparse
import os
import platform
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dubber import config, edit_ops, logs, media, timecode
from dubber.deps import require
from dubber.openai_api import ApiError
from dubber.ui import Abort, ask, choose, die, say, status

EPILOG = """examples:
  python3 video_edit.py cut demo.mp4 --range 00.10-00.17
  python3 video_edit.py cut demo.mp4 --range 00.10-00.17 --range 01.05-01.20
  python3 video_edit.py narrate demo.mp4 --start 00.30 --end 00.40 --text "Tap Continue to move on."
  python3 video_edit.py narrate demo.mp4 --script narration.txt --replace
  python3 video_edit.py info demo.mp4
  python3 video_edit.py                     (interactive: pick cut or narrate, repeat, then save)

times are mm.ss: 00.10 is 10 seconds, 01.05 is 1 minute 5 seconds (mm:ss and h:mm:ss also work).
narration script lines:  START-END | text    or    START | text    (# starts a comment line)"""


def common_options():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--out", help="output file (default: <video folder>/<name>_edits/<name>_edited<ext>)")
    p.add_argument("--voice", default="nova")
    p.add_argument("--tts-model", default="gpt-4o-mini-tts")
    p.add_argument("--text-model", default="gpt-4.1")
    p.add_argument("--style", default=config.DEFAULT_STYLE, help="speaking style for the narration voice")
    p.add_argument("--max-tempo", type=float, default=1.3, help="max speed-up to fit narration into its time window")
    p.add_argument("--replace", action="store_true", help="narrate: mute the original audio inside each narration window instead of mixing over it")
    return p


def parse_args():
    common = common_options()
    p = argparse.ArgumentParser(description="Edit a finished demo video: cut parts out or add narration at a time.", epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter, parents=[common])
    sub = p.add_subparsers(dest="command")
    cut = sub.add_parser("cut", help="remove parts of the video (no AI, no cost)", parents=[common])
    cut.add_argument("video")
    cut.add_argument("--range", action="append", default=[], metavar="START-END", help="part to remove, e.g. 00.10-00.17 (repeatable)")
    cut.add_argument("--start", help="start of the part to remove")
    cut.add_argument("--end", help="end of the part to remove")
    narrate = sub.add_parser("narrate", help="generate AI voice for a text and add it at a time", parents=[common])
    narrate.add_argument("video")
    narrate.add_argument("--start", help="time the narration starts")
    narrate.add_argument("--end", help="end of the window the narration must fit in (optional)")
    narrate.add_argument("--text", help="what the voice says")
    narrate.add_argument("--text-file", help="read the narration text from a file")
    narrate.add_argument("--script", help="file with several narrations: START-END | text")
    info = sub.add_parser("info", help="show length, size and audio of a video")
    info.add_argument("video")
    return p.parse_args()


class Session:
    def __init__(self, video, out=None):
        self.source = os.path.abspath(os.path.expanduser(video))
        if not os.path.isfile(self.source):
            die("video not found: %s" % video)
        self.stem, self.ext = os.path.splitext(os.path.basename(self.source))
        self.folder = os.path.join(os.path.dirname(self.source), self.stem + "_edits")
        self.work = os.path.join(self.folder, "work")
        self.current = self.source
        self.count = 0
        self.out = os.path.abspath(os.path.expanduser(out)) if out else None
        self.history = []
        os.makedirs(self.work, exist_ok=True)

    def final_path(self):
        if self.out:
            return self.out
        path = os.path.join(self.folder, "%s_edited%s" % (self.stem, self.ext))
        n = 2
        while os.path.exists(path):
            path = os.path.join(self.folder, "%s_edited_%d%s" % (self.stem, n, self.ext))
            n += 1
        return path

    def step(self, key, title, func):
        self.count += 1
        target = os.path.join(self.work, "step%d%s" % (self.count, self.ext))
        logs.current.step_start(key, title, self.count)
        status.start(title)
        try:
            result = func(target)
        except BaseException as e:
            status.stop()
            logs.current.step_end("interrupted" if isinstance(e, KeyboardInterrupt) else "failed", str(e) or type(e).__name__)
            raise
        status.stop()
        logs.current.step_end("success")
        self.current = target
        self.history.append(key)
        return result

    def finish(self):
        if self.current == self.source:
            say("No edits were made.")
            return None
        final = self.final_path()
        os.makedirs(os.path.dirname(final), exist_ok=True)
        shutil.copyfile(self.current, final)
        return final


def open_log(session, command, args):
    logs.current.open(session.folder, tool="video_edit", command=command, source=session.source,
                      options={k: v for k, v in vars(args).items() if k not in ("video",)}, host=platform.node())


def do_cut(session, ranges):
    return session.step("cut", "Cutting the video", lambda out: edit_ops.cut(session.current, ranges, out))


def do_narrate(session, items, args):
    return session.step("narrate", "Generating and adding narration", lambda out: edit_ops.narrate(session.current, items, out, args, session.work))


def collect_cut_ranges(args):
    ranges = [timecode.parse_range(r) for r in args.range]
    if args.start or args.end:
        if not (args.start and args.end):
            die("give both --start and --end")
        ranges.append(timecode.parse_range("%s-%s" % (args.start, args.end)))
    return ranges


def collect_narrations(args):
    items = []
    if args.script:
        items += edit_ops.parse_script(os.path.expanduser(args.script))
    text = args.text
    if args.text_file:
        with open(os.path.expanduser(args.text_file)) as f:
            text = f.read().strip()
    if text:
        if not args.start:
            die("--text needs --start")
        items.append({"start": timecode.parse_time(args.start), "end": timecode.parse_time(args.end) if args.end else None, "text": text})
    return items


def command_info(video):
    path = os.path.abspath(os.path.expanduser(video))
    if not os.path.isfile(path):
        die("video not found: %s" % video)
    width, height, fps = media.probe_video(path)
    say("")
    say("  file      : %s" % path)
    say("  length    : %s (%.2fs)" % (timecode.format_time(media.probe_duration(path)), media.probe_duration(path)))
    say("  size      : %dx%d at %s fps" % (width, height, fps))
    say("  audio     : %s" % ("yes" if media.has_audio(path) else "no"))
    say("  file size : %.1f MB" % (os.path.getsize(path) / 1e6))


def run_single(args):
    require(args.command == "narrate")
    session = Session(args.video, args.out)
    if args.command == "cut":
        ranges = collect_cut_ranges(args)
        if not ranges:
            die("nothing to cut. Give --range START-END (for example --range 00.10-00.17)")
        open_log(session, "cut", args)
        do_cut(session, ranges)
    else:
        items = collect_narrations(args)
        if not items:
            die("nothing to narrate. Give --start with --text, or --script FILE")
        open_log(session, "narrate", args)
        do_narrate(session, items, args)
    return session


def interactive_cut(session):
    say("  Current length: %s" % timecode.format_time(media.probe_duration(session.current)))
    raw = ask("Parts to remove, as START-END (for example 00.10-00.17; separate several with commas)", show_default=False)
    if not raw:
        return False
    do_cut(session, [timecode.parse_range(part) for part in raw.split(",") if part.strip()])
    return True


def interactive_narrate(session, args):
    say("  Current length: %s" % timecode.format_time(media.probe_duration(session.current)))
    items = []
    while True:
        start = ask("Narration start time (mm.ss, Enter to finish adding)", show_default=False)
        if not start:
            break
        end = ask("Window end time (mm.ss, Enter = no limit)", show_default=False)
        text = ask("What should the voice say", show_default=False)
        if not text:
            continue
        items.append({"start": timecode.parse_time(start), "end": timecode.parse_time(end) if end else None, "text": text})
        say("  added %d narration(s)" % len(items))
    if not items:
        return False
    args.replace = ask("Mute the original audio inside those windows? (y/N)", "n").lower().startswith("y")
    require(True)
    do_narrate(session, items, args)
    return True


def run_interactive(args):
    video = ask("Path to the video")
    if not video:
        die("no video given")
    session = Session(video, args.out)
    open_log(session, "interactive", args)
    while True:
        say("")
        say("Working on: %s (%s)" % (os.path.basename(session.current), timecode.format_time(media.probe_duration(session.current))))
        action = choose(
            "What do you want to do?",
            {"c": "Cut - remove part of the video", "n": "Narrate - add AI voice at a time", "i": "Info about the current video", "d": "Done - save and finish", "q": "Quit without saving"},
            "d" if session.history else "c",
        )
        if action == "q":
            return None
        if action == "d":
            return session
        if action == "i":
            command_info(session.current)
        elif action == "c":
            require(False)
            interactive_cut(session)
        else:
            interactive_narrate(session, args)


def main():
    args = parse_args()
    if args.command == "info":
        require(False)
        command_info(args.video)
        return
    try:
        session = run_single(args) if args.command else run_interactive(args)
    finally:
        status.stop()
    if session is None:
        logs.current.finish("cancelled")
        say("Nothing saved.")
        return
    final = session.finish()
    logs.current.finish("success")
    if final:
        say("")
        say("✓ Saved %s (%s)" % (final, timecode.format_time(media.probe_duration(final))))
        say("  Original untouched. Log: %s" % logs.current.path)


if __name__ == "__main__":
    try:
        main()
    except (Abort, ApiError) as e:
        status.stop()
        logs.current.finish("failed", str(e))
        say("")
        say("✗ Failed: %s" % e)
        if logs.current.path:
            say("  Details: %s" % logs.current.path)
        sys.exit(1)
    except KeyboardInterrupt:
        status.stop()
        logs.current.finish("interrupted", "interrupted by user")
        say("")
        say("Interrupted.")
        sys.exit(1)
