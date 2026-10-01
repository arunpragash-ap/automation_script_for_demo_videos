import hashlib
import json
import os

from . import config, media, openai_api, pauses
from .ui import info, ok, progress, warn


def wav_path(text, opts, work, label):
    key = json.dumps([opts.tts_model, opts.voice, opts.style, text])
    return "%s/%s_%s.wav" % (work, label, hashlib.sha1(key.encode()).hexdigest()[:8])


def join_pieces(text, opts, work, label, trimmed):
    paths, created = [], False
    for n, piece in enumerate(pauses.split(text)):
        path, made = ensure_audio(piece["text"], opts, work, "%s_p%d" % (label, n))
        paths.append((path, piece["pause"]))
        created = created or made
    inputs, chains = [], []
    for n, (path, pause) in enumerate(paths):
        inputs += ["-i", path]
        delay = int(round(pause * 1000))
        chains.append("[%d:a]aresample=24000,aformat=channel_layouts=mono%s[a%d]" % (n, ",adelay=%d" % delay if delay else "", n))
    chains.append("%sconcat=n=%d:v=0:a=1[o]" % ("".join("[a%d]" % n for n in range(len(paths))), len(paths)))
    media.run("ffmpeg", "-y", *inputs, "-filter_complex", ";".join(chains), "-map", "[o]", trimmed)
    return trimmed, created


def ensure_audio(text, opts, work, label):
    raw = wav_path(text, opts, work, label)
    trimmed = raw[:-4] + "_t.wav"
    if pauses.has(text):
        if os.path.exists(trimmed):
            return trimmed, False
        return join_pieces(text, opts, work, label, trimmed)
    created = not os.path.exists(raw)
    if created:
        openai_api.synthesize(text, raw, opts.tts_model, opts.voice, opts.style)
    if not os.path.exists(trimmed):
        media.trim_silence(raw, trimmed)
    return trimmed, created


def synth_and_fit(segs, opts, work, context):
    live = [s for s in segs if s["text"].strip()]
    for s in segs:
        s.setdefault("dur", 0.0)
        s.setdefault("over", False)
    for rnd in range(4):
        over = []
        for n, s in enumerate(live, 1):
            progress("%d/%d" % (n, len(live)))
            path, created = ensure_audio(s["text"], opts, work, "seg_%03d" % s["id"])
            if created:
                info("speech generated", segment=s["id"], slot_seconds=round(s["slot"], 2), text=s["text"])
            s["wav"] = path
            s["dur"] = media.probe_duration(path)
            s["over"] = s["dur"] / opts.max_tempo > s["slot"] + 0.05
            if s["over"]:
                over.append(s)
        if not over:
            ok("all %d lines fit their time slots" % len(live))
            return
        if rnd == 3:
            break
        info("%d line(s) too long for their slot, asking %s to shorten (round %d/3)" % (len(over), opts.text_model, rnd + 1))
        for s in over:
            ratio = s["slot"] * opts.max_tempo / s["dur"]
            limit = max(int(len(s["text"]) * ratio * 0.93), 6)
            old = s["text"]
            s["text"] = openai_api.shorten(old, limit, opts.text_model, context)
            info("line shortened to fit", segment=s["id"], before=old, after=s["text"])
    for s in over:
        warn("seg#%d still overflows its slot by %.2fs and may overlap the next line" % (s["id"], s["dur"] / opts.max_tempo - s["slot"]))


def make_voice(text, opts, work, label):
    path, created = ensure_audio(text, opts, work, label)
    if created:
        info("%s voice generated" % label, text=text)
    return path, media.probe_duration(path)


def make_thanks(opts, work):
    return make_voice(config.THANKS_TEXT, opts, work, "thanks")
