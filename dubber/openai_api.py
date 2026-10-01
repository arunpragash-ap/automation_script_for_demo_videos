import http.client
import json
import os
import signal
import threading
import time
import urllib.error
import urllib.request
import uuid

from . import config, logs
from .logs import redact
from .ui import die, info, warn


class ApiError(Exception):
    def __init__(self, code, detail):
        self.code = code
        self.detail = detail
        super().__init__("OpenAI HTTP %s: %s" % (code, self.readable(detail)))

    @staticmethod
    def readable(detail):
        try:
            return json.loads(detail)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            return detail


class Deadline:
    def __init__(self, seconds):
        self.seconds = seconds
        self.active = hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()
        self.previous = None

    def fire(self, signum, frame):
        raise TimeoutError("no complete response within %ds" % self.seconds)

    def __enter__(self):
        if self.active:
            self.previous = signal.signal(signal.SIGALRM, self.fire)
            signal.setitimer(signal.ITIMER_REAL, self.seconds)
        return self

    def __exit__(self, *exc):
        if self.active:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous)
        return False


def api_key():
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        die("OPENAI_API_KEY is not set. Add `export OPENAI_API_KEY=...` to ~/.zshrc, then run: source ~/.zshrc")
    return key


def call(path, body, content_type, timeout=90):
    for attempt in range(1, 6):
        req = urllib.request.Request(
            config.API_URL + path, data=body, method="POST",
            headers={"Authorization": "Bearer " + api_key(), "Content-Type": content_type},
        )
        started = time.time()
        try:
            with Deadline(timeout + 30), urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            logs.current.count(path)
            logs.current.event("debug", "openai request", endpoint=path, status=200, attempt=attempt,
                               seconds=round(time.time() - started, 2), request_bytes=len(body), response_bytes=len(data))
            return data
        except urllib.error.HTTPError as e:
            detail = redact(e.read().decode("utf-8", "replace")[:500])
            logs.current.event("debug", "openai request failed", endpoint=path, status=e.code, attempt=attempt,
                               seconds=round(time.time() - started, 2), detail=detail)
            if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                wait = 2 ** attempt
                warn("HTTP %d from OpenAI, retrying in %ds" % (e.code, wait), endpoint=path)
                time.sleep(wait)
                continue
            raise ApiError(e.code, detail)
        except (OSError, http.client.HTTPException) as e:
            logs.current.event("debug", "openai request error", endpoint=path, attempt=attempt,
                               seconds=round(time.time() - started, 2), detail=redact(e))
            if attempt < 5:
                wait = 2 ** attempt
                warn("network error, retrying in %ds" % wait, endpoint=path)
                time.sleep(wait)
                continue
            raise ApiError("network", redact(e))


def transcribe(wav, model, language, prompt):
    boundary = uuid.uuid4().hex
    fields = [("model", model), ("response_format", "verbose_json"), ("timestamp_granularities[]", "segment"), ("timestamp_granularities[]", "word"), ("temperature", "0")]
    if language:
        fields.append(("language", language))
    if prompt:
        fields.append(("prompt", prompt[:800]))
    parts = [('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)).encode() for k, v in fields]
    with open(wav, "rb") as f:
        data = f.read()
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n' % boundary).encode() + data + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    res = json.loads(call("/audio/transcriptions", b"".join(parts), "multipart/form-data; boundary=" + boundary, 300))
    raw = res.get("segments")
    if not raw:
        die("Transcription model '%s' returned no timestamped segments. Use whisper-1." % model)
    words = res.get("words") or []
    segs = []
    for s in raw:
        text = s["text"].strip()
        if text:
            segs.append({"id": len(segs), "start": round(s["start"], 2), "end": round(s["end"], 2), "text": text,
                         "whisper_start": round(s["start"], 2), "whisper_end": round(s["end"], 2),
                         "no_speech_prob": round(s.get("no_speech_prob", 0.0), 3),
                         "avg_logprob": round(s.get("avg_logprob", 0.0), 3),
                         "compression_ratio": round(s.get("compression_ratio", 0.0), 2)})
    refine_with_words(segs, words)
    return segs


def refine_with_words(segs, words):
    if not words:
        return
    i = 0
    grouped = [[] for _ in segs]
    for w in words:
        while i < len(segs) - 1 and w["start"] >= segs[i]["whisper_end"] + 0.25:
            i += 1
        grouped[i].append(w)
    for s, ws in zip(segs, grouped):
        if ws:
            s["start"] = round(ws[0]["start"], 2)
            s["end"] = round(max(w["end"] for w in ws), 2)


def transcribe_text(audio_path, model, language):
    boundary = uuid.uuid4().hex
    fields = [("model", model), ("response_format", "json")]
    if language:
        fields.append(("language", language))
    parts = [('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)).encode() for k, v in fields]
    with open(audio_path, "rb") as f:
        data = f.read()
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="audio.mp3"\r\nContent-Type: audio/mpeg\r\n\r\n' % boundary).encode() + data + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    res = json.loads(call("/audio/transcriptions", b"".join(parts), "multipart/form-data; boundary=" + boundary, 300))
    return res.get("text", "")


def chat_json(model, system, user):
    base = {"model": model, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    try:
        res = json.loads(call("/chat/completions", json.dumps(dict(base, temperature=0.2)).encode(), "application/json"))
    except ApiError as e:
        if e.code != 400 or "temperature" not in str(e.detail):
            raise
        res = json.loads(call("/chat/completions", json.dumps(base).encode(), "application/json"))
    return json.loads(res["choices"][0]["message"]["content"])


def detect_title_intro(title, first_lines, model):
    return chat_json(model, config.TITLE_INTRO_SYSTEM, json.dumps({"title": title, "first_lines": first_lines}))


def detect_closing_thanks(last_lines, model):
    return chat_json(model, config.CLOSING_THANKS_SYSTEM, json.dumps({"last_lines": last_lines}))


def shorten(text, limit, model, context):
    best = text
    for _ in range(3):
        data = chat_json(model, config.SHORTEN_SYSTEM, json.dumps({"context": context or "none", "max_chars": limit, "text": best}))
        cand = str(data.get("text", "")).strip()
        if cand and len(cand) <= limit:
            return cand
        if cand and len(cand) < len(best):
            best = cand
        limit = max(limit - 2, 4)
    return best


def improve_segments(segs, model, context, chunk=60, feedback="", reference=""):
    for i in range(0, len(segs), chunk):
        part = segs[i:i + chunk]
        payload = {
            "context": context or "none",
            "reviewer_feedback": feedback or "none",
            "second_transcription": reference[:12000] or "none",
            "segments": [
                {"id": s["id"], "start": s["start"], "end": s["end"], "slot_seconds": round(s["slot"], 2),
                 "max_chars": s["max_chars"], "text": s["text"]}
                for s in part
            ],
        }
        got = None
        for attempt in range(3):
            cand = chat_json(model, config.REVISE_SYSTEM if feedback else config.IMPROVE_SYSTEM, json.dumps(payload)).get("segments")
            if isinstance(cand, list) and [c.get("id") for c in cand] == [s["id"] for s in part] and all(isinstance(c.get("text"), str) for c in cand):
                got = cand
                break
            warn("model returned malformed segments, retry %d/3" % (attempt + 1))
        if got is None:
            warn("keeping original text for segments %d-%d" % (part[0]["id"], part[-1]["id"]))
            continue
        for s, c in zip(part, got):
            text = c["text"].strip()
            s["text"] = text if text or s["slot"] < 1.5 else s["text"]
        info("rewrote segments %d-%d" % (part[0]["id"], part[-1]["id"]))
    trimmed = 0
    for s in segs:
        if len(s["text"]) > s["max_chars"]:
            s["text"] = shorten(s["text"], s["max_chars"], model, context)
            trimmed += 1
    if trimmed:
        info("shortened %d over-limit line(s)" % trimmed)


def synthesize(text, path, model, voice, style):
    body = json.dumps({"model": model, "voice": voice, "input": text, "instructions": style, "response_format": "wav"}).encode()
    with open(path, "wb") as f:
        f.write(call("/audio/speech", body, "application/json"))
