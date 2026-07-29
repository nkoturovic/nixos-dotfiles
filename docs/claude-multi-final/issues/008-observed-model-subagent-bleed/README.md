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

## Mechanism (verified from code, 2026-07-29)

The shim's SessionStart handler (`cli.py:_handle_session_event`) reads
`payload.model`; for managed sessions it reconciles only when the model
equals the lead's wire/selector — anything else lands in
`observed_model` (by design, to catch real `/model` switches). Claude
Code reports the model **of the context the event fired in** — and at
2.1.220 subagents fire their own lifecycle events (`hook_agent` is a
known querySource). A compacting Sol subagent (the first D40 reviewer
logged 277K tokens against Sol's 316,800 reactive trigger — it did
compact) emits SessionStart(source=compact) with ITS model; the shim,
invoked with the parent's `--managed-id`, attributes it to the parent
record. Everything observed follows: only sessions with compacting
subagents get flagged; the observed model is always a roster selector;
source is always "compact".

**Why the naive fix is wrong:** ignoring roster-selector models would
also hide a REAL `/model` switch to a roster model (D39's documented
escape hatch — backstopped by exactly this machinery). The fix must
distinguish the EVENT CONTEXT (agent vs main session), not the model
value — pending the payload-shape probe (does the compact payload carry
an agent/context marker? Offline probe against the pinned binary can
answer it).

## Interim

Model-only repair-needed is benign and self-healing: the next launcher
resume reconciles (allow-model-relaunch → unverified → authoritative on
the next start hook). Operators seeing this after heavy subagent use
should know it is likely this bleed, not a real model switch — this
issue stays open until the mechanism is confirmed and fixed.
