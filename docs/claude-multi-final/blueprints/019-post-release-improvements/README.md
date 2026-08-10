# 019 — Post-release improvement batch (2.14.0)

## Context

After 2.13.0 activation the operator asked for a full improvement pass over
the new surfaces ("make them fit better, easier to use… analyze everything
first"; "make sure that through the TUI everything is usable and intuitive";
"low-hanging fruits should be tracked down and implemented"). Analysis ran as
a six-leg agent evaluation: discoverability / coherence / onboarding / safety
lenses plus a deep Kimi TUI-usability walk (keystroke traces + first-run
simulation) and a lead friction pass (the sol friction lens twice died on
context overflow; its ground was covered by the Kimi leg).

## What landed (grouped)

### First-run & dead-ends
- BLOCKED cards with a secret problem now point at the in-TUI fix
  (G → P → Enter, masked entry) — the biggest first-run gap.
- OAuth pools with **no credential record** are marked `(sign in needed)` in
  the G picker (dim + Enter-confirm naming the exact login command); before,
  the card went Ready and the session failed at request time.
- Line mode gains the **H** doctor key it always promised (footer + branch),
  and the TUI doctor prints the "not damage" footer the CLI had.

### Providers pane & secret entry
- Save-modal button order is Save-first like every other input modal
  (type → Enter → Enter confirms; Esc cancels).
- The pane renders a **config-drift / gateway-down banner** from the shared
  snapshot (previously only doctor could explain <100% served).
- The pane gains `?` help; action keys are case-insensitive; failures render
  in warn (not accent).
- `proxy.set_secret_value` hardened: FileLock-serialized read-modify-write
  (no lost keys from concurrent saves), candidate re-validated through the
  shared strict parser (a saved file is always consumable; duplicates of the
  repaired key collapse), `export` prefixes preserved exactly, LF/newline
  normalization documented instead of overclaimed.

### Coherence & wording
- Every restart instruction is the runnable `systemctl --user restart
  cli-proxy-api` (never the bare shorthand), with the "restart between
  turns" timing note; connect hints at every failure point (modal, line
  listing, CLI warning).
- Doctor radar: config-drift established → secondary selector classifications
  are skipped (not independently actionable); a healthz→models race reports
  a transient skip line instead of silence; stale-alias info is action-first.
- `compose list` help + chooser titles name the MRU order; the transition
  chooser marks the current composition; keybars order `?` just before Esc
  (overflow elides middle entries first — D30).

### Editor
- Refusal messages render while BLOCKED (previously invisible exactly when
  repairing); `←→` radio movement documented in help; ^C routes through the
  dirty-discard modal instead of silently dropping edits; duplicated method
  definitions removed (merge artifact).

### Onboarding & discovery
- `claude-multi discover PROVIDER` — **explicit-invocation-only** provider
  model listing (the invocation is the per-call approval). Verified endpoint
  matrix: **Kimi works** (Anthropic-shape `GET /coding/v1/models`,
  x-api-key) — this is also the evidence behind the kimi-k3 1M
  qualification upgrade (provider advertises `context_length: 1048576`);
  **Qwen Token Plan does not** (404 "Not support", verified); OAuth pools
  have no direct credential to list with. Output marks each advertised id
  as cataloged vs onboarding candidate.
- `claude-multi-dev model add --like MODEL --id X --wire-id Y` scaffold:
  mechanical fields inherited from the sibling (lane structure, effort
  contracts, lead block, minimum_tested), selectors derived
  pattern-preservingly + collision-checked, judgment fields become QUALIFY
  markers (display / qualification / routing_note; role_hints reset;
  validated_tokens capped; user-attested bounds stripped).
- `claude-multi-dev --help` (two tracks + the seed-pin runbook), and
  `promote` prints the remaining runbook instead of ending at "promoted".
- Dead `fixtures` draft field removed (schema tolerates it for old drafts).

### Catalog
- kimi-k3 qualification: the 1M bound is now **provider-advertised**
  (verified via the approved listing probe) — still not near-limit
  benchmark-verified; the phrase the catalog rule requires is retained.
- catalog 16, launcher 2.14.0.

## What was deliberately NOT done (lens consensus)

- No auto init/restart from the TUI (ordinary sessions don't retry
  mid-stream; the guidance carries timing wording instead).
- No standalone providers screen / new card key; no live discovery in
  doctor or the pane (loopback-only stays the doctrine there).
- No TUI composition generator (availability mutation would be silent);
  no new MRU state; no line-mode duplicate editor; no `_VALUE_SHAPE`
  widening without a documented provider format.
