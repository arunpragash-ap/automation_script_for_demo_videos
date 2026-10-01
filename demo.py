#!/usr/bin/env python3
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dubber import edits as edit_model
from dubber import logs, media, plan, render, speechmap, timecode
from dubber import project as projects
from dubber.deps import require
from dubber.openai_api import ApiError
from dubber.ui import Abort, ask, choose, say, status

HERE = os.path.dirname(os.path.abspath(__file__))

EPILOG = """examples:
  python3 demo.py                                       (guided menu, nothing to remember)
  python3 demo.py new offline_flow.mp4                  (start a project for a video)
  python3 demo.py map offline_flow.demo                 (where is the speech?)
  python3 demo.py add offline_flow.demo "cut 02.58-03.06" "shift-audio 00.58 earlier 1s"
  python3 demo.py apply offline_flow.demo               (applies everything added so far)
  python3 demo.py undo offline_flow.demo
  python3 demo.py export offline_flow.demo              (saves offline_flow_final.mp4)
  python3 demo.py dub new_video.mp4                     (create the AI voice-over from scratch)

""" + edit_model.GUIDE


def model_options():
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--voice")
    p.add_argument("--tts-model")
    p.add_argument("--text-model")
    p.add_argument("--style")
    p.add_argument("--max-tempo", type=float)
    return p


def parse_args():
    models = model_options()
    p = argparse.ArgumentParser(description="Demo video studio: cut, fix audio timing and add AI voice, with undo.", epilog=EPILOG,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command")
    new = sub.add_parser("new", help="start a project for a video", parents=[models])
    new.add_argument("video")
    add = sub.add_parser("add", help="add edits to the to-do list", parents=[models])
    add.add_argument("target")
    add.add_argument("edit", nargs="+", help='for example "cut 02.58-03.06"')
    status_cmd = sub.add_parser("status", help="show history and pending edits")
    status_cmd.add_argument("target")
    remove = sub.add_parser("remove", help="remove one pending edit")
    remove.add_argument("target")
    remove.add_argument("number", type=int)
    clear = sub.add_parser("clear", help="remove all pending edits")
    clear.add_argument("target")
    for name, text in (("apply", "apply all pending edits and save a new version"), ("preview", "apply pending edits quickly at low quality, nothing is saved")):
        sp = sub.add_parser(name, help=text, parents=[models])
        sp.add_argument("target")
        sp.add_argument("--yes", action="store_true", help="do not ask for confirmation")
        sp.add_argument("--label", help="name for this version")
        sp.add_argument("--snap", action="store_true", help="extend cuts and mutes that start or end inside speech to clean speech edges")
    for name, text in (("undo", "go back to the previous version"), ("redo", "go forward again")):
        sp = sub.add_parser(name, help=text)
        sp.add_argument("target")
    check = sub.add_parser("plan", help="check the pending edits and show where everything will land, without rendering")
    check.add_argument("target")
    where = sub.add_parser("where", help="where a time in the current video will be after the pending cuts")
    where.add_argument("target")
    where.add_argument("time")
    audio = sub.add_parser("map", help="show where the speech starts and stops")
    audio.add_argument("target")
    audio.add_argument("--gap", type=float, default=0.6, help="silence that separates two blocks (seconds)")
    audio.add_argument("--from", dest="from_time")
    audio.add_argument("--to", dest="to_time")
    script = sub.add_parser("script", help="add the edits written in a text file", parents=[models])
    script.add_argument("target")
    script.add_argument("file")
    script.add_argument("--apply", action="store_true", help="apply them straight away")
    script.add_argument("--yes", action="store_true")
    export = sub.add_parser("export", help="save the current version as the final video")
    export.add_argument("target")
    export.add_argument("--out")
    dub = sub.add_parser("dub", help="create the AI voice-over for a raw screen recording (runs ai_dub.py)")
    dub.add_argument("rest", nargs=argparse.REMAINDER)
    sub.add_parser("guide", help="explain the edit words")
    return p.parse_args()


def settings_from(args):
    return {"voice": getattr(args, "voice", None), "tts_model": getattr(args, "tts_model", None),
            "text_model": getattr(args, "text_model", None), "style": getattr(args, "style", None),
            "max_tempo": getattr(args, "max_tempo", None)}


def open_project(target, args=None):
    settings = settings_from(args) if args else None
    project, created = projects.resolve(target, settings)
    if created:
        say("Created project folder: %s" % project.folder)
    elif settings:
        project.set_settings(**settings)
    return project


def open_log(project, command):
    logs.current.open(project.folder, tool="demo", command=command, project=project.name, version=project.versions[project.cursor]["id"], host=platform.node())


def seconds_text(seconds):
    return timecode.format_time(seconds) if seconds is not None else "?"


def show_status(project):
    say("")
    say("Project: %s" % project.name)
    say("Folder : %s" % project.folder)
    say("")
    say("Versions (> is the one you are on):")
    for index, version in enumerate(project.versions):
        mark = ">" if index == project.cursor else " "
        say("  %s v%-2d %-8s %s" % (mark, version["id"], seconds_text(version.get("seconds")), version["label"]))
    say("")
    if project.pending:
        say("Pending edits (applied together to the version marked >):")
        for number, edit in enumerate(project.pending, 1):
            say("  %d. %s" % (number, edit_model.describe(edit)))
    else:
        say("No pending edits.")


def show_plan(project, rows):
    say("")
    say("Plan for v%d (times are in the video as it is now):" % project.versions[project.cursor]["id"])
    for row in rows:
        edit = row["edit"]
        line = "  %d. %s" % (row["index"] + 1, edit_model.describe(edit))
        if edit["type"] != "cut" and row["final"] is not None:
            line += "  -> at %s in the result" % timecode.format_time(row["final"])
        say(line)
        for level, text in row["notes"]:
            say("       %s %s" % ("✗" if level == "error" else "!", text))
    calls = sum(len(edit_model.parts(r["edit"])) for r in rows if edit_model.uses_api(r["edit"]))
    cut_total = sum(r["edit"]["end"] - r["edit"]["start"] for r in rows if r["edit"]["type"] == "cut")
    say("  AI speech requests: %d%s. Video length change: -%.1fs" % (calls, "" if calls else " (no cost)", cut_total))


def make_plan(project):
    if not project.pending:
        raise Abort("no pending edits. Add some first, for example: python3 demo.py add %s \"cut 00.10-00.17\"" % os.path.basename(project.folder))
    status.start("Checking the edits against the audio")
    try:
        return plan.analyse(project.current_file(), project.pending, project.opts(), project.cache)
    finally:
        status.stop()


def confirm_plan(project, yes, snap):
    rows, _ = make_plan(project)
    show_plan(project, rows)
    if plan.has_errors(rows):
        raise Abort("fix the problems marked ✗ first (remove the edit with: python3 demo.py remove %s NUMBER)" % os.path.basename(project.folder))
    if plan.has_snaps(rows):
        if snap or (not yes and ask("Extend the marked edges to clean speech edges? (Y/n)", "y").lower().startswith("y")):
            project.data["pending"] = plan.snap_edits(project.pending, rows)
            project.save()
            say("  Edges extended.")
            rows, _ = make_plan(project)
            show_plan(project, rows)
    if yes:
        return True
    return ask("Apply now? (Y/n)", "y").lower().startswith("y")


def apply_pending(project, quick=False, yes=False, label=None, snap=False):
    require(any(edit_model.uses_api(e) for e in project.pending))
    if not confirm_plan(project, yes, snap):
        say("Nothing changed.")
        return None
    open_log(project, "preview" if quick else "apply")
    edits = list(project.pending)
    ext = os.path.splitext(project.versions[0]["file"])[1]
    if quick:
        os.makedirs(project.path("previews"), exist_ok=True)
        out = project.path("previews", "preview" + ext)
    else:
        out = project.next_version_path()
    logs.current.step_start("render", "Applying edits", 1)
    status.start("Applying %d edit(s)" % len(edits))
    try:
        render.render(project.current_file(), edits, out, project.opts(), project.work, project.cache, quick)
    except BaseException as e:
        status.stop()
        logs.current.step_end("failed", str(e) or type(e).__name__)
        if os.path.exists(out):
            os.remove(out)
        raise
    status.stop()
    logs.current.step_end("success")
    if quick:
        logs.current.finish("success")
        say("")
        say("✓ Preview (low quality, not saved as a version): %s" % out)
        say("  Pending edits are still waiting. Run apply to keep them.")
        return out
    project.commit(edits, out, label)
    logs.current.finish("success")
    say("")
    say("✓ Saved version v%d (%s): %s" % (project.versions[project.cursor]["id"], seconds_text(project.versions[project.cursor]["seconds"]), out))
    say("  Undo any time with: python3 demo.py undo %s" % os.path.basename(project.folder))
    return out


def print_map(project, gap, start=None, end=None):
    path = project.current_file()
    status.start("Listening to the audio")
    try:
        spans, total = speechmap.analyse(path)
    finally:
        status.stop()
    lo = timecode.parse_time(start) if start else 0.0
    hi = timecode.parse_time(end) if end else total
    blocks = speechmap.group(spans, gap)
    say("")
    say("Speech in v%d (%s long). A block ends when silence lasts %.1fs or more." % (project.versions[project.cursor]["id"], timecode.format_time(total), gap))
    say("")
    say("   #   starts     ends      length   quiet before")
    previous = 0.0
    shown = 0
    for number, (s, e) in enumerate(blocks, 1):
        if e >= lo and s <= hi:
            say("  %3d  %-9s  %-9s  %5.1fs   %5.1fs" % (number, timecode.format_time(s), timecode.format_time(e), e - s, s - previous))
            shown += 1
        previous = e
    if not shown:
        say("  no speech in that range")
    say("")
    say("Use a 'starts' time in shift-audio, or any time in cut, mute, narrate and replace-voice.")


def do_export(project, out=None):
    if project.cursor == 0:
        raise Abort("this is still the original video. Apply some edits first.")
    final = project.export_path(out)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    shutil.copyfile(project.current_file(), final)
    say("✓ Saved %s (%s)" % (final, seconds_text(project.versions[project.cursor]["seconds"])))
    return final


def read_script(path):
    lines = []
    with open(os.path.expanduser(path)) as f:
        for raw in f:
            line = raw.strip()
            if line and not line.startswith("#"):
                lines.append(line)
    if not lines:
        raise Abort("%s has no edits" % path)
    return lines


def attempt(action):
    try:
        return action()
    except Abort as e:
        say("")
        say("  ✗ %s" % e)
        return None


def guided_add(project):
    kinds = {
        "1": "Cut - remove part of the video",
        "2": "Add voice - AI says something at a time",
        "3": "Replace voice - mute part of the audio and say something new there",
        "4": "Move speech - make a spoken part start earlier or later",
        "5": "Silence - mute part of the audio",
        "6": "Type the edit in words",
        "b": "Back",
    }
    kind = choose("What kind of edit?", kinds, "1")
    if kind == "b":
        return
    if kind == "6":
        say(edit_model.GUIDE)
        line = ask("Edit", show_default=False)
        if line:
            attempt(lambda: project.add_edits([line]))
        return
    say("  (times: 00.10 = 10 seconds, 01.05 = 1 minute 5 seconds)")
    if kind in ("1", "5"):
        start, end = ask("From", show_default=False), ask("To", show_default=False)
        line = "%s %s-%s" % ("cut" if kind == "1" else "mute", start, end)
    elif kind == "2":
        start, end = ask("Voice starts at", show_default=False), ask("Must finish by (Enter = no limit)", show_default=False)
        text = ask("What should the voice say", show_default=False)
        line = "narrate %s %s" % (start + ("-" + end if end else ""), json.dumps(text, ensure_ascii=False))
    elif kind == "3":
        start, end = ask("Replace audio from", show_default=False), ask("to", show_default=False)
        text = ask("What should the voice say instead", show_default=False)
        line = "replace-voice %s-%s %s" % (start, end, json.dumps(text, ensure_ascii=False))
    else:
        start = ask("Time the speech starts (see the audio map)", show_default=False)
        way = choose("Move it", {"e": "earlier", "l": "later"}, "e")
        amount = ask("By how many seconds", "1")
        line = "shift-audio %s %s %ss" % (start, "earlier" if way == "e" else "later", amount.rstrip("s"))
    attempt(lambda: project.add_edits([line]))
    if project.pending:
        say("  added: %s" % edit_model.describe(project.pending[-1]))


def manage_pending(project):
    show_status(project)
    if not project.pending:
        return
    number = ask("Number to remove (Enter = keep all, 'all' = remove all)", show_default=False)
    if number == "all":
        project.clear_pending()
    elif number.isdigit():
        attempt(lambda: project.remove_pending(int(number)))


def menu(project):
    while True:
        version = project.versions[project.cursor]
        say("")
        say("%s - on v%d (%s), %d pending edit(s)" % (project.name, version["id"], seconds_text(version.get("seconds")), len(project.pending)))
        options = {
            "a": "Add an edit (cut, add voice, replace voice, move speech)",
            "m": "Map - show where the speech is",
            "p": "Pending edits - see or remove",
            "c": "Check - see where each pending edit lands and what could go wrong",
            "g": "Go - apply the pending edits",
            "v": "Preview - quick low-quality try, nothing saved",
            "u": "Undo last applied version",
            "r": "Redo",
            "h": "History",
            "e": "Export the final video",
            "q": "Quit (everything is saved, come back any time)",
        }
        key = choose("What next?", options, "g" if project.pending else "a")
        if key == "q":
            return
        if key == "a":
            guided_add(project)
        elif key == "m":
            attempt(lambda: print_map(project, 0.6))
        elif key == "p":
            manage_pending(project)
        elif key == "c":
            attempt(lambda: show_plan(project, make_plan(project)[0]))
        elif key in ("g", "v"):
            attempt(lambda: apply_pending(project, quick=key == "v"))
        elif key == "u":
            attempt(project.undo)
        elif key == "r":
            attempt(project.redo)
        elif key == "h":
            show_status(project)
        elif key == "e":
            attempt(lambda: do_export(project))


def pick_project():
    folders = projects.recent()
    for name in sorted(os.listdir(".")):
        full = os.path.abspath(name)
        if name.endswith(projects.SUFFIX) and os.path.isfile(os.path.join(full, projects.FILE)) and full not in folders:
            folders.append(full)
    options = {str(i): "Continue: %s" % folder for i, folder in enumerate(folders[:6], 1)}
    options["v"] = "Start a new project from a video file"
    options["d"] = "Create the AI voice-over for a raw screen recording first"
    options["q"] = "Quit"
    say("")
    say("Demo video studio")
    key = choose("What do you want to do?", options, "1" if folders else "v")
    if key == "q":
        return None
    if key == "d":
        subprocess.call([sys.executable, os.path.join(HERE, "ai_dub.py")])
        return None
    if key == "v":
        path = ask("Path to the video", show_default=False)
        if not path:
            return None
        return open_project(path)
    return projects.load(folders[int(key) - 1])


def run_command(args):
    command = args.command
    if command == "guide":
        say(edit_model.GUIDE)
        return
    if command == "dub":
        sys.exit(subprocess.call([sys.executable, os.path.join(HERE, "ai_dub.py")] + args.rest))
    require(False)
    if command == "new":
        project = open_project(args.video, args)
        show_status(project)
        return
    project = open_project(args.target, args)
    if command == "add":
        project.add_edits(args.edit)
        say("Added. Pending edits:")
        show_status(project)
    elif command == "status":
        show_status(project)
    elif command == "remove":
        gone = project.remove_pending(args.number)
        say("Removed: %s" % edit_model.describe(gone))
    elif command == "clear":
        project.clear_pending()
        say("Cleared.")
    elif command in ("apply", "preview"):
        apply_pending(project, quick=command == "preview", yes=args.yes, label=getattr(args, "label", None), snap=args.snap)
    elif command == "plan":
        rows, _ = make_plan(project)
        show_plan(project, rows)
    elif command == "where":
        total = media.probe_duration(project.current_file())
        moment = timecode.parse_time(args.time)
        result = plan.final_time(moment, plan.merged_cuts(project.pending, total))
        if result is None:
            say("%s is inside a pending cut, so it will be removed." % timecode.format_time(moment))
        else:
            say("%s will be at %s in the result." % (timecode.format_time(moment), timecode.format_time(result)))
    elif command == "undo":
        project.undo()
        say("Back to v%d." % project.versions[project.cursor]["id"])
    elif command == "redo":
        project.redo()
        say("Forward to v%d." % project.versions[project.cursor]["id"])
    elif command == "map":
        print_map(project, args.gap, args.from_time, args.to_time)
    elif command == "script":
        project.add_edits(read_script(args.file))
        show_status(project)
        if args.apply:
            apply_pending(project, yes=args.yes, snap=False)
    elif command == "export":
        do_export(project, args.out)


def main():
    args = parse_args()
    if args.command:
        run_command(args)
        return
    require(False)
    project = pick_project()
    if project:
        menu(project)


if __name__ == "__main__":
    try:
        main()
    except (Abort, ApiError) as e:
        status.stop()
        logs.current.finish("failed", str(e))
        say("")
        say("✗ %s" % e)
        if logs.current.path:
            say("  Details: %s" % logs.current.path)
        sys.exit(1)
    except KeyboardInterrupt:
        status.stop()
        logs.current.finish("interrupted", "interrupted by user")
        say("")
        say("Interrupted. Your project is saved; run the same command to continue.")
        sys.exit(1)
