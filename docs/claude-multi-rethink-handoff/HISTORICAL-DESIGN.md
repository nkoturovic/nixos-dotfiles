# Frozen historical design

## What C\* attempted

The frozen blueprint proposed a private per-session-UUID `CLAUDE_CONFIG_DIR`
containing generated physical agents, settings, a role-neutral `CLAUDE.md` and
manifest. New sessions would use this root; old/linked sessions remained legacy
argv mode. The intent was for replacement processes to rediscover durable agent
definitions instead of depending on launch-only `--agents` JSON.

Later revisions added workflow mode, available-model bounds, worktree base,
generic-agent denies, spawn pins, project/managed inventory gates, lifecycle
probes and a large P0 namespace sandbox.

## Why it is frozen

- The solution was not simple enough and was not planned from a complete
  whole-product model.
- It grew into a 900-line blueprint and 600-line plan before alternatives were
  compared cleanly.
- Official docs arrived late and changed assumptions about nesting, workflows,
  model precedence, worktrees and supervisors.
- Per-UUID config roots relocate the full Claude home, multiplying transcript,
  auth, onboarding and supervisor concerns.
- Selected-only guarantees conflict with project scopes and workflow executors
  unless narrowed substantially.
- Invocation model overrides/user actions prevent an absolute no-drift claim.
- The project-agent inventory gate became restrictive.
- P0 safety work became complex enough to require several review loops.
- Review orchestration repeatedly created accidental fresh Oracle sessions,
  reducing confidence.
- Final architecture reapproval was not obtained before freeze.

## Promising direction, not mandate

A **simpler JIT agent-config compiler** may still be the best answer: compile
the selected composition into exact Claude-supported files/settings immediately
before launch or deliberate transition, without creating a parallel runtime.
The new agent must evaluate how to fit that cleanly into the existing plugin,
TUI, sessions, Nix/Home Manager package and lifecycle—and compare it against
other simpler options.

The replacement should also reconsider the frozen design's blanket specialist
tool denial and blanket project-agent rejection. Capable default agents with
broad tools, bounded file ownership and optional visible project-specific agents
may produce better velocity without sacrificing correctness.

## Useful reference work

- argv-loss diagnosis and daemon evidence;
- official-doc and pinned-binary facts;
- G0 binary/Doctor/policy/sentinel work;
- reviewed no-provider P0 harness, if justified;
- tests and failure matrices as examples of rigor.

None is automatically part of the final solution.

## Historical artifacts

- `docs/claude-multi-durable-session-config.md`
- `docs/claude-multi-durable-session-config-plan.md`
- `.slim/deepwork/claude-multi-durable-session-config.md`

Create new architecture documents; do not mutate these into the replacement.
