# State snapshot — 2026-09-10, commit a44e2a2 (live: 2.24.0 / catalog 26, gen 139)

## Live state (verified)

- Home Manager **generation 139**; launcher **2.24.0**, catalog **26**.
- `claude-multi doctor`: **Ready** (only the by-design 2.1.261 Attention —
  the pin is 2.1.220; see issue 029).
- Gateway restarted after the catalog26 activation; `healthz` ok;
  **44 selectors served**, including `claude-multi-deepseek-flash-*`,
  `claude-multi-qwen-flash-next`, the Astra lanes and `gpt-multi-gpt55-high`.
- Presets resolve: `deepseek` (5), `deepseek-flash` (3), `grok-deepseek` (6),
  `muse-direct`, `muse-contributor-direct`, `qwen-local-direct`.

## DeepSeek V4.1-Flash configuration (D65, catalog 25)

| field | value |
|---|---|
| wire | `deepseek-flash` (canonical since 2026-09-10) |
| display | `DeepSeek V4.1 Flash` |
| lanes | `high` → `output_config.effort: high`; `max` → `: max` |
| context | client/provider/declared 1,000,000; validated 200,000 |
| operating | window **800,000**, trigger **702,000** |
| provider | `direct` → `https://api.deepseek.com/anthropic`, `x-api-key` |
| pro wire | `deepseek-v4-pro` **reroutes to V4.1-Flash from 2026-09-14 04:00 UTC** |

Bounded end-to-end probes (approved): `claude-multi-deepseek-flash-high` and
`-pro-high` both HTTP 200, force-mapped back to our alias; a no-`max_tokens`
request also 200 (`end_turn`).

## Output tokens (D66) — delegated, effective 32,000

`CLAUDE_CODE_MAX_OUTPUT_TOKENS` is a **reserved** key and is **unset** on
both launch paths; no catalog/schema/scope/gateway field exists for output.
The pinned client resolves `max_tokens` as env → registry → unknown-model
fallback **32000** (`Mxg`; upper `Oxg=128000`), and our aliases are
registry-unknown, so **32,000 is what is actually sent** on every lane —
inside DeepSeek's 384K ceiling. No change made; none justified without an
observed truncation.

## 800K boundary (D66) — verified complete

`OPERATING_WINDOW_CEILING = 800_000` applied at all six window producers;
traced every launch path (managed, ordinary/direct, single-model,
custom-registry, managed resume, ordinary resume, repair/converge, legacy
argv) — **no bypass**. Sub-ceiling values preserved: grok46 500,000;
gpt55 258,400; qwen-flash-next 320,032; qwen38 983,616 → 800,000.

## Evidence state

- Full suite: **1,716 tests**, 2 failures — both pre-existing/environmental.
- `nix-build package.nix` + sandbox suite: green (2.24.0).
- Rendered gateway diff at activation: exactly 4 lines (2 wires, 2 display
  names) — matches the re-blessed golden.
- Same-family reviews recorded for D64 (APPROVE-WITH-NITS, all fixed) and
  D65 (REVISE, all findings addressed).
