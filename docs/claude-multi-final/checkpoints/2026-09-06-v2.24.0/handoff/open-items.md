# Open items (ordered)

1. **LAN alias decision** (yours — the server is yours): add
   `--alias qwen3.8-flash-next` to the bt-lab-02 production deploy
   (recommended: matches `loadtest.sh`, stable across profiles), or tell
   the launcher to wire the GGUF-path id (rots on profile change).
2. **Approval-gated probes**, one at a time, bounded: LAN listing
   re-check (after the alias), LAN acceptance + near-limit; Meta probe
   with tools/schema case (issue 028); Astra near-limit (optional).
3. **Activate 2.24.0/catalog23** — green light needed: `home-manager
   switch` (rollback anchor gen 135) → healthz → doctor Ready → create
   the three presets via CompositionStore → zero-live census.
4. **Push** `feature/term-only` (unpushed) — explicit approval needed.
5. **#324 close-out** — confirm the Astra-batch docs sweep is covered by
   the D62/D63 ledgering + checkpoints, then close.
6. **Remaining optionals** — extra presets, TUI reconsideration, 2.1.261
   re-pin.
