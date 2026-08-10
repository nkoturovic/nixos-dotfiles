# Open items — 2026-08-10 · v2.13.0

Ordered. Each entry names its context and done-criteria. The standing rules
from the checkpoint README remain binding.

## Now (operator decisions, not code)

- **5ee2f942 (issue 006):** unchanged — restore from backup (record is
  healthy) or `sessions forget`.
- **Key rotation (hygiene):** `KIMI_CLAUDE_API_KEY`/`QWEN_CLAUDE_API_KEY`
  were echoed into local transcripts twice. Rotate only if transcripts are
  ever synced/shared, then `claude-multi-proxy init` + gateway restart.

## Soon (world-triggered acceptances)

- **U1 takeover proof, routing level (L2):** first post-2.6.x daemon
  takeover of a managed session making a successful model call via the
  apiKeyHelper path; record in STATUS.md.
- **Opus 5 / Kimi near-limit acceptance:** confirm reactive compaction at
  the bound, then promote `qualification` in `catalog/models.json`.
- **Qwen near-limit acceptance (new, from 016):** the live call proved
  routing, not near-limit behavior; `qualification` keeps the
  "unverified until a live acceptance call" note until then.

## Deferred with triggers (decided this cycle — do not build speculatively)

- **Editor "enable-and-set-lead" confirm** (015): only if the
  availability-first dance proves to be the dominant papercut.
- **`claude-multi discover PROVIDER`** live list calls (015): only after
  per-provider endpoints are verified AND an approval pattern exists;
  Qwen Token Plan / Kimi Coding list endpoints are unverified/absent.
- **Transition-engine snapshot history / rollback** (015 D-e): only if
  roll-back-to-previous-composition becomes a real ask; a display-only
  `prior_compositions` field was deliberately rejected (no consumer).
- **File ingestion into `sessions transition`** (sweep info note): the
  transition surface is store-name-only by design (R1 P1); extend only if
  the store-only path proves to be the papercut.
- **Auto init/restart from the TUI providers pane** (018): only after
  demonstrated demand; must confirm about in-flight interruption and go
  through a supported `claude-multi-proxy restart` command.

## Watch (accepted reservations — act only if they start paying rent)

- **Ordinary unmarked-compact bleed (D44 residual, issues/008):** unchanged.
- **Issue 005 (conditional isolation):** unchanged (harness limitation).
- **Issue 007 (subagent context exhaustion):** recovery = resume-with-steer
  (D43); upstream owns a real fix.
- **Lead-slot lane support** (D39 residual): unchanged.
- **Same-context-family `/model` (parked):** typed selectors + T + relaunch
  cover it (015 D-c made selectors discoverable).
- **Real-binary probe flake (documented 2026-08-10):**
  `test_scope_probe.RealPinnedBinaryTests` can time out under machine-wide
  load; passes isolated. Signature + response documented in AGENTS.md §4.
- `cli.py` module size; `probe.py` weight; wide-char cell math;
  hook-delivery invisibility; ● marker is a heuristic (degrades to absent).

## Done this cycle (D50, blueprints 014–018; activated gen 120)

- 016 qwen3.8 production flip (D21 sequence complete incl. the live call)
- 015 MRU pick order · `--composition-file` · typed selectors · doctor
  gateway radar (aliases-only served check, config byte-drift, OAuth
  disambiguation — the last three shaped by cross-family review)
- 018 providers pane (P in G) with masked key entry
- 014 editor ^O save chord · 017 readable muted color
- Sweep fixes: QUICK_HELP H/U, gateway-down start hint, secret problems
  name the env file path, `models` prints selectors, HANDOFF pin/wording
