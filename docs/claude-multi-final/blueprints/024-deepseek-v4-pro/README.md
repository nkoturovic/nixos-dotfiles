# 024 — DeepSeek V4 Pro GA (0813) + Pro/Flash routing

## Context

DeepSeek-V4-Pro-0813 is the official GA release of V4 Pro (2026-08-13),
superseding Preview. First-party API calls use the stable alias
`deepseek-v4-pro`; `DeepSeek-V4-Pro-0813` is the current resolved version
label, not a documented callable DeepSeek API id. The existing Flash entry
already does the same thing correctly: `deepseek-v4-flash` currently
resolves Flash-0731. No dated suffix is used in either wire id, so future
first-party alias bumps need no catalog-wire migration.

Primary evidence:

- Official DeepSeek pricing/model table (2026-08-19): API model ids
  `deepseek-v4-flash` / `deepseek-v4-pro`, versions Flash-0731 /
  Pro-0813; both 1M context, max output 384K, Anthropic API + tools.
- Official HF card: 0813 is the official release, superseding Preview;
  agent benchmarks use `max` effort.
- Official Anthropic compatibility: base
  `https://api.deepseek.com/anthropic`, x-api-key, model field accepts
  `deepseek-v4-pro`; `output_config.effort` supported, budget_tokens
  ignored, tools/stream/system supported, images/documents unsupported.
- Community benchmark (routing guidance, NOT transport contract): Flash
  is the consistent edge/error-path bug hunter; Pro is broader and safer
  for architecture, production fixes, propagation, and regression tests.
  The measured pipeline is Flash scan → Pro implementation/fact-check →
  independent review.

Qwen3.8 Max and GPT-5.6 Sol remain stronger general models; DeepSeek Pro
is a specialized architecture/implementation/finalization option, not a
new top general escalation tier.

## Catalog design

Add `deepseek-pro` to `catalog/models.json`:

- provider `deepseek`, wire `deepseek-v4-pro`, display `DeepSeek V4 Pro`;
  roles lead/analyst/implementer/reviewer;
- context: client/provider/declared 1,000,000, profile `large`, scalar null;
  validated 200,000 (conservative unverified floor — docs do not move the
  evidence ledger; one approval-gated live call moves it later);
- lanes: `high` → `output-config-high`, `max` → `output-config-max`;
  default high (every existing multi-lane model defaults to its cheaper
  lane; compositions pin max explicitly for implementation/finalization);
- selectors `claude-multi-deepseek-pro-high[1m]` and `-max[1m]`;
- lean routing note: GA alias/current version, thinking default-on,
  architecture/careful implementation role, pair with Flash; explicitly
  keeps Qwen/Sol as stronger general choices.

Provider transport/contracts and schemas are already sufficient; no
CLIProxyAPI embedded-registry or Nix patch is needed (direct catalog-driven
`claude-api-key` route). DeepSeek listing already advertises both stable
aliases; provider-scoped discover marks Pro cataloged automatically.
Catalog version 18 → 19; launcher remains 2.18.0 (catalog-only behavior).

## Composition policy

- `deepseek` (updated in place, operator requested): Pro lead (high default),
  Flash preferred analyst, Pro preferred max implementer, Flash preferred
  max reviewer; each has the other DeepSeek model as alternate. Same-family
  review is honestly reduced independence.
- `deepseek-flash` (new): preserves the previous cheap all-Flash rig.
- `grok-deepseek` (updated): Grok lead; Flash preferred analyst; Pro max
  preferred implementer/finalizer; **Grok preferred reviewer** + Pro
  alternate — the preferred review path is cross-family for DeepSeek-authored
  changes.
- `sol-qwen-glm-deepseek-flash` (updated in place, legacy name retained):
  Pro max alternatives added to analyst/implementer/reviewer; Sol/Qwen
  remain stronger preferred/general options. The explicitly named Flash
  model remains available.
- Trusted `default` stays unchanged; no broad roster inflation.

The catalog19 documents are checked into the package under
`tests/fixtures/compositions/024-planned/` (validated by the sandbox suite
against the source catalog). The catalog18-compatible rollback copies are in
`tests/fixtures/compositions/024-rollback-catalog18/`. Live
`~/.config/claude-multi/compositions/` files remain catalog18-compatible
until activation — no broken presets while waiting for approval. At the
activation boundary, save the planned documents through the validated
composition store (mode 0600), then converge/verify.

## Costs and constraints

Current official rates: Pro is 3x Flash on miss/output and ~3.14x on cache
hits; Pro concurrency 500 vs Flash 2500. Peak hours 01:00–04:00 and
06:00–10:00 UTC double rates. Put cost/scheduling detail here, not in the
catalog routing note. Pro run-to-run variance from the community benchmark
is medium-confidence/transient evidence—guidance, not permanent catalog fact.

Both models are text-only on the Anthropic path; `cache_control` is ignored;
MCP content blocks unsupported (client-side tools work). Pro/Flash share the
same DeepSeek independence family—one cannot be the sole independent review
of the other's authored code.

## Verification plan

- Catalog: production alias/version shape, Flash alias unchanged, selector/
  wire uniqueness, context/lanes/contracts/default lane, catalog 19.
- Context/compiler: Pro lead tuple 1M/1M/882K; large-profile selector set
  gains Pro high/max; wire/lane/canonical `wire[1m]` resolver forms.
- Render: exactly two Pro aliases in DeepSeek section; high/max aliases join
  the matching override groups; no dated wire; render golden only.
- CLI/TUI: Pro in ordinary picker/models browser, keyless row count +1,
  navigation/floor ripple, typed selectors, direct launch/record profile,
  mocked discover reports Pro cataloged.
- Composition tests: exact hybrid DeepSeek/Grok shapes, preferred markers,
  generated IDs, 1M/500K math, preferred reviewer family differs preferred
  implementer in grok-deepseek. Validate the four user files + 0600.
- Full discovery, package build, sandbox suite, diff check; cross-family
  review + adversarial verification.

## Cross-family review amendments

- **Catalog/surfaces review — revise, fixed**: HANDOFF/USAGE now distinguish
  live catalog18 presets from staged catalog19 files; no pre-activation
  claim that `deepseek-flash` or the Pro-backed pipelines are live.
- **Composition review — approve + coverage nit fixed**: the exact four
  planned files moved into package test fixtures and are schema-loaded,
  resolved, and shape-pinned in the sandbox (including the 17-slot
  `sol-qwen-glm-deepseek-flash` compatibility file). Catalog18 rollback
  files are fixtures too.
- **Rollback runbook — fixed**: a failed Pro max acceptance call restores
  both HM catalog18 and the XDG user compositions; HM rollback alone is
  insufficient. The exact steps follow below.

## Activation boundary

Implementation/docs only until explicit green light. Activation re-renders
and restarts CLIProxyAPI, saves the four `024-planned` fixtures through the
composition store (0600), runs doctor/repair-all, and verifies the local
served selectors. A **single separately approved bounded Pro max call**
validates wire/auth/thinking/tool blocks and the max payload contract; until
then the qualification remains docs-verified, not live-verified. Create a
new catalog19 checkpoint after activation; do not rewrite the
v2.18.0/catalog18 historical checkpoint.

**Failed-call rollback (explicit):** restore HM generation 125 (the
catalog18 generation shown by `home-manager generations`; invoke its store
path's `/activate`), then restore the three files from
`tests/fixtures/compositions/024-rollback-catalog18/` into
`~/.config/claude-multi/compositions/` with mode 0600, delete the new
`deepseek-flash.json`, run `claude-multi-proxy init` + restart
`cli-proxy-api`, and `doctor --repair-all`. This rolls back BOTH the catalog
and the XDG user compositions—an HM rollback alone cannot restore the latter.
