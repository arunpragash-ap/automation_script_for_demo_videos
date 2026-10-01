import difflib
import re
import shlex
import subprocess
import time

from . import config, logs
from .ui import die

VIDEO_NORM = "setsar=1,fps=%s,format=yuv420p"
AUDIO_NORM = "aresample=48000,aformat=channel_layouts=stereo"


def run(*args):
    started = time.time()
    r = subprocess.run(args, capture_output=True, text=True)
    logs.current.event("debug", "command", tool=args[0], returncode=r.returncode, seconds=round(time.time() - started, 2),
                       command=" ".join(shlex.quote(str(a)) for a in args)[:400])
    if r.returncode != 0:
        die("%s failed:\n%s" % (args[0], r.stderr[-800:]))
    return r


def probe_duration(path):
    r = run("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path)
    return float(r.stdout.strip())


def probe_video(path):
    r = run("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate", "-of", "csv=p=0", path)
    width, height, fps = r.stdout.strip().split(",")
    return int(width), int(height), fps


def has_audio(path):
    r = run("ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path)
    return bool(r.stdout.strip())


def extract_audio(video, wav):
    run("ffmpeg", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", wav)


def detect_silences(wav):
    r = subprocess.run(
        ["ffmpeg", "-i", wav, "-af", "silencedetect=noise=-38dB:d=0.25", "-f", "null", "-"],
        capture_output=True, text=True,
    )
    out, cur = [], None
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: (-?[\d.]+)", line)
        if m:
            cur = max(float(m.group(1)), 0.0)
            continue
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and cur is not None:
            out.append((cur, float(m.group(1))))
            cur = None
    return out


def silent_fraction(start, end, silences):
    if end <= start:
        return 1.0
    covered = sum(max(0.0, min(end, b) - max(start, a)) for a, b in silences)
    return covered / (end - start)


def normalize_text(text):
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def similarity(a, b):
    return difflib.SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def drop_hallucinations(segs, silences):
    kept, dropped = [], []
    for s in segs:
        quiet = silent_fraction(s["whisper_start"], s["whisper_end"], silences)
        around = silent_fraction(s["whisper_start"] - 1.0, s["whisper_end"] + 1.0, silences)
        reason = None
        if kept and similarity(s["text"], kept[-1]["text"]) >= 0.9 and (len(s["text"]) >= 20 or quiet >= 0.3):
            reason = "same sentence repeated right after itself"
        elif normalize_text(s["text"]) in [normalize_text(k["text"]) for k in kept[-3:]] and quiet >= 0.4:
            reason = "repeat of a recent line in a quiet stretch"
        elif around >= 0.9:
            reason = "no speech in the audio around it"
        elif s.get("no_speech_prob", 0.0) >= 0.8 and s.get("avg_logprob", 0.0) < -0.8:
            reason = "Whisper itself marked it as probably not speech"
        if reason:
            dropped.append({"id": s["id"], "start": s["whisper_start"], "end": s["whisper_end"], "text": s["text"],
                            "reason": reason, "silent_fraction": round(quiet, 2)})
        else:
            kept.append(s)
    for i, s in enumerate(kept):
        s["id"] = i
    return kept, dropped


def refine_edges(segs, silences):
    onsets = [b for _, b in silences]
    if not silences or silences[0][0] > 0.05:
        onsets.insert(0, 0.0)
    offsets = [a for a, _ in silences if a > 0.05]
    moved = 0
    prev_start = -1.0
    for s in segs:
        inside = [b for a, b in silences if a <= s["start"] < b and b < s["end"] - 0.3]
        starts = [o for o in onsets if s["start"] - 1.0 <= o <= s["start"] + 0.2 and o > prev_start + 0.5]
        if starts:
            s["start"] = round(min(starts), 2)
            moved += 1
        elif inside:
            s["start"] = round(inside[0], 2)
            moved += 1
        ends = [f for f in offsets if s["end"] - 0.8 <= f <= s["end"] + 0.3 and f > s["start"] + 0.3]
        if ends:
            s["end"] = round(min(ends, key=lambda f: abs(f - s["end"])), 2)
        prev_start = s["start"]
    for s, nxt in zip(segs, segs[1:]):
        s["end"] = round(max(min(s["end"], nxt["start"] - 0.05), s["start"] + 0.3), 2)
    return moved


def assign_slots(segs, total, lead, grace):
    for i, s in enumerate(segs):
        s["place"] = round(max(s["start"] - lead, 0.0), 2)
        nxt = max(segs[i + 1]["start"] - lead, 0.0) if i + 1 < len(segs) else total
        slot_end = min(nxt - 0.05, s["end"] + grace)
        s["slot"] = max(slot_end - s["place"], 0.8)
        s["max_chars"] = max(int(s["slot"] * config.CHARS_PER_SECOND), 8)


def trim_silence(src, dst):
    edge = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.03"
    run("ffmpeg", "-y", "-i", src, "-af", "%s,areverse,%s,areverse" % (edge, edge), dst)


def tempo_for(seg, max_tempo):
    if seg["dur"] <= seg["slot"]:
        return 1.0
    return min(seg["dur"] / seg["slot"], max_tempo)


def build_track(segs, total, work, max_tempo):
    live = [s for s in segs if s["text"].strip()]
    inputs, filters, labels = [], [], []
    for k, s in enumerate(live):
        fit = "%s/fit_%03d.wav" % (work, s["id"])
        run("ffmpeg", "-y", "-i", s["wav"], "-filter:a", "atempo=%.4f" % tempo_for(s, max_tempo), fit)
        inputs += ["-i", fit]
        ms = int(s["place"] * 1000)
        filters.append("[%d:a]adelay=%d|%d[a%d]" % (k, ms, ms, k))
        labels.append("[a%d]" % k)
    graph = ";".join(filters) + ";" + "".join(labels) + (
        "amix=inputs=%d:normalize=0:duration=longest,afftdn=nf=-30,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,apad,atrim=0:%.3f[out]" % (len(labels), total)
    )
    graph_path = work + "/graph.txt"
    with open(graph_path, "w") as f:
        f.write(graph)
    track = work + "/ai_track.m4a"
    run("ffmpeg", "-y", *inputs, "-filter_complex_script", graph_path, "-map", "[out]", "-c:a", "aac", "-b:a", "192k", track)
    return track


def voiced_card(index, seconds, delay, vnorm, tag):
    ms = int(delay * 1000)
    secs = "%g" % seconds
    return [
        "[%d:v]%s[%sv]" % (index, vnorm, tag),
        "[%d:a]%s,adelay=%d|%d,apad=whole_dur=%s,atrim=0:%s[%sa]" % (index + 1, AUDIO_NORM, ms, ms, secs, secs, tag),
    ]


def assemble(video, track, intro, end_png, thanks_wav, out, fps):
    vnorm = VIDEO_NORM % fps
    inputs = ["-i", video, "-i", track]
    chains, order, nxt = [], [], 2
    if intro:
        inputs += ["-framerate", fps, "-loop", "1", "-t", "%g" % intro["seconds"], "-i", intro["png"], "-i", intro["wav"]]
        chains += voiced_card(nxt, intro["seconds"], config.TITLE_VOICE_DELAY, vnorm, "i")
        order.append("[iv][ia]")
        nxt += 2
    chains += ["[0:v]%s[mv]" % vnorm, "[1:a]%s[ma]" % AUDIO_NORM]
    order.append("[mv][ma]")
    inputs += ["-framerate", fps, "-loop", "1", "-t", str(config.END_SECONDS), "-i", end_png, "-i", thanks_wav]
    chains += voiced_card(nxt, config.END_SECONDS, config.THANKS_DELAY, vnorm, "e")
    order.append("[ev][ea]")
    graph = ";".join(chains) + ";" + "".join(order) + "concat=n=%d:v=1:a=1[v][a]" % len(order)
    run("ffmpeg", "-y", *inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", out)
