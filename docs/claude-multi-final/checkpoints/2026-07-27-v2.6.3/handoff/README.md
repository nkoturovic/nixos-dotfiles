# Checkpoint 2026-07-27 · claude-multi v2.6.3 — START HERE

You are picking up the claude-multi project at its 2026-07-27 **v2.6.3**
checkpoint: **activated (HM gen 106), doctor Ready with zero Attention
lines, Claude pinned at 2.1.220 (hash-verified, symlink-aligned), fork
lifecycle fully operable, gateway routing durable across daemon takeovers,
the update flow proven end-to-end, and the codebase adversarially hardened
by an 8-reviewer fan-out plus a final cross-family gate (D37).**
Supersedes
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
hook shim. v2.6 closed the last operability gaps found by a live incident:
**native forks** (created when a menu reattaches to a background-owned
session) self-clear when authority lands on them, are visible in the picker
(⚠/● markers, `(fork)` native rows), resolve in one keypress (X) or one
command (`sessions resolve-fork`), and every message names the fork UUID +
exact remedy. **Gateway routing is durable**: compiled settings carry the
non-secret base URL + an `apiKeyHelper` shim, so daemon-relaunched sessions
keep working (the daemon scrubs `ANTHROPIC_*` from children). **Updates**
are phase-narrated (`[N/M]` + heartbeat), serialized, and — after D36 —
the evidence gate validates the *new* contract (promotion syncs the
suite's version pins). The default composition leads **Opus 5**; plain
`claude` is never touched (its setup/behavior is documented in
`STANDALONE.md`).

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles` branch `feature/term-only`
  (series through `0314ba9` + the activation commit; nothing pushed).
- **Activated:** Home Manager generation **106**; entrypoints report
  `2.6.3`. Rollback: gen 105/104.
- **Health:** `claude-multi doctor` → **Ready, zero Attention lines**;
  `Managed Claude 2.1.220 verified` (hash-verified; symlink resolves to
  the inspected artifact); the redundant override was removed by the
  designed cleanup after the baseline landed.
- **Sessions:** 17 durable records, all scopes converged to the v2.6 shape;
  both incident fork records (`58c87cef`, `a048b8f0`) cleared and
  `authoritative`. Flows re-verified live (state-snapshot §flows).
- **Docs:** USAGE (all use cases + compose management + tiering),
  STANDALONE (plain-Claude setup/behavior), AGENTS (development), design
  package (DECISIONS through D36).

Verify with:

```bash
claude-multi --version            # 2.6.3
claude-multi doctor               # Ready, zero Attention
cd /home/kotur/personal/nixos-dotfiles && git log --oneline -8
cd home-manager/claude-multi && PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
```

## Canonical docs (living — always prefer these over copies)

| What | Where |
| --- | --- |
| **Development guide (agents)** | [`home-manager/claude-multi/AGENTS.md`](../../../../../home-manager/claude-multi/AGENTS.md) |
| **User guide (humans)** | [`home-manager/claude-multi/USAGE.md`](../../../../../home-manager/claude-multi/USAGE.md) — all use cases, forks, tiering, compose management |
| **Standalone Claude guide** | [`home-manager/claude-multi/STANDALONE.md`](../../../../../home-manager/claude-multi/STANDALONE.md) — machine setup, daemon, native forks, update channel |
| **Design assessment** | [`../../../SANITY.md`](../../../SANITY.md) — through v2.6.x |
| **Design package** | [`../../../`](../../../README.md) — SPEC, TRANSITIONS, UX, DECISIONS (D32–D36) |
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
