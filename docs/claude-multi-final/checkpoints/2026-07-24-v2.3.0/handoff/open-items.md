# Open items — checkpoint 2026-07-24 · v2.3.0

Ordered next work. Each item lists its entry points and done-criteria. The
standing rules from [`README.md`](README.md#hard-rules-inherited-still-binding)
apply to all of them.

## 1. Re-pin the managed binary to Claude 2.1.218 (bounded, next)

**DONE 2026-07-24** (lands at the 2.4.0 activation): the contract pins
2.1.218 with the full offline evidence suite green (1,171 tests, real-binary
compaction/delegation probes included). From 2.4.0 this whole class is a
routine one-command flow: `claude-multi update` (detect → offline inspect →
promote → evidence suite → operator override, instant effect; `--activate`
for the baseline refresh). The doctor Attention line is the standing
trigger.

## 2. U1 takeover proof — watch once (acceptance, no code)

The 2.1.218 supervisor already took over backgrounded managed sessions in
production. The durable design exists for exactly this; it needs one honest
observation.

- **Do:** after the next natural background/takeover of a durable session,
  open its Agent tool roster and confirm all six `cm-*` types are present;
  record the observation in STATUS.md (acceptance step L2).
- **Done when:** one post-takeover roster is observed intact (or a failure
  is filed as a real bug).

## 3. Qwen preview → production (when `qwen3.8-max` ships)

Follow DECISIONS D21 exactly: `wire_model` → context re-verification →
`reasoning_effort` tier check (any tier above xhigh?) → one consent-gated
live call → drop "· Preview" from the display.

## 4. Legacy record `9bc5fd42` (user choice)

**DONE 2026-07-24**: forgotten (record only; the transcript is untouched and
stays natively resumable). Doctor is fully clean (Ready, no Attention lines).

## 5. Residual reservations (from [`../../../SANITY.md`](../../../SANITY.md))

Accepted, documented — address only if they start paying rent:

- `cli.py` concentrates the product surface (4,458 lines) — split per-screen
  only when a change forces it.
- `probe.py` (2,579 lines) — dev-only, lazy-imported; shrink if it stops
  being the evidence machine.
- Wide-char cell math in the TUI (cosmetic; CJK paths misalign tables).
- Hook-delivery invisibility (Claude never firing a hook is undetectable
  from the launcher; one observed case).
- Two proxy lows (concurrent init/run token split; discarded availability
  report) — theoretical on a single-user systemd service.

## 6. Housekeeping cadence (steady state)

- After catalog/model changes or package rebuilds: `claude-multi doctor`
  should stay Ready; if scope drift appears, `doctor --repair-all` (it is
  idempotent and failure-isolating).
- Occasionally: `doctor --prune`; `sessions forget` transcriptless records
  (the hygiene pattern is proven — verify transcriptlessness per record
  first, then forget, then prune).
- Home Manager generations: expiry is now pure disk hygiene (the hook shim
  removed the coupling); `home-manager remove-generations <id>` when wanted.

## Recording your work

Update `../STATUS.md` at milestones. When the state is meaningfully
different (activation, architecture change, major integration), create the
next checkpoint per [`../../README.md`](../../README.md) and link it there.
