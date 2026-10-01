import copy
import hashlib
import os

from . import edit_ops, media, speechmap, timecode
from . import edits as edit_model
from .ui import die, info, ok, warn

PAD = 0.05
OVERLAP_LIMIT = 0.9


def check_edits(edits, total):
    for e in edits:
        kind = e["type"]
        first = e["at"] if kind == "shift" else e["start"]
        if first >= total:
            die("%s starts at %s but the video is only %s long" % (kind, timecode.format_time(first), timecode.format_time(total)))
        if kind != "shift" and e.get("end") is not None and e["end"] > total:
            warn("%s window ends after the video, clipped to %s" % (kind, timecode.format_time(total)))
            e["end"] = total
    return edits


def make_voices(edits, opts, cache):
    voices = []
    for e in edits:
        if e["type"] not in ("narrate", "replace"):
            continue
        pieces = edit_model.parts(e)
        group = []
        cursor = e["start"]
        for part in pieces:
            cursor += part["pause"]
            window_end = e.get("end") if len(pieces) == 1 else None
            item = {"start": cursor, "end": window_end, "text": part["text"]}
            key = int(hashlib.sha1(part["text"].encode()).hexdigest()[:6], 16)
            voice = edit_ops.fit_voice(item, opts, cache, "voice", key)
            voice.update(start=cursor, end=window_end, replace=e["type"] == "replace")
            group.append(voice)
            cursor += voice["length"]
        if e.get("end") is not None and cursor > e["end"] + 0.05:
            warn("voice at %s runs %.2fs past its window" % (timecode.format_time(e["start"]), cursor - e["end"]))
        for voice in group:
            voice["mute_from"] = e["start"]
            voice["mute_to"] = max(e["end"], cursor) if e.get("end") is not None else cursor
        voices.extend(group)
    return voices


def resolve_shifts(base, edits, total):
    shifts = [e for e in edits if e["type"] == "shift"]
    if not shifts:
        return []
    silences = media.detect_silences(base)
    spans = speechmap.speech_intervals(silences, total)
    plans = []
    for e in shifts:
        at = timecode.format_time(e["at"])
        found = speechmap.find_block(spans, e["at"], e["gap"])
        if not found:
            inside = speechmap.containing(spans, e["at"])
            if inside:
                die("shift-audio %s: speech is already playing at that time, it started at %s. Use that time instead." % (at, timecode.format_time(inside[0])))
            die("shift-audio %s: no speech starts near that time. Run the audio map to see where speech starts." % at)
        start, end = found
        if e.get("until") is not None:
            if e["until"] <= start:
                die("shift-audio %s: 'until' must be after the speech start %s" % (at, timecode.format_time(start)))
            end = e["until"]
        lo, hi = max(start - PAD, 0.0), min(end + PAD, total)
        dest = lo + e["by"]
        if dest < 0:
            warn("shift-audio %s would start before the video, moved to the very start instead" % at)
            dest = 0.0
        region = (dest, lo) if e["by"] < 0 else (hi, min(hi + e["by"], total))
        if region[1] - region[0] > 0.05 and media.silent_fraction(region[0], region[1], silences) < OVERLAP_LIMIT:
            warn("shift-audio %s: speech plays in the place it moves to (%s-%s), so the two may overlap" % (at, timecode.format_time(region[0]), timecode.format_time(region[1])))
        info("speech block found", asked=at, start=round(start, 2), end=round(end, 2), moved_to=round(dest, 2))
        plans.append({"lo": lo, "hi": hi, "dest": dest, "start": start, "end": end})
    return plans


def audio_pass(base, total, has_audio, voices, shifts, mutes, out, lossless):
    inputs = ["-i", base]
    source, nxt = "[0:a]", 1
    if not has_audio:
        inputs += ["-f", "lavfi", "-t", "%.3f" % total, "-i", "anullsrc=r=48000:cl=stereo"]
        source, nxt = "[1:a]", 2
    pre = "%saresample=48000,aformat=channel_layouts=stereo" % source
    names = ["[p%d]" % i for i in range(len(shifts))]
    chains = ["%s,asplit=%d[bm]%s" % (pre, len(shifts) + 1, "".join(names)) if shifts else pre + "[bm]"]
    if mutes:
        chains.append("[bm]volume=0:enable='%s'[base]" % "+".join("between(t,%.3f,%.3f)" % m for m in mutes))
    else:
        chains.append("[bm]anull[base]")
    labels = "[base]"
    for i, plan in enumerate(shifts):
        ms = int(round(plan["dest"] * 1000))
        chains.append("%satrim=%.3f:%.3f,asetpts=PTS-STARTPTS,adelay=%d|%d[x%d]" % (names[i], plan["lo"], plan["hi"], ms, ms, i))
        labels += "[x%d]" % i
    for k, voice in enumerate(voices):
        ms = int(round(voice["start"] * 1000))
        inputs += ["-i", voice["wav"]]
        chains.append("[%d:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo,adelay=%d|%d[n%d]" % (nxt + k, ms, ms, k))
        labels += "[n%d]" % k
    count = 1 + len(shifts) + len(voices)
    if count > 1:
        tail = "amix=inputs=%d:normalize=0:duration=first" % count + (",alimiter=limit=0.95" if voices else "")
        chains.append("%s%s[out]" % (labels, tail))
    else:
        chains.append("[base]anull[out]")
    codec = ["-c:a", "pcm_s16le"] if lossless else ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"]
    media.run("ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[out]", "-c:v", "copy", *codec, out)


def render(base, edits, out, opts, work, cache, quick=False):
    total = media.probe_duration(base)
    edits = check_edits(copy.deepcopy(edits), total)
    cuts = [(e["start"], e["end"]) for e in edits if e["type"] == "cut"]
    audio_edits = [e for e in edits if e["type"] != "cut"]
    has_audio = media.has_audio(base)
    if not has_audio and any(e["type"] in ("mute", "shift") for e in edits):
        die("this video has no audio, so mute and shift-audio cannot be applied")
    current = base
    if audio_edits:
        voices = make_voices(edits, opts, cache)
        shifts = resolve_shifts(base, edits, total)
        mutes = [(e["start"], e["end"]) for e in edits if e["type"] == "mute"]
        mutes += [(p["lo"], p["hi"]) for p in shifts]
        mutes += [(v["mute_from"], v["mute_to"]) for v in voices if v["replace"]]
        current = os.path.join(work, "audio_pass.mkv") if cuts else out
        audio_pass(base, total, has_audio, voices, shifts, mutes, current, lossless=bool(cuts))
        ok("audio edits applied", voices=len(voices), shifts=len(shifts), mutes=len(mutes))
    if cuts:
        edit_ops.cut(current, cuts, out, quick)
    return {"seconds": media.probe_duration(out)}
