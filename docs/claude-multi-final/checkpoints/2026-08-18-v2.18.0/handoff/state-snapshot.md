# State snapshot — 2026-08-18 · v2.18.0

Evidence behind the checkpoint README claims. Verify before trusting.

## Activation

- `home-manager switch --flake /home/kotur/personal/nixos-dotfiles#kotur`
  → exit 0; HM generation **125** current (124/123 rollback);
  `cli-proxy-api.service` restarted. `claude-multi --version` → 2.18.0.
- `doctor --repair-all`: 33 durable sessions converged onto catalog 18;
  one expected FAILED — `d928f2a2` (ordinary sol session, retired 'sol'
  profile) which repair cannot converge by design; doctor prints the
  exact re-pin resume command (the 023 review fix).
- `claude-multi doctor` → all checks pass except that one note.

## Acceptance probe (approval-gated, 2026-08-18)

- POST through the live gateway (`gpt-multi-sol-high` alias) with a
  ~1.79MB payload: **HTTP 200, usage.input_tokens = 343,541** — decisively
  past the old 258,400 ceiling; the 1M budget is live for the
  claude-multi client path. (First attempt at 1.29MB / 248,311 tokens was
  under the ceiling — sizing notes: ~5.2 chars/token for numbered prose;
  a repeat request reported 75,171 due to prefix-cache accounting, so the
  final probe used a cache-defeating prefix.)
- `validated_tokens` = 343541; near-limit (1M) behavior unverified —
  watch for prompt-too-long recurrence in real sessions before probing
  nearer the bound.

## Verification sweep (glm52 + qwen38, adversarial verify)

Confirmed and fixed: sol qualification names both trigger paths (882,000
composition / 867,254 ordinary); doctor's scope-integrity hint is
retired-profile-aware (a pre-upgrade 'sol' record gets the re-pin resume,
not an erroring --repair); a swept HANDOFF sentence repaired; trailing
newline restored. Refuted: the gpt55 qualification divergence nit (D57
itself carries the divergence note).

## Test/build evidence

- Full host discovery: `Ran 1664 tests … OK (skipped=2)` on the committed
  tree.
- Sandbox: `nix build --no-link --file tests/default.nix` green (2.18.0).
- Goldens re-blessed and reviewed: exactly the `[1m]` selectors, the
  scalar-unset env diff, and the appendix process-scalar line removal.

## Commits (feature/term-only, nothing pushed)

- `1ff5a1e`/`c08dd77`/`2f49567`/`9e72fbc` — 2.17.0 + activation (gen 124).
- `52b6219` — 2.18.0: sol joins the 1M class (D57/023).
- This checkpoint: committed on top.

## Composition/registry census

16 user compositions + trusted `default`; custom registry empty; MRU head
at activation: `kimi-sol-qwen-glm`.
