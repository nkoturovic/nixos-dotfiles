# 008 — observed_model bleed: compact event attributes a subagent's model

**Status: open** (needs mechanism verification; reconcile path works)

## Found (2026-07-29, immediately after the 2.8.0 activation)

`claude-multi doctor` flagged exactly one session as repair-needed:

```
session 7fa62138-… identity is repair-needed (observed model
gpt-multi-sol-xhigh differs from the recorded claude-multi-kimi-k3[1m])
```

That session (the composition lead's own) is a kimi-sol session whose
LEAD never changed model. Its record shows `last_event_source: compact`
at 2026-07-29T10:22:22Z — a compaction event right after a morning of
dozens of `cm-reviewer-sol-xhigh` / `cm-analyst-sol-high` dispatches.
The observed model is exactly the preferred reviewer's selector, which
is no default of anything.

## Hypothesis (mechanism to verify)

Session-wide hooks (SessionStart/SessionEnd via the scope settings)
fire in subagent contexts too at 2.1.220 (`hook_agent` is a known
querySource). A hook event emitted in a subagent's context carries THE
SUBAGENT'S model; the shim attributes it to the managed record's
`observed_model`, which then correctly flags a session that never
switched. If true, every heavy-subagent session eventually ends up
repair-needed through no fault of the operator — a genuine
mis-attribution, surfacing now because 2.8.0's relink_message makes it
visible everywhere (card, doctor, gate).

## What to check (investigation path)

1. Does the shim receive hook events from subagent contexts (the
   `hook_agent` querySource), and does it record their `model` field as
   the session's observed_model? Read the shim's event handling and
   2.1.220's hook payload for agent contexts.
2. If confirmed: the identity machinery should only accept model
   evidence from the main session context (or explicitly ignore
   agent-context events); add radar so a single subagent can never mark
   a session repair-needed.
3. Whether the `compact` source specifically carries the compaction
   turn's model vs the session model.

## Interim

Model-only repair-needed is benign and self-healing: the next launcher
resume reconciles (allow-model-relaunch → unverified → authoritative on
the next start hook). Operators seeing this after heavy subagent use
should know it is likely this bleed, not a real model switch — this
issue stays open until the mechanism is confirmed and fixed.
