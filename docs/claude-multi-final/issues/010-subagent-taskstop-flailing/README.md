# 010 — Subagent TaskStop flailing: ownership refusals + hallucinated task IDs

**Status: resolved** · fixed in 2.11.0 (D47, prompt contract)

## Found (2026-07-30, from a production review transcript)

A nested reviewer subagent, waiting on its own background children, tried
`Stop Task` repeatedly: first with invented IDs (`nonexistent`,
`nonexistent2`, `none`), then with real child IDs, receiving
`Task <id> is owned by <id>; agent <id> cannot stop it` each time. The
same agent later ended with the status line "Review in progress." instead
of its report (an infrastructure death — recovered correctly by the D43
continuation rule: one SendMessage and it delivered the full critique).

## Root cause (binary-verified, 2.1.220)

TaskStop validation in the binary carries exactly three refusal strings:
`Observer … cannot stop itself; use the task UI or a main-session
TaskStop`, `Task … is owned by …; agent … cannot stop it` (`not_owner`),
and `… is not running`. The rule is ownership: the main session may stop
any task; a descendant cannot stop another agent's tasks — including its
delegated children, whose tasks register under the CHILD agent's identity
rather than the spawning parent's — though it can still stop background
shell/monitor tasks registered under its own identity. The invented IDs
were the model flailing after the refusals — not a launcher issue.

Our D43 lead contract ("abandoned — stop it with TaskStop first") is
correct for the lead (which IS the main session) but said nothing about
descendants; nested agents applying the same rule to their own delegates
hit the native refusal with no guidance.

## Fix (surgical, prompt-only)

- `cm-lead.md` failure handling: the abandon bullet now states stopping a
  delegated agent works only from the main session (native ownership) —
  descendants report stuck agents upward in their final text.
- All three non-lead role prompts (analyst/implementer/reviewer) gained a
  delegation line: spawn only `cm-*` types; never TaskStop a delegated
  child agent (refused by ownership — own background shell/monitor tasks
  are unaffected); report stuck/failed delegates in the final text.

Pins: `test_roles` PROMPT_KEYWORDS (`refused by ownership` in the
non-lead prompts, `from the main session (native ownership)` in lead).
Goldens re-blessed.
