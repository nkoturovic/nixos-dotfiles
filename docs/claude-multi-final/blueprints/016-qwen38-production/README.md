# 016 — Qwen3.8 Max production flip

## Context

Qwen3.8 Max went GA on **2026-08-03**. The catalog still carries the preview:
`wire_model: qwen3.8-max-preview`, display `Qwen3.8 Max · Preview`, and a
routing note with the preview-revision checklist. **D21** scripted this exact
moment; **D45** recorded that at production qwen38 moves ahead of glm52 as the
preferred escalation. Operator direction (2026-08-10): "preview should be
removed and replaced with qwen-3.8-max (which is actual - released model)";
positioning "on par with GPT 5.6 Sol".

Verified against primary/secondary sources (2026-08-10):

- Production wire id on the Token Plan `apps/anthropic` endpoint is
  **`qwen3.8-max`** — the official Alibaba Claude Code doc's Token Plan
  examples now use `ANTHROPIC_MODEL=qwen3.8-max`
  (help.aliyun.com/en/model-studio/claude-code).
- `CLAUDE_CODE_MAX_CONTEXT_TOKENS=983616` remains the doc-recommended value →
  catalog context numbers stay unchanged (983616 client/provider/scalar).
- `reasoning_effort` tiers remain low/high/xhigh with **xhigh the provider
  maximum** (nothing above xhigh at GA) → lane `max` contract
  `reasoning-effort-xhigh` stays.
- Vendor-reported benchmarks: PaperBench 93.0 (Sol 90.5, Fable 88.8);
  SWE-bench Pro 67.7 (Fable 80.0); HLE 43.6 (Fable 53.3). Supports
  "on par with Sol" as routing language; Fable stays the finalizer class.

## Changes (D21 sequence)

1. **`wire_model`** — `catalog/models.json` qwen38: `qwen3.8-max-preview` →
   `qwen3.8-max`. Surgical string edit only (no JSON re-serialization).
2. **Context re-verification** — done from the official doc: 983616 stands;
   `qualification` text rewritten to reference the production doc and drop
   preview wording; near-limit real-provider behavior stays marked unverified
   until the acceptance call (item 4).
3. **Effort tier check** — done: no tier above xhigh; lanes untouched.
4. **One live verification call** — bounded single request through the local
   gateway selecting `qwen3.8-max`; **explicitly approval-gated** (working
   agreement 1) and performed only at activation time.
5. **Display** — `Qwen3.8 Max · Preview` → `Qwen3.8 Max`; `routing_note`
   rewritten: production model, parity-with-Sol escalation language, PREVIEW
   checklist removed (D21/D45 remain the historical record).

## Slot-order flip (D45 note executed)

qwen38 ahead of glm52 in every user composition listing both (per-role slot
order = alternate preference). Files (`~/.config/claude-multi/compositions/`,
0600): `kimi-sol-qwen-glm.json`, `kimi-sol-qwen-glm-fable.json`,
`fable-sol-qwen-glm.json`. `glm-sol` / `qwen-sol` list one model each —
unchanged. Sol stays the preferred workhorse variant everywhere; this flip
only orders the two max-lane escalations. No hint changes (glm52/qwen38 keep
byte-identical role hints — parity was about *capability language*, order now
expresses the preference).

## Ripple surface

- `tests/goldens/render/gateway-default.yaml` — embeds the preview wire id;
  re-bless after the catalog edit; delta must be qwen38-wire-only.
- `tests/test_catalog.py` — add pins: no `preview` substring anywhere in the
  catalog bundle; qwen38 wire_model == `qwen3.8-max`.
- `catalog_version` 14 → **15** (`version.json` at activation).
- Agent variant display strings (`Qwen3.8 Max · Preview` in generated scopes)
  converge via `doctor --repair-all` at activation.
- Live gateway: HM switch re-renders + restarts `cli-proxy-api` (standing
  restart rule) — the preview route disappears.
- Docs: DECISIONS **D50** (flip executed + evidence links), STATUS entry,
  USAGE model table, HANDOFF profiles line.

## Verification

- `test_catalog` + `test_render` (golden delta inspected), full unittest
  discovery, sandbox `nix build`.
- `claude-multi doctor` after activation: catalog/compositions/gateway valid.
- Approval-gated live call: one bounded gateway request asserting the served
  wire id is `qwen3.8-max` (journal selector line) — recorded in STATUS.

## Non-goals

- No new models from the GA roster (qwen3.7-max, qwen3.6-flash) — that's
  blueprint 015's discovery surface, with its own catalog entries if adopted.
- No preferred-variant promotion for qwen38 (Sol stays preferred; escalation
  order only).
