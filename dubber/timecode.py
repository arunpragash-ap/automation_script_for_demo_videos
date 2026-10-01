import re

from .ui import die

DOT = re.compile(r"^(\d+)\.(\d{2})$")
COLON = re.compile(r"^(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)$")
SECONDS = re.compile(r"^(\d+(?:\.\d+)?)s$")
SPLIT = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)


def parse_time(text):
    raw = str(text).strip()
    m = DOT.match(raw)
    if m:
        minutes, seconds = int(m.group(1)), int(m.group(2))
        if seconds > 59:
            die("invalid time '%s': in mm.ss the seconds must be 00 to 59" % raw)
        return minutes * 60.0 + seconds
    m = COLON.match(raw)
    if m:
        hours, minutes, seconds = int(m.group(1) or 0), int(m.group(2)), float(m.group(3))
        if seconds >= 60:
            die("invalid time '%s': seconds must be below 60" % raw)
        return hours * 3600.0 + minutes * 60.0 + seconds
    m = SECONDS.match(raw)
    if m:
        return float(m.group(1))
    die("invalid time '%s'. Use mm.ss (00.10 = 10 seconds, 01.05 = 1 minute 5 seconds), mm:ss, h:mm:ss or 75s" % raw)


def format_time(t):
    t = round(t, 2)
    minutes, seconds = divmod(t, 60)
    if abs(seconds - round(seconds)) < 0.005:
        return "%02d.%02d" % (minutes, round(seconds))
    return "%02d:%05.2f" % (minutes, seconds)


def parse_range(text):
    parts = SPLIT.split(str(text).strip(), maxsplit=1)
    if len(parts) != 2:
        die("invalid range '%s'. Use START-END, for example 00.10-00.17" % text)
    start, end = parse_time(parts[0]), parse_time(parts[1])
    if end <= start:
        die("range '%s' ends before it starts" % text)
    return start, end


def merge_ranges(ranges, total):
    clipped = []
    for start, end in sorted(ranges):
        if start >= total:
            die("range starts at %s but the video is only %s long" % (format_time(start), format_time(total)))
        clipped.append((start, min(end, total)))
    merged = []
    for start, end in clipped:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
