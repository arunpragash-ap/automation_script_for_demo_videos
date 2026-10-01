import json
import os
import platform
import re
import sys
import time
from datetime import datetime


def redact(text):
    text = re.sub(r"sk-[A-Za-z0-9_\-\*\.]{6,}", "sk-***", str(text))
    key = os.environ.get("OPENAI_API_KEY", "")
    if key:
        text = text.replace(key, "***")
    return text


def stamp(precision="seconds"):
    return datetime.now().astimezone().isoformat(timespec=precision)


class RunLog:
    def __init__(self):
        self.path = None
        self.run = {}
        self.steps = []
        self.events = []
        self.usage = {}
        self.step_key = None
        self._t0 = time.time()
        self._step_t0 = 0.0

    def open(self, base_dir, **meta):
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        logs_dir = os.path.join(base_dir, "logs")
        os.makedirs(logs_dir, exist_ok=True)
        self.path = os.path.join(logs_dir, "log_%s.json" % run_id)
        self._t0 = time.time()
        self.run = dict(
            id=run_id, started=stamp(), finished=None, status="running", error=None, duration_seconds=None,
            python=sys.version.split()[0], platform=platform.platform(), **meta,
        )
        self.write()

    def event(self, level, message, **data):
        entry = {"time": stamp("milliseconds"), "level": level, "step": self.step_key, "message": message}
        entry.update(data)
        self.events.append(json.loads(redact(json.dumps(entry, default=str, ensure_ascii=False))))
        self.write()

    def count(self, endpoint):
        self.usage[endpoint] = self.usage.get(endpoint, 0) + 1

    def step_start(self, key, title, index):
        self.step_key = key
        self._step_t0 = time.time()
        self.steps.append({"index": index, "key": key, "title": title, "status": "running", "started": stamp(), "ended": None, "seconds": None, "error": None})
        self.event("info", "step started", title=title)

    def step_end(self, status, error=None):
        if not self.steps or self.steps[-1]["status"] != "running":
            return
        seconds = round(time.time() - self._step_t0, 2)
        self.steps[-1].update(status=status, ended=stamp(), seconds=seconds, error=redact(error) if error else None)
        self.event("info" if status == "success" else "error", "step %s" % status, seconds=seconds)
        self.step_key = None

    def finish(self, status, error=None):
        self.run.update(status=status, finished=stamp(), error=redact(error) if error else None,
                        duration_seconds=round(time.time() - self._t0, 2))
        self.write()

    def write(self):
        if not self.path:
            return
        doc = {"run": self.run, "steps": self.steps, "api_usage": self.usage, "events": self.events}
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f, indent=2, ensure_ascii=False, default=str)
        os.replace(tmp, self.path)


current = RunLog()
