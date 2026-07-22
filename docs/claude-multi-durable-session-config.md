# claude-multi durable session configuration blueprint

> **FROZEN HISTORICAL ARTIFACT — DO NOT IMPLEMENT**
>
> Frozen on 2026-07-22 after the user requested a first-principles redesign of
> the whole `claude-multi` product. This document records one explored C\*
> architecture; it is not the approved target. Start from
> [`claude-multi-rethink-handoff/PROMPT.md`](./claude-multi-rethink-handoff/PROMPT.md).

Status: historical and superseded as an active design. No implementation claim.

## 1. Decision

`claude-multi` will use two explicit runtime modes:

- **legacy argv mode** for every existing, linked, v1, or otherwise adopted
  session;
- **session-config mode (C\*)** for new sessions created after the capability
  probes and implementation are approved.

C\* generates a private Claude configuration root per managed session UUID and
sets `CLAUDE_CONFIG_DIR` for that session. The root contains only that
composition's generated agent definitions, persistent native-agent policy, and
role-neutral session instructions plus a closed allowlist of non-secret user
preferences. Official Claude documentation defines this variable as replacement
for the entire user Claude home: transcripts, history, plugins, user
agents/memory, Linux/Windows credentials and a separate supervisor also live
below it. The root is reused for exact resume and its managed files are
regenerated only during a deliberate composition transition while the session
is not running.

Compositions and session snapshots may carry one optional closed-vocabulary
field controlling Claude's native dynamic-workflow runtime:

```json
"workflows": { "mode": "native" | "off" }
```

An absent field means `native`: the trusted default and every existing user
composition retain their current behavior under old and new launchers.
Compositions, and records whose snapshot bears the resolved mode, require the
launcher generation that ships this reviewed change.

The design is hard-gated. If persistent agent discovery, transcript resume,
trust/onboarding, frontmatter parity, policy denies, background inheritance, or
restart survival cannot be proven against the pinned Claude version with a
disposable loopback fixture, C\* does not activate. The approved fallback is
G0' hardening plus loud legacy relaunch recovery; no hook, plugin, global agent
registry, supervisor, or private transcript migration is added speculatively.

## 2. Problem and evidence

The active v2 compiler supplies selected agents through Claude's launch-only
`--agents` JSON, supplies native denies through `--disallowedTools`, and
supplies the main-thread lead contract through
`--append-system-prompt-file`. It then `execve`s Claude and no launcher process
remains.

A clean long-running `kimi-sol` session initially exposed its six selected
`cm-*` types. Later, all six were explicitly removed and only persistent
built-ins remained. Generic `claude` agents inherited the Kimi lead; Sol
stopped being used and review became same-family.

The shared Claude daemon log records the causal event: the user installation's
symlink changed from 2.1.216 to 2.1.217, the shared supervisor restarted for an
upgrade, adopted one worker, and refused one stale worker respawn. Environment,
model and transcript survived; launch argv did not. This failure recurs whenever
plain Claude updates and the shared daemon takes over.

The managed launcher must therefore place composition state in a persistent
source that the restarted Claude process reloads. The launcher must not attempt
to control the shared daemon or interfere with plain Claude's update workflow.

## 3. Goals

1. Selected agent definitions survive the proven daemon upgrade takeover.
2. Unselected variants are structurally absent from a C\* session.
3. Generic/native fallback cannot silently replace managed variants.
4. Exact model selector, effort lane, canonical role prompt, tools policy, and
   implementer isolation match the trusted composition.
5. Lead policy survives restart and compaction.
6. Concurrent sessions with different compositions have disjoint mutable
   state.
7. Plain Claude, its auth/config/update flow, and the shared daemon are not
   modified.
8. Existing sessions remain resumable without reading or copying private Claude
   transcript files.
9. Every unsupported lifecycle path fails closed with an exact recovery command.
10. Home Manager rollback and source rollback never delete a transcript.

### Goal narrowing under `native` workflow mode

Under the user-selected `native` default, goals 2 and 4 are explicitly
narrowed: the Agent-tool inventory remains selected-only, but the native
workflow runtime is a composition-owned exception that licenses lead-model
workflow executors outside the variant registry and weakens the
worktree/one-writer guarantees for workflow fan-out. This narrowing is
acceptable only because the mode is user-selected or defaulted, visible in the
TUI/`show`/Doctor, covered by the composition hash, and documented in this
blueprint. It is not silent drift. The exact guarantee wording and its
residuals live in §7.2.

## 4. Non-goals

- No daemon control, restart, environment injection, or replacement.
- No resident launcher, scheduler, or per-turn interceptor.
- No global `~/.claude/agents` registry.
- No plugin marketplace or per-launch plugin directory.
- No global active-preset rewrite.
- No private Claude transcript inspection, copying, synthesis, or migration.
- No hot swap of a running process or worker definitions.
- No automatic model fallback or `/models` gateway discovery.
- No claim that C\* solves native fork persistence or dangling native links.
- No guarantee for two simultaneous processes using the same session UUID.
- No launcher-authored workflow scripts, skills, or orchestration machinery;
  the `native` workflow mode exposes platform functionality only.
- No workflow environment variable (`CLAUDE_CODE_DISABLE_WORKFLOWS`); the
  workflow mode is owned by generated settings alone.
- No agent teams, lifecycle hooks, plugins, subagent persistent memory, or
  `--append-subagent-system-prompt` as architecture.
- No configurable nested-delegation depth; the platform depth limit is fixed
  and the composition denies specialists the Agent tool mechanically (§10).
- No claim that `availableModels` prevents in-set per-invocation overrides or
  user `/model` changes (§7.2, §12).
- No workflow run-state resume claim across process exit; the next session
  starts a stopped workflow fresh (§8).
- No copying of personal `~/.claude/workflows` content or any other arbitrary
  user state into a session root.

## 5. Runtime modes and compatibility

Session records gain an optional closed-vocabulary field:

```json
"runtime_mode": "legacy-argv" | "session-config-v1"
```

Old records without the field are interpreted as `legacy-argv`. `sessions
link` always creates `legacy-argv` records because the native transcript was
not created inside a managed C\* root. New fresh sessions use
`session-config-v1` only after the C\* capability contract is marked verified.
The optional `runtime_mode` and `managed_config_hash` fields are emitted only
for `session-config-v1` records. New-launcher `legacy-argv` records remain
field-less and byte-identical in shape to today's records so an older launcher
can still validate and resume them after rollback.

An explicit field is preferred over deriving mode from launcher version:

- linked sessions may be created by a new launcher but must remain legacy;
- future modes must remain distinguishable;
- failure messages can name the exact support boundary.

Legacy behavior stays unchanged except for G0' hardening, generic fallback
denial, and the deterministic pins of §10. No legacy record is migrated. A
legacy exact resume may still reassert `--agents`; it remains vulnerable to
later daemon takeover and receives the documented relaunch recovery.

Older launcher versions cannot consume C\* records. C\*-created sessions are
therefore explicitly tied to a launcher that understands
`session-config-v1`. Rolling Home Manager back preserves their files but cannot
resume them until the C\*-capable generation is restored.

### Workflow mode field

Composition documents gain the optional closed field of §1:

```json
"workflows": { "mode": "native" | "off" }
```

- Absent means `native`. The trusted default composition and existing user
  compositions carry no field and keep current behavior under old and new
  launchers; their hashes and records are untouched.
- The resolved mode is recorded in the session snapshot as an optional
  `workflow_mode` field, present only when the composition document declares
  `workflows`. It therefore affects the composition hash and the resume drift
  check exactly when declared, and never invalidates old field-less records.
- Field-bearing composition documents and field-bearing records require the
  launcher that ships this reviewed change: old launchers validate against the
  closed schemas and reject the unknown key instead of silently ignoring it.
- Declaring `{ "mode": "native" }` explicitly is permitted but redundant; it
  produces a distinct composition hash from an absent field and the same
  runtime behavior.
- Legacy argv mode passes the static trusted settings asset via `--settings`
  and cannot flip the workflow mode per composition. Creating a fresh legacy
  session from a composition declaring `off` fails closed with exact guidance:
  `off` requires session-config mode. Resuming an old legacy record whose
  snapshot carries no workflow mode keeps its recorded native behavior.

## 6. C\* state layout

For session UUID `U`:

```text
${XDG_STATE_HOME:-~/.local/state}/claude-multi/
├── sessions/U.json
├── last-session-by-cwd/...
└── claude-config/U/
    ├── CLAUDE.md
    ├── settings.json
    ├── agents/
    │   ├── cm-analyst-sol-high.md
    │   └── ...selected variants only...
    ├── .claude-multi-generation.json
    └── ...Claude-owned files, including native session state if the probe confirms...
```

The UUID is validated by the existing UUIDv4 contract before path derivation.
The root and every launcher-created directory are owner-only mode 0700.
Launcher-created files are regular, owner-only mode 0600, never symlinks.

Official documentation says transcripts move under `CLAUDE_CONFIG_DIR`, so the
transcript-bearing branch is expected. The pinned CLI probe still records the
actual behavior and retains two supported outcomes:

| P-t outcome | Root ownership and transition semantics |
| --- | --- |
| Native transcript stays in the ordinary Claude project/session store | The C\* root is configuration-only. Whole-root staging/replacement is safe, `forget` may remove it, and exact resume must still be proven with the same `CLAUDE_CONFIG_DIR`. Replacement discards root-local disposable trust/onboarding state and may re-prompt; the runbook must state this. |
| Native transcript moves under `CLAUDE_CONFIG_DIR` | The root is transcript-bearing. The launcher only reconciles known managed paths, `forget` retains the root, and destructive purge is deferred. |

The launcher only reads or writes its known managed paths: `CLAUDE.md`,
`settings.json`, `agents/cm-*.md`, and the generation manifest. In the
transcript-bearing outcome it does not enumerate, parse, copy, move, or delete
unknown Claude-owned files. Workflow run scripts and run state written under
the root's `projects/` tree are Claude-owned files under the same rule.

## 7. Generated artifacts

### 7.1 Agent files

One file is generated for each selected non-lead variant. The trusted role
prompt remains byte-identical across model variants of the same role. The
frontmatter is derived from the resolved catalog variant:

```markdown
---
name: cm-implementer-sol-high
description: <generated role/model/routing/preferred description>
model: gpt-multi-sol-high
effort: high
isolation: worktree
disallowedTools: Agent
---

<canonical cm-implementer prompt>
```

Every selected non-lead variant is generated with exactly
`disallowedTools: Agent`. Official documentation makes nested subagents the
platform default and names this frontmatter as the mechanism that prevents a
specific subagent from spawning others; the composition therefore does not
depend on any platform default for its lead-only delegation rule. The main
thread retains the Agent tool. The same field is added to legacy `--agents`
JSON in the same reviewed change for parity, together with the `roles.json`
delegation contracts and lead/session policy wording: specialist Agent denial,
role contracts, and policy wording change atomically.

No specialist `permissionMode` is relied upon to strengthen or weaken the
lead: the parent permission-mode matrix of §11 applies, and a delegated agent
inherits the session's permission context as the platform defines it. No
`hooks`, `memory`, `mcpServers`, or `skills` fields are generated; they are
rejected architecture (§4).

Fields are included only after the pinned native probe proves exact semantics.
Unsupported or silently ignored model, effort, isolation, tools, or deny fields
are a G1 failure unless an equally persistent, simpler representation is
verified. C\* must not claim parity while silently dropping a lane or
isolation. `ultracode` is never emitted in agent frontmatter; it is a
lead-only session effort.

No persistent `cm-lead` agent is generated. The main thread remains the lead.

### 7.2 `settings.json`

The generated settings start from the repository's trusted settings asset and
add only pinned-version-verified keys. Ownership is explicit:

- The trusted asset owns the baseline workflow policy keys
  (`disableWorkflows`, `workflowSizeGuideline`, `workflowKeywordTriggerEnabled`).
- The composition workflow mode owns the `disableWorkflows` value in generated
  settings. Under `native`, the trusted asset value (`false`) stands. Under
  `off`, the generator emits only `disableWorkflows: true` for the flip; it
  adds no other workflow key and never uses a workflow environment variable.
  The trusted asset may stay `false`: the generated mode owns the flip.
- Generated security policy (probe-gated): `permissions.deny` Agent-type
  denies, `availableModels` set to the exact resolved composition selector set
  (lead plus every selected variant), `worktree.baseRef: "head"`,
  composition-owned `effortLevel` only when takeover/reload evidence requires
  it, and `disableAgentView: true` only when its probe proves background
  subagents remain functional with agent view disabled.
- To preserve the current managed-session UX without pretending that an
  isolated root overlays normal Claude, the launcher may snapshot a closed
  allowlist of non-secret preferences from the ordinary user
  `~/.claude/settings.json` at session creation: permission default mode,
  denied MCP server names, statusline command, dangerous-mode prompt choice,
  model-switch UI preference, and agent notification preference. Workflow
  enablement is never inherited: the composition mode and the trusted asset
  own every `workflow*` key. Lead model and effort remain composition-owned
  and are not inherited. Every managed-file generation event, including
  deliberate transition, re-snapshots current allowlisted preferences.
  Snapshotted values pass the existing trusted-data secret-shape scan before
  emission. Unknown keys are omitted with a diagnostic; credentials, plugin
  state, MCP OAuth data, history, arbitrary hooks, and personal workflow
  content are never copied. On same-feature conflicts, the repository's
  trusted settings asset and generated security policy win.

The `availableModels` bound is an honest one: arbitrary external selector
drift is bounded to the composition set, because out-of-set environment,
CLI, or per-invocation values are officially skipped in favor of the
inherited in-set model. In-set per-invocation overrides and user `/model`
changes inside the set are accepted residuals, not silent drift; for native
workflows the bound is probe-required and in-set stage routing remains
accepted.

With `worktree.baseRef: "head"`, an isolated implementer branches from the
session's local HEAD: it sees committed lead work but cannot see uncommitted
lead work. Handoff to an isolated implementer is therefore either independent
of uncommitted state or gated on an explicit commit checkpoint the lead
records before delegating.

`off` mode and lead `ultracode` interact on the pinned platform: official
documentation removes `ultracode` from the `/effort` menu when workflows are
disabled. P-wf therefore probes `--effort ultracode` under
`disableWorkflows: true`. If the probe proves incompatibility, the compiler
derives `ultracode` → `xhigh` for that off-mode composition at launch; the
catalog, models, and composition documents remain untouched, and the derived
mapping ships in the same reviewed/activated change as the settings flip. If
the probe proves compatibility, no mapping is emitted. There is no later
catalog migration in either outcome.

The intended persistent Agent-type policy is:

- deny generic `Agent(claude)`;
- deny `Agent(general-purpose)` when off;
- deny `Agent(Explore)` when replaced/off;
- deny `Agent(Plan)` when off.

If settings-level `Agent(type)` deny syntax is not verified, C\* may retain an
argv deny as defense in depth, but the design records deny loss across daemon
takeover as a residual risk. No hook is added merely to compensate without a
separate Oracle-reviewed decision.

#### Native workflow guarantee

Under `native` mode the following wording is the exact guarantee; it is
repeated in generated session policy, `sessions show`, Doctor, and the
runbook:

- The Agent-tool inventory is selected-only. The native workflow runtime is a
  composition-owned exception with a lead-model default, platform-hardcoded
  `acceptEdits` for every workflow agent, and no worktree isolation, no cm
  role contracts, and no family-routing guarantees for workflow fan-out.
- Changes produced by workflow fan-out count as lead-family authored for the
  independent-review rules.
- Workflow executors may prompt mid-run — non-allowlisted shell, web-fetch,
  and MCP tool calls — even when the session runs under `bypassPermissions`,
  because workflow agents always run in `acceptEdits` regardless of the
  session mode. Potential stalls are displayed, not hidden.
- Workflow `agent()` spawns do not consume the session's 200-per-session
  Agent-tool subagent budget; they are bounded by the platform's per-run caps
  of 16 concurrent agents and 1,000 agents per run.
- The platform `Large workflow` warning is advisory only and is suppressed
  under `ultracode`; `workflowSizeGuideline` is advisory guidance sent to the
  model, not a cap.

The no-substitution sentinel of §7.3 remains scoped to Agent-tool `cm-*`
types; the workflow paragraph in generated session policy is a separate
statement, so the two never contradict.

### 7.3 `CLAUDE.md`

The generated root `CLAUDE.md` is role-neutral session policy addressed to
every reader the platform loads it for:

- The main thread is the lead and owns integration and final synthesis.
- A delegated `cm-*` reader follows its own system role definition; this file
  never reassigns its role or grants lead authority.
- Delegation decisions are made only by the main thread: specialists hold no
  Agent tool (§7.1), so a specialist that needs its task split reports the
  split back instead of delegating.

It states the selected variant inventory, the session-specific no-substitution
sentinel, and the workflow-mode paragraph. The sentinel is valid for both
audiences — the lead when delegating, and any delegated reader when asked to
substitute:

```text
Managed session: U
If a selected cm-* type is unavailable, stop delegation. Never substitute a
native or generic agent. Ask the user to run:
claude-multi --composition <name> -r U
```

The workflow paragraph is mode-dependent and separate from the sentinel. Under
`native`:

```text
This composition permits Claude's native dynamic-workflow runtime. Workflow
executors are platform agents outside the cm-* registry: they default to the
lead model, always run acceptEdits, receive no worktree isolation, and carry
no cm role contract. Their changes count as lead-authored for review
independence. The sentinel above governs Agent-tool delegation only.
```

Under `off`:

```text
Native dynamic workflows are disabled in this composition
(disableWorkflows: true); the ultracode keyword and bundled workflow commands
are inert.
```

The probe must verify that this config-root `CLAUDE.md` is loaded on startup,
resume, compaction, and daemon takeover for both the main thread and delegated
`cm-*` readers, and that its precedence does not erase or unexpectedly
override project instructions. The generated text never embeds secrets.

### 7.4 Generation manifest

The manifest contains no secret or transcript data:

```json
{
  "version": 1,
  "session_id": "U",
  "runtime_mode": "session-config-v1",
  "catalog_hash": "sha256:...",
  "composition_hash": "sha256:...",
  "managed_files_hash": "sha256:..."
}
```

The manifest is written last and proves that all known managed files correspond
to one resolved composition. `composition_hash` covers the workflow mode
through the recorded snapshot (§5). The manifest is not a claim that Claude's
native transcript exists.

## 8. Launch and persistence transaction

`Runtime.prepare` remains effect-free. It returns the pure launch plan, session
record, and a pure mapping of managed relative paths to bytes for C\*.

`perform_launch` preserves current ordering:

1. verify the exact inspected Claude binary;
2. perform loopback-only gateway readiness;
3. reconcile/generate the session config root;
4. persist the launcher session record and per-CWD pointer;
5. `execve` the inspected binary with `CLAUDE_CONFIG_DIR` and gateway env.

Official file watching covers only `agents/` directories that existed when the
session started; creating a scope's first `agents` directory after startup
requires a restart. C\* therefore always assembles the complete `agents/`
directory before `execve`, and deliberate transitions reconcile files inside
the already-watched directory. Add, edit, and remove watcher behavior, the
first-directory restart rule, and persistent-file survival versus CLI-only
`--agents` loss are probe and test scope (§12).

### Fresh C\* session

The complete root is assembled in a private sibling temporary directory,
fsynced, and atomically renamed to `claude-config/U` before the record is saved.
No native transcript exists yet, so whole-directory installation is safe.

If `execve` raises `OSError`, the fresh record, pointer, and newly generated root
are removed because the native session never started.

### Exact C\* resume

The existing root must exist, be owner-controlled, not be a symlink, and contain
a valid manifest matching the record. Recorded-snapshot resume reuses the root
without regeneration. In the configuration-only P-t outcome, a missing root is
regenerated deterministically from the record only when the existing drift
check passes; otherwise resume fails closed. In the transcript-bearing outcome,
a missing root is always a hard failure because the native transcript may also
be missing.

### Workflow run state across process exit

Workflow scripts and per-run state live under the root's `projects/` tree as
Claude-owned files. Official semantics resume a stopped run only within the
same live session: after the Claude process exits, the next session starts the
workflow fresh. C\* makes no cross-exit workflow resume claim, and the launcher
never parses workflow artifacts. `cleanupPeriodDays` governs their retention;
the launcher does not clean them.

### Deliberate composition transition on the same UUID

If P-t proves the root is configuration-only, the launcher stages and replaces
the complete root exactly as for fresh creation. If P-t proves the root is
transcript-bearing, the root itself is never replaced. Only known managed files
are reconciled under a per-session preparation lock while no supported managed
process for the UUID is running:

1. pre-read the exact old launcher-managed bytes into memory;
2. generate the target managed file mapping in memory;
3. atomically write new/changed agent files, settings, and `CLAUDE.md`;
4. remove obsolete managed `cm-*` files only after replacements are durable;
5. write the new manifest last;
6. save the new launcher record;
7. exec the target composition.

If an ordinary error or `execve` failure occurs, restore the pre-read exact old
managed bytes and old record; do not regenerate rollback bytes from a changed
catalog. A hard crash before record save leaves a manifest or record mismatch.
The next launcher offers reconciliation from the old record only when the
existing catalog/composition drift check still passes; otherwise it fails
closed with exact transition/forget guidance. The launcher never copies unknown
Claude files.

Opening the same UUID concurrently is unsupported. The launcher uses a
preparation lock to prevent simultaneous launch preparation but cannot hold a
lock across `execve` without becoming a supervisor. The runbook states that a
second live process for the same UUID can corrupt native state and must not be
started.

## 9. Session commands and deletion

`sessions show` reports runtime mode, config-root path, manifest status,
catalog/composition hashes, workflow mode, inspected client version, and legacy
limitations. Under `native` it also surfaces the workflow caveats of §7.2:
`acceptEdits`/no-worktree exception, potential mid-run stalls, and the
platform caps of 16 concurrent agents and 1,000 agents per run. The
quick-confirm TUI shows the composition's workflow mode before launch, and the
runbook repeats the same display contract. `show` does not inspect native
transcript contents.

For C\* sessions, `sessions forget U` follows the proven P-t outcome:

- configuration-only root: remove the record/pointer and generated root;
- transcript-bearing root: remove only the record/pointer, retain the root, and
  report its orphan path.

Destructive purge of a transcript-bearing root is excluded from the first
implementation. A future explicit purge command requires separate confirmation
and review.

`sessions link` remains metadata-only, creates `legacy-argv`, and clearly labels
native existence as unverified. It cannot create or migrate C\* state.

Doctor additionally reports the workflow mode of managed records with the
same native-mode caveats, verifies the managed-scope agent absence assumption
of §11, and reports per-root supervisor status through the official
root-scoped `claude daemon status` only after its probe passes (§12).

## 10. Binary trust and G0' hardening

Managed launch verifies the native contract's immutable `resolved_path`,
version basename, executable bit, and full SHA-256 directly. Movement of the
configured convenience symlink is informational, not fatal, when the inspected
artifact remains intact. Plain Claude may update independently.

Doctor uses the identical resolver and additionally reports:

- inspected managed client version and hash status;
- current configured-symlink target as informational drift;
- shared daemon version/status when available through non-transcript status
  metadata;
- newer unqualified installation availability;
- session-config manifest health for managed records.

Managed launch sets `DISABLE_AUTOUPDATER=1` as hygiene but does not claim this
controls the shared daemon. Native generic aliases are recorded per inspected
version in the native contract; `Agent(claude)` is denied for the current
version.

### Delegation and determinism policy on the pinned platform

Official documentation states that nested subagents are enabled by default
(since v2.1.172) and that the depth limit is fixed at five and not
configurable: a subagent at depth five does not receive the Agent tool. The
composition does not depend on any configurable depth: every generated
specialist file carries `disallowedTools: Agent` (§7.1), so only the
main-thread lead can delegate. The negative probe must show a typed Agent
denial when a specialist attempts to spawn.

Environment facts on the pinned platform:

- `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` and
  `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` remain reserved-and-unset facts only;
  they are never compiled.
- `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` is reserved and explicitly unset so
  the platform default of 200 Agent-tool spawns per session applies; the probe
  verifies the default. Generated session policy notes that the lead shares
  this single budget across all of its Agent-tool delegation — every spawn
  counts, including background subagents and finished ones, while workflow
  `agent()` spawns do not.
- Managed launches compile `CLAUDE_CODE_FORK_SUBAGENT=0`, which officially
  disables fork mode everywhere including any server-side rollout, and
  reserve the key against model `lead.env` override.
- `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` is reserved and explicitly unset,
  forcing agent teams off.
- `CLAUDE_CODE_DISABLE_WORKFLOWS` is reserved and explicitly unset so the
  generated settings of §7.2 alone own the workflow mode.
- `disableAgentView: true` is emitted into generated settings only if a
  dedicated probe proves background subagents remain functional with agent
  view disabled on the pinned version. If the probe fails or is inconclusive,
  agent view is left enabled, arbitrary agent-view dispatch is documented as
  explicit user action, and root-scoped `claude daemon status` provides
  observability.

The native contract must be reinspected for 2.1.217 before promotion. Existing
unverified fork and same-launch contracts remain unverified unless the probe
actually establishes them.

## 11. Project and user configuration boundaries

C\* must not copy or symlink normal `~/.claude` private state into a session
root without an explicit reviewed requirement. Gateway authentication remains
environment/token based. Probe G1 determines which trust/onboarding and user
settings are required for a usable isolated root. Normal user credentials,
plugins, user/local MCP state, history, memory, personal workflows, and cache
do not layer into C\*; project `.claude`, project `CLAUDE.md`, `.mcp.json`,
and managed policy still do. This isolation is intentional and must be visible
in `show`/Doctor.

Official documentation states that each config root has a separate supervisor
and session roster: a distinct `CLAUDE_CONFIG_DIR` runs as a separate
supervisor instance with its own sessions. This per-config-root supervisor is
the primary daemon isolation for C\*; P0/P-bg verify it on 2.1.217 and measure
whether inactive per-session supervisors terminate cleanly. Acceptance requires
the supervisor and pre-warmed workers to exit within a probe-defined bounded
window after the client exits, with zero lingering processes after repeated
create/exit cycles. Failure rejects C\* through the P-bg Oracle gate; it never
adds launcher-side daemon cleanup or control.

### Agent definition discovery

Official precedence is: managed-settings agents above `--agents` CLI above
project `.claude/agents` above user-root agents above plugin agents. In C\*
the generated files are the user-root scope and the higher-precedence
`--agents` overlay is gone, so any project or `--add-dir` agent definition
would outrank or extend the selected inventory. Project definitions are
discovered by walking up from the working directory to the repository root,
and `.claude/agents` inside each `--add-dir` target loads alongside them.

C\* therefore fails closed on ANY discovered agent definition — not only
`cm-*` name collisions — in:

- every `.claude/agents/` directory from the session working directory up to
  the repository root, including nested definitions where the closest one
  wins by official rule; and
- `.claude/agents/` inside every parsed passthrough `--add-dir` target.

A non-git working directory (no repository root to bound the walk), an
unreadable path, or a symlink escape fails closed. The failure lists every
discovered path and the remedy: remove or rename the definition, or run the
session elsewhere. The launcher does not parse unrelated agent prompt bodies.

Managed-scope agent definitions (highest precedence, host-deployed) cannot be
scanned portably. C\* assumes their absence on this host; Doctor verifies that
assumption, and detection blocks C\* launch until the host is remediated. The
documented `Agent(claude)` generic-alias deny remains compiled from the
native contract per inspected version.

### Permissions and delegation invocation

Official permission semantics bind the delegated agent to its parent, so the
launcher never relies on a specialist `permissionMode` to strengthen the
lead:

| Parent (session) mode | Specialist frontmatter `permissionMode` |
| --- | --- |
| `bypassPermissions` or `acceptEdits` | parent mode takes precedence and cannot be overridden |
| auto | specialist inherits auto; frontmatter value is ignored |
| any other | frontmatter value applies; the generator emits none |

Invocation guarantees are equally bounded. An explicit `@agent-<name>`
mention guarantees dispatch of that type for one task. Natural-language and
description-based automatic routing is behavioral quality only, and file
persistence means availability and discovery, not mandatory use: the lead may
still choose direct work, which the role contract already permits. The
sentinel and lead policy are the behavioral layer; the generated files and
denies are the structural layer.

Project/local `.claude/settings*.json` sources may layer after generated user
settings. Official permission evaluation applies deny before ask/allow across
scopes, so project/local allow should not weaken a generated `Agent(type)`
deny; G2 verifies pinned-CLI parity, the documented `Agent(claude)` alias,
and that project/local layering cannot flip the composition workflow mode or
widen generated `availableModels` — or the approved fallback is recorded.

Plain `claude` receives no `CLAUDE_CONFIG_DIR` from Home Manager and remains
unchanged.

## 12. Capability probe contract

All probes use a disposable HOME/XDG root, the inspected Claude binary, a local
loopback fake provider, fixed non-sensitive prompts, no real OAuth/provider
request, and no user transcript. A real TTY is used where Claude requires one.

### P0 daemon/background isolation

The official per-config-root supervisor is the primary isolation mechanism: a
disposable `CLAUDE_CONFIG_DIR` fixture root gives the probe its own supervisor
instance, session roster, and state tree by construction, disjoint from the
plain-Claude daemon. The user-namespace sandbox (DeepWork `ora-24` approved
design) remains probe-harness defense-in-depth only: it exists to guarantee no
host credential, network, provider, or live-daemon contact during probes, and
it is never production architecture. Static binary hints about
config-root-hashed socket subdomains are not treated as compatibility
guarantees.

Before any native probe, the harness empirically proves its supervisor/socket
domain is disjoint from the live uid-keyed daemon (`/tmp/cc-daemon-<uid>`) and
aborts otherwise. No probe may register with, signal, or restart the live
daemon. Daemon-takeover evidence comes from the fixture-root supervisor or the
documented client-side persistent-config reload simulation; real takeover
remains user acceptance.

P0 also probes the official root-scoped `claude daemon status` (reachability,
version, socket directory, worker count) before Doctor adopts it for per-root
supervisor reporting.

### G1 mandatory probes

1. **P1 discovery:** the complete `agents/` directory exists before exec;
   selected config-root agent files appear and dispatch by exact ID;
   unselected IDs do not; watcher add/edit/remove is detected without restart;
   the first-directory-after-start restart rule is recorded; persistent files
   survive a restart that loses CLI-only `--agents` definitions.
2. **P2 policy:** persistent settings deny generic/native agent types exactly;
   a specialist's `disallowedTools: Agent` yields a typed Agent denial on a
   spawn attempt; the documented `Agent(claude)` alias behaves as recorded.
3. **P3 onboarding:** a fresh root has acceptable trust/onboarding/auth
   behavior and no repeated prompt that makes per-session roots impractical;
   functional permission mode is preserved without copying
   credentials/plugins/history, and the parent permission-mode matrix is
   confirmed (parent `bypassPermissions`/`acceptEdits` take precedence and
   cannot be overridden by specialist frontmatter; parent auto mode is
   inherited and ignores specialist `permissionMode`). Statusline and
   remaining preference differences are recorded diagnostics, not C\*
   rejection criteria.
4. **P-t transcript:** determine whether native transcript state stays in the
   ordinary store or moves under the root; in either outcome, kill and exact
   resume with the same root must retain visible context. Apply the outcome
   semantics from sections 6, 8 and 9.
5. **P8 parity:** model selector, high/xhigh/max effort, tools and worktree
   isolation are effective; unsupported fields are detected, not inferred.
   Official docs support the fields, but third-party selectors remain a pinned
   gateway contract. `ultracode` is never emitted in agent frontmatter. P8
   additionally verifies generated `worktree.baseRef: "head"` (worktree base
   commit equals the session HEAD, uncommitted lead work is invisible to the
   isolated agent, the worktree lock is held while the agent runs, a
   changed worktree is retained for the periodic sweep, and enter/exit path
   transitions behave) and the generated `availableModels` bound (settings and
   gateway `/models` surfaces, CLI/per-invocation out-of-set values skipped to
   the inherited model, in-set overrides honored).
6. **P-bg inheritance and lifecycle:** lead-spawned background and
   daemon-hosted agents retain the session config root and exact type; config
   root, exact type/ID, model and effort selectors, tools policy, gateway/auth
   environment, and worktree isolation are verified across process stop,
   wake, respawn, supervisor update, and compaction. Background subagents
   remain functional under the compiled pins (`CLAUDE_CODE_FORK_SUBAGENT=0`,
   agent-view state as decided in §10). Managed sessions are foreground
   sessions: their stop/wake cycles are driven through `claude-multi` exact
   resume, never through agent-view dispatch. Separate-supervisor lifecycle
   and the 200-per-session Agent-tool budget behave as recorded, and no
   prewarm/daemon processes linger after bounded repeated cycles.
7. **P9 lead policy:** the role-neutral root `CLAUDE.md` loads on start,
   resume, reload, and compaction for the main thread and for delegated `cm-*`
   readers, with the sentinel valid for both audiences and project
   instructions still layering after it.
8. **P-project discovery:** any project or `--add-dir` agent definition fails
   closed across the nested working-directory-to-repository-root walk and
   every parsed passthrough directory; non-git, unreadable, and symlink-escape
   cases fail closed; the error lists paths and remedy. Project/local settings
   cannot weaken generated Agent denies, flip the composition workflow mode,
   or widen generated `availableModels`, or the approved fallback is recorded.
9. **P-wf workflow mode:**
   - `native`: keyword/effort-trigger behavior follows the trusted asset;
     workflow scripts and run state land under the root's `projects/` tree;
     workflow agents run with platform-hardcoded `acceptEdits`; mid-run
     prompts surface under a `bypassPermissions` session; the 16-concurrent
     and 1,000-per-run platform caps and the `Large workflow` warning behave
     as documented (suppressed under `ultracode`); workflow stage routing
     honors `availableModels` (in-set routing accepted); gateway/auth
     environment reaches workflow agents; no personal `~/.claude/workflows`
     content appears in the root.
   - `off`: only `disableWorkflows: true` is emitted; the keyword and bundled
     workflow commands are inert; the `--effort ultracode` interaction is
     recorded and decides the derived `ultracode → xhigh` mapping of §7.2.
   - both modes: fresh-after-process-exit restart semantics hold (no cross-exit
     workflow resume claim) and `cleanupPeriodDays` covers workflow and
     subagent artifacts.

If P1 or P3 fails, or P-t cannot exact-resume in either storage outcome, C\* is
rejected. Transcript storage outside the root is a favorable supported result,
not failure. If P2/P8/P-bg/P9/P-project/P-wf fails, the blueprint returns to
Oracle with the exact evidence; no hook, environment-variable fallback, or
global registry is added automatically.

### G2 packaged lifecycle probes

1. when P0 proves a disposable daemon domain, daemon takeover after a disposable
   symlink upgrade retains selected agents, settings denies, lead policy and
   transcript; otherwise run only the documented client-side reload simulation;
2. repeated deterministic compaction retains the same;
3. two simultaneous different UUID/composition roots remain isolated;
4. project discovery fail-closed behavior matches §11 across fresh, resume,
   and transition paths;
5. project/local settings cannot weaken generated policy, or the approved
   fallback is recorded;
6. a root-present corrupt/missing managed file set, or any missing
   transcript-bearing root, produces an exact recovery error; a missing
   configuration-only root follows the drift-gated regeneration path;
7. watcher add/edit/remove lifecycle and the first-directory restart rule
   behave as recorded;
8. workflow native/off goldens, schema/snapshot cases, and `show`/TUI/Doctor
   displays match §7.2 and §9;
9. plain Claude in the disposable normal root remains unaffected.

The deterministic takeover/compaction loop is the gate; a multi-hour soak is
supplemental user acceptance, not the only evidence.

## 13. Failure behavior

| Failure | Required behavior |
| --- | --- |
| inspected binary missing/hash mismatch | block before readiness/state writes; report reinspection |
| convenience symlink points newer | use intact inspected path; report newer version in Doctor |
| C\* root missing on resume | configuration-only: regenerate when drift check passes, otherwise block with recovery guidance; transcript-bearing: always block |
| manifest mismatch | block and offer managed-file reconciliation without reading native files |
| selected agent unavailable | lead stops; generic substitution forbidden; exact resume command shown |
| generic/native denied agent requested | Claude policy denies with selected inventory guidance where supported |
| specialist attempts nested spawn | typed Agent denial from generated `disallowedTools: Agent`; lead routes any split |
| project or `--add-dir` agent definition discovered | block before root staging/exec; list every discovered path and the remedy |
| managed-scope agent definitions detected on host | block C\* launch; Doctor reports; remediate host |
| fresh legacy session from `off`-mode composition | block with exact guidance: `off` requires session-config mode |
| workflow probe failure | stop and return exact evidence to Oracle; no env-var fallback |
| workflow executor mid-run prompt under bypass | expected platform behavior (`acceptEdits` exception); answer the prompt or stop the run; runbook documents potential stalls |
| daemon takeover | persistent root reloads selected agents/settings/lead policy when P0/G2 prove it; otherwise real takeover remains user acceptance |
| legacy daemon takeover | loud recovery remains best effort; relaunch exact UUID |
| exec failure on fresh C\* | remove new record/pointer/root |
| exec failure during transition | restore pre-read exact old managed bytes and old record |
| `forget` C\* | remove config-only root, or retain/report transcript-bearing root according to P-t |
| same UUID already live elsewhere | unsupported; user must stop one process |

## 14. Security invariants

- UUID validation precedes path construction.
- No user composition name is used as a filesystem path component.
- `CLAUDE_CONFIG_DIR` and every other C\*-owned process key are reserved against
  model `lead.env` override. The reserved set includes
  `CLAUDE_CODE_FORK_SUBAGENT`, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`,
  `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION`, `CLAUDE_CODE_DISABLE_WORKFLOWS`,
  `CLAUDE_CODE_DISABLE_AGENT_VIEW`, and the two unverified nested
  depth/concurrency keys, which are unset facts and never compiled values.
- State parents and roots reject symlinks and group/other access.
- Managed files are regular 0600 files; generated directories are 0700.
- Generated content is strict catalog/session data and contains no provider
  token or private transcript.
- In the transcript-bearing outcome, unknown files under the config root are
  never read or removed by the launcher. In the configuration-only outcome,
  whole-root replacement/removal may discard Claude-owned disposable state but
  never a transcript, as proven by P-t; possible trust/onboarding re-prompts are
  documented.
- Agent-definition discovery fails closed on any project or `--add-dir`
  definition; managed-scope absence is verified by Doctor.
- Private roots copy no personal workflows, plugins, credentials, history, or
  memory.
- Generated `availableModels` is a closed selector set derived only from the
  resolved composition.
- The workflow mode is composition-owned, hash-covered, and recorded in the
  snapshot; field-bearing records require the launcher that ships this change.
- Native-contract alias/setting fields are closed vocabulary and versioned.
- Every write is atomic at file level; the manifest is the commit marker.

## 15. Rollout and rollback

1. Promote G0' and the disposable probes without changing active session mode.
2. Oracle reviews probe evidence and confirms G1.
3. Ship C\* behind a capability record; only new sessions select it.
4. Keep every old session legacy; no migration prompt.
5. Package and Home Manager build, then activate once. The `off`-mode settings
   flip and, when required by P-wf evidence, the derived `ultracode → xhigh`
   mapping ship in this same reviewed/activated change; there is no separate
   catalog migration.
6. Verify Doctor, proxy, trusted default, `kimi-sol`, and a disposable packaged
   C\* session.
7. User stops/relaunches only when ready and performs first real C\* acceptance.

Home Manager rollback restores the old launcher and leaves C\* roots untouched.
The runbook states that C\* sessions require the C\*-capable generation for
resume. With the current closed old schema, the rolled-back launcher rejects
field-bearing composition documents at validation, skips C\* records in
`sessions list`, and reports schema errors from `show`/`forget`. Doctor in the
C\*-capable generation reports config roots without readable records as
orphans; under a rolled-back generation, manual owner removal is the only
cleanup and must be documented. Source rollback anchor is recorded before
activation.

## 16. Definition of done

- G0' launch/Doctor trust semantics are identical and tested.
- G1 and G2 evidence is recorded against the inspected Claude version.
- New sessions use selected-only persistent agent files and survive the
  disposable daemon takeover when P0 provides an isolated daemon domain;
  otherwise they pass the documented client-side reload simulation and real
  takeover remains user acceptance.
- Generic Kimi-derived fallback is unavailable in C\*.
- Specialists are mechanically denied the Agent tool with typed-denial
  evidence; the lead retains sole delegation.
- The workflow mode defaults to `native`, is hash-covered and displayed, the
  `off` flip emits only `disableWorkflows: true`, and any derived
  `ultracode → xhigh` mapping ships in the same change as the flip.
- Legacy sessions remain readable and unchanged.
- Concurrent different sessions do not share mutable composition state.
- No real provider call occurs during implementation verification.
- Full focused unit/PTTY/probe/package checks pass.
- Oracle approves the blueprint, implementation plan, implementation phases,
  and final diff.
- Home Manager activation is healthy and rollback is documented.
- Source and wiki changes are committed; the user receives exact manual
  acceptance instructions.
