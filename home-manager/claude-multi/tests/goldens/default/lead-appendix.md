## Effective inventory (generated)

- `cm-analyst-sol-high` — cm-analyst · GPT-5.6 Sol · lane high · preferred. Use for routine reconnaissance and bounded reasoning.
- `cm-analyst-kimi-k3-max` — cm-analyst · Kimi K3 · 1M selector · lane max. Use for broad architecture, security, or repository-wide synthesis.
- `cm-implementer-sol-high` — cm-implementer · GPT-5.6 Sol · lane high · preferred. Use for bounded implementation in an isolated worktree.
- `cm-implementer-kimi-k3-max` — cm-implementer · Kimi K3 · 1M selector · lane max. Use when implementation requires broad context across many files.
- `cm-reviewer-sol-xhigh` — cm-reviewer · GPT-5.6 Sol · lane xhigh · preferred. Use for focused review of bounded small-to-medium changes; prefer xhigh for deeper review within bounded scope.
- `cm-reviewer-opus5-xhigh` — cm-reviewer · Opus 5 · 1M selector · lane xhigh. Use for the optional Claude-native independent verdict.

## Native-agent policy (generated)

- Explore: replaced by `cm-analyst-*` variants (native Explore denied).
- Plan: native.
- general-purpose: off.

## Review independence (generated)

Enabled provider families: anthropic, moonshot, openai.
- A change authored by an anthropic-family variant must not receive its sole verdict or final review from another anthropic-family variant while a reviewer from openai is enabled.
- A change authored by a moonshot-family variant must not receive its sole verdict or final review from another moonshot-family variant while a reviewer from anthropic/openai is enabled.
- A change authored by an openai-family variant must not receive its sole verdict or final review from another openai-family variant while a reviewer from anthropic is enabled.

## Context policy (generated)

- Lead context: 1000000 client tokens; user-attested configured provider bound 1000000; process compaction capacity 1000000; deterministic reactive trigger 882000. Proactive summary preparation is runtime-controlled and may occur earlier.
- Process scalar: CLAUDE_CODE_MAX_CONTEXT_TOKENS=372000 is exported for this mixed process; it bounds lower-context delegated variants, while the lead thread keeps the capacity and trigger above.
- Context qualification: this configured provider bound is not near-limit benchmark-verified. It follows explicit route/operator attestation; live acceptance must confirm it before it is described as provider-safe.

## Standing rules (generated)

- One writer owns an overlapping file scope at a time.
- Invoke generated agents by exact ID; never pass a per-invocation model override.

## Session sentinel (generated)

- Managed session: 11111111-1111-4111-8111-111111111111 (composition `default`).
- If a selected cm-* type is unavailable, stop delegation. Never substitute a native or generic agent.
- Exact relaunch after interruption: ask the user to run `claude-multi --composition default -r 11111111-1111-4111-8111-111111111111`.
