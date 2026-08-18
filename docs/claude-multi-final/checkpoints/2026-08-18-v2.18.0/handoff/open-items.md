# Open items — 2026-08-18 · v2.18.0

Ordered. The standing rules from the checkpoint README remain binding.
Carried over from 2.17.0 unless noted.

## Now (operator decisions, not code)

- **d928f2a2 (retired 'sol' profile, ordinary session, workspace/news):**
  resume with `claude-gateway -r d928f2a2-ecd9-41f0-b414-b828b74887f8
  --model sol` to re-pin to `large` (or `sessions forget` — the record is
  healthy, the profile is gone).
- **5ee2f942 (issue 006):** unchanged — restore from backup or forget.
- **Key rotation (hygiene):** rotate `KIMI_CLAUDE_API_KEY`/
  `QWEN_CLAUDE_API_KEY` only if transcripts are ever synced/shared.

## Soon (world-triggered acceptances)

- **U1 takeover proof (L2)**; **near-limit acceptances (Opus 5, Kimi 1M,
  Qwen 983616)** — unchanged.
- **Sol 1M near-limit**: the acceptance probe verified 343,541; a
  near-limit (~900K-1M) probe stays optional — watch for prompt-too-long
  recurrence in real sessions first (none expected past 258.4K).
- **gpt55**: if OpenAI documents a 1M enablement for it too, mirror D57
  (its D56 fence stands until then).

## Deferred with triggers (unchanged from 2.17.0)

- deepseek-v4-pro (preview → GA flip); per-model independence_family
  override (first second-family model on an aggregator); OpenAI-compatible
  custom providers; editor enable-and-set-lead confirm; transition
  snapshot history; file ingestion into transition; auto init/restart from
  the TUI; custom-registry orphan flagging; catalog scope-exceeds repair
  text; grok-code-fast-1 / grok-build-0.1.

## Watch (accepted reservations)

- Ordinary unmarked-compact bleed (008); issues 005/007; lead-slot lane
  support (D39); the two documented test flakes (AGENTS.md §4);
  corrupt-record forget by runtime id re-raises (friction);
  `max_completion_tokens` parsed-but-undisplayed; `cli.py` size.

## Done this cycle

- 2.17.0 (gen 124): DeepSeek + OpenRouter providers; descriptor listing;
  D55 multi-route; D56 sol codex correction.
- 2.18.0 (gen 125): sol 1M class (D57/023) with the acceptance probe
  green; retired-profile-aware doctor hint.
