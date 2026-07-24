# Checkpoint 2026-07-24 · claude-multi v2.3.0 — START HERE

You are picking up the claude-multi project at its 2026-07-24 checkpoint:
**v2.3.0 activated, doctor Ready, full documentation in place, live state
healthy.** Everything you need is wired below. Read in this order:

1. This file (5 min) — what the state is and how to verify it.
2. [`open-items.md`](open-items.md) — the next work, ordered.
3. [`state-snapshot.md`](state-snapshot.md) — the evidence behind this checkpoint.
4. The two canonical product docs (linked below) when you start changing things.

## The project in 90 seconds

claude-multi is a stdlib-only Python launcher that compiles a chosen
**composition** (a lead model + generated `cm-*` agent variants) into a
**per-session durable scope** (`~/.local/state/claude-multi/scopes/<managed-id>/`)
and `execve`s a hash-verified Claude Code binary with `--add-dir` +
`--settings`. Durable on-disk agents survive supervisor takeovers (the failure
this project exists to fix). Schema-v3 records separate stable `managed_id`
from Claude's `runtime_session_id`, reconciled by metadata-only
SessionStart/SessionEnd hooks invoked through a **stable hook shim**
(`~/.local/state/claude-multi/bin/claude-multi-hook` — never a store path).
`claude-gateway`/`direct` is the ordinary (no-composition) gateway mode;
plain `claude` is never touched.

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles` branch `feature/term-only`
  at commit `08a3335` (series `d65218d` → `08a3335`; nothing pushed).
- **Activated:** Home Manager generation **97**; profile package
  `/nix/store/v8h7y3nzj84zqsw4j71ff14jjzg0y4l2-claude-multi-2.3.0`;
  all three entrypoints report `2.3.0`. Rollback: gen 96 (2.2.0).
- **Health:** `claude-multi doctor` → **Ready** (one Attention line for the
  last legacy record `9bc5fd42`, which keeps its transcript).
- **Evidence:** 1,167 host tests green (1 intentional skip); offline package
  build green; Nix sandbox suite green
  (`/nix/store/ixfmr7f59x6l73yr7p5xhap57hj62cnc-claude-multi-tests`);
  cross-family review resolved (REVISE → fixed).
- **Live state:** 16 resumable sessions (15 durable + 1 legacy); scopes embed
  the stable shim; gateway `cli-proxy-api` active (`127.0.0.1:8317`);
  backup of the pre-repair state at `/tmp/cm-live-backup-20260724-110010`.

Verify with:

```bash
claude-multi --version            # 2.3.0
claude-multi doctor               # Ready
cd /home/kotur/personal/nixos-dotfiles && git log --oneline -8
cd home-manager/claude-multi && PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
```

## Canonical docs (living — always prefer these over copies)

| What | Where |
| --- | --- |
| **Development guide (agents)** | [`home-manager/claude-multi/AGENTS.md`](../../../../../home-manager/claude-multi/AGENTS.md) — doctrine invariants, module contracts, workflow, rules |
| **User guide (humans)** | [`home-manager/claude-multi/USAGE.md`](../../../../../home-manager/claude-multi/USAGE.md) — tools, use cases, FAQ |
| **Design assessment** | [`../../../SANITY.md`](../../../SANITY.md) — is the design sound? Q1–Q13 with evidence |
| **Design package** | [`../../../`](../../../README.md) — BLUEPRINT, SPEC, TRANSITIONS, UX, DECISIONS, VERIFICATION, MIGRATION-ROLLBACK |
| **Live ledger** | [`../../../STATUS.md`](../../../STATUS.md) — milestone history; update it at your milestones |
| **Operator handoff (daily ops)** | [`../../../HANDOFF.md`](../../../HANDOFF.md) — daily use + live-repair records |
| **Checkpoints index** | [`../../README.md`](../../README.md) (the checkpoints index) — checkpoint convention + list |
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

Now go to [`open-items.md`](open-items.md).
