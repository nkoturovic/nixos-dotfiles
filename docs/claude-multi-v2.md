# claude-multi v2 — source cutover and runbook

This document covers the v2 source cutover state, build-only verification,
and the **later, separately authorized** activation procedure. The DeepWork
workflow that produced this cutover explicitly does **not** activate
anything: the active v1 process and `cli-proxy-api` user service remain
unchanged until a separate Home Manager switch/restart is authorized.

## Cutover summary

v2 replaces the v1 profile/conservation/static-agent architecture with one
composition compiler. Removed from source: v1 launcher/proxy scripts, the
static model-bound plugin agents, the v1 settings file, the v1 proxy config
template, and the four v1 test scripts. Added: the
`home-manager/claude-multi/` tree (trusted catalog, schemas, stdlib source,
bins, tests, package/module Nix), wired into `home-manager/kotur.home.nix`
via a single `imports` entry, plus `checks.x86_64-linux.claude-multi` in
`flake.nix`. Both CLIProxyAPI patches are unchanged; auth state under
`~/.local/share/claude-multi` is preserved.

## Build-only verification (no activation)

```bash
# full offline unit suite
PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH=home-manager/claude-multi/src:home-manager/claude-multi/tests \
  python3 -m unittest discover -s home-manager/claude-multi/tests -p 'test_*.py'

# standalone gates (flake-independent)
nix build --offline --no-link --file home-manager/claude-multi/package.nix
nix build --offline --no-link --file home-manager/claude-multi/tests/default.nix

# flake gates (see limitation note below)
nix flake check --offline --no-build
nix build --offline --no-link .#checks.x86_64-linux.claude-multi
nix build --offline --no-link .#homeConfigurations.kotur.activationPackage
```

**Flake source visibility limitation:** before the new files are committed,
`nix flake …` on the live checkout cannot see untracked files. Verify with a
temporary path copy instead (no staging/commit):

```bash
rsync -a --exclude='.git' --exclude='result' --exclude='.slim' \
  /home/kotur/personal/nixos-dotfiles/ /tmp/flake-verify/
nix flake check path:/tmp/flake-verify --offline --no-build
nix build path:/tmp/flake-verify#checks.x86_64-linux.claude-multi --offline --no-link
nix build path:/tmp/flake-verify#homeConfigurations.kotur.activationPackage --offline --no-link
```

Known pre-existing failure unrelated to this cutover: the
`nixosConfigurations.kotur-pc` evaluation fails on `pkgs.greetd.tuigreet`
(also broken at the baseline commit). The home configuration and checks are
unaffected.

Builds produce store paths only — they never activate a generation or
restart any service.

## Activation procedure (requires explicit authorization)

1. `home-manager switch --flake .#kotur`
2. Fresh shell; verify store paths:
   `which claude-multi claude-multi-dev claude-multi-proxy cli-proxy-api`
   resolve into `/nix/store/…-claude-multi-2.0.0` and the CLIProxyAPI path.
3. `systemctl --user status cli-proxy-api` — only if the new generation
   changed the unit (it does), the sd-switch reload restarts the service
   with the new `claude-multi-proxy run` ExecStart. Otherwise
   `systemctl --user restart cli-proxy-api` manually.
4. `claude-multi-proxy status` — reports runtime/proxy health, loopback only.
   `claude-multi-proxy init` is only needed if the gateway config or secret
   layout changed; existing auth records are preserved.
5. `claude-multi doctor` — offline validation plus loopback readiness.

## Rollback

Rollback is a previous Home Manager generation:
`home-manager generations` then activate the prior generation path (or
`home-manager switch --flake .#kotur` after `git checkout <prior>` of the
source). The v2 runtime state (compositions, sessions) lives outside any
generation and does not block rollback.

## Session, state, and auth preservation

- OAuth/auth records under `~/.local/share/claude-multi/auth` are never
  touched by the cutover; both login helpers exec the pinned proxy's own
  flows against those same paths.
- The local gateway token at `~/.config/claude-multi/api-key` is read or
  created (mode 0600) by proxy control; it is never printed.
- v1 sessions resume natively; adopt them into v2 memory with
  `claude-multi sessions link UUID`. Private Claude files are never scraped.
- Sessions using current canonical IDs and retained `gpt-multi-*` or
  `claude-multi-*[1m]` selectors remain resolvable. Sessions created with removed
  pre-canonical aliases such as `claude-multi-fable-5`, `claude-multi-sol-*`, or
  `claude-multi-gpt55-*` require a fresh handoff or rollback to the prior Home
  Manager generation; v2 intentionally does not retain those compatibility aliases.

## Live smoke status — user-led acceptance: PASSED (2026-07-21)

Activation and local smoke passed (generation 73 active, v2 proxy active,
all three providers configured, doctor/default Ready). The user-led first
real Claude/provider session then **passed**: the bare launch initially
exposed a no-controlling-terminal bug, fixed by preferring real `/dev/tty`
via separate read/write handles with a TTY stdin/stdout fallback (437 tests
pass, 1 skip). The corrected package
`/nix/store/fxapndpszjxdxz61jm5m12q1ngw014i4-claude-multi-2.0.0` (444 tests,
1 skip) was activated with doctor/proxy Ready. The user subsequently created
and live-validated the `kimi-sol` composition: Kimi K3 lead; Sol-high
preferred analyst/implementer with Kimi-max alternates; Sol-xhigh preferred
reviewer for focused bounded small-to-medium review; Kimi-max alternate
reviewer for architecture/plan validation, security, broad cross-cutting,
complex, high-risk, or large-context review; generated cross-family rules
route Sol-authored work to Kimi review and Kimi-authored work to Sol review;
Anthropic/GPT-5.5 off; scalar 372000. The trusted default composition remains
unchanged.

No full automated provider smoke was run; `claude-multi-dev smoke-test
MODEL --allow-provider-call` remains only a fallback diagnostic.

## Provider smoke boundary

No command in the cutover or normal paths contacts a provider. The single
optional live check is `claude-multi-dev smoke-test MODEL
--allow-provider-call` with a fixed non-sensitive prompt, held in reserve as
a diagnostic only.
