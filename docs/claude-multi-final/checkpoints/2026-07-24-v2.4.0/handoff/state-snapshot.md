# State snapshot — checkpoint 2026-07-24 · v2.4.0

Point-in-time evidence for [`README.md`](README.md). Captured 2026-07-24
after the 2.4.0 activation; this file is a record, not a living doc.

## Commits (feature/term-only, nothing pushed)

```
c3c21d3 Final review fixes: override lifecycle, activate flake path, New · Off
c109b44 Enforce New · Off for provider drafts; refresh AGENTS.md open items
da895cf Add upgrade.py to the AGENTS.md module map
a95452d claude-multi 2.4.0: layered contract, `update` command, TUI conventions
1e87ba3 Point product README at the checkpoint handoff
a2afc47 Record final commit in checkpoint handoff and wiki
da1f9e6 Add checkpoint system, wire wikis, codify the documentation map
08a3335 Correct test counts in SANITY.md
```

## Activation

- Home Manager generation **98** (current); gen 97 (2.3.0) retained as
  rollback.
- Profile package: `/nix/store/01m7jfsk17cgil2iklk01jbwjna05i5i-claude-multi-2.4.0`.
- `claude-multi` and `claude-gateway` report `2.4.0`.
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

- Host suite: **1,189 tests OK, 1 intentional skip** (93s) — includes the
  real-binary probes against 2.1.218 (auto+manual compaction hooks,
  delegation accepted, takeover fail-closed by design).
- Offline package build: 2.4.0.
- Sandbox suite: green (`claude-multi-tests` derivation).
- `git diff --check` clean; tree clean at `c3c21d3`.
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
