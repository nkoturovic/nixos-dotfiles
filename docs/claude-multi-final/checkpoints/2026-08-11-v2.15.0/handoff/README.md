# Checkpoint 2026-08-11 · claude-multi v2.15.0 — START HERE

You are picking up the claude-multi project at its 2026-08-11 **v2.15.0**
checkpoint: **activated (HM gen 122), doctor Ready, Claude pinned at
2.1.220 (hash-verified, symlink-aligned). The model/provider funnel is
complete: discover (`claude-multi discover`, Kimi verified) → mark in the
TUI (providers pane N/A/D flows, `custom.json` registry) → try in an
ordinary session (per-bound picker fences) → adopt into compositions
(`claude-multi-dev model add --like` scaffold).** Supersedes
[`../../2026-08-10-v2.13.0/handoff/README.md`](../../2026-08-10-v2.13.0/handoff/README.md).
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
`/model`. Since 2.13.0: qwen3.8-max is production (live-verified); picking
is MRU-first everywhere (derived from records — no new state);
`--composition-file` launches unsaved documents; the providers pane (P in
G) offers honest per-provider status + masked key entry; doctor runs a
loopback gateway radar (served aliases vs rendered + config byte-drift +
OAuth-record awareness). Since 2.15.0 (D51/D52, blueprints 019/020): the
**custom registry** (`~/.config/claude-multi/custom.json`) adds
Anthropic-compatible providers (endpoint + env key) and ordinary-only
models (wire id + context bound) — never composition-eligible, always
covered by the doctor radar; the **models browser** (M in G) lists
catalog+custom with an **E** enable-jump into the editor's Availability
row; the editor saves anywhere with **^O**; and `claude-multi discover
<provider>` lists provider models on explicit invocation (Kimi works;
Qwen's Token Plan has none). The default composition leads **Opus 5**;
plain `claude` is never touched.

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles` branch `feature/term-only`,
  HEAD `5a10af9` (`7e280d5` = the 2.15.0 batch; nothing pushed).
- **Activated:** Home Manager generation **122**; entrypoints report
  `2.15.0` (catalog 16). Rollback: gen 121/120.
- **Health:** `claude-multi doctor` → **Ready**; 33 durable sessions;
  gateway radar silent (served == rendered, no drift).
- **Evidence:** 1,582 host tests OK (skipped=2); sandbox
  `nix build --file home-manager/claude-multi/tests/default.nix` green;
  cross-family reviews with adversarial verification on every batch;
  the 020 review caught two P0 seam defects (session-schema profile enum,
  `_connect_hint` custom-provider KeyError) that UI tests couldn't reach —
  fixed with seam-level regression tests, then an end-to-end funnel
  verification (add → merge → prepare → record save → doctor → converge →
  render → remove) ran green on the final tree.
- **Docs:** DECISIONS through D52; blueprints 014–020; USAGE/HANDOFF/
  AGENTS/UX/STATUS current; gateway-ops skill carries the verified listing
  matrix; wikis updated.

Verify with:

```bash
claude-multi --version            # 2.15.0
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
| **Design package** | [`../../../`](../../../README.md) — SPEC, TRANSITIONS, UX, DECISIONS (through D52), blueprints/ |
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
