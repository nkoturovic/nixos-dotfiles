# Open items — 2026-08-11 · v2.15.0

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
  `catalog/models.json` (Kimi's 1M is provider-advertised since 2.14.0 —
  the near-limit behavior is the remaining unknown).

## Deferred with triggers (decided — do not build speculatively)

- **OpenAI-compatible custom providers** (020 schema carries `api`):
  build only when a real OpenAI-compatible endpoint is wanted; needs the
  CLIProxyAPI openai-section render surface (not the claude-api-key one).
- **Editor "enable-and-set-lead" confirm** (015): only if the
  availability-first dance proves to be the dominant papercut.
- **`claude-multi discover` for Qwen/Kimi-general/Moonshot surfaces**:
  only if a list endpoint becomes documented; Token Plan 404 verified.
- **Transition snapshot history / rollback** (015 D-e): only if
  roll-back-to-previous-composition becomes a real ask.
- **File ingestion into `sessions transition`**: store-name-only by
  design (R1 P1); extend only if it proves to be the papercut.
- **Auto init/restart from the TUI** (018/020): only after demonstrated
  demand; must confirm about in-flight interruption and go through a
  supported `claude-multi-proxy restart` command.
- **Custom-registry hygiene** (orphan-flagging in doctor): v1 relies on
  manual D/remove; add only if orphaned entries start confusing.
- **Custom models in compositions without full catalog admission:**
  permanently rejected (metadata gates), not deferred.

## Watch (accepted reservations — act only if they start paying rent)

- **Ordinary unmarked-compact bleed (D44 residual, issues/008).**
- **Issue 005 (conditional isolation); issue 007 (subagent context
  exhaustion — recovery = resume-with-steer, D43).**
- **Lead-slot lane support** (D39 residual).
- **Real-binary probe flake** (RealPinnedBinaryTests under machine-wide
  load; documented in AGENTS.md §4 — re-run the class before distrusting).
- `cli.py` module size (7k+ lines — a split is worth considering only
  when it starts slowing reviews); `probe.py` weight; wide-char cell math.

## Done this cycle (D50–D52, blueprints 014–020; gens 120–122)

- 2.13.0: qwen3.8 production; MRU picking; --composition-file; providers
  pane; doctor radar; ^O; readable muted color.
- 2.14.0: first-run dead-ends closed; hardened secret writes; discover
  (Kimi verified; Qwen 404); dev scaffold + help + promote runbook.
- 2.15.0: custom providers & ordinary models registry; pane N/A/D flows;
  per-bound picker fences; models browser + enable jump; review-caught
  P0/P1 seam fixes with regression coverage.
