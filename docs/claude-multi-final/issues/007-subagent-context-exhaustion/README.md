# 007 — Subagent "Prompt is too long" (context exhaustion kills long legs)

**Status: open** (recovery wired in 2.8.0; prevention is delegation
discipline; the harness-level fix is upstream's)

## Found (2026-07-29, live during the 2.8.0 review)

The cross-family Sol reviewer (xhigh lane) died mid-verification:

```
Agent terminated early due to an API error: Prompt is too long
```

after ~80 tool calls re-reading large files (full diff + cli.py regions +
docs + tests). Sol variants carry the process-wide 372K context scalar,
so a Sol agent hits the ceiling well before a 1M-model agent would.

## Why it happens

Subagents at pin 2.1.220 have **no mid-run compaction**: when an agent's
accumulated context exceeds the model's window, the next API call fails
terminally and the agent dies. The main-session compaction machinery
(autoCompactEnabled) does not apply inside delegated agents at this pin.

## What is done about it

1. **Recovery (shipped, D42):** the lead contract's resume-over-redispatch
   rule — continue the dead agent by message with the failure stated and
   a steered instruction (e.g. "deliver the verdict from what you have,
   no more tool calls"). Applied live to this exact incident: the reviewer
   was resumed and asked for its verdict, not a fresh dispatch.
2. **Prevention (discipline):** bound delegated prompts and expected
   context (already in the lead appendix); split very large reviews by
   area so no single agent ingests the whole diff; prefer xhigh lanes
   only where the depth pays for the context cost.
3. **Not ours (documented):** subagent compaction is upstream harness
   behavior; nothing in claude-multi can add it. If a future pin compacts
   subagents, this issue closes.

## Note

This is the same failure family as issue 004 (a subagent dying mid-work)
with a different terminal cause: 004 = upstream API errors (watchdog now
retries them); 007 = context exhaustion (no retry possible — the request
can never succeed; recovery is resume-with-steer or redispatch).
