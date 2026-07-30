# 012 — Sessions picker should default to this directory's sessions

**Status: resolved** · fixed in 2.11.0 (D47)

## Request (2026-07-30)

Harness-style default: opening the sessions screen should show the current
directory's sessions first (cwd filter ON by default, C widens to all).
Also confirm sessions always start in their recorded directory (agent
files like AGENTS.md/CLAUDE.md depend on it).

## Resume-cwd verification (no change needed)

`launch.py::_CwdLease.prepare(record["cwd"])` opens the session's recorded
directory by fd, proves it enterable BEFORE any state commit (failing
loudly: "cannot open/enter the session's original project directory …
repair the recorded CWD"), and `enter()` fchdirs into it immediately
before `execve`. Resumed sessions always start in their original project
directory regardless of where the launcher was invoked. Correct and
already hardened.

## Change

`_SessionsScreen.cwd_filter` now defaults to `True` (title shows
`· cwd filter ON`); **C** toggles to all sessions and back. SESSIONS_HELP
documents the default. The text `sessions list` command stays unfiltered
(it is an explicit cross-directory report). No other TUI window shows
cross-cwd content, so no other window needed the preference.

## Tests

`CwdFilterToggleTests` rewritten for the new default (opens filtered,
widens on toggle, roundtrip). Three tests whose fixtures live in other
directories (native fork annotation, native adopt, hostile-cwd
sanitization) now opt out of the filter explicitly — their subjects are
unchanged.
