import re

from .ui import fmt_time


def write_md(path, title, note, segs):
    lines = ["# " + title, "", note, "", "| # | Start | End | Text |", "|---|-------|-----|------|"]
    for s in segs:
        lines.append("| %d | %s | %s | %s |" % (s["id"], fmt_time(s["start"]), fmt_time(s["end"]), s["text"].replace("|", "\\|")))
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


def read_md_rows(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", line[1:-1] if line.endswith("|") else line[1:])]
            if len(cells) >= 4 and cells[0].isdigit():
                rows.append((int(cells[0]), cells[1], cells[3].replace("\\|", "|")))
    return rows


def update_md_text(path, seg_id, text):
    with open(path) as f:
        lines = f.read().split("\n")
    for i, line in enumerate(lines):
        cells = re.split(r"(?<!\\)\|", line.strip())
        if len(cells) >= 6 and cells[1].strip() == str(seg_id):
            lines[i] = "|%s|%s|%s| %s |" % (cells[1], cells[2], cells[3], text.replace("|", "\\|"))
            break
    with open(path, "w") as f:
        f.write("\n".join(lines))
