# Checkpoint 2026-08-19 · claude-multi v2.19.0/catalog19 — START HERE

You are picking up claude-multi at its 2026-08-19 **v2.19.0/catalog19**
checkpoint: **activated at Home Manager generation 129**, Claude pinned at
2.1.220, DeepSeek V4 Pro GA and exact-slug Grok 4.6 active, and all three
approval-gated acceptance calls green. This supersedes
[`../../2026-08-18-v2.18.0/handoff/README.md`](../../2026-08-18-v2.18.0/handoff/README.md).

## What changed (D58/D59, blueprints 024/025)

- Added `deepseek-pro`, stable first-party wire `deepseek-v4-pro` (currently
  Pro-0813), 1M `large` profile, high/max lanes. Flash keeps stable wire
  `deepseek-v4-flash` (Flash-0731).
- Replaced active Grok 4.5 with `grok46`, exact OpenRouter wire
  `x-ai/grok-4.6`, 500K `grok` profile, high fallback + xhigh default. The
  moving alias `~x-ai/grok-latest` remains deliberately untrusted under D3.
- Activated four validated mode-0600 compositions: Pro/Flash hybrid `deepseek`,
  preserved all-Flash `deepseek-flash`, Grok→Flash→Pro→Grok
  `grok-deepseek`, and the Pro-extended compatibility preset.
- Added the explicit OpenRouter `output_config.effort=xhigh` adapter contract;
  launcher 2.19.0, catalog 19.

## Acceptance and rollback truth

Attempt 1 activated catalog19 locally, then DeepSeek rejected the probe's named
forced `tool_choice` in thinking mode. The complete catalog+XDG+gateway
rollback was exercised successfully: history generation 127 points at the
catalog18 generation-125 store; rollback compositions were restored 0600 and
35 repairable scopes reconverged. No Grok call ran in that attempt.

After a reviewed amendment that preserved caller intent and changed only the
probe, attempt 2 passed exactly three separately approved calls, with no
retries and no moving-alias request:

1. DeepSeek Pro max: HTTP 200, `thinking` + one valid model-selected
   `tool_use`, 447 input / 108 output tokens.
2. Grok 4.6 high: HTTP 200, thinking/redacted-thinking/tool-use, 258/137.
3. Grok 4.6 xhigh streaming: HTTP 200 SSE, 63 ordered events, assembled tool
   payload correct, 271/152 (110 thinking).

These prove route/auth/effort-field/tool/stream shapes, not 1M/500K near-limit
context. Both models retain the conservative 200K catalog floor.

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles`, branch
  `feature/term-only`; implementation/evidence commits `cee0249`, `a4896e1`,
  `be958fc`, `c6a06c0`, plus this activation/checkpoint commit; nothing
  pushed.
- **Activated:** HM generation **129**; full catalog18 rollback generation
  **127**; generation 128 is functional catalog19 before the final evidence
  metadata flip.
- **Installed package:**
  `/nix/store/kv3359dlxvsc7zywp7756kkrwbfmgz29-claude-multi-2.19.0`.
- **Health:** gateway active, `/healthz` 200, six new aliases served, zero
  active Grok 4.5 references. `doctor --repair-all` converges 35/36 durable
  records; the sole BLOCKED item is the pre-existing ordinary record
  `d928f2a2…` with retired `sol` profile, intentionally left for the operator.
- **Evidence:** 1,680 host tests OK (2 skips), final package build and sandbox
  green, Qwen3.8 Max independent recovery review APPROVE, prior cross-family
  catalog/composition reviews green.
- **Docs:** DECISIONS through D59; blueprints 014–025; STATUS/HANDOFF/USAGE/
  UX/AGENTS current; wikis updated.

Verify with:

```bash
claude-multi --version
home-manager generations
claude-multi doctor
claude-multi models
systemctl --user is-active cli-proxy-api
cd /home/kotur/personal/nixos-dotfiles && git log --oneline -6
```

Expected doctor status is BLOCKED only for `d928f2a2…`; do not call it Ready
until the operator explicitly re-pins or forgets that record.

## Canonical docs and hard rules

Development rules live in `home-manager/claude-multi/AGENTS.md`; daily
operations in `docs/claude-multi-final/HANDOFF.md`; evidence ledger in
`STATUS.md`; rationale in `DECISIONS.md`; rollback in
`MIGRATION-ROLLBACK.md` and blueprint 024.

Standing rules remain binding: no real-provider call without explicit
per-call approval; never read transcripts or touch the Claude daemon; secrets
by name/count/length only; tests before claims; rollback never deletes state;
commits on `feature/term-only`, no push without approval; cross-family review
at meaningful boundaries.

Now go to [`open-items.md`](open-items.md).
