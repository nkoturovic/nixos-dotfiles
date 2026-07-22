# Evidence and established facts

## Original failure

A clean long-running `kimi-sol` session initially exposed all six selected
`cm-*` agent types. After a Claude 2.1.216 → 2.1.217 supervisor takeover, every
custom type disappeared. Built-ins remained; generic `claude` inherited the
Kimi lead, Sol stopped being used, and review became same-family.

Evidence source:

- `/home/kotur/.claude/daemon.log`
- It recorded symlink change, upgrade restart, worker adoption and refusal of a
  stale worker respawn.
- Environment/model/transcript survived; launch-only `--agents` state did not.
- No user transcript was read to establish this.

This is the core problem. Verify the causal interpretation and identify the
smallest officially supported persistent state that survives the same lifecycle.

## Official facts already surfaced

Re-derive these from `SOURCE-MATERIAL.md`; do not trust this summary alone:

- CLI `--agents` definitions are current-session-only and not saved to disk.
- User/project `agents/*.md` files are recursively discovered; existing agent
  directories are watched and edits affect later delegations.
- Scope precedence includes managed, CLI, project, user and plugin definitions.
- Project/`--add-dir` definitions can widen inventory.
- Agent model selection can be overridden by environment, invocation and
  user/session actions before or around frontmatter.
- Nested subagents, background agents, agent view, teams and workflows are
  distinct lifecycle surfaces.
- Native workflows can use lead/session models, `acceptEdits`, high fan-out and
  guarantees different from selected `cm-*` role definitions.
- `CLAUDE_CONFIG_DIR` relocates sessions/transcripts, settings/credentials and a
  separate supervisor—not only agent files.
- Worktree base-ref, uncommitted state and cleanup affect implementer behavior.

## P0 no-provider evidence

The uncommitted P0 harness was reviewed and executed only in the staged sequence:

- Fake self-test:
  `/tmp/opencode/cm-p0-selftest.nEUrem/evidence/p0-self-test.json`
- Contract-pinned Claude `--version`:
  `/tmp/opencode/cm-p0-version.XnP6H0/evidence/p0-last-run.json`

Both had return code 0, 26/26 isolation assertions, zero TCP/Unix canary
contacts, intact shm/SysV canaries, zero key payloads, zero provider requests and
metadata-only persisted evidence.

P0 is a development probe harness, not production architecture. Reassess its
complexity.

## Unknowns

- Best persistent agent/state scope for the whole product.
- Replacement-process reload semantics for the final architecture.
- Workflow + selected-agent coexistence under model, gateway, permissions,
  restart and review-family constraints.
- Honest enforceability of model and role guarantees.
- Transcript/auth/onboarding impact of alternate config-root layouts.
- Existing-session/fork boundaries and holistic product acceptance.
