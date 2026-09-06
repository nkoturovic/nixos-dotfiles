# State snapshot — 2026-09-06, commit 8d520f8 (source; live lags one release)

## Source state (verified this session)

- `home-manager/claude-multi/version.json`: launcher **2.24.0**, catalog **23**.
- Full Python suite: **1,710 tests, 2 failures, 2 skipped** — both failures
  pre-existing/environmental (unchanged from the D63 baseline).
- `nix-build --no-out-link package.nix`: green (2.24.0 store path).
- `nix build --no-link --file tests/default.nix`: green.
- `git diff --check`: clean.
- Goldens: render golden gains the `openai-compatibility` section (exact
  pinned-shape bytes, generated from the candidate tree and reviewed);
  scope/compiler goldens byte-identical (bless wrote no changes).
- Dev pipeline for `llm-local`: draft → check green → review recorded
  (`~/.local/state/claude-multi/drafts/llm-local{,.review}.json`) →
  promoted (canonical post-images only).
- Same-family review (meta/meta, reduced independence): **APPROVE-WITH-NITS**,
  all 3 findings fixed (single-model reconcile guard + test; switch
  asymmetry documented deliberate; doctor null-profile message).
- Working tree after commit: only pre-existing unrelated
  `home-manager/kotur.dotfiles/profile` modification remains.

## Last recorded live state

- HM generation **135**, launcher 2.23.0/catalog22, doctor Ready.
- 38 sessions recorded/durable. Rollback anchor for WS4 activation: **gen 135**.

## Deltas that activation will apply (pending green light)

- Launcher 2.23.0 → 2.24.0, catalog 22 → 23 (llm-local provider +
  qwen-flash-next model + qualification prose).
- Rendered gateway config gains the `openai-compatibility` section with
  the llm-local entry (currently unrouteable until the alias gate clears).
- G picker gains `flash431` + `single` sections; floor boundaries move
  (exact pins in tests).
- Presets `muse-direct`, `muse-contributor-direct`, `qwen-local-direct`
  to be created via CompositionStore at activation (0600), then validated.
- `home-manager switch` restarts the gateway per standing procedure.
