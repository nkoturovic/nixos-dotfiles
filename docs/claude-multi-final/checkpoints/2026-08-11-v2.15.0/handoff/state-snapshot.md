# State snapshot — 2026-08-11 · v2.15.0

Evidence behind the checkpoint README claims. Verify before trusting.

## Activation

- `home-manager switch --flake /home/kotur/personal/nixos-dotfiles#kotur`
  → exit 0; HM generation **122** current (121/120 rollback).
  `claude-multi --version` → `claude-multi 2.15.0`.
- `claude-multi doctor` → **Ready**; `Managed Claude 2.1.220 verified
  (sha256 674f61f20ff3…)`; symlink resolves to the inspected artifact;
  gateway radar silent (served == rendered, no drift); 33 recorded · 33
  durable · 0 legacy. No repair-all needed (no scope-affecting changes).
- Live smoke: `claude-multi custom list` answers (empty registry);
  `compose list` MRU-first with ages; `models` two-line output.

## The 020 review story (why the seam tests exist)

The 2.15.0 cross-family review (glm52 integration + qwen38 system scan;
the sol-xhigh safety leg died on context overflow — its territory was
covered by the system leg's seam analysis) found defects exactly where
UI-level tests never reach:

- **P0**: `schemas/session.schema.json` `context_profile` enum rejected
  `custom-<n>` — every custom ordinary launch would have died at record
  save. Widened (oneOf: enum | custom pattern) + record-save regression.
- **P0**: `_connect_hint` indexed the bare catalog → KeyError for custom
  providers (line-mode `g`, picker confirm, CLI warning). Now merged-view;
  listing regression test.
- **P1**: doctor scope census and `transition.converge` couldn't resolve
  custom sessions → false problems + unrepairable sessions. Both threaded
  with `ordinary_docs`; regression tests for both.
- **P1**: the fetch-mark path missed the catalog-id shadow guard (a
  provider-listed `sol` would have overridden the catalog sol in the
  merged view). Guard now covers both paths; regression test.
- **should-fixes**: selection clamp after provider removal; E-jump hidden
  vs enabled mismatch on custom rows; browser title honesty; misleading
  success message when the key prompt is skipped.
- **P2/P3 batch**: render-with-customs coverage; discover names the next
  step (and accepts custom providers); `custom` CLI subcommands for
  line-mode parity; edit-in-place for custom providers; manual display
  names; context-bound never guessed (asked when the listing omits it);
  CustomModelsError in the CLI error boundary; USAGE/ORDINARY_HELP gaps.
- End-to-end funnel verification (fixture, no network): add provider →
  add model → merge boundary (composition resolve rejects the custom id)
  → prepare_direct → record save → doctor census clean → converge →
  render contains the alias → removal. All green on the final tree.

## Test/build evidence

- Full host discovery: `Ran 1582 tests … OK (skipped=2)`.
- Sandbox: `nix build --no-link --file home-manager/claude-multi/tests/default.nix`
  green for the 2.15.0 derivation.
- The documented RealPinnedBinaryTests load flake (AGENTS.md §4) appeared
  once under agent fan-out load; isolated re-run green.

## Commits (feature/term-only, nothing pushed)

- `318be9f` — 2.14.0 improvement pass (D51/019).
- `5fca996` — docs: 2.14.0 activated (gen 121).
- `7e280d5` — 2.15.0 custom providers/models + models browser (D52/020).
- `5a10af9` — docs: 2.15.0 activated (gen 122).
- This checkpoint: committed on top.

## Composition/registry census

14 user compositions (all resolve on catalog 16) + trusted `default`;
custom registry empty at checkpoint (flows verified in fixtures).
