#!/usr/bin/env python3
import argparse
import hashlib
import http.client
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

API = "https://api.openai.com/v1"
CPS = 14.0
STEPS = 7

DEFAULT_TRANSCRIBE_PROMPT = (
    "Verbatim transcript of a narrated screen-recording walkthrough of a software or mobile app. "
    "Transcribe exactly what is spoken with correct punctuation. Capitalise button and screen names."
)

DEFAULT_STYLE = (
    "Speak in a natural, warm, conversational tone with human pacing and gentle expression, "
    "like a friendly professional explaining a product walkthrough. "
    "Always finish each sentence completely and clearly."
)

IMPROVE_SYSTEM = """You are a strict script editor for narrated software demo videos.
You receive a machine transcript of a spoken screen-recording walkthrough as JSON segments, each with: id, start, end, slot_seconds, max_chars, text. You also receive an optional "context" string with glossary and terminology rules that you MUST follow exactly.
Rewrite every segment into clear, polished, natural voice-over narration for a professional demo video.

RULES (all mandatory):
1. Output ONLY valid JSON of the form {"segments":[{"id":<int>,"text":"<string>"}]}. Same ids, same order, same count as the input. No other keys, no commentary.
2. HARD LIMIT: the character length of each text must be <= that segment's max_chars. Count characters. Prefer shorter.
3. Every text must be a complete, grammatical sentence or clause that ends cleanly and can be spoken fully within slot_seconds. Never leave a sentence unfinished or cut off. A clause that deliberately continues into the next segment may end with a comma.
4. Preserve the meaning and the order of the steps. Do NOT invent features, buttons, screens, numbers or steps that are not supported by the transcript. No marketing claims.
5. Fix obvious speech-to-text errors only when the surrounding steps make the correct wording clear. Otherwise keep the original wording.
6. Use second-person, present tense, instructional voice (for example "Tap Continue."). Capitalise UI element names.
7. Only greet the viewer in the first segment and thank them in the last segment if the original transcript does so. Do not add a greeting or closing otherwise.
8. If slot_seconds is below 1.5 and the meaning cannot fit, you may return "" for that segment, but only after moving its meaning into the previous segment when that segment has room.
9. Plain spoken text only: no markdown, no emojis, no quotation marks, no stage directions, no timestamps.
10. Apply the context glossary and terminology exactly as given."""

SHORTEN_SYSTEM = """You shorten one line of narration for a software demo voice-over.
Return ONLY valid JSON {"text":"<string>"}.
RULES: the result must be at most max_chars characters; it must be a complete, grammatical sentence or clause that ends cleanly; keep the original meaning and all key UI names; do not add new information; plain spoken text only; follow the context glossary exactly."""


class ApiError(Exception):
    def __init__(self, code, detail):
        super().__init__("OpenAI HTTP %s: %s" % (code, detail))
        self.code = code
        self.detail = detail


def redact(text):
    text = re.sub(r"sk-[A-Za-z0-9_\-\*\.]{6,}", "sk-***", str(text))
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        text = text.replace(key, "***")
    return text


def log(msg=""):
    print(redact(msg), flush=True)


def step(n, title):
    log("")
    log("[%d/%d] %s" % (n, STEPS, title))


def ok(msg):
    log("  ✓ " + msg)


def info(msg):
    log("  · " + msg)


def warn(msg):
    log("  ! " + msg)


def die(msg):
    log("")
    log("ERROR: " + str(msg))
    sys.exit(1)


def api_key():
    k = os.environ.get("OPENAI_API_KEY", "").strip()
    if not k:
        die("OPENAI_API_KEY is not set. Add `export OPENAI_API_KEY=...` to ~/.zshrc, then run: source ~/.zshrc")
    return k


def call(path, body, content_type, timeout=90):
    for attempt in range(5):
        req = urllib.request.Request(
            API + path, data=body, method="POST",
            headers={"Authorization": "Bearer " + api_key(), "Content-Type": content_type},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            if e.code in (429, 500, 502, 503, 504) and attempt < 4:
                wait = 2 ** (attempt + 1)
                warn("HTTP %d from OpenAI, retrying in %ds" % (e.code, wait))
                time.sleep(wait)
                continue
            raise ApiError(e.code, redact(detail))
        except (OSError, http.client.HTTPException) as e:
            if attempt < 4:
                wait = 2 ** (attempt + 1)
                warn("network error, retrying in %ds" % wait)
                time.sleep(wait)
                continue
            raise ApiError("network", redact(e))


def sh(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        die("%s failed:\n%s" % (args[0], r.stderr[-800:]))
    return r


def duration(path):
    r = sh("ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path)
    return float(r.stdout.strip())


def has_audio(path):
    r = sh("ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", path)
    return bool(r.stdout.strip())


def ts(t):
    m, s = divmod(t, 60)
    return "%02d:%05.2f" % (m, s)


def extract_audio(video, wav):
    sh("ffmpeg", "-y", "-i", video, "-vn", "-ac", "1", "-ar", "16000", wav)


def detect_silences(wav):
    r = subprocess.run(
        ["ffmpeg", "-i", wav, "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-"],
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


def snap_starts(segs, silences):
    moved = 0
    for s in segs:
        for a, b in silences:
            if a - 0.05 <= s["start"] < b and b < s["end"] - 0.4:
                s["start"] = round(b, 2)
                moved += 1
                break
    return moved


def transcribe(wav, model, language, prompt):
    boundary = uuid.uuid4().hex
    fields = [("model", model), ("response_format", "verbose_json"), ("timestamp_granularities[]", "segment"), ("temperature", "0")]
    if language:
        fields.append(("language", language))
    if prompt:
        fields.append(("prompt", prompt[:800]))
    parts = []
    for name, val in fields:
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, name, val)).encode())
    with open(wav, "rb") as f:
        data = f.read()
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n' % boundary).encode() + data + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    try:
        res = json.loads(call("/audio/transcriptions", b"".join(parts), "multipart/form-data; boundary=" + boundary, 300))
    except ApiError as e:
        die(e)
    raw = res.get("segments")
    if not raw:
        die("Transcription model '%s' returned no timestamped segments. Use whisper-1." % model)
    segs = []
    for s in raw:
        text = s["text"].strip()
        if text:
            segs.append({"id": len(segs), "start": round(s["start"], 2), "end": round(s["end"], 2), "text": text})
    return segs


def write_md(path, title, note, segs):
    lines = ["# " + title, "", note, "", "| # | Start | End | Text |", "|---|-------|-----|------|"]
    for s in segs:
        lines.append("| %d | %s | %s | %s |" % (s["id"], ts(s["start"]), ts(s["end"]), s["text"].replace("|", "\\|")))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def read_md_texts(path):
    rows = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line[1:-1] if line.endswith("|") else line[1:])]
            if len(cells) >= 4 and cells[0].isdigit():
                rows[int(cells[0])] = cells[3].replace("\\|", "|")
    return rows


def assign_slots(segs, total):
    for i, s in enumerate(segs):
        nxt = segs[i + 1]["start"] if i + 1 < len(segs) else total
        s["slot"] = max(nxt - s["start"] - 0.05, 0.3)
        s["max_chars"] = max(int(s["slot"] * CPS), 8)


def chat_json(model, system, user):
    base = {"model": model, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    try:
        res = json.loads(call("/chat/completions", json.dumps(dict(base, temperature=0.2)).encode(), "application/json"))
    except ApiError as e:
        if e.code == 400 and "temperature" in str(e.detail):
            res = json.loads(call("/chat/completions", json.dumps(base).encode(), "application/json"))
        else:
            die(e)
    return json.loads(res["choices"][0]["message"]["content"])


def shorten(text, limit, model, context):
    best = text
    for _ in range(3):
        data = chat_json(model, SHORTEN_SYSTEM, json.dumps({"context": context or "none", "max_chars": limit, "text": best}))
        cand = str(data.get("text", "")).strip()
        if cand and len(cand) <= limit:
            return cand
        if cand and len(cand) < len(best):
            best = cand
        limit = max(limit - 2, 4)
    return best


def improve(segs, model, context, chunk=60):
    for i in range(0, len(segs), chunk):
        part = segs[i:i + chunk]
        payload = {
            "context": context or "none",
            "segments": [
                {"id": s["id"], "start": s["start"], "end": s["end"], "slot_seconds": round(s["slot"], 2),
                 "max_chars": s["max_chars"], "text": s["text"]}
                for s in part
            ],
        }
        got = None
        for attempt in range(3):
            data = chat_json(model, IMPROVE_SYSTEM, json.dumps(payload))
            cand = data.get("segments")
            if isinstance(cand, list) and [c.get("id") for c in cand] == [s["id"] for s in part] and all(isinstance(c.get("text"), str) for c in cand):
                got = cand
                break
            warn("model returned malformed segments, retry %d/3" % (attempt + 1))
        if got is None:
            warn("keeping original text for segments %d-%d" % (part[0]["id"], part[-1]["id"]))
            continue
        for s, c in zip(part, got):
            text = c["text"].strip()
            if not text and s["slot"] >= 1.5:
                text = s["text"]
            s["text"] = text
        info("rewrote segments %d-%d" % (part[0]["id"], part[-1]["id"]))
    trimmed = 0
    for s in segs:
        if len(s["text"]) > s["max_chars"]:
            s["text"] = shorten(s["text"], s["max_chars"], model, context)
            trimmed += 1
    if trimmed:
        info("shortened %d over-limit line(s)" % trimmed)


def tts_path(s, args, work):
    key = json.dumps([args.tts_model, args.voice, args.style, s["text"]])
    return "%s/seg_%03d_%s.wav" % (work, s["id"], hashlib.sha1(key.encode()).hexdigest()[:8])


def tts(text, path, args):
    body = json.dumps({
        "model": args.tts_model, "voice": args.voice, "input": text,
        "instructions": args.style, "response_format": "wav",
    }).encode()
    try:
        data = call("/audio/speech", body, "application/json")
    except ApiError as e:
        die(e)
    with open(path, "wb") as f:
        f.write(data)


def synth_and_fit(segs, args, work):
    live = [s for s in segs if s["text"].strip()]
    for rnd in range(4):
        over = []
        made = 0
        for n, s in enumerate(live, 1):
            path = tts_path(s, args, work)
            if not os.path.exists(path):
                tts(s["text"], path, args)
                made += 1
                log("    TTS %d/%d seg#%d (slot %.1fs) %r" % (n, len(live), s["id"], s["slot"], s["text"][:60]))
            s["wav"] = path
            s["dur"] = duration(path)
            s["over"] = s["dur"] / args.max_tempo > s["slot"] + 0.05
            if s["over"]:
                over.append(s)
        if not over:
            ok("all %d lines fit their time slots" % len(live))
            return
        if rnd == 3:
            break
        info("%d line(s) too long for their slot, asking %s to shorten (round %d/3)" % (len(over), args.text_model, rnd + 1))
        for s in over:
            ratio = s["slot"] * args.max_tempo / s["dur"]
            limit = max(int(len(s["text"]) * ratio * 0.93), 6)
            old = s["text"]
            s["text"] = shorten(old, limit, args.text_model, args.context)
            log("    seg#%d %r -> %r" % (s["id"], old, s["text"]))
    for s in over:
        warn("seg#%d still overflows its slot by %.2fs and may overlap the next line" % (s["id"], s["dur"] / args.max_tempo - s["slot"]))


def tempo_for(s, max_tempo):
    if s["dur"] <= s["slot"]:
        return 1.0
    return min(s["dur"] / s["slot"], max_tempo)


def mix_and_merge(segs, video, total, work, out_video, args):
    live = [s for s in segs if s["text"].strip()]
    inputs, filters, labels = [], [], []
    for k, s in enumerate(live):
        fit = "%s/fit_%03d.wav" % (work, s["id"])
        sh("ffmpeg", "-y", "-i", s["wav"], "-filter:a", "atempo=%.4f" % tempo_for(s, args.max_tempo), fit)
        inputs += ["-i", fit]
        ms = int(s["start"] * 1000)
        filters.append("[%d:a]adelay=%d|%d[a%d]" % (k, ms, ms, k))
        labels.append("[a%d]" % k)
    graph = ";".join(filters) + ";" + "".join(labels) + (
        "amix=inputs=%d:normalize=0:duration=longest,afftdn=nf=-30,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,apad,atrim=0:%.3f[out]" % (len(labels), total)
    )
    gpath = work + "/graph.txt"
    with open(gpath, "w") as f:
        f.write(graph)
    track = work + "/ai_track.m4a"
    sh("ffmpeg", "-y", *inputs, "-filter_complex_script", gpath, "-map", "[out]", "-c:a", "aac", "-b:a", "192k", track)
    ok("mixed %d lines, denoised, loudness-normalised" % len(live))
    return track


def write_report(path, video, segs, args, out_video, total):
    lines = [
        "# AI Dub Report: %s" % os.path.basename(video), "",
        "- Output: `%s`" % os.path.basename(out_video),
        "- Duration: %s" % ts(total),
        "- Transcription model: `%s`" % args.transcribe_model,
        "- Text model: `%s`" % args.text_model,
        "- TTS model / voice: `%s` / `%s`" % (args.tts_model, args.voice),
        "- Max speed-up: %.2fx" % args.max_tempo, "",
        "| # | Start | Slot (s) | Speech (s) | Speed | Status | Text |",
        "|---|-------|----------|------------|-------|--------|------|",
    ]
    for s in segs:
        if not s["text"].strip():
            lines.append("| %d | %s | %.2f | - | - | merged | (empty) |" % (s["id"], ts(s["start"]), s["slot"]))
            continue
        t = tempo_for(s, args.max_tempo)
        status = "OVERFLOW" if s["over"] else "ok"
        lines.append("| %d | %s | %.2f | %.2f | %.2fx | %s | %s |" % (
            s["id"], ts(s["start"]), s["slot"], s["dur"] / t, t, status, s["text"].replace("|", "\\|")))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def load_cache(path, sig):
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            if data.get("sig") == sig:
                return data["segments"]
        except (ValueError, KeyError):
            pass
    return None


def parse_args():
    p = argparse.ArgumentParser(description="Replace a video's speech with natural AI voice-over using OpenAI.")
    p.add_argument("video", help="input video containing an audio track")
    p.add_argument("--out-dir", help="where to write outputs (default: next to the video)")
    p.add_argument("--voice", default="nova")
    p.add_argument("--tts-model", default="gpt-4o-mini-tts")
    p.add_argument("--text-model", default="gpt-4.1")
    p.add_argument("--transcribe-model", default="whisper-1")
    p.add_argument("--language", default="en", help="ISO-639-1 code of the spoken language, empty for auto")
    p.add_argument("--context", default="", help="glossary/terminology rules for the rewrite, e.g. \"Say 'QR code' instead of 'fuel code'\"")
    p.add_argument("--terms", default="", help="comma-separated names to help transcription, e.g. \"QR code, Emirates ID\"")
    p.add_argument("--style", default=DEFAULT_STYLE, help="speaking style instructions for the TTS voice")
    p.add_argument("--max-tempo", type=float, default=1.3, help="max speed-up applied to fit a line into its slot")
    p.add_argument("--no-snap", action="store_true", help="do not snap line starts to detected speech onsets")
    p.add_argument("--no-improve", action="store_true", help="skip the AI rewrite and voice the raw transcript")
    p.add_argument("--use-md", action="store_true", help="skip transcribe/rewrite and use the existing *_transcript_improved.md as edited by you")
    p.add_argument("--review", action="store_true", help="pause after the rewrite so you can edit the improved .md")
    p.add_argument("--fresh", action="store_true", help="ignore cached transcription")
    p.add_argument("--clean", action="store_true", help="delete the work folder when finished")
    return p.parse_args()


def main():
    args = parse_args()
    started = time.time()
    video = os.path.abspath(args.video)
    if not os.path.isfile(video):
        die("video not found: " + video)

    step(1, "Checking inputs")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            die(tool + " not found. Install with: brew install ffmpeg")
    api_key()
    ok("OPENAI_API_KEY found (value hidden)")
    if not has_audio(video):
        die("the video has no audio track")
    total = duration(video)
    base, ext = os.path.splitext(os.path.basename(video))
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else os.path.dirname(video)
    os.makedirs(out_dir, exist_ok=True)
    work = "%s/%s_ai_work" % (out_dir, base)
    os.makedirs(work, exist_ok=True)
    md_orig = "%s/%s_transcript.md" % (out_dir, base)
    md_impr = "%s/%s_transcript_improved.md" % (out_dir, base)
    md_report = "%s/%s_ai_report.md" % (out_dir, base)
    out_video = "%s/%s_ai%s" % (out_dir, base, ext)
    st = os.stat(video)
    sig = "%d-%d" % (st.st_size, int(st.st_mtime))
    ok("video: %s (%s)" % (os.path.basename(video), ts(total)))
    ok("output will be: %s" % os.path.basename(out_video))

    step(2, "Extracting audio")
    wav = work + "/original.wav"
    extract_audio(video, wav)
    ok("saved %s" % os.path.relpath(wav, out_dir))

    step(3, "Transcribing with OpenAI (%s)" % args.transcribe_model)
    cache = work + "/transcript.json"
    segs = None if args.fresh else load_cache(cache, sig)
    if segs:
        ok("reusing cached transcription (%d segments), use --fresh to redo" % len(segs))
    else:
        prompt = DEFAULT_TRANSCRIBE_PROMPT
        if args.terms:
            prompt += " Terms: " + args.terms + "."
        segs = transcribe(wav, args.transcribe_model, args.language, prompt)
        with open(cache, "w") as f:
            json.dump({"sig": sig, "segments": segs}, f, indent=2)
        ok("%d timestamped segments" % len(segs))
    write_md(md_orig, "Transcript: " + os.path.basename(video),
             "Source: OpenAI `%s`. Duration: %s. Raw machine transcript with segment timings." % (args.transcribe_model, ts(total)), segs)
    ok("wrote %s" % os.path.basename(md_orig))

    if not args.no_snap:
        moved = snap_starts(segs, detect_silences(wav))
        info("snapped %d line start(s) to detected speech onset" % moved)
    assign_slots(segs, total)

    step(4, "Improving the script with %s" % args.text_model)
    if args.use_md:
        if not os.path.exists(md_impr):
            die("--use-md given but %s does not exist" % md_impr)
        texts = read_md_texts(md_impr)
        for s in segs:
            if s["id"] in texts:
                s["text"] = texts[s["id"]]
        ok("using your edited %s" % os.path.basename(md_impr))
    elif args.no_improve:
        info("--no-improve: voicing the raw transcript")
    else:
        improve(segs, args.text_model, args.context, 60)
    write_md(md_impr, "Improved Transcript: " + os.path.basename(video),
             "Narration script the AI voice reads. Edit the Text column, then re-run with `--use-md`. Original: `%s`." % os.path.basename(md_orig), segs)
    ok("wrote %s" % os.path.basename(md_impr))
    if args.review and sys.stdin.isatty():
        input("  Edit %s if you want, then press Enter to continue... " % os.path.basename(md_impr))
        texts = read_md_texts(md_impr)
        for s in segs:
            if s["id"] in texts:
                s["text"] = texts[s["id"]]

    step(5, "Generating AI speech (%s, voice %s) and fitting to timings" % (args.tts_model, args.voice))
    synth_and_fit(segs, args, work)
    write_md(md_impr, "Improved Transcript: " + os.path.basename(video),
             "Narration script the AI voice reads. Edit the Text column, then re-run with `--use-md`. Original: `%s`." % os.path.basename(md_orig), segs)

    step(6, "Mixing timeline and enhancing audio")
    track = mix_and_merge(segs, video, total, work, out_video, args)

    step(7, "Merging with the original video")
    sh("ffmpeg", "-y", "-i", video, "-i", track, "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "copy", "-shortest", out_video)
    write_report(md_report, video, segs, args, out_video, total)
    if args.clean:
        shutil.rmtree(work, ignore_errors=True)
    ok("wrote %s" % os.path.basename(out_video))
    ok("wrote %s" % os.path.basename(md_report))

    log("")
    log("Done in %.0fs. Original video untouched." % (time.time() - started))
    log("  video : " + out_video)
    log("  md    : " + md_orig)
    log("  md    : " + md_impr)
    log("  md    : " + md_report)


if __name__ == "__main__":
    try:
        main()
    except ApiError as e:
        die(e)
    except KeyboardInterrupt:
        die("interrupted")
