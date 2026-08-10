"""Terminal UI layer for claude-multi: widgets, palettes, and the form editor.

Everything interactive lives here: a small stdlib-curses widget set (labels,
badges, key bars, checkboxes and groups, select lists, text inputs, modals,
tables), light/dark/mono palette detection (COLORFGBG, then an OSC 11 query,
default dark; NO_COLOR honored), and the single form-based composition editor.

Terminal discipline: curses is only ever entered through
:func:`run_curses_on_streams`, which points fds 0/1 at the caller's terminal
streams when needed and always restores them (``try/finally`` plus
``curses.wrapper``'s own teardown).  Every colorized element also carries a
text form (UX section 8): checkboxes render ``[x]``/``[ ]``, badges render
their words, status lines render ``Ready``/``BLOCKED`` verbatim.

The composition editor state (:class:`EditorState`) is pure and has no
terminal or persistence dependencies; the form screen and the CLI interpret
its outcomes.  ``$EDITOR`` is integrated exactly once: Ctrl+G inside the form
opens the raw composition JSON for editing (with the terminal suspended and
restored around it).  On terminals without curses capability there is no
second interactive implementation — the CLI prints the plan read-only plus
the exact ``$EDITOR`` command (see :func:`dumb_edit_guidance`).
"""

from __future__ import annotations

import contextlib
import copy
import curses
import os
import re
import select
import shlex
import subprocess
import sys
import tempfile
import termios
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from . import catalog as catalog_mod
from . import strict_json
from .composition import WORKFLOWS_VOCABULARY


# ---------------------------------------------------------------------------
# UX section 3 guarantee panel (verbatim wording; moved from editor.py).

WORKFLOW_GUARANTEE_PANEL = (
    "Native workflows (ultracode): {state}\n"
    "  · workflow agents run as the SESSION model by default (lead family),\n"
    "    always acceptEdits — scripts may route stages to other models, so\n"
    "    unobserved workflow output is family-unknown/mixed and never counts\n"
    "    as an independent review verdict\n"
    "  · no cm-role contract, no worktree isolation, ≤16 concurrent / 1000 per run\n"
    "  · cm-* selected agents keep their own model/effort/isolation contracts"
)

WORKFLOW_OFF_NOTE = (
    "off mode: disableWorkflows:true compiled; lead ultracode→xhigh; keyword inert;\n"
    "/deep-research unavailable (documented consequences, WF L327). The TUI never\n"
    "presents off as \"safer subagents\" — just different."
)


def workflow_guarantee_panel(workflows: str) -> str:
    """The UX section 3 guarantee panel for a workflow mode (text form)."""

    panel = WORKFLOW_GUARANTEE_PANEL.format(
        state="OFF" if workflows == "off" else "ON"
    )
    if workflows == "off":
        panel += "\n" + WORKFLOW_OFF_NOTE
    return panel


# ---------------------------------------------------------------------------
# Dumb-terminal edit guidance (the no-curses path prints, never interacts).

DUMB_EDIT_BANNER = (
    "Interactive editing needs a full-screen terminal "
    "(TERM set to a capable terminal and a real tty on stdin/stdout)."
)
DUMB_EDIT_COMMAND_INTRO = (
    "The composition JSON is the source of truth. Edit it directly, "
    "then re-run claude-multi:"
)


def dumb_edit_guidance(path: Path | str, *, name: str, has_user: bool) -> str:
    """Printed edit guidance for terminals without curses capability.

    Read-only: never writes the composition; states the exact ``$EDITOR``
    command and, for the trusted seed, how a user override comes to exist.
    """

    lines = [
        DUMB_EDIT_BANNER,
        DUMB_EDIT_COMMAND_INTRO,
        f"  $EDITOR {visible_text(path)}",
    ]
    if not has_user:
        lines.append(
            f"('{visible_text(name)}' currently resolves to the trusted seed; saving JSON at "
            "that path creates your user override. "
            f"'claude-multi compose duplicate {visible_text(name)} <newname>' makes a "
            "starter copy.)"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Palettes: light/dark detection, NO_COLOR, role attributes.

# Fixed pair numbers so role attributes are plain integers (n << 8) that are
# valid before curses is initialized; init_curses_colors assigns the pairs.
_PAIR_BY_ROLE = {"accent": 1, "ok": 2, "warn": 3, "error": 4, "dim": 5}
_PAIR_FG = {
    "dark": {
        "accent": curses.COLOR_CYAN,
        "ok": curses.COLOR_GREEN,
        "warn": curses.COLOR_YELLOW,
        "error": curses.COLOR_RED,
        # 8-color fallback only; init_curses_colors overrides with a 256-color
        # gray when the terminal has one. Never black: bold black renders as
        # nearly-invisible dark gray on dark backgrounds (017).
        "dim": curses.COLOR_WHITE,
    },
    "light": {
        "accent": curses.COLOR_BLUE,
        "ok": curses.COLOR_GREEN,
        "warn": curses.COLOR_YELLOW,
        "error": curses.COLOR_RED,
        "dim": curses.COLOR_BLACK,
    },
}
_BOLD_ROLES = {
    "dark": frozenset(("accent", "error", "dim")),
    "light": frozenset(("accent", "dim")),
}
_ANSI_SGR = {
    "dark": {
        "accent": "1;36",
        "ok": "32",
        "warn": "33",
        "error": "1;31",
        "dim": "38;5;245",
    },
    "light": {
        "accent": "1;34",
        "ok": "32",
        "warn": "33",
        "error": "31",
        "dim": "38;5;240",
    },
}


@dataclass(frozen=True)
class Palette:
    """Role -> attribute mapping; ``mono`` palettes disable color entirely."""

    name: str  # "dark" | "light" | "mono"
    colors: bool

    def attr(self, role: str) -> int:
        """Curses attribute for a role; 0 for normal/mono (text form stays)."""

        if not self.colors or role in ("normal", "selected"):
            return 0
        pair = _PAIR_BY_ROLE.get(role)
        if pair is None:
            return 0
        attr = pair << 8
        if role in _BOLD_ROLES.get(self.name, ()):
            attr |= curses.A_BOLD
        return attr

    def ansi(self, text: str, role: str) -> str:
        """ANSI-styled text for line output (doctor badges); mono is verbatim."""

        if not self.colors or role not in _ANSI_SGR.get(self.name, {}):
            return text
        return f"\x1b[{_ANSI_SGR[self.name][role]}m{text}\x1b[0m"


MONO_PALETTE = Palette("mono", False)
DARK_PALETTE = Palette("dark", True)
LIGHT_PALETTE = Palette("light", True)


def background_from_colorfgbg(value: str) -> str | None:
    """COLORFGBG convention: last field is the background color index."""

    if not value:
        return None
    try:
        bg = int(value.split(";")[-1])
    except ValueError:
        return None
    if bg == 7 or bg >= 9:  # white or bright backgrounds read as light
        return "light"
    if 0 <= bg <= 8:
        return "dark"
    return None


_OSC11_RE = re.compile(
    rb"\]11;rgba?:([0-9A-Fa-f]{1,4})/([0-9A-Fa-f]{1,4})/([0-9A-Fa-f]{1,4})"
)


def _channel(hex_digits: bytes) -> float:
    digits = hex_digits.decode("ascii")
    if len(digits) >= 2:
        return int(digits[:2], 16) / 255.0
    return int(digits, 16) / 15.0


def parse_osc11_response(data: bytes) -> str | None:
    """Parse an OSC 11 reply into ``light``/``dark`` by relative luminance."""

    match = _OSC11_RE.search(data)
    if match is None:
        return None
    red, green, blue = (_channel(group) for group in match.groups())
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return "light" if luminance > 0.5 else "dark"


def query_background_osc11(
    fd_in: int, fd_out: int, *, timeout: float = 0.15
) -> str | None:
    """Ask the terminal for its background color (OSC 11), best effort.

    Runs the query in raw mode with a short timeout and drains the reply so
    no response bytes leak into a later curses input buffer.  Any failure
    (no termios, no answer, garbage) yields ``None`` — the caller defaults.
    """

    try:
        saved = termios.tcgetattr(fd_in)
    except (termios.error, OSError):
        return None
    try:
        raw = termios.tcgetattr(fd_in)
        raw[3] &= ~(termios.ICANON | termios.ECHO)
        raw[6][termios.VMIN] = 0
        raw[6][termios.VTIME] = 0
        termios.tcsetattr(fd_in, termios.TCSANOW, raw)
        try:
            os.write(fd_out, b"\x1b]11;?\x07")
        except OSError:
            return None
        data = bytearray()
        deadline_reads = 4  # reply may arrive in fragments; drain after first
        for _ in range(deadline_reads):
            try:
                ready, _, _ = select.select([fd_in], [], [], timeout)
            except (OSError, ValueError):
                return None
            if not ready:
                break
            try:
                chunk = os.read(fd_in, 256)
            except OSError:
                return None
            if not chunk:
                break
            data.extend(chunk)
            timeout = 0.02  # drain: later fragments get a tiny window
        return parse_osc11_response(bytes(data))
    except (termios.error, OSError, ValueError):
        return None
    finally:
        try:
            termios.tcsetattr(fd_in, termios.TCSANOW, saved)
        except (termios.error, OSError):
            pass


def detect_palette(
    environ: Mapping[str, str] | None = None,
    *,
    no_color: bool = False,
    tty_in: Any = None,
    tty_out: Any = None,
) -> Palette:
    """Resolve the active palette.

    Order: ``no_color``/``NO_COLOR`` forces mono; ``COLORFGBG`` decides when
    present; otherwise an OSC 11 background query on the terminal streams
    (where supported); default dark.  Streams without fds simply skip OSC.
    """

    environ = os.environ if environ is None else environ
    if no_color or "NO_COLOR" in environ:
        return MONO_PALETTE
    background = background_from_colorfgbg(environ.get("COLORFGBG", ""))
    if background is None and tty_in is not None and tty_out is not None:
        try:
            fd_in = tty_in.fileno()
            fd_out = tty_out.fileno()
        except (AttributeError, OSError):
            fd_in = fd_out = None
        if fd_in is not None and fd_out is not None:
            background = query_background_osc11(fd_in, fd_out)
    if background == "light":
        return LIGHT_PALETTE
    return DARK_PALETTE


def _dim_foreground(palette_name: str) -> int:
    """Muted-text gray (017): 256-color mid gray when the terminal has it.

    245 (#8a8a8a) on dark, 240 (#585858) on light — muted but readable on
    each. 8-color fallback is white on dark palettes and black on light
    ones — never black on dark, and never the old black-on-black trap when
    the terminal has no default-colors support either.
    """

    if getattr(curses, "COLORS", 0) >= 256:
        return 245 if palette_name == "dark" else 240
    return curses.COLOR_WHITE if palette_name == "dark" else curses.COLOR_BLACK


def init_curses_colors(palette: Palette) -> None:
    """Initialize color pairs for a palette inside a live curses session."""

    try:
        if not palette.colors or not curses.has_colors():
            return
        curses.start_color()
        try:
            curses.use_default_colors()
            background = -1
        except curses.error:
            background = curses.COLOR_BLACK
        for role, pair in _PAIR_BY_ROLE.items():
            foreground = _PAIR_FG[palette.name][role]
            if role == "dim":
                foreground = _dim_foreground(palette.name)
            try:
                curses.init_pair(pair, foreground, background)
            except curses.error:
                continue
    except curses.error:
        return


def streams_curses_capable(
    input_stream: Any, output_stream: Any, environ: Mapping[str, str] | None = None
) -> bool:
    """Whether a full-screen curses UI may run on these streams.

    Requires a capable TERM and real ttys on both sides; the actual curses
    entry point still defensively falls back if initscr itself fails.
    """

    environ = os.environ if environ is None else environ
    if environ.get("TERM") in (None, "", "dumb"):
        return False
    try:
        return bool(input_stream.isatty() and output_stream.isatty())
    except (AttributeError, ValueError, OSError):
        return False


# ---------------------------------------------------------------------------
# Key normalization: curses codes (and the test double) become Key values.


@dataclass(frozen=True)
class Key:
    kind: str  # char|enter|tab|esc|up|down|left|right|home|end|backspace|
    # delete|resize|btab|pageup|pagedown|ctrl|unknown
    ch: str = ""


_KEY_KINDS = {
    curses.KEY_UP: "up",
    curses.KEY_DOWN: "down",
    curses.KEY_LEFT: "left",
    curses.KEY_RIGHT: "right",
    curses.KEY_HOME: "home",
    curses.KEY_END: "end",
    curses.KEY_BACKSPACE: "backspace",
    curses.KEY_DC: "delete",
    curses.KEY_ENTER: "enter",
    curses.KEY_BTAB: "btab",
    curses.KEY_RESIZE: "resize",
    curses.KEY_PPAGE: "pageup",
    curses.KEY_NPAGE: "pagedown",
}


def read_key(win: Any) -> Key:
    """Read one key via ``get_wch`` (unicode-safe) and normalize it.

    Note: ncurses deliberately delivers an Alt-chord as ESC followed by its
    tail character (ESCDELAY disambiguation applies only to keypad function
    sequences). Any Python-level re-merging heuristic misclassifies fast
    human input as chords, so screens treat ESC as cancel and an immediately
    following printable as fresh input — terminal-standard behavior.
    """

    value = win.get_wch()
    if isinstance(value, str):
        if value in ("\n", "\r"):
            return Key("enter")
        if value == "\t":
            return Key("tab")
        if value == "\x1b":
            return Key("esc")
        if value == "\x7f":
            return Key("backspace")
        if len(value) == 1 and ord(value) < 32:
            return Key("ctrl", chr(ord(value) + 96))
        return Key("char", value)
    kind = _KEY_KINDS.get(value)
    if kind is None:
        return Key("unknown", str(value))
    return Key(kind)


# ---------------------------------------------------------------------------
# Drawing helpers and widgets.


def visible_text(text: str) -> str:
    """Render external text inert for terminal display (R1 audit P3).

    External strings — session cwd, project paths, agent names/files,
    composition names from user files — may contain terminal control bytes
    (ESC/OSC payloads, newlines). Every such string passes through this ONE
    sanitizer at the render points before it reaches a terminal: C0 controls
    (including ESC -> ``^[`` and newline -> ``^J``) become caret notation,
    DEL becomes ``^?``, and C1 controls become ``\\xNN`` hex. Launcher-owned
    ANSI styling is never routed through this function.
    """

    out: list[str] = []
    for ch in str(text):
        code = ord(ch)
        if code < 0x20:
            out.append("^" + chr(code + 0x40))
        elif code == 0x7F:
            out.append("^?")
        elif 0x80 <= code <= 0x9F:
            out.append(f"\\x{code:02x}")
        else:
            out.append(ch)
    return "".join(out)


def visible_message(text: object) -> str:
    """Newline-preserving variant for launcher-owned multi-line messages.

    Apply at message write points (e.g. the main error writer): each line is
    neutralized independently (external payloads on any line stay inert),
    while launcher-owned line structure survives — a multi-line error never
    collapses into a single ``^J``-mangled line.
    """

    return "\n".join(visible_text(line) for line in str(text).split("\n"))


def safe_add(win: Any, row: int, col: int, text: str, attr: int = 0) -> None:
    """Bounds-checked addstr; writing the last cell raises in curses, so clip.

    The text is sanitized with :func:`visible_text`: this is the single
    curses drawing helper, so no external control bytes ever reach the
    terminal through a widget. Styling travels via curses attributes, never
    via embedded ANSI, so sanitizing the text cannot mangle launcher styling.
    """

    try:
        height, width = win.getmaxyx()
    except (AttributeError, curses.error):
        return
    if row < 0 or row >= height or col >= width - 1:
        return
    if col < 0:
        text = text[-col:]
        col = 0
    clipped = visible_text(text)[: max(0, width - col - 1)]
    if not clipped:
        return
    try:
        win.addstr(row, col, clipped, attr)
    except curses.error:
        pass


@dataclass
class Label:
    """Static text line with an optional palette role."""

    text: str
    role: str = "normal"

    def draw(self, win: Any, row: int, col: int, width: int, palette: Palette) -> None:
        safe_add(win, row, col, self.text, palette.attr(self.role))


@dataclass
class Badge:
    """Colored status token; the words themselves are the text form."""

    text: str
    role: str = "accent"

    def draw(self, win: Any, row: int, col: int, palette: Palette) -> int:
        safe_add(win, row, col, self.text, palette.attr(self.role))
        return len(self.text)


class KeyBar:
    """Footer hint bar: ``Enter launch · E edit · Esc cancel`` with accented keys.

    Draws at ``col 2``; when the bindings do not fit one row the bar wraps
    upward (using the row above the given one) instead of clipping keys —
    the exit binding is always fully visible.
    """

    def __init__(self, bindings: Sequence[tuple[str, str]]):
        self.bindings = list(bindings)

    def text(self) -> str:
        return " · ".join(f"{key} {label}" for key, label in self.bindings)

    def _rows_needed(self, width: int) -> int:
        col = 2
        rows = 1
        for index, (key, label) in enumerate(self.bindings):
            entry = len(key) + 1 + len(label) + (3 if index else 0)
            if col + entry > width - 2 and index:
                rows += 1
                col = 2 + len(key) + 1 + len(label)
                continue
            col += entry
        return rows

    def rows(self, width: int) -> int:
        """Rows the bar will occupy when drawn (capped at 2, wrapping upward)."""

        return min(self._rows_needed(width), 2)

    def draw(self, win: Any, row: int, palette: Palette, col: int = 2) -> None:
        height, width = win.getmaxyx()
        used = self.rows(width)
        if self._rows_needed(width) > used:
            # Overflow beyond two rows: the D30 contract is that the exit
            # binding is never clipped; the help binding (? — the discovery
            # mechanism) shares the guaranteed bottom-row spot when the
            # screen put it immediately before the exit. Middle bindings
            # compact behind an ellipsis.
            origin = max(0, row - (used - 1))
            x = col
            index = 0
            protected = (
                self.bindings[-2:]
                if len(self.bindings) >= 2 and self.bindings[-2][0] == "?"
                else self.bindings[-1:]
            )
            body = self.bindings[: -len(protected)]
            while index < len(body):
                key, label = body[index]
                entry = len(key) + 1 + len(label) + (3 if index else 0)
                if index and x + entry > width - 2:
                    break
                if index:
                    safe_add(win, origin, x, " · ", palette.attr("dim"))
                    x += 3
                safe_add(win, origin, x, key, palette.attr("accent"))
                x += len(key)
                safe_add(win, origin, x, f" {label}", palette.attr("normal"))
                x += 1 + len(label)
                index += 1
            tail = "… · " if index < len(body) else ""
            safe_add(win, origin + 1, col, tail, palette.attr("dim"))
            x = col + len(tail)
            for offset, (key, label) in enumerate(protected):
                if offset:
                    safe_add(win, origin + 1, x, " · ", palette.attr("dim"))
                    x += 3
                safe_add(win, origin + 1, x, key, palette.attr("accent"))
                x += len(key)
                safe_add(win, origin + 1, x, f" {label}", palette.attr("normal"))
                x += 1 + len(label)
            return
        origin = max(0, row - (used - 1))
        current = origin
        for index, (key, label) in enumerate(self.bindings):
            entry = len(key) + 1 + len(label) + (3 if index else 0)
            if index and col + entry > width - 2 and current < origin + used - 1:
                current += 1
                col = 2
            elif index:
                safe_add(win, current, col, " · ", palette.attr("dim"))
                col += 3
            safe_add(win, current, col, key, palette.attr("accent"))
            col += len(key)
            safe_add(win, current, col, f" {label}", palette.attr("normal"))
            col += 1 + len(label)
            if col >= width - 2 and current >= row:
                break


@dataclass
class Checkbox:
    """Single checkbox: ``[x] label``; radio flavor renders ``(•)``/``( )``."""

    label: str
    checked: bool = False
    enabled: bool = True
    radio: bool = False
    note: str = ""

    def marker(self) -> str:
        if self.radio:
            return "(•)" if self.checked else "( )"
        return "[x]" if self.checked else "[ ]"

    def draw(
        self,
        win: Any,
        row: int,
        col: int,
        palette: Palette,
        *,
        focused: bool = False,
        option_cursor: bool = False,
    ) -> int:
        attr = palette.attr("normal")
        if not self.enabled:
            attr = palette.attr("dim")
        if focused or option_cursor:
            attr |= curses.A_REVERSE
        safe_add(win, row, col, self.marker(), palette.attr("accent") | (curses.A_REVERSE if option_cursor else 0))
        safe_add(win, row, col + 4, self.label, attr)
        used = 4 + len(self.label)
        if self.note:
            safe_add(win, row, col + used, f" {self.note}", palette.attr("dim"))
            used += 1 + len(self.note)
        return used


class CheckboxGroup:
    """A focusable group of checkboxes; radio groups keep exactly one checked."""

    def __init__(self, boxes: Sequence[Checkbox], *, horizontal: bool = False):
        self.boxes = list(boxes)
        self.horizontal = horizontal
        self.cursor = 0

    def handle(self, key: Key) -> bool:
        """Move within the group / activate; True when the key was consumed."""

        move_keys = ("left", "right") if self.horizontal else ("up", "down")
        if key.kind == move_keys[0]:
            self.cursor = (self.cursor - 1) % max(1, len(self.boxes))
            return True
        if key.kind == move_keys[1]:
            self.cursor = (self.cursor + 1) % max(1, len(self.boxes))
            return True
        if key.kind in ("char",) and key.ch == " " or key.kind == "enter":
            self.activate(self.cursor)
            return True
        return False

    def activate(self, index: int) -> None:
        if not 0 <= index < len(self.boxes):
            return
        box = self.boxes[index]
        if not box.enabled:
            return
        if box.radio:
            for other in self.boxes:
                other.checked = False
            box.checked = True
        else:
            box.checked = not box.checked

    def checked_indexes(self) -> list[int]:
        return [index for index, box in enumerate(self.boxes) if box.checked]


class TextInput:
    """Single-line edit input with a real cursor.

    Handles printable insertion plus left/right/home/end/backspace/delete;
    navigation keys (up/down/tab/enter/esc) are returned unconsumed so the
    parent form can move focus.
    """

    def __init__(
        self,
        value: str = "",
        *,
        max_length: int | None = None,
        mask: str | None = None,
    ):
        self.value = value
        self.cursor = len(value)
        self.max_length = max_length
        # Render-only echo replacement for secret entry (018): the value is
        # drawn as mask glyphs and never reaches a rendered frame.
        self.mask = mask
        self.offset = 0  # horizontal scroll: first visible character index

    def handle(self, key: Key) -> bool:
        if key.kind == "char" and key.ch:
            if key.ch == " " or key.ch.isprintable():
                if self.max_length is None or len(self.value) < self.max_length:
                    self.value = (
                        self.value[: self.cursor] + key.ch + self.value[self.cursor :]
                    )
                    self.cursor += len(key.ch)
                return True
            return False
        if key.kind == "left":
            self.cursor = max(0, self.cursor - 1)
            return True
        if key.kind == "right":
            self.cursor = min(len(self.value), self.cursor + 1)
            return True
        if key.kind == "home":
            self.cursor = 0
            return True
        if key.kind == "end":
            self.cursor = len(self.value)
            return True
        if key.kind == "backspace":
            if self.cursor > 0:
                self.value = self.value[: self.cursor - 1] + self.value[self.cursor :]
                self.cursor -= 1
            return True
        if key.kind == "delete":
            if self.cursor < len(self.value):
                self.value = self.value[: self.cursor] + self.value[self.cursor + 1 :]
            return True
        return False

    def _visible(self, inner_width: int) -> str:
        """Visible slice with the cursor kept in view; returns the slice."""

        if inner_width <= 0:
            return ""
        if self.cursor < self.offset:
            self.offset = self.cursor
        if self.cursor > self.offset + inner_width:
            self.offset = self.cursor - inner_width
        self.offset = max(0, min(self.offset, max(0, len(self.value) - inner_width)))
        return self.value[self.offset : self.offset + inner_width]

    def draw(
        self,
        win: Any,
        row: int,
        col: int,
        width: int,
        palette: Palette,
        *,
        focused: bool = False,
    ) -> tuple[int, int]:
        """Draw ``[value]`` in ``width`` columns; returns the cursor (y, x)."""

        inner = max(1, width - 2)
        visible = self._visible(inner)
        if self.mask is not None:
            visible = self.mask * len(visible)
        attr = palette.attr("normal") | (curses.A_REVERSE if focused else 0)
        safe_add(win, row, col, "[", palette.attr("dim"))
        safe_add(win, row, col + 1, visible.ljust(inner), attr)
        safe_add(win, row, col + 1 + inner, "]", palette.attr("dim"))
        # At end-of-input with a full field, cursor - offset == inner would
        # place the cursor on the closing bracket; keep it on the last field
        # cell (the next typed character shifts into view there).
        cursor_x = col + 1 + min(self.cursor - self.offset, inner - 1)
        return row, cursor_x


@dataclass
class SelectItem:
    label: str
    checked: bool | None = None  # None: plain row; bool: multi-select row
    enabled: bool = True
    note: str = ""


class SelectList:
    """Scrollable option list; single-select or multi (checkbox rows).

    ``on_activate(index)`` (single mode, Enter) may return an error string to
    stay open; ``on_toggle``/``on_prefer`` (multi mode) likewise.  ``refresh``
    rebuilds the item list after every mutation so checked markers track
    state.  Returns the activated index (single mode) or None.
    """

    def __init__(
        self,
        title: str,
        items: Sequence[SelectItem],
        *,
        footer: Sequence[tuple[str, str]] = (("Enter", "choose"), ("Esc", "back")),
        multi: bool = False,
        selected: int = 0,
    ):
        self.title = title
        self.items = list(items)
        self.footer = KeyBar(footer)
        self.multi = multi
        self.selected = selected
        self.message = ""

    def draw(self, win: Any, palette: Palette) -> None:
        win.erase()
        height, width = win.getmaxyx()
        safe_add(win, 0, 0, self.title, palette.attr("accent") | curses.A_BOLD)
        visible = max(1, height - 5)
        self.selected = min(max(self.selected, 0), max(0, len(self.items) - 1))
        start = max(
            0, min(self.selected - visible + 1, max(0, len(self.items) - visible))
        )
        for screen_row, index in enumerate(
            range(start, min(len(self.items), start + visible)), 2
        ):
            item = self.items[index]
            focused = index == self.selected
            attr = palette.attr("normal") | (curses.A_REVERSE if focused else 0)
            if not item.enabled:
                attr = palette.attr("dim") | (curses.A_REVERSE if focused else 0)
            marker = ""
            if item.checked is not None:
                marker = "[x] " if item.checked else "[ ] "
            cursor = ">" if focused else " "
            safe_add(win, screen_row, 0, f"{cursor} {marker}{item.label}", attr)
            if item.note:
                safe_add(
                    win,
                    screen_row,
                    2 + len(marker) + len(item.label),
                    f"  {item.note}",
                    palette.attr("dim") | (curses.A_REVERSE if focused else 0),
                )
        bar_rows = self.footer.rows(width)
        if self.message:
            safe_add(win, height - 1 - bar_rows, 0, self.message, palette.attr("warn"))
        self.footer.draw(win, height - 1, palette)
        win.refresh()

    def run(
        self,
        win: Any,
        palette: Palette,
        *,
        on_activate: Callable[[int], str | None] | None = None,
        on_toggle: Callable[[int], str | None] | None = None,
        on_prefer: Callable[[int], str | None] | None = None,
        on_help: Callable[[], None] | None = None,
        refresh: Callable[[], Sequence[SelectItem]] | None = None,
    ) -> int | None:
        while True:
            if refresh is not None:
                self.items = list(refresh())
            self.draw(win, palette)
            key = read_key(win)
            self.message = ""
            if key.kind == "resize":
                continue
            if key.kind == "up" or (key.kind == "char" and key.ch == "k"):
                self.selected = (self.selected - 1) % max(1, len(self.items))
            elif key.kind == "down" or (key.kind == "char" and key.ch == "j"):
                self.selected = (self.selected + 1) % max(1, len(self.items))
            elif key.kind == "home":
                self.selected = 0
            elif key.kind == "end":
                self.selected = max(0, len(self.items) - 1)
            elif key.kind == "esc" or (key.kind == "char" and key.ch == "q"):
                return None
            elif key.kind == "ctrl" and key.ch == "c":
                raise KeyboardInterrupt
            elif key.kind == "enter":
                if self.multi:
                    return None
                if not self.items:
                    return None
                if on_activate is not None:
                    error = on_activate(self.selected)
                    if error:
                        self.message = error
                        continue
                return self.selected
            elif key.kind == "char" and key.ch == " " and self.multi:
                if on_toggle is not None and self.items:
                    error = on_toggle(self.selected)
                    if error:
                        self.message = error
            elif key.kind == "char" and key.ch in ("p", "P") and self.multi:
                if on_prefer is not None and self.items:
                    error = on_prefer(self.selected)
                    if error:
                        self.message = error
            elif key.kind == "char" and key.ch == "?" and on_help is not None:
                on_help()


class Modal:
    """Centered dialog with focused buttons; Esc cancels (returns None).

    With ``input`` set, a TextInput sits above the buttons; Tab (and
    shift-Tab) cycles focus between the input and the buttons, and the
    caller reads ``modal.input.value`` after a confirming button returns.
    """

    def __init__(
        self,
        title: str,
        lines: Sequence[str] = (),
        *,
        buttons: Sequence[tuple[str, Any]] = (("OK", True), ("Cancel", False)),
        input: TextInput | None = None,
    ):
        self.title = title
        self.lines = list(lines)
        self.buttons = list(buttons)
        self.input = input
        self.focus = 0 if input is not None else 1  # 0 input, 1..n buttons

    def _geometry(self, win: Any) -> tuple[int, int, int, int]:
        height, width = win.getmaxyx()
        buttons_width = sum(len(label) + 4 for label, _ in self.buttons) + 2
        content = max(
            [len(self.title), buttons_width]
            + [len(line) for line in self.lines]
            + ([30] if self.input is not None else [])
        )
        box_w = min(width - 2, content + 6)
        body = len(self.lines) + (2 if self.input is not None else 0) + 2
        box_h = min(height, body + 4)
        top = max(0, (height - box_h) // 2)
        left = max(0, (width - box_w) // 2)
        return top, left, box_h, box_w

    def draw(self, win: Any, palette: Palette) -> None:
        top, left, box_h, box_w = self._geometry(win)
        # Paint the full interior first: underlying screen text must never
        # bleed through the dialog.
        blank = " " * (box_w - 2)
        for row in range(top + 1, top + box_h - 1):
            safe_add(win, row, left + 1, blank)
        horizontal = "+" + "-" * (box_w - 2) + "+"
        safe_add(win, top, left, horizontal, palette.attr("accent"))
        for row in range(top + 1, top + box_h - 1):
            safe_add(win, row, left, "|", palette.attr("accent"))
            safe_add(win, row, left + box_w - 1, "|", palette.attr("accent"))
        safe_add(win, top + box_h - 1, left, horizontal, palette.attr("accent"))
        safe_add(win, top + 1, left + 2, self.title, palette.attr("accent") | curses.A_BOLD)
        row = top + 2
        # Clamp the body slice: below box_h 5 the naive slice goes negative
        # and would index from the list's END, showing the wrong lines on
        # tiny terminals (review H14).
        for line in self.lines[: max(0, box_h - 5)]:
            safe_add(win, row, left + 2, line)
            row += 1
        if self.input is not None:
            cursor = self.input.draw(
                win, row, left + 2, box_w - 4, palette, focused=self.focus == 0
            )
            if self.focus == 0:
                try:
                    win.move(*cursor)
                except curses.error:
                    pass
            row += 2
        col = left + 2
        for index, (label, _value) in enumerate(self.buttons):
            focused = self.focus == index + 1
            attr = palette.attr("accent") | (curses.A_REVERSE if focused else 0)
            safe_add(win, top + box_h - 2, col, f"[ {label} ]", attr)
            col += len(label) + 5
        win.refresh()

    def run(
        self,
        win: Any,
        palette: Palette,
        background: Callable[[Any], None] | None = None,
    ) -> Any:
        while True:
            self.draw(win, palette)
            key = read_key(win)
            if key.kind == "resize":
                # The old frame must not linger at its previous geometry, but
                # the screen behind should not stay blank either: repaint the
                # parent first (when given), then redraw the dialog fresh.
                if background is not None:
                    background(win)
                else:
                    win.erase()
                continue
            if key.kind == "esc":
                return None
            if key.kind == "ctrl" and key.ch == "c":
                raise KeyboardInterrupt
            zones = len(self.buttons) + (1 if self.input is not None else 0)
            if key.kind == "tab":
                self.focus = (self.focus + 1) % zones
                continue
            if key.kind == "btab":
                self.focus = (self.focus - 1) % zones
                continue
            if self.focus == 0 and self.input is not None:
                if key.kind == "enter" or key.kind == "down":
                    self.focus = 1
                    continue
                self.input.handle(key)
                continue
            if key.kind == "left":
                self.focus = 1 + (self.focus - 2) % len(self.buttons)
                continue
            if key.kind == "right":
                self.focus = 1 + self.focus % len(self.buttons)
                continue
            if key.kind == "enter":
                index = self.focus - 1
                if 0 <= index < len(self.buttons):
                    return self.buttons[index][1]


class Table:
    """Selectable row table with aligned columns."""

    def __init__(
        self,
        columns: Sequence[str],
        rows: Sequence[Sequence[str]],
        *,
        selected: int = 0,
        max_col_width: int = 36,
        min_widths: Sequence[int] | None = None,
    ):
        self.columns = list(columns)
        self.rows = [list(row) for row in rows]
        self.selected = selected
        self.max_col_width = max_col_width
        self.min_widths = (
            list(min_widths) if min_widths is not None else [8] * len(self.columns)
        )

    def _widths(self, total: int) -> list[int]:
        natural = []
        for index, column in enumerate(self.columns):
            cells = [len(row[index]) for row in self.rows if index < len(row)]
            natural.append(min(self.max_col_width, max([len(column), *cells, 1])))
        over = sum(natural) + 3 * (len(natural) - 1) - total
        while over > 0:
            candidates = [
                i for i in range(len(natural)) if natural[i] > self.min_widths[i]
            ]
            if not candidates:
                break
            widest = max(candidates, key=lambda i: natural[i])
            natural[widest] -= 1
            over -= 1
        return natural

    def draw(
        self,
        win: Any,
        row: int,
        col: int,
        width: int,
        palette: Palette,
        *,
        max_rows: int | None = None,
    ) -> int:
        widths = self._widths(width - col)
        header = "   ".join(
            column.ljust(widths[i])[: widths[i]] for i, column in enumerate(self.columns)
        )
        safe_add(win, row, col, header, palette.attr("dim") | curses.A_BOLD)
        shown = self.rows if max_rows is None else self.rows[:max_rows]
        for offset, cells in enumerate(shown, 1):
            line = "   ".join(
                (cells[i] if i < len(cells) else "").ljust(widths[i])[: widths[i]]
                for i in range(len(self.columns))
            )
            focused = offset - 1 == self.selected
            attr = palette.attr("normal") | (curses.A_REVERSE if focused else 0)
            safe_add(win, row + offset, col, line, attr)
        return 1 + len(shown)

    def handle(self, key: Key) -> bool:
        if not self.rows:
            return False
        if key.kind == "up" or (key.kind == "char" and key.ch == "k"):
            self.selected = (self.selected - 1) % len(self.rows)
            return True
        if key.kind == "down" or (key.kind == "char" and key.ch == "j"):
            self.selected = (self.selected + 1) % len(self.rows)
            return True
        if key.kind == "home":
            self.selected = 0
            return True
        if key.kind == "end":
            self.selected = len(self.rows) - 1
            return True
        return False


# ---------------------------------------------------------------------------
# Curses entry discipline: fds pointed at the caller's streams, always restored.


def _fileno(stream: Any) -> int | None:
    try:
        return stream.fileno()
    except (AttributeError, OSError, ValueError):
        return None


def run_curses_on_streams(
    app: Callable[[Any], Any],
    input_stream: Any,
    output_stream: Any,
    *,
    palette: Palette | None = None,
) -> Any:
    """Run ``app(stdscr)`` on the terminal behind the given streams.

    curses always initializes on fds 0/1, so when the caller's streams are
    other tty handles (typically /dev/tty) they are dup2'ed onto 0/1 for the
    duration and restored afterwards, unconditionally.  ``curses.wrapper``
    performs its own terminal-mode restore; KeyboardInterrupt propagates.
    """

    in_fd = _fileno(input_stream)
    out_fd = _fileno(output_stream)
    if in_fd is None or out_fd is None:
        raise OSError("curses requires streams backed by real file descriptors")
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.flush()
        except (AttributeError, OSError, ValueError):
            pass
    saved: list[tuple[int, int]] = []
    try:
        if in_fd != 0:
            saved.append((0, os.dup(0)))
            os.dup2(in_fd, 0)
        if out_fd != 1:
            saved.append((1, os.dup(1)))
            os.dup2(out_fd, 1)

        def _wrapped(stdscr: Any) -> Any:
            if palette is not None:
                init_curses_colors(palette)
            stdscr.keypad(True)
            return app(stdscr)

        return curses.wrapper(_wrapped)
    finally:
        for fd, duplicate in saved:
            os.dup2(duplicate, fd)
            os.close(duplicate)


@contextlib.contextmanager
def suspended_curses(win: Any) -> Iterator[None]:
    """Suspend curses around an external program ($EDITOR) and restore.

    Degrades to a plain output region when curses is not active (tests,
    line mode): the terminal state is managed only when there is a curses
    session to manage.
    """

    try:
        curses.def_prog_mode()
        curses.endwin()
    except curses.error:
        yield
        return
    try:
        yield
    finally:
        curses.reset_prog_mode()
        try:
            win.refresh()
        except curses.error:
            pass


def hide_cursor() -> None:
    try:
        curses.curs_set(0)
    except curses.error:
        pass


def show_cursor() -> None:
    try:
        curses.curs_set(1)
    except curses.error:
        pass


# ---------------------------------------------------------------------------
# Composition editor state (pure; moved verbatim from editor.py).


class EditorError(ValueError):
    """A requested edit would violate an explicit editor invariant."""


@dataclass(frozen=True)
class Invalidation:
    kind: str
    label: str
    role: str
    model: str


@dataclass(frozen=True)
class AvailabilityChange:
    level: str  # provider | model
    key: str
    old_scope: str
    new_scope: str
    invalidated: tuple[Invalidation, ...]
    resulting_errors: tuple[str, ...]


@dataclass(frozen=True)
class EditorOutcome:
    """Requested side effect; interpreted by the CLI after the editor exits."""

    action: str
    document: dict[str, Any]
    target: str | None = None


SCOPES = catalog_mod.SCOPES
_SCOPE_USES = {
    "lead+agents": frozenset(("lead", "agents")),
    "lead": frozenset(("lead",)),
    "agents": frozenset(("agents",)),
    "off": frozenset(),
}


def _role_label(role_id: str) -> str:
    return role_id.removeprefix("cm-").replace("-", " ").title()


@dataclass
class EditorState:
    """Pure, revisitable composition editor state."""

    docs: dict[str, Any]
    document: dict[str, Any]
    seed_document: dict[str, Any]
    original_document: dict[str, Any] | None = None
    message: str = ""

    def __post_init__(self) -> None:
        self.document = copy.deepcopy(self.document)
        self.seed_document = copy.deepcopy(self.seed_document)
        if self.original_document is None:
            self.original_document = copy.deepcopy(self.document)
        else:
            self.original_document = copy.deepcopy(self.original_document)

    @property
    def dirty(self) -> bool:
        return self.document != self.original_document

    @property
    def name(self) -> str:
        return self.document["name"]

    @property
    def models(self) -> dict[str, Any]:
        return self.docs["models"]["models"]

    @property
    def providers(self) -> dict[str, Any]:
        return self.docs["providers"]["providers"]

    @property
    def roles(self) -> dict[str, Any]:
        return self.docs["roles"]["roles"]

    def validation_errors(self) -> list[str]:
        return catalog_mod.validate_composition(
            self.document, self.models, self.roles, self.providers
        )

    def lead_slot(self) -> dict[str, Any] | None:
        for slot in self.document["slots"]:
            if slot["role"] == catalog_mod.LEAD_ROLE:
                return slot
        return None

    def lead_summary(self) -> str:
        slot = self.lead_slot()
        if slot is None:
            return "none selected"
        model = self.models.get(slot.get("model"))
        return model["display"] if model is not None else "unknown model"

    def set_lead(self, model_id: str) -> None:
        model = self.models.get(model_id)
        if model is None:
            raise EditorError(f"unknown model {model_id!r}")
        if "lead" not in model["capabilities"] or model["lead"] is None:
            raise EditorError(f"{model['display']} cannot be used as the lead")
        if "lead" not in self.effective_uses(model_id):
            raise EditorError(
                f"{model['display']} is not available for lead use; edit Availability first"
            )
        lead = self.lead_slot()
        if lead is None:
            # Lead is always first; all existing non-lead slot ordering remains
            # byte-for-byte stable relative to itself.
            self.document["slots"].insert(
                0, {"role": catalog_mod.LEAD_ROLE, "model": model_id}
            )
        else:
            lead["model"] = model_id

    def provider_scope(self, provider_id: str) -> str:
        return self.document["availability"]["providers"].get(provider_id, "off")

    def model_scope(self, model_id: str) -> str:
        return self.document["availability"]["models"].get(model_id, "off")

    def model_is_new(self, model_id: str) -> bool:
        return model_id not in self.document["availability"]["models"]

    def effective_uses(self, model_id: str) -> frozenset[str]:
        model = self.models[model_id]
        provider_uses = _SCOPE_USES[self.provider_scope(model["provider"])]
        model_uses = _SCOPE_USES[self.model_scope(model_id)]
        return provider_uses & model_uses & frozenset(model["capabilities"])

    def _preview_document(self, level: str, key: str, scope: str) -> dict[str, Any]:
        if scope not in SCOPES:
            raise EditorError(f"unknown availability scope {scope!r}")
        if level == "provider" and key not in self.providers:
            raise EditorError(f"unknown provider {key!r}")
        if level == "model" and key not in self.models:
            raise EditorError(f"unknown model {key!r}")
        candidate = copy.deepcopy(self.document)
        plural = "providers" if level == "provider" else "models"
        candidate["availability"][plural][key] = scope
        return candidate

    def preview_availability(
        self, level: str, key: str, new_scope: str
    ) -> AvailabilityChange:
        candidate = self._preview_document(level, key, new_scope)
        plural = "providers" if level == "provider" else "models"
        old_scope = self.document["availability"][plural].get(key, "off")
        provider_scopes = candidate["availability"]["providers"]
        model_scopes = candidate["availability"]["models"]
        invalidated: list[Invalidation] = []
        for slot in candidate["slots"]:
            model_id = slot["model"]
            model = self.models.get(model_id)
            if model is None:
                continue
            allowed = (
                _SCOPE_USES[provider_scopes.get(model["provider"], "off")]
                & _SCOPE_USES[model_scopes.get(model_id, "off")]
                & frozenset(model["capabilities"])
            )
            use = "lead" if slot["role"] == catalog_mod.LEAD_ROLE else "agents"
            if use in allowed:
                continue
            lane = slot.get("lane", model["default_lane"])
            if use == "lead":
                label = f"Lead · {model['display']}"
            else:
                label = f"{_role_label(slot['role'])} · {model['display']} · {lane}"
            invalidated.append(Invalidation(use, label, slot["role"], model_id))
        errors = catalog_mod.validate_composition(
            candidate, self.models, self.roles, self.providers
        )
        return AvailabilityChange(
            level=level,
            key=key,
            old_scope=old_scope,
            new_scope=new_scope,
            invalidated=tuple(invalidated),
            resulting_errors=tuple(errors),
        )

    def apply_availability(
        self, change: AvailabilityChange, *, confirm: bool = False
    ) -> None:
        if change.invalidated and not confirm:
            labels = ", ".join(item.label for item in change.invalidated)
            raise EditorError(
                f"availability change invalidates {labels}; explicit confirmation required"
            )
        candidate = self._preview_document(change.level, change.key, change.new_scope)
        self.document = candidate

    def variants_for_role(self, role_id: str) -> list[dict[str, Any]]:
        return [slot for slot in self.document["slots"] if slot["role"] == role_id]

    def compatible_variants(self, role_id: str) -> list[tuple[str, str]]:
        variants: list[tuple[str, str]] = []
        for model_id, model in sorted(self.models.items()):
            if role_id not in model["compatible_roles"] or role_id == catalog_mod.LEAD_ROLE:
                continue
            for lane in sorted(model["lanes"]):
                variants.append((model_id, lane))
        return variants

    def has_variant(self, role_id: str, model_id: str, lane: str) -> bool:
        return any(
            slot["role"] == role_id
            and slot["model"] == model_id
            and slot.get("lane", self.models[model_id]["default_lane"]) == lane
            for slot in self.document["slots"]
        )

    def toggle_variant(
        self,
        role_id: str,
        model_id: str,
        lane: str,
        *,
        replacement_preferred: tuple[str, str] | None = None,
    ) -> None:
        if role_id == catalog_mod.LEAD_ROLE or role_id not in self.roles:
            raise EditorError(f"invalid variant role {role_id!r}")
        model = self.models.get(model_id)
        if model is None or role_id not in model["compatible_roles"]:
            raise EditorError(f"model {model_id!r} is not compatible with {role_id!r}")
        if lane not in model["lanes"]:
            raise EditorError(f"model {model_id!r} has no lane {lane!r}")

        slots = self.document["slots"]
        found_index: int | None = None
        for index, slot in enumerate(slots):
            if (
                slot["role"] == role_id
                and slot["model"] == model_id
                and slot.get("lane", model["default_lane"]) == lane
            ):
                found_index = index
                break
        if found_index is None:
            if "agents" not in self.effective_uses(model_id):
                raise EditorError(
                    f"{model['display']} is not available for agent use; edit Availability first"
                )
            existing = self.variants_for_role(role_id)
            slots.append(
                {
                    "role": role_id,
                    "model": model_id,
                    "lane": lane,
                    "preferred": not existing,
                }
            )
            return

        removed = slots[found_index]
        remaining = [
            slot
            for index, slot in enumerate(slots)
            if index != found_index and slot["role"] == role_id
        ]
        if removed.get("preferred") and remaining:
            if replacement_preferred is None:
                raise EditorError(
                    "the preferred variant cannot be removed until another variant is preferred"
                )
            if not any(
                slot["model"] == replacement_preferred[0]
                and slot.get("lane", self.models[slot["model"]]["default_lane"])
                == replacement_preferred[1]
                for slot in remaining
            ):
                raise EditorError("replacement preferred variant is not selected")
            for slot in remaining:
                slot["preferred"] = (
                    slot["model"],
                    slot.get("lane", self.models[slot["model"]]["default_lane"]),
                ) == replacement_preferred
        del slots[found_index]

    def set_preferred(self, role_id: str, model_id: str, lane: str) -> None:
        variants = self.variants_for_role(role_id)
        target_found = False
        for slot in variants:
            slot_lane = slot.get("lane", self.models[slot["model"]]["default_lane"])
            selected = slot["model"] == model_id and slot_lane == lane
            slot["preferred"] = selected
            target_found |= selected
        if not target_found:
            raise EditorError("preferred variant must already be selected")

    def set_native(self, key: str, value: str) -> None:
        allowed = {
            "explore": ("replace", "native", "off"),
            "plan": ("native", "off"),
            "general_purpose": ("on", "off"),
        }
        if key not in allowed or value not in allowed[key]:
            raise EditorError(f"invalid native-agent setting {key}={value!r}")
        self.document["native_agents"][key] = value

    @property
    def workflows(self) -> str:
        """Composition workflow mode; ``native`` when the key is absent."""

        return self.document.get("workflows", "native")

    def set_workflows(self, value: str) -> None:
        """Toggle the composition workflow mode (``native`` | ``off``).

        ``native`` is the resolution default, so it is stored by omitting the
        key: a native-mode composition stays byte-identical to the
        pre-workflows form.
        """

        if value not in WORKFLOWS_VOCABULARY:
            raise EditorError(f"invalid workflows mode {value!r}")
        if value == "native":
            self.document.pop("workflows", None)
        else:
            self.document["workflows"] = value

    def restore_default(self) -> None:
        self.document = copy.deepcopy(self.seed_document)
        self.message = "Trusted default restored in the editor; save to persist it."

    def outcome(self, action: str, target: str | None = None) -> EditorOutcome:
        if action not in {
            "save",
            "update",
            "save-as",
            "launch-once",
            "duplicate",
            "rename",
            "delete",
            "restore-default",
            "use-as-template",
        }:
            raise EditorError(f"unknown editor action {action!r}")
        if target is not None:
            from .state import check_name

            check_name(target)
        return EditorOutcome(action, copy.deepcopy(self.document), target)


# ---------------------------------------------------------------------------
# The form-based composition editor screen.

FORM_TOO_SMALL = "Terminal too small for the composition editor."
FORM_TOO_SMALL_HINT = "Resize to at least 60x20, or press Esc to cancel."
FORM_KEYBAR = (
    ("↑↓", "move"),
    ("Enter", "edit"),
    ("Space", "toggle"),
    ("^G", "JSON editor"),
    ("^O", "save"),
    ("?", "help"),
    ("Esc", "cancel"),
)
FORM_DISCARD_TITLE = "Discard unsaved changes?"
FORM_DISCARD_BODY = "The composition was modified; unsaved edits will be lost."
FORM_UPDATE_RENAMED = (
    "the name was changed; use Save as new composition or Rename instead"
)

_ACTIONS = (
    ("update", "Update current composition"),
    ("save-as", "Save as new composition"),
    ("launch-once", "Launch once without saving"),
    ("duplicate", "Duplicate"),
    ("rename", "Rename"),
    ("delete", "Delete"),
    ("restore-default", "Restore trusted default"),
    ("use-as-template", "Use as template"),
)
_TARGET_ACTIONS = frozenset(("save-as", "duplicate", "rename", "use-as-template"))
_DESTRUCTIVE_ACTIONS = frozenset(("delete", "restore-default"))
_NATIVE_RADIO = {
    "explore": ("replace", "native", "off"),
    "plan": ("native", "off"),
}


@dataclass
class _Row:
    kind: str  # section|text|lead|avail|role|radio|check|actions
    label: str = ""
    payload: Any = None
    note: str = ""


EDITOR_HELP = (
    "↑↓ — move between fields.\n"
    "Enter — edit the focused field (text input, select list, or actions).\n"
    "Space — toggle checkboxes and multi-select variants.\n"
    "P — prefer a variant (roles).\n"
    "←→ — move between options on a radio row (native-agent policy).\n"
    "^G — open the raw composition JSON in the JSON editor.\n"
    "^O — open the Save or launch menu from anywhere (nano's WriteOut chord).\n"
    "Esc — back out; asks before discarding unsaved edits.\n"
    "? — this help, then the workflow guarantees below.\n"
    "\n"
    "Key conventions shared with every screen: Esc backs out, Enter is the\n"
    "primary action, ? opens help. Text fields own printable keys, so this\n"
    "screen uses arrows instead of j/k and Esc instead of q.\n"
    "\n"
    "-- workflow guarantees --------------------------------------------"
)


class FormEditorScreen:
    """The single form-based composition editor (runs on a curses window).

    Sections are labels; every editable value is a real widget: name and
    description are TextInputs, the lead model and availability scopes open
    SelectLists, each role's model/lane variants open a multi SelectList,
    the native-agent policy is a checkbox group (radio rows + single
    checkboxes), and save/launch actions open a SelectList with Modal
    confirmations.  Ctrl+G hands the raw composition JSON to ``$EDITOR``.
    """

    def __init__(
        self,
        state: EditorState,
        *,
        palette: Palette | None = None,
        name_taken: Callable[[str], bool] | None = None,
        open_in_editor: Callable[[str], int] | None = None,
        suspender: Callable[[Any], contextlib.AbstractContextManager[Any]] | None = None,
        environ: Mapping[str, str] | None = None,
    ):
        self.state = state
        self.palette = palette or MONO_PALETTE
        self.name_taken = name_taken
        self.open_in_editor = open_in_editor
        self.suspender = suspender
        self.environ = dict(os.environ if environ is None else environ)
        self.name_input = TextInput(state.document.get("name", ""), max_length=64)
        self.desc_input = TextInput(state.document.get("description", ""))
        self.focus = 0
        self.scroll = 0
        self.radio_cursor: dict[str, int] = {}
        self._rows: list[_Row] = []

    # -- row model ---------------------------------------------------------

    def _build_rows(self) -> list[_Row]:
        state = self.state
        rows: list[_Row] = [_Row("section", "General")]
        rows.append(_Row("text", "Name", self.name_input))
        rows.append(_Row("text", "Description", self.desc_input))
        rows.append(_Row("section", "Lead"))
        rows.append(_Row("lead", "Model", note=state.lead_summary()))
        rows.append(_Row("section", "Availability"))
        for provider_id, provider in sorted(state.providers.items()):
            scope = state.provider_scope(provider_id)
            rows.append(
                _Row("avail", provider["display"], ("provider", provider_id), scope)
            )
            for model_id, model in sorted(state.models.items()):
                if model["provider"] != provider_id:
                    continue
                note = state.model_scope(model_id)
                if state.model_is_new(model_id):
                    note += " · New · Off"
                rows.append(
                    _Row("avail", "  " + model["display"], ("model", model_id), note)
                )
        rows.append(_Row("section", "Roles"))
        for role_id in sorted(state.roles):
            if role_id == catalog_mod.LEAD_ROLE:
                continue
            variants = state.variants_for_role(role_id)
            preferred = next((s for s in variants if s.get("preferred")), None)
            preferred_text = (
                state.models[preferred["model"]]["display"] if preferred else "none"
            )
            rows.append(
                _Row(
                    "role",
                    _role_label(role_id),
                    role_id,
                    f"{len(variants)} variants · preferred {preferred_text}",
                )
            )
        rows.append(_Row("section", "Native agents & workflows"))
        for key, values in _NATIVE_RADIO.items():
            rows.append(_Row("radio", key.replace("_", " ").title(), (key, values)))
        rows.append(
            _Row(
                "check",
                "general-purpose agent",
                "general_purpose",
            )
        )
        rows.append(
            _Row(
                "check",
                "native workflows (ultracode)",
                "workflows",
            )
        )
        rows.append(_Row("section", "Actions"))
        rows.append(_Row("actions", "Save or launch", note="— update · save as · launch once · more…"))
        return rows

    def _focusable(self) -> list[int]:
        return [i for i, row in enumerate(self._rows) if row.kind != "section"]

    def _move_focus(self, delta: int) -> None:
        focusable = self._focusable()
        if not focusable:
            return
        current = focusable.index(self.focus) if self.focus in focusable else 0
        self.focus = focusable[(current + delta) % len(focusable)]

    # -- drawing -----------------------------------------------------------

    def _draw(self, win: Any) -> None:
        win.erase()
        palette = self.palette
        height, width = win.getmaxyx()
        marker = " · modified" if self.state.dirty else ""
        safe_add(
            win,
            1,
            2,
            f"claude-multi / Edit {self.state.name}{marker}",
            palette.attr("accent") | curses.A_BOLD,
        )
        body_top = 3
        # Status zone: the status row (height-3) plus, when the form is
        # BLOCKED with a pending message, one reserved message line above it
        # — content never collides with either (review H6-style reservation).
        pre_errors = self._validation()
        message_row = (
            height - 4 if pre_errors and self.state.message else None
        )
        body_bottom = height - 3 if message_row is None else height - 4
        visible = max(1, body_bottom - body_top)
        if self.focus < self.scroll:
            self.scroll = self.focus
        if self.focus >= self.scroll + visible:
            self.scroll = self.focus - visible + 1
        self.scroll = max(0, min(self.scroll, max(0, len(self._rows) - visible)))
        cursor_pos: tuple[int, int] | None = None
        for index in range(self.scroll, min(len(self._rows), self.scroll + visible)):
            row = self._rows[index]
            y = body_top + index - self.scroll
            focused = index == self.focus
            if row.kind == "section":
                safe_add(win, y, 2, row.label, palette.attr("accent") | curses.A_BOLD)
                continue
            prefix = "> " if focused else "  "
            attr = palette.attr("normal") | (curses.A_REVERSE if focused else 0)
            safe_add(win, y, 2, prefix, attr)
            if row.kind == "text":
                safe_add(win, y, 4, f"{row.label:<14}", attr)
                pos = row.payload.draw(
                    win, y, 18, min(48, width - 20), palette, focused=focused
                )
                if focused:
                    cursor_pos = pos
            elif row.kind == "lead":
                safe_add(win, y, 4, f"{row.label:<16}{row.note}", attr)
            elif row.kind == "avail":
                safe_add(win, y, 4, f"{row.label:<28}{row.note}", attr)
            elif row.kind == "role":
                safe_add(win, y, 4, f"{row.label:<28}{row.note}", attr)
            elif row.kind == "radio":
                key, values = row.payload
                current = self.state.document["native_agents"][key]
                safe_add(win, y, 4, f"{row.label:<14}", attr)
                col = 18
                cursor = self.radio_cursor.get(
                    key, next((i for i, v in enumerate(values) if v == current), 0)
                )
                for option_index, value in enumerate(values):
                    box = Checkbox(value, checked=value == current, radio=True)
                    col += box.draw(
                        win,
                        y,
                        col,
                        palette,
                        focused=focused,
                        option_cursor=focused and option_index == cursor,
                    )
                    col += 2
            elif row.kind == "check":
                checked = (
                    self.state.document["native_agents"]["general_purpose"] == "on"
                    if row.payload == "general_purpose"
                    else self.state.workflows == "native"
                )
                box = Checkbox(row.label, checked=checked)
                box.draw(win, y, 4, palette, focused=focused)
                safe_add(win, y, 4 + 4 + len(row.label) + 1, row.note, palette.attr("dim"))
            elif row.kind == "actions":
                safe_add(win, y, 4, f"{row.label:<16}{row.note}", attr)
        errors = pre_errors
        status_row = height - 3
        if errors:
            safe_add(win, status_row, 2, "Status: BLOCKED", palette.attr("error") | curses.A_BOLD)
            safe_add(win, status_row, 19, errors[0], palette.attr("error"))
            # Refusal feedback (set_lead/variant guards) lands in
            # state.message; when BLOCKED it would be invisible exactly
            # while the user is repairing — it gets a reserved line.
            if message_row is not None:
                safe_add(win, message_row, 2, self.state.message, palette.attr("warn"))
        else:
            safe_add(win, status_row, 2, "Status: Ready", palette.attr("ok") | curses.A_BOLD)
            if self.state.message:
                safe_add(win, status_row, 19, self.state.message, palette.attr("warn"))
        KeyBar(FORM_KEYBAR).draw(win, height - 1, palette)
        if cursor_pos is not None:
            try:
                win.move(*cursor_pos)
            except curses.error:
                pass
        win.refresh()

    def _validation(self) -> list[str]:
        errors = self.state.validation_errors()
        name = self.state.document.get("name", "")
        if not name.strip():
            errors = [*errors, "name must not be empty"]
        elif (
            name != self.state.original_document.get("name")
            and self.name_taken is not None
            and self.name_taken(name)
        ):
            errors = [*errors, f"composition name {name!r} already exists"]
        return errors

    # -- sub-screens ---------------------------------------------------------

    def _open_lead(self, win: Any) -> None:
        state = self.state
        ids = [
            model_id
            for model_id, model in sorted(state.models.items())
            if "lead" in model["capabilities"] and model["lead"] is not None
        ]
        items = [
            SelectItem(
                state.models[mid]["display"],
                checked=state.lead_slot() is not None
                and state.lead_slot().get("model") == mid,
                note="available"
                if "lead" in state.effective_uses(mid)
                else "unavailable",
            )
            for mid in ids
        ]
        current = next(
            (i for i, item in enumerate(items) if item.checked), 0
        )
        chooser = SelectList(
            "Lead model",
            items,
            footer=(("Enter", "choose"), ("Esc", "back")),
            selected=current,
        )
        index = chooser.run(win, self.palette)
        if index is None:
            return
        try:
            state.set_lead(ids[index])
            state.message = "Lead updated."
        except EditorError as exc:
            state.message = str(exc)

    def _open_scope(self, win: Any, level: str, key: str, label: str) -> None:
        state = self.state
        current = (
            state.provider_scope(key) if level == "provider" else state.model_scope(key)
        )
        items = [
            SelectItem(scope, checked=scope == current) for scope in SCOPES
        ]
        chooser = SelectList(
            f"Scope for {label.strip()}",
            items,
            footer=(("Enter", "choose"), ("Esc", "back")),
            selected=SCOPES.index(current),
        )
        index = chooser.run(win, self.palette)
        if index is None:
            return
        new_scope = SCOPES[index]
        if new_scope == current:
            return
        change = state.preview_availability(level, key, new_scope)
        if change.invalidated:
            lines = [
                f"{label.strip()}: {current} -> {new_scope}",
                "",
                "Invalidated selections:",
                *(f"- {item.label}" for item in change.invalidated),
                "",
                "Selections remain present and the composition becomes BLOCKED.",
            ]
            confirmed = Modal(
                "Confirm availability change",
                lines,
                buttons=(("Apply", True), ("Cancel", False)),
            ).run(win, self.palette, background=self._draw)
            if not confirmed:
                state.message = "Availability change cancelled."
                return
        state.apply_availability(change, confirm=bool(change.invalidated))
        state.message = "Availability updated; selections were preserved."

    def _open_variants(self, win: Any, role_id: str) -> None:
        state = self.state
        variants = state.compatible_variants(role_id)

        def refresh() -> list[SelectItem]:
            items: list[SelectItem] = []
            for model_id, lane in variants:
                active = state.has_variant(role_id, model_id, lane)
                slot = next(
                    (
                        item
                        for item in state.variants_for_role(role_id)
                        if item["model"] == model_id
                        and item.get("lane", state.models[model_id]["default_lane"])
                        == lane
                    ),
                    None,
                )
                preferred = bool(slot and slot.get("preferred"))
                available = "agents" in state.effective_uses(model_id)
                hint = state.models[model_id]["role_hints"].get(
                    role_id, state.models[model_id]["routing_note"]
                )
                note = hint or ""
                if preferred:
                    note = "preferred · " + note if note else "preferred"
                if not available:
                    note = "unavailable · " + note if note else "unavailable"
                items.append(
                    SelectItem(
                        f"{state.models[model_id]['display']} · {lane}",
                        checked=active,
                        note=note,
                    )
                )
            return items

        def toggle(index: int) -> str | None:
            model_id, lane = variants[index]
            try:
                state.toggle_variant(role_id, model_id, lane)
            except EditorError as exc:
                return str(exc)
            state.message = "Variant selection updated."
            return None

        def prefer(index: int) -> str | None:
            model_id, lane = variants[index]
            try:
                state.set_preferred(role_id, model_id, lane)
            except EditorError as exc:
                return str(exc)
            state.message = "Preferred variant updated."
            return None

        chooser = SelectList(
            f"{_role_label(role_id)} variants",
            refresh(),
            footer=(
                ("Space", "toggle"),
                ("P", "prefer"),
                ("Enter", "done"),
                ("Esc", "back"),
            ),
            multi=True,
        )
        chooser.run(
            win,
            self.palette,
            on_toggle=toggle,
            on_prefer=prefer,
            refresh=refresh,
        )

    def _open_actions(self, win: Any) -> EditorOutcome | None:
        chooser = SelectList(
            "Save or launch",
            [SelectItem(label) for _, label in _ACTIONS],
            footer=(("Enter", "choose"), ("Esc", "back")),
        )
        index = chooser.run(win, self.palette)
        if index is None:
            return None
        action = _ACTIONS[index][0]
        label = _ACTIONS[index][1]
        target: str | None = None
        if action in _TARGET_ACTIONS:
            modal = Modal(
                label,
                ["Target name (letters, digits, dots, underscores, dashes):"],
                buttons=(("OK", True), ("Cancel", False)),
                input=TextInput(""),
            )
            confirmed = modal.run(win, self.palette)
            target = modal.input.value.strip()
            if not confirmed or not target:
                self.state.message = "A target name is required."
                return None
        if action in _DESTRUCTIVE_ACTIONS:
            title = (
                f"Delete {self.state.name!r}?"
                if action == "delete"
                else "Replace the editor with the trusted default?"
            )
            confirmed = Modal(
                title,
                ["This action is explicit and will not launch Claude."],
                buttons=(("Apply", True), ("Cancel", False)),
            ).run(win, self.palette, background=self._draw)
            if not confirmed:
                self.state.message = "Destructive action cancelled."
                return None
        if action == "update" and (
            self.state.document.get("name") != self.state.original_document.get("name")
        ):
            self.state.message = FORM_UPDATE_RENAMED
            return None
        try:
            return self.state.outcome(action, target)
        except (EditorError, OSError) as exc:
            self.state.message = str(exc)
            return None

    # -- $EDITOR integration (the only one) ----------------------------------

    def _default_opener(self) -> Callable[[str], int]:
        environ = self.environ

        def open(path: str) -> int:
            editor = environ.get("EDITOR") or "vi"
            return subprocess.call([*shlex.split(editor), path])

        return open

    def _edit_json(self, win: Any) -> None:
        state = self.state
        before = strict_json.pretty_file_bytes(state.document)
        fd, path = tempfile.mkstemp(prefix="claude-multi-edit-", suffix=".json")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(before)
            suspender = self.suspender or suspended_curses
            opener = self.open_in_editor or self._default_opener()
            try:
                with suspender(win):
                    status = opener(path)
            except OSError as exc:
                state.message = f"cannot start $EDITOR: {exc}"
                return
            if status != 0:
                state.message = f"$EDITOR exited with status {status}; JSON not applied"
                return
            try:
                raw = Path(path).read_bytes()
            except OSError as exc:
                state.message = f"cannot read edited JSON: {exc}"
                return
            if raw == before:
                state.message = "JSON unchanged."
                return
            try:
                candidate = strict_json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                state.message = f"JSON not applied: {exc}"
                return
            if not isinstance(candidate, dict):
                state.message = "JSON not applied: the composition must be an object"
                return
            try:
                problems = catalog_mod.validate_composition(
                    candidate, state.models, state.roles, state.providers
                )
            except (KeyError, TypeError) as exc:
                state.message = f"JSON not applied: missing or mistyped field ({exc})"
                return
            name = candidate.get("name", "")
            try:
                from .state import check_name

                check_name(name)
            except OSError as exc:
                state.message = f"JSON not applied: {exc}"
                return
            if (
                name != state.original_document.get("name")
                and self.name_taken is not None
                and self.name_taken(name)
            ):
                state.message = f"JSON not applied: composition name {name!r} already exists"
                return
            state.document = candidate
            self._sync_text_inputs()
            if problems:
                state.message = (
                    "JSON applied with validation problems; the form shows BLOCKED."
                )
            else:
                state.message = "JSON applied from $EDITOR."
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def _sync_text_inputs(self) -> None:
        self.name_input = TextInput(self.state.document.get("name", ""), max_length=64)
        self.desc_input = TextInput(self.state.document.get("description", ""))
        self.radio_cursor = {}

    # -- main loop -----------------------------------------------------------

    def _guarantee_modal(self, win: Any) -> None:
        Modal(
            "editor — help",
            EDITOR_HELP.splitlines()
            + workflow_guarantee_panel(self.state.workflows).splitlines(),
            buttons=(("Close", True),),
        ).run(win, self.palette, background=self._draw)

    def run(self, win: Any) -> EditorOutcome | None:
        hide_cursor()
        while True:
            self._rows = self._build_rows()
            height, width = win.getmaxyx()
            if height < 20 or width < 60:
                win.erase()
                safe_add(win, 0, 0, FORM_TOO_SMALL, self.palette.attr("error") | curses.A_BOLD)
                safe_add(win, 2, 0, FORM_TOO_SMALL_HINT)
                win.refresh()
                key = read_key(win)
                if key.kind in ("esc",) or (key.kind == "char" and key.ch == "q"):
                    return None
                if key.kind == "ctrl" and key.ch == "c":
                    raise KeyboardInterrupt
                continue
            focusable = self._focusable()
            if self.focus not in focusable:
                self.focus = focusable[0] if focusable else 0
            self._draw(win)
            key = read_key(win)
            row = self._rows[self.focus]
            if key.kind == "resize":
                continue
            if key.kind == "ctrl" and key.ch == "c":
                # Panic interrupt routes through the same dirty check as
                # Esc: unsaved edits are never lost to a stray ^C.
                if self.state.dirty:
                    confirmed = Modal(
                        FORM_DISCARD_TITLE,
                        [FORM_DISCARD_BODY],
                        buttons=(("Discard", True), ("Keep editing", False)),
                    ).run(win, self.palette, background=self._draw)
                    if not confirmed:
                        continue
                raise KeyboardInterrupt
            if key.kind == "ctrl" and key.ch == "g":
                show_cursor()
                self._edit_json(win)
                hide_cursor()
                continue
            if key.kind == "ctrl" and key.ch == "o":
                # Convenience save chord (014): opens the same Save-or-launch
                # menu the actions row opens, focus-independent. Global like
                # ?/^G/Esc so text rows keep every printable key; ^O has no
                # terminal flow-control meaning (unlike ^S = XOFF).
                outcome = self._open_actions(win)
                if outcome is not None:
                    return outcome
                continue
            if key.kind == "esc":
                if self.state.dirty:
                    confirmed = Modal(
                        FORM_DISCARD_TITLE,
                        [FORM_DISCARD_BODY],
                        buttons=(("Discard", True), ("Keep editing", False)),
                    ).run(win, self.palette, background=self._draw)
                    if not confirmed:
                        continue
                return None
            if key.kind == "char" and key.ch == "?":
                self._guarantee_modal(win)
                continue
            if row.kind == "text":
                if key.kind == "up" or (key.kind == "btab"):
                    self._move_focus(-1)
                elif key.kind in ("down", "tab", "enter"):
                    self._move_focus(1)
                else:
                    if row.payload.handle(key):
                        field = "name" if row.label == "Name" else "description"
                        self.state.document[field] = row.payload.value
                continue
            if key.kind == "up" or key.kind == "btab":
                self._move_focus(-1)
                continue
            if key.kind == "down" or key.kind == "tab":
                self._move_focus(1)
                continue
            if row.kind == "radio":
                key_name, values = row.payload
                current_value = self.state.document["native_agents"][key_name]
                cursor = self.radio_cursor.get(
                    key_name,
                    next((i for i, v in enumerate(values) if v == current_value), 0),
                )
                if key.kind == "left":
                    cursor = (cursor - 1) % len(values)
                    self.radio_cursor[key_name] = cursor
                elif key.kind == "right":
                    cursor = (cursor + 1) % len(values)
                    self.radio_cursor[key_name] = cursor
                elif key.kind == "enter" or (key.kind == "char" and key.ch == " "):
                    try:
                        self.state.set_native(key_name, values[cursor])
                        self.state.message = "Native-agent policy updated."
                    except EditorError as exc:
                        self.state.message = str(exc)
                continue
            if row.kind == "check":
                if key.kind == "enter" or (key.kind == "char" and key.ch == " "):
                    if row.payload == "general_purpose":
                        current = self.state.document["native_agents"]["general_purpose"]
                        self.state.set_native(
                            "general_purpose", "off" if current == "on" else "on"
                        )
                        self.state.message = "Native-agent policy updated."
                    else:
                        self.state.set_workflows(
                            "off" if self.state.workflows == "native" else "native"
                        )
                        self.state.message = "Workflow mode updated."
                continue
            if key.kind == "enter" or (key.kind == "char" and key.ch == " "):
                if row.kind == "lead":
                    self._open_lead(win)
                elif row.kind == "avail":
                    level, key_id = row.payload
                    self._open_scope(win, level, key_id, row.label)
                elif row.kind == "role":
                    self._open_variants(win, row.payload)
                elif row.kind == "actions":
                    outcome = self._open_actions(win)
                    if outcome is not None:
                        return outcome
                continue
            # home/end jump between first/last fields
            if key.kind == "home":
                self.focus = self._focusable()[0]
            elif key.kind == "end":
                self.focus = self._focusable()[-1]


def run_form_editor(
    state: EditorState,
    *,
    name_taken: Callable[[str], bool] | None = None,
    open_in_editor: Callable[[str], int] | None = None,
    suspender: Callable[[Any], contextlib.AbstractContextManager[Any]] | None = None,
    no_color: bool = False,
    environ: Mapping[str, str] | None = None,
    input_stream: Any = None,
    output_stream: Any = None,
) -> EditorOutcome | None:
    """Run the form editor in its own curses session (streams default stdio).

    The caller decides curses is appropriate; this function still restores
    the terminal unconditionally and converts Ctrl+C into a cancel.
    """

    environ = dict(os.environ if environ is None else environ)
    input_stream = sys.stdin if input_stream is None else input_stream
    output_stream = sys.stdout if output_stream is None else output_stream
    palette = detect_palette(
        environ, no_color=no_color, tty_in=input_stream, tty_out=output_stream
    )
    screen = FormEditorScreen(
        state,
        palette=palette,
        name_taken=name_taken,
        open_in_editor=open_in_editor,
        suspender=suspender,
        environ=environ,
    )
    try:
        return run_curses_on_streams(
            screen.run, input_stream, output_stream, palette=palette
        )
    except KeyboardInterrupt:
        return None
