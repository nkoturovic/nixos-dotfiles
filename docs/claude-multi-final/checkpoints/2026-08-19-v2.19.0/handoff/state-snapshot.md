# State snapshot — 2026-08-19 · v2.19.0/catalog19

Evidence behind the checkpoint README claims. Verify before trusting.

## Activation history

- **Generation 126:** first catalog19 activation; local checks green.
- **Generation 127:** verified full rollback to the generation-125 catalog18
  store after the forced-tool-choice Pro call returned HTTP 400. Three XDG
  compositions restored at 0600, `deepseek-flash.json` removed, gateway
  re-rendered/restarted, 35 repairable scopes reconverged.
- **Generation 128:** corrected catalog19 reactivation after fresh tests/build,
  with all four planned compositions installed and six new aliases served.
- **Generation 129 (current):** final provider-acceptance evidence metadata
  activated. Store path:
  `/nix/store/lckj97cn2gkj2pwkpk4p8qdkhq3w9anq-home-manager-generation`.

Installed package:
`/nix/store/kv3359dlxvsc7zywp7756kkrwbfmgz29-claude-multi-2.19.0`.
Final package's `claude-multi` sha256:
`365be8ed472c1f1c803ba0c44ddbc589473e6d189e7739a6877ca852ef063c2d`.
Pinned Claude 2.1.220 sha256:
`674f61f20ff306f3100cf9200e4c36c4b70278b5bef2884549819b942a89c863`.

## Local gateway and state

- `cli-proxy-api.service`: active/running, main status 0; `/healthz` HTTP 200.
- Config mode 0600, sha256
  `53ddd9c7d7d0fffd93d6e674cc65aaab2015e01776cf8ff44adc0a8b6167d392`.
- `/v1/models`: 37 selectors; all six DeepSeek Flash/Pro and Grok 4.6
  high/max/xhigh aliases present; no Grok 4.5 alias.
- 36 session records, 36 durable, 0 legacy. Records/scopes/compositions have
  zero `grok45` or `x-ai/grok-4.5` references.
- 18 user compositions; active catalog19 files semantically equal the planned
  fixtures and are mode 0600: `deepseek.json`, `deepseek-flash.json`,
  `grok-deepseek.json`, `sol-qwen-glm-deepseek-flash.json`.
- Custom registry absent.
- `doctor --repair-all`: 35 converged; one expected failure, ordinary record
  `d928f2a2-ecd9-41f0-b414-b828b74887f8` with retired `sol` profile. Doctor
  prints the explicit same-model re-pin command. No automation changed it.

## Approval-gated calls (exactly three successful attempt-2 requests)

### DeepSeek Pro max

- Selector `claude-multi-deepseek-pro-max`; corrected request omitted
  `tool_choice`, offered one tool.
- HTTP 200; response type message; stop reason `tool_use`; blocks
  `thinking,tool_use`; exactly one expected tool with marker and sum 42.
- Usage: 447 input, 108 output. This proves route/auth/max-field acceptance,
  thinking, and model-selected tool shape. It does not prove forced choice,
  max compute spent, or 1M context.

### Grok 4.6 high

- Selector `claude-multi-grok46-high`, exact wire configured
  `x-ai/grok-4.6`.
- HTTP 200; blocks `thinking,redacted_thinking,tool_use`; stop `tool_use`;
  expected tool payload. Usage 258 input, 137 output.

### Grok 4.6 xhigh streaming

- Selector `claude-multi-grok46-xhigh`, exact wire configured
  `x-ai/grok-4.6`.
- HTTP 200 `text/event-stream`; 63 events; ordered block starts
  `thinking,redacted_thinking,tool_use`; assembled JSON tool payload correct;
  stop `tool_use`. Usage 271 input, 152 output, 110 thinking.
- The moving alias `~x-ai/grok-latest` was not called.

## Test/build/review evidence

- Full host discovery after final evidence flip: `Ran 1680 tests ... OK
  (skipped=2)`.
- Package build:
  `/nix/store/9qhk0r54flss6gn4pl5q2f5wb46k16ab-claude-multi-2.19.0`.
- Sandbox: `nix build --no-link --file tests/default.nix` green.
- Disposable loopback proxy regression proves Pro max wire mapping,
  `output_config.effort=max`, tool preservation, and no invented
  `tool_choice`; no external provider involved.
- Recovery amendment independent review: Qwen3.8 Max APPROVE; two wording/
  citation nits fixed, forced-choice pass-through pin intentionally
  trigger-gated because runtime behavior did not change.
- Earlier combined review: Sol/Qwen/GLM cross-family + adversarial verification;
  all confirmed catalog/composition/rollback/UX findings fixed.
- Final activation-doc review: GLM-5.2 independently cross-checked the live
  generation/store graph, rollback identity, package/hash evidence, sandbox,
  census, aliases, calls, doctor state, D3, and checkpoint links: APPROVE. Its
  one serialization-wording nit was fixed; authenticated local `/v1/models`
  independently confirmed the 37-entry census.

## Commits (feature/term-only, nothing pushed)

- `cee0249` — DeepSeek V4 Pro GA, catalog19 (D58/024).
- `a4896e1` — Grok 4.6 replacement and launcher 2.19.0 (D59/025).
- `be958fc` — forced-choice failure evidence, verified rollback, corrected
  request-shape pin.
- `c6a06c0` — successful Pro/Grok acceptance evidence.
- This checkpoint/activation docs commit: on top.
