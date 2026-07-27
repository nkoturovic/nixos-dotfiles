# HANDOFF — daily operation of claude-multi

This is the **daily-operations** note: how to run and maintain the installed
system. Picking the project up cold (state, evidence, next work)? Go to the
current checkpoint instead:
[`checkpoints/`](checkpoints/README.md) → latest `handoff/README.md`.

## What this is

`claude-multi` compiles a chosen composition into per-session durable files
(`~/.local/state/claude-multi/scopes/<managed-id>/{.claude/agents/*.md,
settings.json}`) and launches ordinary Claude Code pointed at them with
`--add-dir` + `--settings`. Agent definitions live on disk and are
re-discovered on every process start (proven across supervisor takeover in
production). Stable scope identity is separate from Claude's runtime resume
UUID and reconciled by SessionStart hooks through the stable hook shim
(`~/.local/state/claude-multi/bin/claude-multi-hook`). Plain `claude` is
untouched; `claude-gateway` is the ordinary non-composition entrypoint.

## Daily use

- `claude-multi` → composition card → **Enter** to launch.
  **Tab/P** cycles presets (`default`, `kimi-sol`, `qwen-sol`, `sol-direct`),
  **W** toggles workflows native/off, **S** opens the sessions picker,
  **?** explains workflow guarantees, **E** edits the composition,
  **H** runs doctor in place, **U** appears when a Claude update is
  available and re-pins in place. The card's health strip always shows
  gateway status and the pinned version; **Esc** cancels everywhere.
- `claude-multi -c` resumes the last managed composition in this directory;
  `-r <managed-id|runtime-id|name>` resolves exactly; bare `-r` opens the
  unified picker (managed + native; **L** adopts, **C** filters by cwd,
  **X** resolves a fork; row markers **●** = live/background-owned,
  **⚠** = fork-blocked).
- Native forks (a session forked by reattaching to a background-owned
  session) block the parent's resume until decided: **X** in the picker,
  `sessions resolve-fork <parent> <fork>` to discard, or `sessions link
  <fork> --composition <name>` to adopt. A marker whose fork IS the live
  branch self-clears; `doctor --repair-all` clears it too. Fork transcripts
  are never deleted.
- **Stop a live (●) session:** **E** on the picker row, or
  `claude-multi sessions stop <uuid> [--yes]` — upstream `claude stop` via
  the verified binary (never a signal; conversation always kept). Refuses
  self-stops and non-live sessions. Transitions on a ● session warn first.
- `claude-gateway [--model sol|qwen38|kimi-k3|fable|opus]` starts an ordinary
  gateway session with native `/model`; `-c`/`-r` continue it. Same-profile
  switching stays in-process, cross-profile switching is an explicit relaunch.
- `claude-multi sessions transition <uuid> --composition <name>` changes a
  session's composition: shows the diff, asks you to confirm the old process
  has exited, relaunches with the exact transcript.
- `claude-multi doctor` — health: **Ready**, **Attention** (by-design lazy
  state with the exact fix command), or **BLOCKED** (real damage).
  `doctor --repair-all` converges every durable session and refreshes record
  snapshots in one failure-isolating pass (the older-session answer);
  `doctor --repair <uuid>` does one; `doctor --prune` sweeps stale generated
  files (never transcripts); `sessions relink-runtime <id> <runtime-id>`
  repairs pre-hook UUID drift using native `/status`; `sessions link`
  adopts plain sessions; `sessions forget` deletes record + generated scope
  (never the transcript).
- `--legacy` = old argv mode (compatibility hatch, not a durability answer).

## Installed state (2026-07-27)

- claude-multi **2.6.3** active (HM generation 106; rollback: gen 105/104),
  Claude pinned at **2.1.220** (hash-verified, symlink-aligned; the update
  flow proved itself end-to-end against the real candidate).
  Fork lifecycle operable (⚠/● markers, X resolve, `sessions resolve-fork`),
  gateway routing durable across daemon takeovers (apiKeyHelper shim),
  updates narrate per phase and serialize through a lock.
- **Opus 5 is the default lead** (default = opus5+sol+kimi; profiles
  `opus-sol`, `opus-kimi`, `fable`, `kimi-sol`, `qwen-sol`, `sol-direct`
  all live); gateway serves `claude-opus-5` and `claude-multi-opus-5`
  (CLIProxy registry patched); binary pinned at 2.1.220 and symlink-matched.
- `claude-multi doctor` → **Ready, zero Attention lines**; 17 durable
  sessions; flows re-verified live (checkpoint state-snapshot §flows).
- Claude updates are routine: the card badge or doctor Attention appears →
  press **U** (or `claude-multi update`) → instant effect via the operator
  override (`--activate` for the baseline refresh).
- Full evidence and census: the current checkpoint's
  [`handoff/state-snapshot.md`](checkpoints/2026-07-27-v2.6.3/handoff/state-snapshot.md).

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

## Working agreements for sessions working on this project

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
7. **Keep the map current**: `STATUS.md` at milestones; next checkpoint at
   meaningful boundaries; one canonical home per topic (see the
   "Documentation map" in this package's README).

## Open items (as of 2026-07-27)

Ordered in the current checkpoint: [`checkpoints/2026-07-27-v2.6.3/handoff/open-items.md`](checkpoints/2026-07-27-v2.6.3/handoff/open-items.md).

## Where things live

- **This package** (`docs/claude-multi-final/`): design docs + STATUS.md
  (ledger) + SANITY.md (assessment) + `checkpoints/` (state entry points).
- **Product**: `home-manager/claude-multi/` — canonical docs `AGENTS.md`
  (development) + `USAGE.md` (use); `.agents/` is a scratch workspace only.
- **History**: `docs/claude-multi-rethink-handoff/` (frozen C\* + artifacts).
- **Wikis** (pointers, not copies): `~/.agents/wiki/projects/claude.md`,
  `~/.claude/.agents/wiki/index.md`.
