import json
import os

from . import media, transcript
from .ui import info


def timeline(job):
    with open(job.raw_json) as f:
        segs = json.load(f)["segments"]
    if os.path.exists(job.md_original):
        texts = transcript.read_md_texts(job.md_original)
        edited = sum(1 for s in segs if texts.get(s["id"], s["text"]) != s["text"])
        for s in segs:
            s["text"] = texts.get(s["id"], s["text"])
        if edited:
            info("using %d line(s) you edited in the raw transcript" % edited)
    if not job.opts.no_snap:
        moved = media.refine_edges(segs, media.detect_silences(job.wav))
        info("aligned %d line start(s) to the measured speech onset" % moved)
    media.assign_slots(segs, media.probe_duration(job.video), job.opts.lead, job.opts.end_grace)
    return segs


def write_improved(job, segs):
    transcript.write_md(
        job.md_improved, "Improved Transcript: " + os.path.basename(job.video),
        "Narration script the AI voice reads. Edit the Text column, then re-run the audio step. Write [pause 2s] (or [pause] for 1s) inside a text to make the voice stop for that long. Original: `transcript.md`.", segs)


def apply_texts(job, segs):
    texts = transcript.read_md_texts(job.md_improved)
    for s in segs:
        if s["id"] in texts:
            s["text"] = texts[s["id"]]
