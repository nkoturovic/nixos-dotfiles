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
| `2026-07-24-v2.4.0` (**current**) | claude-multi 2.4.0 activated (HM gen 98), doctor fully clean, binary re-pinned to 2.1.218, one-command update loop | [handoff/README.md](2026-07-24-v2.4.0/handoff/README.md) |
| `2026-07-24-v2.3.0` | claude-multi 2.3.0 activated (HM gen 97), doctor Ready, hygiene done, docs complete | [handoff/README.md](2026-07-24-v2.3.0/handoff/README.md) |
