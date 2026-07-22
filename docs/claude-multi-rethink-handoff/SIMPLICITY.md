# Simplicity and anti-overengineering constraints

## Goal

Solve the actual agent-loss and whole-product reliability problems with the
fewest new concepts and the smallest durable state surface.

## Preferred shape

If evidence supports a JIT compiler, prefer something close to:

```text
validated composition + current session context
    → pure generated agent/config artifacts
    → small launch/transition plan
```

It should compile into Claude's documented native mechanisms. It should not
become a parallel agent runtime, supervisor, workflow engine or state database.

## Complexity budget

- One authoritative composition definition.
- One clearly owned session snapshot only if sessions require it.
- Minimum runtime modes; avoid dual implementations kept indefinitely.
- No resident launcher/daemon unless a proven requirement cannot be met without
  one.
- No hooks/plugins/message bus/shared-state layer merely for theoretical
  flexibility.
- No schema field, fallback path or transaction journal before its failure case
  is demonstrated and materially important.
- No broad filesystem scanner when a narrower documented precedence mechanism
  or visible merge policy solves the real problem.
- No custom workflow machinery when native workflows are sufficient.
- No production sandbox copied from the P0 probe harness.

## Questions for every proposed component

1. Which observed failure or user requirement requires it?
2. Can a documented Claude feature solve the same problem?
3. Can the design work without persistent state here?
4. What code, tests, migration and rollback burden does it add?
5. What happens if this component is deleted?
6. Is it needed now, or only for a hypothetical future?

If the answer is hypothetical, omit it.

## Simplification checkpoints

Before implementation:

- compare at least one solution materially simpler than the recommendation;
- state why each retained component survives YAGNI review;
- identify frozen C* machinery that will not be reused.

Before completing a large milestone:

- remove dead compatibility branches and duplicate state;
- collapse abstractions used once where direct code is clearer;
- keep tests focused on behavior and failure boundaries, not implementation
  ceremony;
- avoid adding another mode to rescue a flawed earlier mode.

## Red flags

- Architecture documents growing faster than executable evidence.
- Multiple supervisors/config homes/state stores for one user-visible session
  without a proven need.
- A simple agent-file generation problem requiring hundreds of lines of
  transaction or sandbox machinery in production.
- More review/gate code than feature code.
- Preserving complexity because tests already exist for it.

Existing work is a reference, not sunk-cost justification.
