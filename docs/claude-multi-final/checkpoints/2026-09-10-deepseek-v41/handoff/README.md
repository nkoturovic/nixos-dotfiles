# Checkpoint 2026-09-10 — DeepSeek V4.1-Flash, output-token policy, two new issues

**Recorded commit:** `a44e2a2` (branch `feature/term-only`, pushed).
Live: launcher **2.24.0 / catalog 26**, Home Manager **gen 139**, doctor Ready.

## What this state is

The session's work: the **DeepSeek V4.1-Flash** release handled (canonical
wire migration + the Pro wire's scheduled reroute), an operator-requested
**full audit** of the 800K context boundary and the output-token/thinking
configuration, and two new issue records. All committed, pushed, activated
and verified live.

## Why (one line each)

- **D65** — V4.1-Flash (2026-09-10) made `deepseek-flash` the canonical
  callable id; `deepseek-v4-flash` is now only a redirect (no stated
  sunset), and `deepseek-v4-pro` reroutes to V4.1-Flash from **2026-09-14
  04:00 UTC** until V4.1-Pro ships. Nothing needed to *route* differently
  (the deepseek route is registry-free and aliases derive from
  `client_selector`) — the fix was **truthfulness**.
- **D66** — the 800K boundary was audited and found to cover **every**
  launch path (no bypass); output tokens are **delegated to the client** by
  design (effective value 32,000, verified in the pinned binary) and no
  change is justified without a demonstrated failure; the `high`/`max`
  effort lanes were confirmed correct against DeepSeek's documented ladder.
- **Issue 028** — Meta rejects strict-incomplete tool schemas (goal
  evaluator); probe-gated.
- **Issue 029** — the 2.1.261 re-pin is blocked; **the keystroke was ruled
  out** as the cause (nine variants), so the next step is a transcript
  diff, not an interaction-table edit. Pin stays 2.1.220.

## How to verify it still holds

```bash
cd /home/kotur/personal/nixos-dotfiles/home-manager/claude-multi
git log --oneline -1        # expect a44e2a2
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
claude-multi doctor          # expect Ready (plus the 2.1.261 Attention)
```

Expected: 1,716 tests, only the 2 known pre-existing environmental failures
(real 2.1.261 binary trips doctor-Ready; tmp-checkout detection trips the
override test — both proven on clean HEAD).

## Wiring

- Live ledger: `docs/claude-multi-final/STATUS.md` (top entries).
- Rationale: `DECISIONS.md` → **D65**, **D66** (and D63 for the ceiling).
- Issues: `issues/028-meta-strict-tool-schema/`,
  `issues/029-repin-trust-dialog-default-flip/`.
- Prior checkpoint: `checkpoints/2026-09-06-v2.24.0/`.
- Detail: `handoff/state-snapshot.md`; next work: `handoff/open-items.md`.
