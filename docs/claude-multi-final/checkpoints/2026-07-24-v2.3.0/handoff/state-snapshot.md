# State snapshot — checkpoint 2026-07-24 · v2.3.0

Point-in-time evidence for [`README.md`](README.md). Captured 2026-07-24
after activation + hygiene; this file is a record, not a living doc.

## Commits (feature/term-only, nothing pushed)

```
08a3335 Correct test counts in SANITY.md
14bd944 Add AGENTS/USAGE documentation and the SANITY design assessment
9caae8b Record 2.3.0 activation and session hygiene in STATUS
a9b2842 Prune also collects orphaned pre-2.2 digest-only lead prompts
f50e66f Record 2.3.0 review round and live repair in STATUS/HANDOFF
ae228a1 Address cross-family review of 2.3.0 (REVISE -> resolved)
d65218d claude-multi 2.3.0: lifecycle identity + ordinary gateway + hardening
```

## Activation

- Home Manager generation **97** (current); gen 96 (2.2.0) retained as
  rollback; gen 95 link removed (`home-manager remove-generations 95`).
- Profile package: `/nix/store/v8h7y3nzj84zqsw4j71ff14jjzg0y4l2-claude-multi-2.3.0`.
- `claude-multi`, `claude-gateway`, `claude-multi-proxy` all report `2.3.0`.
- Gateway: `cli-proxy-api.service` active after activation restart;
  `GET http://127.0.0.1:8317/healthz` → 200.
- Hook shim `~/.local/state/claude-multi/bin/claude-multi-hook` targets the
  activated package with PATH fallback.

## Health (verbatim doctor head)

```
Ready
Attention
  - 1 legacy (pre-durable) record(s) upgrade on resume: resume each once, or `sessions forget` the ones you no longer need
Managed Claude 2.1.217 verified (sha256 2630fc5dc6db..., /home/kotur/.local/share/claude/versions/2.1.217).
```

- Managed binary: pinned 2.1.217 (hash-verified); configured symlink on
  2.1.218 (advisory-only drift; re-pin queued in open-items).
- Shared daemon: present at `/tmp/cc-daemon-1000`, running 2.1.218.

## Evidence

- Host suite: **1,167 tests OK, 1 intentional skip** (full discovery,
  85s). One earlier transient PTY error under extreme load did not
  reproduce; the bare-launch PTY child now self-diagnoses via faulthandler.
- Offline package build: `/nix/store/y1hcbp7ki01kcqxb5c0jw3frsbpzparx-claude-multi-2.3.0`.
- Nix sandbox suite: `/nix/store/ixfmr7f59x6l73yr7p5xhap57hj62cnc-claude-multi-tests`.
- Cross-family review (cm-reviewer-sol-xhigh, read-only): REVISE, zero
  must-fix; both should-fix items resolved in `ae228a1` with regression tests.
- `git diff --check` clean; tree clean at `08a3335`.

## Live state

- **16 sessions** (15 durable + 1 legacy `9bc5fd42`, which keeps its
  transcript), down from 31 after the approved hygiene: 15 transcriptless
  records forgotten (fresh transcript check per record), lead prompts pruned,
  2 orphaned pre-2.2 digest-only prompts swept.
- All durable scopes embed the stable hook shim + `autoCompactEnabled: true`
  + narrow lead fence; records refreshed to schema-v3 with context fields
  (launcher 2.3.0).
- Pointers: 4, all live. Locks: free. `.prev` retained for 2 sessions
  (transition rollback copies, by design).
- Pre-repair backup: `/tmp/cm-live-backup-20260724-110010`.
- Running managed sessions at capture: pid 9285 (7fa62138, this work),
  pid 4191432 (a24fc875, sol-direct — its start-time hooks reference the
  gen-95 store path until it exits; advisory-only).

## Environment notes

- Gateway serves model aliases for anthropic/kimi/openai/qwen routes;
  Qwen live verification passed 2026-07-23 (one consent-gated call).
- User-level `~/.claude/settings.json` has `autoCompactEnabled: false` —
  neutralized for managed sessions by the compiled `autoCompactEnabled: true`
  pin (2.3.0); plain `claude` keeps the user setting by design.
