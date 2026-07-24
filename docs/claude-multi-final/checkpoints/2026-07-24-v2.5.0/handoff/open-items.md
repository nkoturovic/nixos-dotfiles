# Open items — checkpoint 2026-07-24 · v2.5.0

Ordered next work. Each item lists entry points and done-criteria. The
standing rules from [`README.md`](README.md) apply.

## 1. U1 takeover proof — watch once (acceptance, no code)

The 2.1.218 supervisor already took over backgrounded managed sessions in
production. The durable design exists for exactly this; it needs one honest
observation.

- **Do:** after the next natural background/takeover of a durable session,
  open its Agent tool roster and confirm all six `cm-*` types are present;
  record the observation in STATUS.md (acceptance step L2).
- **Done when:** one post-takeover roster is observed intact (or a failure
  is filed as a real bug).

## 2. Qwen preview → production (when `qwen3.8-max` ships)

Follow DECISIONS D21 exactly: `wire_model` → context re-verification →
`reasoning_effort` tier check (any tier above xhigh?) → one consent-gated
live call → drop "· Preview" from the display.

## 3. Opus 5 near-limit acceptance (when a 1M session gets long)

The Opus 5 1M bound is family-attested (the announcement states no bound).
When an Opus-5-led session approaches the configured capacity, confirm the
reactive compaction behavior at the bound and then promote the
qualification to validated (models.json `opus5.context`), the same
acceptance shape Kimi is still waiting on.

## 4. Claude updates — routine, no action needed

The loop is closed: the card's update badge (or doctor Attention) fires on
version drift → **U** or `claude-multi update` (inspect → promote → full
offline suite → operator override, instant effect; `--activate` for the
baseline refresh). Nothing to schedule.

## 5. Residual reservations (from [`../../../SANITY.md`](../../../SANITY.md))

Accepted, documented — address only if they start paying rent:

- `cli.py` concentrates the product surface — split per-screen only when a
  change forces it.
- `probe.py` — dev-only, lazy-imported; shrink if it stops being the
  evidence machine.
- Wide-char cell math in the TUI (cosmetic; CJK paths misalign tables).
- Hook-delivery invisibility (Claude never firing a hook is undetectable
  from the launcher; one observed case).
- Two proxy lows (concurrent init/run token split — now lock-serialized;
  discarded availability report — now printed by run/login).

## 6. Housekeeping cadence (steady state)

- `claude-multi doctor` should stay Ready; scope drift → `doctor
  --repair-all` (idempotent, failure-isolating); `doctor --prune`
  occasionally.
- `sessions forget` transcriptless records after a fresh transcript check;
  transcripts are never touched.
- Home Manager generations: expiry is pure disk hygiene
  (`home-manager remove-generations <id>`); the hook shim removed the
  coupling.

## Recording your work

Update `../STATUS.md` at milestones. When the state is meaningfully
different (activation, architecture change, major integration), create the
next checkpoint per [`../../README.md`](../../README.md) and link it there.
