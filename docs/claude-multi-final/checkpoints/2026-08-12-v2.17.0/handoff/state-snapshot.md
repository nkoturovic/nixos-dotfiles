# State snapshot — 2026-08-12 · v2.17.0

Evidence behind the checkpoint README claims. Verify before trusting.

## Activation

- `home-manager switch --flake /home/kotur/personal/nixos-dotfiles#kotur`
  → exit 0; HM generation **124** current (123/122 rollback);
  `cli-proxy-api.service` restarted by the activation.
  `claude-multi --version` → `claude-multi 2.17.0`.
- `claude-multi doctor --repair-all` → **33 durable sessions converged to
  record authority** (catalog 17, including the D56 sol fence);
  re-run `claude-multi doctor` → **Ready** (catalog/compositions/gateway
  valid; radar silent).
- Live smoke: `compose list` shows `deepseek` + `grok-deepseek`;
  `models` lists deepseek-flash + grok45 with selectors.

## Probe battery (approval-gated, 2026-08-12, ~10 tiny calls)

- DeepSeek listing: Anthropic path `/anthropic/v1/models` → **404**; the
  documented OpenAI-shape `GET https://api.deepseek.com/models` (Bearer)
  → 200 with `{deepseek-v4-flash, deepseek-v4-pro}` (ids only).
- DeepSeek smoke: 200 with x-api-key AND Bearer; canonical thinking+text
  blocks; correct answer.
- OpenRouter public lookup: grok-4.5 = 500K context; reasoning mandatory
  with supported_efforts [high, medium, low] (high is the top — matches
  the shipped single-high-lane); max completion unpublished.
- OpenRouter skin: well-formed tool_use (stop_reason tool_use, parsed
  input, thinking + redacted_thinking blocks); streaming event order
  canonical (no text-before-thinking inversion — the empty-result failure
  mode absent); `output_config.effort` high+max and `reasoning.effort`
  accepted (200).

## The D56 pinpoint (sol prompt-too-long)

Symptom (operator-reported, recurring): sol subagents visibly work, then
fail mid-turn with "Prompt is too long" / "Input prompt is too long".
Binary-verified: Claude Code's `zW="Prompt is too long"` is raised on
UPSTREAM 400s (`/prompt is too long[^0-9]*(\d+) tokens? > (\d+)/i`).
Root cause: the codex OAuth route's budget was cut in July 2026 —
v0.144.5-era 372K (500K = ~372K + 128K reserve) → v0.144.6 metadata
272K → server-observed 95% effective ≈ 258,400. Our 372K fence's
reactive trigger (~316.8K) let agents grow past the real ceiling. The
deep review's sol-xhigh catalog reviewer died of exactly this class
mid-run (its own 89-tool-call review). Correction: sol + gpt55 fence at
258,400 (trigger 214,560); revert path recorded in the qualification.

## Review story

- Sweep 1 (pre-probe): glm52 approve (nits fixed); sol-xhigh revise →
  slash-safe registry keys, listing parse hardening (missing `data` ≠
  empty success, bool≠int, RecursionError wrapped), immutable
  descriptors; exposed a 020-shipped defect (SelectList multi-mode could
  never return a selection — the fetch-mark flow was dead).
- Sweep 2 (deep workflow, adversarial verify): glm52 approve; qwen38
  revise → confirmed: invisible fetch-mark toggles (SelectList renders
  markers from the widget-tracked set), listing-modal URL accuracy,
  stale ledger lines. Refuted: on_toggle ordering hazard (no caller
  mixes the contracts), think_efforts-unconsumed (discover consumes it).
  The catalog reviewer (sol-xhigh) died with "Prompt is too long" — the
  D56 class on our own tooling.

## Test/build evidence

- Full host discovery: `Ran 1662 tests … OK (skipped=2)` on the committed
  tree (includes the 022 pins: listing descriptors, grok fence math,
  selector forms, composition resolution, direct launches, wire-pattern,
  route-wire guard, fetch-mark end-to-end, marker rendering).
- Sandbox: `nix build --no-link --file tests/default.nix` green.

## Commits (feature/term-only, nothing pushed)

- `216c434`/`e5d2409` — 2.16.0 + activation docs (gen 123).
- `1ff5a1e` — 2.17.0 providers batch (D54/022).
- `c08dd77` — probe results landed.
- `2f49567` — review resolutions + D55 multi-route + D56 sol correction.
- This checkpoint: committed on top.

## Composition/registry census

16 user compositions (incl. `deepseek`, `grok-deepseek`) + trusted
`default`; custom registry empty; MRU head at activation:
`kimi-sol-qwen-glm`.
