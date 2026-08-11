# 021 — Deep system analysis: correctness hardening batch

## Context

Operator directive: an extensive multi-lane analysis of the whole
claude-multi / Claude Code integration — find gaps, shortcomings, and
correctness issues across core machinery, TUI, gateway integration,
composition lifecycle, hygiene, and security — then organize, plan, and
proactively address everything.

A six-lane workflow (core / tui / gateway / compose / hygiene / security)
plus adversarial verification produced the finding list; three lanes that
died on context were re-dispatched with narrowed briefs (no whole-file
reads, windowed greps) and completed. Verdicts: core "strong shape",
TUI "well-architected", gateway "good shape" — no P0s; the batch is the
P1/P2 set, each fixed with regression pins.

## Findings addressed

### Core

- **Corrupt-record forget** (P1): `forget_session` dead-ended on the same
  load that defined the corruption. Now forgets load-free — scope removal
  + a by-id pointer sweep that re-reads under the FileLock before unlink.
  CLI prints the "unreadable; forgetting load-free" note.
- **Renamed-directory resume** (P1): the transcript sits exactly where the
  record points but the recorded project dir is gone; resume would fail
  entering the CWD at launch. New `cwd-missing` resume gate names both
  real exits (rename back / move transcript + relink-runtime). (An earlier
  elsewhere-branch discriminator made the branch unreachable — replaced.)
- **`sessions forget` live guard** (P2): forgetting a running session
  deleted its record + scope from under it. The CLI now refuses live
  (with the `sessions stop` remedy) and self (inside the session) —
  the guard `sessions stop` already trusted; corrupt records keep the
  self-guard by id (no runtime id to liveness-check).
- **Managed 1M-lead equivalence** (P2): the SessionStart reconciliation
  accepted `{wire, client_selector}` only; Claude Code may report the
  canonical `wire+'[1m]'` form (the ordinary path accepts it, verified).
  Misses recorded false model drift (repair-needed). The managed set now
  mirrors `compiler.direct_model_for_selector` for ≥1M leads.
- **Hook survives catalog drift** (P2): `catalog.models[lead_id]` was an
  uncaught KeyError when the recorded lead left the catalog — the hook
  died and lost the runtime-id reconciliation. `.get()` + skip; the
  unverifiable model report records as informational drift (R1 P2).

### Gateway / security

- **served_models status tuple** → **401 is a problem**: the daemon
  holding an older config rejects the rendered token on /v1/models while
  healthz stays green. Now a doctor problem naming restart (transient
  non-200 stays an informational skip; gateway-down-after-readiness gets
  its own skip line).
- **Listing hardening**: redirects are never followed (urllib would
  forward the credential header, incl. HTTPS→HTTP downgrade); remote
  bodies bounded before the strict-JSON limit; HTTP status / connection
  reason / type-name-only error messages (no request data); parse
  failures inside the redacted boundary ("unexpected shape").
- **Registry guards**: `add_model` rejects OAuth-pool providers (a
  pool-backed custom alias renders past catalog admission and 401s
  upstream); header auth is fail-closed to `x-api-key` (the pinned gateway
  build silently falls back to Bearer otherwise); the whole
  load-modify-save registry transaction runs under one FileLock.
- **Custom merge shadow safety**: hand-written registry entries that
  collide with the trusted catalog are dropped loudly —
  `merge_conflicts` names them (doctor attention); the catalog always
  wins the merge.
- **Rendered-but-unserved marking**: fully wired rows the running gateway
  doesn't serve are marked `(not served)` with the init+restart detail;
  Enter asks before launching anyway (init/restart pending funnel state).
- **Session-event bounded stdin**: the hook payload read is bounded at
  the strict-JSON limit + 1 on original bytes (a hostile pipe can't
  allocate unbounded memory before the limit runs).

### Compose / TUI

- **Tab-cycle discard guard**: cycling away from an unsaved composition
  ("Unsaved launch" / "Unsaved editor changes") asked nothing — one Tab
  destroyed editor work. Curses modal ("Cycle away"/"Stay") + line-mode
  [y/N] now guard both modes.
- **CommittedStateError honesty**: a committed-but-durability-unconfirmed
  write must never report "unsaved" (a lie in the dangerous direction);
  the failure plan's source is "Committed, durability unconfirmed".
- **Durable deletes**: `CompositionStore.delete` routes through
  `state.remove_private`; the rename-source delete crash window is
  documented.
- **Card project line** renders only when there are project agents or
  collisions to report (empty-state noise removed; label included).
- **Native overflow indicator**: the newest-20 native-session cap now
  says `+N more (newest 20 shown)` in the section header.
- Editor BLOCKED footer shows `errors[0] + "  (+N more)"`; QUICK_HELP /
  PROVIDERS_HELP / ORDINARY_HELP wording repairs (U confirm, N/A keys,
  typed selectors, j/k, home/end); line-mode `h` offers repair-all;
  dead Shift-Tab branch removed; `interactive` NameError in the line-mode
  s/g hints fixed (pre-existing, surfaced by the suite).

### Hygiene

- `package.nix` filters `__pycache__`/`.pyc` out of the source (they
  reached the store share tree and churned the source hash on every
  test run); working-tree caches removed.
- 4 dead module-level symbols removed (`_validate_uuid`, `_none_resolver`,
  `COMMANDS`, `ROOT_METADATA_KEYWORDS`).
- Docs: README title no longer hardcodes a stale version (points at
  `version.json`); HANDOFF `--model` names catalog-or-custom ids instead
  of an incomplete enumeration; USAGE Tab/P split (Tab curses, P line
  mode); SANITY Q5 token-split finding marked closed (render-path
  FileLock since 2.4.1).

## As-built amendments (cross-family review)

- **Forget liveness is an under-lock check** (`forget_session`'s
  `pre_delete_check`, sol-xhigh must-fix): a verdict taken before blocking
  on the lifecycle lock is stale by deletion time. The shared
  `_forget_liveness_guard` runs with a fresh prefix scan for the CLI and
  the picker alike; corrupt records get the stable-id prefix check (their
  earlier bypass closed). Picker live rows get stop-first (E) guidance
  instead of the modal.
- **Pointer sweep deletes durably** (`state.remove_private`, fsyncs the
  pointer directory) — a bare unlink could outlive the fsynced record
  delete across a crash.
- **cwd-missing remedy is completable**: no `<new-slug>` to compute by
  hand (the real algorithm hashes over 200 UTF-16 units). Rename back, or
  relink to the new dir and resume again — the follow-up gate now names
  the exact expected transcript location (also added to the elsewhere
  branch).
- Nits: `add_model`'s `catalog_providers` is dict-typed (the OAuth guard
  indexes); listing `HTTPError` sockets are closed explicitly.

## Explicitly not in this batch

- Catalog scope-exceeds message repair-action text (optional P2; the
  error already names both scope fields and their JSON paths).
- Editor enable-and-set-lead confirm; OpenAI-compatible upstream adapter
  (both deferred with triggers from 019/020).

## Files

- `cli.py`: gate (`cwd-missing`), forget guards, hook equivalence +
  drift, served radar (401), unserved marking, bounded stdin, cycle
  guard, CommittedStateError source, project line, native overflow,
  help/wording, `interactive` NameError.
- `sessions.py`: corrupt-forget branch + `_sweep_pointers_for`.
- `custom.py`: `_mutate` FileLock, OAuth-pool/header guards,
  `merge_conflicts`, merge drops.
- `proxy.py`: listing hardening; `launch.py`: served_models tuple +
  `read_gateway_token`.
- `tui.py`: editor BLOCKED footer shows `errors[0] + "  (+N more)"`.
- `package.nix`: cleanSourceWith filter.
- Tests: 41 new pins across test_cli (13 classes), test_proxy
  (redirect/error shapes), test_sessions (store-level corrupt forget).
