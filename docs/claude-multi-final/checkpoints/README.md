# Checkpoints — point-in-time project states

Each subfolder is one named, immutable-ish checkpoint of the claude-multi
project: what was true, what was verified, and how to pick the project up
from there. Checkpoints are records, not branches — the living docs stay
canonical in their own places; a checkpoint links them at a recorded commit
and captures the evidence that made the state trustworthy.

## Convention

`checkpoints/<date>-<slug>/` contains exactly one `handoff/` folder with:

| File | Content |
| --- | --- |
| `handoff/README.md` | **Entry point.** What this state is, the recorded commit(s), how to verify it still holds, and the wiring to every important reference (living docs, other checkpoints, external state). |
| `handoff/state-snapshot.md` | Point-in-time evidence: doctor output, session census, package/store paths, generations, test/build evidence. |
| `handoff/open-items.md` | The next work, ordered, each item with its entry points and done-criteria. |

Rules:

- A checkpoint is written once and then only corrected for factual errors
  (it is a record); new work produces a NEW checkpoint.
- Link living docs by repo-relative path + commit; never copy their content
  into the checkpoint (copies rot).
- The entry point must let a fresh agent work with zero prior context:
  purpose, doctrine pointers, verification commands, open items.
- The current checkpoint is always the alphabetically-last subfolder; the
  design package README and `STATUS.md` link it explicitly.

## Checkpoints

| Checkpoint | State | Handoff |
| --- | --- | --- |
| `2026-08-19-v2.19.0` (**current**) | claude-multi 2.19.0/catalog19 (HM gen 129): DeepSeek V4 Pro GA + Grok 4.6 exact slug; Pro max and Grok high/xhigh streaming calls accepted; complete failed-call rollback drill proven; 35/36 scopes converged (one operator-owned retired-sol record) | [handoff/README.md](2026-08-19-v2.19.0/handoff/README.md) |
| `2026-08-18-v2.18.0` | claude-multi 2.18.0 (HM gen 125): sol joins the 1M class on the codex route (D57/023; acceptance probe green — 343,541 input tokens); retired-profile-aware doctor hint | [handoff/README.md](2026-08-18-v2.18.0/handoff/README.md) |
| `2026-08-12-v2.17.0` | claude-multi 2.17.0 (HM gen 124): DeepSeek + OpenRouter providers (flash 1M, grok45 500K grok profile), descriptor listing, D55 multi-route convention, D56 sol codex-route correction (372K→258.4K); probes green; doctor Ready | [handoff/README.md](2026-08-12-v2.17.0/handoff/README.md) |
| `2026-08-11-v2.16.0` | claude-multi 2.16.0 (HM gen 123): deep six-lane analysis batch landed — load-free corrupt forget, cwd-missing gate, under-lock forget liveness (CLI+picker), managed 1M reconciliation + hook drift survival, doctor 401 problem, redirectless fetches, registry FileLock/guards, unserved marking, cycle discard guard, pycache source filter (D53; blueprint 021); doctor Ready | [handoff/README.md](2026-08-11-v2.16.0/handoff/README.md) |
| `2026-08-11-v2.15.0` | claude-multi 2.15.0 (HM gen 122): the model/provider funnel complete — discover → mark in TUI → try ordinary → adopt into compositions; custom providers/models registry, models browser, doctor radar covers customs (D50–D52; blueprints 014–020); doctor Ready | [handoff/README.md](2026-08-11-v2.15.0/handoff/README.md) |
| `2026-08-10-v2.13.0` | claude-multi 2.13.0 (HM gen 120): qwen3.8-max production live-verified, MRU picking, `--composition-file`, providers pane, doctor gateway radar (D50; blueprints 014–018); doctor Ready | [handoff/README.md](2026-08-10-v2.13.0/handoff/README.md) |
| `2026-07-27-v2.7.0` | claude-multi 2.7.0 (HM gen 107): lifecycle complete (sessions stop, D38) on top of the v2.6 hardening; pin 2.1.220; doctor Ready, zero Attention | [handoff/README.md](2026-07-27-v2.7.0/handoff/README.md) |
| `2026-07-24-v2.5.0` | claude-multi 2.5.0 (HM gen 101), Opus 5 default lead, gateway serves Opus 5, doctor fully clean | [handoff/README.md](2026-07-24-v2.5.0/handoff/README.md) |
| `2026-07-24-v2.4.1` | claude-multi 2.4.1 (HM gen 99), layered contract, TUI health surface | [handoff/README.md](2026-07-24-v2.4.1/handoff/README.md) |
| `2026-07-24-v2.3.0` | claude-multi 2.3.0 (HM gen 97), hook shim, doctor Ready, hygiene done | [handoff/README.md](2026-07-24-v2.3.0/handoff/README.md) |
