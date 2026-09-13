# Checkpoint 2026-09-13 — session handoff (D63→D66, WS4, DeepSeek V4.1-Flash, two open issues)

**For:** the next agent picking this up cold. You have no prior context; read
this file, then `state-snapshot.md` (where everything is), then
`open-items.md` (what to do next, with done-criteria).

**Recorded commit:** `7525b31` on `feature/term-only`, **pushed** (0 commits
ahead of `ssh/feature/term-only`).
**Live:** launcher **2.24.0 / catalog 26**, Home Manager **gen 139**,
`claude-multi doctor` → **Ready** (one by-design Attention, see below).

---

## 1. The 60-second orientation

`claude-multi` is a stdlib-only Python launcher that compiles a *composition*
(a lead model + generated `cm-*` agent roster) into a per-session **durable
scope**, then execs a hash-verified Claude Code binary pointed at it. Every
model call routes through a **local CLIProxyAPI gateway** on
`127.0.0.1:8317` (loopback-only), which proxies to Anthropic (OAuth), OpenAI
Codex (OAuth), Kimi, Qwen/GLM, DeepSeek, OpenRouter, Meta, and one keyless
local LAN route.

- **Source:** `/home/kotur/personal/nixos-dotfiles/home-manager/claude-multi/`
- **Design docs / ledgers:** `/home/kotur/personal/nixos-dotfiles/docs/claude-multi-final/`
- **Dev guide (read it fully before changing anything):**
  `home-manager/claude-multi/AGENTS.md`

**State model (D3, the load-bearing doctrine):** *record = intent; catalog =
trusted source; scope = pure function of (record, installed catalog).*
Scopes are always re-derivable; repair recompiles and displays drift. Never
treat scope content as authority. A **moving upstream alias is hidden state
and is not a trusted catalog wire** — with one documented, accepted exception
for DeepSeek (see `open-items.md` §7).

## 2. Verify it still holds

```bash
cd /home/kotur/personal/nixos-dotfiles/home-manager/claude-multi
git log --oneline -1        # expect 7525b31
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .
nix-build --no-out-link package.nix
nix build --no-link --file tests/default.nix
git diff --check
claude-multi doctor          # expect: Ready
```

**Expected result:** **1,716 tests**, exactly **2 failures** — both
pre-existing and environmental, not caused by any change here (a real
`claude` 2.1.261 binary sits on disk while the trust anchor is 2.1.220, which
trips the doctor-`Ready` assertion; and the update-override test's
tmp-checkout detection fails under the test's isolated `HOME`). Both are
proven failing on clean HEAD. Details and exact test ids in
`state-snapshot.md` §7.

## 3. Rules of engagement (non-negotiable — `AGENTS.md` §6, verbatim)

1. **"No real-provider calls without explicit user approval, per call."**
2. **"Never touch the live Claude daemon/supervisor; never read user
   transcripts; never delete anything under `~/.claude` or a
   transcript-bearing root."** Rollback never deletes state.
3. **"Tests before claims."** Full discovery green + package build + sandbox
   suite before declaring done.
4. **"Commit discipline:"** coherent commits on `feature/term-only`; no push
   without approval. Home Manager activation needs user approval — it
   restarts the gateway.
5. **"Review cadence:"** self-verify milestones; one batched **cross-family**
   review at meaningful boundaries; no review loops.
6. **"Doctor is the truth surface:"** real damage must BLOCK; by-design lazy
   state is Attention with the exact fix command.
7. **"Keep the map current (replicability rule):"** update `STATUS.md` at
   every milestone before claiming "done"; update `AGENTS.md`/`USAGE.md` with
   any behavior change; create the next checkpoint at every meaningful
   boundary; **one canonical home per topic — pointers elsewhere, never
   copies.**

Also non-negotiable: **secrets by name/count/length only, never values**;
`transcript_path` is never stored or read; the compiled-settings key set is a
**closed allowlist** (`COMPILED_SETTINGS_KEYS`) — nothing new without a
demonstrated failure case.

## 4. Documentation map (one canonical home per topic)

| Topic | Canonical home |
|---|---|
| Live ledger (what happened when) | `docs/claude-multi-final/STATUS.md` |
| Decisions + rejected alternatives | `docs/claude-multi-final/DECISIONS.md` |
| Incident pipeline | `docs/claude-multi-final/issues/` (one folder per issue) |
| Forward-looking design+plan | `docs/claude-multi-final/blueprints/` |
| Current state + next work | `docs/claude-multi-final/checkpoints/` → latest `handoff/` |
| Development guide | `home-manager/claude-multi/AGENTS.md` |
| User guide / standalone guide | `USAGE.md` / `STANDALONE.md` |
| Daily operations | `docs/claude-multi-final/HANDOFF.md` + `USAGE.md` |
| Design specs | `BLUEPRINT` / `SPEC` / `TRANSITIONS` / `UX` / `VERIFICATION` / `MIGRATION-ROLLBACK` |

**Checkpoint convention** (`checkpoints/README.md`): `<date>-<slug>/` holds
exactly one `handoff/` with **three** files — `README.md` (entry point),
`state-snapshot.md` (point-in-time evidence), `open-items.md` (next work).
Written once, then corrected only for factual errors; new work produces a NEW
checkpoint; link living docs by path+commit, never copy their content. The
current checkpoint is always the alphabetically-last subfolder — **this one**.

## 5. What this session did (one line each)

- **D63** — capped every 1M-class model's *operating* window at **800,000**
  tokens (trigger 702,000) via one central clamp. Live at gen 135.
- **D64 (WS4)** — single-model mode for **any** catalog model, the
  `--no-subagents` flag, and a keyless OpenAI-compatible adapter +
  `llm-local`/`qwen-flash-next`. Live at gen 136.
- **D65** — **DeepSeek V4.1-Flash**: migrated the flash wire to the canonical
  `deepseek-flash`, and recorded that `deepseek-v4-pro` **reroutes to
  V4.1-Flash from 2026-09-14 04:00 UTC**. Live at gen 138.
- **catalog26** — dropped `deepseek-flash` `validated_tokens` 1M → 200,000
  (a docs claim is not a measurement). Live at gen 139.
- **D66** — audit: the 800K ceiling covers **every** launch path; output
  tokens are **delegated to the client** (effective 32,000) and need no
  change; effort lanes verified. Source + docs only.
- **Two new issues:** 028 (Meta strict tool schema) and 029 (the 2.1.261
  re-pin blocked by an upstream trust-dialog change).
- **Notable finding for our architecture:** `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`
  is new in 2.1.261 and is a second roster-flattening vector that nothing in
  the launcher currently models — see `open-items.md` §2, it is the
  highest-value defensive fix available.

## 6. Known staleness in the docs (not fixed — flagged for the next agent)

These are real, verified inconsistencies. They are cosmetic-to-moderate; fix
them opportunistically if you touch the same files.

1. `checkpoints/README.md` index marks `2026-08-21-v2.20.0` as "current" and
   omits the three newest checkpoints. The folder set is authoritative.
2. `docs/claude-multi-final/README.md` opens "Status: 2.7.0 activated
   (2026-07-27)" — long superseded by `STATUS.md`.
3. `issues/README.md` still labels issue 029 "diagnosed (ready to implement)";
   the issue folder (newer) says "investigating — two attempts failed".
4. `claude-multi.nix` lines 1–6 say "the four local gateway patches"; there
   are **five** (the astra-registry patch is missing from the comment).
5. `DECISIONS.md` headers for D63/D64 say "source-complete, not activated";
   `STATUS.md` records both as activated. **STATUS is the live truth.**
6. `HANDOFF.md` still describes the pre-D65 DeepSeek wire convention.
7. The `flash431` profile window (320,032) is **emergent** from
   `qwen-flash-next`'s `provider_tokens` under the 800K clamp — there is no
   per-profile numeric table anywhere.

## 7. Wiring

- **This checkpoint records** commit `7525b31`; live gen **139**.
- **Prior checkpoints:** `2026-09-10-deepseek-v41/` (D65/D66),
  `2026-09-06-v2.24.0/` (WS4), `2026-09-06-v2.23.0/` (D63), `2026-08-21-v2.20.0/`.
- **The one-off model handoff** from the previous batch is
  `docs/claude-multi-final/HANDOFF-ASTRA-BATCH.md` (historical, not a
  canonical home).
- **Rollback anchors:** HM gen 139 is current; gen 138/137/136/135 are the
  per-release anchors. `home-manager generations` lists them.
