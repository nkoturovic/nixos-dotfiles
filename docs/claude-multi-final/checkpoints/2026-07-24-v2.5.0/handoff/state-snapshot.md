# State snapshot — checkpoint 2026-07-24 · v2.5.0

Point-in-time evidence for [`README.md`](README.md). Captured 2026-07-24
after the 2.5.0 activation; this file is a record, not a living doc.

## Commits (feature/term-only, nothing pushed)

The 2.4.x→2.5.0 series from `d65218d` (2.3.0 bundle) through the Opus 5
integration (catalog + registry patch + profiles + live verification).
See `git log --oneline d65218d..HEAD`.

## Activation

- Home Manager generation **101** (current); gen 100 and gen 99 retained
  as rollback.
- `claude-multi` and `claude-gateway` report `2.5.0`; catalog_version 4.
- CLIProxyAPI rebuilt with `cli-proxy-api-opus-5-model.patch` (third local
  patch) and restarted cleanly (`healthz` 200).
- Hook shim targets the activated package with PATH fallback.

## Health (verbatim doctor head)

```
Ready
Managed Claude 2.1.218 verified (sha256 e12071751a93..., /home/kotur/.local/share/claude/versions/2.1.218).
Configured symlink /home/kotur/.local/bin/claude resolves to the inspected artifact.
Shared daemon: shared-daemon domain present at /tmp/cc-daemon-1000 (pid not exposed).
Sessions: 15 recorded · 15 durable · 0 legacy.
```

## Evidence

- Host suite: **1,194 tests OK, 1 intentional skip** (94s) — includes the
  real-binary probes against 2.1.218 (compaction hooks, delegation,
  takeover fail-closed).
- Offline package build: `claude-multi-2.5.0`. Sandbox suite:
  `/nix/store/kmqj7vx3aka29cinww3gf981nl43jrfp-claude-multi-tests`.
- CLIProxy: live config byte-identical to a fresh catalog render;
  `/v1/models` serves every catalog alias including `claude-opus-5` and
  `claude-multi-opus-5`; live verification call PASSED (consent-gated,
  exact instruction, clean `end_turn`, native usage fields).
- TUI: card renders the new default (Opus 5 lead, Sol preferred +
  worktree, Kimi alternates, Opus 5 reviewer alternate, health strip);
  Esc-only exits; uniform column-2 margin; KeyBar wraps.
- `git diff --check` clean.

## Profiles (all verified by resolution + print-launch)

| Preset | Lead | Preferred subagents | Alternate |
| --- | --- | --- | --- |
| default | opus5 | sol (all roles) | kimi-k3; opus5 (reviewer) |
| opus-sol | opus5 | sol (all roles) | opus5 (reviewer) |
| opus-kimi | opus5 | kimi-k3 (all roles) | opus5 (reviewer) |
| fable | fable | sol (all roles) | kimi-k3; opus (reviewer) |
| kimi-sol | kimi-k3 | sol (all roles) | kimi-k3 (max) |
| qwen-sol | qwen38 | sol (all roles) | kimi-k3 (max) |
| sol-direct | sol | — | — |

## Live state

- **15 sessions** (all durable, zero legacy); scopes embed the stable hook
  shim, narrow lead fence, and `autoCompactEnabled: true`; records are
  schema-v3 with context fields.
- No operator contract override present (packaged == effective == 2.1.218).
- Pointers: 4, all live. `.prev` retained for 2 sessions (transition
  rollback copies, by design).
- Running managed sessions at capture: pid 9285 (7fa62138, kimi-sol),
  pid 4191432 (a24fc875, sol-direct).

## Environment notes

- Plain `claude` runs 2.1.218 via its own auto-updater (upstream channel
  intentionally left on); managed sessions launch the same pinned artifact
  with `DISABLE_AUTOUPDATER=1`.
- Opus 5's 1M context bound is **user-attested** (announcement states no
  bound); the validated floor stays conservative until near-limit
  acceptance (open item 3).
