# Mandatory source material

Read every Markdown file in:

```text
/home/kotur/.claude/.agents/wiki/raw/docs/
```

## Official Claude documentation snapshots

- `claude-agents-general.md`
- `claude-sub-agents.md`
- `claude-agent-view.md`
- `claude-agent-teams.md`
- `claude-dynamic-workflows.md`
- `claude-isolate-with-worktrees.md`

Inspect frontmatter/source URLs and cite local lines. Build one behavior map for:

- discovery, scope precedence, watcher/reload and first-directory behavior;
- invocation, model/effort/tool/permission precedence and resume;
- nesting, background tasks, budgets and supervisor replacement;
- agent view, config-root supervisor and gateway/auth environment;
- workflow effort, permissions, model routing, artifacts and limits;
- worktree base, uncommitted state, cleanup and path transitions;
- teams and whether they belong in the product.

## Anthropic orchestration guidance

- `claude-orchestration-patterns.md`

This is an Anthropic blog/guidance document, not a Claude Code API contract.
Use it for architecture heuristics:

- start with the simplest pattern that can work;
- orchestrator–subagent handles most cases with least coordination overhead;
- generator–verifier needs explicit criteria and a termination/fallback rule;
- teams fit long-running independent partitions, not coupled edits;
- message bus/shared state add tracing, conflict and termination complexity.

## Community/background material

Treat these as leads, not contracts:

- `blog-run-gpt-5.6-in-claude-code.md`
- `reddit-claude-code-gpt-5.6.md`
- `vladislav-baidin-gpt-5.6-in-claude-code.md`

Corroborate useful observations against official docs, code or safe tests.

## Other local knowledge

- `/home/kotur/.agents/wiki/projects/claude.md`
- `/home/kotur/.agents/wiki/log.md`
- `/home/kotur/.claude/.agents/wiki/index.md`
- repository docs and `home-manager/claude-multi` source/catalog/schema/tests

Use the local corpus first. If behavior remains ambiguous, use an external-doc
research specialist and record exact URLs/version applicability. Static binary
strings show possibility, not functional support; pinned behavior needs probes.
