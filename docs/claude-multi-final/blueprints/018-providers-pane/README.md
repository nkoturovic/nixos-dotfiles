# 018 — Providers pane (inside G): status, connect guidance, masked key entry

## Context

Operator ask: a TUI pane for connecting providers and choosing active models —
"consider whether it is feasible… a pane/page in the TUI for connecting
providers, choosing active models"; later steers: in-pane connect is desired,
and secrets still land in `~/.config/secrets/claude.env`. Evaluated by a
four-lens design workflow (UX / gateway / simplicity-critic reports; the
secrets lens died on context overflow and was finalized by the lead from the
same evidence base). All lenses converge: **no new top-level screen** — enrich
the existing owners instead.

## Decision (lead synthesis)

**The G ordinary picker IS the models page; the providers pane hangs inside
it.** No new card key, no global "active models" state (that would be a third
intent authority beside compositions and ordinary records — D3), no gateway
mutations, no live provider probes, no management API.

1. **P providers** key inside `_OrdinaryScreen` (both launch and switch
   purpose) opens `_ProvidersScreen`: a read-only status matrix over the
   shared `GatewaySnapshot` (015 D-d) — per provider: transport kind,
   credential-source state (secret present/missing by NAME; OAuth
   credential-record count, names never shown), rendered/served selector
   counts, an honest state word, and the exact remediation text.
2. **Masked key entry for direct providers** (operator steer): Enter on a
   direct provider row opens a confirm modal with a MASKED TextInput (new
   `mask` option on `tui.TextInput`); saving writes the standard env file via
   `proxy.set_secret_value` — replace the `KEY=` line byte-preservingly or
   append, `state.atomic_write` 0600, symlink-refusing, value validated against
   the existing `_VALUE_SHAPE`. The value is never echoed, logged, or shown in
   a frame; the confirmation shows name + length only (secrets agreement).
   After saving, the pane shows the apply commands (`claude-multi-proxy init`
   + `systemctl --user restart cli-proxy-api`) — never auto-run in v1.
   OAuth pools get the login command (`claude-multi-proxy claude-login` /
   `codex-device-login`) — driving OAuth inside curses is out of scope.
3. **Connect hint at the friction point** (critic's survivor): the G
   missing-secret confirm modal and detail line gain the exact connect
   instruction per transport; line-mode `_print_ordinary_listing` mirrors with
   a providers section; doctor secret-problem lines already name the env file
   path (done in the sweep-fix batch).
4. **Honesty vocabulary** everywhere: `secret present` / `rendered` /
   `served` are separate claims; "served" means *registered by the running
   local gateway* — never "connected/authenticated/quota". Gateway down →
   route fields say `unknown`, not `not served`.
5. **Doctor hardening (already landed with 015 D-d fixes)**: shared
   `_gateway_snapshot`; config-drift byte check (covers wire-only remappings
   like 016, invisible to /v1/models); OAuth no-record → login guidance
   instead of restart; credential-record presence disambiguates restart vs
   login.

## Explicitly rejected (with the lenses)

- New card key / standalone screen (duplicates G/E/H; keybar pressure, D30).
- Global active-model toggles (third intent authority; 015 already rejected
  the generator form).
- `claude-multi discover PROVIDER` live list calls (unverified endpoints; no
  consumer; approval machinery).
- Enabling CLIProxyAPI management endpoints to power the pane (attack
  surface + second config authority).
- Auto `init`/auto-restart from the TUI (in-flight sessions could break).
- Displaying/copying secret values anywhere.

## Files

- `cli.py`: `_ProvidersScreen` (new, near `_OrdinaryScreen`), P key in
  `ORDINARY_KEYBAR`/`ORDINARY_KEYBAR_SWITCH`, connect-hint text in the
  missing-secret confirm modal + `_print_ordinary_listing` providers section,
  `GatewaySnapshot` reuse.
- `proxy.py`: `set_secret_value(path, name, value)` (parse-preserving,
  0600 atomic, shape-validated).
- `tui.py`: `TextInput(mask=…)` render-only change.
- Tests: `ProvidersScreenTests` (rows/honesty/refresh/oauth guidance),
  masked-entry tests (writes file 0600, value never in any frame, invalid
  rejected, existing lines preserved), listing/doctor parity.
