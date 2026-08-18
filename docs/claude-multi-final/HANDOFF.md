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
  **Tab/P** cycles presets most-recently-used first (e.g. `default`,
  `kimi-sol`, `qwen-sol`, `glm-sol`, `sol-direct` — full table in USAGE.md),
  **G** opens the ordinary gateway picker (profile-grouped models, typed
  `/model` selectors per row, **P** inside it = providers pane with local
  status + masked key entry + connect instructions),
  **W** toggles workflows native/off, **S** opens the sessions picker,
  **?** explains workflow guarantees, **E** edits the composition (**^O**
  there opens Save-or-launch from anywhere),
  **H** runs doctor in place, **U** appears when a Claude update is
  available and re-pins in place. The card's health strip always shows
  gateway status and the pinned version; **Esc** cancels everywhere.
- `claude-multi -c` resumes the last managed composition in this directory;
  `-r <managed-id|runtime-id|name>` resolves exactly; bare `-r` opens the
  unified picker (managed + native, opening on **this directory's**
  sessions; **L** adopts, **T** switches composition (managed) / model
  (gateway rows), **C** widens to all directories,
  **X** resolves a fork; row markers **●** = live/background-owned,
  **⚠** = fork-blocked).
- **Debug skills (reusable, in `~/.claude/skills/`):** `model-routing-debug`
  (named-vs-actual verification: gateway selector journal, process argv,
  scope fence, the degraded-dispatch playbook), `session-forensics`
  (metadata-only session inspection, fork/● workflows, safe record
  surgery), `gateway-ops` (health, config parity, restart rule, journal
  patterns, new-model checklist). Use them before inventing ad-hoc steps.
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
- `claude-gateway [--model MODEL]` starts an ordinary
  gateway session with native `/model` (MODEL is a catalog or custom-registry
  id — `claude-multi models`, `claude-multi custom list`); `-c`/`-r` continue
  it. Same-profile switching stays in-process, cross-profile switching is an
  explicit relaunch.
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
  (never the transcript) and refuses a live session (stop it first) or the
  one you're inside.
- `--legacy` = old argv mode (compatibility hatch, not a durability answer).

## Installed state (2026-07-29)

- claude-multi **2.18.0** active (HM generation 125; rollback: gen 124/123),
  Claude pinned at **2.1.220** (hash-verified, symlink-aligned).
  Resume gate live (D41): repair-needed records get one-keypress Repair &
  resume in the TUI, daemon-owned resumes gate with Stop & resume /
  Resume anyway / Cancel, missing transcripts are named before exec —
  never a bare native error. Managed sessions pin watchdog retry
  (`CLAUDE_CODE_RETRY_WATCHDOG=1`, D42): transient upstream errors
  recover automatically — subagents and backgrounded turns no longer die
  waiting for a typed "continue".
- **Opus 5 is the default lead** (default = opus5+sol+kimi; profiles
  `opus-sol`, `opus-kimi`, `fable`, `fable-sol-qwen-glm`, `kimi-sol`,
  `kimi-sol-qwen`, `kimi-sol-qwen-glm`, `kimi-sol-qwen-glm-fable`,
  `qwen-sol`, `glm-sol`, `sol-direct`, `deepseek`, `grok-deepseek`
  all live); gateway serves `claude-opus-5` and `claude-multi-opus-5`
  (CLIProxy registry patched); binary pinned at 2.1.220 and symlink-matched.
- **DeepSeek + OpenRouter providers** (2.17.0, D54): `deepseek-flash`
  (wire `deepseek-v4-flash`, the latest-alias; 1M, `large` profile, lanes
  high/max with `output_config.effort` pinned per lane — budget_tokens is
  ignored upstream) and `grok45` (wire `x-ai/grok-4.5` via the Anthropic
  skin; 500K in its own `grok` profile — window from the scope env, no
  `[1m]`). Compositions: `deepseek` (all-flash side-task rig),
  `grok-deepseek` (grok lead + flash agents, cross-family review).
  Listing: deepseek lists via the documented OpenAI-shape `GET /models`
  (Bearer — verified 2026-08-12; the Anthropic path 404s); openrouter
  lists via the PUBLIC OpenAI-shape `GET /api/v1/models` (no key).
- **GLM-5.2 live on the qwen provider** (2.9.0, D45): wire `glm-5.2` at the
  Token Plan endpoint, selector `claude-multi-glm52-max[1m]`, lead+agents,
  lane max with `reasoning_effort: "max"` — 1M context, alibaba family
  (Sol/Kimi stay the cross-family reviewers). `glm-sol` = GLM lead;
  `kimi-sol-qwen-glm` = Kimi lead with GLM/Qwen comparable alternates.
- **Ordinary sessions launch from the card** (2.10.0, D46): **G new gateway**
  opens a profile-grouped model picker — the TUI twin of `claude-gateway`.
  `(no secret)` rows reflect render-time availability; Enter rechecks and
  asks before launching anyway. CLI `direct` warns (non-blocking) on a
  missing provider secret; line-mode `g` lists the groups.
- **D47 batch live** (2.11.0): sessions screen opens **cwd-filtered** (C
  widens); session names carry the project (`cm:kimi-sol@project`,
  `cg:glm52@project`; resume-by-name accepts the qualified form); role
  prompts now bound nested delegation (cm-* types only, TaskStop ownership,
  all 20 scopes converged via `doctor --repair-all`); `/model` docs match
  the native 2.1.220 display filter (allow-list ≠ displayed subset).
- **Ordinary model switch live** (2.12.0, D48): **T** on a gateway row
  switches model in the TUI — picker preselected on the current model,
  same/cross-profile confirm, full resume gate afterwards (R parity).
  Switch paths for ordinary sessions: typed `/model <selector>`, **T** in
  the picker, or `claude-gateway -r <id> --model X`. Sessions screen
  empty state names the filter when sessions exist elsewhere.
- **2.13.0 batch live** (D50; blueprints 014–018; gen 120): qwen38
  production wire (`qwen3.8-max`, catalog 15) + qwen38-ahead-of-glm52 slot
  order in the three authored profiles; MRU-first composition picking
  everywhere (derived from records — no new state); `--composition-file`
  on-the-fly ingestion; **P providers pane** in the G picker (status,
  connect instructions, masked key entry to the standard env file); doctor
  gateway radar (served-vs-rendered aliases + config byte-drift + OAuth
  record disambiguation); typed `/model` selectors on picker rows; editor
  **^O** save chord; readable dark-theme muted color. Live acceptance call
  proved `qwen3.8-max` routing; `doctor --repair-all` converged all 33
  durable sessions; doctor Ready.
- **2.15.0 live** (D52; blueprint 020; gen 122): custom
  providers & ordinary models via the `custom.json` registry — N/A/D
  flows in the providers pane (fetch-mark with advertised context bounds
  for Kimi, manual type-in anywhere), per-bound picker groups, doctor
  radar coverage automatic; compositions untouched by design. Plus the M
  models browser with the E enable-jump and the G "gateway models" rename.
- **2.14.0 improvement pass live** (gen 121) (D51; blueprint
  019): BLOCKED cards point at the in-TUI key fix; OAuth `(sign in needed)`
  row marking; line-mode H; hardened secret writes (locked + re-parsed);
  providers-pane drift banner + help; editor BLOCKED feedback; `claude-multi
  discover PROVIDER` (Kimi listing verified; Qwen has none); `claude-multi-dev
  model add --like` scaffold + promote runbook. Activation: HM switch only
  (no catalog-shape changes beyond the kimi qualification note).
- `claude-multi doctor` → **Ready** (all 18 durable scopes on catalog 12 + D44 shim guard;
  watchdog pin + roster prompts live; D43 radar in the suite).
  Sessions screen sorts by last used with the created age alongside
  (2.8.4; width-tiered). Issue 008 resolved in 2.8.3 (D44): MANAGED
  compact events no longer contribute model/cwd evidence (the
  production bleed case), and marker-bearing agent-context events are
  ignored. Ordinary sessions keep compact-model reconciliation — an
  unmarked compact bleed there is the accepted residual (issues/008).
- Claude updates are routine: the card badge or doctor Attention appears →
  press **U** (or `claude-multi update`) → instant effect via the operator
  override (`--activate` for the baseline refresh).
- Gateway re-renders (`claude-multi-proxy init`, catalog edits) need a
  `systemctl --user restart cli-proxy-api` — the daemon does not hot-reload
  a rename-replaced config on 7.2.80 (HM switch does this for you).
- Full evidence and census: the current checkpoint's
  [`handoff/state-snapshot.md`](checkpoints/2026-08-11-v2.15.0/handoff/state-snapshot.md).

## Composition: qwen-sol

Qwen3.8 Max lead (production since 2026-08-03 GA; ultracode, thinking always on, `reasoning_effort: xhigh`
— the provider maximum), Sol preferred analyst/implementer/reviewer
variants, Qwen-max alternates. Cross-family review: Sol (openai) reviews
Qwen (alibaba) work and vice versa. Sol is 1M-class (D57: official
subscription-route 1M enablement) and contributes no process scalar, so
the qwen-sol rig's shared cap is qwen38's 983,616 — a `[1m]` Qwen lead
uses that provider-safe compaction capacity plus the explicit 90%
override. Pinned 2.1.220 reserves 20K output,
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

Ordered in the current checkpoint: [`checkpoints/2026-08-11-v2.15.0/handoff/open-items.md`](checkpoints/2026-08-11-v2.15.0/handoff/open-items.md).

## Where things live

- **This package** (`docs/claude-multi-final/`): design docs + STATUS.md
  (ledger) + SANITY.md (assessment) + `checkpoints/` (state entry points).
- **Product**: `home-manager/claude-multi/` — canonical docs `AGENTS.md`
  (development) + `USAGE.md` (use); `.agents/` is a scratch workspace only.
- **History**: `docs/claude-multi-rethink-handoff/` (frozen C\* + artifacts).
- **Wikis** (pointers, not copies): `~/.agents/wiki/projects/claude.md`,
  `~/.claude/.agents/wiki/index.md`.
