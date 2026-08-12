# Open items — 2026-08-12 · v2.17.0

Ordered. Each entry names its context and done-criteria. The standing rules
from the checkpoint README remain binding.

## Now (operator decisions, not code)

- **5ee2f942 (issue 006):** unchanged — restore from backup (record is
  healthy) or `sessions forget`.
- **Key rotation (hygiene):** `KIMI_CLAUDE_API_KEY`/`QWEN_CLAUDE_API_KEY`
  were echoed into local transcripts. Rotate only if transcripts are ever
  synced/shared, then `claude-multi-proxy init` + gateway restart.

## Soon (world-triggered acceptances)

- **U1 takeover proof, routing level (L2):** first post-2.6.x daemon
  takeover of a managed session making a successful model call via the
  apiKeyHelper path; record in STATUS.md.
- **Near-limit acceptances (Opus 5, Kimi 1M, Qwen 983616):** confirm
  reactive compaction at each bound, then promote `qualification` text in
  `catalog/models.json`.
- **D56 watch: sol subagent failures** — the 258,400 fence should end the
  mid-turn prompt-too-long class. If any recur, a bounded approval-gated
  probe (kimi-208034 precedent) finds the empirical cap; if the server
  catalog returns to 372K+, revert per the qualification text.
- **First real deepseek/grok45 sessions** — ordinary `claude-gateway
  --model deepseek-flash` / `--model grok45` and the `deepseek` /
  `grok-deepseek` compositions are probe-verified at the transport level;
  the first real sessions confirm the everyday behavior (then flip the
  remaining "acceptance" language).

## Deferred with triggers (decided — do not build speculatively)

- **deepseek-v4-pro** (preview): add at GA via the D21/D50 flip sequence;
  its effort mapping is documented-unstable ("early August" update note).
- **Per-model independence_family override** (D55): build when the first
  second-family model lands on an aggregator provider.
- **OpenAI-compatible custom providers** (020 schema carries `api`):
  build only when a real OpenAI-only endpoint is wanted; the Anthropic
  skins covered DeepSeek/OpenRouter.
- **Editor "enable-and-set-lead" confirm** (015): only if the
  availability-first dance proves to be the dominant papercut.
- **Transition snapshot history / rollback** (015 D-e); **file ingestion
  into `sessions transition`**; **auto init/restart from the TUI**;
  **custom-registry orphan flagging**; **catalog scope-exceeds
  repair-action text** (021 optional P2).
- **Custom models in compositions without full catalog admission:**
  permanently rejected (metadata gates), not deferred.
- **grok-code-fast-1 / grok-build-0.1** (noted in research; not
  requested).

## Watch (accepted reservations — act only if they start paying rent)

- **Ordinary unmarked-compact bleed (D44 residual, issues/008).**
- **Issue 005 (conditional isolation); issue 007 (subagent context
  exhaustion — recovery = resume-with-steer, D43).**
- **Lead-slot lane support** (D39 residual).
- **Real-binary probe flake + pty timing flake** (AGENTS.md §4 — a
  failure that passes on an isolated re-run is the flake signature).
- **Corrupt-record forget by runtime id** re-raises (the error names the
  managed id to retry with — friction, not correctness).
- **`max_completion_tokens` listed-but-unconsumed** (parsed from
  OpenAI-shape listings, displayed nowhere yet — consumed by nothing
  outside tests; add a display when it matters).
- `cli.py` module size (8k+ lines); `probe.py` weight; wide-char cell math.

## Done this cycle (D50–D56, blueprints 014–022; gens 120–124)

- 2.13.0: qwen3.8 production; MRU picking; --composition-file; providers
  pane; doctor radar; ^O; readable muted color.
- 2.14.0: first-run dead-ends closed; hardened secret writes; discover
  (Kimi verified; Qwen 404); dev scaffold + help + promote runbook.
- 2.15.0: custom providers & ordinary models registry; pane N/A/D flows;
  per-bound picker fences; models browser + enable jump.
- 2.16.0: deep six-lane analysis batch — load-free corrupt forget,
  cwd-missing gate, under-lock forget liveness, managed 1M reconciliation,
  doctor 401 problem, redirectless fetches, registry guards, unserved
  marking, cycle discard guard, pycache source filter.
- 2.17.0: DeepSeek + OpenRouter providers (flash 1M / grok45 500K grok
  profile); descriptor listing; compositions; D55 multi-route; D56 sol
  codex-route correction; review sweep incl. the fetch-mark revival.
