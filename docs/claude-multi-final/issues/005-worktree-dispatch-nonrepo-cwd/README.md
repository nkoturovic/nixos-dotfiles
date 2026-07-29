# 005 — Worktree-isolated dispatch unavailable outside git repos

**Status: open** (mitigation shipped in 2.8.0; machinery candidate deferred)

## Found (2026-07-29, live during the issue-002/003 work)

Dispatching any `cm-implementer-*` variant from this session
(cwd `/home/kotur/.claude`) fails at spawn:

```
Cannot create agent worktree: not in a git repository and no
WorktreeCreate hooks are configured.
```

## Mechanism (verified distinct from issue 001)

- `roles.json` gives cm-implementer `isolation: "worktree"`; the compiled
  agent definition carries `isolation: worktree` frontmatter
  (verified in the live scope). The **harness** creates the worktree at
  spawn time, resolving the repo from the session's cwd.
- A session rooted outside any git repository (e.g. `~/.claude` — the
  typical home for claude-multi's own development sessions) therefore
  **cannot dispatch implementer variants at all**. The Agent tool has no
  per-invocation "none" override for isolation.
- Distinct from issue 001 (D40): that was the `EnterWorktree` *tool*
  refusing a switch; this is *spawn-time creation* failing. roles.json
  was untouched by 2.7.2 — verified.

## Impact and workaround

Implementer role unusable in non-repo sessions; a lead that doesn't know
this thrashes (spawn → fail → retry → fail). Working fallback (used this
session): the lead creates the worktree itself —
`git worktree add <path> -b <branch> <base>` — and works/delegates by
absolute path, or does bounded implementation inline.

## Candidate directions

1. **Prompt note (shipped in 2.8.0, D42):** the lead contract states the
   requirement and the real fallbacks — spawn failures no longer thrash:
   in a non-repo session, implementer variants cannot be spawned at all
   (the isolation frontmatter is evaluated at spawn), so the lead does
   the bounded implementation inline (creating worktrees by hand with
   `git worktree add` when useful), uses read-mostly variants by absolute
   path for other legs, or runs the implementation from a repo-rooted
   session. (An earlier wording implied a hand-created worktree makes
   implementers spawnable — second review corrected it: it does not.)
2. **Conditional isolation (deferred, candidate D43)**: the scope
   compiler strips `isolation` from implementer definitions when the
   *record's* cwd is not a git repository (stable per session). Keeps
   implementers dispatchable; writer discipline then relies on dispatch
   sequencing instead of filesystem isolation. Trade-off against scope
   reproducibility needs a decision round — not built yet.
