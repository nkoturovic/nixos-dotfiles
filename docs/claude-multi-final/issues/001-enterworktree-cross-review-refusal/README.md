# 001 — EnterWorktree refusal on cross-worktree review

**Status: resolved** · fixed in 2.7.2 (D40) · activated HM gen 109 (2026-07-29)

## Report (2026-07-28, operator)

A reviewer subagent dispatched to review another leg's worktree-isolated
work failed with:

```
Error: Cannot enter worktree: the current working directory
/home/kotur/projects/occams-agent-flow is the repository root, not an
isolated worktree — switching is only available to sessions whose working
directory is inside a worktree of this repository.
```

## Root cause

Shape mismatch, not a claude-multi bug: `roles.json` gives cm-implementer
`isolation: "worktree"` while reviewers/analysts run at the repository
root. A read-mostly agent reaching for the native `EnterWorktree` tool to
"enter" the implementer's worktree hits the pinned binary's refusal of
root→worktree switching (2.1.220; newer harness docs describe looser
semantics — the tool's behavior drifts between versions, so neither may
be relied upon).

## Fix (D40)

Prompt-level contract in `catalog/prompts/` — no machinery:
- a worktree is a plain directory: read-mostly agents inspect from the
  outside (direct reads, `git -C <path> status|diff|log`, subshell
  `(cd <path> && <cmd>)`) and never call EnterWorktree;
- implementers close reports with worktree path + branch + base ref +
  committed-state, and leave the worktree in place;
- the lead passes coordinates at dispatch and integrates from its own
  root: committed work via the branch, uncommitted via
  `git -C <path> diff HEAD --binary | git apply -` after a
  `status --short` check (tracked-only; untracked copied by path or
  committed first);
- keyword pins in `tests/test_roles.py` lock every guarantee; scope
  goldens re-blessed.

Rejected: tool denies (over-reach), reviewer worktree isolation
(wasteful, hop semantics), native-binary workarounds (out of bounds).

En passant: `tests/default.nix` never staged the gateway patch files, so
the manifest file-existence test could never pass in the sandbox
(pre-existing); it now stages the repo-shaped layout from the manifest.

## Verification

1,339 tests OK; sandbox derivation green (builds 2.7.2); cross-family Sol
review in 3 rounds (caught an integration-ownership overstatement, the
uncommitted-work gap, the untracked-file gap — fixture-proven — and a
heal-semantics doc error) → approve; live converged scope shows 6/6
roster files carrying the guidance.

## References

DECISIONS.md D40 · commit `5c6cbd2` (+ activation docs `856dfdb`) ·
catalog_version 8.
