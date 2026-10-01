import json
import re
import shlex

from . import pauses, timecode
from .ui import die

DEFAULT_GAP = 2.0

KINDS = {
    "cut": "cut", "remove": "cut",
    "mute": "mute", "silence": "mute",
    "shift": "shift", "shift-audio": "shift", "move-audio": "shift",
    "narrate": "narrate", "say": "narrate", "add-voice": "narrate",
    "replace-voice": "replace", "replace": "replace",
}

GUIDE = """edit words:
  cut 02.58-03.06                          remove that part of the video
  mute 02.05-02.08                         silence the audio in that part
  shift-audio 00.58 earlier 1s             move the speech that starts at 00.58 one second earlier
  shift-audio 01.17 by -1s gap 2s          same, 'gap' = silence that ends the speech block
  narrate 00.48-00.52 "Click Next."        add AI voice at a time (window end is optional)
  replace-voice 02.05-02.08 "New text."    mute that part and speak new text there
  narrate 04.00 "Line one." pause 2s "Line two."   several lines with a pause between them

times: 00.10 = 10 seconds, 01.05 = 1 min 5 s, also 1:05, 65s. All times refer to the video as it is now."""


def parse_offset(text):
    raw = str(text).strip().lower()
    if raw.endswith("s"):
        raw = raw[:-1]
    try:
        return float(raw)
    except ValueError:
        die("invalid time shift '%s'. Use for example 1s, -1s or 0.5s" % text)


def split_when(tokens, label):
    if not tokens:
        die("'%s' needs a time. Example: %s 00.10-00.17" % (label, label))
    first = tokens[0]
    if any(sep in first for sep in "-–—"):
        start, end = timecode.parse_range(first)
        return start, end, tokens[1:]
    if len(tokens) >= 3 and tokens[1].lower() == "to":
        start, end = timecode.parse_range("%s to %s" % (tokens[0], tokens[2]))
        return start, end, tokens[3:]
    return timecode.parse_time(first), None, tokens[1:]


def parse_shift(tokens):
    if not tokens:
        die("'shift-audio' needs a time. Example: shift-audio 00.58 earlier 1s")
    edit = {"type": "shift", "at": timecode.parse_time(tokens[0]), "by": None, "gap": DEFAULT_GAP, "until": None}
    rest, i = tokens[1:], 0
    while i < len(rest):
        word = rest[i].lower()
        if word not in ("by", "earlier", "later", "gap", "until") or i + 1 >= len(rest):
            die("don't understand '%s' in shift-audio. Example: shift-audio 00.58 earlier 1s" % rest[i])
        value = rest[i + 1]
        if word == "by":
            edit["by"] = parse_offset(value)
        elif word == "earlier":
            edit["by"] = -abs(parse_offset(value))
        elif word == "later":
            edit["by"] = abs(parse_offset(value))
        elif word == "gap":
            edit["gap"] = abs(parse_offset(value))
        else:
            edit["until"] = timecode.parse_time(value)
        i += 2
    if not edit["by"]:
        die("shift-audio needs 'earlier 1s', 'later 1s' or 'by -1s'")
    return edit


def try_offset(text):
    raw = str(text).strip().lower()
    if raw.endswith("s"):
        raw = raw[:-1]
    try:
        return float(raw)
    except ValueError:
        return None


def add_pieces(parts, text, pause):
    pieces = pauses.split(text)
    if pieces:
        pieces[0]["pause"] += pause
        parts.extend(pieces)


def parse_voice(rest, label):
    parts, current, pause = [], [], 0.0
    i = 0
    while i < len(rest):
        if rest[i].lower() == "pause":
            value = try_offset(rest[i + 1]) if i + 1 < len(rest) else None
            if value is None:
                die("'pause' needs a length, for example: %s 00.48 \"First line.\" pause 2s \"Second line.\"" % label)
            if current:
                add_pieces(parts, " ".join(current), pause)
                current, pause = [], 0.0
            pause += abs(value)
            i += 2
            continue
        current.append(rest[i])
        i += 1
    if current:
        add_pieces(parts, " ".join(current), pause)
    return [p for p in parts if p["text"].strip()]


def parts(edit):
    return edit.get("parts") or [{"text": edit["text"], "pause": 0.0}]


def parse(line):
    try:
        tokens = shlex.split(line)
    except ValueError as e:
        die("cannot read '%s': %s" % (line, e))
    if not tokens:
        die("empty edit")
    kind = KINDS.get(tokens[0].lower())
    if not kind:
        die("unknown edit '%s'.\n%s" % (tokens[0], GUIDE))
    rest = tokens[1:]
    if kind == "shift":
        return parse_shift(rest)
    start, end, rest = split_when(rest, tokens[0].lower())
    if kind in ("cut", "mute"):
        if end is None or rest:
            die("'%s' needs a start and an end. Example: %s 00.10-00.17" % (tokens[0], tokens[0]))
        return {"type": kind, "start": start, "end": end}
    voice = parse_voice(rest, tokens[0].lower())
    if not voice:
        die("'%s' needs the words to say, in quotes. Example: %s 00.48 \"Click Next.\"" % (tokens[0], tokens[0]))
    if kind == "replace" and end is None:
        die("replace-voice needs a start and an end, because that part is muted. Example: replace-voice 02.05-02.08 \"New text.\"")
    edit = {"type": kind, "start": start, "end": end, "text": " ".join(p["text"] for p in voice)}
    if len(voice) > 1 or voice[0]["pause"]:
        edit["parts"] = voice
    return edit


def span(edit):
    t = timecode.format_time
    return "%s-%s" % (t(edit["start"]), t(edit["end"])) if edit.get("end") is not None else t(edit["start"])


def to_text(edit):
    kind = edit["type"]
    if kind in ("cut", "mute"):
        return "%s %s" % (kind, span(edit))
    if kind == "shift":
        text = "shift-audio %s by %+gs" % (timecode.format_time(edit["at"]), edit["by"])
        if edit["gap"] != DEFAULT_GAP:
            text += " gap %gs" % edit["gap"]
        if edit.get("until") is not None:
            text += " until %s" % timecode.format_time(edit["until"])
        return text
    name = "narrate" if kind == "narrate" else "replace-voice"
    words = []
    for part in parts(edit):
        if part["pause"]:
            words.append("pause %gs" % part["pause"])
        words.append(json.dumps(part["text"], ensure_ascii=False))
    return "%s %s %s" % (name, span(edit), " ".join(words))


def describe(edit):
    kind = edit["type"]
    if kind == "cut":
        return "Remove %s from the video (%.1fs)" % (span(edit), edit["end"] - edit["start"])
    if kind == "mute":
        return "Silence the audio from %s" % span(edit)
    if kind == "shift":
        way = "earlier" if edit["by"] < 0 else "later"
        return "Move the speech starting at %s %gs %s" % (timecode.format_time(edit["at"]), abs(edit["by"]), way)
    spoken = " ... ".join(p["text"] for p in parts(edit))
    if kind == "narrate":
        return "Add voice at %s: \"%s\"" % (span(edit), spoken)
    return "Replace audio at %s with voice: \"%s\"" % (span(edit), spoken)


def uses_api(edit):
    return edit["type"] in ("narrate", "replace")
