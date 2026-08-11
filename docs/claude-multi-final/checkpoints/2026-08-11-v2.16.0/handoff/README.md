# Checkpoint 2026-08-11 · claude-multi v2.16.0 — START HERE

You are picking up the claude-multi project at its 2026-08-11 **v2.16.0**
checkpoint: **activated (HM gen 123), doctor Ready, Claude pinned at
2.1.220 (hash-verified, symlink-aligned). The deep six-lane system
analysis batch is landed: every P1/P2 from the core/tui/gateway/compose/
hygiene/security sweep is fixed with regression pins, and the
cross-family review resolutions are in (sol-xhigh block → fixed;
glm52 approve → nits fixed).** Supersedes
[`../../2026-08-11-v2.15.0/handoff/README.md`](../../2026-08-11-v2.15.0/handoff/README.md).
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
`/model`; the custom registry (`custom.json`) adds operator providers and
ordinary-only models through the providers pane. Since 2.16.0 (D53,
blueprint 021): corrupt records forget load-free; a renamed project dir
is a `cwd-missing` resume gate with completable recovery steps;
`sessions forget` refuses live/self sessions **under the lifecycle lock**
(shared by CLI and picker); managed 1M-lead `wire+'[1m]'` hook reports
reconcile; the SessionStart hook survives catalog drift; a 401 from
`/v1/models` is a doctor problem naming restart; credential fetches never
follow redirects; custom-registry mutations run under one FileLock with
OAuth-pool/header guards and loud shadow-merge drops; fully-wired
unserved rows are marked `(not served)`; cycling away from unsaved edits
asks first; and `package.nix` keeps test `__pycache__` out of the store.
The default composition leads **Opus 5**; plain `claude` is never touched.

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles` branch `feature/term-only`,
  HEAD `216c434` (the 2.16.0 batch incl. review fixes; nothing pushed).
- **Activated:** Home Manager generation **123**; entrypoints report
  `2.16.0` (catalog 16). Rollback: gen 122/121.
- **Health:** `claude-multi doctor` → **Ready**; 33 durable sessions
  converged by `doctor --repair-all`; gateway radar silent (no 401/drift).
- **Evidence:** 1,631 host tests OK (skipped=2); sandbox
  `nix-build package.nix` green with zero `.pyc`/`__pycache__` in the
  output (the new cleanSourceWith filter); cross-family review:
  sol-xhigh **block** (under-lock forget liveness incl. picker, durable
  pointer sweep, completable cwd-missing remedy — all fixed + re-pinned),
  glm52 **approve** (picker-bypass P2 + nits fixed).
- **Docs:** DECISIONS through D53; blueprints 014–021; USAGE/HANDOFF/
  AGENTS/UX/STATUS/SANITY current; gateway-ops skill carries the 401
  radar note; wikis updated.

Verify with:

```bash
claude-multi --version            # 2.16.0
claude-multi doctor               # Ready
claude-multi custom list          # the custom registry (empty until used)
claude-multi compose list         # MRU-first, last-used column
cd /home/kotur/personal/nixos-dotfiles && git log --oneline -4
cd home-manager/claude-multi && PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
```

## Canonical docs (living — always prefer these over copies)

| What | Where |
| --- | --- |
| **Development guide (agents)** | [`home-manager/claude-multi/AGENTS.md`](../../../../../home-manager/claude-multi/AGENTS.md) |
| **User guide (humans)** | [`home-manager/claude-multi/USAGE.md`](../../../../../home-manager/claude-multi/USAGE.md) |
| **Standalone Claude guide** | [`home-manager/claude-multi/STANDALONE.md`](../../../../../home-manager/claude-multi/STANDALONE.md) |
| **Design assessment** | [`../../../SANITY.md`](../../../SANITY.md) |
| **Design package** | [`../../../`](../../../README.md) — SPEC, TRANSITIONS, UX, DECISIONS (through D53), blueprints/ |
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
