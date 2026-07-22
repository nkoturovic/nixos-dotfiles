# Independent quality checkpoints: velocity with quality

## Problem to avoid

The prior session repeatedly used:

```text
small fix → review → small fix → review → small fix → review
```

This increased coordination cost, slowed progress and made architecture review
feel like implementation-by-ping-pong.

Anthropic's `claude-orchestration-patterns.md` describes generator–verifier as
valuable only with explicit criteria and warns that iterative loops can stall.
This strategy therefore uses a bounded quality pass, not an open-ended loop.

## Default cadence

1. Define a bounded milestone and acceptance evidence before coding.
2. One author or non-overlapping writer lanes complete a substantial coherent
   milestone—not a handful of tiny edits.
3. Authors self-review, run focused tests and consolidate known cleanup first.
4. Decide whether independent review would materially reduce risk. Most routine
   milestones proceed on author verification without it.
5. When useful, a fresh **reviewer/finisher** receives the coherent diff,
   surrounding context, acceptance criteria and test evidence once.
6. It may directly fix clear bounded implementation defects, test failures,
   consistency issues and worthwhile cleanup, then runs focused verification.
7. It reports findings, every edit and evidence in one batch. Architectural or
   policy uncertainty is escalated rather than silently changed.
8. The lead integrates the result and closes the checkpoint. Do not
   automatically send it to another reviewer.

## Two distinct quality roles

### Reviewer/finisher — default when a quality pass is useful

- Works after a large coherent milestone.
- Reads the actual diff and surrounding code.
- Proactively fixes clear implementation problems within a bounded scope.
- Runs tests and returns one combined change/evidence report.
- Does not provide a claim of independent approval for its own edits.

The name may remain `reviewer`; alternatives such as `finisher` or
`reviewer-fixer` may communicate the behavior better. Naming is secondary to a
clear contract.

### Independent auditor — rare

- Read-only and separate from the author/fixer.
- Used only when independence materially reduces architecture, security,
  session-integrity, data-loss or release risk.
- Returns one batch of material findings. It does not start a default review
  loop.

## When another independent review is justified

- The fixes materially changed architecture, security, session/data integrity
  or the original acceptance boundary.
- Evidence cannot establish that a high-severity finding is actually resolved.
- A new independent material problem was discovered.
- The user explicitly requests another opinion.

Otherwise, once findings are fixed/rebutted and focused evidence passes, stop.
Do not reopen unchanged code or turn optional nits into review churn.

## Good candidates for an independent audit

- architecture decisions;
- security/data/session integrity boundaries;
- large phase-complete implementation diffs where failure impact is meaningful;
- package/activation/rollback changes;
- final integrated release candidate.

## When review is not needed

- every tiny mechanical edit;
- formatting or wording while a larger doc rewrite is still underway;
- intermediate red tests during an author-owned implementation pass;
- repeated confirmation of already-approved unchanged code.

## Product-level role and naming

The final architecture may keep `cm-reviewer` and allow it to finish/fix, split
`cm-finisher` from a rare `cm-auditor`, keep the behavior lead-managed, or expose
a composition policy. Regardless of representation, it acts on a meaningful
completed set of changes, never every small fix. Cross-family independence
remains important only for the high-risk audit path.

Potential policy for the new design to evaluate:

```text
review.mode = milestone | manual | off
```

Do not add this schema merely because it is written here. First decide whether
lead policy alone is enough and how the TUI should communicate review cadence.
