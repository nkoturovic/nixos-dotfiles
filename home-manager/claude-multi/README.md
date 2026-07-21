# claude-multi v2

Clean-slate composition compiler for a Home Manager-managed Claude Code
environment. One validated composition model replaces v1 static profiles,
model-bound agent files, and conservation modes. The compiler runs once
before launch, then `execve`s ordinary Claude Code — no scheduler, wrapper
daemon, or per-turn interception remains.

## Architecture

- **Trusted catalog** (`catalog/`): versioned JSON for providers, models,
  roles, the native-contract evidence record, the balanced default
  composition, and the canonical model-neutral role prompts. The provider
  profile owns transport/auth references and the canonical `fork: true`
  passthrough rule.
- **Compiler** (`src/claude_multi/`): strict JSON/schema validation,
  composition resolution, deterministic agent generation (`cm-lead` plus
  `<role>-<model>-<lane>` variants), native built-in policy, environment,
  session records, and launch. Python standard library only.
- **Renderer** (`render.py`): pure deterministic CLIProxyAPI YAML from
  trusted JSON; provider secrets resolve only at runtime into a mode-0600
  artifact outside this repository.
- **Onboarding** (`dev.py`): Draft → Check → Review exact diff/hash →
  Promote source for new models/providers, with scratch candidate builds.
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
claude-multi                          quick-confirm and launch
claude-multi compose list|show|new|edit|duplicate|rename|delete|restore-default
claude-multi sessions list|show|forget|link UUID
claude-multi doctor                   offline validation + loopback checks
claude-multi-dev check|review|promote developer onboarding (no provider calls)
claude-multi-proxy init|status|run    gateway control (loopback only)
```

## Local-only verification

```bash
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=home-manager/claude-multi/src:home-manager/claude-multi/tests \
  python3 -m unittest discover -s home-manager/claude-multi/tests -p 'test_*.py'
nix build --offline --no-link --file home-manager/claude-multi/package.nix
nix build --offline --no-link --file home-manager/claude-multi/tests/default.nix
```

No command in the normal paths contacts a provider. The optional
`claude-multi-dev smoke-test` requires explicit `--allow-provider-call`.

**Live smoke status:** activation and local checks passed (generation 73
active, v2 proxy active, all providers configured, doctor/default Ready),
but the implementation workflow made no provider request or real Claude
launch. The user is performing the first real Claude/provider session as
the live user-acceptance smoke test — **IN PROGRESS / awaiting user
result**. The automated smoke-test remains only a fallback diagnostic.
