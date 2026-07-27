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
~/.config/claude-multi/          # user compositions, gateway config.yaml (secrets), api-key,
                                 # native-contract.json (operator override, from `update`)
```

Current default composition: **Opus 5 lead** (Sol preferred variants, Kimi
alternates, opus5 native reviewer alternate); the trusted catalog pins Claude
**2.1.220** as the verified binary.

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
   packaged bundle hash never reflects the override. An **invalid** override
   is never applied: the runtime degrades to the packaged baseline and
   doctor BLOCKs with the fix command (`claude-multi update` removes the
   broken file); it never bricks the CLI (D37).
2. **Two UUIDs, not one.** `managed_id` keys claude-multi state (record file,
   scope, pointer, locks). `runtime_session_id` is the authoritative native
   `--resume` target, reconciled from SessionStart hook metadata
   (startup/resume/clear/compact); old IDs become bounded aliases (≤16).
   Monotonic `launch_epoch` rejects delayed hooks from older launches; a
   higher epoch always wins. `transcript_path` is ignored, never stored,
   transcripts never read. **Forks:** a `fork`-sourced hook only appends to
   `pending_forks` (never retargets authority); when a later hook retargets
   authority ONTO a pending fork id, the marker self-clears (reality
   resolved it — the 2026-07-27 self-block incident). Genuine pending forks
   block resume/transition until the operator adopts
   (`sessions link`, which strips the parent's marker) or discards
   (`sessions resolve-fork`; metadata-only, transcript kept);
   **both decisions bump the parent's `launch_epoch`, revoking the fork's
   baked hook credential** so its later hooks cannot migrate the parent's
   authority into the fork's lineage (D37; trade-off: a still-running
   parent app reconciles on its next launcher resume — the relink-runtime
   precedent). The cap never evicts silently: the 17th distinct fork hook
   fails visibly. Resume flows converge resolved-by-reality markers at
   action paths (plan/resume guards); display paths keep them visible.
   `doctor --repair-all` converges the stale-authority case; every
   fork-blocked message names the fork UUID + exact commands
   (`sessions.pending_fork_message`, single source, ordinary-aware).
3. **Never embed volatile paths in scope content.** Package/store paths
   change on every rebuild → mass scope mismatch (the 2026-07-24 incident).
   Hooks invoke `<state>/bin/claude-multi-hook`, refreshed by every launcher
   run. The shim prefers the resolved launcher, falls back to PATH.
   `scope.resolve_hook_command` is the single authority for the real command.
   Same pattern for gateway auth: `<state>/bin/claude-multi-gateway-token`
   (`scope.ensure_token_helper_command`) backs the compiled `apiKeyHelper`,
   so sessions relaunched by the background daemon — which scrubs
   `ANTHROPIC_*` from its children's env — keep gateway routing. The token
   value never enters scope files; only the non-secret
   `env.ANTHROPIC_BASE_URL` does. The token path is **strictly
   HOME-relative** (`~/.config/claude-multi/api-key`), matching its writer
   (`proxy.config_dir`) and reader (launch via catalog-pinned
   `gateway.token_file`) — it deliberately ignores `XDG_CONFIG_HOME`, which
   moves compositions/override (`sessions.config_root`) but not the
   catalog-pinned token location (hardening review H1).
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
   `hooks`, `worktree`, `autoCompactEnabled`, `apiKeyHelper`. Nothing else
   without a demonstrated failure case (D11; `apiKeyHelper` earned its place
   via the daemon env-scrub incident, D33).
8. **Managed sessions own their policy.** `autoCompactEnabled:true` is pinned
   (a user-level false silently wedges 1M sessions). `availableModels` is
   **lead + every roster selector** (dedup, sorted) with `model` pinned to
   the lead: the pool must cover the roster or agent dispatch silently
   falls back to the lead (the 2026-07-28 routing incident, D39 — the
   delegation probe guards the wire model against the production-shaped
   fence every build). A manual `/model` switch away from the lead is
   backstopped by the identity machinery (observed-model → repair-needed),
   never hidden. Ordinary mode respects user settings except the
   context-safe profile fence.
9. **Simplicity budget.** No new mode/schema/daemon/state without a
   demonstrated failure case. Prefer deletion over addition.
10. **Upstream `claude` is never configured or hijacked.** D19/D23: no global
    writes (`~/.claude/agents`, `~/.claude/settings.json`, gateway env).

## 3. Module map (src/claude_multi/)

| Module | Owns | Key contracts |
| --- | --- | --- |
| `state.py` | atomic writes, private dirs, FileLock | symlink-safe; `CommittedStateError`; lock fd `O_CLOEXEC` |
| `strict_json.py` | strict JSON + canonical bytes | dup-key rejection, size limits; `canonical_file_bytes` (state), `pretty_file_bytes` (user-facing docs) |
| `sessions.py` | schema-v3 records, store, pointers, locks, adoption, reconcile | `_normalize_legacy_record` is side-effect-free on read; `transition_record` (generation+1) vs `refresh_record_snapshot` (same composition, catalog drift absorbed); fork lifecycle: `pending_fork_message` (single message source), `drop_resolved_pending_forks`/`converge_pending_forks`, `resolve_fork` + `link` epoch-bump revocation (D37), 17th-fork visible failure |
| `composition.py` | resolve/snapshot compositions | scalar = min explicit `scalar_tokens`; capacity narrows to strictest provider bound; trigger = (capacity−20K)×90% |
| `compiler.py` | pure launch plan (argv/env/lead prompt) | durable requires scope_dir **and** hook_command (fails closed); never PATH-fallbacks |
| `scope.py` | scope plan/write/gate, hook + token shims | `resolve_hook_command`/`ensure_hook_shim`/`hook_shim_path`; shim chmod repaired unconditionally; `ensure_token_helper_command`/`gateway_token_shim_path` (apiKeyHelper); `CatalogMeta.gateway_base_url`; exact `cm-*` collision gate |
| `launch.py` | verify→readiness→state→execve | full-hash binary check every launch; CAS cleanup; `precommitted` epoch rule for transition relaunches |
| `transition.py` | diff, generation swap, converge | record loaded inside the lock; `converge()` = doctor repair (managed+ordinary, refresh+recompile); convert resolve failures to `TransitionError` |
| `cli.py` | commands, TUI screens, Runtime, doctor | Runtime init refreshes both shims and degrades a broken contract override to packaged + `broken_override_error` (doctor BLOCKs; never applied); report commands write to stdout, interactive flows to the tty; the card reserves the keybar+Status zone up front and carries the health strip + update badge; the sessions screen renders ⚠ fork / ● live markers with X resolve-fork and E end-session (upstream `claude stop` via the verified binary; self/non-live refused) actions and has a minimum-size floor; resume paths self-heal resolved-by-reality fork markers; a transition on a ● session warns and names `sessions stop` |
| `upgrade.py` | evidence-gated re-pin (`update`) | detect (version-key ordered, invalid candidates skipped) → offline inspect → promote + version-pin sync (boundary-anchored) → suite → override; crash-atomic `_write_repo_file` for promotion AND restore; redundant override removed only when ≤ packaged baseline (`packaged_contract`, `override_broken`); `--activate` retries land the baseline; FileLock-serialized; `[N/M]` progress + heartbeat |
| `tui.py` | curses widget layer | every external string through `visible_text`; `read_key` does not re-merge Alt+chords (ncurses splits them by design); Esc is the only exit key; uniform col-2 margin; KeyBar wraps upward (≤2 rows) and past that compacts middle bindings behind an ellipsis — the exit binding is unclippable; screens must reserve `KeyBar.rows(width)` above the bar |
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

On memory-tight boxes the suite's tmpfs fixture copies can fail with
`Errno 122 Disk quota exceeded` (seen 2026-07-27 with `/tmp` at 84% and swap
full): run with a disk-backed temp dir, e.g.
`TMPDIR=~/.cache/claude-multi-test-tmp PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .`

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

- **Add/revise a model or provider:** two sanctioned paths, by size. For a
  new provider or a provider-kind change (transport, auth, routes), use the
  product's own pipeline — `claude-multi-dev` draft → check → review
  (exact diff/hash) → promote; never hand-edit `catalog/` without it. For a
  same-provider model addition (the Opus 5 pattern), a direct catalog edit
  is acceptable **when it lands with the full battery**: schema load +
  `validate_catalog`, all pinned expectations updated (composition, editor,
  transition, render, scope, catalog, cli), goldens re-blessed and the diff
  reviewed, the live gateway re-rendered **plus restarted**
  (`claude-multi-proxy init` writes the config, then
  `systemctl --user restart cli-proxy-api` — the daemon does NOT hot-reload
  a rename-replaced config.yaml on 7.2.80; without the restart it serves the
  old routes), and one consent-gated live call.
  Only `models.json` / `providers.json` / compositions are ever edited.
  When `qwen3.8-max` ships, follow DECISIONS D21 (wire_model → context
  re-verify → effort tiers → one live call → drop "· Preview").
- **Add a model the gateway doesn't know (like Opus 5):** CLIProxyAPI's
  embedded registry may predate the model — aliases then drop from
  `/v1/models` (routing still works). Add a local registry patch under
  `home-manager/` mirroring the nearest existing entry (see
  `cli-proxy-api-opus-5-model.patch`) and wire it into
  `home-manager/claude-multi/claude-multi.nix`'s patch list; rebuild
  through Home Manager and verify `/v1/models` serves it.
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
   with any behavior change (`STANDALONE.md` for machine-setup/upstream
   behavior facts); create the next checkpoint
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
