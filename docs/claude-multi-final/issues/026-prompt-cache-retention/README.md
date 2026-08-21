# 026 — Non-Claude routes leak `prompt_cache_retention` (HTTP 400)

**Status: resolved** · fixed and activated in 2.20.0/catalog20, HM generation 130 (D60)

## Report

A Sol-led session routed correctly through `gpt-multi-sol-high` to the
ChatGPT/Codex subscription backend returned HTTP 400
`prompt_cache_retention is not supported on this model` on several
cache-marked requests, while thousands of otherwise identical Sol requests
succeeded. The failure is request-shape-dependent — not a context-window,
selector, lane, or scope-fence defect. The same field also leaked through
ordinary `/v1/messages?beta=true` traffic.

`prompt_cache_retention` is an OpenAI Responses-platform cache TTL control
("24h", etc.). It is supported by official OpenAI platform Responses
routes but rejected by the Codex subscription backend, third-party
Claude-compatible endpoints, and xAI.

## Root cause

CLIProxyAPI 7.2.80 strips the field only in a scattered set of early
cleanup steps (Codex ordinary `/responses`, Codex WebSocket non-stream,
xAI preparation). Audit of the pinned source found:

| Route | Unpatched 7.2.80 |
| --- | --- |
| Codex HTTP `/responses` (stream + non-stream) | stripped early — passes |
| Codex HTTP `/responses/compact` | **leaks** (no strip at all) |
| Codex WebSocket non-stream `response.create` | stripped early — passes |
| Codex WebSocket stream `response.create` | **leaks** (no strip at all) |
| Claude-compatible third-party (Kimi/Qwen-GLM/DeepSeek/OpenRouter), `/v1/messages` stream + non-stream + `count_tokens` | **leaks** (no strip anywhere in the Claude executor) |
| xAI HTTP + WebSocket | stripped early — passes |
| OpenAI-compatible platform Responses | preserved by design (supported) |

The loopback fake-upstream matrix on an unpatched writable copy of the
pinned 7.2.80 source reproduced exactly the five leak cells and confirmed
the four pass cells (see verification below).

The ordinary `/v1/messages?beta=true` live leak cannot be attributed to one
exact leaking function in the ordinary Codex path — that path's early
cleanup already appears to strip the field, and the request transcript
shape is not available to this incident (transcripts are never read).
**The exact ordinary live leakage function remains unproven.** What is
proven is that the class exists (five deterministic leak cells), so the
fix is an outbound-boundary invariant, not a hunt for one function.

## Fix

One focused patch
(`home-manager/cli-proxy-api-non-claude-cache-retention.patch`) against
pinned CLIProxyAPI 7.2.80, generated with context-bearing hunks against
the sequentially patched source (loopback-OAuth + Kimi-compat + opus-5
first, exactly the order the Nix module applies them):

- New shared, idempotent helper `stripPromptCacheRetention` removes **all**
  top-level `prompt_cache_retention` occurrences — including duplicate
  keys, which a single `sjson.DeleteBytes` cannot cover — preserves
  `prompt_cache_key` and nested/unrelated fields, and fails closed with an
  enforced postcondition: the returned body must be valid JSON and
  provably free of the top-level field, otherwise the request errors
  instead of sending an unprovable body. Empty and nil bodies fail closed
  as invalid JSON.
- Codex: sanitized inside `CodexExecutor.cacheHelper` after cache-key
  insertion and identity rewriting, immediately before HTTP request
  construction — covers ordinary stream/non-stream and
  `/responses/compact`, including fields reintroduced by payload rules or
  identity confusion.
- Codex WebSocket: sanitized in both `Execute` and `ExecuteStream` after
  identity rewriting, before request logging/frame transmission.
- Claude executor: sanitized after translation, payload rules, cloaking,
  and Claude-message normalization for third-party Claude-compatible base
  URLs (all current Kimi, Qwen/GLM, DeepSeek, and OpenRouter routes);
  stream, non-stream, and count-tokens. On the message paths the strip
  runs **before CCH signing**, so the signature covers the sanitized body
  and no later transformation can reintroduce the field (count_tokens
  strips after its final sanitizer; it has no signing step).
  Classification is **endpoint-first**: only the resolved official HTTPS
  `api.anthropic.com` (default/443 port, case-insensitive host, any path)
  or the default empty base URL is preserved; every custom, non-HTTPS,
  non-default-port, or malformed base URL fails closed as third-party
  **regardless of token shape** — an OAuth-shaped token never upgrades a
  non-official endpoint.
- xAI: the early retention cleanup in `prepareResponsesRequestTo` is
  **removed and folded** into the shared boundary strip at the end of the
  final preparation — one invariant for both HTTP and WebSocket, and the
  xAI tests now exercise the boundary as the only strip.
- `openai_compat_executor.go` is **not modified**: official/platform-
  compatible Responses routes support the field and are the negative guard
  against global stripping.
- No model-name, alias, lane, or context-profile conditions anywhere.

Paths deliberately outside the boundary, noted accurately: the
server-level Codex `/alpha/search` passthrough and plugin management
routes (`internal/api/server.go`) and the Claude OAuth token refresh
(`ClaudeExecutor.Refresh`) do not carry messages/responses payloads and do
not pass through the executor body pipeline; no retention TTL producer
reaches them, and they are not modified.

The patch carries its own Go regression suite (loopback fakes and
injected round-trippers only, no external network): helper semantics
including duplicate-key removal, empty/nil-body fail-closed, and the
fail-closed postcondition; endpoint classification; Codex
ordinary/compact/WebSocket; identity-confusion ordering; third-party
Claude-compatible strip (including OAuth-shaped tokens on custom base
URLs and malformed-body no-leak); CCH-signing ordering — two end-to-end
tests (non-stream and stream, custom base URL + OAuth-shaped token)
assert retention is absent and the emitted CCH recomputes exactly over
the sanitized outbound body; official-endpoint preservation; xAI; and
OpenAI-compatible preservation.

## Verification

- Unpatched pinned 7.2.80 (writable copy under /tmp, scratch demo tests):
  compact, WS-stream, and all three Claude third-party tests **FAIL**
  (leak confirmed); Codex ordinary HTTP, WS non-stream, xAI, and
  OpenAI-compat preservation tests **PASS** (already covered) — the honest
  contradiction bounding the gap.
- Patched tree: all 28 new tests pass (repeated runs and `-race` clean);
  full `go test ./...` = 77 packages OK, 0 failures. Patch applies to the
  sequentially patched pinned source with GNU `patch -p1` (the tool Nix's
  patchPhase uses) byte-exactly — context-bearing hunks land with zero
  offset/fuzz, no rejects, and the resulting tree is byte-identical to the
  development tree.
- Nix sandbox gate: the cli-proxy-api override now runs
  `go test ./internal/runtime/executor` in `postCheck` (the upstream
  checkPhase only tests `subPackages` = cmd/server, so this gate is what
  makes the patched tests run in the build). Build logs show the echoed
  gate marker and the executor package `ok` line; vendored modules and
  `GOPROXY=off` — no external network.
- claude-multi gates: full Python discovery green; render/compiler/scope
  goldens byte-identical (no model/provider/composition/context/schema
  change — the only catalog delta is the patch-manifest line and the
  version bump). Patch-manifest consistency now compares exact ordered
  lists (module order == manifest order), including opus-5.
- Known unrelated pre-existing flakes (observed on the unpatched base
  too): `TestCodexWebsocketsUpstreamDisconnectChanSignalsOnInvalidate`
  fails intermittently under `-count` repetition, and
  `TestAntigravityAuthHasCreditsRequiredHomeBalanceUsesKV` fails on
  repeated runs (its KVGet counter is not reset between `-count`
  iterations). Both predate this patch and are not touched by it. The
  retention suite itself is stable across repeated and `-race` runs.
- Activation (2026-08-21): HM generation 130, gateway restart, health and
  both model-list auth forms green, 37 selectors intact, doctor Ready. The
  active gateway binary is the exact Nix-tested derivation and contains the
  sanitizer symbol; its build log proves the executor regression gate ran.
  No real-provider call was needed. Generation 129 is the rollback anchor.

## Claim boundary

The patch closes every identified leak cell and enforces the outbound
invariant for all current non-Claude routes. It does **not** claim to
reproduce the exact ordinary live leak site — that remains unproven; the
invariant closes the class regardless of which function reintroduced the
field live. Fail-closed is proven wherever the boundary can observe
malformed JSON (helper unit tests, Codex `cacheHelper` end-to-end); the
Claude pipeline repairs malformed client JSON upstream of the boundary
itself, and its end-to-end test pins the resulting invariant — the
upstream body is never sent retention-bearing either way.
