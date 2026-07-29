# Issue log — claude-multi

**One folder per issue** (`NNN-short-slug/`), each with a `README.md`
holding the complete picture: report → root cause → investigation → fix →
verification → activation, plus any artifacts (evidence dumps, workflow
outputs, notes) the issue accumulated.

Purpose: never lose track of reported problems. Distinct from
`checkpoints/*/handoff/open-items.md` (planned future work) and
`DECISIONS.md` (accepted design rulings) — this log is the *incident
pipeline*.

Status values: `open` (reported, not yet root-caused) · `investigating` ·
`fixing` · `in release` (committed, awaiting activation) · `resolved`
(activated and verified live) · `wontfix` (reason recorded) · `upstream`
(not ours; documented behavior or out of bounds).

| # | Issue | Status | Release |
|---|-------|--------|---------|
| 001 | [EnterWorktree refusal on cross-worktree review](001-enterworktree-cross-review-refusal/) | resolved | 2.7.2 (gen 109) |
| 002 | [Unactionable repair-needed guidance + relink semantics](002-relink-guidance-unactionable/) | resolved | 2.8.0 (D41, gen 110) |
| 003 | [Resume blockers: daemon-owned + no-conversation + TUI gate](003-resume-blockers-gate/) | resolved | 2.8.0 (D41, gen 110) |
| 004 | [API Error "An error occurred while processing"](004-api-error-processing/) | resolved | 2.8.0–2.8.2 (D42/D43) |
| 005 | [Worktree-isolated dispatch unavailable outside git repos](005-worktree-dispatch-nonrepo-cwd/) | open (mitigation shipped 2.8.0) | — |
| 006 | [5ee2f942 transcript genuinely missing](006-5ee2f942-transcript-missing/) | open | — |
| 007 | [Subagent "Prompt is too long" (context exhaustion)](007-subagent-context-exhaustion/) | open (recovery shipped 2.8.0) | — |
| 008 | [observed_model bleed: compact event attributes a subagent's model](008-observed-model-subagent-bleed/) | resolved | 2.8.3 (D44) |
