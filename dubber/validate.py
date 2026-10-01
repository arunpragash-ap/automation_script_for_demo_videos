import collections
import json

from . import media, pauses
from .ui import fmt_time

MIN_SPEECH = 0.15
MAX_EARLY_START = 0.5
MIN_MATCH = 0.6


def words(text):
    return media.normalize_text(pauses.strip(text)).split()


def match_ratio(text, reference_counts):
    tokens = words(text)
    if not tokens:
        return 1.0
    pool = collections.Counter(reference_counts)
    hits = 0
    for token in tokens:
        if pool[token] > 0:
            pool[token] -= 1
            hits += 1
    return hits / len(tokens)


def check_lines(segs, silences, reference_text):
    reference = collections.Counter(words(reference_text)) if reference_text else None
    results = []
    for i, s in enumerate(segs):
        speech = 1.0 - media.silent_fraction(s["start"], s["end"], silences)
        early = max((b - s["start"] for a, b in silences if a <= s["start"] < b), default=0.0)
        match = match_ratio(s["text"], reference) if reference is not None else None
        if reference is not None:
            for token in words(s["text"]):
                if reference[token] > 0:
                    reference[token] -= 1
        issues = []
        if speech < MIN_SPEECH:
            issues.append("little or no speech in this time range")
        if early > MAX_EARLY_START:
            issues.append("starts %.1fs before any speech begins" % early)
        if i + 1 < len(segs) and s["end"] > segs[i + 1]["start"] + 0.05:
            issues.append("overlaps the next line")
        if match is not None and match < MIN_MATCH:
            issues.append("second transcription disagrees (%.0f%% of words found)" % (100 * match))
        results.append({
            "id": s["id"], "start": s["start"], "end": s["end"], "text": s["text"],
            "speech_fraction": round(speech, 2), "starts_early_by": round(early, 2),
            "second_opinion_match": None if match is None else round(match, 2),
            "issues": issues, "verdict": "check" if issues else "ok",
        })
    return results


def write_report(path, video_name, results, used_second_opinion, model):
    flagged = [r for r in results if r["verdict"] == "check"]
    lines = [
        "# Validation: %s" % video_name, "",
        "- Lines: %d, OK: %d, need a look: %d" % (len(results), len(results) - len(flagged), len(flagged)),
        "- Timing check: speech must be present in each line's time range, and a line must not start inside a silence before the speaker begins.",
        "- Second opinion: %s" % ("independent transcription with `%s`; each line's words must appear in it" % model if used_second_opinion else "not available in this run"),
        "", "| # | Time | Speech | Starts early by | Second opinion | Verdict | Text |",
        "|---|------|--------|-----------------|----------------|---------|------|",
    ]
    for r in results:
        offset = "%.2fs" % r["starts_early_by"]
        match = "-" if r["second_opinion_match"] is None else "%.0f%%" % (100 * r["second_opinion_match"])
        verdict = "ok" if not r["issues"] else "CHECK: " + "; ".join(r["issues"])
        lines.append("| %d | %s-%s | %.0f%% | %s | %s | %s | %s |" % (
            r["id"], fmt_time(r["start"]), fmt_time(r["end"]), 100 * r["speech_fraction"], offset, match, verdict, r["text"].replace("|", "\\|")))
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def save_json(path, results):
    with open(path, "w") as f:
        json.dump(results, f, indent=2)


def load_json(path):
    with open(path) as f:
        return json.load(f)
