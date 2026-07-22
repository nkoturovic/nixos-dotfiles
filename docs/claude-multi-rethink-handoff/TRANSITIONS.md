# Composition transitions and controlled restart

This is a required design problem, not a settled implementation.

## User need

During a managed session, the user may want to change:

- selected agent roles/variants;
- model selectors and effort lanes;
- workflow mode;
- tool/permission/isolation policy;
- project-specific agent participation;
- lead policy or review/finisher behavior.

The product must explain whether each change affects only future delegations,
requires replacement processes, or requires a new session/root.

## State classification to derive

The new architecture must build a table from official docs and probes:

1. **Watched/hot-reloadable** — for example, edits to an already-existing
   discovered agent directory may affect the next delegation.
2. **Process-bound** — lead model/effort/environment/argv or worker snapshots
   may require a managed Claude process relaunch.
3. **Session/root-bound** — transcript, supervisor, auth/onboarding, workflow
   run state or config-root identity may make in-place transition unsafe.
4. **User-controlled residual** — `/model`, active workers or native workflow
   actions may not be reversible by the launcher.

Do not infer a hot-reload guarantee from file watching alone.

## Candidate transition UX to evaluate

1. User opens a composition-change action from the TUI/session command.
2. Product resolves the current immutable snapshot and proposed target.
3. TUI shows a semantic diff: agents, models, effort, workflow mode, policy,
   project agents and whether restart is required.
4. Product checks active subagents/background agents/workflows and refuses or
   offers a clear stop/finish choice if transition would mix definitions.
5. A JIT compiler prepares the target config atomically without replacing the
   working state yet.
6. If proven hot-reloadable, update only the supported managed files and verify
   discovery before recording the new snapshot.
7. Otherwise, checkpoint transition intent and relaunch the managed Claude
   process with exact resume of the same session UUID/transcript if the chosen
   architecture supports it. Do not restart the shared daemon.
8. Save the new snapshot only when launch preparation succeeds. On ordinary
   failure restore old bytes/state; on crash use a drift-gated recovery record.
9. Print exact commands for retry, rollback or starting a new session when exact
   resume is unsupported.

This flow is a hypothesis. The next agent must compare it with simpler native
capabilities and avoid inventing transaction machinery unnecessarily.

## Required verification

- no-op transition;
- agent-only change and next-delegation behavior;
- model/effort change with required process replacement;
- workflow on/off transition with active and idle runs;
- project-agent scope changes after CWD/add-dir changes;
- exact session/transcript identity after controlled relaunch;
- failed exec and hard-crash rollback;
- concurrent use of the same UUID rejected or defined;
- old/linked session behavior and native fork boundaries;
- TUI diff/restart/recovery wording.
