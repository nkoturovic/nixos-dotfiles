# Checkpoint 2026-08-10 · claude-multi v2.13.0 — START HERE

You are picking up the claude-multi project at its 2026-08-10 **v2.13.0**
checkpoint: **activated (HM gen 120), doctor Ready, Claude pinned at
2.1.220 (hash-verified, symlink-aligned), qwen3.8-max production live-verified,
MRU-first composition picking, on-the-fly `--composition-file` ingestion, the
providers pane (P in the G picker) with masked key entry, and the doctor
gateway radar (served-vs-rendered + config byte-drift + OAuth-record
disambiguation).** Supersedes
[`../../2026-07-27-v2.7.0/handoff/README.md`](../../2026-07-27-v2.7.0/handoff/README.md).
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
plain Claude through the local CLIProxyAPI gateway with profile-scoped
`/model`. v2.13.0 (D50, blueprints 014–018) landed the operator batch:
**qwen38 is production** (`qwen3.8-max`, catalog 15, live call verified;
qwen38 now outranks glm52 in escalation order); **composition picking is
MRU-first everywhere** (derived from session records — no new state, D3);
**`--composition-file PATH|-`** launches unsaved composition documents
(launch-once semantics, resume rebuilds from the recorded snapshot); the
**providers pane** (P inside G) shows honest per-provider local status with
exact connect instructions and **masked direct-key entry** into the standard
secret env file; **doctor** cross-checks the gateway (served aliases vs
rendered, on-disk config byte-drift, OAuth credential-record awareness);
the G picker shows typed `/model` selectors per row; the editor saves from
anywhere with **^O**; the dark-theme muted color is readable (256-color
gray with safe fallbacks). The default composition leads **Opus 5**; plain
`claude` is never touched.

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles` branch `feature/term-only`,
  HEAD `cd4c41d` (`d7099f9` = the batch; nothing pushed).
- **Activated:** Home Manager generation **120**; entrypoints report
  `2.13.0` (catalog 15). Rollback: gen 119/118.
- **Health:** `claude-multi doctor` → **Ready**; all 33 durable sessions
  converged via `doctor --repair-all`; gateway radar silent (served ==
  rendered; no config drift); `Managed Claude 2.1.220 verified`.
- **Live acceptance (approval-gated, 2026-08-10):** one bounded gateway
  call on `claude-multi-qwen38-max` → 200, production Qwen3.8 identity;
  served config carries `qwen3.8-max`, zero preview residue.
- **Compositions:** 14 user files in `~/.config/claude-multi/compositions/`
  (0600) + the trusted `default` seed; all resolve against catalog 15.
- **Evidence:** 1,530 host tests OK (skipped=2); sandbox
  `nix build --file home-manager/claude-multi/tests/default.nix` green;
  cross-family review with adversarial verification (sol-xhigh must-fixes
  fixed; glm52 approve; qwen38 sweep GOOD).
- **Docs:** DECISIONS through D50; blueprints 014–018 with as-built
  amendments; USAGE/HANDOFF/AGENTS/UX/STATUS current; gateway-ops skill
  documents the doctor radar; wikis updated.

Verify with:

```bash
claude-multi --version            # 2.13.0
claude-multi doctor               # Ready
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
| **Design package** | [`../../../`](../../../README.md) — SPEC, TRANSITIONS, UX, DECISIONS (through D50), blueprints/ |
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
