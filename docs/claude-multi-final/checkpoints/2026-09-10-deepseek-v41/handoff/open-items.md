# Open items (ordered) — nothing here is blocked on effort

1. **Operator testing** — the operator took testing over; no probe work is
   outstanding on the agent side.
2. **Issue 029 — the 2.1.261 re-pin.** Two fix attempts failed; the
   **keystroke was ruled out** (nine variants, identical failure), so the
   next step is a raw transcript diff of the 2.1.261 run against 2.1.220 to
   find the first screen-sequence divergence — *not* an interaction-table
   edit. Pin stays 2.1.220; the gate is failing closed correctly. The
   committed `trust_dialog_answer` helper (`ce033cf`) is correct on its own
   terms and non-regressing; keep or revert on operator preference.
3. **Issue 028 — Meta strict-schema 400** (goal evaluator). Needs one
   bounded, per-call-approved probe folding into the §6b Meta probe; then
   the provider support-note divergence list can be extended.
4. **Near-limit probes** (LAN, Meta, Astra) — real provider calls,
   per-call approval per `AGENTS.md` §6.
5. **TUI / interface reconsideration** — the operator's standing holistic
   ask; needs a scope direction before it is more than an open item.
6. **Output tokens** — revisit only on an observed `stop_reason:
   max_tokens` (or visibly truncated output) on a DeepSeek lane; that
   evidence would justify adding an output field and compiling the reserved
   key. Until then, no change.
7. **DeepSeek Pro wire** — after **2026-09-14 04:00 UTC** the
   `deepseek-v4-pro` wire serves V4.1-Flash at Flash prices. Re-check then
   whether to keep the Pro entry, and drop its labels to match, when
   V4.1-Pro actually ships.
