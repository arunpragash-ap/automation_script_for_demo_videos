from . import media

REACH = 1.5


def speech_intervals(silences, total):
    spans, pos = [], 0.0
    for start, end in silences:
        if start - pos > 0.01:
            spans.append((pos, start))
        pos = max(pos, end)
    if total - pos > 0.01:
        spans.append((pos, total))
    return spans


def group(spans, gap):
    blocks = []
    for start, end in spans:
        if blocks and start - blocks[-1][1] < gap:
            blocks[-1] = (blocks[-1][0], end)
        else:
            blocks.append((start, end))
    return blocks


def find_block(spans, at, gap):
    near = [(abs(start - at), i) for i, (start, _) in enumerate(spans) if abs(start - at) <= REACH]
    if not near:
        return None
    first = min(near)[1]
    start, end = spans[first]
    for s, e in spans[first + 1:]:
        if s - end >= gap:
            break
        end = e
    return start, end


def containing(spans, at):
    return next(((s, e) for s, e in spans if s <= at <= e), None)


def analyse(path, total=None):
    total = total or media.probe_duration(path)
    return speech_intervals(media.detect_silences(path), total), total
