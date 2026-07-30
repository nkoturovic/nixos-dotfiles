# 011 — Subagents denied spawning native types (general-purpose / Explore)

**Status: resolved** · fixed in 2.11.0 (D47, prompt contract)

## Found (2026-07-30, operator observation)

In subagent transcripts: `Error: Agent type 'general-purpose' has been
denied by permission rule 'Agent(general-purpose)' from flagSettings`,
and pairs of "2 agents finished · 0 tool uses · Done" immediately
followed by successful `cm-analyst-*` dispatches.

## Root cause

Working as designed — the composition's native-agent policy
(`general_purpose: off`, `explore: replace`) compiles into
`--disallowedTools "Agent(Explore) Agent(general-purpose) Agent(claude)"`
(visible verbatim in the argv goldens). The denied spawns complete
instantly with zero tool uses; the lead then dispatches the correct
`cm-*` types — the fence and the fallback both doing their job.

The noise source: non-lead role prompt BODIES said delegates "may
delegate" but never said WHICH agent types are legal, so nested agents
guessed native types and ate the denial. The lead's sentinel ("never
substitute a native or generic agent") existed only in the lead prompt;
durable-path agent descriptions already carried a weaker variant ("never
substitute a generic agent", the U2 sentinel) — aimed at delegators, not
delegates — so the prompt-body clause is the layer that was missing.

## Fix (surgical, prompt-only)

A delegation line added to all three non-lead role prompts (shared with
issue 010): spawn only `cm-*` agent types for delegated work; native
generic agents are not valid substitutes and may be denied by the
effective session policy (policy-neutral — the composition schema
legitimately allows `explore: native` / `general_purpose: on`, so the
prompt never claims a universal denial). One clause per prompt; no
settings change (the fence stays).

Pins: `test_roles` PROMPT_KEYWORDS (`Spawn only \`cm-*\` agent types`,
`not valid substitutes` in analyst/implementer/reviewer). Goldens
re-blessed.
