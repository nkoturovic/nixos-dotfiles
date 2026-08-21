# Checkpoint 2026-08-21 · claude-multi v2.20.0/catalog20 — START HERE

claude-multi **2.20.0/catalog20** is active at Home Manager generation 130.
This release fixes intermittent HTTP 400
`prompt_cache_retention is not supported on this model` failures seen on a
Sol lead and enforces the same invariant for every current managed non-Claude
route. It supersedes
[`../../2026-08-19-v2.19.0/handoff/README.md`](../../2026-08-19-v2.19.0/handoff/README.md).

## What changed (D60, issue 026)

One independently removable patch against pinned CLIProxyAPI 7.2.80 removes
all top-level `prompt_cache_retention` occurrences at the final outbound
boundary for:

- Codex HTTP, compact, and WebSocket routes (Sol/gpt55);
- third-party Claude-compatible routes (Kimi, Qwen/GLM, DeepSeek, OpenRouter);
- xAI HTTP/WebSocket routes.

The policy is transport/endpoint-scoped, never model-name-scoped. Official
Anthropic endpoints and OpenAI-compatible platform Responses preserve the
field. The sanitizer is fail-closed, handles duplicate keys, preserves
`prompt_cache_key`/nested data, and runs before Claude CCH signing so the
signature covers the sanitized body.

## Recorded state

- **Source:** `/home/kotur/personal/nixos-dotfiles`, branch
  `feature/term-only`; implementation commit `4a3b07b` plus this activation
  checkpoint commit; nothing pushed.
- **Activated:** HM generation **130**
  (`/nix/store/przzykwfdpqj754gxrys1zvch8cfglhd-home-manager-generation`).
  Full rollback: generation **129**.
- **Installed launcher:**
  `/nix/store/q6sggf2sry5655yh8b0qkgv5aa37dd7f-claude-multi-2.20.0`.
- **Installed gateway:**
  `/nix/store/kgbjv4g2smg5768anqbnf7yiqcyinrf6-cli-proxy-api-7.2.80`;
  sha256 `bf236023c9dd6433d7f882bf93a1ee75adffe3ffa3d69c182dd74f5a012df349`.
- **Health:** gateway active, `/healthz` 200, both local model-list auth forms
  200, 37 selectors with all required aliases, config parity unchanged,
  `claude-multi doctor` Ready, 36 durable records/scopes, 18 compositions.
- **Evidence:** 28 new executor tests stable under repetition/race; full
  `go test ./...` 77 packages green; 1,680 Python tests green; package,
  sandbox, and Home Manager activation-package builds green; Nix build log
  proves the executor regression gate ran.
- **Review:** Sol-xhigh findings fixed in two passes; final independent
  Qwen3.8 Max review re-ran the route matrix and all gates: APPROVE.

No provider call, transcript access, secret output, or state rewrite was needed.

## Verify

```bash
claude-multi --version                 # 2.20.0
home-manager generations               # 130 current, 129 rollback
systemctl --user is-active cli-proxy-api
claude-multi doctor                     # Ready
```

Canonical details: `STATUS.md`, `DECISIONS.md` D60,
`issues/026-prompt-cache-retention/`, product `AGENTS.md`, `USAGE.md`, and
`STANDALONE.md`.

Now go to [`open-items.md`](open-items.md).
