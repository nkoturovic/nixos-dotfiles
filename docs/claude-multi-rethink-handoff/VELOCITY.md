# Delivery velocity without sacrificing quality

## Secondary problem

Beyond the agent-disappearance bug, the prior effort progressed too slowly.
Contributors spent too much time in serial research, oversized plans,
fine-grained delegation, repeated review cycles and coordination overhead.

## Operating model

1. **One clear critical path.** Keep the next user-visible/risk-closing outcome
   explicit. Do not let secondary improvements block it.
2. **Test the riskiest assumption early.** A small safe probe or prototype is
   better than designing many branches around an unknown.
3. **Substantial milestones.** Complete a coherent feature slice before
   integration or quality review.
4. **Parallelize real independence.** Run documentation/code mapping, TUI
   design, isolated implementation folders and tests concurrently when write
   scopes do not overlap.
5. **Lead works directly.** The lead integrates and may implement tightly
   coupled work; delegation is for separable context or execution, not a goal.
6. **Capable agents.** Give agents the tools needed to finish bounded work.
   Control file ownership and isolation rather than defaulting to read-only.
7. **Focused evidence.** Start with the narrow test that proves the requested
   behavior. Broaden only for integration risk or failure.
8. **Commit to decisions.** Record the evidence and proceed. Reopen only when
   new evidence materially contradicts it.
9. **Batch quality passes.** Use the reviewer/finisher strategy only after a
   meaningful completed chunk and only when it improves confidence.
10. **Visible progress.** At each milestone report the behavior now working,
    evidence, remaining blocker and next move—not a narration of tool calls.

## Anti-patterns

- Multiple agents researching the same narrow question.
- Reviewer loops for every small edit.
- Architecture documents growing faster than executable evidence.
- Delegating tightly coupled work that the lead must immediately reconstruct.
- Running full test/build suites by habit after every file change.
- Adding schemas, modes or fallback branches before proving they are needed.
- Treating more agents or more gates as inherently higher quality.

## Quality floor

Velocity never permits:

- silent data/session loss;
- unreviewed high-risk architecture/security decisions;
- provider/live-daemon automation against the user's environment;
- unverifiable model/agent guarantees;
- activation without rollback and meaningful package/runtime evidence.

Everything above that floor should optimize for simple implementation and fast,
observable progress.
