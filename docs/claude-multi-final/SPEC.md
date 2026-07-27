# SPEC — durable-scope claude-multi

Normative behavior for the final design. `MUST`/`MAY` as usual. Citations:
`SA` = claude-sub-agents.md, `AV` = claude-agent-view.md, `WF` =
claude-dynamic-workflows.md, `WT` = claude-isolate-with-worktrees.md,
`TM` = claude-agent-teams.md (all local snapshots, created 2026-07-22).

## 1. Definitions

- **Composition**: a user composition document resolved against the trusted
  catalog (unchanged from v2) plus new optional field
  `workflows: "native" | "off"` (default `"native"`).
- **Scope**: per-session directory `<state_root>/scopes/<uuid>/` holding all
  generated, policy-relevant files for that session.
- **Mode**: `durable` (has scope) or `legacy` (argv-only, pre-rethink
  records/linked sessions).
- **Generation**: integer content version of a scope, bumped on transition.

## 2. Scope layout (generated, mode 0700/0600, atomic via state.py)

```
scopes/<uuid>/
├── .claude/agents/<variant-id>.md   # one per selected variant
└── settings.json                    # compiled session settings
```

The lead appendix remains `<state_root>/lead-prompt-<digest16>-<uuid>.md`
(per-session; retained while the session record lives, and pruned by
`doctor --prune` only when the record is gone — a forgotten session's
appendix can never be needed again).

### 2.1 Agent files

One Markdown file per selected variant, YAML frontmatter + canonical role
prompt body (byte-identical per role across variants):

```markdown
---
name: cm-implementer-sol-high
description: <role summary> Model: <display> (lane <lane>). <routing hint> Preferred cm-<role> variant. Managed cm session: if a selected cm-* type is unavailable, stop; never substitute a generic agent.
model: <client_selector>          # e.g. gpt-multi-sol-high
effort: <lane>                    # low|medium|high|xhigh|max
isolation: worktree               # only when the role declares it
---

<canonical role prompt body>
```

Reviewer variants additionally carry the independence rule in their
description (durable, visible every roster read):
`Review independence: a change authored by a <family>-family variant must not
receive its sole verdict from another <family>-family variant while a
cross-family reviewer is enabled.` The lead appendix repeats these rules for
convenience; the durable floor is the descriptions + settings, so appendix
loss (U2) degrades rendering, not policy.

Rules:
- `name` MUST equal the variant ID (identity comes from `name`, not the
  filename — SA L124, L216). IDs match `^[a-z0-9-]+$` (SA L216).
- Frontmatter MUST NOT include `permissionMode`, `hooks`, `mcpServers`,
  `tools`, or `disallowedTools` unless the catalog role declares them
  (today: none — capable defaults with broad inherited tools, Q10).
- `ultracode` MUST NOT appear as an agent `effort` value (lead-only — WF
  L110–120). Lead `ultracode` remains argv/session state.
- The directory MUST exist before `execve` (first-directory watcher rule,
  SA L183–186).
- Files are written to `scopes/<uuid>/.new/` and atomically renamed into
  place; per-file writes use `state.atomic_write`.

### 2.2 Compiled session settings (`scopes/<uuid>/settings.json`)

Merge order (later wins): package base `settings.json` → composition overlay.
Closed key allowlist (catalog-validated):

| Key | Value rule |
| --- | --- |
| `disableWorkflows` | `false` when `workflows:"native"`; `true` when `"off"` (WF L315–327) |
| `workflowSizeGuideline` | package default `"medium"` (unchanged) |
| `workflowKeywordTriggerEnabled` | `false` (unchanged; explicit asks still work, WF L83) |
| `permissions.deny` | `["Agent(Explore)","Agent(general-purpose)","Agent(claude)"]` per `native_agents` policy + contract generic aliases (SA L501–517; replaces `--disallowedTools` argv) |
| `availableModels` | sorted catalog client selectors + lead selector (model fence, U5) |
| `worktree.baseRef` | `"head"` when any selected variant uses `isolation: worktree` — implementers branch from the current local HEAD, carrying local/unpushed **commits** (WT L101–106). It does NOT expose uncommitted working-tree changes; that is accepted (lead integrates; implementers never edit the main checkout) |

Rationale per key is in DECISIONS.md D11. No other settings keys are
emitted. Env policy at launch is unchanged (gateway, `DISABLE_AUTOUPDATER`,
hygiene unsets incl. `CLAUDE_CONFIG_DIR`, both spawn keys,
`CLAUDE_CODE_SUBAGENT_MODEL`, `CLAUDE_CODE_DISABLE_WORKFLOWS`,
`CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS` when policy replaces built-ins) —
env is launch hygiene, never the durable policy channel (invariant 1).

**Since v2.2–2.4 (the allowlist as shipped):** the managed fence narrowed
`availableModels` to exactly the compiled lead (plus a `model` pin so the
native Default entry cannot escape it, D24); `env`/`hooks` carry the
lifecycle identity (`CLAUDE_MULTI_MANAGED_ID`, `CLAUDE_MULTI_LAUNCH_EPOCH`,
and SessionStart/End hook commands through the stable
`<state>/bin/claude-multi-hook` shim, D22/D26); and `autoCompactEnabled: true`
is pinned for managed sessions (D28). Ordinary scopes emit only
`availableModels` (a context-profile fence), `model`, `env`, and `hooks`.

**Since v2.6 (gateway routing is durable, D33):** lifecycle settings
additionally carry `env.ANTHROPIC_BASE_URL` (non-secret loopback URL from
the trusted gateway doc) and `apiKeyHelper` (stable
`<state>/bin/claude-multi-gateway-token` shim; prints the 0600 token file
at runtime — the token value never enters scope files). Both managed and
ordinary lifecycle scopes emit them. Motivation: the background daemon
relaunches adopted sessions preserving argv but scrubbing `ANTHROPIC_*`
env; without durable routing those sessions fail every model call.
`apiKeyHelper` is in `COMPILED_SETTINGS_KEYS`.

## 3. Launch contract

Argv order (fresh / resume / transition-relaunch):

```
claude --session-id <uuid>            # or: --resume <uuid>
       --name cm:<composition>
       --settings <scope>/settings.json
       --model <lead_selector> --effort <lead_effort>
       --add-dir <scope>
       --append-system-prompt-file <appendix>
       [passthrough…]
```

- `--agents`, `--disallowedTools`, `--agent cm-lead` MUST NOT be emitted.
- Legacy mode: identical minus `--add-dir`; `--agents`/`--disallowedTools`
  emitted exactly as v2 (one compile parameter, not a second subsystem).
- Ordering: binary verify → gateway readiness (loopback) → scope compile →
  record save → pointer update → execve (current contract preserved).
- execve `OSError` cleanup is **action-aware**:
  - fresh: forget the new record, compare-and-clear the pointer, remove the
    new scope;
  - resume (durable or linked): the record pre-existed — preserve it and the
    pointer untouched; remove only a scope staged by this launch;
  - legacy→durable upgrade: restore the exact pre-read legacy record bytes,
    remove the attempted scope;
  - transition: restore prior record bytes and prior scope generation
    (TRANSITIONS §4).
- `workflows:"off"` + lead effort `ultracode` ⇒ compile-time mapping to
  `xhigh` (composition-level derivation only, recorded in the snapshot).

## 4. Session records (schema v2)

```json
{
  "version": 2,
  "session_id": "…", "cwd": "…", "composition_name": "…",
  "composition_hash": "sha256:…", "snapshot": {…},
  "mode": "durable", "scope_generation": 1, "workflows": "native",
  "catalog_version": 1, "catalog_hash": "sha256:…",
  "launcher_version": "2.1.0", "created_at": "…", "forked_from": null
}
```

- v1 records load with defaults `{mode:"legacy", scope_generation:0,
  workflows:"native"}`; no rewrite until the session is resumed/upgraded.
- `sessions link` adopts a native session against a user-selected composition
  (as today); the record is `legacy` and upgrades on resume like any other —
  one uniform rule, no origin discriminator.
- `sessions forget <uuid>` removes record + scope dir + compare-and-clear
  pointer. It MUST NOT touch anything outside claude-multi state.

## 5. Resume & upgrade path

- Scope is a pure function of **(record composition, installed catalog)** —
  two explicit authorities. The record stores the catalog hash; recompiles
  use the installed catalog and report drift when hashes differ
  (TRANSITIONS §2). No hidden or third state source exists.
- Resuming a `durable` session: recompile scope at the record's generation
  when files are missing/drifted (Doctor's repair uses the same function),
  launch with `--resume`.
- Resuming any `legacy` record (including linked): compile a scope from the
  record's composition and launch durable (mode flips, generation 1). The
  transcript is untouched. `--legacy` preserves old argv behavior as a
  **compatibility escape hatch — not a durability fallback** (it restores
  exactly the fragile behavior that caused the incident).
- Exact resume relies on native `--resume <uuid>` (SA L851; AV L104).

**Since v2.6 (fork lifecycle, D32/D37):** a `fork`-sourced hook only appends
to `pending_forks` (never retargets authority); a later non-fork hook that
retargets authority ONTO a pending fork id clears that marker (reality
resolved it). Genuine pending forks block resume/transition until adopted
(`sessions link`) or discarded (`sessions resolve-fork`); **both decisions
bump the parent's `launch_epoch`**, revoking the fork's baked hook
credential (managed-id + epoch) so its later hooks cannot claim the parent
(D37 — the still-running-parent trade-off is the relink-runtime precedent).
The pending cap never evicts silently: the 17th distinct fork hook fails
visibly. Resume flows converge resolved-by-reality markers at action
paths; display paths (picker, doctor) keep them visible until acted on.

**Since v2.6 (invalid contract override):** an override that fails the
strict load (JSON/schema/secret scan) is never applied — the runtime
degrades to the packaged baseline and doctor reports it as BLOCKED with
the fix command; `claude-multi update` removes the broken file (D37).

## 6. Classification of every adjacent surface (Q5)

| Surface | Behavior in final design |
| --- | --- |
| Project `.claude/agents` | Loads natively (precedence above user scope, SA L110–118); exact `cm-*` name collision ⇒ launch fails closed with the offending path; other project agents listed in TUI/Doctor as "project agents (native precedence)" |
| Managed-settings agents | Not deployed by us; they outrank everything (SA L110). The exact-`cm-*` gate extends to the managed agents dir when readable; when its presence can't be verified, Doctor notes the unverifiable source |
| User `--add-dir` passthrough dirs | Their agents load alongside ours (SA L120); the same exact-`cm-*` gate scans them and fails closed on collision |
| Built-ins | Explore → replaced by `cm-analyst-*` (deny + policy); Plan native; general-purpose off; statusline-setup/claude-code-guide untouched |
| Generic `claude` agent | Denied via settings `permissions.deny Agent(claude)` (durable) |
| Per-invocation `model` override | Possible by the lead (SA L242); fenced by `availableModels` (U5); displayed as residual either way |
| `/model`, `/effort` | User-controlled; change lead + inheritance only; TUI/Doctor note |
| Background wake / agent view | Managed sessions appear in `claude agents` like any session; `--name cm:<comp>` labels them; nothing custom |
| Teams | Off (experimental, env-gated; TM facts). We never set `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS`; documented as out of scope |
| Forks | U7 has two parts: (a) whether `/fork` is **refused** for sessions launched with `--append-system-prompt-file` (AV L330 refuses sessions with non-inheritable flags such as replaced system prompts); (b) if allowed, whether the scope pointer carries (launch `--add-dir` is in the carry set L340–349). Until verified, UX says managed forks may be refused; supported paths are transition or a new session |
| Nested spawning | Allowed by role contracts again (U6); depth 5 fixed (SA L771); spawn env keys stay unset |
| `--disable-slash-commands` passthrough | Rejected by the launcher (sessions started with it don't watch agent dirs, SA L186) — added to blocked flags |

## 7. Doctor checks (additions to existing binary/gateway parity)

1. Scope integrity per recorded session: files present, sha256 match against
   re-compiled expectation; `doctor --repair <uuid>` recompiles.
2. `cm-*` collision scan of cwd project agent tree (fail-closed list).
3. Settings drift: package base vs catalog allowlist.
4. Stale scopes: scopes without a living record → listed, `doctor --prune`
   removes (generated files only).
5. Evidence levels printed: `documented` vs `acceptance-verified` for U1–U7.
6. Shared daemon: existence + pid only (simplified reader).
7. Identity states surfaced per record; a `pending_forks` entry that holds
   the current resume authority is Attention (lazy state —
   `--repair-all`/`converge_pending_forks` clears it), while a genuine
   pending fork is reported with the adopt/discard commands (D32).
8. Contract source + override health: an invalid override is reported as
   BLOCKED (packaged baseline in effect; `update` heals it); a
   strictly-newer valid override is info with the override marker (D37).

## 8. Catalog/schema changes

- `compositions/*.json`: optional `workflows` field (schema-extended, closed).
- `settings` allowlist: keys in §2.2 only.
- `roles.json`: drop `mutation_contract`/`delegation_contract` strings
  (prompts canonical); reviewer prompt gains finisher clause; analyst/
  reviewer/implementer prompts restore bounded delegation (U6).
- `native-contract.json`: re-scope to what we still pin (binary identity,
  offline help surface); capability-pending objects collapse into one
  `acceptance` map keyed by U-number; schema collapses accordingly.
- `version.json`: `launcher_version: "2.1.0"`. Old 2.0.0 launchers reject
  the new catalog (closed schema) — desired fail-closed.

## 9. Security notes

- Scope dirs 0700, files 0600, owner-checked; never symlinked (state.py).
- Generated agent files are data; no code execution path added.
- Compiled settings **request** the denies via `permissions.deny` (SA
  L501–517). Cross-file deny/allow merge precedence is not covered by our
  official corpus: treated as **U9**. Practical posture: we control the
  user's own settings files (none define conflicting allows today — Doctor
  reports if that changes), and the gateway/permission defaults are
  unchanged. The guarantee claimed today is "deny present in the effective
  session settings", not "unweakenable by any other file".
- The gateway token never enters scope files, records, or logs (unchanged).
- `--add-dir` grants tool file-access to the scope dir only; it contains no
  secrets.
