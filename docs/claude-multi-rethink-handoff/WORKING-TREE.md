# Working-tree inventory

This snapshot is uncommitted. Re-run `git status`, `git diff`, and inspect every
untracked file before deciding what to keep.

## Baseline

- HEAD: `cf04fce`
- No current changes are installed or activated.
- Do not stage or commit until the new architecture decides ownership.

## Tracked G0/native-contract changes

Representative files:

- `home-manager/claude-multi/catalog/native-contract.json`
- `home-manager/claude-multi/schemas/native-contract.schema.json`
- `home-manager/claude-multi/src/claude_multi/{catalog,cli,compiler,launch}.py`
- canonical role prompts and `catalog/roles.json`
- compiler/launch/CLI/catalog/native-policy tests and default goldens

Intent:

- promote inspected native artifact evidence to 2.1.217 while retaining model
  minimum-tested metadata at 2.1.216;
- verify immutable binary path/hash directly and make symlink drift advisory;
- share binary verification with Doctor;
- add generic `Agent(claude)` denial, updater hygiene and reserved env keys;
- add a session-scoped lead sentinel;
- align nested-delegation prompts with the now-frozen design.

Re-evaluate every item—especially prompts, reserved keys and policy semantics.

## Untracked P0/probe files

- `home-manager/claude-multi/src/claude_multi/p0.py`
- `home-manager/claude-multi/src/claude_multi/p0inner.py`
- `home-manager/claude-multi/src/claude_multi/probe.py`
- `home-manager/claude-multi/tests/test_p0.py`
- `home-manager/claude-multi/tests/test_probe.py`
- tracked integration changes in `src/claude_multi/dev.py`

The harness provides namespaces/mount/network/IPC/device/keyring gates,
loopback fake provider, metadata-only evidence and explicit real consent. It
passed review and staged `--version` acceptance, but may be more complex than
the final architecture needs.

## Frozen docs and handoff

- `docs/claude-multi-durable-session-config.md`
- `docs/claude-multi-durable-session-config-plan.md`
- `docs/claude-multi-rethink-handoff/`

## Saved reference bundle

`artifacts/` contains:

- binary-capable tracked diff against `cf04fce`;
- archive of untracked implementation/historical-doc files;
- git status/stat/log snapshots;
- SHA-256 checksums and restoration instructions.

This bundle preserves work for selective reuse. It is not a patch that should
be applied wholesale without architecture review.

## Generated artifacts

Remove `__pycache__`/`.pyc` before final status checks. P0 evidence under
`/tmp/opencode` is ephemeral and may disappear after reboot.

## Safe review commands

```bash
git status --short
git diff --check
git diff --stat
git diff -- home-manager/claude-multi
```

Ordinary `git diff` omits untracked content and flakes ignore untracked source.
