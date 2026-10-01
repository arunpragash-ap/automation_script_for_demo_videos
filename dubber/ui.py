import shutil
import sys
import threading
import time

from . import logs
from .logs import redact


class Abort(Exception):
    pass


def say(msg=""):
    print(redact(msg), flush=True)


def ok(msg, **data):
    logs.current.event("success", msg, **data)


def info(msg, **data):
    logs.current.event("info", msg, **data)


def warn(msg, **data):
    logs.current.event("warning", msg, **data)


def die(msg):
    logs.current.event("error", str(msg))
    raise Abort(msg)


def fmt_time(t):
    m, s = divmod(t, 60)
    return "%02d:%05.2f" % (m, s)


def ask(prompt, default="", show_default=True):
    suffix = " [%s]" % default if default and show_default else ""
    try:
        value = input("%s%s: " % (prompt, suffix)).strip()
    except EOFError:
        return default
    return value or default


def choose(prompt, options, default):
    say(prompt)
    for key, label in options.items():
        say("  %s) %s" % (key, label))
    while True:
        value = ask("Choose", default).lower()
        if value in options:
            return value
        say("  enter one of: " + ", ".join(options))


class Status:
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self):
        self.label = ""
        self.detail = ""
        self._thread = None
        self._stop = threading.Event()
        self._t0 = 0.0

    def start(self, label):
        self.stop()
        self.label, self.detail, self._t0 = label, "", time.time()
        if not sys.stdout.isatty():
            say("→ " + label)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            text = "%s %s%s  %ds" % (self.FRAMES[i % len(self.FRAMES)], self.label, " " + self.detail if self.detail else "", time.time() - self._t0)
            width = shutil.get_terminal_size().columns - 1
            sys.stdout.write("\r\033[K" + text[:width])
            sys.stdout.flush()
            i += 1
            time.sleep(0.1)

    def stop(self):
        if self._thread:
            self._stop.set()
            self._thread.join()
            self._thread = None
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


status = Status()


def progress(detail):
    status.detail = detail
