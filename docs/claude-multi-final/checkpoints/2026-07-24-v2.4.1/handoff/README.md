# Checkpoint 2026-07-24 · claude-multi v2.4.1 — START HERE

You are picking up the claude-multi project at its 2026-07-24 **v2.4.1**
checkpoint: **activated (HM gen 99), doctor fully clean (Ready, zero
Attention lines), managed binary re-pinned to 2.1.218, the update loop is a
one-command (or one-keypress) routine with a TUI health/update surface.**
This checkpoint supersedes [`../../2026-07-24-v2.3.0/handoff/README.md`](../../2026-07-24-v2.3.0/handoff/README.md) (same day, earlier activation).
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
contract is **layered**: the packaged contract is the reviewed baseline and
a strictly-newer operator override (written by `claude-multi update` after
its evidence gate) wins while newer. `claude-gateway`/`direct` is the
ordinary mode; plain `claude` is never touched; its upstream auto-updater
stays on while the managed side anchors.

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles` branch `feature/term-only`
  (series `d65218d` → current HEAD; nothing pushed).
- **Activated:** Home Manager generation **99**; entrypoints report `2.4.1`.
  Rollback: gen 98 (2.4.0), gen 97 (2.3.0).
- **Health:** `claude-multi doctor` → **Ready** with **zero** Attention
  lines: `Managed Claude 2.1.218 verified`; configured symlink resolves to
  the inspected artifact; **15 sessions, 15 durable, 0 legacy**.
- **Evidence:** **1,194 host tests OK** (1 intentional skip); offline package
  build green; sandbox suite green; gateway `cli-proxy-api` active
  (`127.0.0.1:8317`, healthz 200 after the activation restart); hook shim
  targets the activated package; CLIProxy render == live config byte-identical;
  every catalog alias served; TUI health/update surface verified via PTY.

Verify with:

```bash
claude-multi --version            # 2.4.1
claude-multi doctor               # Ready, no Attention lines
claude-multi update               # "nothing to re-pin" (idempotent)
cd /home/kotur/personal/nixos-dotfiles && git log --oneline -8
```

## Canonical docs (living — always prefer these over copies)

| What | Where |
| --- | --- |
| **Development guide (agents)** | [`../../../../../home-manager/claude-multi/AGENTS.md`](../../../../../home-manager/claude-multi/AGENTS.md) |
| **User guide (humans)** | [`../../../../../home-manager/claude-multi/USAGE.md`](../../../../../home-manager/claude-multi/USAGE.md) |
| **Design assessment** | [`../../../SANITY.md`](../../../SANITY.md) |
| **Design package** | [`../../../`](../../../README.md) — BLUEPRINT, SPEC, TRANSITIONS, UX, DECISIONS (D26–D30), VERIFICATION, MIGRATION-ROLLBACK |
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
