# 009 — /model picker shows only a subset of availableModels (native display filter)

**Status: resolved** · documented in 2.11.0 (D47)

## Found (2026-07-30)

An ordinary session launched via the card's **G** picker (glm52) showed only
three rows in the in-session `/model` picker: `Default (recommended)
(currently Opus 5 (1M context))`, `Fable 5`, and `claude-multi-glm52-max[1m]`
— none of the other large-profile models (kimi-k3, qwen38, opus 4.8/5).

## Root cause (binary-verified, 2.1.220)

The scope fence was correct — the session's `settings.json` contained all
six large-profile selectors in `availableModels`. The display filtering is
native Claude Code. The picker-option builder in the 2.1.220 binary
(`jug`): for each `availableModels` entry it requires
`^claude-[a-z0-9-]+$` AND the name to contain `opus`, `sonnet`, or `haiku`
(lowercased); entries are then deduplicated by equivalence
(`claude-multi-opus-4-8[1m]`/`claude-multi-opus-5[1m]` fold into the
Default/opus rows, `claude-fable-5[1m]` into the Fable row). Third-party
names (kimi, qwen, glm) fail the substring rule and are never displayed —
**except** that the session's current model is always appended (the glm52
row with the ✔).

The "Default (currently Opus 5 (1M context))" row comes from the
`ANTHROPIC_DEFAULT_OPUS_MODEL` env the launcher pins to the canonical
opus route — expected.

## Decision

Nothing to fix in code: the `availableModels` allow-list (what a switch
may use) is intact; only the interactive display is filtered. Switching to
any model in the group works by relaunch
(`claude-gateway -r <uuid> --model <model>`; managed: `sessions
transition`). Documented in USAGE (both /model sections), UX §1.5, and
the G picker's help text. Also corrected the stale USAGE claim that the
managed /model menu is "roster-shaped" — the same native filter applies
to managed rosters at 2.1.220.
