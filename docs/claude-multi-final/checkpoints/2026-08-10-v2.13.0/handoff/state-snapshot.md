# State snapshot — 2026-08-10 · v2.13.0

Evidence behind the checkpoint README claims. Verify before trusting.

## Activation

- `home-manager switch --flake /home/kotur/personal/nixos-dotfiles#kotur`
  → exit 0; `cli-proxy-api.service` stopped/started (re-rendered config
  applied). HM generation **120** current (119/118 rollback).
  Active package via
  `/nix/store/0ji5jlgak4f23lvj0lv1j6cirxhz0dnj-home-manager-path`.
- `claude-multi --version` → `claude-multi 2.13.0`.
- Pre-activation doctor was BLOCKED on exactly the expected lazy-state
  class: 4 record↔scope generation mismatches + catalog-drift scope notes
  (catalog 14 → 15). `claude-multi doctor --repair-all` → "33 durable
  session(s) converged to record authority"; re-run → **Ready**.
- Post-activation doctor: **Ready**; `Managed Claude 2.1.220 verified
  (sha256 674f61f20ff3…)`; symlink resolves to the inspected artifact;
  gateway radar silent (served == rendered; no config drift; no OAuth
  gaps); 33 recorded · 33 durable · 0 legacy.

## Live acceptance (approval-gated, the D21 sequence's step 4)

- One bounded call: `POST http://127.0.0.1:8317/v1/messages` with
  `model=claude-multi-qwen38-max`, `max_tokens` 32→64 → HTTP 200,
  `stop_reason` end_turn/max_tokens, real usage counters; thinking block
  shows the upstream production identity ("you are Qwen3.8"). Journal
  `selector.go` lines show the alias binding (`model=claude-multi-qwen38-max`).
- Structural grep of the live config (names only, never values):
  `- name: "qwen3.8-max"` present; `qwen3.8-max-preview` count = 0.
- Near-limit context behavior remains unverified (open-items).

## Review evidence (pre-activation)

Cross-family fan-out + adversarial verification (ultracode workflows):

- **sol-xhigh on 015**: verdict revise — 3 must-fix (wire names in the
  expected set would BLOCK a healthy gateway forever — verified live with
  a disposable loopback proxy; MRU here/elsewhere duplication; same-named
  composition file bypassing the resume refusal) + 1 should-fix (unbounded
  stdin read). All fixed and re-pinned in the same tree.
- **glm52 on 014/016/017**: verdict approve; nits addressed (240 light
  gray; narrowed preview pin; Table-header dim pin; blueprint amendments).
  ^O delivery empirically verified on a Fedora pty (^S swallowed by the
  line discipline; ^O delivered, no output discard).
- **qwen38 full-system sweep**: GOOD + lows (all addressed — QUICK_HELP
  H/U, gateway-down start hint, secret-path naming, `models` selectors,
  HANDOFF stale pin/preview wording; the one medium = wire-only stale
  detection, closed by the config byte-drift check).
- The adversarial verifier re-checked the critical finding against the
  final tree and confirmed it corrected (served == rendered live: 11
  aliases; expected-minus-served empty).

## Test/build evidence

- Full host discovery: `Ran 1530 tests … OK (skipped=2)` (from 1,469
  pre-batch; +61 new pins).
- Sandbox: `nix build --no-link --file home-manager/claude-multi/tests/default.nix`
  green for the 2.13.0 derivation (before and after review fixes).
- One known flake class documented (RealPinnedBinaryTests under load;
  AGENTS.md §4) — both full runs here were green.

## Live smoke (post-activation)

- `compose list` → MRU-first with last-used ages (`kimi-sol-qwen-glm`
  8h ago first — this session's own composition).
- Line-mode `g` listing providers section (live gateway):
  `anthropic · OAuth pool · 1 credential record · 5 rendered · 5/5 served`,
  `kimi · direct key · KIMI_CLAUDE_API_KEY present · 1 rendered · 1/1 served`,
  `openai · OAuth pool · 1 credential record · 3 rendered · 3/3 served`,
  `qwen · direct key · QWEN_CLAUDE_API_KEY present · 2 rendered · 2/2 served`.

## Compositions census (14 user files, all resolve on catalog 15)

`fable`, `fable-sol-glm-qwen`, `fable-sol-glm-qwen-opus`,
`fable-sol-qwen-glm`, `glm-sol`, `kimi-sol`, `kimi-sol-qwen`,
`kimi-sol-qwen-glm`, `kimi-sol-qwen-glm-fable`, `opus48-sol-glm-qwen`,
`opus-kimi`, `opus-sol`, `qwen-sol`, `sol-direct` (+ trusted `default`
seed). The three claude-multi-authored pool profiles carry qwen38 ahead of
glm52 (D45 note executed); operator-authored glm-first files deliberately
untouched.

## Commits (feature/term-only, nothing pushed)

- `d7099f9` — the 2.13.0 batch (27 files, +2,326/−89).
- `cd4c41d` — docs: 2.13.0 activated (HM gen 120).
- This checkpoint: committed on top.
