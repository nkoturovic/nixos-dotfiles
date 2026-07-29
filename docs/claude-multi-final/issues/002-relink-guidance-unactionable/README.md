# 002 — Unactionable repair-needed guidance + relink semantics

**Status: in release** · fixed in 2.8.0 (D41)

## Report (2026-07-29, operator)

The composition card showed:

```
Status  BLOCKED
  - session 58c87cef-… has unresolved runtime/CWD identity; repair it with
    `claude-multi sessions relink-runtime` before resuming
```

The operator ran the suggested command — nothing cleared ("no luck").
The only working resume was via native `claude agents`, which bypasses
the launcher entirely. The session's own lead hit the same wall when
repairing it manually: only `relink-runtime <uuid> <runtime_uuid> --cwd
<recorded cwd>` cleared the flag.

## Root cause (investigator-verified)

1. The suggested command is **not runnable as printed** — it lacks the
   required positional args (`uuid`, `runtime_uuid`).
2. `SessionStore.relink_runtime` only pops `observed_cwd` when `--cwd` is
   passed (sessions.py ~L910-912); the bare form reconciles the runtime
   ID but leaves the repair-needed flag — by code, but the edge was
   untested and undocumented (no evidence it was a deliberate contract).
3. Every surface (card Status, launch guard, transition guard, sessions
   list, doctor) already holds every field needed for a fully
   copy-pasteable command; the defect is duplicated/incomplete
   presentation. `sessions.pending_fork_message` is the established
   single-source pattern to mirror.

## Fix (this batch)

- `sessions.relink_message(record)` — single actionable message source
  mirroring `pending_fork_message`: real UUIDs, both `--cwd` options
  labeled (keep recorded dir = common case; observed dir = intentional
  re-home only).
- Semantics: bare `relink-runtime` now also clears `observed_cwd`
  (operator re-asserts the recorded cwd by running it; `observed_model`
  untouched). Worst case if wrong: native "No conversation found", which
  issue 003's gate pre-detects.
- All surfaces consume the helper (card, launch guard, transition guard,
  sessions list, doctor).
- Tests: bare-clears-cwd, model preserved, message content, guard texts.

## Hardening rounds (two cross-family reviewers)

Round 1 (Sol xhigh): gate order (transcript outranks liveness), precommitted narrowed to daemon-only, ordinary scope leak, wrap corrupting commands, quoting/model-only guidance gaps, enforcement before callback, ordinary combined flow, force placement. Round 2/3 (both reviewers, resumed after API/stream deaths per the D42 rule): fail-closed slug ambiguity, non-file transcripts, target-only liveness + stop-rescan, --force threaded through all interactive paths, transient gate notices (no plan poisoning), card repair modal end-to-end, ordinary relink message, docs precision. Final verdicts: approve (both).

## Verification

1,361 tests OK (incl. new RelinkGuidanceTests: bare-clears-cwd, model
preserved, message content, launch/transition guard texts); sandbox
derivation green; two cross-family reviews → both approve; 1,377 tests OK.
