# claude-multi rethink handoff

This folder is the restart package for a new agent. The current C\* blueprint
and plan are frozen historical artifacts and must not be treated as the target.
The canonical prompt instructs the next agent to investigate, create a new
`docs/claude-multi-final/` blueprint/spec package, implement it, and verify the
whole product—not merely return another plan.

## Start here

1. [`PROMPT.md`](./PROMPT.md) — canonical prompt for the next agent.
2. [`REQUIREMENTS.md`](./REQUIREMENTS.md) — user requirements and constraints.
3. [`CURRENT-STATE.md`](./CURRENT-STATE.md) — repository/runtime/test state.
4. [`EVIDENCE.md`](./EVIDENCE.md) — observed failure and verified facts.
5. [`SOURCE-MATERIAL.md`](./SOURCE-MATERIAL.md) — mandatory documentation corpus.
6. [`HISTORICAL-DESIGN.md`](./HISTORICAL-DESIGN.md) — what C\* attempted and why it is frozen.
7. [`WORKING-TREE.md`](./WORKING-TREE.md) — uncommitted change inventory and reference bundle.
8. [`EXECUTION-GATES.md`](./EXECUTION-GATES.md) — restart workflow and acceptance gates.
9. [`REVIEW-STRATEGY.md`](./REVIEW-STRATEGY.md) — quality without micro-review ping-pong.
10. [`TRANSITIONS.md`](./TRANSITIONS.md) — composition changes and controlled restart requirements.
11. [`VELOCITY.md`](./VELOCITY.md) — faster delivery without lowering quality.
12. [`SIMPLICITY.md`](./SIMPLICITY.md) — explicit anti-overengineering constraints.
13. [`artifacts/README.md`](./artifacts/README.md) — saved patch/archive restoration guide.

The next agent must inspect the live repository and runtime rather than trusting
dates, PIDs, process lists, or package paths in this handoff indefinitely.
