import copy
import hashlib
import os

from . import edits as edit_model
from . import media, speech, speechmap, timecode

EDGE = 0.05
SNAP_REACH = 1.5
OVERLAP_LIMIT = 0.15
CHARS_PER_SECOND = 14.0


def merged_cuts(edits, total):
    ranges = [(e["start"], min(e["end"], total)) for e in edits if e["type"] == "cut" and e["start"] < total]
    merged = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def final_time(t, cuts):
    removed = 0.0
    for start, end in cuts:
        if t >= end:
            removed += end - start
        elif t > start:
            return None
    return t - removed


def inside(spans, t):
    return next(((s, e) for s, e in spans if s + EDGE < t < e - EDGE), None)


def subtract(spans, holes):
    out = []
    for start, end in spans:
        pieces = [(start, end)]
        for hs, he in holes:
            nxt = []
            for ps, pe in pieces:
                if he <= ps or hs >= pe:
                    nxt.append((ps, pe))
                    continue
                if hs > ps:
                    nxt.append((ps, hs))
                if he < pe:
                    nxt.append((he, pe))
            pieces = nxt
        out.extend(pieces)
    return out


def voice_length(text, opts, cache):
    key = int(hashlib.sha1(text.encode()).hexdigest()[:6], 16)
    raw = speech.wav_path(text, opts, cache, "voice_%02d" % key)
    cached = raw[:-4] + "_t.wav"
    if os.path.exists(cached):
        return media.probe_duration(cached), False
    return len(text) / CHARS_PER_SECOND + 0.3, True


def voice_layout(edit, opts, cache):
    cursor = edit["start"]
    layout = []
    for part in edit_model.parts(edit):
        cursor += part["pause"]
        length, estimated = voice_length(part["text"], opts, cache)
        layout.append((cursor, length, estimated))
        cursor += length
    return layout


def analyse(base, edits, opts, cache):
    spans, total = speechmap.analyse(base)
    cuts = merged_cuts(edits, total)
    layouts = {i: voice_layout(e, opts, cache) for i, e in enumerate(edits) if edit_model.uses_api(e)}
    voice_starts = [start for lay in layouts.values() for start, _, _ in lay]
    holes = [(e["start"], e["end"]) for e in edits if e["type"] == "mute"]
    for i, e in enumerate(edits):
        if e["type"] == "replace":
            holes.append((e["start"], max(e["end"], layouts[i][-1][0] + layouts[i][-1][1])))
    live = subtract(subtract(spans, holes), cuts)
    t = timecode.format_time
    rows = []
    for i, e in enumerate(edits):
        notes, snap = [], {}
        kind = e["type"]
        if kind in ("cut", "mute", "replace"):
            for side, label, outward in (("start", "starts", -1), ("end", "ends", 1)):
                hit = inside(spans, e[side])
                if not hit:
                    continue
                edge = hit[0] if outward < 0 else hit[1]
                swallowed = any(min(e[side], edge) <= v < max(e[side], edge) for v in voice_starts)
                if abs(edge - e[side]) <= SNAP_REACH and not swallowed:
                    snap[side] = edge
                    notes.append(("warn", "%s in the middle of speech (%s-%s). Extending it to %s leaves no clipped word." % (label, t(hit[0]), t(hit[1]), t(edge))))
                else:
                    notes.append(("warn", "%s in the middle of speech (%s-%s)." % (label, t(hit[0]), t(hit[1]))))
        if kind in ("narrate", "replace"):
            for start, length, estimated in layouts[i]:
                end = start + length
                where = final_time(start, cuts)
                if where is None:
                    notes.append(("error", "the voice at %s is inside a cut and would be removed" % t(start)))
                    continue
                clash = sum(max(0.0, min(end, se) - max(start, ss)) for ss, se in live)
                first = next(((ss, se) for ss, se in live if min(end, se) - max(start, ss) > 0), None)
                if clash > OVERLAP_LIMIT:
                    notes.append(("warn", "the voice (%s-%s%s) plays over existing speech at %s-%s. Mute or replace that part first." % (t(start), t(end), ", estimated length" if estimated else "", t(first[0]), t(first[1]))))
        rows.append({"index": i, "edit": e, "notes": notes, "snap": snap, "final": final_time(e.get("start", e.get("at", 0)), cuts)})
    return rows, total


def snap_edits(edits, rows):
    out = copy.deepcopy(edits)
    for row in rows:
        for side, value in row["snap"].items():
            out[row["index"]][side] = value
    return out


def has_errors(rows):
    return any(level == "error" for row in rows for level, _ in row["notes"])


def has_snaps(rows):
    return any(row["snap"] for row in rows)
