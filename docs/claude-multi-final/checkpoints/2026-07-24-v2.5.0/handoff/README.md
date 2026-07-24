# Checkpoint 2026-07-24 · claude-multi v2.5.0 — START HERE

You are picking up the claude-multi project at its 2026-07-24 **v2.5.0**
checkpoint: **activated (HM gen 101), Opus 5 is the default lead, doctor
fully clean (Ready, zero Attention lines), gateway serves Opus 5, and the
update loop is a one-command/one-keypress routine.** Supersedes
[`../../2026-07-24-v2.4.1/handoff/README.md`](../../2026-07-24-v2.4.1/handoff/README.md).
Read in this order:

1. This file — what the state is and how to verify it.
2. [`open-items.md`](open-items.md) — the next work, ordered.
3. [`state-snapshot.md`](state-snapshot.md) — the evidence.
4. The canonical product docs (linked below) when you start changing things.

## The project in 90 seconds

claude-multi is a stdlib-only Python launcher that compiles a chosen
**composition** (a lead model + generated `cm-*` agent variants) into a
**per-session durable scope** (`~/.local/state/claude-multi/scopes/<managed-id>/`)
and `execve`s the hash-verified Claude binary with `--add-dir` +
`--settings`. Durable on-disk agents survive supervisor takeovers. Schema-v3
records separate stable `managed_id` from Claude's `runtime_session_id`,
reconciled by metadata-only hooks through the stable hook shim. The native
contract is **layered** (packaged baseline + strictly-newer operator
override via `claude-multi update`). The default composition leads **Opus 5**
(Anthropic's 2026-07-24 release) with GPT 5.6 Sol preferred variants and
Kimi K3 alternates; `claude-gateway`/`direct` is the ordinary mode; plain
`claude` is never touched.

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles` branch `feature/term-only`
  at `9181a58`+ (series `d65218d` → current HEAD; nothing pushed).
- **Activated:** Home Manager generation **101**; entrypoints report
  `2.5.0`; CLIProxyAPI rebuilt with the `claude-opus-5` registry patch.
  Rollback: gen 100, gen 99.
- **Health:** `claude-multi doctor` → **Ready** (zero Attention lines);
  `Managed Claude 2.1.218 verified`; **15 sessions, all durable, 0 legacy**.
- **Gateway:** `/v1/models` serves `claude-opus-5` + `claude-multi-opus-5`
  (plus all prior aliases); config is byte-identical to a fresh catalog
  render; live verification call to `claude-multi-opus-5` PASSED
  (consent-gated, exact-instruction, clean `end_turn`).
- **Profiles (7, all verified):** `default` (opus5+sol+kimi), `opus-sol`
  (opus5+sol), `opus-kimi` (opus5+kimi), `fable`, `kimi-sol`, `qwen-sol`,
  `sol-direct` — each print-launch checked for the correct lead model.
- **Evidence:** **1,194 host tests OK** (1 intentional skip); offline
  package build (`2.5.0`); sandbox suite green
  (`/nix/store/kmqj7vx3aka29cinww3gf981nl43jrfp-claude-multi-tests`).

Verify with:

```bash
claude-multi --version            # 2.5.0
claude-multi doctor               # Ready, no Attention lines
claude-multi --composition opus-sol --print-launch
curl -s http://127.0.0.1:8317/v1/models -H "Authorization: Bearer $(cat ~/.config/claude-multi/api-key)" | grep opus-5
```

## Canonical docs (living — always prefer these over copies)

| What | Where |
| --- | --- |
| **Development guide (agents)** | [`../../../../../home-manager/claude-multi/AGENTS.md`](../../../../../home-manager/claude-multi/AGENTS.md) |
| **User guide (humans)** | [`../../../../../home-manager/claude-multi/USAGE.md`](../../../../../home-manager/claude-multi/USAGE.md) |
| **Design assessment** | [`../../../SANITY.md`](../../../SANITY.md) |
| **Design package** | [`../../../`](../../../README.md) — BLUEPRINT, SPEC, TRANSITIONS, UX, DECISIONS (through D31), VERIFICATION, MIGRATION-ROLLBACK |
| **Live ledger** | [`../../../STATUS.md`](../../../STATUS.md) |
| **Operator handoff (daily ops)** | [`../../../HANDOFF.md`](../../../HANDOFF.md) |
| **Checkpoints index** | [`../../README.md`](../../README.md) |
| **Global wiki project page** | `~/.agents/wiki/projects/claude.md` |
| **Local wiki (~/.claude project)** | `~/.claude/.agents/wiki/index.md` |

## Hard rules (inherited, still binding)

1. No real-provider calls without explicit user approval, per call.
2. Never touch the live Claude daemon/supervisor; never read user
   transcripts; never delete anything under `~/.claude`.
3. Tests before claims (full discovery + package build + sandbox suite).
4. Coherent commits on `feature/term-only`; no push without approval;
   Home Manager activation needs user approval.
5. One batched cross-family review at meaningful boundaries (Sol reviews
   Kimi/Qwen-authored work and vice versa).
6. Simplicity budget: no new mode/schema/daemon/state without a
   demonstrated failure case.
7. Keep the map current: STATUS at milestones, next checkpoint at
   meaningful boundaries, pointers never copies.

Now go to [`open-items.md`](open-items.md).
