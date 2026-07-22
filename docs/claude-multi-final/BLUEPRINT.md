# BLUEPRINT — claude-multi final architecture

## 1. Factual audit

### 1.1 Root cause (Q1), established from official docs + daemon log + binary

Timeline evidence (`~/.claude/daemon.log`, no transcripts read):

- `2026-07-21T21:42:28Z` — supervisor observes `~/.local/bin/claude` change
  (2.1.216 → 2.1.217), self-restarts for upgrade.
- `21:42:30` — new supervisor (2.1.217) starts; `bg adopt: adopted=1
  respawned=0 dead=0`; one stale worker respawn refused.
- After takeover, the session's custom `cm-*` agent types were gone; built-ins
  remained; transcript, environment, lead model survived.

Official behavior that explains it:

1. **`--agents` is process state, never persisted.** "CLI-defined subagents
   ... exist only for that session and aren't saved to disk"
   (`claude-sub-agents.md` L130).
2. **On-disk agent scopes are re-scanned on every process start** and watched
   for edits: managed settings, `--agents` (session), `.claude/agents/`
   (project), `~/.claude/agents/` (user), plugin `agents/` — precedence 1–5
   (sub-agents L108–114). `--add-dir` directories' `.claude/agents/` "load
   alongside project subagents" (L120).
3. **A documented carry-through set survives backgrounding**:
   `--mcp-config`, `--strict-mcp-config`, `--settings`, `--add-dir`,
   `--plugin-dir`, `--fallback-model`,
   `--allow-dangerously-skip-permissions` (agent-view L340–349, written for
   backgrounding). "Session state persists on disk through auto-updates and
   supervisor restarts" (L104). The 2.1.217 binary's respawn path re-resolves
   exactly `{settings, pluginDir, addDir, mcpConfig, strictMcpConfig}` (static
   inspection, consistent with the doc list). **Takeover carry of these flags
   is therefore binary-consistent but not yet acceptance-proven — that is U1,
   a stop-the-line M1 gate (PLAN §2), not an assumption.**
4. **The session record persists** model, effort, `--agent` choice, cwd,
   added directories, permission grants (agent-view L324; sub-agents L644:
   `--agent` "persists when you resume").
5. **Not in the durable set**: `--agents`, `--disallowedTools`, and (unverified)
   `--append-system-prompt-file` and process env.

Conclusion: the exact loss event was the supervisor-upgrade takeover
rebuilding the session's agent registry from persisted/on-disk state, where
the `--agents` JSON had never been written. Any fix must put every
policy-relevant byte into files or the documented carry-through set.

### 1.2 Runtime audit (2026-07-22, re-verified live)

- Repo `/home/kotur/personal/nixos-dotfiles`, HEAD `cf04fce`, working tree:
  25 tracked modifications (G0'), 8 untracked paths (P0 probe, frozen docs,
  handoff).
- HM generation **76** current; rollback **75**. Active package
  `/nix/store/sjkxlr3aax7hycvn2hk548h1065lq7js-claude-multi-2.0.0`.
- Claude binary 2.1.217 active (sha256 `2630fc5d…`, matches native-contract);
  2.1.215/2.1.216 retained.
- Gateway: `cli-proxy-api` (CLIProxyAPI) on `127.0.0.1:8317`, systemd user
  service, config rendered from `~/.config/claude-multi/config.yaml`; auth dir
  `~/.local/share/claude-multi/auth`; per-launch token from
  `~/.config/claude-multi/api-key`.
- Product: stdlib-only Python; entry points `claude-multi`,
  `claude-multi-dev`, `claude-multi-proxy`. State: `~/.local/state/claude-multi`
  (sessions, last-session pointers, lead prompts). 651 tests pass, 1
  intentional skip.
- Package `settings.json` (3 allowlisted keys): `disableWorkflows:false`,
  `workflowSizeGuideline:"medium"`, `workflowKeywordTriggerEnabled:false`.
- This very session is a live managed launch and shows the v2 argv shape:
  `--session-id --name cm:kimi-sol --settings <store> --model
  claude-multi-kimi-k3[1m] --effort ultracode --agents {…6 variants…}
  --append-system-prompt-file … --disallowedTools Agent(Explore)
  Agent(general-purpose)`.

### 1.3 Doc corpus classification

| Doc | Class | Used for |
| --- | --- | --- |
| claude-sub-agents.md | official (code.claude.com) | scopes, precedence, watcher, model order, nesting, resume |
| claude-agent-view.md | official | supervisor, persistence/carry-through, backgrounding, fork |
| claude-dynamic-workflows.md | official | ultracode, disable switches, acceptEdits, caps, model routing |
| claude-agents-general.md | official | surface comparison; teams experimental/off by default |
| claude-agent-teams.md | official | teams env-gated, model fixing, display modes |
| claude-isolate-with-worktrees.md | official | subagent isolation, baseRef, cleanup, resume-in-worktree |
| claude-orchestration-patterns.md | Anthropic guidance (not a runtime contract) | start simple; orchestrator–subagent first; bounded verifier |
| blog/reddit/vladislav (3) | community leads | gateway mechanics corroboration only |

**Corpus gaps** (no local snapshot): settings reference, CLI reference,
permissions, env-vars. Mitigations: `claude --help` offline output, the
G0'-verified `native-contract.json` (binary + help inspection), static binary
strings, and explicit "unverified" labels in SPEC.

### 1.4 Unresolved unknowns (honest list)

| # | Unknown | Evidence today | Resolution |
| --- | --- | --- | --- |
| U1 | Does the supervisor-takeover respawn re-supply `--add-dir` (not just `/background`)? | Carry-through documented for backgrounding; binary respawn re-resolves addDir — takeover itself unverified | **M1 stop-gate**: fixture-daemon probe if per-root daemon domains verify (PLAN §2), else user acceptance L2. If U1 fails: STOP, return to an Option 2 checkpoint — `--legacy` is a compatibility hatch, not a durability fallback |
| U2 | Does `--append-system-prompt-file` survive takeover? | Stored in binary session-config bag with `agent`/`agents`; not in doc list | Belt-and-braces floor (§3.4) + acceptance check |
| U3 | Are `--add-dir` agent dirs *watched* (hot edits)? | Watcher doc covers only `~/.claude/agents` and `.claude/agents` | M1 fixture probe; v1 transitions are relaunch-only regardless |
| U4 | Precedence between add-dir agents and cwd project agents on exact name collision | "closest to cwd wins" covers nested projects only | Collision gate extended to project tree + user `--add-dir`s + managed agents dir (SPEC §6) |
| U5 | `availableModels` as a settings-file fence for per-invocation overrides | Doc L251 describes org-allowlist skip behavior | Compile it; acceptance-verified; else downgrade to displayed residual |
| U6 | Nested spawning default on 2.1.217 (doc: on, depth 5 fixed, L763–771; binary has depth env strings; frozen notes claim default-off) | Contradiction | Allow nested delegation in prompts; keep spawn env keys unset; acceptance confirms |
| U7 | Fork with managed sessions: (a) refused due to `--append-system-prompt-file` (AV L330)? (b) if allowed, does the scope pointer carry? | AV L324/L330/L340–349 | Acceptance; until then UX says forks may be refused |
| U8 | Are settings file edits re-read mid-session? | Not documented in corpus | v1 transitions relaunch-only; probe informs later hot mode |
| U9 | Cross-file deny/allow merge precedence (can project/local weaken our settings denies)? | SA L501–517 documents the deny mechanism, not merge precedence | Claim downgraded to "deny present in effective settings"; we control the user's files; Doctor reports conflicts |

## 2. Architecture options (Q2)

### Option 1 — Per-session durable scope via `--add-dir` (RECOMMENDED)

At launch, a pure JIT compiler writes into
`~/.local/state/claude-multi/scopes/<uuid>/`:

```
scopes/<uuid>/
├── .claude/agents/cm-*.md   # one file per selected variant (frontmatter + canonical role prompt)
├── settings.json            # package base + composition overlay (denies, workflow mode, model fence)
└── (lead appendix stays at ../lead-prompt-<digest>-<uuid>.md)
```

Launch: `claude --session-id|--resume <uuid> --name cm:<comp> --settings
<scope>/settings.json --model <lead> --effort <lead> --add-dir <scope>
--append-system-prompt-file <appendix>` + gateway env. **No `--agents`, no
`--disallowedTools`** (denies move into settings `permissions.deny`,
documented at sub-agents L501–517).

- Correctness: every policy byte is a discovered file or carry-through argv →
  survives the exact failure event.
- Complexity: one pure compiler + one per-session dir; reuses state.py atomic
  primitives. No daemon/hooks/transactions.
- UX: composition visible as real files; Doctor can diff scope vs catalog.
- Compatibility: existing sessions resume and *upgrade* by gaining a scope;
  plain `claude` never references the scope → unaffected.
- Security: scope is 0700 owner-only state; agents are data, not code;
  project agents keep higher precedence (visible, gated for `cm-*` names).
- Rollback: scope deletion is safe (no transcripts inside); records carry a
  mode field; legacy mode = same compiler with scope disabled.

### Option 2 — One managed `CLAUDE_CONFIG_DIR` ("claude-multi home")

All managed sessions share `~/.local/share/claude-multi/home` as an alternate
Claude home (own agents/, settings, transcripts, supervisor).

- Pros: single durable scope; clean plain/managed separation; root is the
  supervisor's own identity, not per-worker argv.
- Cons: relocates transcripts (existing sessions can't be resumed in place —
  needs legacy split), fresh onboarding/trust/credentials, second supervisor
  lifecycle, `CLAUDE_CONFIG_DIR` is env (not carry-through) so *all* managed
  launches must re-supply it, all compositions share one agents/ library
  (selection purity impossible), heavier migration. This is C\*-lite; the
  frozen effort already showed where the weight goes.

### Option 3 — Global user-scope library (`~/.claude/agents/cm-*.md`)

Compile the catalog into the user agents dir at activation. (The mandated
"materially simpler" comparison.)

- Pros: trivial; watched; durable; zero launch machinery.
- Cons: **plain `claude` sees `cm-*` decoys** whose gateway model selectors
  fail or silently fall back outside the managed env (violates "plain Claude
  unaffected"); no per-composition selection; concurrent compositions can't
  differ; project agents (precedence 3) silently shadow user scope (4).
  REJECTED — fails the product's own compatibility rule.

### Option 4 — Generated plugin via `--plugin-dir`

Package agents as a local plugin (`--plugin-dir` is carry-through).

- Pros: scoped names (`claude-multi:cm-analyst-…`) kill collisions; plugin
  settings/hooks channel; durable.
- Cons: plugin subagents **can't use `permissionMode`/`hooks`/`mcpServers`**
  (sub-agents L173); lowest precedence (5); scoped names change every dispatch
  ID, prompt, and transcript reference; plugin enablement semantics add a
  second mechanism. REJECTED as primary; noted fallback if namespacing ever
  becomes the dominant requirement.

### Option 5 — Keep argv, add a resident re-supply wrapper

Launcher stays alive and re-injects `--agents` on replacement. **Impossible**:
the supervisor owns worker adoption/respawn; a wrapper cannot inject argv
into a supervisor-respawned process. REJECTED (unenforceable, and adds the
resident daemon SIMPLICITY forbids).

### Decision

**Option 1.** It is the smallest design whose every claim rests on documented,
reloaded state — and it needs no transcript/auth/onboarding relocation at all.
Option 2 is the fallback if the U1 stop-gate fails (its cost is known and
bounded). Full rationale per requirement in DECISIONS.md.

## 3. Recommended design

### 3.1 Invariants

1. **Durable policy doctrine**: agent definitions, tool denials, workflow
   mode, and the model fence exist only as files (scope agents, scope
   settings) or documented carry-through argv (`--settings`, `--add-dir`).
   Nothing policy-critical rides in `--agents`, `--disallowedTools`, or env.
2. **One authoritative composition** (user composition resolved against the
   trusted catalog) → one pure compile → one scope per session UUID.
3. **Scope path is stable per session** (`scopes/<uuid>/`); content may
   advance through generations on transitions.
4. **Plain `claude` unaffected**: no writes outside claude-multi state; no
   global scopes; gateway only referenced by managed launches.
5. **Fail closed**: unsafe binary, unreachable gateway, scope compile error,
   or `cm-*` project collision ⇒ no launch, exact recovery printed.
6. **No transcript-bearing root is ever written or deleted** by the launcher.
7. **Honest residuals displayed**: per-invocation model override, `/model`,
   `/effort`, active-subagent mixing, delegation discretion — shown in
   TUI/Doctor, never claimed prevented.

### 3.2 Enforcement & honesty table (Q3)

| Guarantee | Mechanism | Durability | Honesty |
| --- | --- | --- | --- |
| Agent availability | scope `.claude/agents/*.md`, discovered every process start | documented | guaranteed |
| Explicit dispatch | exact `name` via Agent tool / @-mention | documented | guaranteed |
| Automatic routing | descriptions + lead appendix | behavioral | **not guaranteed** — lead discretion; shown in UX |
| Agent model | frontmatter `model` | documented | override order env > invocation > frontmatter > lead (L242–249): env key unset at launch; `availableModels` fence compiled (U5); per-invocation override = displayed residual |
| Agent effort | frontmatter `effort` | documented | same override caveats |
| Tools/denies | `disallowedTools` frontmatter + settings `permissions.deny` | documented | denies merge before allows (cannot be weakened) |
| Generic/built-in policy | settings `permissions.deny`: `Agent(Explore)`, `Agent(general-purpose)`, `Agent(claude)`; Plan native | documented | durable |
| Nesting | native on (U6), depth 5 fixed | documented | prompt contracts bound behavior; `Agent(type)` allowlist is ignored in subagent defs (L332) — not used |
| Implementer isolation | frontmatter `isolation: worktree` + `worktree.baseRef:"head"` in scope settings | documented | worktree lifecycle native |
| Review-family independence | lead policy + appendix + composition families + reviewer descriptions | behavioral | workflow output: lead-family **by default** — scripts may route stages to other models (WF L295), so unobserved workflow output is family-unknown/mixed and never counts as an independent verdict |
| Lead model/effort | `--model`/`--effort` argv; session record persists | documented | user `/model` `/effort` = residual, displayed |
| Workflow mode | scope settings `disableWorkflows`; off ⇒ lead effort ultracode→xhigh at compile | documented | workflow agents always `acceptEdits`, session model, no worktree — displayed |

### 3.3 State/schema (one model, two explicit authorities)

- **Record = intent** (composition snapshot + catalog hash).
- **Catalog = trusted source** (installed, versioned; prompt bodies,
  selectors, contract).
- **Scope = pure function of (record, catalog)** — always re-derivable;
  repair converges to record intent rendered through the installed catalog,
  with catalog drift displayed. No third/hidden state.

Layout: trusted read-only inputs `catalog/*`, schemas, package
`settings.json`, `version.json`; launcher state
`~/.local/state/claude-multi/` = `sessions/<uuid>.json` (record v2: `mode`,
`scope_generation`, `workflows`; v1 loads with defaults), `last-session-by-cwd/`,
`scopes/<uuid>/` (agents + settings), `lead-prompt-*.md`. No manifest
journal, transaction log, daemon socket, or PID files.

### 3.4 Lead-policy delivery (U2 belt-and-braces)

- Primary: `--append-system-prompt-file <appendix>` (current behavior;
  appendix carries the rendered inventory, independence rules, sentinel,
  relaunch command).
- Hard floor (fully durable, independent of U2): generic/built-in denies live
  in scope settings; every `cm-*` file's description carries the
  no-substitute sentinel; reviewer descriptions carry the cross-family
  independence rule; role prompt bodies carry their own contracts (SPEC
  §2.1). Every **load-bearing** rule thus exists in files.
- If the appendix survives takeover, nothing changes; if not, the session
  loses the rendered convenience view but no policy — degraded, not broken.
  This is claimed as **policy-preserving degradation**, not "no effect".

### 3.5 Failure behavior

- Binary/gateway/scope/collision failure ⇒ `LaunchError`, no state writes
  (current ordering contract preserved), exact recovery command.
- execve `OSError` ⇒ record forgotten, pointer compare-and-cleared, **scope
  dir removed** (new generation only), prior generation restored on
  transition.
- Mid-session agent loss (recurrence of the original bug) ⇒ Doctor's scope
  integrity check names the missing files; `claude-multi doctor --repair
  <uuid>` recompiles the scope from the record's composition (files only;
  next delegation or relaunch picks them up).

### 3.6 What is deliberately NOT built (Q13, simplicity justification)

| Frozen/speculative machinery | Fate | Why |
| --- | --- | --- |
| Per-UUID `CLAUDE_CONFIG_DIR` roots | not reused | relocates transcripts/auth/onboarding; needs probes for what Option 1 gets from docs |
| P0 namespace sandbox (`p0.py`, `p0inner.py`, `test_p0.py`) | **delete** (archived in handoff artifacts) | no production requirement; probe evidence already recorded |
| `probe.py` + `test_probe.py` | keep, **dev-only**, trimmed usage | fake-provider discovery probes (U3) without provider contact |
| Same-launch lead mode (`--agent cm-lead`, acceptance gate) | delete | contingency delivery is the only mode; removes dead branches |
| Triple-flag fork compiler path | delete | keep native-fork + `sessions link` adoption guidance |
| Hardened daemon-metadata reader (~140 lines) | simplify to existence/pid check | the metadata file does not exist; defensive parser defends a hypothetical |
| native-contract schema: 8 copy-paste capability objects | collapse to map pattern (~40 lines) | ceremony, not behavior |
| `roles.json` mutation/delegation contract strings | drop (prompts are canonical) | double source of truth, drift-prone |
| Project-agent broad inventory gate/scanner | replaced by exact-`cm-*` collision check | user direction: project agents participate visibly, not blanket-blocked |
| `review.mode` schema field | not added | lead policy suffices; REVIEW-STRATEGY says don't add schema merely because suggested |
| Resident launcher/daemon/hooks/message bus | never | no demonstrated requirement |

**Complexity budget after implementation** (estimate): scope compiler ≤ 300
lines; transitions ≤ 250; settings overlay ≤ 120; net **removal** of ≥ 4,500
lines (P0 + dead branches + ceremony) against ≤ 900 added. Tests target
behavior/failure boundaries; goldens get a `bless` regenerator.

## 4. Answers to the remaining posed questions (short form)

- **Q4 (coexistence)**: workflows per-composition, default `native`;
  coexistence visible in TUI (workflow badge + guarantee panel); off =
  settings `disableWorkflows:true` + xhigh mapping; no custom machinery.
- **Q5**: project/managed/add-dir agents load natively; built-ins per
  `native_agents` policy; generic `claude` denied in durable settings;
  per-invocation overrides/`/model`/background wake/agent-view/teams/forks
  each classified in SPEC §6 — teams stay off (experimental, env-gated,
  orchestration guidance: partitions, not coupled edits).
- **Q6**: auth/gateway via env at launch (observed surviving adoption);
  transcripts/history/onboarding/plugins/preferences untouched (no config
  root); supervisors never contacted.
- **Q7**: startup TUI keeps quick-confirm + editor; adds durability badge,
  workflow badge, transition action, Doctor scope checks — UX.md.
- **Q8**: G0/P0 disposition in §3.6 and MIGRATION-ROLLBACK (23 KEEP, dev.py
  REWRITE lazy-import, P0 delete, probe dev-only).
- **Q9**: quality = milestone reviewer/finisher (default) + one rare
  read-only cross-family audit (pre-activation). No loops. PLAN §4.
- **Q10**: default agents stay 4 roles × 2 families with broad tools;
  project agents participate visibly (listed in TUI/Doctor); `cm-*` exact-name
  collision fails closed (our namespace), everything else loads natively.
- **Q11**: TRANSITIONS.md — v1 relaunch-only with exact `--resume`, exited
  confirmation, sibling-generation swap, crash-converge to record authority.
- **Q12**: PLAN.md — critical path M1 (scope compiler) → M2 (transitions/TUI)
  → M3 (deletions/catalog) → M4 (verification) → M5 (acceptance); U3 probed
  in M1 via fake provider.
- **Q13**: §3.6.
