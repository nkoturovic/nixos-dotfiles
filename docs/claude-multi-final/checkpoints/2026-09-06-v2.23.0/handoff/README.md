# Checkpoint 2026-09-06 — v2.23.0: D63 800K operating ceiling (source-complete, NOT activated)

**Recorded commit:** `17b4e39` (branch `feature/term-only`, unpushed).
Prior: `d03744a` (Astra handoff) ← `84cdd83` (catalog22 Astra lead-capable).

## What this state is

Launcher **2.23.0 / catalog 22** in source. Every 1M-class model (astra,
deepseek-flash/pro, fable, glm52, kimi-k3, muse-spark/-contributor, opus,
opus5, qwen38, sol) operates at an **800,000-token window → 702,000
reactive trigger** via one central clamp; grok46 (500K) and gpt55 (258400)
untouched. Catalog evidence, `[1m]` selectors, and rendered gateway YAML are
byte-identical to 2.22.0. **Live system is still 2.22.0/catalog22 at HM gen
134** — activation is a separate operator gate.

## Why (one line each)

- D62 (backfill): Astra + Meta muse-spark batch, activated gen 134; Meta
  route never live-probed. Full narrative:
  `docs/claude-multi-final/HANDOFF-ASTRA-BATCH.md`.
- D63: operator set 800K as the default 1M-class window (Astra's route is
  acceptance-verified only). Central clamp chosen over per-model catalog
  edits (would falsify Kimi's attestation) and over ~900K/922K (OAuth-route
  equivalence unverified). Full rationale: D63 in `DECISIONS.md`.

## How to verify it still holds

```bash
cd /home/kotur/personal/nixos-dotfiles/home-manager/claude-multi
git log --oneline -1  # expect 17b4e39
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
nix-build --no-out-link package.nix
nix build --no-link --file tests/default.nix
git diff --check
```

Expected: 1,687 tests, only 2 pre-existing environmental failures
(doctor-Ready assertion trips on the real 2.1.261 binary on disk;
override test trips on tmp-checkout detection — both proven failing on
clean HEAD). Live `claude-multi doctor` should read Ready at gen 134 until
activation.

## Wiring

- Live ledger: `docs/claude-multi-final/STATUS.md` (top entry).
- Rationale: `docs/claude-multi-final/DECISIONS.md` (D62, D63).
- Plan file (WS4 spec + superseded 800K/900K deliberation):
  `/home/kotur/.claude/plans/iridescent-sauteeing-lampson.md`.
- Doctrine: `home-manager/claude-multi/AGENTS.md`; design package README in
  `docs/claude-multi-final/README.md`.
- Prior checkpoint: `checkpoints/2026-08-21-v2.20.0/`.
- Detail: `handoff/state-snapshot.md`; next work: `handoff/open-items.md`.
