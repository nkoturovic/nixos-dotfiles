# 022 — DeepSeek & OpenRouter providers (V4 Flash, Grok 4.5)

## Context

Operator directive: connect two new providers, both Anthropic-compatible:

1. **DeepSeek** — `https://api.deepseek.com/anthropic`. The interesting
   model is **DeepSeek V4 Flash** — extremely fast, very cheap
   ($0.14/$0.28 per 1M), 1M context, thinking default-on — for
   exploration, analysis, side tasks, and bounded/selective
   implementation. Wire id `deepseek-v4-flash` is the latest-alias
   (currently DeepSeek-V4-Flash-0731); `deepseek-v4-pro` is preview, added
   at GA (D21/D50 flip sequence).
2. **OpenRouter** — `https://openrouter.ai/api` Anthropic skin. Target:
   **x-ai/grok-4.5** (500K context, $2/$6 per 1M under 200K prompt;
   $4/$12 at/above — tiered). The operator has no xAI subscription;
   OpenRouter is the route. The skin is only *guaranteed* with Anthropic
   first-party models — grok through it is probe-gated.

Secrets exist already: `DEEPSEEK_CLAUDE_API_KEY`,
`OPENROUTER_CLAUDE_API_KEY` in `~/.config/secrets/claude.env`.

## Verified research (workflow wf_ee1f27da; docs legs)

**DeepSeek** (Anthropic path, all verified against live docs):

- Auth: `x-api-key` header "Fully Supported" (Bearer uncertain → probe);
  `anthropic-version`/`anthropic-beta` ignored.
- Wire ids: `deepseek-v4-flash` (latest-alias; dated `…-0731` is a label,
  not a documented callable id); `deepseek-v4-pro` is preview (effort
  mapping unstable — an "early August" update note).
- Both models: 1M context native, max output 384K, thinking ON by default
  (default effort high).
- **Effort on the Anthropic path: `output_config.effort` ∈ {low, high,
  max}** — no medium, no xhigh (xhigh exists only as a pro-internal
  actual). `reasoning.effort` ∈ {none, low, high, max} separately toggles
  thinking OFF with `none`. `thinking.budget_tokens` ignored. Top-level
  `reasoning_effort` (the qwen contract param) is OpenAI-path only.
- Tool use fully supported (client-side); streaming fully supported;
  system/temperature/stop supported; **images/documents NOT supported**;
  `cache_control` ignored (DeepSeek's own implicit prefix cache instead);
  MCP server-side tool content types unsupported.
- claude-* name auto-mapping exists (opus*→pro, sonnet/haiku*→flash;
  anything else→flash) — a debugging signal, not a routing path.
- Listing: OpenAI-shape `GET /models` documented (Bearer). Anthropic-path
  listing undocumented → probe.
- Concurrency: flash 2500, pro 500 (429 past that). China-based provider,
  no region pinning; pricing increase announced; thinking tokens bill as
  output.

**OpenRouter** (Anthropic skin):

- Base `https://openrouter.ai/api` (SDK appends `/v1/messages`); auth
  x-api-key works (probe Bearer too); HTTP-Referer/X-OpenRouter-Title
  optional attribution.
- Non-Anthropic models DO flow through the skin in practice (third-party
  ran deepseek/* slugs), but a real failure mode exists: empty result
  events when content-block ordering is non-Anthropic (text before
  thinking). Grok reasons in xai-responses-v1 form → the streaming
  ordering probe gates the `lead` capability.
- Unified `reasoning` object {effort: max|xhigh|high|medium|low|minimal|
  none}; Grok models support reasoning effort. Skin→reasoning translation
  for non-Anthropic models is undocumented → probe.
- grok-4.5: slug `x-ai/grok-4.5`, 500K context, tiered pricing, reasoning
  supported, tool calling works via OpenRouter; max output unpublished
  (probe reads `top_provider.max_completion_tokens`).
- Listing is PUBLIC: `GET /api/v1/models` (no auth), single-model lookup
  `GET /api/v1/model/{author}/{slug}`; per-model `reasoning` object
  carries `supported_efforts`/`default_effort`/`mandatory`.
- Data policy: no prompt logging by default; no routing to training
  providers unless the user opts in. Paid models: no OpenRouter rate
  limit beyond credits + upstream.

## Design

### Catalog providers (`catalog/providers.json`)

- `deepseek` — family `deepseek`; adapter `cliproxy-claude-compatible-v1`;
  base_url `https://api.deepseek.com/anthropic`; auth header `x-api-key`
  → `env:DEEPSEEK_CLAUDE_API_KEY`; contracts: `output-config-high` (NEW),
  `output-config-max`.
- `openrouter` — family `x-ai` (aggregator transport; the review family
  tracks the model maker); adapter same; base_url
  `https://openrouter.ai/api`; auth header `x-api-key` →
  `env:OPENROUTER_CLAUDE_API_KEY`; contracts per probe outcome.

### New payload contract (`render.py` ADAPTER_PAYLOAD_CONTRACTS)

- `output-config-high`: override `output_config.effort: high` (claude
  protocol) — the deepseek-flash high-lane pin. (`output-config-max`
  already exists; `reasoning-effort-*` is OpenAI-shape-only for DeepSeek
  and never applies here.)

### Catalog models (`catalog/models.json`)

- `deepseek-flash` — provider deepseek; wire `deepseek-v4-flash`;
  display "DeepSeek V4 Flash"; 1M → `large` profile; lanes:
  - `high` (default): selector `claude-multi-deepseek-flash-high[1m]`,
    agent_effort high, contract `output-config-high`.
  - `max`: selector `claude-multi-deepseek-flash-max[1m]`, agent_effort
    max, contract `output-config-max`.
  Capabilities lead+agents; all four cm roles. Qualification:
  provider-documented 1M (docs verified 2026-08-11); near-limit behavior
  unverified until the approval-gated acceptance call.
- `grok-4.5` — provider openrouter; wire `x-ai/grok-4.5`; display "Grok
  4.5"; 500K → **new ordinary profile `grok`** (window 500K, trigger
  (500000−20000)×0.9 = 432000; selector `claude-multi-grok-4.5` — no
  `[1m]`, not a 1M-class model; the window comes from the profile-derived
  scope env). Lanes per probe (reasoning effort support); capabilities
  `lead` gated on the skin tool-use/streaming probe.

### Schema/enum extensions (the `grok` 500K profile)

- `schemas/models.schema.json` `ordinary_profile` enum gains `"grok"`.
- `schemas/session.schema.json` `context_profile` oneOf enum gains
  `"grok"` (alongside `"sol"`, `"large"`, `^custom-[0-9]+$`).
- Mechanics leg to confirm nothing else switches on profile names
  (profiles derive windows from member bounds; pickers render per-profile
  groups generically).

### Listing (`discover` / providers pane A)

- `proxy.list_provider_models` gains a per-provider listing descriptor
  (replacing the bare `_LISTING_SUPPORT` map): `{shape: anthropic|openai-
  style, url override, auth: required|none|bearer}`.
  - deepseek: try Anthropic-shape on the configured base first (probe
    pending); documented fallback is OpenAI-shape `GET
    https://api.deepseek.com/models` (Bearer).
  - openrouter: OpenAI-style `GET https://openrouter.ai/api/v1/models`,
    **no auth required** — the secret requirement must be relaxed for
    this provider. Rich fields (context_length, supported_parameters,
    reasoning.supported_efforts) feed the add-model flow.

### Compositions (operator-level, `~/.config/claude-multi/compositions/`)

- `deepseek` — all-flash rig (sol-direct shape + agent variants): lead
  deepseek-flash (max lane), analyst/implementer flash high, reviewer
  flash max. The cheap/fast side-task choice.
- `grok-deepseek` — lead grok-4.5; analyst+implementer deepseek-flash
  high; reviewer grok-4.5 (x-ai ≠ deepseek keeps review cross-family).
  Ships only if the grok skin probe passes; otherwise the grok entry is
  marked not-lead-capable and this composition waits.

## Approval-gated probes (one batch approval, pre-activation)

1. DeepSeek listing: `GET https://api.deepseek.com/anthropic/v1/models`
   (x-api-key) + `GET https://api.deepseek.com/models` (Bearer) — which
   listing works, advertised fields.
2. OpenRouter listing (public): `GET /api/v1/model/x-ai/grok-4.5` —
   context_length, top_provider.max_completion_tokens,
   supported_parameters, reasoning.{supported_efforts,default_effort,
   mandatory}, pricing.overrides (200K tier).
3. DeepSeek smoke (direct, pre-activation): tiny Anthropic message
   `deepseek-v4-flash` with x-api-key — endpoint+auth+wire smoke;
   variant with Bearer to settle the header question.
4. OpenRouter skin sanity (direct, pre-activation): tiny Anthropic
   message `x-ai/grok-4.5` with one client tool; a streaming variant
   checks block ordering (the empty-result failure mode); an effort
   variant checks `output_config.effort`/`reasoning` acceptance.

All probes are tiny (max_tokens ≤ 2048, < 15 requests, < $0.05).

## Probe results (2026-08-12, approval-gated, ~10 calls)

1. **DeepSeek listing**: the Anthropic path 404s (like qwen); the
   documented OpenAI-shape `GET https://api.deepseek.com/models` (Bearer)
   answers 200 with `{deepseek-v4-flash, deepseek-v4-pro}` (ids only — no
   context advertised). Descriptor updated: verified, url+auth:bearer+
   shape:openai overrides; the add flow asks for context (never guesses).
2. **OpenRouter grok-4.5 lookup** (public): context 500000 confirmed;
   `reasoning.mandatory: true` with supported_efforts [high, medium,
   low] (default high) — **high IS grok's top effort**, so the shipped
   single-high-lane shape is exactly right; `tools`/`tool_choice`
   supported; max completion unpublished.
3. **DeepSeek smoke**: 200 with x-api-key AND Bearer (both work; we ship
   x-api-key, kimi-shape); canonical thinking+text blocks; correct reply.
4. **OpenRouter skin (grok-4.5)**: well-formed tool_use
   (`stop_reason: tool_use`, parsed input) with thinking +
   redacted_thinking blocks (Claude-Code-native shapes); streaming event
   order canonical (message_start → block start → deltas → stops →
   message_delta/stop — no text-before-thinking inversion, the
   empty-result failure mode absent); `output_config.effort` high+max and
   `reasoning.effort=high` all accepted (200).

Post-probe catalog text flips: grok45 qualification/routing_note and both
provider support_notes now state verified behavior (probing date-stamped);
the qualification keeps "near-limit behavior unverified until a live
acceptance call" (true of every 1M-class entry until its acceptance run).

## As-built amendments (implementation findings)

- **Model id is `grok45`**, not `grok-4.5`: the composition slot pattern
  (`^[a-z0-9][a-z0-9-]*$`) rejects dots, and the catalog convention is
  squashed ids (qwen38, glm52, opus5). Selector `claude-multi-grok45`;
  wire stays `x-ai/grok-4.5`. The wire_model schema patterns (catalog +
  custom registry) gained exactly-one-`/segment` support for OpenRouter
  slugs (traversal shapes like `a/../b` verified rejected).
- **The lead enters via the default-lane contract**: catalog validation
  pins `client_selector == default lane's selector`. deepseek-flash's
  default lane is `high`, so an all-flash lead runs thinking-high (the
  flash-appropriate contract); the max lane covers the reviewer pass and
  agent escalation.
- **deepseek-flash in `large`**: the profile fence derives
  min(member bounds) — the ordinary-session window for the large profile
  stays 983616 (qwen38's bound) even though flash is 1M. The fence
  protects the smallest member, by design. Composition leads get their
  own bound (flash lead: 1M window, 882K trigger).
- **Compositions are operator-level** (`~/.config/claude-multi/
  compositions/deepseek.json`, `grok-deepseek.json`), authored through
  the validated store; repo pins construct the same documents inline.
- **grok45 lane set is probe-gated**: the catalog ships a single `high`
  lane with no contract until the skin probe establishes reasoning/effort
  passthrough for x-ai models; a max/xhigh lane (OpenRouter's reasoning
  object supports them) follows from the probe.
- **OpenRouter's listing is public**: the listing machinery grew an
  `auth: none` descriptor mode — no secret is resolved or required, and
  the pane's query modal says so.

## Review-driven amendments (cross-family sweep)

- **sol-xhigh leg → revise, all fixed + pinned**: (P1) the fetch-mark
  path keyed the registry by the raw wire id — OpenRouter's slashed ids
  are invalid state names, so every OpenRouter listing entry was
  unmarkable; `_registry_id_for_wire` now derives the basename
  (dash-joined fallback on collision). (P2) listing parse hardening: a
  200 envelope without `data` no longer reads as an empty success;
  wrongly-typed `name`/`context_length`/`max_completion_tokens` normalize
  instead of riding into the registry (JSON booleans excluded from int
  guards explicitly); deeply nested bodies (`RecursionError` at ~104 KiB)
  wrap into the redacted ProxyError boundary. (P3) the pane no longer
  reaches into the private `_LISTING_SUPPORT` table —
  `listing_supported`/`listing_is_public` helpers.
- **The same review exposed a 020-shipped defect**: the pane's multi
  picker passed no `on_toggle` and SelectList's multi-mode Enter returned
  None — the fetch-mark checkbox flow could never actually mark.
  SelectList now tracks toggles internally and multi-Enter returns the
  sorted toggled indexes (Esc still None); `_add_models` consumes it.
  First pane-level end-to-end pin covers the whole flow.
- **glm52 → approve** (no P0/P1): header hygiene nit fixed
  (`anthropic-version` is only sent to Anthropic-shape listings);
  deepseek qualification text made precise about the two window paths;
  a grok-profile min-bound guard was considered and deliberately skipped
  (the fence derives min(member bounds) — misclassification is
  conservative in every direction; the `large` guard exists for the
  `[1m]` selector classification grok doesn't use).

## Explicitly not in scope

- `deepseek-v4-pro` (preview — add at GA via the D21/D50 flip sequence).
- grok-code-fast-1 / grok-build-0.1 (noted, not requested).
- OpenAI-compatible upstream adapter (020 deferral stands — the Anthropic
  skins cover both providers).
- OpenRouter fallback chains / auto-routing config.

## Verification plan

- Catalog validation + golden render updates (bless, review the diff).
- New pins: provider/model schema conformance; `grok` profile fence math
  (500K → 432K trigger); selector emission (no `[1m]` on grok);
  composition resolution for both rigs; doctor radar covers the new
  aliases; listing descriptor shape tolerance (Anthropic vs OpenRouter
  shape, public auth).
- Full discovery + sandbox; cross-family review sweep; probe batch; then
  activation on the operator's green light.
