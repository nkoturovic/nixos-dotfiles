# 017 — Muted color visibility

## Context

Operator report: dark-gray TUI text is nearly invisible on dark terminal
backgrounds. Named victims: main-card labels (`lead`, `agents`, `role`,
`policy`, `project`, `sessions`, the `health gateway ok · pin …` strip) and
the sessions screen (`managed (claude-multi)` / `native (unmanaged…)` section
headers, `session/project/active` column headers, `[r]esume [t]ransition
[f]orget` row hints).

## Root cause (verified by recon)

One centralized role causes every case: **`dim` = `COLOR_BLACK` foreground +
`A_BOLD`** (`tui.py:118-137`), which terminals render as bright black ≈ dark
gray. Worse, when `curses.use_default_colors()` fails, the pair falls back to
black-on-**black** (`tui.py:321`). The ANSI mirror is SGR `90` (bright black,
`tui.py:138-152`; currently no production caller). Every screen routes through
`palette.attr("dim")` — card labels, `Table` headers (`tui.py:1035-1039`),
`KeyBar` separators, section headers, row hints — so **one palette change fixes
all screens at once**; no per-screen edits, no goldens (the TUI has no frame
goldens — `bless.py` covers compiler/scope/gateway renders only), and no
existing test pins the dim value.

## Design

Capability-aware gray, by construction never black-on-dark:

1. `init_curses_colors` (`tui.py:310-325`): when `curses.COLORS >= 256`, init
   the dim pair with **256-color 245** (mid gray, readable on dark *and*
   light) for both palettes. 8-color fallback: dark → `COLOR_WHITE` (renders
   light gray on dark themes — strictly more readable than bold black),
   light → `COLOR_BLACK` (unchanged; correct on light backgrounds).
   The black-on-black fallback trap disappears because dark dim fg is never
   black again.
2. `_BOLD_ROLES` keeps `dim` (bold preserves the muted-vs-normal hierarchy
   where colors are approximated).
3. `_ANSI_SGR[...]["dim"]` → `"38;5;245"` (both palettes; production-unused
   today, pinned so it can't drift).

245 choice: `#8a8a8a` — ~3.4:1 on typical dark backgrounds (vs ~1.2:1 for
bright black), still visually secondary to normal text; with A_BOLD it trends
toward 248.

## Tests (`tests/test_tui.py`)

New pins (none exist today):

- pair init: mocked `curses.init_pair` — COLORS≥256 → fg 245; COLORS=8 dark →
  `COLOR_WHITE`; COLORS=8 light → `COLOR_BLACK`.
- ANSI: exact `\x1b[38;5;245m…\x1b[0m` for `DARK_PALETTE.ansi(text, "dim")`.
- Role application: `Table` header and a selected-row action hint render with
  `DARK_PALETTE.attr("dim")` (attribute equality, not hard-coded bits).

## Non-goals

- No per-screen restyling, no new roles, no theme configuration surface.
- Light-palette 8-color behavior unchanged (no complaint, correct today).
