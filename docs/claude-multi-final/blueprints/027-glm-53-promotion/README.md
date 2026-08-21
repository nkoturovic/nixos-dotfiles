# 027 — GLM-5.2 → GLM-5.3 in-place promotion (stable compatibility identity)

## Context

GLM-5.3 is the new Z.ai GLM iteration; the user attests it is enabled in
their Alibaba/Qwen Token Plan subscription. Official Z.ai documentation
(2026-08-21) confirms the release contract:

- exact model ID `glm-5.3`
- 1,000,000-token context, 128K max output
- text-only, always-on reasoning, low/high/max effort (default max)
- Anthropic Messages API: tools and streaming supported.

The Alibaba Token Plan web allowlists fetched 2026-08-21 still stop at
GLM-5.2 and predate the release, so the source migration is built from
official model facts + user-attested route availability; one separately
approved bounded live call must verify the Alibaba route before
activation. Validated context stays 200K; near-limit 1M/128K-output
behavior remains unverified until that acceptance. The operator positions
GLM-5.3 in the same general-capability tier as Qwen3.8 Max and GPT-5.6 Sol;
existing GLM/Qwen role hints are already identical, so parity needs routing
wording rather than composition or prompt churn.

## Design: promote in place, never rename

The trusted catalog entry `glm52` is modified in place (wire/display/
declared context/evidence) while every compatibility identity stays
stable:

| Field | Before | After |
| --- | --- | --- |
| catalog key | `glm52` | `glm52` (unchanged) |
| selector | `claude-multi-glm52-max[1m]` | unchanged |
| gateway alias | `claude-multi-glm52-max` | unchanged |
| wire | `glm-5.2` | **`glm-5.3`** |
| display | `GLM-5.2` | **`GLM-5.3`** |
| declared context | 1,048,576 | **1,000,000 (exact official)** |
| client/provider context | 1,000,000 | unchanged |
| validated floor | 200,000 | unchanged |
| provider | `qwen` | unchanged |
| lanes / effort | single `max`, `reasoning-effort-max` | unchanged |
| family / roles | alibaba; lead+agents | unchanged |

This is the Qwen3.8 Preview → production pattern (D50): stable catalog
identity and selector, new wire/display/evidence. GLM-5.2 did **not**
replace GLM-5.1 in this repository (GLM-5.2 was the first GLM entry), so
D45 remains the historical GLM-5.2 decision, unmodified.

No second GLM model, compatibility subsystem, schema, provider, endpoint,
secret, or payload contract is added. Renaming the key/selector is
explicitly rejected: it would rewrite the 17 records/scopes and 11 live
composition files that pin `glm52`. Keeping identity makes this ordinary
catalog drift — catalog20 records stay valid and resolvable. Current catalog
routing removes the old Qwen-ahead-of-`glm52` wording and names GLM-5.3,
Qwen3.8 Max, and GPT-5.6 Sol as peers; existing explicit preferred flags and
composition files remain unchanged.

## Release shape

- Catalog-only: catalog 20 → 21; launcher stays 2.20.0.
- Render golden: exactly two lines move (`glm-5.2` → `glm-5.3`,
  display `GLM-5.2` → `GLM-5.3`). Every compiler/scope golden stays
  byte-identical.
- No composition or state migration; historical docs, checkpoints, D45,
  and catalog18 rollback fixtures keep saying GLM-5.2.

## Tests

- `test_catalog`: stable identity/selector pins, exact GLM-5.3 wire and
  display, exact 1M context values, 200K floor, unchanged max contract,
  qualification evidence boundary, no active GLM-5.2 wire/display field.
- `test_render`: `glm-5.3` renders to stable alias `claude-multi-glm52-max`,
  alibaba owner, 1M context, `reasoning_effort=max`; no rendered glm-5.2.
- `test_proxy` disposable loopback Qwen route (fake upstream only, no
  Alibaba contact): bearer auth, alias → exact `glm-5.3` wire, max
  effort, offered-tool preservation, no invented `tool_choice`, D60
  retention stripping.
- `test_compiler`/`test_context`: `glm-5.3` wire resolution to `glm52`;
  GLM 1M/882K lead policy; large-profile selector tuple unchanged.
- `test_cli`: current presentation shows GLM-5.3; ordinary model id
  `glm52`, session name `cg:glm52`, and the selector stay.

## Acceptance and activation gates (separate approvals)

1. **Live route acceptance (before activation):** one bounded Alibaba
   Token Plan call — existing `apps/anthropic` endpoint and bearer
   credential, `model: glm-5.3`, `reasoning_effort: max`, streaming,
   ~512 max tokens, one harmless marker tool strongly requested but no
   forced `tool_choice`, no thinking disablement. Pass: HTTP/SSE valid,
   GLM-5.3 identity, thinking block, valid stream ordering, one valid
   tool-use block, appropriate stop reason, max effort accepted. Any
   failure blocks activation and must not fall back to GLM-5.2.
2. **Activation:** run a metadata-only GLM liveness census and require zero
   live/mid-turn ordinary sessions with `ordinary_model: glm52`, zero live GLM
   leads, and zero live managed sessions with an enabled GLM variant. Any
   nonzero result blocks activation until that process exits or is stopped
   through the supported operation; do not cross the stable-alias remap in a
   live session. Record generation 130 rollback; activate Home Manager;
   re-render/restart the gateway; verify health, config parity, exact
   `glm-5.3` wire, no active `glm-5.2` wire/display, stable served alias, and
   unchanged max override. Run `doctor --repair-all` only after the zero-live
   gate and local route checks pass; record generation/package/config/census
   evidence and create the catalog21 checkpoint (no additional provider call).

## Rollback

Before any post-activation GLM request: activate generation 130 and
restart the gateway; no composition restoration is needed. After GLM-5.3
has been used, rollback is semantically sensitive because stable
identity `glm52` would map back to GLM-5.2 — stop GLM-bearing sessions
and prefer fixing forward. Records, scopes, credentials, compositions,
and transcripts are never deleted or rewritten.
