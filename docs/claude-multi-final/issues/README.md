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
| 009 | [/model picker shows only a subset of availableModels](009-model-picker-display-filter/) | resolved | 2.11.0 (D47, docs) |
| 010 | [Subagent TaskStop flailing: ownership refusals](010-subagent-taskstop-flailing/) | resolved | 2.11.0 (D47) |
| 011 | [Subagents denied spawning native types](011-native-type-denials/) | resolved | 2.11.0 (D47) |
| 012 | [Sessions picker defaults to this directory](012-sessions-cwd-filter-default/) | resolved | 2.11.0 (D47) |
| 013 | [Session --name distinguishes project](013-generic-session-names/) | resolved | 2.11.0 (D47) |
| 026 | [Non-Claude routes leak `prompt_cache_retention` (HTTP 400)](026-prompt-cache-retention/) | resolved | 2.20.0/catalog20, activated gen 130 (D60) |
| 027 | [GLM-5.3 is not available on Alibaba Token Plan](027-glm-53-token-plan-unavailable/) | blocked upstream | active route remains GLM-5.2 (D61) |
