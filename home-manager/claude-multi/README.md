# claude-multi v2.2

First-class multi-model integration for Claude Code. Managed composition
sessions compile durable `cm-*` agents and policy; ordinary gateway sessions
use the same local model transport without inheriting a composition. Both use
per-session settings under `~/.local/state/claude-multi/scopes/<managed-id>/`
and then `execve` ordinary Claude Code. No scheduler, wrapper daemon, or
per-turn interception remains.

**Why files, not argv:** CLI `--agents` JSON exists only for the launching
session and is never saved to disk. When the shared Claude supervisor
restarts (e.g. for a binary upgrade), the session's agent registry is
rebuilt from persisted state — argv-only definitions disappear (observed
live at the 2.1.216→2.1.217 takeover). On-disk agent files are re-discovered
on every process start, and `--add-dir`/`--settings` are in Claude's
documented carry-through set for backgrounded/respawned sessions.

## Architecture

- **Trusted catalog** (`catalog/`): versioned JSON for providers, models,
  roles, the native-contract evidence record, the balanced default
  composition, and the canonical model-neutral role prompts.
- **Scope compiler** (`src/claude_multi/scope.py`): managed scopes contain
  generated `cm-*` agent files plus a strict lead-model fence; ordinary scopes
  contain only a context-compatible model picker and lifecycle hooks. A
  synchronous metadata-only `SessionStart` hook reconciles Claude's actual
  runtime UUID after startup/resume/clear/compact; `SessionEnd` is advisory.
  Atomic sibling staging keeps every scope re-derivable from its record and
  the installed catalog.
- **Session identity** (`sessions.py`): schema-v3 records separate stable
  `managed_id` (record/scope/pointer key) from authoritative
  `runtime_session_id` (the UUID passed to native `--resume`). Historical
  runtime aliases, original CWD, identity repair state, launch epoch, mutation
  ownership token, and session type are explicit. v1/v2 and early-v3 records
  migrate in memory and rewrite only on a safe mutation.
- **Launcher** (`compiler.py`, `launch.py`): verified binary (full SHA-256
  against the native contract), loopback gateway readiness, collision gate
  (exact `cm-*` names in project/managed/user `--add-dir` agent trees fail
  closed), session record, then `execve`. Legacy argv mode remains as
  `--legacy` (compatibility hatch; old records upgrade on resume).
- **Transitions** (`transition.py`): deliberate mid-session composition
  changes — semantic diff, target-process-exited confirmation, atomic
  scope-generation swap, exact `--resume` relaunch, crash-converge to record
  authority.
- **Renderer** (`render.py`): pure deterministic CLIProxyAPI YAML from
  trusted JSON; provider secrets resolve only at runtime into a mode-0600
  artifact outside this repository.
- **Onboarding** (`dev.py`): Draft → Check → Review exact diff/hash →
  Promote source for new models/providers, with scratch candidate builds.
- **Probe** (`probe.py`, dev-only): disposable-fixture, loopback fake-provider
  harness for exercising the pinned binary without providers or the live
  daemon (config-root daemon-domain gate + live-domain tripwire).
- **Proxy control** (`proxy.py`): `init/status/run/claude-login/
  codex-device-login` for the loopback CLIProxyAPI gateway.

Claude Code remains an external user installation, resolved and verified
through `catalog/native-contract.json`. CLIProxyAPI stays the single
transport owner. Non-Claude models through the Claude gateway are locally
validated experimental routes, not officially supported by Anthropic.

## Source layout

```text
home-manager/claude-multi/
├── claude-multi.nix      # Home Manager module (service, package, patches)
├── package.nix           # standalone buildable package
├── settings.json         # verified workflow toggles only
├── version.json
├── catalog/              # trusted JSON + canonical role prompts
├── schemas/              # closed-vocabulary JSON schemas
├── src/claude_multi/     # stdlib implementation
├── bin/                  # claude-multi, claude-gateway, dev/proxy tools
├── tests/                # offline stdlib suite + goldens
└── tests/default.nix     # sandbox test derivation
```

## Normal commands

```text
claude-multi                          quick-confirm a managed composition
claude-gateway [--model MODEL]        ordinary gateway session; native /model
claude-gateway -c|-r UUID             continue/resume an ordinary session
claude-multi direct [same options]    explicit form of claude-gateway
claude-multi --legacy                 pre-durable managed argv compatibility
claude-multi compose list|show|new|edit|duplicate|rename|delete|restore-default
claude-multi sessions list|show|forget
claude-multi sessions link UUID (--composition NAME|--model MODEL) [--cwd PATH]
                                      adopt plain Claude as managed or ordinary
claude-multi sessions relink-runtime MANAGED_ID RUNTIME_ID [--cwd PATH]
                                      repair pre-hook UUID/CWD drift from /status
claude-multi sessions transition UUID --composition NAME
                                      diff + exited-confirm + exact-resume relaunch
claude-multi doctor                   binary/gateway/scope/collision checks
claude-multi doctor --repair UUID     reconverge a session scope to record authority
claude-multi doctor --prune           remove stale scope generations/staging
claude-multi-dev check|review|promote developer onboarding (no provider calls)
claude-multi-proxy init|status|run    gateway control (loopback only)
```

Managed `/model` is fenced to the composition lead; use a composition
transition for a different lead. Ordinary `/model` remains native within one
safe context profile. The compiler sets the configured route compaction capacity
and an explicit 90% override. Bounds backed by catalog validation are labeled
validated; Kimi's 1M route remains explicitly user-attested and is not labeled
provider-safe until near-limit live acceptance. Pinned Claude Code 2.1.217 reserves up to 20,000
output tokens before applying that percentage, so deterministic reactive
thresholds are 316,800 for Sol, 882,000 for a managed 1M process, and 867,254
for the Qwen-safe 983,616 ordinary large profile. Proactive summary preparation
is runtime-controlled and may occur earlier. Mixed compositions keep the lead
capacity unless an extended selector advertises more context than its provider
accepts; Qwen therefore narrows a shared 1M process to 983,616, while Sol/GPT
remain protected by their own client caps. Cross-profile changes
explicitly relaunch the same ordinary session, e.g.
`claude-gateway -r UUID --model qwen38`.

Fork of a managed session: use Claude's native fork and adopt the result
with `claude-multi sessions link UUID --composition NAME`; a
`SessionStart(source=fork)` event
never overwrites the parent runtime UUID and warns that the fork needs its own
scope. The launcher still refuses to claim an unverified managed fork argv.

Design package (rationale, guarantees, verification, rollback):
[`../../docs/claude-multi-final/`](../../docs/claude-multi-final/README.md).

## Rollback

Rolling back remains non-destructive:

- Older launchers fail closed on schema-v3 records rather than confusing the
  stable managed ID with Claude's runtime UUID. v1/v2 records remain readable
  by 2.2 through an in-memory migration adapter.
- Transcripts belong to Claude, not the launcher. The current runtime UUID is
  visible in `claude-multi sessions show/list`; it can be used natively without
  the managed scope if recovery is required. Keep the 2.2 package's Nix store
  path to retain managed/ordinary record interpretation.
- **Home Manager generation rollback is the clean package rollback.** State is
  left in place; use the retained 2.2 store path for schema-v3 sessions, while
  legacy v1 records remain interpretable by their original launcher.
- **No state deletion is ever part of rollback.** Session records and
  generated scopes under `~/.local/state/claude-multi/` are left in place;
  transcripts under `~/.claude/` are never touched by the launcher at all.

## Local-only verification

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=home-manager/claude-multi/src:home-manager/claude-multi/tests \
  python3 -m unittest discover -s home-manager/claude-multi/tests -p 'test_*.py'
nix build --offline --no-link --file home-manager/claude-multi/package.nix
nix build --offline --no-link --file home-manager/claude-multi/tests/default.nix
```

No command in the normal paths contacts a provider. `claude-multi-dev
smoke-test` currently has no provider transport wired; the bounded
verification path is one consent-gated request through the gateway, e.g.
`curl -H "x-api-key: $(cat ~/.config/claude-multi/api-key)" \
  -H "anthropic-version: 2023-06-01" -H "content-type: application/json" \
  -d '{"model":"<alias>","max_tokens":8,"messages":[{"role":"user","content":"OK"}]}' \
  http://127.0.0.1:8317/v1/messages` — run only with explicit user approval,
one call at a time.

**Lifecycle status (2026-07-23):** 1,135 offline tests green (1
intentional real-provider skip). The suite covers schema migration,
epoch-ordered runtime-ID reconciliation hooks, mutation-token rollback, typed
continue pointers, CWD fail-closed behavior, deduplicated native
discovery, ordinary gateway profiles, managed model fencing, per-lead compact
windows, and observe-only daemon safety. On-disk agent discovery through
`--add-dir` remains proven
against the pinned 2.1.217 binary via the no-provider probe harness
(fake provider, live-domain tripwire armed, delegation accepted and the
subagent request carried the agent file's frontmatter model). Supervisor
**takeover** carry-through of `--add-dir` is documented for backgrounded
sessions and binary-consistent; the final takeover proof is user-performed
acceptance step L2 (headless sessions run no resident supervisor, so it
cannot be automated without a PTY driver).

Historical: v2.0 live smoke PASSED (user-led, 2026-07-21) for the argv-mode
launcher — superseded by the durable-scope design above.
