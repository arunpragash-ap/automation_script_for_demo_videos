import os

from . import media, openai_api, speech, timecode
from .ui import die, info, ok, warn

MIN_KEEP = 0.04
FADE = 0.02


def keep_segments(ranges, total):
    kept, pos = [], 0.0
    for start, end in ranges:
        if start - pos > MIN_KEEP:
            kept.append((pos, start))
        pos = max(pos, end)
    if total - pos > MIN_KEEP:
        kept.append((pos, total))
    return kept


def encode_args(out, quick=False):
    preset, crf = ("ultrafast", "30") if quick else ("veryfast", "18")
    return ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out]


def cut(video, ranges, out, quick=False):
    total = media.probe_duration(video)
    merged = timecode.merge_ranges(ranges, total)
    kept = keep_segments(merged, total)
    if not kept:
        die("cutting those ranges would remove the whole video")
    audio = media.has_audio(video)
    count = len(kept)
    chains = []
    if count > 1:
        chains.append("[0:v]split=%d%s" % (count, "".join("[vs%d]" % i for i in range(count))))
        if audio:
            chains.append("[0:a]asplit=%d%s" % (count, "".join("[as%d]" % i for i in range(count))))
    labels = ""
    for i, (start, end) in enumerate(kept):
        vin, ain = ("[vs%d]" % i, "[as%d]" % i) if count > 1 else ("[0:v]", "[0:a]")
        chains.append("%strim=start=%.3f:end=%.3f,setpts=PTS-STARTPTS[v%d]" % (vin, start, end, i))
        labels += "[v%d]" % i
        if audio:
            fades = []
            if start > 0.001:
                fades.append("afade=t=in:st=0:d=%.3f" % FADE)
            if end < total - 0.001:
                fades.append("afade=t=out:st=%.3f:d=%.3f" % (max(end - start - FADE, 0.0), FADE))
            chains.append("%satrim=start=%.3f:end=%.3f,asetpts=PTS-STARTPTS%s[a%d]" % (ain, start, end, "".join("," + f for f in fades), i))
            labels += "[a%d]" % i
    graph = ";".join(chains) + ";" + labels + "concat=n=%d:v=1:a=%d%s" % (count, 1 if audio else 0, "[v][a]" if audio else "[v]")
    maps = ["-map", "[v]"] + (["-map", "[a]"] if audio else [])
    media.run("ffmpeg", "-y", "-i", video, "-filter_complex", graph, *maps, *encode_args(out, quick))
    removed = sum(end - start for start, end in merged)
    new_total = media.probe_duration(out)
    ok("cut %d range(s), removed %.2fs, new length %.2fs" % (len(merged), removed, new_total),
       removed=[[timecode.format_time(a), timecode.format_time(b)] for a, b in merged], original_seconds=round(total, 2), new_seconds=round(new_total, 2))
    return {"removed_seconds": round(removed, 2), "new_seconds": round(new_total, 2), "ranges": merged}


def fit_voice(item, opts, work, label, index):
    text = item["text"]
    window = None if item["end"] is None else item["end"] - item["start"]
    path, _ = speech.ensure_audio(text, opts, work, "%s_%02d" % (label, index))
    duration = media.probe_duration(path)
    if window:
        for _ in range(3):
            if duration / opts.max_tempo <= window + 0.05:
                break
            limit = max(int(len(text) * window * opts.max_tempo / duration * 0.93), 6)
            shorter = openai_api.shorten(text, limit, opts.text_model, "")
            if shorter == text:
                break
            info("narration shortened to fit the window", before=text, after=shorter)
            text = shorter
            path, _ = speech.ensure_audio(text, opts, work, "%s_%02d" % (label, index))
            duration = media.probe_duration(path)
    tempo = 1.0
    if window and duration > window:
        tempo = min(duration / window, opts.max_tempo)
    fitted = os.path.join(work, "%s_%02d_fit.wav" % (label, index))
    media.run("ffmpeg", "-y", "-i", path, "-filter:a", "atempo=%.4f" % tempo, fitted)
    length = duration / tempo
    if window and length > window + 0.05:
        warn("narration at %s is %.2fs longer than its window and will run past it" % (timecode.format_time(item["start"]), length - window))
    return {"wav": fitted, "length": length, "text": text, "tempo": tempo}


def narrate(video, items, out, opts, work):
    total = media.probe_duration(video)
    audio = media.has_audio(video)
    voices = []
    for index, item in enumerate(items, 1):
        if item["end"] is not None and item["end"] <= item["start"]:
            die("narration window %s-%s ends before it starts" % (timecode.format_time(item["start"]), timecode.format_time(item["end"])))
        if item["start"] >= total:
            die("narration starts at %s but the video is only %s long" % (timecode.format_time(item["start"]), timecode.format_time(total)))
        if item["end"] is not None and item["end"] > total:
            warn("narration window ends after the video, clipped to %s" % timecode.format_time(total))
            item["end"] = total
        voice = fit_voice(item, opts, work, "narr", index)
        voice.update(start=item["start"], end=item["end"])
        voices.append(voice)
    inputs = ["-i", video]
    base = "[0:a]"
    nxt = 1
    if not audio:
        inputs += ["-f", "lavfi", "-t", "%.3f" % total, "-i", "anullsrc=r=48000:cl=stereo"]
        base, nxt = "[1:a]", 2
    mutes = ""
    if opts.replace:
        for v in voices:
            stop = v["end"] if v["end"] is not None else v["start"] + v["length"]
            mutes += ",volume=0:enable='between(t,%.3f,%.3f)'" % (v["start"], stop)
    chains = ["%saresample=48000,aformat=channel_layouts=stereo%s[base]" % (base, mutes)]
    labels = "[base]"
    for k, v in enumerate(voices):
        ms = int(v["start"] * 1000)
        inputs += ["-i", v["wav"]]
        chains.append("[%d:a]loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,aformat=channel_layouts=stereo,adelay=%d|%d[n%d]" % (nxt + k, ms, ms, k))
        labels += "[n%d]" % k
    chains.append("%samix=inputs=%d:normalize=0:duration=first,alimiter=limit=0.95[out]" % (labels, len(voices) + 1))
    media.run("ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", "0:v", "-map", "[out]",
              "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out)
    ok("added %d narration(s)%s" % (len(voices), " replacing the original audio in those windows" if opts.replace else " mixed over the original audio"),
       narrations=[{"start": timecode.format_time(v["start"]), "text": v["text"], "seconds": round(v["length"], 2), "speed": round(v["tempo"], 2)} for v in voices])
    return voices


def parse_script(path):
    items = []
    with open(path) as f:
        for number, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "|" not in line:
                die("%s line %d: expected 'START-END | text' or 'START | text'" % (os.path.basename(path), number))
            when, text = [part.strip() for part in line.split("|", 1)]
            if not text:
                die("%s line %d: the text is empty" % (os.path.basename(path), number))
            if any(sep in when for sep in ("-", "–", "—")) or " to " in when.lower():
                start, end = timecode.parse_range(when)
            else:
                start, end = timecode.parse_time(when), None
            items.append({"start": start, "end": end, "text": text})
    if not items:
        die("%s has no narration lines" % os.path.basename(path))
    return items
