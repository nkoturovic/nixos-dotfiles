# State snapshot — checkpoint 2026-07-24 · v2.4.1

Point-in-time evidence for [`README.md`](README.md). Captured 2026-07-24
after the 2.4.1 activation; this file is a record, not a living doc.

## Commits (feature/term-only, nothing pushed)

The 2.4.x series on `feature/term-only` from `d65218d` (2.3.0 bundle) through
the 2.4.1 final-hardening commits (update flow, layered contract, TUI
conventions + health/update surface, proxy/dev hardening, checkpoint system).
See `git log --oneline d65218d..HEAD`.

## Activation

- Home Manager generation **99** (current); gen 98 (2.4.0) and gen 97 (2.3.0)
  retained as rollback.
- `claude-multi` and `claude-gateway` report `2.4.1`.
- Gateway: `cli-proxy-api.service` active after the activation restart;
  `GET http://127.0.0.1:8317/healthz` → 200.
- Hook shim targets the activated package with PATH fallback.

## Health (verbatim doctor head)

```
Ready
Managed Claude 2.1.218 verified (sha256 e12071751a93..., /home/kotur/.local/share/claude/versions/2.1.218).
Configured symlink /home/kotur/.local/bin/claude resolves to the inspected artifact.
Shared daemon: shared-daemon domain present at /tmp/cc-daemon-1000 (pid not exposed).
Sessions: 15 recorded · 15 durable · 0 legacy.
```

Zero Attention lines: the binary is re-pinned, all scopes embed the shim +
compaction pin, no legacy records remain, and the update command is
idempotent (`claude-multi update` → "nothing to re-pin").

## Evidence

- Host suite: **1,194 tests OK, 1 intentional skip** (93s) — includes the
  real-binary probes against 2.1.218 (auto+manual compaction hooks,
  delegation accepted, takeover fail-closed by design).
- CLIProxy: live gateway config byte-identical to a fresh catalog render;
  every catalog selector's base alias served; `[1m]` proven client-side-only.
- TUI: health strip + update badge + U/H actions verified; KeyBar wraps.
- Offline package build: 2.4.1.
- Sandbox suite: green (`claude-multi-tests` derivation).
- `git diff --check` clean.
- TUI: PTY-verified Esc-only exits and the uniform column-2 margin on the
  card, sessions screen, and editor.

## Live state

- **15 sessions** (all durable, zero legacy) after the completed hygiene;
  all scopes embed the stable hook shim, narrow lead fence, and
  `autoCompactEnabled: true`; all records are schema-v3 with context fields.
- No operator contract override present (packaged == effective == 2.1.218).
- Pointers: 4, all live. `.prev` retained for 2 sessions (transition
  rollback copies, by design).
- Running managed sessions at capture: pid 9285 (7fa62138, the doc/update
  session), pid 4191432 (a24fc875, sol-direct; its start-time hooks
  reference the retained gen-95 store path until it exits — advisory only).

## Environment notes

- Plain `claude` runs 2.1.218 via its own auto-updater (upstream channel
  intentionally left on); managed sessions launch the same pinned artifact
  with `DISABLE_AUTOUPDATER=1`.
- User-level `autoCompactEnabled: false` remains neutralized for managed
  sessions by the compiled pin; ordinary gateway scopes respect it.
