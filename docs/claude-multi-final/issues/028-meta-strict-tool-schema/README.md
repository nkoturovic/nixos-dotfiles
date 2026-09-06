# Issue 028 — Meta route rejects strict-incomplete tool schemas (HTTP 400)

**Status:** investigating (route behavior observed once via local journal;
no provider probe run yet — approval-gated).
**Reported:** 2026-09-06 by the operator, on a `muse-spark-contributor`
single-model lead session.
**Release:** none yet.

## Report

Stop-hook (session goal evaluator) call failed through the Meta route:

```text
Stop hook error: Hook evaluator API error: API Error: 400 'required' is
required to be supplied and to be an array including every key in
properties. Missing 'impossible'.
```

## Evidence (local only, no provider calls)

- Gateway journal (`journalctl --user -u cli-proxy-api`), 2026-09-06
  08:35:29: `POST "/v1/messages?beta=true" → 400 in 1.155s` on selector
  `claude-multi-muse-spark-contributor-high`. Fast rejection = upstream
  (Meta) refused the request; the gateway passed it through.
- Upstream error bodies are not journaled, so the exact offending schema
  was not directly observed — the shape is inferred from the message text
  (honesty boundary: inference, not proof).
- The `~/.claude/.agents/repos/claude-code` reference holds only the public
  repo front page (no CLI source), so the evaluator's schema construction
  could not be inspected locally.

## Suspected root cause

Meta enforces strict tool-schema validation: `input_schema.required` must
list **every** key in `input_schema.properties` (same class as OpenAI
strict-mode). The hook/goal evaluator's internal tool definition omits at
least one property key (one involving `impossible` — plausibly a
possible/impossible verdict schema) from `required`. Our gateway renders
tool schemas untouched, so there is nothing malformed on our side; the
rejection is a **route divergence**, in the same family as the known Meta
divergences (thinking always-on → 400, named/forced `tool_choice` → 400,
extra `refusal` stop_reason).

## Scope: route-specific, not model-specific

- Would hit `muse-spark` identically (same provider/adapter) and any future
  strict-schema route. Anthropic, Codex, Qwen, Kimi, DeepSeek, OpenRouter
  routes accept partial `required` and are unaffected.
- First noticed now most likely because this is the first evaluator call
  ever routed through Meta (the goal was created during a Meta-led
  session) — not because the contributor variant is special.

## Impact

- Functional: normal lead/agent traffic is unaffected (regular tool schemas
  from Claude Code carry complete `required` arrays, as the session itself
  proves — this whole session runs on the contributor variant).
- The session goal ("800K ceiling", verifiably done and activated at gen
  135) cannot auto-clear on Meta-led sessions because the evaluator call
  400s → the stop hook errors every stop. Workaround: clear the goal
  manually (`/goal clear`) — justified here because the work is complete,
  not as an early clear.

## Next steps

1. **Approval-gated Meta probe** (fold into the pending §6b probe, which
   must now include a tools/schema case, not just thinking/streaming):
   send a minimal tool definition with partial `required` through the
   Meta route and record the exact rejection; then a complete-`required`
   control. One call at a time, bounded, separately approved.
2. If confirmed: extend the Meta provider `support_note` divergence list
   (catalog edit → catalog-version bump) and reference this issue.
3. Possible mitigations to evaluate after confirmation (no decision yet):
   - documenting "goal auto-clear is unsupported on Meta-led sessions" and
     relying on manual clear;
   - a gateway-side schema-normalization boundary (HIGH RISK: rewriting
     `required` changes tool-call semantics — needs the same adversarial
     care as D60, and may be correctly rejected as out of bounds).
4. Do NOT "fix" by stripping evaluator properties or by special-casing
   model names — route-scoped or nothing (D60 precedent).

## Related

- Meta provider + muse models: D62, `HANDOFF-ASTRA-BATCH.md` §6b (probe
  still pending).
- Route-scoped sanitizer precedent: D60 / issue 026.
- 800K ceiling goal: D63, checkpoint `2026-09-06-v2.23.0` (done, activated
  gen 135).
