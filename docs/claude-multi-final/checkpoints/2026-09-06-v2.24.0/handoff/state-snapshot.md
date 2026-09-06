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

## Activation 2026-09-06 (gen 136)

- `home-manager switch` applied; gateway restarted; healthz `{"status":"ok"}`.
- `claude-multi doctor`: **Ready** (only the by-design 2.1.261 Attention);
  38 sessions recorded/durable, no collisions.
- 44 selectors served, incl. `claude-multi-qwen-flash-next` and the Astra +
  gpt55 lanes (route presence only — qwen-local still unrouteable until
  the alias gate clears).
- Presets `muse-direct`, `muse-contributor-direct`, `qwen-local-direct`
  created via CompositionStore (0600) and resolve-verified. Rollback
  anchor stays gen 135.
