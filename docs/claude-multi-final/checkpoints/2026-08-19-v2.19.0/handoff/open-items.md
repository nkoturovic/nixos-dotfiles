# Open items — 2026-08-19 · v2.19.0/catalog19

Ordered. Standing rules from the checkpoint README remain binding.

## Now (operator decisions, not code)

- **5ee2f942 (historical issue 006):** restore from its backup or forget when
  the operator chooses; unchanged.
- **Key rotation hygiene:** rotate Kimi/Qwen keys only if the old local
  transcripts are ever synced/shared; no secret value belongs in docs.

## Soon (world-triggered evidence)

- **DeepSeek tool-choice watch:** normal Pro thinking + model-selected tool use
  is verified. If an unmodified Claude Code turn reproduces the forced
  `tool_choice` 400, capture metadata-only request context and then consider a
  caller-visible compatibility policy. Do not silently filter caller intent
  speculatively.
- **Near-limit context:** Pro 1M and Grok 500K remain docs-floor qualified at
  200K. Probe nearer the limits only after a real prompt-too-long signal and a
  new explicit per-call approval.
- **U1 takeover proof (L2)** and other older near-limit acceptances (Opus 5,
  Kimi 1M, Qwen 983616) remain world-triggered.
- **Sol 1M near-limit:** 343,541 is proven; probe ~900K–1M only if real failures
  recur. gpt55 retains its D56 fence until OpenAI documents equivalent 1M
  enablement.

## Deliberately not doing

- Do not wire `~x-ai/grok-latest`; its moving target violates D3 authority.
- Do not add a DeepSeek `tool_choice` filter after only the forced acceptance
  harness failure; model-selected tool use is green and caller intent wins.
- Do not promote `validated_tokens` from tiny route/tool calls; they did not
  exercise context ceilings.

## Watch (accepted reservations)

- Ordinary unmarked-compact bleed (issue 008 residual); issues 005/007;
  lead-slot lane support (D39); the documented test flakes; corrupt-record
  forget by runtime id friction; parsed-but-undisplayed
  `max_completion_tokens`; `cli.py` size.

## Done this cycle

- DeepSeek V4 Pro GA under stable alias; Flash→Pro production routing.
- Grok 4.6 exact-slug replacement with high/xhigh output-config contracts.
- Complete catalog+XDG failed-call rollback drill.
- Corrected Pro max thinking/tool acceptance and Grok high/xhigh streaming
  acceptance.
- Final 2.19.0/catalog19 activation at HM generation 129, checkpoint, docs,
  and evidence metadata.
- Obsolete retired-profile record `d928f2a2…` forgotten by explicit operator
  choice; only launcher record/generated scope removed, transcript retained;
  doctor Ready.
