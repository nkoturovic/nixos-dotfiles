# State snapshot — 2026-07-27 · v2.7.0 (+2026-07-28 v2.7.1, +2026-07-29 v2.7.2, +2026-07-29 v2.8.0)

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

## v2.6.2 (same night): adversarial hardening, two review rounds (D37)

1. **Round 1 — 8-reviewer fan-out** (mixed families, dimension-scoped,
   read-only): 35 raw findings, ~25 deduped real. Deepest: fork hook
   credentials outlived adoption/discard (parent authority could migrate
   into the fork's lineage); apiKeyHelper token path diverged under
   XDG_CONFIG_HOME; the update "current" path could delete a strictly-newer
   override (silent pin downgrade); the quick card had no bottom
   reservation (Status/BLOCKED clipped at common sizes).
2. **Fix set:** all 25, each with regression tests — fork credential
   revocation by epoch bump, single-authority token path, redundancy-gated
   override cleanup + activate retry, anchored literal sync, crash-atomic
   promotion+restore writes, candidate fallback, exception-safe cleanups,
   card/sessions/keybar/modal layout reservations, self-healing resume
   paths, dead-code deletion.
3. **Round 2 — final cross-family gate** (Sol xhigh on the fix diff):
   caught a candidate-ordering regression (lexical 2.1.99 > numeric
   2.1.218, U8 — fixed with version-key sort + test), non-atomic restore
   writes (U1 — fixed), a missed rollback branch for the legacy scope
   rewrite (fixed + test), and the broken-override-bricks-CLI
   reachability flaw (fixed by degrade-to-packaged + doctor BLOCKED +
   update-heals loop, tested end-to-end).
4. **Evidence:** 1,319 host tests OK (2 provider-secret skips); PTY OK;
   sandbox suite OK; package builds 2.6.2; `git diff --check` clean.
   Activated HM gen 105; doctor Ready, zero Attention; no scope-shape
   change (no repair-all needed); pin 2.1.220 verified, symlink-aligned.

## v2.6.3 (same night, final gate resolved)

The final gate's verdict was **Revise** — the fix set verified as correct
and complete against all 35 findings — with four flip items, all resolved
with regression tests:

- **SF1 (should-fix):** schema-invalid overrides (valid JSON, loader-
  rejected version shape) escaped the heal loop — kept as "not redundant"
  while doctor said update would remove them. Now such versions count as
  broken, and `runtime.broken_override_error` threads into `run_upgrade`
  from all three call sites (`override_broken`) — the doctor→update heal
  loop is proven end-to-end (test).
- **SF2 (should-fix):** Ctrl-C messages claimed "nothing was promoted"
  even in the post-override window; phase-neutral text at all three entry
  points ("check `claude-multi doctor` for the effective pin state").
- **N1:** details view admitted one line onto the reserved Status row and
  its fixed rows were unbounded — both clamped (test).
- **N2:** `_write_repo_file` refuses symlinked targets (test).

**Evidence:** 1,323 host tests OK (2 provider-secret skips); package
builds 2.6.3; `git diff --check` clean. Activated HM **gen 106**; doctor
Ready, zero Attention; pin 2.1.220 verified, symlink-aligned. Commits:
`1058133` (fix set) → `0314ba9` (gate resolution) → checkpoint commit.

## v2.7.0 (same night): the lifecycle gains stop (D38)

The incident's last usability gap: claude-multi could detect (●) but not
end a live background session — ending the zombie required native
agent-view keybindings under pressure. `sessions stop <uuid> [--yes]` and
**E** on picker rows now stop live sessions through upstream's own
`claude stop <id>` (verified binary, scrubbed env, timeout; never a
signal, never daemon internals; conversation always kept). Guards:
self-stop refused (env sentinel), non-live refused (best-effort pty
liveness), interactive confirmation, upstream errors surfaced. **E** was
chosen over K because K collides with the picker's vim `k`=navigate-up.
A transition started on a ● live session now warns and names the command.
Evidence: 9 new stop tests + transition-note test; focused cross-family
review of the feature: **approve** (env scrub pinned as a test, stdin
DEVNULL added, test names re-documented). Final suite with everything:
1,336 host tests OK (2 skips).

## The CLIProxy integration review (approve) and its resolution

Independent cross-family review of the gateway integration: **approve** —
secrets never leak into argv/logs/files, the live config is byte-identical
to a fresh render, both auth header forms accepted, token authority
singular (HOME-pinned), `/v1/models` serves claude-opus-5 (registry patch
live). Resolution items shipped: proxy `StateError` wraps as one-line
errors (no systemd crash-loop tracebacks), the `gateway.json` patches
manifest matches the build's three patches (catalog_version 6), the
model-add battery documents the required gateway restart after re-renders
(7.2.80 does not hot-reload rename-replaced configs), and STANDALONE.md
documents the codex cloak as a listing cosmetic. Suite 1,336 OK (2
skips). Activated **HM gen 107**; `doctor --repair-all` absorbed the
catalog bump into all 17 records; doctor Ready, zero Attention.

**Process disclosure (operator action recommended):** during the review,
the review agent's redaction slipped and the live Kimi/Qwen provider keys
were printed into its local transcript (mode-0600 transcript dir, this
machine only). Rotate `KIMI_CLAUDE_API_KEY` and `QWEN_CLAUDE_API_KEY` if
transcripts are ever synced or shared.

## 2026-07-28 · v2.7.1 — the routing incident and its fix (D39)

**Found by the operator, triple-confirmed:** the D24 lead-only
`availableModels` fence silently degraded ALL subagent dispatch to the
lead model (SA L242-251 documents the silent skip-and-inherit chain):
355 transcript entries of `cm-reviewer-sol-xhigh` running
`claude-multi-kimi-k3`, zero `gpt-multi` in 12h of gateway journal, and
an Opus-5-led session running everything as Opus 5. Cross-family review
independence was void in practice; per-agent effort executed on the
wrong model too.

**Fix:** `availableModels` = lead + every roster selector (dedup, lead
pinned). Radar: the offline delegation probe asserts the subagent's wire
model through the production-shaped fence on the pinned binary every
build (positive + lead-only negative control); U5 acceptance flipped to
**verified**; doctor audits `CLAUDE_CODE_SUBAGENT_MODEL` in user/project
settings (the last flattening vector).

**Live acceptance (this section is the proof):** activated HM gen 108;
`doctor --repair-all` converged all 17 scopes to the roster pool; the
EXACT converged scope bytes of session 58c87cef were then executed
through the pinned 2.1.220 binary against the fake provider — the wire
showed `claude-multi-opus-5` (lead) and `gpt-multi-sol-high` (the
subagent's declared selector). Pre-fix, both would have been the lead.
Process note (honest accounting): the first verification attempt passed
the live settings including `env.ANTHROPIC_BASE_URL`, which pointed the
probe at the REAL gateway and caused two real provider requests (one
turn, one follow-up) before the env was stripped — bounded, logged here
per the working agreements.

Evidence: 1,339 host tests OK (2 skips); focused cross-family review:
approve; package builds 2.7.1; doctor Ready, zero Attention.

## 2026-07-29 · v2.7.2 — the worktree-review contract (D40)

**Found by the operator:** a reviewer subagent at a repository root tried
to inspect an implementer's worktree via the native `EnterWorktree` tool
and was refused ("…is the repository root, not an isolated worktree").
The native tool's switching semantics drift between Claude versions, so
the fix is a roster-prompt contract, not machinery: a worktree is a
plain directory — read-mostly agents inspect from outside (direct reads,
`git -C`, subshell `cd`) and never EnterWorktree; implementers report
worktree path/branch/base ref + committed-state; the lead integrates
from its own root with explicit committed/uncommitted/untracked
handling. En passant: the sandbox derivation never staged the gateway
patch files, so the manifest file-existence test could never pass there
(pre-existing); it now stages the repo-shaped layout from the manifest.

**Hardening (3 cross-family rounds, Sol xhigh):** caught an
integration-ownership overstatement, the uncommitted-work gap (merge
would have silently integrated nothing — implementers don't commit by
default), the untracked-file gap (fixture-proven: `diff HEAD` omits new
files), and a heal-semantics doc error (every durable launch rewrites
the full scope — a plain resume heals; repair-all is only for sessions
never resumed). Final verdict: approve.

**Activation:** HM gen 109 (rollback 108/107); `doctor --repair-all`
converged all 18 durable scopes to catalog 8; live scope verified
6/6 roster files carry the new guidance, D39 fence + apiKeyHelper
intact. Also repaired en passant: session 58c87cef had drifted
repair-needed on 2026-07-28 17:40 UTC (a resume observed from an agent
worktree cwd — its transcript never left the original project dir;
evidence: single 35MB transcript, mtime 07-29 07:35) —
`relink-runtime … --cwd <recorded project>` re-derived identity to
authoritative (epoch 7). `claude-multi doctor` → **Ready**.

Evidence: 1,339 host tests OK (2 skips); sandbox derivation green
(builds claude-multi-2.7.2); keyword pins lock every guarantee.

## 2026-07-29 · v2.8.0 — resume operability + recovery policy (D41/D42)

**Found by the operator, investigated by 7 agents + 2 adversarial
verifiers (34/34 claims confirmed):** repair-needed guidance that could
not run as printed; daemon-owned resumes unguarded into the native
fork-on-ramp; "No conversation found" passing through bare; and an
"API Error: An error occurred while processing" that turned out to be
an upstream 500 surviving the client's 10 retries (not rate limiting —
zero 429/529 in 12h of journal; the "Sol agent" attribution was a UI
line merge — the window's traffic was all Kimi).

**What shipped:** `sessions.relink_message` (single actionable source,
ordinary-aware, shell-quoted) at every surface; bare relink clears
cwd-drift; the resume gate (`_evaluate_resume_gate` pure + mandatory at
`Runtime.perform`) with TUI modals (Repair / Stop & resume / Resume
anyway / Cancel), `--force` threaded identically through TUI/line/
noninteractive, transcript checks outranking liveness, fail-closed slug
decoding, one-keypress card repair that unblocks a blocked record; the
watchdog retry pin (managed scopes only); the lead contract's
resume-over-redispatch (proven live: three reviewer deaths continued,
never redispatched).

**Review:** two full cross-family rounds (both Sol xhigh: revise →
approve) + a per-issue round of 7 bounded reviewers (001 approve,
002/003/004/005/007 revise→fixed, 006 approve). Caught and fixed en
route: card-picker ordinary-record KeyError crash, stop→force TOCTOU,
alias-vs-target liveness mismatch, wrap-corrupted commands, ambiguous
slug decode, ordinary scope watchdog leak, and a pre-existing sandbox
staging gap. **1,380 tests OK; sandbox green.**

**Activation:** HM gen 110 (rollback 109/108); `doctor --repair-all`
converged all 18 durable scopes to catalog 9; live scope verified
(watchdog pin 1, D39 fence + apiKeyHelper intact, new roster prompts).
`doctor` → Ready. Open item found AT activation: one session (the
lead's own) flagged model-only repair-needed after a compaction
following dozens of Sol-reviewer dispatches — the observed model is
exactly the reviewer's selector, suggesting hooks in agent contexts
attribute a subagent's model to the session record (issue 008;
benign, self-heals on next launcher resume; mechanism under
investigation).
