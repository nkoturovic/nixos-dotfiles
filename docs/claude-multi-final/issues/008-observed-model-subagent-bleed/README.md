# 008 — observed_model bleed: compact event attributes a subagent's model

**Status: resolved** · fixed in 2.8.3 (D44)

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

## Mechanism (inference from the production record + binary path)

**Status: inference, not runtime-verified.** The record shows
`last_event_source: compact` and `observed_model` equal to the preferred
reviewer's selector — nothing else. The handler code proves only how
such a model lands in `observed_model` (any non-lead model is recorded
as drift by design). The original write-up asserted the subagent
compacted and emitted the event; a later fixture attempt to reproduce a
subagent compaction never produced one (subagents may not compact at
2.1.220 at all — see issue 007), so the exact pollution path (subagent
compact event vs. a parent compact event carrying a stale model
attribution from the last active subagent) is NOT established. The D44
managed rule (compact model/cwd evidence inadmissible) is correct under
either path.

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


## Resolution (D44, 2.8.3)

The 2.1.220 compact payload carries NO agent-context marker
(probe-verified shape: cwd, hook_event_name, session_id, source,
transcript_path), so the fix is by session type: MANAGED sessions (the
production bleed case) treat compact model/cwd evidence as inadmissible
unconditionally — a managed model change is a transition (its start
event reports the model) or is re-observed at the next start/resume
(the flag self-heals, observed live on this very session). Marker
branches (agent_id / agent_transcript_path / /subagents/) stay as
defense-in-depth for any payload shape that carries them. ORDINARY
sessions keep compact-model reconciliation (their in-session /model
tracking is load-bearing); an unmarked compact bleed there is the
documented accepted residual — no discriminator exists at this pin and
no such case has been observed. A fixture reproduction of a subagent
compaction was attempted and did not produce one (possibly subagents
do not compact at 2.1.220, consistent with issue 007's open question);
the managed rule does not depend on it. Tests: SubagentModelBleedTests
+ SubagentModelBleedOrdinaryTests pin the behavioral branches.
