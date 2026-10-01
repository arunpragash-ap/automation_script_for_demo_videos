import json
import os
import shutil
import types
from datetime import datetime

from . import config, edits as edit_model, media
from .ui import die

FORMAT = 1
FILE = "project.json"
SUFFIX = ".demo"
RECENT = os.path.expanduser("~/.demo_studio.json")

DEFAULT_SETTINGS = {"voice": "nova", "tts_model": "gpt-4o-mini-tts", "text_model": "gpt-4.1", "style": config.DEFAULT_STYLE, "max_tempo": 1.3}


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def remember(folder):
    try:
        with open(RECENT) as f:
            items = json.load(f)
    except (OSError, ValueError):
        items = []
    items = [folder] + [i for i in items if i != folder]
    try:
        with open(RECENT, "w") as f:
            json.dump(items[:8], f)
    except OSError:
        pass


def recent():
    try:
        with open(RECENT) as f:
            return [p for p in json.load(f) if os.path.isfile(os.path.join(p, FILE))]
    except (OSError, ValueError):
        return []


class Project:
    def __init__(self, folder, data):
        self.folder = folder
        self.data = data

    @property
    def name(self):
        return self.data["name"]

    @property
    def versions(self):
        return self.data["versions"]

    @property
    def cursor(self):
        return self.data["cursor"]

    @property
    def pending(self):
        return self.data["pending"]

    @property
    def settings(self):
        return self.data["settings"]

    def path(self, *parts):
        return os.path.join(self.folder, *parts)

    @property
    def work(self):
        return self._dir("work")

    @property
    def cache(self):
        return self._dir("cache")

    def _dir(self, name):
        path = self.path(name)
        os.makedirs(path, exist_ok=True)
        return path

    def current_file(self):
        return self.path(self.versions[self.cursor]["file"])

    def opts(self):
        return types.SimpleNamespace(replace=False, **self.settings)

    def save(self):
        tmp = self.path(FILE + ".tmp")
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path(FILE))
        remember(self.folder)

    def set_settings(self, **values):
        changed = {k: v for k, v in values.items() if v is not None and self.settings.get(k) != v}
        self.settings.update(changed)
        if changed:
            self.save()

    def add_edits(self, texts):
        parsed = [edit_model.parse(t) for t in texts]
        self.pending.extend(parsed)
        self.save()
        return parsed

    def remove_pending(self, number):
        if not 1 <= number <= len(self.pending):
            die("no pending edit number %d" % number)
        gone = self.pending.pop(number - 1)
        self.save()
        return gone

    def clear_pending(self):
        self.pending.clear()
        self.save()

    def commit(self, edits, path, label=None):
        number = self.next_number()
        for old in self.versions[self.cursor + 1:]:
            if self.path(old["file"]) != path:
                try:
                    os.remove(self.path(old["file"]))
                except OSError:
                    pass
        del self.versions[self.cursor + 1:]
        self.data["counter"] = number
        self.versions.append({"id": number, "file": os.path.relpath(path, self.folder), "label": label or "; ".join(edit_model.to_text(e) for e in edits)[:200],
                              "edits": edits, "created": now(), "seconds": round(media.probe_duration(path), 2)})
        self.data["cursor"] = len(self.versions) - 1
        self.data["pending"] = []
        self.save()

    def undo(self):
        if self.cursor == 0:
            die("nothing to undo, this is the original video")
        self.data["cursor"] -= 1
        self.save()

    def redo(self):
        if self.cursor >= len(self.versions) - 1:
            die("nothing to redo")
        self.data["cursor"] += 1
        self.save()

    def next_number(self):
        return max(self.data.get("counter", 0), *(v["id"] for v in self.versions)) + 1

    def next_version_path(self):
        number = self.next_number()
        ext = os.path.splitext(self.versions[0]["file"])[1]
        os.makedirs(self.path("versions"), exist_ok=True)
        return self.path("versions", "v%d%s" % (number, ext))

    def export_path(self, out=None):
        if out:
            return os.path.abspath(os.path.expanduser(out))
        stem, ext = os.path.splitext(os.path.basename(self.data["original_name"]))
        base = os.path.dirname(self.folder)
        path = os.path.join(base, "%s_final%s" % (stem, ext))
        n = 2
        while os.path.exists(path):
            path = os.path.join(base, "%s_final_%d%s" % (stem, n, ext))
            n += 1
        return path


def create(video, settings=None):
    source = os.path.abspath(os.path.expanduser(video))
    if not os.path.isfile(source):
        die("video not found: %s" % video)
    stem, ext = os.path.splitext(os.path.basename(source))
    folder = os.path.join(os.path.dirname(source), stem + SUFFIX)
    os.makedirs(folder, exist_ok=True)
    copy = os.path.join(folder, "source" + ext)
    if not os.path.exists(copy):
        shutil.copy2(source, copy)
    seconds = media.probe_duration(copy)
    data = {
        "format": FORMAT, "name": stem, "original_name": os.path.basename(source), "created": now(),
        "settings": dict(DEFAULT_SETTINGS, **{k: v for k, v in (settings or {}).items() if v is not None}),
        "versions": [{"id": 0, "file": "source" + ext, "label": "original video", "edits": [], "created": now(), "seconds": round(seconds, 2)}],
        "cursor": 0, "pending": [],
    }
    project = Project(folder, data)
    project.save()
    return project


def load(folder):
    with open(os.path.join(folder, FILE)) as f:
        data = json.load(f)
    if data.get("format") != FORMAT:
        die("project %s was made by a different version of this tool" % folder)
    return Project(folder, data)


def resolve(target, settings=None):
    path = os.path.abspath(os.path.expanduser(target))
    if os.path.isdir(path):
        if os.path.isfile(os.path.join(path, FILE)):
            return load(path), False
        die("%s is not a demo project (no %s inside). Give the video file to start a new one." % (target, FILE))
    if not os.path.isfile(path):
        die("not found: %s" % target)
    stem = os.path.splitext(os.path.basename(path))[0]
    folder = os.path.join(os.path.dirname(path), stem + SUFFIX)
    if os.path.isfile(os.path.join(folder, FILE)):
        return load(folder), False
    return create(path, settings), True
