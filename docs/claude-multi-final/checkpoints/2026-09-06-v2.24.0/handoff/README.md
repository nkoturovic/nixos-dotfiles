# Checkpoint 2026-09-06 — v2.24.0/catalog23: WS4 source-complete, NOT activated

**Recorded commit:** `8d520f8` (branch `feature/term-only`, unpushed).
Prior: `4859ab6` (WS4a+4b) ← `e0a81b6` (issue 028).

## What this state is

Launcher **2.24.0 / catalog 23** in source. WS4 delivered:
single-model mode for any catalog model (null `context_profile`, own-model
fence, G picker `single` section, class-confined switches);
`--no-subagents` (recorded policy, scope deny + env belt);
keyless OpenAI-compat adapter + `llm-local`/`qwen-flash-next` (fence 320032,
`flash431`); lead-only composition support. **Live system is still
2.23.0/catalog22 at HM gen 135** — activation is a separate operator gate.

## Why (one line each)

- Single-model: any model (incl. agents-only gpt55) runs fenced to itself
  (gpt55: 258400/258400/trigger 214560); profiled models byte-identical.
- `--no-subagents`: tri-state flag, re-applied on resume, mismatch rejected.
- Local Qwen: dev-pipeline promoted; LAN listing confirmed 431104/text-only
  but found NO served alias (GGUF-path id) — wire needs server `--alias`
  or a wire change; acceptance/near-limit probes approval-gated.
- Full rationale: D64 in `DECISIONS.md`.

## How to verify it still holds

```bash
cd /home/kotur/personal/nixos-dotfiles/home-manager/claude-multi
git log --oneline -1  # expect 8d520f8
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
nix-build --no-out-link package.nix
nix build --no-link --file tests/default.nix
git diff --check
```

Expected: 1,710 tests, only 2 pre-existing environmental failures (real
2.1.261 binary trips doctor-Ready; tmp-checkout detection trips override
test — both proven on clean HEAD).

## Wiring

- Live ledger: `docs/claude-multi-final/STATUS.md` (top entry).
- Rationale: `docs/claude-multi-final/DECISIONS.md` (D64).
- Issue 028 (Meta strict-schema 400): `issues/028-meta-strict-tool-schema/`.
- Plan file: `/home/kotur/.claude/plans/iridescent-sauteeing-lampson.md`.
- Prior checkpoint: `checkpoints/2026-09-06-v2.23.0/`.
- Detail: `handoff/state-snapshot.md`; next work: `handoff/open-items.md`.
