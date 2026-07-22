# STATUS — live tracker

## 2026-07-22 — M0 design package

**Done**
- Full orientation: handoff, AGENTS/wiki rules, SIMPLICITY/VELOCITY, frozen
  C\* docs, live runtime (HM gen 76, package `sjkxlr3…`, Claude 2.1.217,
  daemon log), 10-doc corpus (classified: 6 official, 1 Anthropic guidance,
  3 community), whole-product source map (16-agent parallel sweep).
- Root cause pinned to documented semantics: `--agents` never saved to disk;
  supervisor 2.1.216→2.1.217 takeover rebuilt the registry from persisted
  state; carry-through argv set documented (agent-view L340–349) and
  binary-consistent.
- Architecture decided: per-session durable scope via `--add-dir` + compiled
  settings; 5 options evaluated; simplicity pass applied (≥4,500 lines slated
  for removal: P0 sandbox, dead branches, schema ceremony).
- Package written: README, BLUEPRINT, SPEC, TRANSITIONS, UX, PLAN,
  VERIFICATION, MIGRATION-ROLLBACK, DECISIONS, STATUS.

**Open unknowns**: U1 takeover carry of `--add-dir` (M1 stop-gate, then
acceptance L2), U2 appendix survival, U3 add-dir watcher, U4 add-dir
precedence, U5 availableModels fence, U6 nested default, U7 fork
refusal/carry, U8 settings reload timing, U9 deny merge precedence.

**R0 checkpoint (cm-reviewer-sol-xhigh)**: REVISE with 12 must-fix findings —
all resolved in one revision on 2026-07-22:
1. U1 language corrected (backgrounding documented; takeover binary-consistent)
   and promoted to M1 stop-gate; `--legacy` relabeled compatibility hatch.
2. State model fixed: record + catalog as two explicit authorities.
3. Scope staging moved to sibling dirs (`scopes/.<uuid>.new|.prev`); all crash
   windows enumerated.
4. exec-failure cleanup made action-aware (fresh/resume/upgrade/transition).
5. Transitions redefined: never mutate a live session's scope; exited
   confirmation; from-inside = print-only; v1 relaunch-only (hot path dropped).
6. Workflow family classification: lead-family by default; routed stages
   unknown/mixed; never an independent verdict.
7. Appendix-loss floor: all load-bearing rules duplicated into durable
   descriptions/settings; degradation not disappearance.
8. Collision gate extended to project tree + user `--add-dir`s + managed
   agents dir.
9. F1–F4 gated on per-root daemon-domain verification; otherwise move to
   acceptance; F7 takeover probe defined as the U1 stop-gate.
10. `worktree.baseRef:"head"` claim corrected (commits, not uncommitted work).
11. Linked sessions follow the uniform legacy-upgrade rule.
12. Rollback truth: old launcher can't read v2 records; native resume +
    retained store path documented.
Also: deny merge precedence downgraded to U9; wrong-composition rollback row
corrected. Verdict after revision: architecture stands (Option 1), proceed to
M1. No second loop per REVIEW-STRATEGY (no architecture change).

**Next**: user acceptance continues (L1 kill-resume, L2 takeover at next
upgrade). First acceptance friction fixed same-day below.

## 2026-07-22 — acceptance fix: name-based resume (`2dbbd1c`, activated)

User acceptance hit a dead end: native Claude's exit hint prints
`claude --resume "cm:<composition>"` (display name), while `-r` required a
UUIDv4. Fixed: `-r` accepts UUIDs, composition names, and `cm:`-prefixed
forms — unique match resumes, several list candidates with UUIDs (verified
live: 8 kimi-sol sessions listed with timestamps/cwds), none errors with a
`claude-multi sessions list` pointer. 984 tests green; new generation
active (`/nix/store/4pdik20isyfyb2g7f9pyjl9racrfwdvr-claude-multi-2.1.0`).
User already has 5 durable(g1) sessions from first launches.

## 2026-07-22 — M5 boundary: COMMITTED + ACTIVATED

- Commits: `7df42e2` (source, 56 files, +17,211/−1,809), `7253e85` (docs,
  36 files). Branch `feature/term-only`, tree clean.
- Flake eval: exit 0. **HM generation 77** active:
  `/nix/store/375i78cr7dqdh2gssphlhqk3xlixhxji-home-manager-generation`
  (rollback: generation 76).
- Package: `/nix/store/9qg71y5b111v0kdhjifqvmkicaybkjb0-claude-multi-2.1.0`.
  One environment fix required: a stale **direct nix-profile entry**
  (claude-multi 2.0.0, installed out-of-band earlier) shadowed HM's 2.1.0 —
  removed via `nix profile remove claude-multi`; resolution now 2.1.0.
- Post-activation `claude-multi doctor`: **Ready** — binary 2.1.217 verified,
  symlink matches, gateway valid, 6 recorded sessions (all legacy, incl. the
  rethink session itself), no collisions, U1 evidence line honest.
- Gateway restarted by activation without issue.

## 2026-07-22 — R1 remediation + delta confirmation: ALL-RESOLVED

All 9 R1 findings fixed in two lanes (L1-L4 lifecycle, P1-P5 product) plus
lead production wiring (`trusted=` and `expected_record_bytes=` into the
live call sites). Cross-family delta auditor (Sol xhigh, fresh bounded
pass): **all-resolved 9/9** with file:line evidence, suite re-run by the
auditor. **978 tests green host + sandbox.** Design refinements recorded in
DECISIONS D20.

## 2026-07-22 — R1 independent audit (Sol xhigh, read-only ×2)

**Verdict: findings-material** (9 findings, 0 architectural). Recorded in
DECISIONS D20. Lifecycle: stale-authority races across the lock boundary
(prepare→execute, converge, resume, link), unguarded exec-failure cleanup,
transition committing scope/record before preflight, durable-resume failure
deleting a valid scope. Product: resume bypassing the transition engine,
catalog drift stranding sessions, terminal-escape injection in rendered
external text, `--legacy`+workflows:off silent inversion, README rollback
note. Positives: no token leaks, no lock-fd leaks/deadlocks, durable
settings shape exactly per SPEC, versioning consistent, packaging sane.

## 2026-07-22 — M3.5 TUI rework (user-requested): DONE

**Evidence**: full discovery **928 tests OK, 1 intentional skip** (from 830).
Package builds as `claude-multi-2.1.0` (version now tracked from
version.json). Sandbox suite re-run in flight.

**Delivered**: `tui.py` (2,087 lines) — stdlib-curses widget layer (Label,
Badge, KeyBar, Checkbox(+groups), SelectList single/multi, TextInput with
real cursor editing, Modal with focused buttons + Esc-cancel, Table) +
light/dark/mono palette detection (COLORFGBG → OSC 11 → dark default;
NO_COLOR + `--no-color`; every colorized element has a text form, pinned by
an across-palettes equality test). All screens rebuilt on widgets with flow
semantics unchanged: quick-confirm composition card, ONE form-based editor
(TextInputs/SelectLists/checkboxes/Modals; Ctrl+G `$EDITOR` as the only
external-editor integration), sessions Table with Modal-confirmed actions,
transition diff + EXITED-wording Modal, doctor badge styling (line contract
byte-identical for scripts). `TERM=dumb` → printed plan + exact `$EDITOR`
command (no second interactive editor). **editor.py deleted (−1,130 lines
dual-mode duplication)**; line-mode contracts ('Status Ready', footers,
doctor lines) byte-preserved. 112 new/updated tests (41 widget, 52 form,
19 PTY incl. Ctrl+G `$EDITOR` end-to-end and /dev/tty routing).
UX.md §2 and DECISIONS D14 updated to match.

## 2026-07-22 — Finisher pass (cross-family, Sol xhigh ×2): APPROVE

Two bounded sharded lanes (first attempt context-overflowed on the full diff;
sharding fixed it). 8 findings, all fixed with regression coverage:
- Lifecycle: rollback order (record before scope), UUID/path validation
  everywhere, fresh/resume preconditions, exact pointer compare-and-restore,
  scope drift detection (shape/symlinks/owner/modes), durable deletes.
- Compile: collision-gate recursion + YAML-comment name parsing;
  `CLAUDE_CODE_DISABLE_WORKFLOWS` reserved against lead env.
- One architecture finding (reported, not fixed): same-UUID lifecycle
  operations unserialized.
**Lead integration after the pass**: per-UUID **lifecycle lock**
(`sessions.lifecycle_lock`) across launch scope/record/pointer mutation and
transition staging/swap/save/repair — released before execve so no lock fd
leaks into Claude; PTY flake fixed (exit deadline 6s→30s under suite load);
**830/830 tests green**. package.nix now derives its version from
version.json (2.1.0); product README rewritten for the durable architecture.

## 2026-07-22 — M3 deletions + catalog slimming: DONE

**Evidence**: full discovery **809 tests OK, 1 intentional skip** (count down
from 885 by design: P0's 63 tests and ceremony tests removed with their
machinery). Probe import verified lazy (`claude_multi.dev` import no longer
loads `claude_multi.probe`).

**Removed (net −3,100 lines)**: P0 namespace sandbox (p0.py 776, p0inner.py
793, test_p0.py 994 — reference archive intact in handoff artifacts);
same-launch lead mode + `--agent cm-lead` machinery; triple-flag fork
compiler path (fork now fails with the native-fork guidance);
~130-line hardened daemon-metadata parser → minimal existence/pid check;
probe fixture daemon scaffolding; ADAPTER_IDS + dead smoke branch;
native-contract 8 capability objects → one map + U-numbered acceptance map;
roles.json contract-string duplication (prompts canonical).
**Updated**: prompts restored to bounded delegation (byte-identical to the
live session's proven wording) + reviewer finisher clause (D7); goldens
re-blessed (only prompt-embedding goldens changed); fork requests fail with
the verbatim native-fork guidance.

## 2026-07-22 — M2 transitions + TUI: DONE

**Evidence**: full discovery **885 tests OK, 1 intentional skip** (from 792 at M1).

**Delivered (Lane D)**: `transition.py` — v1 relaunch-only transitions per
TRANSITIONS.md: semantic diff, exited-confirmation (and from-inside print-only
via new `CLAUDE_MULTI_SESSION_ID` env), sibling-generation swap with fsync,
every crash window converging to record authority, exec-failure restore of
exact prior bytes, `converge()` for doctor --repair. 46 tests.
**Delivered (Lane C)**: quick-confirm badges (durability/workflow/policy/
project-agents), editor workflows row (curses+line), sessions screen
(mode column, per-row actions, forget-with-scope-removal), `sessions
transition` wiring, Doctor (scope integrity, --repair, --prune, collisions,
evidence line).
**Lead integration**: durable-by-default in `Runtime.prepare` (fresh/resume;
legacy records upgrade; fork stays legacy), `--legacy` escape hatch with
lineage preservation, badge forms, `SessionStore` UUID guards, 9 integrator
tests.
**Notable**: mid-M2 the shared supervisor restarted again (14:50Z) and this
session's argv `--agents` vanished live — the third observed instance of the
exact failure this design eliminates. Cross-family review now runs via
workflow `model` override (gpt-multi-sol-xhigh) since cm-* types are gone.

## 2026-07-22 — M1 durable-scope core: DONE

**Evidence**
- Full discovery: **792 tests OK, 1 intentional skip** (pre-existing native-contract real-probe gate). Lane-focused: 186/186.
- **F1 PROVEN against the real 2.1.217 binary** (fake provider, live-domain tripwire armed): scripted delegation to a marker agent in an `--add-dir` scope **accepted**; the subagent's own request carried the marker frontmatter model — on-disk discovery through `--add-dir` works on the pinned binary. 5 requests, `/tmp/cc-daemon-1000` byte-identical pre/post.
- **Daemon-domain precondition VERIFIED** for headless sessions: no live-daemon contact; `-p` sessions create no daemon domain.
- **U1 (takeover) FAILS CLOSED in automation as designed**: headless sessions run no resident supervisor → takeover carry-through moves to user acceptance **L2**. M2+ proceeds on the documented-backgrounding basis per VERIFICATION §2.
- Integrator fix: probe.py retained-versions dir now derives from the contract's `resolved_path` (hygiene gate green).

**Delivered (Lane A)**: `scope.py` (pure scope compiler + collision gate), composition `workflows` field (+ultracode→xhigh derivation), record v2 (v1 loads unchanged), durable-mode compiler (no `--agents`/`--disallowedTools`), launch action-aware cleanup, `bless.py`, durable goldens, version 2.1.0.
**Delivered (Lane B)**: probe daemon-domain gate + live-domain tripwire, scripted-delegation harness, F7 takeover skeleton (fail-closed), 60 new probe tests.

## Milestone ledger

| M | Scope | State | Evidence |
| --- | --- | --- | --- |
| M0 | design package | done (R0 resolved) | this folder |
| M1 | durable-scope core | done | 792 tests OK; F1 proven on 2.1.217; U1→L2 |
| M2 | transitions + TUI | **done** | 885 tests OK; transition matrix; badges/Doctor |
| M3 | deletions + catalog | **done** | 809 tests OK; −3,100 net lines; lazy probe |
| M3.5 | TUI rework (user-requested) | **done** | 928 tests OK; editor.py −1,130 lines |
| M4 | integrated verification | **done** | 978 host + 978 sandbox green; package 2.1.0 builds |
| M5 | boundary: commit/activation/acceptance | **committed+activated** | gen 77; doctor Ready; acceptance pending user |
