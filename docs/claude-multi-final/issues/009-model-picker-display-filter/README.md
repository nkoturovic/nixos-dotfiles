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
native Claude Code, and it is stricter than a first read suggests. In the
picker-option builder (`jug`), each `availableModels` entry must match
`^claude-[a-z0-9-]+$` AND contain `opus`, `sonnet`, or `haiku`
(lowercased), then resolve through the bundled static model registry for
a display label. Our custom selectors die at that registry gate
(`claude-multi-opus-4-8[1m]` / `claude-multi-opus-5[1m]` have no registry
entry, so no label, so no row) — there is no "fold into the Default row"
step; the Default row is built-in with `value: null`. Third-party names
(kimi, qwen, glm) fail the substring rule even earlier. The visible
**Fable 5** row is a BUILT-IN roster row kept because the allow-list's
`claude-fable-5[1m]` alias-matches the canonical fable (the fable entry
itself fails the substring rule like other third-party names). And the
session's current model is always appended as a "Custom model" row (the
glm52/kimi rows with the ✔).

The "Default (currently Opus 5 (1M context))" row comes from the
`ANTHROPIC_DEFAULT_OPUS_MODEL` env the launcher pins to the canonical
opus route — expected.

**Typed switching works (binary-verified):** the `/model <name>` command
handler validates the typed selector against the allow-list, not the
displayed list — so `/model claude-multi-kimi-k3[1m]` switches in-session
even though the row never renders.

## Decision

Nothing to fix in code: the `availableModels` allow-list (what a switch
may use) is intact; only the interactive display is filtered. Switching
works by typing the selector (`/model <selector>`) or by relaunch
(`claude-gateway -r <uuid> --model <model>`; managed: `sessions
transition`; ordinary in the TUI: **T** in the sessions screen, D48).
Documented in USAGE (both /model sections + FAQ), UX §1.5, and the G
picker's help text. Also corrected the stale USAGE claim that the managed
/model menu is "roster-shaped" — the same native filter applies to
managed rosters at 2.1.220.
