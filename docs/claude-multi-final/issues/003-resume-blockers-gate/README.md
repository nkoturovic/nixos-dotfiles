# 003 — Resume blockers: daemon-owned + no-conversation + TUI gate

**Status: in release** · fixed in 2.8.0 (D41)

## Report (2026-07-29, operator)

Three native messages seen when trying to resume sessions:

1. The repair-needed BLOCKED card (issue 002).
2. `No conversation found with session ID: 5ee2f942-a367-4e96-a113-72eb4ecbd84c`
3. `Session 707e80d4-… is currently running as a background agent (bg).
   Use `claude agents` to find and attach to it, or add --fork-session to
   branch off a copy.`

Operator directive: pin down why each happens; if expected, make the
right thing easy — a TUI popup on resume stating what is needed with
accept/cancel, matching the existing UX elements. TUI is the main way
claude-multi is used.

## Root cause (investigator-verified)

- **Daemon-owned (bg) resumes are completely unguarded**: picker resume
  and `-r` both compile + exec native `claude --resume <runtime_id>`
  even for ● rows; native Claude then rejects after exec. Following the
  native advice (attach via agents menu / --fork-session) is exactly what
  created the original fork incident (see checkpoint v2.7.0 §incident).
- The ● signal is a **filename heuristic** (`/tmp/cc-daemon-*/pty/*.sock`
  glob, no liveness proof) — fine as an action-needed gate with choices,
  unsafe as a hard block (stale pathnames).
- **No-conversation is pre-detectable**: the expected transcript path is
  deterministic (`~/.claude/projects/<slug-of-cwd>/<runtime_id>.jsonl`)
  from data claude-multi already holds. No check existed; the native
  error surfaced bare. (For 5ee2f942 the transcript is absent *everywhere*
  — see issue 006.)
- Smallest common interception point: `Runtime.perform(prepared)` —
  but curses is torn down before it, so one **pure evaluator** +
  UI-specific presentation adapters is required. Lock doctrine: checks +
  interaction strictly before the lifecycle lock; the gate takes no
  locks and does no mutations itself.

## Fix (this batch)

- Pure `_evaluate_resume_gate(runtime, prepared)` in cli.py →
  `ok | repair-needed | daemon-owned | transcript-missing/elsewhere`
  with pre-wrapped lines (≤60 cols) and value-bearing actions.
- `Runtime.perform` = single mandatory enforcement point (all launch
  paths); `--print-launch` stays a pure diagnostic.
- TUI adapters (sessions picker, card picker, quick-confirm): Modal with
  actions — daemon-owned: **Stop & resume** (D38 stop machinery) /
  **Resume anyway** (stale-marker escape) / **Cancel**; repair-needed:
  **Repair & resume** (store relink via helper) / **Cancel**;
  transcript-missing: guidance + Cancel only.
- Text mode: actionable exact commands; `-r --force` bypasses ONLY the
  daemon-owned branch (documented stale-signal escape hatch).
- Sessions list marks repair-needed rows (extends ●/⚠ markers).
- Lock doctrine preserved: gate is pure metadata, no locks, pre-lock.

## Verification

1,361 tests OK (15 new ResumeGateTests: evaluator branches, perform
backstop incl. force/precommitted/fresh exemptions, picker modal flow,
markers, text surfaces); PTY-verified modal rendering; sandbox green;
cross-family review (pending at log time).
