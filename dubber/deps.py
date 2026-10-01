import os
import shutil
import sys

from .ui import say


def missing_dependencies(needs_api):
    found = []
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            found.append("'%s' not found. Install it with: brew install ffmpeg" % tool)
    if needs_api and not os.environ.get("OPENAI_API_KEY", "").strip():
        found.append("OPENAI_API_KEY is not set. Add `export OPENAI_API_KEY=...` to ~/.zshrc, then: source ~/.zshrc")
    return found


def require(needs_api):
    problems = missing_dependencies(needs_api)
    if problems:
        say("")
        say("Cannot start. Fix these first:")
        for item in problems:
            say("  - " + item)
        sys.exit(1)
