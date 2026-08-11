# State snapshot — 2026-08-11 · v2.16.0

Evidence behind the checkpoint README claims. Verify before trusting.

## Activation

- `home-manager switch --flake /home/kotur/personal/nixos-dotfiles#kotur`
  → exit 0; HM generation **123** current (122/121 rollback);
  `cli-proxy-api.service` restarted by the activation.
  `claude-multi --version` → `claude-multi 2.16.0`.
- `claude-multi doctor --repair-all` → **33 durable sessions converged to
  record authority**; re-run `claude-multi doctor` → **Ready** —
  catalog/compositions/gateway valid, collisions none, gateway radar
  silent (no 401/drift/unserved problems).
- Live smoke: `claude-multi custom list` answers (empty registry);
  `compose list` MRU-first with ages.

## The 021 review story (why the under-lock guard exists)

Two cross-family reviewers on the uncommitted diff:

- **sol-xhigh → block (3 findings, all reproduced, all fixed + pinned)**:
  (1) must-fix — the forget liveness verdict was taken before blocking on
  the lifecycle lock, so it was stale by deletion time (reproduced with a
  lock-transition probe), and corrupt records bypassed daemon liveness
  entirely. Fix: `forget_session(pre_delete_check=…)` runs the shared
  `_forget_liveness_guard` under the lock with a fresh prefix scan; the
  picker `_forget` passes it too (its earlier modal-only path was the
  glm52 P2), and corrupt records get the stable-id prefix check.
  (2) should-fix — the pointer sweep used a bare `unlink()`; now
  `state.remove_private` (the record delete fsyncs a different
  directory). (3) should-fix — the cwd-missing remedy showed a backticked
  `mkdir -p ~/.claude/projects/<new-slug>` the operator could not
  reliably compute (UTF-16-unit sanitize + truncation + signed hash over
  200 chars). The gate now offers rename-back or relink-then-resume, and
  the follow-up elsewhere gate names the exact expected transcript
  location.
- **glm52 → approve** (2 P2s + 3 nits): picker-forget bypass (fixed with
  the shared guard, live rows get stop-first E guidance); corrupt bypass
  (closed by the same under-lock check); nits fixed — `add_model`'s
  `catalog_providers` is dict-typed (the OAuth guard indexes), listing
  `HTTPError` sockets closed. Runtime-id corrupt forget stays a
  re-raise (the error names the managed id; watch item).
- Verified clean by both: gate ordering, `_NoRedirect` truly blocking
  (single redirect handler in the opener), `_mutate` lock shape (sidecar
  `.lock`, no reentrancy), merge drop loudness, unserved marking
  exclusivity, hook drift guard, sweep TOCTOU, bounded stdin,
  CommittedStateError handler ordering, package.nix filter coverage, dead
  symbols, and all new pins enshrining correct behavior.

## Test/build evidence

- Full host discovery: `Ran 1631 tests … OK (skipped=2)` on the committed
  tree (49 new pins across test_cli/test_proxy/test_sessions).
- Sandbox: `nix-build --no-out-link package.nix` green →
  `/nix/store/bfv14802xpgkg2vnq4ccy5r7dh8qav63-claude-multi-2.16.0`;
  zero `.pyc`/`__pycache__` in the output (cleanSourceWith filter).
- The documented pty timing flake (AGENTS.md §4) appeared once in two
  different single-test forms during loaded runs; isolated re-runs green.

## Commits (feature/term-only, nothing pushed)

- `7e280d5` — 2.15.0 custom providers/models + models browser (D52/020).
- `5a10af9` — docs: 2.15.0 activated (gen 122).
- `734c285` — docs: checkpoint 2026-08-11-v2.15.0.
- `216c434` — 2.16.0 deep analysis hardening (D53/021, incl. review fixes).
- This checkpoint: committed on top.

## Composition/registry census

14 user compositions (all resolve on catalog 16) + trusted `default`;
custom registry empty at checkpoint (flows verified in fixtures).
MRU head at activation: `kimi-sol-qwen-glm` (2h), `kimi-sol` (6d).
