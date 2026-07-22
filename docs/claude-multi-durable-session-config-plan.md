# claude-multi durable session configuration implementation plan

> **FROZEN HISTORICAL ARTIFACT — DO NOT EXECUTE**
>
> Frozen on 2026-07-22 with its companion blueprint. This plan is evidence of
> prior reasoning, not an active implementation schedule. Start from
> [`claude-multi-rethink-handoff/PROMPT.md`](./claude-multi-rethink-handoff/PROMPT.md).

Status: historical and superseded. Historical blueprint:
[`claude-multi-durable-session-config.md`](./claude-multi-durable-session-config.md).

## 1. Delivery policy

- The blueprint is normative; probe evidence can reject C\* but cannot silently
  weaken its guarantees.
- No real provider request, user transcript read, live-daemon restart, or active
  Claude-session stop is permitted during implementation verification.
- The main orchestrator owns integration, sequencing, state reconciliation,
  activation and commits. Bounded specialists may own non-overlapping code/test
  lanes; Oracle is the architecture and phase reviewer.
- Existing source remains active until packaged gates and Oracle approval pass.
- Final ordering: packaged gates and the final Oracle review precede the source
  commit; Home Manager activation runs only from the committed, flake-visible
  source; documentation/wiki commits may follow activation.
- One Home Manager activation is the default, with the current generation
  recorded as rollback; an optional early activation immediately after Phase-1
  Oracle approval is permitted (G0' plus promoted contract only, C\* inert).
- Every Home Manager switch restarts `cli-proxy-api` and briefly cuts the
  gateway; live managed sessions must be checkpointed before any switch.
- The `off`-mode settings flip and, when P-wf evidence requires it, the derived
  `ultracode → xhigh` mapping ship in the same reviewed and activated change;
  there is no standalone or later catalog migration.
- Specialist Agent denial (`disallowedTools: Agent`), `roles.json` delegation
  contracts, and lead/session policy wording change atomically in one change
  set, gated by a typed-error negative probe; the interim "spawn-depth
  decision pending" wording is replaced, not layered.

## 2. Phase map and review gates

| Phase | Deliverable | Gate |
| --- | --- | --- |
| 0 | baseline, native 2.1.217 evidence, rollback anchors | evidence recorded |
| 1 | G0' trust/health hardening and no-provider probe harness | Oracle accepts G0' and G1 evidence |
| 2 | C\* or approved fallback, full focused coverage | lightweight Oracle 2A checkpoint, then Oracle accepts 2B+2C integration |
| 3 | packaged lifecycle checks, source commit, activation, docs/wiki commits | Oracle final APPROVE |

Planned Oracle reviews: two pre-implementation document gates (blueprint and
this plan, both re-approved in the same `ora-30` session after this revision)
plus four implementation reviews (Phase-1 gate, Phase-2A lightweight
checkpoint, Phase-2B+2C integration review, Phase-3 final). Any hard probe
failure returns to the Phase-1 Oracle gate before Phase 2. Phase 3 orders
packaged gates and the final Oracle review before the source commit, and the
source commit before activation.

## 3. Phase 0 — baseline and contract evidence

### 3.1 Repository/runtime baseline

Record in DeepWork:

- source HEAD/status and intended diff scope;
- active Home Manager generation and previous rollback generation;
- active `claude-multi` package and proxy service ExecStart/PID;
- current `kimi-sol` and trusted-default summaries;
- current configured Claude symlink target;
- retained 2.1.216 and current 2.1.217 paths/hashes;
- shared daemon version/status metadata without reading transcripts.

### 3.2 Native 2.1.217 inspection

Offline/local inspection only:

- version/path/full SHA-256/executable bit;
- CLI help for agents, settings, config-dir, model, effort, session and fork
  surfaces;
- static evidence for config-dir/updater/background controls;
- official-documentation references already captured by `lib-1` and the six
  local snapshots.

Update `catalog/native-contract.json` and its closed schema only with facts
actually established. Keep fork and same-launch acceptance unverified. Add or
revise closed fields for:

- generic native agent aliases for policy compilation;
- session-config capability/probe statuses;
- the nested-delegation record: official fixed depth five, enabled by default,
  not configurable; the composition decision is mechanical specialist denial
  via generated `disallowedTools: Agent`; the depth/concurrency environment
  keys are recorded as reserved-and-unset facts, never compiled;
- workflow capability statuses: `native` runtime probes, `off`-mode flip,
  `--effort ultracode` interaction and the derived-mapping decision;
- `availableModels`, `worktree.baseRef`, per-root `claude daemon status`, and
  conditional agent-view disable statuses;
- deterministic pin records (`CLAUDE_CODE_FORK_SUBAGENT=0`, agent-teams
  force-off, subagent-session budget default 200);
- inspected-version lifecycle evidence identifier.

### 3.3 Versioning

Bump launcher/package version to `2.1.0` only when the runtime-mode record and
G0' behavior are implemented. Catalog version remains 1 unless a catalog data
shape consumed by external records requires a deliberate bump. The workflow
mode field is additive-optional: absent means `native`, so catalog and record
shapes stay consumable by old launchers until a composition declares the
field.

## 4. Phase 1A — G0' hardening

### 4.1 Direct immutable resolver

`launch.resolve_claude` verifies the native contract's `resolved_path` directly:

- absolute expected path exists, is regular, executable and non-symlink;
- basename equals validated version;
- full SHA-256 matches.

The configured symlink is resolved only for an informational status object. A
newer symlink target does not block launch when the inspected artifact remains
valid. Missing/tampered inspected artifact blocks before readiness/state writes.

### 4.2 Doctor parity

Doctor calls the same resolver and reports:

- inspected managed version/hash status;
- configured symlink target and newer unqualified version;
- shared daemon status/version when exposed through non-transcript metadata;
- actionable reinspection guidance.

Doctor must not report Ready when launch would fail binary verification.

### 4.3 Native policy and environment

- `compile_native_policy` consumes contract-recorded generic aliases and denies
  `Agent(claude)` plus existing policy-denied natives.
- `compile_environment` sets `DISABLE_AUTOUPDATER=1` as non-load-bearing
  hygiene and compiles `CLAUDE_CODE_FORK_SUBAGENT=0`.
- Reserve against model `lead.env` override, and explicitly unset where noted:
  `CLAUDE_CONFIG_DIR`, the updater key, `CLAUDE_CODE_SUBAGENT_MODEL`,
  `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` (unset; platform default 200),
  `CLAUDE_CODE_DISABLE_WORKFLOWS` (unset; generated settings own the mode),
  `CLAUDE_CODE_DISABLE_AGENT_VIEW` (unset pending its conditional probe),
  `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` (unset; teams forced off), and both
  nested depth/concurrency keys (unset facts, never compiled).
- Generated lead appendix includes the exact session UUID and a no-substitution
  sentinel/relaunch command, plus the shared 200-per-session Agent-tool budget
  note.
- Specialist Agent denial, `roles.json` contracts, and lead/session policy
  wording ship atomically in the Phase-2 change set (§7, §9), replacing the
  interim pending-decision wording; no prompt may promise or forbid a
  capability the pinned platform contradicts.

### 4.4 G0' tests

Focused tests cover:

- symlink moved while inspected artifact remains valid;
- missing, symlinked, non-executable or hash-mismatched inspected artifact;
- Doctor and launch parity;
- generic alias deny goldens;
- updater/reserved environment behavior, including every new reservation and
  the fork pin;
- sentinel exact UUID/composition and the budget note;
- no provider path and no state write on failure.

## 5. Phase 1B — disposable capability probe harness

### 5.1 Harness location and safety

Add an explicitly gated developer command under `claude-multi-dev`, backed by a
stdlib-only probe module and tests. It requires:

```text
--allow-local-claude
--fixture-root <private disposable path>
```

It refuses:

- normal HOME/XDG paths;
- any config root outside the fixture;
- non-loopback base URLs;
- real tokens/credentials;
- a daemon/socket/backend that is not proven disjoint from the live daemon.

The fixture uses dummy credentials and a local stdlib fake Anthropic endpoint.
It captures request metadata/tool schema necessary for assertions but writes no
prompt/transcript content to source or wiki.

### 5.2 P0 isolation

The primary isolation mechanism is the official per-config-root supervisor: a
disposable fixture `CLAUDE_CONFIG_DIR` runs its own supervisor instance,
session roster, and state tree by construction. The `ora-24` user-namespace
sandbox remains defense-in-depth for the harness only — no host credential,
network, provider, or live-daemon contact — and is never production
architecture. Before starting Claude, empirically prove the fixture
supervisor/socket domain is disjoint from the live uid-keyed daemon; if the
proof is unavailable, abort all daemon/bg probes and permit only client-side
config reload simulation. Never signal, restart, register work with, or alter
the live daemon. Probe the official root-scoped `claude daemon status` for
reachability, version, socket directory, and worker count before Doctor adopts
it.

### 5.3 G1 probes

Run and record:

1. **P1:** complete `agents/` directory before exec; selected root-agent
   discovery; unselected absence; watcher add/edit/remove detection; the
   first-directory-after-start restart rule; persistent-file survival versus
   CLI-only `--agents` loss.
2. **P2:** persistent Agent denies, especially documented `Agent(claude)`;
   typed Agent denial when a specialist with generated
   `disallowedTools: Agent` attempts a nested spawn.
3. **P3:** onboarding/trust/auth and functional permission mode, including the
   parent permission-mode matrix (parent `bypassPermissions`/`acceptEdits`
   precedence; auto-mode inheritance; specialist `permissionMode` never
   strengthens the lead); statusline is diagnostic only.
4. **P-t:** documented transcript-bearing location and exact resume with the
   same root; accept config-only only if pinned behavior differs and resume works.
5. **P8:** third-party selectors, high/xhigh/max effort, tools and worktree
   isolation; negative controls detect ignored fields; generated
   `worktree.baseRef: "head"` base-commit/visibility/lock/retention/path
   semantics; generated `availableModels` across settings and gateway
   `/models`, CLI/per-invocation out-of-set fallback to inherited, and in-set
   overrides.
6. **P-bg:** background/daemon inheritance; config root, exact type/ID,
   model/effort selectors, tools policy, gateway/auth environment, and
   worktree isolation across stop, wake, respawn, supervisor update, and
   compaction; background subagents functional under the compiled pins and the
   decided agent-view state; separate-supervisor lifecycle with wakes driven
   through `claude-multi` exact resume; the 200-per-session Agent-tool budget;
   no lingering prewarm/daemon processes after bounded repeated cycles.
7. **P9:** role-neutral root `CLAUDE.md` on start/resume/reload/compact for the
   main thread and delegated readers; sentinel valid for both audiences;
   project instructions still layer.
8. **P-project:** any project or `--add-dir` agent definition fails closed
   across the nested walk and passthrough directories, with non-git,
   unreadable, and symlink-escape cases; project/local settings cannot weaken
   generated denies, flip the workflow mode, or widen `availableModels`.
9. **P-wf:** workflow mode probes per blueprint §12 — `native` runtime
   behavior (script/run state under root `projects/`, hardcoded `acceptEdits`,
   mid-run prompts under `bypassPermissions`, 16/1,000 caps, suppressed
   `Large workflow` warning under `ultracode`, `availableModels` in/out-set
   routing, gateway/auth reach, no personal workflows in the root), `off`-mode
   flip semantics and the `--effort ultracode` interaction, and
   fresh-after-process-exit plus `cleanupPeriodDays` coverage in both modes.

### 5.4 Mechanical specialist Agent denial (fixed decision)

Official documentation makes nested subagents the platform default with a
fixed, non-configurable depth limit of five. There is no compile-or-prompt
decision chain:

1. The contract records the official default and the composition decision:
   every generated specialist file carries `disallowedTools: Agent`; the main
   thread retains the Agent tool.
2. The same reviewed change rewrites `roles.json` delegation contracts and
   lead/session policy wording to lead-only delegation and adds the field to
   legacy `--agents` JSON for parity — one atomic change, no interim wording
   left behind.
3. The G1 negative probe requires a typed Agent denial when a specialist
   attempts a nested spawn; an untyped or silent outcome is a probe failure.
4. `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` and
   `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` are recorded as reserved-and-unset
   facts only; no launcher code compiles either key.
5. `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION` is reserved and unset; the probe
   verifies the platform default of 200 and which spawn kinds count (Agent-tool
   spawns including nested, forks, and background; workflow `agent()` spawns
   excluded).

### 5.5 Decision record

Write machine-readable probe results to a private DeepWork evidence artifact and
promote only closed status/evidence IDs into the native contract.

- P1, P3, exact P-t or supervisor cleanup failure: reject C\*.
- P2/P8/P-bg/P9/P-project/P-wf failure: stop and return exact evidence to
  Oracle; do not add hooks, environment-variable fallbacks, or global files
  automatically.
- All mandatory gates pass: approve Phase 2 C\*.

## 6. Phase 1 ownership and validation

Potential non-overlapping lanes:

- **G0 implementation:** resolver, compiler policy, Doctor and focused tests.
- **Probe harness:** developer probe module, fake local endpoint/PTTY fixtures,
  probe tests only.

The main integrator exclusively owns `catalog/native-contract.json` and both
closed schemas (native contract schema and session schema); lanes propose
contract/schema values, the integrator writes them. The integrator reconciles
both lanes, runs focused suites, updates DeepWork, and requests one Oracle
phase review. No Phase 2 writer starts before the gate.

## 7. Phase 2A — runtime mode and pure generation

### 7.1 Session record compatibility

Extend the closed session schema with optional fields:

```json
"runtime_mode": "legacy-argv" | "session-config-v1",
"managed_config_hash": "sha256:..."
```

and one optional snapshot field, present only when the composition document
declares `workflows`:

```json
"workflow_mode": "native" | "off"
```

Missing mode means legacy, and a missing workflow mode means `native`.
`sessions link` always writes legacy. Fresh sessions select C\* only when the
native capability status is verified. Existing field-less records remain valid
and their drift checks are byte-identical to today; field-bearing composition
documents and records require this launcher generation because old closed
schemas reject the unknown keys.
The optional `runtime_mode` and `managed_config_hash` fields are emitted only
for `session-config-v1` records. Every `legacy-argv` record written by the new
launcher retains today's field-less shape and must validate against the old
closed session schema, preserving rollback readability.

### 7.2 Pure session-config generator

Add a focused module or compiler submodule that accepts only validated inputs
and returns `{relative_path: bytes}` plus a managed-files hash:

- selected `agents/*.md` with proven frontmatter, each carrying exactly
  `disallowedTools: Agent`;
- generated `settings.json` per blueprint §7.2: trusted asset baseline,
  composition-owned `disableWorkflows` value (`off` emits only
  `disableWorkflows: true`; no workflow environment variable), probe-gated
  `availableModels`, `worktree.baseRef: "head"`, conditional `effortLevel`,
  conditional `disableAgentView`;
- generated role-neutral `CLAUDE.md` session policy with sentinel and the
  mode-dependent workflow paragraph;
- generation manifest.

When P-wf proves `--effort ultracode` incompatible with `disableWorkflows:
true`, the compiler derives `ultracode → xhigh` for that off-mode composition
at launch; catalog, models, and composition documents are untouched and the
mapping ships in the same reviewed change as the flip.

The generator also reads the ordinary user settings only through a closed
preference extractor:

- strict JSON object;
- allowlisted keys/subkeys only, excluding every `workflow*` key (the
  composition mode and trusted asset own them);
- existing secret-shape scan on emitted values;
- trusted settings win conflicts;
- unknown/invalid preferences produce diagnostics, never silent broad copying;
- no personal workflow, plugin, credential, history, or memory content is
  copied.

Pure generation receives no provider token and no transcript path/content.

### 7.3 Project agent discovery scanner

Inspect public agent-definition locations frontmatter-only and fail closed on
ANY discovered definition, not only `cm-*` name collisions:

- every `.claude/agents/**/*.md` from the session working directory up to the
  repository root;
- `.claude/agents/**/*.md` inside every parsed passthrough `--add-dir` target.

Do not parse prompt bodies. Non-git working directories, unreadable paths, and
symlink/path escapes fail closed. The failure lists every discovered path and
its remedy. Managed-scope definitions are covered by the host-absence
assumption plus the Doctor check (blueprint §11); detection blocks launch.
The scan runs on every launch path — fresh (§8.2), exact resume (§8.3) and
deliberate composition transition (§8.4) — and fails closed before any root
staging/reconciliation or exec.

## 8. Phase 2B — config-root transaction

### 8.1 State primitives

Add symlink-safe private-tree helpers only as required:

- private root creation/staging;
- known managed-file read/write/remove;
- directory fsync;
- manifest-last commit;
- per-session preparation lock;
- exact rollback from pre-read bytes.

Unknown Claude-owned files — including workflow scripts and run state under
the root's `projects/` tree — are untouched in the transcript-bearing mode.

### 8.2 Fresh session

After binary/readiness checks:

1. run the project agent discovery scan;
2. stage the full private root with the complete `agents/` directory (the
   official watcher only covers directories that exist at session start);
3. atomically install root;
4. save C\* record/pointer;
5. exec with `CLAUDE_CONFIG_DIR`.

Exec failure removes only the new record/pointer/root.

### 8.3 Exact resume

- Legacy record: existing argv path.
- C\* record: derive UUID root; validate owner/mode/manifest/hash; exact resume
  reuses it.
- The project agent discovery scan runs before exec on every resume, same as
  fresh launch.
- Missing transcript-bearing root: block.
- Missing config-only root: regenerate only when drift check passes.
- Manifest corruption: block with reconciliation guidance.

### 8.4 Deliberate composition transition

The project agent discovery scan runs first on every transition. If
config-only, stage/swap the root. If transcript-bearing, reconcile only known
managed files under the preparation lock:

- pre-read exact old bytes;
- resnapshot allowlisted preferences;
- atomically write target files;
- remove obsolete managed agent files;
- manifest last;
- save new record;
- exec.

Ordinary/exec failure restores old bytes and record. Hard-crash recovery is
drift-gated. Same UUID in two live terminals remains unsupported.

### 8.5 Forget and observability

- Legacy forget unchanged.
- C\* transcript-bearing forget removes record/pointer and reports retained
  orphan root.
- Config-only forget may remove generated root.
- No destructive purge in this release.
- `sessions show` reports runtime mode/config path/manifest/binary/workflow
  mode plus the native-mode caveats (acceptEdits/no-worktree exception,
  potential stalls, 16-concurrent and 1,000-per-run platform caps).
- The quick-confirm TUI shows the composition workflow mode before launch.
- Doctor reports root-without-record orphans without reading transcript files,
  reports record workflow modes with the same native-mode caveats, verifies
  the managed-scope absence assumption, and reports per-root supervisor status
  through the probed root-scoped `claude daemon status`.

## 9. Phase 2C — compiler/launch cutover

For C\* sessions after G1:

- set `CLAUDE_CONFIG_DIR`;
- retain lead `--model`/`--effort` and native session identity;
- takeover/reload evidence must show the lead effort lane survives a daemon
  takeover and a persistent-config reload; if it does not, persist the
  composition-owned native `effortLevel` in generated `settings.json` instead
  of relying on argv alone;
- remove selected variant `--agents` only after file parity passes;
- remove append-system-prompt argv only after root `CLAUDE.md` parity passes;
- use persistent settings for verified denies, optionally retaining argv deny as
  defense until takeover evidence passes;
- ship the workflow-mode settings flip, any required derived
  `ultracode → xhigh` mapping, the specialist `disallowedTools: Agent`
  frontmatter, the `roles.json`/lead-policy wording rewrite, and the legacy
  `--agents` parity field in this same change;
- compile the deterministic pins (`CLAUDE_CODE_FORK_SUBAGENT=0`, agent-teams
  force-off, reserved/unset facts of §4.3) for every managed launch;
- keep every legacy argv path byte-compatible except G0' and the parity field.

Update package/launcher version to 2.1.0 and preserve default/kimi-sol
composition semantics. Native workflow mode preserves today's `ultracode`
lead behavior; no composition is re-rendered to `xhigh` except through the
probe-required derived mapping for `off`-mode compositions.

## 10. Phase 2 tests

Add or update:

- generator goldens for default and `kimi-sol`, including native/off workflow
  settings goldens, the role-neutral `CLAUDE.md`, and the derived-mapping
  goldens when P-wf requires the mapping;
- record-schema old/new/link cases plus workflow snapshot cases (absent,
  explicit `native`, `off`), old-schema rejection of field-bearing input, and
  proof that a new-launcher legacy record carries neither new optional record
  field and validates against the old schema;
- record-hash coverage: the workflow mode affects composition hash and resume
  drift exactly when declared;
- private path, symlink, mode and traversal tests;
- preference allowlist/secret/conflict tests, including `workflow*` exclusion;
- typed-denial negative tests for specialist `disallowedTools: Agent`;
- project agent discovery tests: nested working-directory-to-root walk,
  `--add-dir` targets, non-git, unreadable, symlink escape, any-definition
  failure with paths/remedy, managed-scope detection blocking;
- fresh/legacy/resume/transition launch ordering;
- exec-failure rollback and injected hard-crash-state recovery;
- missing/corrupt root/manifest behavior;
- forget/orphan behavior;
- concurrent different UUID roots and same-UUID preparation exclusion;
- watcher add/edit/remove and complete-directory-before-exec tests;
- takeover/reload lead-effort assertion and persisted `effortLevel` fallback;
- `availableModels`, `worktree.baseRef`, fork-pin, teams-off, and
  budget-default goldens;
- argv/env goldens by runtime mode and workflow mode;
- TUI/`show`/Doctor workflow-mode display goldens;
- no provider/token/transcript/personal-workflow leakage.

Phase 2 ownership and review split:

- **Lane 1 (pure 2A):** new pure-generation modules only — session-config
  generator, closed preference extractor, project agent discovery scanner —
  plus their goldens and unit tests.
- **Lane 2 (2B+2C integration):** the config-root transaction and the
  compiler/launch cutover together as one shared integration lane; there is no
  separate 2C lane.
- The main integrator exclusively owns `catalog/native-contract.json` and both
  closed schemas (native contract schema and session schema) across both lanes.

Run narrow module suites, then full discovery. A lightweight Oracle checkpoint
reviews the pure Lane-1 2A diff and goldens; it must pass before the combined
2B+2C integration review is requested. Oracle then reviews the complete 2B+2C
integration diff and test evidence; all findings return to the original owning
lane.

## 11. Phase 3 — packaged lifecycle, source commit and activation

### 11.1 Packaged gates

1. full Python discovery with bytecode disabled;
2. `git diff --check` and generated-artifact scan;
3. standalone/package check and flake `checks.x86_64-linux.claude-multi`;
4. Home Manager activation-package build;
5. packaged local probe suite under P0 rules;
6. plain-Claude unaffected check;
7. default and `kimi-sol` `show`/Doctor checks, including workflow-mode
   displays.

Every new source file must be git-tracked (at minimum staged) before any
flake-evaluating gate runs; flakes silently ignore untracked files. No broad
flake gate is retried blindly if an unrelated baseline failure recurs.

### 11.2 Oracle final gate

Oracle reviews:

- blueprint/plan conformance;
- final diff and test evidence;
- legacy compatibility and C\* guarantees, including the narrowed native-mode
  goals and their exact guarantee wording;
- the same-change shipment of the workflow flip and any derived mapping;
- commit/activation/rollback ordering and procedure;
- YAGNI/simplification opportunities.

No source commit and no activation on REVISE.

### 11.3 Source commit

Commit the complete source before activation. Nix flakes evaluate only
git-tracked content: an untracked new file is silently invisible to the flake,
and an uncommitted working tree is neither reproducible nor attributable to
the reviewed diff. Activation therefore runs only from the committed tree, so
the flake-visible source is exactly what the packaged gates and the final
Oracle review approved. Inspect status, diff, recent log and secret scan
before committing; never push.

### 11.4 Activation

One activation at the end remains the default. An optional early activation
immediately after Phase-1 Oracle approval is permitted: it ships G0'
hardening and the promoted native contract only, with C\* inert (no session
selects `session-config-v1` until Phase 2 and Phase 3 complete).

Required checkpoint: a Home Manager switch restarts the `cli-proxy-api` user
service and briefly cuts the gateway. Before any switch — early or final —
checkpoint all live managed sessions and confirm no critical delegation is in
flight.

Record current and prior Home Manager generations and active package/service.
Run one authorized `home-manager switch --flake .#kotur` from the committed
source, then verify:

- expected new package path and launcher version;
- proxy service active with correct ExecStart;
- `claude-multi doctor` Ready with inspected/installed/daemon status;
- trusted default unchanged;
- `kimi-sol` Ready with its workflow mode displayed;
- legacy session metadata still readable;
- no active user Claude process was stopped by the activation.

### 11.5 Documentation and follow-up commits

Documentation/wiki commits may follow activation. Update:

- README and v2 runbook, including the workflow-mode display contract and
  caveats;
- blueprint status and probe evidence summary;
- session-transition note with C\* interaction boundaries;
- project wiki/log;
- exact user acceptance/restart instructions.

Before each follow-up commit repeat the status/diff/log/secret inspection.
Commit docs and wiki separately; never push.

## 12. User acceptance boundary

The user performs the first real C\* launch. Instructions must include:

1. finish/checkpoint the current degraded legacy session;
2. launch a fresh `kimi-sol` C\* session;
3. confirm six exact `cm-*` types and no generic `claude` fallback;
4. run one Sol routine delegation and one cross-family review;
5. resume exact UUID and verify context/registry persistence;
6. confirm the workflow mode displays as `native` in the quick-confirm TUI and
   `sessions show`, with the `acceptEdits`/no-worktree caveat, potential-stall
   note, and 16/1,000 platform caps, and that `ultracode` lead behavior is
   unchanged;
7. if the user declares an `off`-mode composition, confirm the
   `disableWorkflows: true` flip, inert keyword, and any derived
   `ultracode → xhigh` launch effort note;
8. optionally observe the next natural Claude update takeover;
9. report exact failure text without secrets.

Implementation automation does not make a real provider request.

## 13. Deferred follow-ups

- verified native fork and exact transition UX;
- destructive orphan purge;
- migration of legacy/native sessions (currently forbidden);
- runtime registry introspection API if Claude later exposes one;
- root-curated workflow content, only if a future reviewed requirement asks
  for it (private roots currently copy no personal workflows);
- broader lifecycle automation only if user acceptance reveals a gap.

Final todo item: re-orient on the session-transition workstream after C\* user
acceptance.
