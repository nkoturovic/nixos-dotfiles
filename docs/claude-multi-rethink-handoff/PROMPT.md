# Prompt: rethink claude-multi from first principles

You are taking over `claude-multi` after a long design/implementation attempt
lost user confidence. Your job is **not** to finish the frozen C\* plan. Your
job is to understand the whole product, independently re-derive the problem,
choose the best final architecture, specify it, implement it, and verify the
whole product end to end.

Proceed autonomously. Do not stop after investigation or planning. Make routine
technical/product decisions yourself from evidence. Ask the user only for a
genuinely ambiguous product preference, an irreversible/destructive action, or
a permission explicitly required by the loaded AGENTS rules.

Simplicity is a hard requirement. Do not replace the current overcomplicated
design with another elaborate framework. Prefer Claude's documented native
mechanisms, a small pure JIT compiler where useful, one authoritative state
model, minimal schema/runtime modes and deletion of speculative machinery. Read
and apply `SIMPLICITY.md` before selecting the architecture.

## Mandatory orientation

Work in `/home/kotur/personal/nixos-dotfiles`.

Before reasoning or editing:

1. Read all applicable `AGENTS.md` and wiki instructions.
2. Read every file in this handoff folder.
3. Read **every Markdown document** in
   `/home/kotur/.claude/.agents/wiki/raw/docs/`; classify official Claude docs
   separately from blogs/Reddit/community material and verify frontmatter/source.
4. Read the frozen historical artifacts:
   - `docs/claude-multi-durable-session-config.md`
   - `docs/claude-multi-durable-session-config-plan.md`
   - `.slim/deepwork/claude-multi-durable-session-config.md`
5. Inspect `git status`, tracked and untracked diffs, recent commits, tests,
   packaging, current Home Manager generation, active package/service, Claude
   binary version and process metadata. Do not read user transcripts or contact
   a provider/live Claude daemon.
6. Map the whole `home-manager/claude-multi` product: startup TUI, composition
   schemas/catalog/compiler, model/role routing, sessions/resume/link/fork
   boundaries, proxy/service, Doctor, launch/exec, developer tooling, package,
   Nix/Home Manager activation, rollback, tests and docs.

## Main problem

The current launcher defines selected custom agents through launch-only
`--agents` JSON. A clean `kimi-sol` session initially had all six selected
`cm-*` types. After Claude upgraded its shared supervisor from 2.1.216 to
2.1.217, all custom types disappeared while the transcript, environment and
lead model survived. Generic `claude` then inherited the Kimi lead, Sol stopped
being used, and review became same-family. The product needs durable,
composition-correct agent behavior across the lifecycle that caused this loss.

## Why this restart is necessary

The prior solution was not simple enough and was not planned from a sufficiently
complete whole-product model. It expanded from a registry fix into per-UUID
full Claude homes, multiple schema/lifecycle branches, broad collision gates and
a large probe sandbox before alternatives were compared cleanly. Official docs
arrived late and changed assumptions. Review orchestration also became error
prone. Passing tests does not repair an architecture-confidence problem.

There is also a broader process problem: development pace became too slow.
Research, planning, agent dispatch and review were too serial and too granular.
The replacement must optimize for visible delivery velocity as well as
correctness: larger coherent milestones, parallel non-overlapping lanes, early
tests of the riskiest assumption, fewer high-value gates and no repeated
reconsideration without new evidence.

A just-in-time configuration compiler for selected agents remains a promising
direction: derive exact files/settings/policy from the trusted composition only
when launching or deliberately transitioning a session. But it must be the
simplest version that fits the existing plugin/Nix/Home Manager setup, avoids
duplicating Claude machinery, integrates with the startup TUI and sessions, and
addresses every documented precedence, workflow, model and lifecycle gap.
Treat this as a hypothesis to evaluate—not a predetermined answer.

Anthropic's local orchestration-patterns guidance recommends starting with the
simplest orchestrator–subagent pattern and evolving only when observed limits
justify more coordination machinery. Use that as a design heuristic, not a
Claude Code runtime contract.

## User intent

Design `claude-multi` as one reliable end-to-end product, not a narrow agent
registry patch. Preserve selected custom subagents across replacement/restart
lifecycle, make weaker guarantees explicit, and avoid silent model/role/family
substitution. Native dynamic workflows remain a per-composition capability with
default **on** unless evidence forces a revised proposal; selected `cm-*`
subagents and native workflow agents may coexist, but their different guarantees
must be visible in the TUI and documentation.

The user wants excellent startup TUI/interaction quality, reliable sessions,
clear errors/recovery, plain-Claude compatibility, safe upgrades, packaging,
activation and rollback—not only durable agent files.

## Non-negotiable constraints

- Treat the frozen C\* design as historical evidence, not authority. Reuse,
  simplify or reject any part only after independent analysis.
- Do not start implementation until the new architecture/specification package
  is internally coherent and has one appropriate strategic checkpoint. Then
  proceed autonomously; no separate micro-approval is needed for routine work.
- Do not install packages, push/upload/transmit code, commit, activate Home
  Manager, stop/restart Claude sessions, contact real providers, inspect user
  transcripts, or contact/control the live Claude daemon without explicit user
  approval.
- Automated native checks must use no-provider, no-live-daemon isolation.
- Preserve rollback anchors and never delete a transcript-bearing root.
- Existing and linked sessions require an explicit compatibility story.
- Distinguish documented guarantees, pinned-version evidence, behavioral prompt
  expectations and residual user-controlled actions.

## Questions the new design must answer

1. What exact lifecycle event removed custom agents, and which state does
   Claude officially reload after process/supervisor replacement?
2. What is the simplest durable representation of composition state? Compare
   physical agents, project/managed/plugin scopes, CLI state, `--agent`,
   settings, JIT compilation, per-session roots and other documented options.
3. How are availability, explicit dispatch, automatic routing, model, effort,
   tools, permissions, nesting, worktrees and review-family independence
   enforced or honestly bounded?
4. How should selected `cm-*` agents coexist with native workflows when
   workflows default on? What is shown in the TUI?
5. How are project/managed/`--add-dir` agents, built-ins, generic `claude`,
   per-invocation model overrides, `/model`, background wake, agent view, teams
   and forks handled without silent drift?
6. How do auth/gateway environment, transcripts, history, onboarding, plugins,
   preferences and supervisors behave under each state-layout option?
7. What happens to startup TUI, composition editing, TTY paths, Doctor,
   sessions, proxy and rollback?
8. Which uncommitted G0/P0 changes are useful, overbuilt or wrong?
9. Which large milestones benefit from a proactive reviewer/finisher that may
   fix clear defects directly, and which rare risks require a separate
   independent read-only audit?
10. What small set of capable default agents works well with broad tools and
    edit permissions? How can useful project-specific agents participate
    intentionally and visibly instead of being blanket-blocked or silently
    merged by precedence?
11. How can the user deliberately change composition—agents, models, effort,
    workflow mode or policy—during an existing session? Which fields can reload
    safely, which require a controlled process relaunch, and how is the same
    transcript/session resumed or rolled back without shared-daemon control?
12. What is the shortest credible delivery path? Which risks need an early
    prototype/probe, which work can proceed in parallel, and which planning or
    review steps can be removed without reducing confidence?
13. Which proposed components can be deleted? For every daemon, mode, schema,
    transaction layer, hook, scanner or compatibility branch, what proven
    requirement makes it necessary?

## Required deliverables before coding

Create a new active design folder, separate from frozen history:

```text
docs/claude-multi-final/
├── README.md
├── BLUEPRINT.md
├── SPEC.md
├── PLAN.md
├── UX.md
├── TRANSITIONS.md
├── VERIFICATION.md
├── MIGRATION-ROLLBACK.md
├── DECISIONS.md
└── STATUS.md
```

The package must contain:

1. **Factual audit** — current code/runtime, official-doc facts, pinned-version
   evidence and unresolved unknowns.
2. **Architecture options** — at least three plausible solutions with explicit
   correctness, complexity, UX, compatibility, security and rollback tradeoffs.
3. **Recommended final design** — minimum moving parts, invariants, state/schema,
   lifecycle, failure behavior and honest guarantees.
4. **Whole-product UX plan** — startup TUI, workflow visibility, errors, Doctor,
   sessions and runbook. Route UI judgment to the designer specialist.
5. **Verification plan** — unit/integration/native/package/activation/live-user
   evidence with no-provider and no-live-daemon gates.
6. **Migration and rollback plan** — old records/sessions, current uncommitted
   work, Home Manager generations and transcript safety.
7. **Quality checkpoint design** — prefer a proactive reviewer/finisher after a
   substantial milestone: inspect the coherent diff, directly fix clear bounded
   implementation issues, verify, and report once. Reserve a separate read-only
   independent audit for architecture/security/session-integrity risks where
   separation materially matters. Do not create an automatic re-review loop.
8. **User decision summary** — final guarantees and choices before coding.
9. **Velocity plan** — critical path, parallel lanes, milestone sizes,
   time-boxed research and risk-based verification using `VELOCITY.md`.
10. **Simplicity justification** — explicit component/complexity budget and a
    list of frozen machinery intentionally deleted or not reused.

## Execute the solution after the design package

Do not end the task with documents. After one coherent architecture checkpoint:

1. Reconcile the current working tree against the chosen design. Selectively
   keep, rewrite or remove G0/P0 work using the saved artifacts as reference.
2. Implement in substantial end-to-end slices with non-overlapping parallel
   lanes where useful. Keep the lead responsible for integration.
3. Route startup TUI/interaction work through a designer-quality pass.
4. Use capable agents with broad tools by default; constrain only concrete
   collision/security/data-integrity risks.
5. After large meaningful milestones, optionally use a reviewer/finisher that
   may fix clear issues directly and verify once. Use a separate read-only audit
   only for high-risk architecture/security/session-integrity boundaries.
6. Implement composition transitions/restart behavior, workflow-on/off,
   subagent durability, project-specific agent policy, sessions, Doctor, proxy,
   packaging and rollback as one coherent product.
7. Run focused tests first, then the integration/package/Nix/activation evidence
   justified by the final plan. Never contact a real provider or live daemon in
   automation.
8. Update `docs/claude-multi-final/STATUS.md` with completed milestones and
   evidence as work progresses.
9. Prepare final source/wiki diffs and exact live-user acceptance steps.

If commit or Home Manager activation requires explicit permission under the
loaded rules, complete everything up to that boundary, present the exact
command/evidence/rollback, and ask once. Otherwise continue through final local
verification without routine interruptions.

Prefer deletion and simplification over preserving frozen machinery. The saved
patch/archive in `artifacts/` ensures no completed work is lost while the design
is reconsidered.
