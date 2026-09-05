# State snapshot — 2026-09-06, commit 17b4e39 (source; live lags one release)

## Source state (verified this session)

- `home-manager/claude-multi/version.json`: launcher **2.23.0**, catalog **22**.
- Full Python suite: **1,687 tests, 2 failures, 2 skipped** — both failures
  pre-existing/environmental, reproduced on clean HEAD via `git stash`:
  1. `ImprovementBatchTests.test_line_mode_h_prints_doctor` — the sandbox
     sees the real `/home/kotur/.local/bin/claude` 2.1.261 while the pin is
     2.1.220, so doctor reports Attention instead of Ready.
  2. `BrokenOverrideDegradationTests.test_update_removes_the_broken_override`
     — tmp-checkout source detection fails under the test TMPDIR layout.
- `nix-build --no-out-link package.nix`: green, store path
  `...-claude-multi-2.23.0`.
- `nix build --no-link --file tests/default.nix`: green.
- `git diff --check`: clean.
- Goldens re-blessed, delta reviewed line-by-line: `env.json` (window
  1000000→800000 only), `lead-appendix.md` (capacity/trigger + one D63
  ceiling line), four argv goldens (lead-prompt digest only, verified
  byte-identical with digest masked). No render/gateway golden touched.
- Same-family review (meta/meta, reduced independence — no cross-family
  reviewer enabled): **APPROVE**, no must-fix findings.
- Working tree after commit: only pre-existing unrelated
  `home-manager/kotur.dotfiles/profile` modification remains.

## Last recorded live state (from HANDOFF-ASTRA-BATCH.md, not re-probed)

- HM generation **134**, launcher 2.22.0/catalog22, doctor Ready.
- 37 durable sessions converged via `doctor --repair-all`.
- Compositions `astra`, `astra-muse`, `astra-qwen-muse` present (0600).
- Rollback anchor for D63 activation: **gen 134**.

## Deltas that activation will apply (pending green light)

- Launcher 2.22.0 → 2.23.0 (catalog unchanged at 22).
- Fresh/resumed/repaired 1M-class sessions get window 800000 / trigger
  702000; already-running processes keep their environment until relaunched.
- No gateway config change (rendered YAML byte-identical); the
  `home-manager switch` still restarts the gateway per standing procedure.
