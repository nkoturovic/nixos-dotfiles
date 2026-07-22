# PLAN — milestones, lanes, velocity, quality checkpoints

## 1. Critical path

```
M0 design package ──checkpoint R0──▶ M1 durable-scope core ──▶ M2 transitions+TUI ──▶ M3 deletions+catalog ──▶ M4 verification ──▶ M5 acceptance+boundary
```

M1 closes the root-cause risk (agents must come from files). Everything else
is product completion around it. U3 (watcher coverage) is probed in M1 with
the fake provider — the riskiest remaining assumption gets the earliest test.

## 2. Milestones

### M0 — Design package (this folder)
- 10 documents, internal coherence, one strategic checkpoint (R0).
- Done when: R0 findings resolved as one revision.

### M1 — Durable-scope core (root-cause fix)
- Scope compiler: `composition → scopes/<uuid>/{.claude/agents/*.md,
  settings.json}` (atomic sibling staging `scopes/.<uuid>.new`), lead
  appendix unchanged.
- Settings overlay: denies, workflow keys, `availableModels`, `worktree.baseRef`.
- Launch integration: new argv (no `--agents`/`--disallowedTools`), legacy
  parameter preserved, record v2 (`mode`, `scope_generation`, `workflows`),
  action-aware exec-failure cleanup.
- Collision gate: exact `cm-*` scan across project tree, user `--add-dir`s,
  and managed agents dir (when readable) — fail closed.
- **U1 stop-gate probe**: a dev-only fixture probe that proves takeover
  carry-through *if* per-config-root daemon domains verify (static strings +
  a fixture run that observes the fixture's own daemon domain without
  touching `/tmp/cc-daemon-<uid>`). If per-root domains do NOT verify, the
  takeover proof moves to user acceptance L2 and M2+ proceeds on the explicit
  basis "documented for backgrounding, takeover pending user proof". If the
  probe proves takeover does NOT carry the scope pointer: **stop, return to
  an Option 2 architecture checkpoint.** `--legacy` is not a durability
  fallback.
- U3 watcher probe (same fixture, best-effort; informs future hot mode only).
- Tests: scope compile golden tree + bless tool; launch ordering; cleanup
  matrix; collision; record v1→v2 defaults; legacy parity golden.
- Done when: focused tests green; fresh durable launch under the fake
  provider; `git diff` shows no P0 import anywhere; U1 gate outcome recorded
  in STATUS.

### M2 — Transitions + TUI visibility
- `sessions transition` flow (diff view, exited-confirmation, sibling
  generation swap, restore-on-failure, crash-converge via record authority;
  v1 relaunch-only).
- TUI: durability badge, workflow badge, project-agent line, sessions screen
  actions, Doctor §7 checks (scope integrity, repair, prune, collisions,
  evidence levels, simplified daemon line).
- Tests: transition matrix (no-op, agent-only, model, workflow, failure
  restore, crash converge), doctor checks, render goldens.
- Done when: focused tests green; UX.md flows manually walk through with a
  fake provider.

### M3 — Deletions + catalog/schema simplification
- Delete `p0.py`, `p0inner.py`, `test_p0.py`; keep `probe.py` dev-only
  (lazy import in dev.py — fixes the G0 REWRITE verdict).
- Delete same-launch lead mode + triple-flag fork path + dead symbols
  (ADAPTER_IDS, dead smoke branch, fixture daemon scaffolding).
- Simplify daemon-metadata reader → existence/pid.
- Collapse native-contract capability schema to a map; drop roles.json
  contract strings; prompts: reviewer finisher clause, bounded-delegation
  restore (U6), sentinel suffix into generated descriptions.
- `version.json` → 2.1.0.
- Tests: suite pruned alongside (ceremony tests die, behavior tests stay);
  full discovery green.
- Done when: net line removal ≥ 4,500; full suite green; package builds.

### M4 — Integrated verification
- Full Python discovery; goldens re-blessed and reviewed; Nix package build;
  `tests/default.nix`; flake check target.
- Fake-provider integration: fresh durable launch, legacy upgrade resume,
  transition relaunch, collision refusal.
- Docs: README/runbook/acceptance updated; wiki notes; STATUS finalized.
- Done when: VERIFICATION §§1–4 all green; diff review clean.

### M5 — Boundary: commit + activation + user acceptance
- One independent read-only cross-family audit (R1) of the integrated diff
  (architecture/security/session-integrity/package).
- Present exact commit command, activation command, evidence, rollback; ask
  once. Then user performs live acceptance (VERIFICATION §5).

## 3. Parallel lanes (writer discipline)

| Lane | Scope (exclusive) | Who |
| --- | --- | --- |
| A: scope compiler + launch + records | `compiler.py`, `launch.py`, `sessions.py`, `state.py`, new `scope.py`, related tests | cm-implementer-sol-high (M1) |
| B: fake-provider probe | `probe.py` usage + new `tests/test_scope_probe.py` (dev-only) | cm-implementer-kimi-k3-max (M1) |
| C: TUI + doctor surfaces | `cli.py`, `editor.py`, `render.py`, related tests | cm-implementer-kimi-k3-max (M2) |
| D: transitions | `transition.py` + `sessions.py` touchpoints (after A lands) | lead (M2) |
| E: deletions + catalog | `dev.py`, `p0*`, `probe.py` trim, catalog/schemas/prompts | cm-implementer-sol-high (M3) |

Lead integrates at each milestone boundary; lanes never share files in the
same window (`sessions.py` A→D handoff is sequential, not concurrent).

## 4. Quality checkpoints (no loops)

- **R0 (design)**: one batched cross-family review of this package
  (cm-reviewer-sol-xhigh; author family = moonshot). Resolve once, proceed.
- **M1–M3**: author self-verification + focused tests. One
  **reviewer/finisher** pass after M2+M3 combined (may fix bounded defects,
  reports all edits once). No re-review unless fixes change architecture.
- **R1 (pre-activation)**: one independent read-only audit, cross-family vs
  the majority implementation family, covering: durable-state claims vs docs,
  failure/rollback correctness, record/scope authority, package/activation,
  secret hygiene. One batch of findings; resolve; stop.
- Per REVIEW-STRATEGY: reviewer may be renamed `cm-finisher` behaviorally
  (fix + verify + report) while the rare audit stays read-only.

## 5. Velocity plan (per VELOCITY.md)

- **Time-boxes**: research is done (orientation sweep complete). Each
  milestone has a single done-when; no gold-plating between milestones.
- **Early risk**: U3 fake-provider probe lands in M1, not M4.
- **Focused tests first**: run only the touched module's tests during a lane;
  full discovery at milestone end, not per edit.
- **Substantial milestones**: each M is user-visible or risk-closing; no
  micro-commits of half-features (commit boundary is M5 anyway).
- **Visible progress**: STATUS.md updated at each milestone with behavior +
  evidence; user sees movement at milestone granularity.
- **No repeated reconsideration**: decisions recorded in DECISIONS.md;
  reopened only on new evidence (U-series outcomes, audit findings).

## 6. Risks and fallbacks

| Risk | Fallback |
| --- | --- |
| U1 fails (add-dir not carried on takeover) | **Stop; return to an Option 2 (managed config root) checkpoint with known costs.** `--legacy` is only a compatibility hatch — sessions work until a takeover, which is exactly the incident shape; it is never presented as a durability answer |
| U3/U8 false (no hot reload) | v1 is already relaunch-only; nothing thrown away |
| U5 false (no model fence) | downgrade to displayed residual; TUI/Doctor wording already honest |
| availableModels breaks gateway selectors | acceptance gate; drop the key (fence is defense-in-depth, not load-bearing) |
| Schema v2 breaks old launcher | intended fail-closed; rollback = HM generation 76 + old catalog |
