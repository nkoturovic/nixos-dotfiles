# Blueprints — numbered work items with a design doc per folder

Blueprints are forward-looking design+plan docs for work not yet (or currently)
landed; `../issues/` holds problem investigations. One numeric sequence is
shared across both so every work item has a unique id.

| # | Item | Target | Status |
|---|------|--------|--------|
| 014 | [Edit-tab save hotkey](014-edit-tab-save-hotkey/) | 2.13.0 | landed (activated, gen 120) |
| 015 | [Dynamic composition/model selection](015-dynamic-model-selection/) | 2.13.0 | landed (activated, gen 120) |
| 016 | [Qwen3.8 Max production flip](016-qwen38-production/) | 2.13.0 | landed (activated, gen 120) |
| 017 | [Muted color visibility](017-muted-color-visibility/) | 2.13.0 | landed (activated, gen 120) |
| 018 | [Providers pane](018-providers-pane/) | 2.13.0 | landed (activated, gen 120) |
| 019 | [Post-release improvements](019-post-release-improvements/) | 2.14.0 | landed (activated, gen 121) |
| 020 | [Custom providers & ordinary models](020-custom-ordinary-models/) | 2.15.0 | landed (activated, gen 122) |
| 021 | [Deep analysis hardening](021-deep-analysis-hardening/) | 2.16.0 | landed (activated, gen 123) |
| 022 | [DeepSeek & OpenRouter providers](022-deepseek-openrouter/) | 2.17.0 | landed (activated, gen 124) |
| 023 | [Sol joins the 1M class](023-sol-1m-class/) | 2.18.0 | landed (activated, gen 125; acceptance probe green) |
| 024 | [DeepSeek V4 Pro GA](024-deepseek-v4-pro/) | 2.19.0 / catalog 19 | landed; corrected Pro max thinking/tool call accepted |
| 025 | [Grok 4.6 replaces 4.5](025-grok-46-replacement/) | 2.19.0 / catalog 19 | landed; exact-slug high + xhigh streaming calls accepted |

Batch approval flow: blueprints → implementation → per-item review → full suite
→ commits on `feature/term-only` → **operator green light** → HM activation
(+ the one approval-gated live provider call from 016).

As-built amendments recorded after cross-family review (2.13.0):

- **015 D-d**: `rendered_selectors` compares ALIASES ONLY — live verification
  proved CLIProxyAPI 7.2.80 never serves direct wire names (an aliases+wire
  expectation BLOCKs a healthy gateway forever). The byte-level on-disk
  config-drift check covers wire-only remappings (invisible to /v1/models);
  OAuth pools without a credential record get login guidance instead of
  restart advice.
- **015 D-a**: the elsewhere MRU tier excludes this-cwd names (a composition
  used in both appears exactly once); `--composition-file` refuses
  resume/continue unconditionally (name equality is not verification);
  stdin reads are bounded at the strict-JSON limit + 1.
- **014**: ^O is technically VDISCARD under IEXTEN — empirically verified
  delivered to the app on the operator's pty (unlike ^S, which the line
  discipline swallows); the "no flow-control meaning" shorthand stands as
  the practical rationale.
- **016**: the final routing_note is leaner than the blueprint's
  "parity-with-Sol" wording — the operator steered configs to production
  language without dates/process residue (the evidence lives here and in
  DECISIONS D50, not in the catalog).
- **017**: light-palette 256-color dim is 240 (not 245) — 245 on white is
  ~2.7:1, too faint (review nit).
