# 014 — Edit-tab save hotkey (^O)

## Context

The composition editor (`FormEditorScreen`, `tui.py:1582`) exposes Save via
exactly one path: move focus to the **Save or launch** row and press
Enter/Space. The row is last in the form, so from the initial Name-field focus
it is Shift-Tab (wrap) or many Downs away. Operator ask: a convenience hotkey
on the edit screen that opens the same menu — "being a convenience button".

## Design (chosen after conflict analysis)

**`^O` opens the Save-or-launch menu from anywhere on the form.**

Candidate analysis (from recon, all verified against `tui.py`):

- `s` / `S` — rejected: text rows (Name/Description) pass every printable key
  to `TextInput.handle` (`tui.py:2148-2157`); a global printable `s` would make
  the letter untypable in composition names ("sol"!), and a
  focus-conditional `s` is an inconsistent half-hotkey.
- `^S` — rejected: the app runs cbreak, not raw, and never disables IXON
  (`tui.py:1080-1119`); on flow-controlled terminals ^S is eaten as XOFF and
  can freeze output. Unreliable by construction.
- **`^O` — chosen**: nano's WriteOut (= save) chord; unbound anywhere in the
  form today; `read_key` normalizes 0x0F to `Key("ctrl", "o")`
  (`tui.py:387-399`); no terminal flow-control meaning.

Precedent for global interception already exists: `?`, `^G`, `Esc`, `^C` are
handled before the text-row branch (`tui.py:2124-2147`), and
`EditorHelpKeyTests` pins that `?` fires even while a text row is focused.

## Changes

1. `FormEditorScreen.run` — new global branch immediately after the `^G`
   branch (`tui.py:~2135`): on `Key("ctrl","o")` call `self._open_actions(win)`;
   a non-None outcome returns from `run` exactly as the actions-row path does
   (`tui.py:2206-2209`); None (menu Esc / validation refusal) continues the
   loop. Focus-independent by placement, before the text-row branch.
2. `FORM_KEYBAR` (`tui.py:1525`) gains `("^O", "save")` before `("Esc",
   "cancel")`. Width safety: `KeyBar.draw` wraps to two rows and the D30
   contract guarantees the last (Esc) binding is never clipped — middle
   bindings compact behind an ellipsis, so 60-column minimum terminals stay
   correct.
3. Help text: the form's `?` modal (`FORM` help constant near
   `tui.py:1570`) gains a `^O` line mirroring the keybar wording.

## Tests (`tests/test_editor.py`, FakeWindow pattern)

New `EditorSaveHotkeyTests`:

- ^O from a non-text row opens the actions menu; Esc in menu returns to the
  form (outcome None, screen still running — verified via a follow-up key).
- ^O while the **Name text row is focused** opens the menu (global
  interception, the `?` precedent).
- Typing safety: `s`/`S`/`o` on a text row still insert into the field
  (no regression from branch placement).
- ^O → Enter on "Update current composition" yields the `update` outcome —
  same as the row path (outcome equality).
- Keybar render contains `^O save` (`FormEditorRenderTests`-style frame
  assertion).

## Non-goals

- No line-mode (`RunEditorChooser`) binding — the fallback already has a
  numbered menu; curses-only convenience.
- No `^S` alias (XOFF hazard above).
