"""Widget-layer tests for claude-multi's TUI (curses test double, no terminal).

The :class:`FakeWindow` double implements the exact window protocol the
widgets use (``getmaxyx``/``addstr``/``erase``/``refresh``/``get_wch``/
``move``/``keypad``) against an in-memory cell grid, so screens and widgets
are driven with scripted keys and asserted on rendered text and attributes.
"""

from __future__ import annotations

import curses
import io
import os
import pty
import threading
import unittest
import unittest.mock

from claude_multi import tui

# Scripted key helpers: characters are fed to get_wch verbatim; special keys
# use the real curses constants (available without initscr).
ENTER = "\n"
ESC = "\x1b"
TAB = "\t"
BACKSPACE = "\x7f"
CTRL_G = "\x07"
CTRL_O = "\x0f"
DOWN = curses.KEY_DOWN
UP = curses.KEY_UP
LEFT = curses.KEY_LEFT
RIGHT = curses.KEY_RIGHT
HOME = curses.KEY_HOME
END = curses.KEY_END
BTAB = curses.KEY_BTAB
DELETE = curses.KEY_DC


class FakeWindow:
    """In-memory curses window: records cells + attrs, replays scripted keys."""

    def __init__(self, keys=(), *, height=30, width=90):
        self.height = height
        self.width = width
        self.keys = list(keys)
        self.grid: dict[tuple[int, int], tuple[str, int]] = {}
        self.frames: list[str] = []
        self.cursor_pos: tuple[int, int] | None = None
        self.keypad_flag = False

    def getmaxyx(self):
        return (self.height, self.width)

    def addstr(self, y, x, text, attr=0):
        if y < 0 or y >= self.height or x < 0 or x >= self.width:
            raise curses.error("addstr out of bounds")
        for offset, ch in enumerate(text):
            if x + offset >= self.width:
                raise curses.error("addstr past right edge")
            self.grid[(y, x + offset)] = (ch, attr)

    def erase(self):
        self.grid = {}

    def refresh(self):
        self.frames.append(self.text())

    def get_wch(self):
        if not self.keys:
            if getattr(self, "_timeout_armed", False):
                raise curses.error("no input")
            raise AssertionError("FakeWindow key script exhausted")
        return self.keys.pop(0)

    def timeout(self, _ms):
        # Model curses: with any timeout armed, an empty queue raises
        # curses.error instead of blocking forever.
        self._timeout_armed = True

    def move(self, y, x):
        if y < 0 or y >= self.height or x < 0 or x >= self.width:
            raise curses.error("move out of bounds")
        self.cursor_pos = (y, x)

    def keypad(self, flag):
        self.keypad_flag = flag

    # -- assertions ---------------------------------------------------------

    def line(self, y: int) -> str:
        return "".join(
            self.grid.get((y, x), (" ", 0))[0] for x in range(self.width)
        ).rstrip()

    def text(self) -> str:
        return "\n".join(self.line(y) for y in range(self.height))

    def attr_at(self, y: int, x: int) -> int:
        return self.grid.get((y, x), (" ", 0))[1]

    def find(self, needle: str) -> list[tuple[int, int]]:
        found = []
        for y in range(self.height):
            line = self.line(y)
            if needle in line:
                found.append((y, line.index(needle)))
        return found


class ReadKeyTests(unittest.TestCase):
    def feed(self, values):
        win = FakeWindow(values)
        return [tui.read_key(win) for _ in values]

    def test_character_and_control_mapping(self):
        kinds = self.feed(["a", ENTER, TAB, ESC, BACKSPACE, CTRL_G, DOWN, BTAB])
        self.assertEqual(
            [(k.kind, k.ch) for k in kinds],
            [
                ("char", "a"),
                ("enter", ""),
                ("tab", ""),
                ("esc", ""),
                ("backspace", ""),
                ("ctrl", "g"),
                ("down", ""),
                ("btab", ""),
            ],
        )

    def test_unknown_special_key(self):
        (key,) = self.feed([curses.KEY_F1])
        self.assertEqual(key.kind, "unknown")

    def test_unicode_character(self):
        (key,) = self.feed(["ö"])
        self.assertEqual((key.kind, key.ch), ("char", "ö"))


class PaletteTests(unittest.TestCase):
    def test_colorfgbg_background_detection(self):
        self.assertEqual(tui.background_from_colorfgbg("15;0"), "dark")
        self.assertEqual(tui.background_from_colorfgbg("0;15"), "light")
        self.assertEqual(tui.background_from_colorfgbg("0;7"), "light")
        self.assertEqual(tui.background_from_colorfgbg("12;8"), "dark")
        self.assertEqual(tui.background_from_colorfgbg("1;14"), "light")
        self.assertIsNone(tui.background_from_colorfgbg(""))
        self.assertIsNone(tui.background_from_colorfgbg("not-a-number"))

    def test_osc11_response_parsing(self):
        self.assertEqual(
            tui.parse_osc11_response(b"\x1b]11;rgb:ffff/ffff/ffff\x07"), "light"
        )
        self.assertEqual(
            tui.parse_osc11_response(b"\x1b]11;rgb:0000/0000/0000\x1b\\"), "dark"
        )
        self.assertEqual(
            tui.parse_osc11_response(b"\x1b]11;rgb:2a2a/2a2a/2a2a\x07"), "dark"
        )
        self.assertEqual(
            tui.parse_osc11_response(b"\x1b]11;rgba:ffff/ffff/ffff/ffff\x07"),
            "light",
        )
        self.assertIsNone(tui.parse_osc11_response(b"garbage"))

    def test_osc11_query_roundtrip_over_pty(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        result: dict[str, str | None] = {}

        def ask():
            result["value"] = tui.query_background_osc11(slave, slave)

        thread = threading.Thread(target=ask)
        thread.start()
        query = os.read(master, 64)
        self.assertIn(b"]11;?", query)
        os.write(master, b"\x1b]11;rgb:ffff/ffff/ffff\x07")
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["value"], "light")

    def test_osc11_query_timeout_defaults_none(self):
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        self.addCleanup(os.close, slave)
        self.assertIsNone(tui.query_background_osc11(slave, slave, timeout=0.05))

    def test_detect_palette_priority(self):
        self.assertIs(tui.detect_palette({"NO_COLOR": "1"}), tui.MONO_PALETTE)
        self.assertIs(
            tui.detect_palette({}, no_color=True), tui.MONO_PALETTE
        )
        self.assertIs(
            tui.detect_palette({"COLORFGBG": "0;15"}), tui.LIGHT_PALETTE
        )
        self.assertIs(tui.detect_palette({"COLORFGBG": "15;0"}), tui.DARK_PALETTE)
        # No COLORFGBG and streams without fds: default dark, no crash.
        self.assertIs(
            tui.detect_palette({}, tty_in=io.StringIO(), tty_out=io.StringIO()),
            tui.DARK_PALETTE,
        )

    def test_role_attrs_and_mono_text_form(self):
        self.assertEqual(tui.DARK_PALETTE.attr("accent"), (1 << 8) | curses.A_BOLD)
        self.assertEqual(tui.DARK_PALETTE.attr("error"), (4 << 8) | curses.A_BOLD)
        self.assertEqual(tui.LIGHT_PALETTE.attr("accent"), (1 << 8) | curses.A_BOLD)
        self.assertEqual(tui.LIGHT_PALETTE.attr("error"), 4 << 8)
        for role in ("accent", "ok", "warn", "error", "dim"):
            self.assertEqual(tui.MONO_PALETTE.attr(role), 0)
        self.assertEqual(tui.DARK_PALETTE.attr("nonsense"), 0)

    def test_ansi_styling_and_mono_verbatim(self):
        self.assertEqual(tui.DARK_PALETTE.ansi("Ready", "ok"), "\x1b[32mReady\x1b[0m")
        self.assertEqual(
            tui.LIGHT_PALETTE.ansi("BLOCKED", "error"), "\x1b[31mBLOCKED\x1b[0m"
        )
        self.assertEqual(tui.MONO_PALETTE.ansi("Ready", "ok"), "Ready")

    def test_init_curses_colors_without_terminal_is_safe(self):
        # has_colors() raises before initscr; the helper must swallow it.
        tui.init_curses_colors(tui.DARK_PALETTE)
        tui.init_curses_colors(tui.MONO_PALETTE)


class DimVisibilityTests(unittest.TestCase):
    """017: muted text must stay readable on dark terminal backgrounds."""

    def test_dim_ansi_is_mid_gray_not_bright_black(self):
        self.assertEqual(
            tui.DARK_PALETTE.ansi("x", "dim"), "\x1b[38;5;245mx\x1b[0m"
        )

    def test_dim_foreground_256_color_gray(self):
        with unittest.mock.patch.object(tui.curses, "COLORS", 256, create=True):
            self.assertEqual(tui._dim_foreground("dark"), 245)
            # Light palettes get a darker muted gray: 245 on white is too
            # faint (review nit: ~2.7:1 contrast).
            self.assertEqual(tui._dim_foreground("light"), 240)

    def test_dim_foreground_8_color_fallback_never_black_on_dark(self):
        with unittest.mock.patch.object(tui.curses, "COLORS", 8, create=True):
            self.assertEqual(tui._dim_foreground("dark"), curses.COLOR_WHITE)
            self.assertEqual(tui._dim_foreground("light"), curses.COLOR_BLACK)

    def test_init_pair_uses_capability_aware_dim(self):
        pairs = {}

        def fake_init_pair(pair, fg, bg):
            pairs[pair] = (fg, bg)

        with (
            unittest.mock.patch.object(tui.curses, "has_colors", return_value=True),
            unittest.mock.patch.object(tui.curses, "start_color"),
            unittest.mock.patch.object(tui.curses, "use_default_colors"),
            unittest.mock.patch.object(tui.curses, "init_pair", fake_init_pair),
            unittest.mock.patch.object(tui.curses, "COLORS", 256, create=True),
        ):
            tui.init_curses_colors(tui.DARK_PALETTE)
        self.assertEqual(pairs[tui._PAIR_BY_ROLE["dim"]], (245, -1))
        self.assertEqual(
            pairs[tui._PAIR_BY_ROLE["accent"]], (curses.COLOR_CYAN, -1)
        )

    def test_init_pair_dim_falls_back_to_white_on_dark_8_color(self):
        pairs = {}

        def fake_init_pair(pair, fg, bg):
            pairs[pair] = (fg, bg)

        with (
            unittest.mock.patch.object(tui.curses, "has_colors", return_value=True),
            unittest.mock.patch.object(tui.curses, "start_color"),
            unittest.mock.patch.object(
                tui.curses, "use_default_colors", side_effect=curses.error
            ),
            unittest.mock.patch.object(tui.curses, "init_pair", fake_init_pair),
            unittest.mock.patch.object(tui.curses, "COLORS", 8, create=True),
        ):
            tui.init_curses_colors(tui.DARK_PALETTE)
        # No default-colors support: background falls back to black, so the
        # dim foreground must NOT be black (the old black-on-black trap).
        self.assertEqual(
            pairs[tui._PAIR_BY_ROLE["dim"]], (curses.COLOR_WHITE, curses.COLOR_BLACK)
        )

    def test_table_headers_render_with_dim_role(self):
        win = FakeWindow()
        table = tui.Table(("name", "state"), [("alpha", "ok")])
        table.draw(win, 0, 2, 80, tui.DARK_PALETTE)
        self.assertEqual(
            win.attr_at(0, 2), tui.DARK_PALETTE.attr("dim") | curses.A_BOLD
        )
        self.assertIn("name", win.line(0))

    def test_streams_curses_capable(self):
        class Tty(io.StringIO):
            def isatty(self):
                return True

        self.assertFalse(
            tui.streams_curses_capable(Tty(), Tty(), {"TERM": "dumb"})
        )
        self.assertFalse(tui.streams_curses_capable(Tty(), Tty(), {}))
        self.assertTrue(
            tui.streams_curses_capable(Tty(), Tty(), {"TERM": "xterm-256color"})
        )
        self.assertFalse(
            tui.streams_curses_capable(
                io.StringIO(), Tty(), {"TERM": "xterm-256color"}
            )
        )


class LabelBadgeKeyBarTests(unittest.TestCase):
    def test_label_and_badge_render_with_role_attrs(self):
        win = FakeWindow()
        tui.Label("hello", "dim").draw(win, 0, 0, 80, tui.DARK_PALETTE)
        width = tui.Badge("durable(g2)", "ok").draw(win, 1, 2, tui.DARK_PALETTE)
        self.assertEqual(width, len("durable(g2)"))
        self.assertIn("hello", win.line(0))
        self.assertIn("durable(g2)", win.line(1))
        self.assertEqual(win.attr_at(0, 0), tui.DARK_PALETTE.attr("dim"))
        self.assertEqual(win.attr_at(1, 2), tui.DARK_PALETTE.attr("ok"))

    def test_keybar_text_and_accented_keys(self):
        bar = tui.KeyBar((("Enter", "launch"), ("Esc", "cancel")))
        self.assertEqual(bar.text(), "Enter launch · Esc cancel")
        win = FakeWindow()
        bar.draw(win, 29, tui.DARK_PALETTE)
        self.assertIn("Enter launch · Esc cancel", win.line(29))
        self.assertEqual(win.attr_at(29, 2), tui.DARK_PALETTE.attr("accent"))

    def test_keybar_clips_to_width(self):
        bar = tui.KeyBar(tuple((f"K{i}", f"action-{i}") for i in range(20)))
        win = FakeWindow(width=30)
        bar.draw(win, 29, tui.MONO_PALETTE)  # must not raise


class CheckboxTests(unittest.TestCase):
    def test_checkbox_markers_are_the_text_form(self):
        win = FakeWindow()
        tui.Checkbox("native workflows", checked=True).draw(win, 0, 0, tui.MONO_PALETTE)
        tui.Checkbox("off option", checked=False).draw(win, 1, 0, tui.MONO_PALETTE)
        self.assertIn("[x] native workflows", win.line(0))
        self.assertIn("[ ] off option", win.line(1))

    def test_radio_markers(self):
        win = FakeWindow()
        tui.Checkbox("replace", checked=True, radio=True).draw(
            win, 0, 0, tui.MONO_PALETTE
        )
        self.assertIn("(•) replace", win.line(0))

    def test_group_toggle_and_radio_exclusivity(self):
        boxes = [
            tui.Checkbox("replace", checked=True, radio=True),
            tui.Checkbox("native", radio=True),
            tui.Checkbox("off", radio=True),
        ]
        group = tui.CheckboxGroup(boxes, horizontal=True)
        group.activate(2)
        self.assertEqual(group.checked_indexes(), [2])
        group.activate(1)
        self.assertEqual(group.checked_indexes(), [1])
        plain = tui.CheckboxGroup([tui.Checkbox("a"), tui.Checkbox("b")])
        plain.activate(0)
        plain.activate(1)
        self.assertEqual(plain.checked_indexes(), [0, 1])

    def test_group_navigation_and_disabled_options(self):
        boxes = [tui.Checkbox("a"), tui.Checkbox("b", enabled=False)]
        group = tui.CheckboxGroup(boxes, horizontal=True)
        self.assertTrue(group.handle(tui.Key("right")))
        self.assertEqual(group.cursor, 1)
        group.activate(1)  # disabled: no-op
        self.assertEqual(group.checked_indexes(), [])
        self.assertTrue(group.handle(tui.Key("char", " ")))
        self.assertEqual(group.checked_indexes(), [])
        self.assertFalse(group.handle(tui.Key("up")))


class TextInputTests(unittest.TestCase):
    def drive(self, keys, value=""):
        field = tui.TextInput(value)
        consumed = [field.handle(key) for key in keys]
        return field, consumed

    def test_insert_cursor_and_home_end(self):
        field, _ = self.drive(
            [tui.Key("char", "x"), tui.Key("home"), tui.Key("char", "y"), tui.Key("end"), tui.Key("char", "z")],
            "abc",
        )
        self.assertEqual(field.value, "yabcxz")

    def test_backspace_and_delete(self):
        # cursor starts at end: left, delete removes 'd'; backspace removes 'c'.
        field, _ = self.drive(
            [tui.Key("left"), tui.Key("delete"), tui.Key("backspace")],
            "abcd",
        )
        self.assertEqual(field.value, "ab")
        self.assertEqual(field.cursor, 2)

    def test_backspace_at_start_is_noop_but_consumed(self):
        field, consumed = self.drive([tui.Key("home"), tui.Key("backspace")], "ab")
        self.assertEqual(field.value, "ab")
        self.assertEqual(consumed, [True, True])

    def test_navigation_keys_are_not_consumed(self):
        field, consumed = self.drive(
            [tui.Key("up"), tui.Key("down"), tui.Key("tab"), tui.Key("enter"), tui.Key("esc")],
            "ab",
        )
        self.assertEqual(consumed, [False] * 5)
        self.assertEqual(field.value, "ab")

    def test_max_length(self):
        field, _ = self.drive(
            [tui.Key("char", "a"), tui.Key("char", "b"), tui.Key("char", "c")],
            "",
        )
        self.assertEqual(field.value, "abc")
        limited = tui.TextInput("", max_length=2)
        for ch in "xyz":
            limited.handle(tui.Key("char", ch))
        self.assertEqual(limited.value, "xy")

    def test_draw_reports_cursor_position(self):
        field = tui.TextInput("hello")
        field.handle(tui.Key("left"))
        win = FakeWindow()
        _row, col = field.draw(win, 3, 2, 12, tui.MONO_PALETTE, focused=True)
        self.assertIn("[hello", win.line(3))
        self.assertEqual((3, col), (3, 2 + 1 + 4))

    def test_horizontal_scroll_keeps_cursor_visible(self):
        field = tui.TextInput("x" * 40)
        win = FakeWindow()
        field.handle(tui.Key("home"))
        _row, col = field.draw(win, 0, 0, 12, tui.MONO_PALETTE, focused=True)
        self.assertLessEqual(col, 11)
        self.assertEqual(win.line(0)[1:11], "x" * 10)

    def test_full_field_end_cursor_stays_off_the_closing_bracket(self):
        # Regression: with a full field and the cursor at end-of-input the
        # reported cursor column must be the last field cell, never the "]".
        field = tui.TextInput("x" * 20)  # cursor starts at end
        win = FakeWindow()
        _row, col = field.draw(win, 0, 0, 12, tui.MONO_PALETTE, focused=True)
        # width 12: "[" at 0, 10 inner cells, "]" at 11.
        self.assertEqual(col, 10)
        self.assertEqual(win.line(0)[11], "]")


class SelectListTests(unittest.TestCase):
    def test_single_select_enter_returns_index(self):
        chooser = tui.SelectList(
            "Pick", [tui.SelectItem("a"), tui.SelectItem("b"), tui.SelectItem("c")]
        )
        win = FakeWindow([DOWN, ENTER])
        self.assertEqual(chooser.run(win, tui.MONO_PALETTE), 1)
        self.assertIn("> b", win.line(3))

    def test_esc_cancels(self):
        chooser = tui.SelectList("Pick", [tui.SelectItem("a")])
        win = FakeWindow([ESC])
        self.assertIsNone(chooser.run(win, tui.MONO_PALETTE))

    def test_activate_error_stays_open_with_message(self):
        calls = []

        def activate(index):
            calls.append(index)
            return "nope" if len(calls) == 1 else None

        chooser = tui.SelectList("Pick", [tui.SelectItem("a"), tui.SelectItem("b")])
        win = FakeWindow([ENTER, DOWN, ENTER])
        self.assertEqual(chooser.run(win, tui.MONO_PALETTE, on_activate=activate), 1)
        self.assertEqual(calls, [0, 1])
        self.assertTrue(any("nope" in frame for frame in win.frames))

    def test_multi_toggle_and_refresh(self):
        state = {"a": False, "b": True}

        def refresh():
            return [tui.SelectItem(k, checked=v) for k, v in state.items()]

        def toggle(index):
            key = list(state)[index]
            state[key] = not state[key]
            return None

        chooser = tui.SelectList("Multi", refresh(), multi=True)
        win = FakeWindow([" ", ENTER])
        # Multi-mode Enter returns the widget-tracked toggled indexes;
        # callback callers may ignore it (the editor works via on_toggle).
        self.assertEqual(
            chooser.run(win, tui.MONO_PALETTE, on_toggle=toggle, refresh=refresh),
            [0],
        )
        self.assertEqual(state, {"a": True, "b": True})
        self.assertTrue(any("[x] a" in frame for frame in win.frames))

    def test_prefer_and_help_callbacks(self):
        events = []
        chooser = tui.SelectList(
            "Multi", [tui.SelectItem("a", checked=True)], multi=True
        )
        win = FakeWindow(["p", "?", ENTER])
        chooser.run(
            win,
            tui.MONO_PALETTE,
            on_prefer=lambda i: events.append(("prefer", i)) or None,
            on_help=lambda: events.append(("help",)),
        )
        self.assertEqual(events, [("prefer", 0), ("help",)])

    def test_jk_navigation_and_home_end(self):
        chooser = tui.SelectList(
            "Pick", [tui.SelectItem(str(i)) for i in range(5)], selected=0
        )
        win = FakeWindow(["j", "j", "k", END, HOME, DOWN, ENTER])
        self.assertEqual(chooser.run(win, tui.MONO_PALETTE), 1)


class ModalTests(unittest.TestCase):
    def test_enter_activates_first_button_esc_cancels(self):
        modal = tui.Modal("Confirm", ["Body line"], buttons=(("Yes", True), ("No", False)))
        win = FakeWindow([ENTER])
        self.assertTrue(modal.run(win, tui.MONO_PALETTE))
        text = win.text()
        self.assertIn("Confirm", text)
        self.assertIn("Body line", text)
        self.assertIn("[ Yes ]", text)
        win2 = FakeWindow([ESC])
        self.assertIsNone(
            tui.Modal("Confirm", [], buttons=(("Yes", True),)).run(
                win2, tui.MONO_PALETTE
            )
        )

    def test_left_right_move_button_focus(self):
        modal = tui.Modal("Confirm", [], buttons=(("Yes", True), ("No", False)))
        win = FakeWindow([RIGHT, ENTER])
        self.assertFalse(modal.run(win, tui.MONO_PALETTE))

    def test_tab_cycles_and_input_editing(self):
        modal = tui.Modal(
            "Name",
            ["Target name:"],
            buttons=(("OK", True), ("Cancel", False)),
            input=tui.TextInput(""),
        )
        win = FakeWindow(list("new-name") + [ENTER, ENTER])
        self.assertTrue(modal.run(win, tui.MONO_PALETTE))
        self.assertEqual(modal.input.value, "new-name")
        self.assertIn("new-name", win.text())

    def test_tab_to_cancel(self):
        modal = tui.Modal(
            "Name",
            [],
            buttons=(("OK", True), ("Cancel", False)),
            input=tui.TextInput("seed"),
        )
        win = FakeWindow([TAB, TAB, ENTER])
        self.assertFalse(modal.run(win, tui.MONO_PALETTE))
        self.assertEqual(modal.input.value, "seed")

    def test_modal_draws_border(self):
        modal = tui.Modal("Titled", ["body"])
        win = FakeWindow([ESC])
        modal.run(win, tui.MONO_PALETTE)
        text = win.text()
        self.assertIn("+", text)
        self.assertIn("|", text)


class TableTests(unittest.TestCase):
    def test_columns_align_and_selection_attr(self):
        table = tui.Table(
            ["session", "mode"],
            [["a1b2", "durable(g2)"], ["9c8e", "legacy"]],
            selected=1,
        )
        win = FakeWindow()
        used = table.draw(win, 2, 0, 60, tui.DARK_PALETTE)
        self.assertEqual(used, 3)
        self.assertIn("session", win.line(2))
        self.assertIn("a1b2", win.line(3))
        self.assertIn("durable(g2)", win.line(3))
        mode_col = win.line(4).index("legacy")
        self.assertTrue(win.attr_at(4, mode_col) & curses.A_REVERSE)
        self.assertFalse(win.attr_at(3, 0) & curses.A_REVERSE)

    def test_handle_moves_selection(self):
        table = tui.Table(["c"], [["1"], ["2"], ["3"]])
        self.assertTrue(table.handle(tui.Key("down")))
        self.assertEqual(table.selected, 1)
        table.handle(tui.Key("end"))
        self.assertEqual(table.selected, 2)
        table.handle(tui.Key("up"))
        self.assertEqual(table.selected, 1)
        table.handle(tui.Key("home"))
        self.assertEqual(table.selected, 0)
        self.assertFalse(table.handle(tui.Key("left")))

    def test_columns_shrink_to_width(self):
        table = tui.Table(
            ["averylongcolumnname", "anotherlongcolumnname"],
            [["x" * 60, "y" * 60]],
        )
        win = FakeWindow(width=40)
        table.draw(win, 0, 0, 40, tui.MONO_PALETTE)
        self.assertLessEqual(len(win.line(1)), 40)


class KeyBarOverflowTests(unittest.TestCase):
    """2.14.0: overflow keeps the exit AND the help binding visible."""

    def test_overflow_keeps_help_and_exit(self):
        bar = tui.KeyBar(
            (
                ("Enter", "launch"),
                ("E", "edit"),
                ("D", "details"),
                ("S", "sessions"),
                ("G", "gateway models"),
                ("H", "health"),
                ("?", "help"),
                ("Esc", "cancel"),
            )
        )
        win = FakeWindow(height=24, width=44)
        bar.draw(win, 23, tui.DARK_PALETTE)
        text = win.text()
        self.assertIn("? help", text)
        self.assertIn("Esc cancel", text)
        self.assertIn("…", text)  # middle bindings compacted

    def test_overflow_without_help_protects_exit_only(self):
        bar = tui.KeyBar(
            (("Enter", "launch"), ("D", "details"), ("Esc", "cancel"))
        )
        win = FakeWindow(height=24, width=20)
        bar.draw(win, 23, tui.DARK_PALETTE)
        self.assertIn("Esc cancel", win.text())


class RunCursesOnStreamsTests(unittest.TestCase):
    def test_fd_less_streams_are_rejected(self):
        with self.assertRaises(OSError):
            tui.run_curses_on_streams(
                lambda win: None, io.StringIO(), io.StringIO()
            )


class VisibleTextTests(unittest.TestCase):
    """R1 P3: the ONE visible-text sanitizer escapes every terminal-control
    byte class into a visible form; clean text is byte-identical."""

    def test_escape_becomes_caret_bracket(self):
        self.assertEqual(tui.visible_text("\x1b"), "^[")
        self.assertEqual(tui.visible_text("\x1b[2J"), "^[[2J")

    def test_osc_payload_is_flattened(self):
        payload = "\x1b]8;;https://evil.example\x07click\x1b]8;;\x07"
        self.assertEqual(
            tui.visible_text(payload),
            "^[]8;;https://evil.example^Gclick^[]8;;^G",
        )
        self.assertNotIn("\x1b", tui.visible_text(payload))
        self.assertNotIn("\x07", tui.visible_text(payload))

    def test_c0_controls_and_newlines_use_caret_notation(self):
        self.assertEqual(tui.visible_text("a\nb\tc\rd"), "a^Jb^Ic^Md")
        self.assertEqual(tui.visible_text("\x00\x01\x1f"), "^@^A^_")

    def test_del_and_c1_controls(self):
        self.assertEqual(tui.visible_text("\x7f"), "^?")
        self.assertEqual(tui.visible_text("\x85"), "\\x85")
        self.assertEqual(tui.visible_text("\x9b"), "\\x9b")

    def test_clean_text_is_identical(self):
        for clean in (
            "default",
            "/repo/dot files/πroject",
            "cm-analyst-sol-high · high ★ preferred → durable(g2)",
            "ö",
        ):
            self.assertEqual(tui.visible_text(clean), clean)

    def test_safe_add_never_passes_control_bytes_to_the_window(self):
        win = FakeWindow()
        tui.safe_add(win, 0, 0, "evil\x1b[2J\x07name", 0)
        line = win.line(0)
        self.assertNotIn("\x1b", line)
        self.assertNotIn("\x07", line)
        self.assertIn("evil^[[2J^Gname", line)

    def test_safe_add_still_renders_clean_text_and_attrs(self):
        win = FakeWindow()
        tui.safe_add(win, 1, 2, "durable scope", 7)
        self.assertIn("durable scope", win.line(1))
        self.assertEqual(win.attr_at(1, 2), 7)


if __name__ == "__main__":
    unittest.main()


class ModalRenderTests(unittest.TestCase):
    MONO_PALETTE = tui.MONO_PALETTE
    Modal = tui.Modal
    Key = tui.Key

    def test_modal_paints_interior_no_bleed_through(self) -> None:
        win = FakeWindow()
        # Pre-fill the whole window with background noise.
        for y in range(win.height):
            for x in range(win.width):
                win.grid[(y, x)] = ("Z", 0)
        modal = tui.Modal("Title", ["line one", "line two"], buttons=(("OK", True),))
        modal.draw(win, tui.MONO_PALETTE)
        top, left, box_h, box_w = modal._geometry(win)
        for row in range(top + 1, top + box_h - 1):
            for col in range(left + 1, left + box_w - 1):
                cell = win.grid.get((row, col), (" ", 0))[0]
                self.assertNotEqual(
                    cell, "Z", f"background leaked at {(row, col)}"
                )

    def test_modal_resize_erases_before_redraw(self) -> None:
        win = FakeWindow(keys=[curses.KEY_RESIZE, "\x1b"])
        modal = tui.Modal("Title", ["body"], buttons=(("OK", True),))
        modal.draw(win, tui.MONO_PALETTE)
        frames_before = len(win.frames)
        modal.run(win, tui.MONO_PALETTE)
        # The resize path erased the window: the frame right after erase is
        # the freshly redrawn dialog on an otherwise empty grid, and the old
        # geometry's border characters appear only in the final position.
        self.assertGreater(len(win.frames), frames_before)


