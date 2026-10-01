import re

PATTERN = re.compile(r"\[\s*pause(?:\s+(\d+(?:\.\d+)?)\s*s?)?\s*\]", re.IGNORECASE)
DEFAULT = 1.0
LONGEST = 10.0


def has(text):
    return bool(PATTERN.search(text))


def strip(text):
    return re.sub(r"\s+", " ", PATTERN.sub(" ", text)).strip()


def split(text):
    pieces, pause, pos = [], 0.0, 0
    for m in PATTERN.finditer(text):
        chunk = text[pos:m.start()].strip()
        if chunk:
            pieces.append({"text": chunk, "pause": pause})
            pause = 0.0
        pause += min(float(m.group(1)) if m.group(1) else DEFAULT, LONGEST)
        pos = m.end()
    chunk = text[pos:].strip()
    if chunk:
        pieces.append({"text": chunk, "pause": pause})
    return pieces
