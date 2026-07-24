# HANDOFF — starting a qwen-sol session

This note hands daily operation of `claude-multi` to a **qwen-sol** session
(Qwen3.8 Max lead). Read this first; it is short on purpose.

## What this is

`claude-multi` compiles a chosen composition into per-session durable files
(`~/.local/state/claude-multi/scopes/<managed-id>/{.claude/agents/*.md,
settings.json}`) and launches ordinary Claude Code pointed at them with
`--add-dir` + `--settings`. Agent definitions live on disk and are
re-discovered on every process start (documented for backgrounded/respawned
sessions and binary-consistent for supervisor takeover; the final takeover
proof is the next natural upgrade — see U1 below). Stable scope identity is
separate from Claude's runtime resume UUID and reconciled by SessionStart
hooks. Plain `claude` is untouched; `claude-gateway` is the ordinary
non-composition multi-model entrypoint.

## Daily use

- `claude-multi` → composition card → **Enter** to launch.
  **Tab** cycles presets (`default`, `kimi-sol`, `qwen-sol`, `sol-direct`),
  **W** toggles workflows native/off, **S** opens the sessions picker,
  **?** explains workflow guarantees, **E** edits the composition.
- `claude-multi -c` resumes the last managed composition in this directory;
  `-r <managed-id|runtime-id|name>` resolves exactly; bare `-r` opens the
  unified picker.
- `claude-gateway [--model sol|qwen38|kimi-k3|fable|opus]` starts an ordinary
  gateway session with native `/model`; `-c`/`-r` continue it. Same-profile
  switching stays in-process, cross-profile switching is an explicit relaunch.
- `claude-multi sessions transition <uuid> --composition <name>` changes a
  session's composition: shows the diff, asks you to confirm the old process
  has exited, relaunches with the exact transcript.
- `claude-multi doctor` — health; `doctor --repair <uuid>` reconverges a
  scope; `doctor --repair-all` converges every durable session and refreshes
  record snapshots in one pass (the older-session answer); `sessions
  relink-runtime <managed-id> <runtime-id>` repairs pre-hook UUID drift using
  the ID shown by native `/status`; plain sessions adopt with `sessions link
  UUID --composition NAME|--model MODEL [--cwd PATH]`; `doctor --prune`
  removes stale generated files (never transcripts). Doctor separates
  **BLOCKED** (real damage) from **Attention** (by-design lazy state with the
  exact fix command).
- `--legacy` = old argv mode (compatibility hatch, not a durability answer).

## Live repair (run once, after activating 2.3.0)

The 2026-07-24 audit found the state root healthy (no corrupt records) but
carrying rebuild drift: 20 scopes with stale hook paths, 21 records with
legacy context snapshots. **Steps 3–4 below were already executed on
2026-07-24 with the built 2.3.0 package** (backup at
/tmp/cm-live-backup-20260724-110010): all 27 durable scopes now embed the
stable hook shim and doctor reports Ready. Remaining:

1. `home-manager switch --flake /home/kotur/personal/nixos-dotfiles#kotur`
   (activation needs your approval; it also restarts the gateway). Until
   activation the profile launcher (2.2.0) reports BLOCKED against the
   shim-migrated scopes — expected; do not run 2.2.0 repairs in between.
2. Optional hygiene: `sessions forget` the records you no longer need
   (transcriptless ones are safe: 9d52543a, 394f7123, f6e66f1e, e7b4a3d7,
   fb733666, b32536cd, 78ccb9a2, 620f73f3, 4b1b39e3, ba6a0f50, ff137da1,
   plus legacy 339425fb, 3fe4293d, 783f524f); `doctor --prune` afterwards.
   af2e51a2 resumes only from /home/kotur/projects/occams-agent-flow.
3. Verify: `claude-multi doctor` shows Ready (possibly with Attention notes
   for the legacy records you kept). Only then expire Home Manager
   generation 95 — its store path is referenced by the oldest live hooks
   (the running a24fc875 session's start-time settings).

## Composition: qwen-sol

Qwen3.8 Max Preview lead (ultracode, thinking always on, `reasoning_effort: xhigh`
— the provider maximum), Sol preferred analyst/implementer/reviewer
variants, Qwen-max alternates. Cross-family review: Sol (openai) reviews
Qwen (alibaba) work and vice versa. Sol variants retain the process-wide 372K
scalar, while the `[1m]` Qwen lead uses a provider-safe 983,616 compaction
capacity plus the explicit 90% override. Pinned 2.1.217 reserves 20K output,
so the deterministic reactive trigger is 867,254; proactive preparation is
runtime-controlled and may occur earlier. Wire:
Token Plan `apps/anthropic`, bearer auth, key in
`~/.config/secrets/claude.env` (`QWEN_CLAUDE_API_KEY`).

## Working agreements for the new session

1. **No real-provider calls without explicit user approval, per call.**
   The only sanctioned automated call shape is one bounded request at a
   time, like the verification call recorded in STATUS.md.
2. **Never touch the live Claude daemon/supervisor**; never read user
   transcripts; never delete anything under `~/.claude` or a transcript-
  bearing root. Rollback never deletes state.
3. **Tests before claims**: focused suites while working, full discovery
   (`PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .`
   from `home-manager/claude-multi/`) before declaring done. Sandbox:
   `nix build --no-link --file home-manager/claude-multi/tests/default.nix`.
4. **Commit discipline**: coherent commits on `feature/term-only`, no
   push without approval. Home Manager activation needs user approval
   (`home-manager switch --flake /home/kotur/personal/nixos-dotfiles#kotur`).
5. **Review cadence**: authors self-verify substantial milestones; a
   cross-family reviewer (Sol when the lead is Qwen/Kimi) gets one batched
   pass at meaningful boundaries — no review loops.
6. **Simplicity budget**: no new mode/schema/daemon/state without a
   demonstrated failure case. Prefer deletion over addition.

## Open items (as of 2026-07-24)

- **U1 takeover proof**: the natural upgrade arrived — the shared supervisor
  now runs 2.1.218 and has already taken over backgrounded managed sessions.
  Watch the first post-takeover durable session: its `cm-*` roster should be
  intact (the durable-scope design exists for exactly this).
- **Re-pin to Claude 2.1.218**: the contract still verifies 2.1.217 while
  plain `claude` and the supervisor run 2.1.218. Re-pinning is a bounded
  evidence task: probe-init the 2.1.218 artifact, re-run the offline probe
  suite (compaction hooks, delegation) against it, promote the new
  native-contract through the draft → review → promote pipeline. Until then
  2.1.217 remains the verified launch artifact and drift is advisory-only.
- **Qwen preview → production**: when `qwen3.8-max` ships, revise
  `wire_model`, re-verify context, re-check `reasoning_effort` tiers, one
  live call, drop "· Preview" from the display (DECISIONS D21).
- **Lower-context delegated prompts:** native agents under a Sol lead inherit
  372K. Keep skill/system payloads bounded or route broad work to an enabled
  1M `cm-*` variant; the generated lead appendix now states this explicitly.

## Where things live

- Design package + live tracker: `docs/claude-multi-final/` (STATUS.md is
  the ledger — update it at milestones).
- Product: `home-manager/claude-multi/` (catalog, src, tests, README).
- History: `docs/claude-multi-rethink-handoff/` (frozen C\* + artifacts).
