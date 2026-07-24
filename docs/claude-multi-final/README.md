# claude-multi — final architecture (active)

Status: **design checkpoint pending** → then implementation.
Owner: cm-lead (integration). Started: 2026-07-22.

This folder is the **active** design/implementation package for the claude-multi
rethink. It replaces the frozen C\* effort
(`docs/claude-multi-durable-session-config*.md`, historical evidence only).

## The problem in one paragraph

claude-multi v2 compiles the selected agent composition into launch-time
`--agents` JSON. Officially, CLI-defined subagents "exist only for that session
and aren't saved to disk" (sub-agents doc). When Claude's shared supervisor
self-restarted for the 2.1.216 → 2.1.217 upgrade (daemon.log
2026-07-21T21:42:28Z), the session's agent registry was rebuilt from persisted
state; the argv-only definitions were gone, so all six selected `cm-*` types
disappeared while transcript, environment and lead model survived. Generic
`claude` inherited the lead's Kimi model and review became same-family.

## The fix in one paragraph

Compile the composition to **files Claude officially discovers and reloads**,
in a **per-session scope directory** referenced by `--add-dir` — a flag in
Claude's documented carry-through set for backgrounded/respawned sessions
(agent-view doc; supervisor-takeover carry is binary-consistent and gated as
unknown U1 with a stop-the-line M1 proof). No `--agents`, no config-root
relocation, no daemon, no hooks, no transaction machinery. One pure JIT
compiler: `composition → scope files + settings + argv`. The durable-policy
doctrine: **files survive; documented carry-through argv survives; everything
else is a displayed residual, never a silent guarantee.**

## Files

| File | Content |
| --- | --- |
| [BLUEPRINT.md](BLUEPRINT.md) | Factual audit, root cause, architecture options (5) with tradeoffs, recommended design, invariants, simplicity justification |
| [SPEC.md](SPEC.md) | Exact state layout, compiler outputs, launch contract, enforcement/honesty table |
| [TRANSITIONS.md](TRANSITIONS.md) | Composition changes mid-session: state classification, hot vs relaunch, rollback |
| [UX.md](UX.md) | Startup TUI, sessions, Doctor, workflow visibility, errors, runbook |
| [PLAN.md](PLAN.md) | Milestones, parallel lanes, velocity plan, quality checkpoints |
| [VERIFICATION.md](VERIFICATION.md) | No-provider/no-daemon evidence plan + user-performed live acceptance |
| [MIGRATION-ROLLBACK.md](MIGRATION-ROLLBACK.md) | Legacy sessions, working-tree reconciliation, HM generations, transcript safety |
| [DECISIONS.md](DECISIONS.md) | Every decision with evidence and alternatives rejected; user decision summary |
| [STATUS.md](STATUS.md) | Live milestone/evidence tracker |
| [SANITY.md](SANITY.md) | Design assessment (Q1–Q13): is the design sound? Verdict + named reservations + case studies |

For daily use and development, the product docs are now canonical:
[`home-manager/claude-multi/USAGE.md`](../../home-manager/claude-multi/USAGE.md)
(simple guide) and
[`home-manager/claude-multi/AGENTS.md`](../../home-manager/claude-multi/AGENTS.md)
(development guide).

## Hard rules inherited from the handoff (unchanged)

- No automated real-provider requests; no live-daemon contact/control.
- No commit, push, package install, or Home Manager activation without
  explicit user approval (asked once, at the boundary, with evidence).
- Never delete a transcript-bearing root; preserve rollback anchors.
- Plain `claude` stays behaviorally unaffected.
- Frozen C\* docs are evidence, never edited into the replacement.
