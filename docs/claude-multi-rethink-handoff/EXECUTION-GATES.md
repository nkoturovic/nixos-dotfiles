# Restart workflow and gates

These are decision boundaries, not mandatory serial ceremonies. Combine gates
when evidence is already available and run independent work in parallel. The
goal is the shortest safe critical path.

## Gate 0 — preserve and audit

- Capture source/runtime/rollback anchors.
- Remove only generated artifacts; do not revert uncommitted work wholesale.
- Map every changed/untracked file to G0, P0, historical docs or unrelated work.
- Re-run only the narrow tests needed for a trustworthy baseline.

## Gate 1 — official behavior model

- Read every raw document in `SOURCE-MATERIAL.md`.
- Reconcile conflicting docs by version/date/source authority.
- Separate documented guarantees from probes and static strings.
- Build one lifecycle/state diagram for plain Claude, selected subagents,
  workflows, supervisors, sessions, config roots and gateway/auth.
- Time-box document synthesis; probe the highest-impact ambiguity early instead
  of expanding the design around assumptions.

## Gate 2 — whole-product architecture

- Map TUI → composition → compiler → launch → proxy → Claude → sessions →
  package/activation/rollback.
- Produce at least three solution options, including a simplification option
  and an option without per-UUID full config roots.
- State exact guarantees/residuals and pick the fewest moving parts that satisfy
  the requirements.
- Write the complete active package under `docs/claude-multi-final/`; never
  reactivate or overwrite the frozen historical blueprint.
- Apply `SIMPLICITY.md`: remove every component not required by evidence before
  the architecture checkpoint.

## Gate 3 — strategic review and user approval

- Review the complete architecture package once if its risk warrants it, not
  fragments.
- Return all findings together; revise as one coherent change.
- Resolve the findings as one coherent package, then proceed autonomously into
  implementation. Ask the user only for a genuinely unresolved product choice
  or a permission boundary required by loaded rules.

## Gate 4 — phased implementation

- Complete and self-verify meaningful milestones before review.
- Keep writer scopes non-overlapping; route TUI/interaction to the designer.
- Preserve plain Claude and legacy behavior until explicit cutover.
- Use independent review only at large/high-risk boundaries where it materially
  improves confidence, following `REVIEW-STRATEGY.md`.
- No provider/live-daemon automation.
- Run independent non-overlapping implementation/test/UX lanes concurrently and
  integrate at substantial milestones.

## Gate 5 — holistic acceptance

Prove at minimum:

- startup TUI/quick-confirm/editor/TTY/error paths;
- composition and workflow visibility;
- selected custom-agent discovery and model/effort/tools;
- deliberate composition transitions, restart-required classification, exact
  same-session resume and failed-transition rollback;
- replacement/restart/compaction/resume behavior;
- workflow-on/off behavior and honest weaker guarantees;
- generic/built-in/project/managed/fork/team escape handling;
- gateway/auth and proxy readiness without secret leakage;
- compatibility, rollback and transcript preservation;
- Python/package/Nix/activation builds and installed runtime paths;
- user-performed final live acceptance.

Only then commit and activate.
