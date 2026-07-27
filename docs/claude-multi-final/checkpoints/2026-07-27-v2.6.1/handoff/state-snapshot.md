# State snapshot — 2026-07-27 · v2.6.1

Evidence behind the checkpoint README claims. Verify before trusting.

## The incident that drove v2.6.0 (2026-07-27, live)

1. An `opus-sol` session (`58c87cef`) was backgrounded; the supervisor
   daemon adopted it. The operator re-entered it from the `claude agents`
   menu (arrow-left/arrow-right) — Claude Code **forked natively**
   (`707e80d4`), because a second process cannot own a session the daemon
   hosts.
2. The daemon relaunched the fork preserving argv (`--settings`,
   `--add-dir`, `--model claude-multi-opus-5[1m]`) — durable scope survived
   — **but scrubbed `ANTHROPIC_*` from the child's env** (verified:
   daemon env had 2 gateway vars, spawned pty-host had 0) → every model
   call failed `invalid model` (a zombie session).
3. The hooks reconciled correctly (fork → pending; authority followed the
   live runtime onto the fork) — but the stale `pending_forks` marker was
   never cleared when authority landed ON the fork → the record
   self-blocked every resume/transition with a message that named neither
   the fork nor a remedy. The picker filtered the fork out of existence
   (it excluded the record's own runtime id).
4. A second record (`a048b8f0`, sol-direct) was found stuck in the same
   state three days earlier — the class was real, not a one-off.

## What v2.6.0 changed (D32–D35 in DECISIONS.md)

- **D32** pending forks self-clear on authority; Attention + `--repair-all`
  convergence; `sessions resolve-fork`; one actionable message builder;
  `link` adoption already stripped parent markers (verified).
- **D33** compiled settings gain `env.ANTHROPIC_BASE_URL` (non-secret) +
  `apiKeyHelper` → stable `bin/claude-multi-gateway-token` shim (0700,
  atomic, mode-repaired); token never in files; plumbed through all nine
  compile call sites (reviewer-verified); gateway accepts both bearer and
  x-api-key (curl 200/200); shim output == token file (sha256 compare).
- **D34** `run_upgrade` progress callback + whole-flow FileLock (with a
  contention note); TUI U runs in one suspended block with live lines;
  line-mode `u` and CLI `update` narrate too.
- **D35** picker ⚠ fork / ● live (daemon pty-socket glob, best-effort)
  markers, `(fork)` native rows, X resolve action, keybar row reservation
  (`KeyBar.rows`), help text documents all of it.

## Verification evidence

- **1,233 host tests OK** (2 provider-secret skips), three consecutive
  full-discovery runs; PTY 19/19; sandbox suite OK; package builds
  (`claude-multi-2.6.0`); `git diff --check` clean.
- **Cross-family review (Sol, xhigh): APPROVE** — fork lifecycle
  fail-closed at every guard attacked; no token leak surface; lock ordering
  consistent; one should-fix (picker exception escape) fixed by the
  reviewer and swept into the code commit; three nits fixed in the review
  commit (`9bebd14`). One external assumption named: Claude Code trims
  `apiKeyHelper` stdout — live acceptance item (open-items.md).
- **Live state:** HM gen 103; `claude-multi --version` → 2.6.0;
  `doctor --repair-all` converged 17/17 durable scopes to the v2.6 shape;
  `doctor --prune` clean; `doctor` → Ready (one Attention: 2.1.220 upstream
  available — the operator's re-pin decision); `a048b8f0` marker cleared,
  identity `authoritative`; a converged scope inspected: `apiKeyHelper` +
  base URL present, no token material in bytes.
- **Activation:** `home-manager switch` (gen 103) restarted cli-proxy-api
  (expected); gateway active, 30 models served.

## Known residuals (accepted)

- Already-running sessions keep their start-time scope until their next
  converge/relaunch (by design; running processes never re-read settings).
- The ● marker depends on the daemon's pty-socket layout (native
  internals); degradation = marker absent, never wrong.
- The zombie fork process (`707e80d4`, pre-fix, env-scrubbed) may still be
  hosted by the daemon until the operator exits it — harmless (API calls
  fail in it), and its transcript is the live branch of `58c87cef`.

## v2.6.1 (same day, evening): the update flow's first real candidate

1. The operator ran `claude-multi update` for the 2.1.220 candidate — the
   flow's candidate path had never run live before. It **failed its own
   evidence gate**: promotion changed the contract, then the suite's
   deliberate version pins (validated-version/SHA/path literals,
   `inspected_at`, `catalog_version`) failed against the new contract
   (7 failures). Fail-closed restore left the repo byte-identical — twice
   (the operator's run and one reproduction).
2. **D36 fix:** promotion now syncs those literals in the same transaction
   (backup/restore included; the decoupled per-model `floors` minimums
   untouched); one widget test made hermetic instead. Progress became
   phase-numbered `[N/M]` with a 15s heartbeat during long subprocess
   phases (plain lines — identical on TTY/pipe/captured streams).
3. **Proven end-to-end:** the fixed flow ran against the real 2.1.220
   candidate — literals synced, suite green (1,237), override written,
   heartbeat lines observed live. HM gen 104 then landed the packaged
   2.1.220 baseline; the redundant override was removed by the designed
   cleanup. Doctor: Ready, zero Attention; pin 2.1.220 hash-verified,
   symlink-aligned.

## §flows — live flow verification (2.6.1, metadata-safe)

- `sessions list`: markers render; ● correctly flags the daemon-hosted
  fork session; `sessions show` clean on the cleared records.
- resolve-fork error path: actionable message, exit 2.
- `claude-multi -r <id> --print-launch` and `claude-gateway --model sol
  --print-launch`: plans compile against the verified binary, correct
  scope/model/name argv (no exec).
- Transition `opus-sol → kimi-sol`: semantic diff correct (variant
  add/remove, lead change, catalog-drift note); abort left the record
  byte-untouched (verified composition + generation).
- Adoption guard: a nonexistent UUID is refused with an actionable
  message (help text corrected to match the metadata check).
- `compose list/show`: 7 profiles, summary renders; `models` table ok.
- `update`: full real run green (above); `update` again afterwards →
  "nothing to re-pin" + redundant-override cleanup (idempotent).

## Docs added this cycle

- `STANDALONE.md`: plain-Claude machine setup (installer layout, HM-owned
  integration, state/secrets map), the daemon model, native forks, the
  update channel, which-tool-when, and the never-touch boundaries.
- `USAGE.md`: composition-management section (all compose subcommands),
  standalone pointers; validation claims verified against the code.
