# 020 — Custom providers & ordinary models (mark-to-enable)

## Context

Operator vision, in two messages: (1) a marking UI over models — checkboxes
for "enabled" ones; for listing-capable providers (Kimi, verified) list the
provider's models and mark which to make available; for non-listing
providers keep current + manual type-in (type-in available for all); marked
models become usable in sessions / model presets. (2) an option to add an
API endpoint + key (Anthropic-compatible primarily, OpenAI-compatible
perhaps later) — i.e. add a NEW PROVIDER from the TUI, then list its
models, enable them, etc.

## Doctrine boundary

Composition admission stays a catalog act (context bounds, lanes, effort
contracts, role gates, qualification). Customs — providers and models — are
**ordinary-session only** (gateway-launched, profile-fenced). That is
exactly the "model presets" use case; composition use goes through the
`claude-multi-dev model add --like` scaffold (019).

## Design

1. **Registry** `~/.config/claude-multi/custom.json` (0600,
   `schemas/custom.schema.json`): `providers` + `models` sections.
   Provider entry: `{base_url, auth_kind (bearer|header), header?,
   secret_env, display?}`. Model entry: `{wire_model, provider,
   context_tokens, display?, created_via}`.
2. **Custom providers** render as direct `claude-api-key` sections
   (Anthropic-compatible transport only in v1; the registry schema leaves
   room for a later OpenAI-compatible adapter — the gateway's other section
   types are a separate render surface). Auth resolves from the standard
   secret env file (`secret_env` names a variable; the masked key entry
   already writes it). `payload_contracts` empty (no effort overrides);
   `passthrough_routes` empty; family `custom`.
3. **Custom models** carry the safety fact `context_tokens` (compaction
   math). Each distinct bound = one picker group/fence (`custom · 256K
   context`); same-bound customs switch in-session via typed /model.
4. **Listing in the add flow**: any direct provider can be probed with the
   Anthropic-shape `GET {base}/v1/models` — an explicit fetch confirm
   keypress is the per-call approval. Verified matrix: kimi works; qwen
   returns 404 (skip straight to manual); custom providers attempt, then
   fall back to manual type-in on any failure. OAuth pools: never.
5. **TUI (providers pane)**: custom provider rows render alongside catalog
   ones (credential = env key present; rendered/served counts through the
   same snapshot). **N new provider** prompts id/base-url/auth/env-name →
   masked key entry. **A add models** on a provider row → fetch-or-manual
   checkbox marking (multi-select) → registry write + the apply rule
   (`claude-multi-proxy init` + restart, between turns). **D** on a custom
   picker row removes it (confirm). Line mode: `claude-gateway --model
   <custom-id>` works; the `g` listing shows custom groups.
6. **Merge points (complete list)**: `Runtime.ordinary_docs` (models +
   providers merged) feeds every ordinary-resolution call
   (prepare_direct, pickers, listings, switch/adopt/relink profile
   computations, the ordinary scope recompile); the render path (proxy
   init + doctor snapshot) merges the same — so the served/drift radar
   covers customs automatically (an unapplied add shows as config drift).
   Managed composition resolution never sees the merge.

## Explicitly not in v1

- Composition use of customs; OpenAI-compatible upstream adapter (schema
  leaves room); auto init/restart; editing catalog entries through the
  registry; fetch caching (every probe asks).

## Files

- `src/claude_multi/custom.py`: registry IO, synthetic provider/model
  entries, merge helpers. `schemas/custom.schema.json`.
- `cli.py`: `ordinary_docs`, picker sections + D-removal, pane N/A flows.
- `proxy.py`: render merge (init path); `list_provider_models` learns the
  generic direct-provider attempt (kimi verified, qwen known-unsupported,
  customs attempt-then-manual).
- Tests: registry roundtrip/schema, synthetic shapes, render golden with a
  custom provider+model (separate golden), picker section/launch/removal,
  add flows (fetch-mark, manual validation, fallback), doctor coverage.
