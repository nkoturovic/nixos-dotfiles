# Checkpoint 2026-08-12 · claude-multi v2.17.0 — START HERE

You are picking up the claude-multi project at its 2026-08-12 **v2.17.0**
checkpoint: **activated (HM gen 124), doctor Ready, Claude pinned at
2.1.220 (hash-verified, symlink-aligned). DeepSeek and OpenRouter are
catalog providers; the sol codex-route budget correction (D56) is live —
sol subagent prompt-too-long failures should be gone.** Supersedes
[`../../2026-08-11-v2.16.0/handoff/README.md`](../../2026-08-11-v2.16.0/handoff/README.md).
Read in this order:

1. This file — what the state is and how to verify it.
2. [`open-items.md`](open-items.md) — the next work, ordered.
3. [`state-snapshot.md`](state-snapshot.md) — the evidence.
4. The canonical product docs (linked below) when you start changing things.

## The project in 90 seconds

claude-multi is a stdlib-only Python launcher that compiles a chosen
**composition** (a lead model + generated `cm-*` agent variants) into a
**per-session durable scope** and `execve`s the hash-verified Claude binary.
Schema-v3 records separate stable `managed_id` from Claude's
`runtime_session_id`, reconciled by metadata-only hooks through the stable
hook shim. Ordinary gateway sessions (`claude-gateway` / card **G**) run
plain Claude through the local CLIProxyAPI gateway with profile-fenced
`/model`; the custom registry adds operator providers/models. Since
2.17.0 (D54–D56, blueprint 022): **DeepSeek** (x-api-key,
`api.deepseek.com/anthropic`) serves `deepseek-flash` (the latest-alias,
1M, lanes high/max via `output_config.effort`) and **OpenRouter**
(Anthropic skin) serves `grok45` (`x-ai/grok-4.5`, 500K in its own `grok`
profile — no `[1m]`); compositions `deepseek` (all-flash side-task rig)
and `grok-deepseek` (grok lead + flash agents) are operator-level.
Listing is descriptor-driven (`_LISTING_SUPPORT`, frozen): OpenRouter's
is public, DeepSeek's is the documented OpenAI-shape `/models` (Bearer).
D55 is the multi-route convention (one catalog entry per model×route,
route-scoped context, provider-scoped discover, duplicate route-wire
guard). D56 corrected the sol/gpt55 codex-route fence to 258,400
effective (July 2026 route cuts; trigger 214,560; revert path recorded).
The default composition leads **Opus 5**; plain `claude` is never touched.

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles` branch `feature/term-only`,
  HEAD `2f49567` (`1ff5a1e` batch · `c08dd77` probes · `2f49567` review
  resolutions + D55/D56; nothing pushed).
- **Activated:** Home Manager generation **124**; entrypoints report
  `2.17.0` (catalog 17). Rollback: gen 123/122.
- **Health:** `claude-multi doctor` → **Ready**; 33 durable sessions
  converged onto catalog 17 (including the D56 sol fence); gateway radar
  silent.
- **Evidence:** 1,662 host tests OK (skipped=2); sandbox suite green;
  approval-gated probe battery green 2026-08-12 (DeepSeek smoke both
  auth headers; OpenRouter skin tool-use/streaming/effort verified);
  deep review: glm52 approve + qwen38 revise → all confirmed findings
  fixed (invisible fetch-mark toggles, listing-modal URL, ledger flips).
- **Docs:** DECISIONS through D56; blueprints 014–022; USAGE/HANDOFF/
  AGENTS/UX/STATUS current; gateway-ops skill carries the listing matrix;
  wikis updated.

Verify with:

```bash
claude-multi --version            # 2.17.0
claude-multi doctor               # Ready
claude-multi compose list         # deepseek + grok-deepseek present
claude-multi models               # deepseek-flash, grok45 rows
cd /home/kotur/personal/nixos-dotfiles && git log --oneline -5
cd home-manager/claude-multi && PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
```

## Canonical docs (living — always prefer these over copies)

| What | Where |
| --- | --- |
| **Development guide (agents)** | [`home-manager/claude-multi/AGENTS.md`](../../../../../home-manager/claude-multi/AGENTS.md) |
| **User guide (humans)** | [`home-manager/claude-multi/USAGE.md`](../../../../../home-manager/claude-multi/USAGE.md) |
| **Standalone Claude guide** | [`home-manager/claude-multi/STANDALONE.md`](../../../../../home-manager/claude-multi/STANDALONE.md) |
| **Design assessment** | [`../../../SANITY.md`](../../../SANITY.md) |
| **Design package** | [`../../../`](../../../README.md) — SPEC, TRANSITIONS, UX, DECISIONS (through D56), blueprints/ |
| **Live ledger** | [`../../../STATUS.md`](../../../STATUS.md) |
| **Operator handoff (daily ops)** | [`../../../HANDOFF.md`](../../../HANDOFF.md) |
| **Checkpoints index** | [`../../README.md`](../../README.md) |
| **Global wiki project page** | `~/.agents/wiki/projects/claude.md` |

## Hard rules (inherited, still binding)

1. No real-provider calls without explicit user approval, per call.
2. Never touch the live Claude daemon/supervisor; never read user
   transcripts; never delete anything under `~/.claude`.
3. Tests before claims (full discovery + package build + sandbox suite).
4. Coherent commits on `feature/term-only`; no push without approval;
   Home Manager activation needs user approval.
5. One batched cross-family review at meaningful boundaries (Sol reviews
   Kimi-authored work and vice versa).
6. Simplicity budget: no new mode/schema/daemon/state without a
   demonstrated failure case.

Now go to [`open-items.md`](open-items.md).
