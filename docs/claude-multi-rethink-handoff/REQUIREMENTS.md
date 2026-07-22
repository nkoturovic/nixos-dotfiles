# User requirements

## Product scope

Treat `claude-multi` as one product:

- startup/no-argument TUI and quick-confirm flow;
- real PTY, no-controlling-terminal, keyboard and cancellation behavior;
- composition catalog/schema/editor/rendering;
- lead, subagent, workflow and cross-family routing;
- sessions, resume, link, transition, forget and fork boundaries;
- proxy/service readiness and Doctor;
- binary trust, upgrades, packaging, activation and rollback;
- documentation, recovery and final live acceptance.

## Agent behavior

- Selected `cm-*` agents must not silently disappear or be replaced after
  restart, update, compaction or resume.
- Physical files are acceptable only if discovery/reload/lifecycle behavior is
  proven for the chosen scope.
- Availability, explicit invocation and automatic routing are different claims.
- Do not claim model drift impossible when Claude permits invocation overrides
  or user `/model` changes; bound and display residuals.
- Review-family independence must classify native workflow output.
- Specialist nesting must not become an ungoverned escape path.
- Default agents should generally be capable of reading, editing and executing
  the tools needed to complete their bounded assignment. Prefer clear scope,
  file ownership, isolation and verification over blanket read-only/tool-deny
  policies.
- Restrictions remain appropriate where a concrete security, data/session
  integrity or writer-collision risk justifies them; they are not the default.
- Evaluate project-specific agents as a useful extension point. Their discovery,
  precedence and guarantees must be visible and intentional rather than either
  silently accepted or categorically blocked.

## Workflows

- Native dynamic workflows are valuable and should be per-composition.
- User-selected default: **on** (`native`).
- Selected subagents and native workflow agents may coexist.
- UI/docs must state workflow model, `acceptEdits`, fan-out and weaker
  cm-role/worktree guarantees.
- Off mode needs a coherent effort policy without breaking ultracode users.
- Do not build custom workflow machinery unless the new design proves it needed.

## Compatibility and safety

- Plain Claude remains unaffected.
- Existing/linked sessions need explicit behavior and recovery.
- No automated real-provider request or live-daemon control/contact.
- User performs final real acceptance.
- Rollback must not delete transcripts or invalidate legacy records.
- No activation, commit, push or package installation without approval.

## Composition transitions and restart

- Users need an intentional way to change agent/model/effort/workflow
  composition for an existing managed session.
- Do not pretend every setting can hot-swap. Classify watched/hot-reloadable,
  process-bound and session/root-bound state from official docs and probes.
- Prefer a controlled managed-process relaunch with exact session resume when a
  transition cannot reload safely. Never restart/control the shared daemon.
- Show the composition diff and whether restart is required before applying it.
- Active subagents/background agents/workflows must finish, stop, or be handled
  explicitly; never leave a mixed old/new composition silently.
- Failed relaunch/exec restores the prior composition snapshot/config and gives
  an exact recovery command.
- Existing native fork/link limitations remain explicit.

## Process expectations

- Gather context first, especially every raw doc.
- Start from the simplest orchestrator–subagent pattern that can work; evolve to
  teams/shared-state/message-bus machinery only after an observed need.
- Rethink architecture independently; current tests are references, not mandate.
- Get strategic review before implementation and at meaningful phase gates.
- Be proactive without silently making major policy choices.
- Reuse specialist sessions only with an explicit verified task/session ID.
- Prefer proactive implementation velocity: authors complete and self-verify a
  substantial coherent milestone before considering independent review.
- Do not run reviewer → tiny fix → reviewer loops. Review one coherent diff and
  its test evidence, return all findings together, apply all accepted findings
  together, then close the checkpoint after author verification.
- A second review is exceptional, not automatic: use it only when fixes change
  architecture/security/session integrity materially or the user asks.
- Reviewer invocation may be implicit at a genuinely high-risk phase boundary
  or explicit by user request; it should not interrupt ordinary implementation.
- The role name is open. `reviewer` may remain, or the final design may prefer
  `auditor`, `verifier`, or another name that communicates occasional quality
  assessment rather than continuous back-and-forth.
- A reviewer may proactively fix clear bounded implementation defects and run
  verification in the same milestone pass. It must report every edit and must
  not silently change architecture, product policy or user-visible guarantees.
- True read-only independence is reserved for the smaller set of high-risk
  architecture/security/session-integrity decisions where self-fixing would
  undermine the value of the check.
- Development pace is a first-class requirement. Avoid long serial chains of
  research → planning → tiny edit → review when independent work or a focused
  prototype can establish the answer faster.
- Prefer substantial user-visible or risk-closing milestones, parallel
  non-overlapping lanes, and focused tests. Keep progress visible.
- Time-box research and architecture elaboration. Once evidence supports a
  decision, proceed and revisit only when new evidence contradicts it.
- Avoid overengineering. New modes, schemas, persistent state, transactions,
  daemons, hooks, scanners and fallback branches require a proven requirement.
- Prefer one small composable mechanism over parallel legacy/new subsystems.
- Run a simplification/YAGNI pass on the architecture before coding and on each
  large implementation milestone before considering it complete.
