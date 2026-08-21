# State snapshot — 2026-08-21 · v2.20.0/catalog20

## Activation

- Home Manager switch exited 0; generation 130 current, 129 rollback.
- `claude-multi --version` and `claude-multi-proxy --version`: 2.20.0.
- Gateway service active/running, main status 0; `/healthz` HTTP 200.
- Bearer and x-api-key `/v1/models`: HTTP 200, 37 selectors, all required
  Sol/Kimi/Qwen/GLM/DeepSeek/Grok aliases present.
- `claude-multi doctor`: Ready; 36 recorded/durable, 0 legacy, no collisions.
- Config sha256 unchanged:
  `53ddd9c7d7d0fffd93d6e674cc65aaab2015e01776cf8ff44adc0a8b6167d392`.

## Installed artifacts

- HM generation:
  `/nix/store/przzykwfdpqj754gxrys1zvch8cfglhd-home-manager-generation`.
- Launcher:
  `/nix/store/q6sggf2sry5655yh8b0qkgv5aa37dd7f-claude-multi-2.20.0`,
  sha256 `8ad3d015c7e9fe513e1266a78ef6f4185b3417e55ee1acc813723dd80bb7cb77`.
- Gateway:
  `/nix/store/kgbjv4g2smg5768anqbnf7yiqcyinrf6-cli-proxy-api-7.2.80`,
  sha256 `bf236023c9dd6433d7f882bf93a1ee75adffe3ffa3d69c182dd74f5a012df349`.
- Gateway derivation:
  `/nix/store/ksqf3rq4xqb6aw8qxx56a1dm89m6r08z-cli-proxy-api-7.2.80.drv`;
  binary contains `stripPromptCacheRetention`; build log includes:
  `cli-proxy-api: executor regression gate` and executor package `ok`.
- Sandbox check:
  `/nix/store/0m2gx92ph15mcsb1n3ym34i89cfl1ykk-claude-multi-tests`.

## Root cause and route matrix

`prompt_cache_retention` is an OpenAI Responses-platform TTL control rejected
by the ChatGPT/Codex subscription backend. CLIProxyAPI 7.2.80 had scattered
early cleanup and deterministic gaps in Codex compact, Codex WS stream, and
third-party Claude-compatible paths. The exact ordinary live leak function is
unproven; the final-boundary invariant closes the class.

- Codex HTTP/compact: strip in `cacheHelper` after cache-key/identity changes.
- Codex WebSocket: strip after identity changes, before frame/log/write.
- Third-party Claude-compatible: endpoint-first strip after normalization and
  before CCH signing; count-token path strips after its final sanitizer.
- xAI: strip at shared final preparation.
- Official Anthropic and OpenAI-compatible platform routes: preserve field.

## Verification and review

- Unpatched matrix: five deterministic leak cells reproduced; four already-safe
  cells and preservation negatives passed.
- New retention suite: 28 tests, repeated/race clean; duplicate keys, empty/nil
  malformed JSON, CCH recomputation, identity ordering, endpoint classification,
  Codex ordinary/compact/WS, Claude stream/non-stream/count, xAI, and OpenAI
  preservation covered.
- Full CLIProxyAPI: 77 tested packages green, 0 failures.
- Full claude-multi host discovery: 1,680 tests OK, 2 skips.
- Nix sandbox: 1,680 tests OK (secret-dependent tests skipped by boundary).
- Package, sandbox check, and Home Manager activation-package builds green.
- Context-bearing patch applies in exact four-patch manifest order with zero
  offset/fuzz/reject; ordered manifest tests pin this contract.
- Final Qwen3.8 Max cross-family review: APPROVE.

Known upstream test flakes under repeated whole-package runs are documented in
issue 026 and reproduce on the unpatched base; the retention suite itself is
stable. No provider call was used for activation acceptance.
