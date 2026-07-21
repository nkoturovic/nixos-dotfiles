## Effective inventory (generated)

- `cm-analyst-sol-high` — cm-analyst · GPT-5.6 Sol · lane high · preferred. Use for routine reconnaissance and bounded reasoning.
- `cm-analyst-kimi-k3-max` — cm-analyst · Kimi K3 · 1M selector · lane max. Use for broad architecture, security, or repository-wide synthesis.
- `cm-implementer-sol-high` — cm-implementer · GPT-5.6 Sol · lane high · preferred. Use for bounded implementation in an isolated worktree.
- `cm-implementer-kimi-k3-max` — cm-implementer · Kimi K3 · 1M selector · lane max. Use when implementation requires broad context across many files.
- `cm-reviewer-gpt55-high` — cm-reviewer · GPT-5.5 · lane high · preferred. Use for routine bounded review.
- `cm-reviewer-opus-xhigh` — cm-reviewer · Opus 4.8 · 1M selector · lane xhigh. Use for the optional Claude-native independent verdict.

## Native-agent policy (generated)

- Explore: replaced by `cm-analyst-*` variants (native Explore denied).
- Plan: native.
- general-purpose: off.

## Review independence (generated)

Enabled provider families: anthropic, moonshot, openai.
- A change authored by an anthropic-family variant must not receive its sole verdict or final review from another anthropic-family variant while a reviewer from openai is enabled.
- A change authored by a moonshot-family variant must not receive its sole verdict or final review from another moonshot-family variant while a reviewer from anthropic/openai is enabled.
- A change authored by an openai-family variant must not receive its sole verdict or final review from another openai-family variant while a reviewer from anthropic is enabled.

## Standing rules (generated)

- One writer owns an overlapping file scope at a time.
- Invoke generated agents by exact ID; never pass a per-invocation model override.
