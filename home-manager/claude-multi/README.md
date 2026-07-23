# claude-multi v2.1

Composition compiler and thin launcher for a Home Manager-managed Claude
Code environment. The compiler turns one validated composition into
**per-session durable files** — generated agent definitions and session
settings under `~/.local/state/claude-multi/scopes/<uuid>/` — and `execve`s
ordinary Claude Code pointed at them via `--add-dir`/`--settings`. No
scheduler, wrapper daemon, or per-turn interception remains.

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
- **Scope compiler** (`src/claude_multi/scope.py`): pure function
  `(composition, catalog) → scopes/<uuid>/.claude/agents/*.md +
  scopes/<uuid>/settings.json` — generated `cm-*` agent files (frontmatter
  name/model/effort/isolation + canonical role prompt) and compiled session
  settings (permission denies, workflow mode, model fence, worktree base).
  Atomic sibling staging; the scope is always re-derivable from the session
  record + installed catalog (the two authorities).
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
├── bin/                  # claude-multi, claude-multi-dev, claude-multi-proxy
├── tests/                # offline stdlib suite + goldens
└── tests/default.nix     # sandbox test derivation
```

## Normal commands

```text
claude-multi                          quick-confirm and launch (durable scope)
claude-multi --legacy                 launch with the pre-durable argv form
claude-multi compose list|show|new|edit|duplicate|rename|delete|restore-default
claude-multi sessions list|show|forget|link UUID
claude-multi sessions transition UUID --composition NAME
                                      diff + exited-confirm + exact-resume relaunch
claude-multi doctor                   binary/gateway/scope/collision checks
claude-multi doctor --repair UUID     reconverge a session scope to record authority
claude-multi doctor --prune           remove stale scope generations/staging
claude-multi-dev check|review|promote developer onboarding (no provider calls)
claude-multi-proxy init|status|run    gateway control (loopback only)
```

Fork of a managed session: use Claude's native fork and adopt the result
with `claude-multi sessions link UUID`; the launcher refuses to compile
forks itself (native fork persistence with managed flags is unverified).

Design package (rationale, guarantees, verification, rollback):
[`../../docs/claude-multi-final/`](../../docs/claude-multi-final/README.md).

## Rollback

Rolling back to the 2.0 (argv-era) launcher is safe by construction:

- **2.0 launchers fail closed on 2.1 data.** The 2.1 catalog and composition
  documents use the closed v2 schemas (`version.json` bump), which the 2.0
  launcher rejects outright — it never half-reads them. Schema-v2 session
  records (durable era: `mode`, `scope_generation`, `workflows`) are likewise
  unreadable to 2.0, whose session schema requires `version: 1` and rejects
  the new fields.
- **No v2-era transcript is ever stranded.** Transcripts belong to Claude,
  not the launcher; any session recorded by 2.1 remains recoverable with
  native `claude --resume <uuid>` (it runs without the managed scope). Keep
  the 2.1 package's Nix store path around and it can also be invoked
  directly for a fully managed resume of v2 records.
- **HM-generation rollback is the clean path for v1 records.** Switching
  Home Manager back to the pre-activation generation restores the old
  launcher together with the old catalog; v1 (argv-era) records resume under
  it exactly as before.
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

**Durable-scope status (2026-07-22):** 809 offline tests green (1
intentional skip). On-disk agent discovery through `--add-dir` proven
against the pinned 2.1.217 binary via the no-provider probe harness
(fake provider, live-domain tripwire armed, delegation accepted and the
subagent request carried the agent file's frontmatter model). Supervisor
**takeover** carry-through of `--add-dir` is documented for backgrounded
sessions and binary-consistent; the final takeover proof is user-performed
acceptance step L2 (headless sessions run no resident supervisor, so it
cannot be automated without a PTY driver).

Historical: v2.0 live smoke PASSED (user-led, 2026-07-21) for the argv-mode
launcher — superseded by the durable-scope design above.
