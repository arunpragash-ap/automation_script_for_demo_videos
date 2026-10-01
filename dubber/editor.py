import curses
import os
import textwrap

BACKSPACE = (curses.KEY_BACKSPACE, "\x7f", "\b")
ENTER = ("\n", "\r", curses.KEY_ENTER)
ESCAPE = "\x1b"


def wrapped(text, width):
    return textwrap.wrap(text, width) or [""]


def item_lines(row, text, width, edited):
    prefix = "%s%3d %s  " % ("*" if edited else " ", row[0], row[1])
    body = wrapped(text or "(merged)", max(width - len(prefix), 10))
    pad = " " * len(prefix)
    return [prefix + body[0]] + [pad + line for line in body[1:]]


def draw_list(screen, rows, texts, edited, selected, top, title):
    screen.erase()
    height, width = screen.getmaxyx()
    screen.addnstr(0, 0, title, width - 1, curses.A_BOLD)
    screen.addnstr(1, 0, "Up/Down move   Enter edit line   s save and exit   q cancel", width - 1)
    row_y = 3
    visible_last = top
    for i in range(top, len(rows)):
        lines = item_lines(rows[i], texts[i], width - 1, i in edited)
        if row_y + len(lines) > height - 2:
            break
        for line in lines:
            screen.addnstr(row_y, 0, line, width - 1, curses.A_REVERSE if i == selected else 0)
            row_y += 1
        visible_last = i
    screen.addnstr(height - 1, 0, "%d line(s) edited" % len(edited), width - 1, curses.A_DIM)
    screen.refresh()
    return visible_last


def keep_visible(rows, texts, edited, selected, top, height, width):
    if selected < top:
        return selected
    while True:
        used = 0
        for i in range(top, selected + 1):
            used += len(item_lines(rows[i], texts[i], width - 1, i in edited))
        if 3 + used <= height - 2 or top >= selected:
            return top
        top += 1


def edit_line(screen, row, text):
    buf, pos = text, len(text)
    while True:
        screen.erase()
        height, width = screen.getmaxyx()
        box = max(width - 4, 10)
        screen.addnstr(0, 0, "Editing line %d (%s)" % (row[0], row[1]), width - 1, curses.A_BOLD)
        screen.addnstr(1, 0, "Enter keep change   Esc cancel   Ctrl-A start   Ctrl-E end   Ctrl-K delete to end   Ctrl-U clear", width - 1)
        for n, chunk in enumerate([buf[i:i + box] for i in range(0, max(len(buf), 1), box)]):
            if 3 + n < height - 1:
                screen.addnstr(3 + n, 2, chunk, box)
        cursor_y, cursor_x = 3 + pos // box, 2 + pos % box
        screen.move(min(cursor_y, height - 1), min(cursor_x, width - 1))
        screen.refresh()
        key = screen.get_wch()
        if key in ENTER:
            return buf
        if key == ESCAPE:
            return None
        if key in BACKSPACE:
            if pos:
                buf, pos = buf[:pos - 1] + buf[pos:], pos - 1
        elif key == curses.KEY_DC:
            buf = buf[:pos] + buf[pos + 1:]
        elif key == curses.KEY_LEFT:
            pos = max(pos - 1, 0)
        elif key == curses.KEY_RIGHT:
            pos = min(pos + 1, len(buf))
        elif key in (curses.KEY_HOME, "\x01"):
            pos = 0
        elif key in (curses.KEY_END, "\x05"):
            pos = len(buf)
        elif key == "\x0b":
            buf = buf[:pos]
        elif key == "\x15":
            buf, pos = buf[pos:], 0
        elif isinstance(key, str) and key.isprintable():
            buf, pos = buf[:pos] + key + buf[pos:], pos + 1


def confirm(screen, question):
    height, width = screen.getmaxyx()
    screen.addnstr(height - 1, 0, (question + " (y/n)").ljust(width - 1), width - 1, curses.A_BOLD)
    screen.refresh()
    while True:
        key = screen.get_wch()
        if key in ("y", "Y"):
            return True
        if key in ("n", "N", ESCAPE):
            return False


def session(screen, rows, title):
    curses.curs_set(0)
    screen.keypad(True)
    texts = [r[2] for r in rows]
    original = list(texts)
    edited = set()
    selected = top = 0
    while True:
        height, width = screen.getmaxyx()
        top = keep_visible(rows, texts, edited, selected, top, height, width)
        draw_list(screen, rows, texts, edited, selected, top, title)
        key = screen.get_wch()
        if key in (curses.KEY_DOWN, "j") and selected < len(rows) - 1:
            selected += 1
        elif key in (curses.KEY_UP, "k") and selected > 0:
            selected -= 1
        elif key == curses.KEY_NPAGE:
            selected = min(selected + 8, len(rows) - 1)
        elif key == curses.KEY_PPAGE:
            selected = max(selected - 8, 0)
        elif key in (curses.KEY_HOME, "g"):
            selected = 0
        elif key in (curses.KEY_END, "G"):
            selected = len(rows) - 1
        elif key in ENTER or key == "e":
            curses.curs_set(1)
            new = edit_line(screen, rows[selected], texts[selected])
            curses.curs_set(0)
            if new is not None:
                texts[selected] = new
                if new == original[selected]:
                    edited.discard(selected)
                else:
                    edited.add(selected)
        elif key in ("s", "S"):
            return {rows[i][0]: texts[i] for i in sorted(edited)}
        elif key in ("q", "Q", ESCAPE):
            if not edited or confirm(screen, "Discard %d edited line(s)?" % len(edited)):
                return None


def edit_lines(rows, title="Edit lines"):
    os.environ.setdefault("ESCDELAY", "25")
    return curses.wrapper(session, rows, title)
