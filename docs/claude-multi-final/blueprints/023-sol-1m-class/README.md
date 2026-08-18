# 023 — Sol joins the 1M class (D57)

## Context

OpenAI documented a 1M-token context window for GPT-5.6 Sol on the
ChatGPT/Codex subscription route (2026-08-12; client-asserted budget:
`model_context_window=1000000`, compaction ~900K). The operator asked for
sol-based leads/subagents to use it. This supersedes D56's 258,400
correction (which was right for the July-cut state — and stays the
documented fallback if the enablement is pulled).

## Change

- `catalog/models.json` sol: client/provider 1,000,000; declared and
  provider-stated 1,050,000 (the documented window); **validated stays
  372,000** — the last historically-proven bound — until the bounded
  acceptance probe (`validated` never moves on documentation alone);
  scalar null (1M-class never constrains the process); selectors gain
  `[1m]` (`gpt-multi-sol-high[1m]`, `gpt-multi-sol-xhigh[1m]`);
  `ordinary_profile` → `large` (the sol-only profile retires).
- gpt55 unchanged (no 1M support for that model; D56 fence stands).
- `ORDINARY_PROFILE_NOTES` drops the stale `sol` note.
- Downstream (no code changes needed): the large fence derives min-member
  bound 983,616 (qwen38) for ordinary sessions; mixed compositions scalar
  at 983,616 (qwen38) instead of 258,400; the all-1M default exports no
  process scalar; the SessionStart hook's `wire+'[1m]'` equivalence
  (021) covers sol's new canonical report form (`gpt-5.6-sol[1m]`).

## Verification

- Full discovery 1,662 OK after the pin sweep; goldens re-blessed
  (exactly the `[1m]` selectors + scalar-unset diffs, reviewed).
- Cross-family sweep (glm52 catalog coherence + qwen38 downstream
  surfaces) with adversarial verify.
- **Acceptance probe (approval-gated, post-activation)**: a bounded
  >258.4K-token request through the codex pool — proves the server honors
  the 1M budget for our client path (not just Codex CLI). On failure:
  revert to the D56 fence (the qualification text carries the how).
