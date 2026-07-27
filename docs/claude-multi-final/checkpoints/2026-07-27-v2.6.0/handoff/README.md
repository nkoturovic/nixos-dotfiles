# Checkpoint 2026-07-27 · claude-multi v2.6.0 — START HERE

You are picking up the claude-multi project at its 2026-07-27 **v2.6.0**
checkpoint: **activated (HM gen 103), doctor Ready, fork lifecycle fully
operable, gateway routing durable across daemon takeovers, update runs
narrate themselves.** Supersedes
[`../../2026-07-24-v2.5.0/handoff/README.md`](../../2026-07-24-v2.5.0/handoff/README.md).
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
hook shim. v2.6.0 closed the last operability gaps found by a live incident:
**native forks** (created when a menu reattaches to a background-owned
session) now self-clear when authority lands on them, are visible in the
picker (⚠/● markers, `(fork)` native rows), resolve in one keypress (X) or
one command (`sessions resolve-fork`), and every message names the fork UUID
+ exact remedy. **Gateway routing is durable**: compiled settings carry the
non-secret base URL + an `apiKeyHelper` shim, so sessions relaunched by the
background daemon (which scrubs `ANTHROPIC_*` from children) keep working
instead of failing with `invalid model`. The default composition leads
**Opus 5**; plain `claude` is never touched.

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles` branch `feature/term-only`
  (series through `9bebd14` + the activation commit; nothing pushed).
- **Activated:** Home Manager generation **103**; entrypoints report
  `2.6.0`. Rollback: gen 102/101.
- **Health:** `claude-multi doctor` → **Ready**; one Attention line for the
  2.1.220 upstream release (re-pin is the operator's U/`update` decision).
  All 17 durable scopes converged to the v2.6 shape via `--repair-all`.
- **Live fork resolutions:** `58c87cef` (opus-sol) and `a048b8f0`
  (sol-direct) were both fork-blocked by the incident class; both cleared
  (metadata-only; transcripts untouched).
- **Evidence:** 1,233 host tests OK (2 provider-secret skips) ×3
  consecutive; PTY 19/19; sandbox suite OK; package builds; cross-family
  Sol review **approved** (one should-fix swept in, three nits fixed);
  gateway accepts bearer + x-api-key (apiKeyHelper-compatible); token shim
  output verified byte-equal to the token file (hashes only).

Verify with:

```bash
claude-multi --version            # 2.6.0
claude-multi doctor               # Ready
cd /home/kotur/personal/nixos-dotfiles && git log --oneline -6
cd home-manager/claude-multi && PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
```

## Canonical docs (living — always prefer these over copies)

| What | Where |
| --- | --- |
| **Development guide (agents)** | [`home-manager/claude-multi/AGENTS.md`](../../../../../home-manager/claude-multi/AGENTS.md) |
| **User guide (humans)** | [`home-manager/claude-multi/USAGE.md`](../../../../../home-manager/claude-multi/USAGE.md) — forks, option tiering, /model rationale |
| **Design assessment** | [`../../../SANITY.md`](../../../SANITY.md) — through v2.6.0 |
| **Design package** | [`../../../`](../../../README.md) — SPEC, TRANSITIONS, UX, DECISIONS (D32–D35 are the v2.6 decisions) |
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
