# AGENTS.md — claude-multi development guide

This file is the entry point for agents developing claude-multi. Read it fully
before changing anything. It states the architecture, the invariants that keep
the system correct, the development workflow, and the rules of engagement.

Canonical ledger: `../../docs/claude-multi-final/STATUS.md` (update at
milestones). Design rationale: `../../docs/claude-multi-final/` (BLUEPRINT,
SPEC, TRANSITIONS, UX, DECISIONS, VERIFICATION, SANITY).

## 1. What this is

claude-multi is a **stdlib-only Python launcher** that compiles a chosen
*composition* (a lead model + generated `cm-*` agent variants) into a
**per-session durable scope** and execs a hash-verified Claude Code binary
pointed at it:

```
~/.local/state/claude-multi/
├── bin/claude-multi-hook        # stable lifecycle-hook shim (never a store path)
├── sessions/<managed-id>.json   # schema-v3 records (the intent authority)
├── scopes/<managed-id>/
│   ├── .claude/agents/cm-*.md   # generated agent definitions
│   └── settings.json            # compiled session settings (hooks, fence, policy)
├── last-session-by-cwd/<hash>.json[.ordinary]  # `-c` pointers
├── locks/                       # runtime-index + per-session lifecycle FileLocks
├── drafts/                      # dev pipeline drafts
└── lead-prompt-<digest>-<managed-id>.md        # compiled cm-lead prompt
~/.config/claude-multi/          # user compositions, gateway config.yaml (secrets), api-key
```

Launch argv (durable mode): `claude --session-id|--resume <runtime-id>
--name cm:<comp>|cg:<model> --settings <scope>/settings.json --model <lead>
--add-dir <scope> --append-system-prompt-file <lead prompt>`.
`--agents`/`--disallowedTools` are never used: argv definitions vanish on
supervisor takeover (observed live); on-disk files are re-discovered every
process start.

Two modes:

- **Managed composition** (`claude-multi`): generated `cm-*` roster, strict
  lead-model fence (`availableModels` = lead only), compiled policy denies,
  cm-lead prompt, composition transitions as the only lead change.
- **Ordinary gateway** (`claude-gateway` / `claude-multi direct`): no
  generated agents or composition policy; a context-compatible model profile
  in `availableModels`; native `/model` inside the profile; cross-profile
  changes are explicit relaunches. Upstream bare `claude` is never touched.

## 2. The doctrine (every change must preserve these)

1. **Record = intent; catalog = trusted source; scope = pure function of
   (record, installed catalog).** Scopes are always re-derivable; repair
   recompiles from record + catalog and displays drift. Never treat scope
   content as authority. The native contract is layered: the packaged
   contract is the reviewed baseline, and a strictly-newer operator override
   (`~/.config/claude-multi/native-contract.json`, written only by
   `claude-multi update` after its evidence gate) wins while newer — the
   packaged bundle hash never reflects the override.
2. **Two UUIDs, not one.** `managed_id` keys claude-multi state (record file,
   scope, pointer, locks). `runtime_session_id` is the authoritative native
   `--resume` target, reconciled from SessionStart hook metadata
   (startup/resume/clear/compact); old IDs become bounded aliases (≤16).
   Monotonic `launch_epoch` rejects delayed hooks from older launches; a
   higher epoch always wins. `transcript_path` is ignored, never stored,
   transcripts never read.
3. **Never embed volatile paths in scope content.** Package/store paths
   change on every rebuild → mass scope mismatch (the 2026-07-24 incident).
   Hooks invoke `<state>/bin/claude-multi-hook`, refreshed by every launcher
   run. The shim prefers the resolved launcher, falls back to PATH.
   `scope.resolve_hook_command` is the single authority for the real command.
4. **Fail closed, atomically.** All state writes: same-dir temp + fsync +
   rename, mode 0600 in 0700 dirs, symlink-refusing (`state.py`).
   `CommittedStateError` marks "bytes replaced, dir durability unconfirmed" so
   callers run transaction-specific recovery. Scope writes stage to
   `scopes/.<id>.new/` then rename.
5. **CAS-by-own-write + mutation tokens.** Rollback only touches record/scope
   while the on-disk record still equals what that attempt committed
   (`mutation_token`, `committed_bytes`). Rollback is **mutation-aware**: a
   failure before this attempt committed anything touches nothing (the
   2.3.0 data-loss fix).
6. **Lock ordering: runtime-index → lifecycle(per-session) → pointer.** Never
   invert. Pointer critical sections are single atomic writes; blocking is
   safe there. Lifecycle fds are `O_CLOEXEC` and survive into execve.
7. **Compiled settings are a closed allowlist** (`COMPILED_SETTINGS_KEYS`):
   workflow keys, `permissions.deny`, `availableModels`, `model`, `env`,
   `hooks`, `worktree`, `autoCompactEnabled`. Nothing else without a
   demonstrated failure case (D11).
8. **Managed sessions own their policy.** `autoCompactEnabled:true` is pinned
   (a user-level false silently wedges 1M sessions); managed `/model` is
   fenced to the lead — transitions are the only lead change. Ordinary mode
   respects user settings except the context-safe profile fence.
9. **Simplicity budget.** No new mode/schema/daemon/state without a
   demonstrated failure case. Prefer deletion over addition.
10. **Upstream `claude` is never configured or hijacked.** D19/D23: no global
    writes (`~/.claude/agents`, `~/.claude/settings.json`, gateway env).

## 3. Module map (src/claude_multi/)

| Module | Owns | Key contracts |
| --- | --- | --- |
| `state.py` | atomic writes, private dirs, FileLock | symlink-safe; `CommittedStateError`; lock fd `O_CLOEXEC` |
| `strict_json.py` | strict JSON + canonical bytes | dup-key rejection, size limits; `canonical_file_bytes` (state), `pretty_file_bytes` (user-facing docs) |
| `sessions.py` | schema-v3 records, store, pointers, locks, adoption, reconcile | `_normalize_legacy_record` is side-effect-free on read; `transition_record` (generation+1) vs `refresh_record_snapshot` (same composition, catalog drift absorbed) |
| `composition.py` | resolve/snapshot compositions | scalar = min explicit `scalar_tokens`; capacity narrows to strictest provider bound; trigger = (capacity−20K)×90% |
| `compiler.py` | pure launch plan (argv/env/lead prompt) | durable requires scope_dir **and** hook_command (fails closed); never PATH-fallbacks |
| `scope.py` | scope plan/write/gate, hook shim | `resolve_hook_command`/`ensure_hook_shim`/`hook_shim_path`; shim chmod repaired unconditionally; exact `cm-*` collision gate |
| `launch.py` | verify→readiness→state→execve | full-hash binary check every launch; CAS cleanup; `precommitted` epoch rule for transition relaunches |
| `transition.py` | diff, generation swap, converge | record loaded inside the lock; `converge()` = doctor repair (managed+ordinary, refresh+recompile); convert resolve failures to `TransitionError` |
| `cli.py` | commands, TUI screens, Runtime, doctor | Runtime init refreshes the hook shim; report commands write to stdout (`_STDOUT_REPORT_COMMANDS`), interactive flows to the tty; the card carries the health strip (one gateway check per open) and the update badge (U/H actions) |
| `upgrade.py` | evidence-gated re-pin (`update`) | detect → offline inspect → promote → suite → override; byte-exact restore on any failure; redundant overrides removed when the baseline catches up |
| `tui.py` | curses widget layer | every external string through `visible_text`; `read_key` does not re-merge Alt+chords (ncurses splits them by design); Esc is the only exit key; uniform col-2 margin; KeyBar wraps upward, never clips |
| `catalog.py` | trusted JSON load + validate | closed schemas; `version.json` single source of version |
| `render.py` | gateway YAML | secrets resolve only at runtime into mode-0600 artifacts |
| `proxy.py` | gateway process control | loopback only; token file 0600 |
| `dev.py` | draft→check→review→promote | promotes only models/providers; dummy secrets in checks; pretty post-images |
| `probe.py` | dev-only loopback harness | never touches live daemon/providers/transcripts; fixture roots only |

## 4. Development workflow

```bash
cd home-manager/claude-multi
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .   # full suite (required before "done")
PYTHONPATH=src:tests python3 -m unittest tests.test_launch         # focused
PYTHONPATH=src:tests python3 tests/bless.py                        # re-bless goldens after intentional compiler changes — review the diff!
nix-build --no-out-link package.nix                                # offline package build
nix build --no-link --file tests/default.nix                       # sandbox suite
git diff --check
```

- **Goldens** pin byte-exact compiler/scope output for the default
  composition. Any intentional change to generated bytes requires bless +
  diff review. The hook shim made scope bytes machine-independent again —
  keep them that way (no volatile paths, no timestamps).
- **PTY tests** (`tests/test_tui_pty.py`) drive the real TUI in pseudo-terminals
  (line + curses modes, no-ctty fallback). The bare-launch child arms a 20s
  `faulthandler` so a hang self-diagnoses into the captured output.
- **Version**: bump `version.json.launcher_version` for behavior changes;
  `catalog_version` only for trusted-catalog content changes. Records carry
  both; old launchers fail closed on newer record versions.

## 5. How to make common changes

- **Add/revise a model or provider:** use the product's own pipeline —
  `claude-multi-dev` draft → check → review (exact diff/hash) → promote.
  Never hand-edit `catalog/` without review. Only `models.json` /
  `providers.json` are promotable. When `qwen3.8-max` ships, follow
  DECISIONS D21 (wire_model → context re-verify → effort tiers → one live
  call → drop "· Preview").
- **Change compiled settings:** extend `COMPILED_SETTINGS_KEYS` + the compile
  + tests + bless; state the demonstrated failure case in the commit.
- **Change record shape:** bump `RECORD_VERSION`, extend
  `_normalize_legacy_record` (side-effect-free read migration), update
  `schemas/session.schema.json`, never rewrite-on-read.
- **Change lifecycle semantics:** preserve the epoch protocol (higher epoch
  wins; equal-epoch duplicate-runtime hook is ignored; `SessionEnd` advisory
  only). Hooks must stay metadata-only, 5s, stdin-reading.
- **Add a command:** report vs interactive classification goes in
  `_STDOUT_REPORT_COMMANDS`; interactive flows self-degrade via
  `streams_curses_capable`.
- **Re-pin Claude (routine, e.g. 2.1.218 → next):** run `claude-multi
  update`. One command: detects the newest installed artifact, inspects it
  offline (`--version`/`--help`/SHA-256), promotes it into the source
  checkout, runs the full offline suite (which includes the real-binary
  compaction/delegation probes against the candidate), and writes the
  **operator contract override** (`~/.config/claude-multi/native-contract.json`)
  — effective immediately, no rebuild or restart. The override wins only
  while strictly newer than the packaged contract; doctor shows which
  contract is in effect and flags a stale override. `--activate` also runs
  `home-manager switch` (baseline refresh); otherwise the packaged baseline
  lands at the next natural activation. Never hand-edit the contract without
  the evidence gate. The doctor **Attention** line fires when the symlink is
  newer than the pin — that is the trigger to run it.

## 6. Rules of engagement (non-negotiable)

1. **No real-provider calls without explicit user approval, per call.**
2. **Never touch the live Claude daemon/supervisor; never read user
   transcripts; never delete anything under `~/.claude` or a
   transcript-bearing root.** Rollback never deletes state.
3. **Tests before claims.** Full discovery green + package build + sandbox
   suite before declaring done.
4. **Commit discipline:** coherent commits on `feature/term-only`; no push
   without approval. Home Manager activation (`home-manager switch --flake
   /home/kotur/personal/nixos-dotfiles#kotur`) needs user approval — it
   restarts the gateway.
5. **Review cadence:** self-verify milestones; one batched **cross-family**
   review (Sol when the author is Kimi/Qwen and vice versa) at meaningful
   boundaries; no review loops.
6. **Doctor is the truth surface:** real damage must BLOCK; by-design lazy
   state is Attention with the exact fix command. Never demote damage to
   attention, never let lazy state block.
7. **Keep the map current (replicability rule):** update `STATUS.md` at
   every milestone before claiming "done"; update `AGENTS.md`/`USAGE.md`
   with any behavior change; create the next checkpoint
   (`../../docs/claude-multi-final/checkpoints/`) at every meaningful
   boundary (activation, architecture change, major integration); keep one
   canonical home per topic — pointers elsewhere, never copies. The full
   organizing doctrine is the "Documentation map" in
   `../../docs/claude-multi-final/README.md`.

## 7. Debugging tools

- `claude-multi doctor` / `--repair UUID` / `--repair-all` / `--prune` —
  health, converge to record authority, bulk converge + snapshot refresh,
  stale-file sweep (never transcripts).
- `claude-multi -r UUID --print-launch` — exact argv + env keys (token never
  shown). `--print-launch` never persists state.
- `claude-multi sessions show UUID` — the record (identity, epochs, aliases).
- `claude-multi sessions relink-runtime ID RUNTIME_ID [--cwd PATH]` — repair
  pre-hook UUID drift using native `/status`.
- Hook shim: `~/.local/state/claude-multi/bin/claude-multi-hook` — invoke it
  only with the record's exact `--launch-epoch`; **a higher epoch is accepted
  as a newer launch and will reject the real session's hooks as stale** (this
  mistake has been made once — restore the record's epoch by editing the JSON
  if it happens).
- `claude-multi-dev probe …` — dev-only disposable-fixture harness; loopback
  fake provider; daemon-domain gate; live-domain tripwire.

## 8. Known limitations / open items

- **U1 takeover proof**: durable `--add-dir` carry is documented +
  binary-consistent; the 2.1.218 supervisor now runs and has taken over
  backgrounded managed sessions — watch a post-takeover roster once.
- **Hook-failure invisibility**: if Claude never invokes a hook (observed
  once: transcript written, zero events), the record is indistinguishable
  from healthy-at-rest. Hook stderr is not captured anywhere; detection
  requires Claude-side logging. Document, don't fake a fix.
- **Wide chars in the TUI**: cell-width is `len()`-based; CJK/wide strings
  misalign tables (cosmetic; escape injection is sanitized separately).
- **`--legacy`** is a compatibility hatch, not a durability answer; legacy
  (v1) records upgrade on resume. Do not extend it.
- **Qwen preview→production**: when `qwen3.8-max` ships, follow DECISIONS
  D21 (wire_model → context re-verify → effort tiers → one live call →
  drop "· Preview").
