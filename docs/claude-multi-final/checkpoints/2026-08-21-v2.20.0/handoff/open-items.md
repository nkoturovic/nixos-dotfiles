# Open items — 2026-08-21 · v2.20.0/catalog20

## Operationally complete

The cache-retention incident is resolved and active. No user action or special
model knowledge is required: route classification and sanitization are automatic.

## Watch only

- Watch organic gateway journals for recurrence of
  `prompt_cache_retention is not supported on this model`. Do not induce a
  provider request solely for this check; the loopback/Nix executor matrix is
  the primary proof.
- On any future CLIProxyAPI baseline or earlier-patch change, require exact
  ordered patch application plus the sandbox executor gate before activation.
- The exact ordinary Codex function that leaked live remains unproven. Reopen
  only if the final-boundary invariant is bypassed in a reproducible local
  request shape.
- Two upstream executor tests are flaky only under repeated whole-package
  runs (WebSocket disconnect timing and Antigravity KV counter state); both
  reproduce on unpatched 7.2.80 and are unrelated to this release.

## Existing world-triggered items

- Near-limit context probes remain opt-in and require explicit per-call
  approval; this release changes no context/model/composition contract.
- `~x-ai/grok-latest` remains deliberately untrusted under D3.
- U1 takeover and older issue watches remain as documented in prior
  checkpoints; no new blocker was introduced.

## Rollback

Generation 129 is the full pre-fix rollback. Activating it and restarting the
gateway restores 2.19.0/catalog19. Never delete or rewrite sessions, scopes,
credentials, or transcripts as part of rollback.
