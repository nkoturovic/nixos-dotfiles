# HANDOFF — starting a qwen-sol session

This note hands daily operation of `claude-multi` to a **qwen-sol** session
(Qwen3.8 Max lead). Read this first; it is short on purpose.

## What this is

`claude-multi` compiles a chosen composition into per-session durable files
(`~/.local/state/claude-multi/scopes/<uuid>/{.claude/agents/*.md,
settings.json}`) and launches ordinary Claude Code pointed at them with
`--add-dir` + `--settings`. Agent definitions live on disk and are
re-discovered on every process start (documented for backgrounded/respawned
sessions and binary-consistent for supervisor takeover; the final takeover
proof is the next natural upgrade — see U1 below). Plain `claude` is
untouched.

## Daily use

- `claude-multi` → composition card → **Enter** to launch.
  **Tab** cycles presets (`default`, `kimi-sol`, `qwen-sol`, `sol-direct`),
  **W** toggles workflows native/off, **S** opens the sessions picker,
  **?** explains workflow guarantees, **E** edits the composition.
- `claude-multi -c` resumes the last session in this directory;
  `-r <uuid|name>` resumes exactly (names list candidates newest-first);
  bare `-r` opens the picker.
- `claude-multi sessions transition <uuid> --composition <name>` changes a
  session's composition: shows the diff, asks you to confirm the old process
  has exited, relaunches with the exact transcript.
- `claude-multi doctor` — health; `doctor --repair <uuid>` reconverges a
  scope; `doctor --prune` removes stale generated files (never transcripts).
- `--legacy` = old argv mode (compatibility hatch, not a durability answer).

## Composition: qwen-sol

Qwen3.8 Max Preview lead (ultracode, thinking always on, `reasoning_effort: xhigh`
— the provider maximum), Sol preferred analyst/implementer/reviewer
variants, Qwen-max alternates. Cross-family review: Sol (openai) reviews
Qwen (alibaba) work and vice versa. Context bound 372K (conservative;
Qwen supports 983,616 but the composition takes the minimum across its
models). Wire: Token Plan `apps/anthropic`, bearer auth, key in
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

## Open items (as of 2026-07-23)

- **U1 takeover proof**: `--add-dir` carry is documented for backgrounding
  and binary-consistent; final proof is the next natural Claude upgrade
  (2.1.218 just appeared). Watch the first post-upgrade durable session —
  its `cm-*` roster should be intact.
- **Qwen preview → production**: when `qwen3.8-max` ships, revise
  `wire_model`, re-verify context, re-check `reasoning_effort` tiers, one
  live call, drop "· Preview" from the display (DECISIONS D21).
- **`sol-direct` preset exists** for raw Sol consultation; `qwen-sol` is
  the daily workhorse.

## Where things live

- Design package + live tracker: `docs/claude-multi-final/` (STATUS.md is
  the ledger — update it at milestones).
- Product: `home-manager/claude-multi/` (catalog, src, tests, README).
- History: `docs/claude-multi-rethink-handoff/` (frozen C\* + artifacts).
