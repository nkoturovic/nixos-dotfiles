# DECISIONS — evidence, alternatives, user-facing summary

Each decision: the choice, the evidence, what was rejected and why. Reopen
only on new evidence (U-series outcomes or audit findings).

## Core architecture

**D1 — Durable state = per-session scope via `--add-dir`; `--agents` eliminated.**
Evidence: `--agents` "exist only for that session and aren't saved to disk"
(SA L130); on-disk scopes re-scanned every start and watched (SA L181);
carry-through set includes `--add-dir` and `--settings` but not `--agents`
(AV L340–349); binary respawn re-resolves addDir/settings/pluginDir.
Rejected: per-UUID config roots (relocates transcripts/auth/onboarding —
C\*'s weight), global user agents (pollutes plain claude), plugin dir
(field restrictions + scoped-name churn), argv re-supply wrapper
(unenforceable against supervisor-owned respawn).

**D2 — Policy durability doctrine: files > carry-through argv > displayed
residual.** Denies and workflow mode live in compiled `settings.json`;
`--disallowedTools` argv retired (not in carry-through set). Evidence: SA
L501–517 (settings deny), AV L340–349.

**D3 — State model: record = intent, catalog = trusted source, scope = pure
function of (record, catalog).** Two explicit authorities, no hidden state.
The record stores the catalog hash; recompiles use the installed catalog and
display drift. (R0 finding: the record alone cannot reproduce scope bytes —
prompt bodies/selectors legitimately live in the versioned catalog.)

**D4 — Lead policy stays `--append-system-prompt-file` + a durable floor.**
U2 (appendix survival) is unverified; every load-bearing rule therefore also
lives in files: generic denies in settings, no-substitute sentinel in every
description, cross-family independence in reviewer descriptions, role
contracts in bodies. Appendix loss degrades the rendered convenience view,
not policy — claimed as policy-preserving degradation, not "no effect".

## Behavior & policy

**D5 — Project agents participate visibly; exact `cm-*` collisions fail
closed.** User direction (Q10): participation over blanket blocking. `cm-*`
is our namespace; a collision would silently shadow a guaranteed definition
(closest-to-cwd wins, SA L118), so it is the one hard gate. Everything else
loads and is listed.

**D6 — Capable defaults kept; nesting re-enabled in prompts.** Roles keep
broad inherited tools (no `tools`/`disallowedTools` frontmatter). Nested
delegation restored to "bounded, same contracts" (U6: SA L763–771 says on,
depth 5 fixed; frozen "default-off" claim contradicts the current doc).
`Agent(type)` allowlist not used — ignored in subagent definitions (SA L332).

**D7 — Reviewer becomes reviewer/finisher.** May fix clear bounded defects,
runs verification, reports all edits once (REVIEW-STRATEGY). The rare
read-only audit stays cross-family and separate. No `review.mode` schema.

**D8 — Workflows per-composition, default `native`; `off` =
`disableWorkflows:true` + ultracode→xhigh at compile.** Consequences shown
in TUI (WF L327). No custom workflow machinery.

**D9 — Transitions are relaunch-only in v1**, with exact `--resume`, an
explicit "target process has exited" confirmation (never mutate a live
session's scope), sibling-generation swap, and crash-converge to record
authority. No pretend hot-swap; a watcher probe (U3) may inform a later hot
mode. Diff always precedes mutation; invoked from inside the target session
the command only prints.

**D10 — Teams stay off and undocumented in product UX.** Experimental,
env-gated (TM); orchestration guidance says teams fit independent partitions,
not this product's coupled delegation model. Never set the env key.

## Settings & catalog

**D11 — Compiled settings keys (closed allowlist):** `disableWorkflows`,
`workflowSizeGuideline`, `workflowKeywordTriggerEnabled` (existing base);
`permissions.deny` (generic/built-in policy, durable); `availableModels`
(type-specific model fence); lifecycle `env`/`hooks` carrying the stable ID;
`worktree.baseRef:"head"` when any
variant is worktree-isolated (implementers branch from current local HEAD,
carrying local/unpushed **commits** — WT L101–106; uncommitted working-tree
changes are NOT carried, which is accepted: implementers never edit the main
checkout, the lead integrates). Nothing else without a demonstrated failure.

**D12 — `version.json` → 2.2.0; old launcher fails closed on schema-v3 state.**
The G0' diff already made the contract unreadable to 2.0.0; we make that
explicit and intentional rather than accidental (map:g0-diff risk).

**D13 — Catalog slimming:** native-contract capability objects collapse to an
acceptance map; roles.json drops duplicated contract strings; prompts stay
canonical. schemas/ loses ~180 ceremony lines.

## Process & scope

**D14 — Editor dual-mode duplication resolved (M3.5, user-requested).** One
form-based curses editor on the `tui.py` widget layer replaced both the
curses editor and the numbered line fallback (~1,130 lines deleted);
`TERM=dumb` gets a read-only printed plan + the exact `$EDITOR` command, and
`Ctrl+G` is the only external-editor integration.
**D15 — Daemon-metadata reader simplified** to existence/pid (the defended
file doesn't exist).
**D16 — P0 sandbox deleted; probe.py kept dev-only.** See MIGRATION §1.
**D17 — No designer specialist exists in the current agent inventory.** TUI
judgment is lead-owned with a dedicated self-review pass against UX.md; if a
designer variant is later added to the catalog, UX gets a routed pass.
**D18 — `--disable-slash-commands` passthrough blocked** (sessions started
with it never watch agent dirs, SA L186 — silent durability loss).

**D19 — Not a global configurator.** A "configure Claude's own files, user
runs plain `claude`" model was considered and rejected: every durable
surface it would write (`~/.claude/agents/`, `~/.claude/settings.json`,
gateway env) is global, so plain sessions would inherit managed agents,
denies, and — decisively — gateway routing (plain `claude` would stop using
normal Anthropic auth); concurrent compositions and atomic per-session
transitions become impossible; session identity/trust gates have no owner.
The design is therefore compiler + **per-session** configurator + thin
launcher (verify → record → execve). A `--print-launch` display mode may be
added for transparency; it is not a second operating mode.

**D20 — R1-audit hardening refinements** (pre-activation, 2026-07-22):
1. **Lifecycle authority is revalidated under the per-UUID lock** (stale
   plans abort; converge/repair reads the record inside the lock), and
   exec-failure cleanup is **CAS-by-own-write**: a rollback only touches
   record/scope while the current record still equals what that attempt
   wrote; a newer commit always wins. No epoch files.
2. **Durable-resume failure preserves the scope**: cleanup restores the
   record (guarded) and recompiles the record-authoritative scope instead of
   deleting it.
3. **Transitions preflight before mutation**: binary verification + gateway
   readiness run before the scope swap, so a preflight failure leaves zero
   trace.
4. **Resume is recorded-intent only**: composition changes go exclusively
   through `sessions transition` (the diff/confirm/generation path); catalog
   drift on resume is informational, never stranding.
5. **`--legacy` + `workflows:"off"` fails closed** (legacy has no durable
   settings channel; the hatch must not silently invert policy).
6. **One visible-text sanitizer** for all external filesystem/user-derived
   strings at render points (terminal-escape injection closed).

**D21 — Qwen Cloud integration parameters are doc-derived, not Kimi-derived.**
The Qwen Token Plan endpoint differs from Kimi in every transport detail:
bearer auth (new `bearer` auth kind; Kimi's `x-api-key` override does not
apply), `reasoning_effort: xhigh` as the effort knob (provider maximum;
options xhigh/high/low — NOT `output_config.effort`), native always-on
thinking (no filter), context 983616 (official Claude Code doc's own
`CLAUDE_CODE_MAX_CONTEXT_TOKENS` recommendation). Selector base
(`claude-multi-qwen38-max`, client form adds `[1m]`) and model id (`qwen38`) are preview-free by
design. **Lifecycle note**: when the production `qwen3.8-max` ships, revise
in order: (1) `wire_model`; (2) context bound re-verification; (3)
`reasoning_effort` tier check (any level above xhigh?); (4) one live
verification call; (5) display name drop of "· Preview". Recorded here and
in the model's `routing_note` so the future edit is deliberate, small, and
localized.

**D22 — Session identity is two UUIDs, not one.** `managed_id` is stable and
keys claude-multi state; `runtime_session_id` is authoritative for native
resume. A synchronous metadata-only SessionStart hook reconciles the latter on
startup/resume/clear/compact; SessionEnd is advisory. Transcript contents and
`transcript_path` are never read. Runtime aliases make old/native UUIDs usable
for lookup without moving scopes or records.

**D23 — Ordinary gateway mode is first-class but does not hijack bare
Claude.** `claude-gateway` / `claude-multi direct` uses the same verified
binary and loopback transport with no generated agents, appendix, composition,
or managed policy. Upstream `claude` remains untouched by default. This
revises D19 narrowly: global configuration is still rejected, while a
per-session ordinary launcher is now supported.

**D24 — `/model` follows session semantics.** Managed settings expose only the
compiled lead; a different lead requires a composition transition. Ordinary
settings expose a context-compatible profile: Sol aliases together, and
Fable/Opus/Kimi/Qwen together. In-profile native switching is preserved across
implicit resume by omitting a new `--model`; cross-profile changes explicitly
relaunch and pin the requested model.

**D25 — Context classification, scalar protection, and compaction are explicit
separate controls.** Each model records client context, configured provider
context, evidence qualification, optional process scalar, and ordinary profile.
`CLAUDE_CODE_AUTO_COMPACT_WINDOW` is compiled as the configured route capacity
and `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90` supplies the reactive percentage;
neither value is itself the final threshold. Catalog-validated bounds are labeled
validated; Kimi's 1M route is explicitly user-attested and must not be described
as provider-safe until near-limit live acceptance. Pinned Claude Code 2.1.217 reserves up to
20K output tokens, producing deterministic reactive thresholds of Sol 316800,
managed 1M process 882000, and Qwen/ordinary-large 867254. Proactive preparation
uses a runtime-controlled fraction and is reported only as "may occur earlier."
In mixed processes Sol/GPT variants are protected by their own client caps; an
extended selector whose provider bound is below its client classification
narrows the shared capacity, so Qwen lowers a 1M process to 983616. Qwen's
`[1m]` selector is client
classification only; its provider bound stays 983616 and renderer tests prove
suffix stripping to the exact wire model.

**D26 — Lifecycle hooks resolve through a stable state-root shim, never a
package path (2.3.0).** Evidence: the hook command had been computed four
ways (wrapper path vs inner-script path vs PATH fallback), so two scopes of
one generation diverged by spelling, every Home Manager rebuild invalidated
all durable scopes (20 live mismatches), and the inner-script spelling died
without the wrapper's PYTHONPATH. Compiled scopes therefore embed only
`<state>/bin/claude-multi-hook`, a shim refreshed by every launcher run
(prefer-resolved, PATH-fallback, exec-bit repaired unconditionally);
`scope.resolve_hook_command` is the single authority, and the durable
compiler fails closed without a hook command. Rejected: canonicalizing to
`~/.nix-profile` (couples scope bytes to install method; dev checkouts
diverge), normalizing the path out of the hash (hides genuinely dead hooks),
PATH-only commands (environment roulette).

**D27 — Doctor separates damage from lazy state, and bulk repair is the
older-session answer (2.3.0).** BLOCKED = real damage (unreadable records,
identity repair, missing/mismatched scopes); Attention = by-design lazy
state (legacy records, legacy context snapshots) with the exact fix command
and exit 0. `doctor --repair-all` converges every durable record
(managed+ordinary) under its lifecycle lock, refreshes managed snapshots
against the installed catalog (`refresh_record_snapshot` fails closed on any
composition change — that remains a transition), skips legacy records, and
is failure-isolating per record. Rejected: one severity bucket (the
2026-07-24 BLOCKED-by-laziness state), requiring 21 interactive resumes
(unnecessary — refresh is shape-preserving catalog-drift absorption, D3's
doctrine extended to record metadata).

**D28 — Managed sessions own their compaction switch (2.3.0).** Compiled
managed settings pin `autoCompactEnabled: true`. Evidence: the user-level
`autoCompactEnabled: false` silently defeated the documented capacity/trigger
model (D25) for every managed session — the exact wedge class observed live
in sol-direct before b07d2d4. `--settings` outranks user settings in Claude
Code's precedence (verified against current docs). Ordinary gateway scopes
deliberately leave the user's setting alone (ordinary mode is the user's own
Claude with a context-safe fence, D23/D24).

**D29 — The native contract is layered, and re-pinning is one command
(2.4.0).** Claude Code auto-updates itself (symlink moves, supervisor adopts
new versions for new/backgrounded sessions); that upstream channel stays ON
for plain `claude` (managed sessions additionally export
`DISABLE_AUTOUPDATER=1` against in-process updater churn). claude-multi
anchors to the pinned binary and must never auto-follow — but a re-pin must
not be a manual scavenger hunt or a mandatory rebuild either. The doctor
Attention line fires when the symlink is newer than the pin;
`claude-multi update` then: detects the candidate, inspects it offline
(`--version`/`--help`/SHA-256), promotes it into the source checkout, runs
the full offline suite (real-binary probes included) against the candidate,
and writes the **operator contract override** in the config root — effective
immediately, no rebuild, no restart. The override wins only while strictly
newer than the packaged contract (a stale override is ignored and reported,
never silently followed); the packaged bundle hash never reflects it.
`--activate` additionally runs `home-manager switch` (baseline refresh);
otherwise the baseline lands at the next natural activation. Rejected:
fully automatic re-pinning without the evidence gate (the pin exists
precisely to prevent unreviewed drift); contract-in-package only (every
Claude point release would force a rebuild+restart); deleting the upstream
auto-updater (plain `claude` should track upstream).

**D30 — Esc is the only exit key; screens share one margin (2.4.0).**
Evidence: `Q` exited the card and the sessions screen but typed `q` into
editor text fields — a "sometimes-exits" key is strictly worse than one
universal rule. Every curses screen now exits/cancels on Esc only
(keybars advertise it); `Q` is free to be text; line-mode flows keep the
word commands `q`/`quit`/`cancel` (word modality, no conflict). All screens
also share a uniform column-2 left margin (title, tables, forms, status,
keybars) — the sessions and editor screens and the quick-confirm details
block previously mixed col-0 and col-2 layouts. Recorded in UX.md §2.1 and
pinned by the PTY/widget tests (which now drive Esc everywhere).

**D31 — Opus 5 is the default lead; Opus 4.8 stays in the catalog, Fable 5
stays a named profile (2.5.0).** The Anthropic release (2026-07-24) makes
Opus 5 the near-Fable, half-price, same-price-as-4.8 model — strictly the
better default at equal cost. The default composition now leads `opus5`
(GPT 5.6 Sol variants preferred, Kimi alternates, `opus5-xhigh` as the
Claude-native reviewer alternate, replacing the 4.8 slot); Opus 5 joins the
anthropic fork-trusted routes **before** 4.8 so the canonical Opus default
(`ANTHROPIC_DEFAULT_OPUS_MODEL`) is `claude-opus-5[1m]`. Opus 4.8's model
entry stays fully usable (existing sessions and Anthropic's own safety
fallback target it). Fable 5 becomes the named user composition `fable`
(the previous default verbatim), one Tab away — no longer the default. The
Opus 5 context bound (1M) is family-attested: the announcement states no
bound, so qualification is user-attested with a conservative validated
floor, matching the Kimi honesty rule — never called provider-safe until
near-limit acceptance.

**D32 — Pending forks self-resolve when authority lands on them; the discard
path is a command, and every fork message is actionable (2.6.0).** Evidence
(the 2026-07-27 incident): a backgrounded session was adopted by the
supervisor daemon; the operator re-entered it from the `claude agents`
menu, which forked natively; the daemon relaunched the fork, and the hook
rightly retargeted resume authority onto it — but the stale `pending_forks`
marker (recorded when the fork was first observed) was never cleared, so
the record self-blocked every resume/transition with "adopt the fork UUID
before resuming the parent" — a message that neither named the fork nor
said how, and the picker hid the fork entirely (it filtered the record's
own runtime id). The model: a pending fork means "two branches, operator
must choose"; once authority lands ON a fork id, reality has chosen, so
`reconcile_runtime_record` drops that id from `pending_forks`
(`drop_resolved_pending_forks`); doctor reports the stale state as
Attention (not BLOCKED) and `--repair-all` converges it
(`converge_pending_forks`). Genuine pending forks still block; the
resolution is explicit: adopt (`sessions link` already strips the parent's
marker failure-atomically) or discard (`sessions resolve-fork <parent>
<fork>`, metadata-only — the fork transcript is always kept). All guard
sites, the card, `sessions show`, and the SessionStart fork notice use one
message builder (`sessions.pending_fork_message`) that names the fork UUID
and both exact commands. Rejected: auto-discarding genuine pending forks
(silent branch loss); unblocking resume without a decision (the branches
can diverge — guessing is worse than blocking).

**D33 — Gateway routing is durable through the scope: `apiKeyHelper` +
non-secret base URL (2.6.0).** Evidence (same incident): the daemon
relaunches adopted sessions preserving argv (`--settings`, `--add-dir`,
`--model`) but **scrubs `ANTHROPIC_*` from its children's environment**
(verified on the live box: daemon env had the gateway vars, its spawned
pty-host had zero) → the taken-over session kept its durable files yet
every model call failed with `invalid model: claude-multi-opus-5[1m]` — a
zombie session. The durable floor now carries routing too: compiled
settings gain `env.ANTHROPIC_BASE_URL` (loopback URL, non-secret) and
`apiKeyHelper` pointing at a new stable shim
`<state>/bin/claude-multi-gateway-token` (same discipline as the hook shim:
atomic write, mode repaired unconditionally, refreshed by every launcher
run). The token value still never enters any file — the helper `exec cat`s
the existing 0600 token file at runtime. Plumbed through every compile
call site next to `hook_command` (`CatalogMeta.gateway_base_url` +
`token_helper_command`); `apiKeyHelper` joins the closed settings allowlist
(a demonstrated failure case, per D11). Rejected: embedding the token in
the scope (SPEC §9 forbids it); accepting zombie takeovers as "native
behavior" (the whole point of the durable scope is that takeovers work);
a daemon-config patch (upstream surface, not ours — D19/D23).

**D34 — Update runs are serialized and narrate themselves (2.6.0).**
Evidence: pressing U in the TUI ran the ~2-minute evidence suite with
curses still active — the card froze with zero output ("stuck / hang —
nothing happened"). `run_upgrade` now takes a `progress` callback (one
line per long phase: inspect, evidence suite with an explicit duration
note, override write, activation), and the whole flow serializes through a
FileLock sibling of the override path (two terminals queue instead of
interleaving the checkout promotion). The TUI action runs inside one
`suspended_curses` block from the start, writing+flushing each line as it
arrives; the CLI prints the same lines. Fail-closed is unchanged (verified:
the interrupted run left the checkout and override byte-identical).

**D35 — The sessions screen shows what it knows: fork markers, live
markers, and a resolve action (2.6.0).** "All potential actions should be
visible in the TUI." Managed rows carry **●** (live: owned by the
background daemon right now — best-effort, read-only glob of the daemon's
`/tmp/cc-daemon-<uid>/*/pty/*.sock` pty-socket names; degrades to no
marker) and **⚠** (fork-blocked). Pending non-authority forks appear as
annotated native rows `(fork of <parent>)`. **X** resolves forks in place
(auto-clear for the stale-authority case, confirm-modal discard
otherwise); resume on a fork-blocked row explains instead of failing
later; the keybar advertises X and ? documents the markers. A wrapped
2-row keybar no longer overdraws the message row (`KeyBar.rows(width)` is
public and screens reserve it).

**D36 — The evidence gate validates the NEW contract; promotion syncs the
suite's version pins (2.6.1).** Evidence (the first real candidate run of
`claude-multi update`, 2.1.218→2.1.220): the flow promoted the contract,
then ran an evidence suite that contains assertions pinned to the OLD
contract on purpose (validated-version literals, resolved-path/SHA-256
facts, `inspected_at`, the `catalog_version` literal — the pins are the
re-pin commit's review trail) → the gate red-lit its own promotion, 7
failures, fail-closed restore. The candidate path had never been exercised
live (2.1.218 predates the command; test fixtures fake the runner).
Promotion now syncs those literals in the same transaction (backup/restore
included; the decoupled per-model `floors = {...}` minimums are explicitly
untouched), so the suite validates the *new* contract; one fixture-style
widget test was made hermetic instead (it controls its in-memory pin).
Also from the same live run: progress is phase-numbered (`[N/M] phase`) —
detect/inspect → promote → evidence → override → activate — with a timed
heartbeat line every 15s during the two long subprocess phases (plain
lines, no carriage-return tricks: identical behavior on TTYs, pipes, and
captured streams). Verified end-to-end against the real 2.1.220 candidate:
suite green, override written, heartbeat lines observed.

**D37 — Fork credentials are revocable, reads self-heal at action paths,
and every guard/message honors the single source (2.6.2).** An 8-reviewer
adversarial pass (35 raw findings, ~25 deduped real) over 2.6.1 drove the
hardening set; every fix carries a regression test:

- **Fork credential revocation (H9):** adoption (`sessions link`) and
  discard (`sessions resolve-fork`) now bump the parent's `launch_epoch`,
  so the fork's baked hook credential (managed-id + epoch) goes stale and
  its later clear/compact hooks can no longer migrate the parent's
  authority into the fork's lineage (the review's deepest finding, verified
  against the real store). Trade-off accepted and documented: a
  still-running parent app's hooks go stale until its next launcher resume
  — the relink-runtime precedent.
- **Self-heal at action paths (H16):** resume flows converge
  resolved-by-reality fork markers (record re-derived inside the same
  flow), while display paths (picker list, doctor) keep the marker visible
  until acted on. The fork-cap no longer evicts silently — the 17th fork
  hook fails visibly (H19). Every guard site — including the in-lock
  launch backstop (H8) and ordinary/multi-fork messages (H18/H20) — uses
  the single message source with correct `--model`/`--composition`
  branching.
- **Token-path single authority (H1):** the apiKeyHelper shim's token path
  is strictly HOME-relative, matching the token's writer (proxy) and
  reader (launch) — `XDG_CONFIG_HOME` moves compositions/override but
  deliberately not the catalog-pinned token location.
- **Update flow (H2/H6/H10/H15/H7):** the "current" path deletes an
  override only when genuinely redundant (≤ the packaged baseline;
  unreadable overrides heal) and `--activate` retries land the baseline
  instead of returning early; literal sync is boundary-anchored (prefix
  pins like 2.1.2 can no longer mangle 2.1.216); invalid candidate
  artifacts are skipped with a note instead of aborting; promotion writes
  are crash-atomic with repo modes preserved; failure messages are
  phase-accurate and interrupts are handled at every entry point.
- **Launch/transition (H4/H17):** `--legacy` resume of a durable session
  rewrites the scope with the new epoch (no more false doctor mismatch);
  every cleanup/restore step is individually exception-safe (never masks
  the original error), and `restore_exec_failure` reports whether it
  restored — callers stop claiming restoration on a no-op.
- **TUI (H3/H6/H8k/H12/H13/H14):** the quick card reserves the keybar +
  Status zone up front (Status/BLOCKED and the first error line survive
  crowded 80x14 cards); KeyBar's exit binding is unclippable (ellipsis
  compaction past two rows); sessions screen has a minimum-size floor;
  SelectList/transition/Modal all reserve or clamp correctly.
- **Hygiene:** dead fork-resolution code deleted; vacuous heartbeat
  assertion fixed; env-var test restores instead of deleting; USAGE marks
  built-in vs machine-local profiles honestly.
- **Second pass (the final pre-release review of the fix set itself):**
  candidate ordering is by version key, never name order (2.1.99 lexically
  outranks 2.1.218 — a promotion ordering regression caught before
  release, U8); restore-path writes are as crash-atomic as promotion
  writes (U1); the pre-exec rollback converges the legacy-rewritten scope
  too (the exec-failure path already did); and an invalid contract
  override degrades to the packaged baseline with a doctor BLOCKED report
  instead of bricking every command — it is still never applied (D29),
  and `claude-multi update` now actually reaches the removal path that
  heals it.
- **Final gate (2.6.3):** the closing cross-family verdict was Revise with
  four items, all resolved: schema-invalid overrides (valid JSON, rejected
  version shape) escaped the heal loop — they now count as broken and the
  runtime's `broken_override_error` threads into `run_upgrade` from every
  call site, so doctor's "update removes it" advice actually resolves
  (SF1); interrupt messages were phase-inaccurate in the post-override
  window — now phase-neutral at every entry point (SF2); the details view
  admitted one line onto the reserved Status row and its fixed rows were
  unbounded (N1); the repo-file writer refuses symlinked targets (N2).

**D38 — Session lifecycle includes stop, through upstream's own CLI
(2.7.0).** Evidence (the 2026-07-27 incident): a daemon-hosted,
env-scrubbed zombie session blocked a clean resume, and claude-multi had
no way to end it — the operator had to learn native agent-view
keybindings under pressure. claude-multi already manages the lifecycle
(start/resume/reconcile/forget); stop was the missing action. Boundary
analysis: the hard rule "never touch the live daemon/supervisor" (and
D19/D23's never-configure-upstream) forbids signals, process kills, and
daemon internals — but `claude stop <id>` is upstream's *own public CLI*
("its conversation is kept"), the same category as the `claude --resume`
the launcher execs every launch. So: `sessions stop <uuid>` and **E**
"end session" on ● picker rows invoke exactly that, via the
hash-verified binary, with a scrubbed env (PATH+HOME), a timeout, and
honest failure surfacing. Guards: refuse self-stop (the session you are
inside, via the env sentinel), refuse non-live sessions (pty-socket
liveness, best-effort as in D35), interactive confirmation (or `--yes`
for scripts), transcripts never touched. The key is **E** because K
collides with the sessions screen's vim `k`=navigate-up; collisions
between screen-local bindings are resolved per screen. A transition
started on a ● live session warns first and names the command (closing
the "must be exited" gap the transition flow always documented but never
helped with). Rejected: direct SIGTERM/pkill (daemon boundary), a
claude-multi-owned kill of any kind (upstream owns process lifecycle),
auto-stopping on transition (the operator decides).

**D39 — `availableModels` covers lead + every roster selector (2.7.1).**
Evidence (2026-07-28, operator-reported and triple-confirmed): subagents
named `cm-*-sol-*` in a Kimi-led session issued `claude-multi-kimi-k3`
requests — 355 transcript entries, 993 model fields, zero `gpt-multi` in
12 hours of gateway journal; the same held for an Opus-5-led session (all
subagents ran Opus 5). Root cause: D24 narrowed `availableModels` to
exactly the lead for `/model` safety — but Claude Code resolves agent
frontmatter `model:` through the same pool (documented chain at SA
L242-251: env var → per-invocation parameter → frontmatter → session,
with a **silent skip-and-inherit** fallback), so every managed
composition was single-model in disguise; cross-family review
independence was void in practice — and per-agent effort executed on the
wrong model too (xhigh lanes ran on the lead). The fence now compiles to
`sorted({lead} ∪ {variant selectors})`; the `/model` Default entry stays
lead-pinned; `effort:`/`isolation:` are fence-independent (verified). A
manual `/model` switch to a roster model is possible again and is
backstopped honestly by the identity machinery (observed-model →
repair-needed with the relaunch guidance) rather than hidden. Radar, so
this class can never go quiet again: (1) the offline delegation probe
runs against the production-shaped fence and asserts the subagent's wire
model on the pinned binary every build — with the lead-only negative
control preserved as the incident document; that probe is the U5
acceptance (native-contract now `verified`); (2) doctor audits
user/project settings for a `CLAUDE_CODE_SUBAGENT_MODEL` env override
(step 1 of that chain — the one remaining roster-flattening vector;
pre-fix it was inert, post-fix it would recreate the incident). All 17
live scopes converge to the new pool on the next `--repair-all`.
Accepted residuals, documented not fixed: `ANTHROPIC_DEFAULT_OPUS/FABLE`
env defaults resolve outside the fence by design (canonical passthrough
routes, served); a Sol lead at harness effort `ultracode` keeps the
sol-high wire contract (reasoning.effort=high) — harness effort and
provider reasoning are different axes (lead-slot lane selection is the
candidate open item if a wire-level mapping is ever wanted); doctor
converge regenerates scopes only — the lead appendix is rewritten on
every launch, which is the correct heal point.

**D40 — Worktree review happens from the outside; EnterWorktree is never
required (2.7.2).** Evidence (2026-07-28, operator-reported): a reviewer
subagent dispatched to review another leg's worktree-isolated work failed
with `Cannot enter worktree: the current working directory
…/occams-agent-flow is the repository root, not an isolated worktree —
switching is only available to sessions whose working directory is inside
a worktree of this repository`. Root cause is a shape mismatch, not a
bug in any claude-multi code: `roles.json` gives cm-implementer
`isolation: "worktree"` while reviewers/analysts run at the repository
root; when a read-mostly agent reaches for the native `EnterWorktree`
tool to "enter" the implementer's worktree, the pinned binary (2.1.220)
refuses root→worktree switching (newer harness documentation describes
first-entry-from-launch-dir as allowed — the native tool's semantics
drift between versions, so neither behavior may be relied upon). The
decision is a contract fix in `catalog/prompts/`, no machinery: a
worktree is a plain directory, and read-mostly agents never need to
enter it — read files at its path directly, `git -C <path>
status|diff|log` for the change, `(cd <path> && <command>)` for checks;
cm-implementer now closes its report with the worktree path, branch,
base ref, and committed-state (implementers do not commit unless told,
so uncommitted work is the normal case) and leaves the worktree in
place; cm-lead passes those coordinates when dispatching review or
analysis, and integrates from its own root — committed work via the
branch, uncommitted work via `git -C <path>` export after a
`status --short` check (tracked-only pipeline; untracked files are
copied by path or committed first).
Rejected: permission denies on EnterWorktree (over-reach — blocks
legitimate native flows and cannot distinguish intent); spawning
reviewers with worktree isolation so they could hop (a wasted worktree
per reviewer, previously-visited worktrees become non-writable, and
hopping is only defined for worktrees under `.claude/worktrees/`);
patching or wrapping the native tool (the binary is hash-verified
upstream, out of bounds by doctrine). Catalog content changed, so
catalog_version 8 / launcher 2.7.2; keyword pins in
`tests/test_roles.py` lock the guidance in and the scope goldens were
re-blessed. Heal semantics: every durable launch — fresh or resume —
rewrites both the lead prompt file (static body + appendix, atomically)
and the full durable scope from the installed catalog, so after
installing 2.7.2 a plain resume picks up the new guidance with no
converge; `doctor --repair-all` and transitions are additional
convergence paths, useful for sessions that are never resumed.
Already-running (live) sessions keep their old roster prompts until
restarted — the failure mode is benign (the old refusal error) rather
than corrupting. En
passant: the sandbox derivation staged only the package tree, so the
manifest-consistency file-existence test could never pass in the sandbox
(pre-existing since the test's introduction); `tests/default.nix` now
stages the repo-shaped layout — patch files copied next to the tree from
the manifest itself (single source of truth), copies not symlinks
because `Path.resolve()` derefs them, `stripHash` for store-path names.

**D41 — Resume operability: actionable repair guidance, honest relink
semantics, and a pre-exec resume gate (2.8.0).** Evidence (2026-07-29,
operator-reported, 21-claim investigator+verifier confirmed): the
repair-needed BLOCKED card suggested a bare `claude-multi sessions
relink-runtime` that could not run as printed (missing both UUIDs) and
could not clear the state anyway (`observed_cwd` only popped under
`--cwd` — an untested, undocumented edge); daemon-owned (●) resumes were
completely unguarded and exec'd straight into upstream's bg refusal,
whose own advice (`claude agents` / `--fork-session`) is the fork
incident's on-ramp; "No conversation found" surfaced bare although the
transcript path is deterministic from record data. The fix has three
parts. (1) `sessions.relink_message(record)` — a single actionable
message source mirroring `pending_fork_message` (real UUIDs; both
`--cwd` choices labeled: keep the recorded dir, or the observed dir only
when intentionally re-homed), consumed by the card, both prepare guards,
the in-lock launch guard, the transition guard, the sessions list, the
row actions hint, `sessions show`, and doctor. (2) Bare `relink-runtime`
now also clears `observed_cwd` (the operator re-asserts the recorded cwd
by running it; `observed_model` is untouched and keeps model-only repair
on the allow-model-relaunch path). (3) A pure `_evaluate_resume_gate`
(metadata-only, no locks, no mutations) with `Runtime.perform` as the
single mandatory enforcement point: repair-needed → actionable refusal;
daemon-owned → modal offering **Stop & resume** (D38 machinery) /
**Resume anyway** (heuristic escape) / **Cancel**, with `-r --force` as
the text-mode bypass for that branch only; transcript-missing/elsewhere
→ pre-detected guidance instead of upstream's bare error (restore from
backup, or `sessions forget`; relink `--cwd` when found elsewhere).
Precommitted transition relaunches are exempt (the transition flow
already warns on live sessions — verifier nuance); interactive adapters
resolve before prepare and thread the decision; the liveness heuristic
stays a gate-with-choices, never a hard block; `--print-launch` stays a
pure diagnostic. Rows now mark repair-needed sessions `!` alongside ●/⚠.
Hardening (cross-family round 1, all confirmed then fixed): transcript
blockers outrank liveness so force can never bypass them; the
precommitted exemption narrowed to the daemon branch only (a transition
relaunch that hits gate damage now refuses and restores instead of
exec'ing into a native error); gate text is stored raw and wrapped only
at Modal presentation (hyphen-split commands at some path lengths were
corrupting the refusal text); relink guidance shell-quotes paths and
points model-only drift at the launcher reconcile instead of a no-op
bare relink; transcript-elsewhere decodes the real project path for an
exact `--cwd`; enforcement moved ahead of the launch-callback seam;
`direct --force` is placement-equivalent (SUPPRESS). Hardening
(per-issue re-review round): the card's embedded picker no longer
treats ordinary records as managed (a KeyError crash — it branches to
`prepare_direct` like the standalone picker); Stop & resume grants no
force exemption — the mandatory gate re-evaluates liveness fresh at the
launch boundary, so a stop racing a relaunch is caught rather than
bypassed; stop precheck targets the exact runtime it will stop (a live
historical alias no longer authorizes it); the ordinary model-only
prepare error routes through the same helper; the implementer report
distinguishes committed/uncommitted/mixed.

**D42 — Recovery policy: watchdog retry pin, gateway retry rejected,
resume-over-redispatch (2.8.0).** Evidence (2026-07-29, operator-reported
"API Error: An error occurred while processing"; journal + binary +
source verified): zero 429/529 and zero incident-window non-2xx in 12h
of gateway journal; the error string is absent from the client binary
(it is the upstream 500 api_error body) and from the journal (CLIProxy
does not log upstream bodies); the incident window's wire traffic was
all-Kimi (the "Sol agent" attribution was a UI line merge). The pinned
client retries 10× (429 conditional, 408/409/401/5xx/connection) with
≤32s backoff, aborts terminally when a wait exceeds 60s, and never
retries mid-stream-after-content (finalizes partial). Subagents carry
the full retry budget (agent:* querySources are protected from the
background 529-drop). Decision: managed scopes pin
`CLAUDE_CODE_RETRY_WATCHDOG=1` in the durable settings env (doctrine #8
— the D33-proven channel that survives daemon env scrubbing): 300
transient retries with per-wait backoff capped at 5 minutes (429
Retry-After waits bounded at 6 hours; there is no total-duration cap —
the honest shape), no 60s retry-after abort — a subagent or
backgrounded turn no longer dies waiting for a human to type
"continue".
**Rejected:** gateway `request-retry` at 7.2.80 — one credential per
provider means rotation has nothing to rotate to; it would re-hit the
same upstream the client already retries (the "hardcoded cooldown"
argument was itself refuted by the verifier — the correct rationale is
topology). **Unfixable at this pin:** mid-stream-after-content errors
have no retry path anywhere — documented, not fake-fixed. The lead
contract gains the operator's recovery rule: a delegated agent that dies
on infrastructure failure is first *continued* (message: it failed, why,
this is a continuation — its context survives), steered when its
approach caused it; fresh dispatch only when the approach/context was
the problem or it died twice. En passant: worktree-isolated dispatch is
impossible from a non-repo session cwd (harness spawn-time creation;
verified distinct from D40) — the lead contract documents the manual
`git worktree add` fallback; conditional isolation stays a parked
candidate (issue 005). Hardening (cross-family round 1): the watchdog
pin is scoped to managed scopes only — the shared lifecycle helper no
longer leaks it into ordinary sessions (absence test added).

**D43 — Subagent recovery is two-layer; the SubagentStop hook cannot
help at this pin (2.8.2).** Evidence (2026-07-29, offline probe against
the pinned 2.1.220 binary, fake provider, zero provider calls): the
`SubagentStop` hook fires on normal subagent completion with a rich
payload (`stop_hook_active`, agent id, transcript paths, last message),
and `decision:block` genuinely continues the subagent (5→13 requests in
probe) with `stop_hook_active` flipping true as the native loop guard —
but the hook **does not fire on API-error deaths at all** (verified
twice: retry-exhaustion death, then again with a 15s post-death window;
zero events). So no mechanical continue-on-death hook exists at this
pin, and building one would be fake automation. The recovery design is
therefore two-layer, both pin/contract level: (1) the watchdog retry
pin prevents most deaths (D42); (2) the lead contract owns recovery —
imperative since 2.8.1, and now shaped as: continue the dead agent by
message across up to **5 consecutive** infrastructure deaths (the
counter resets on any successful continuation), never summarize a death
and move on, never make the operator type "continue"; context-exhaustion
deaths get one finalize-from-what-you-have attempt; past 5 consecutive
deaths, or when the approach or context was the problem, dispatch fresh
with a narrower scope and abandon the failed agent (stop it first if it
still runs; transcripts are never deleted). An agent that completes
with reported failures is not done either — the lead addresses the
failed parts before presenting results. Radar:
`RealPinnedBinaryTests.test_subagent_stop_*` pins the boundary — if a future Claude
version starts firing SubagentStop on API-error deaths, the test fails
and the mechanical continue-hook becomes viable (recorded as U10,
verified, in the native contract).

**D44 — Lifecycle model/cwd evidence is admissible only from the main
session (2.8.3).** Evidence (issues 008 + the original observed_cwd
drift incident): a subagent-context SessionStart event can carry the
parent's session_id with a SUBAGENT's model (and, for a worktree
implementer, its worktree cwd), and the shim attributed both to the
parent record, marking healthy sessions repair-needed. The 2.1.220
compact payload carries **no agent-context marker** (probe-verified
shape: `cwd`, `hook_event_name`, `session_id`, `source`,
`transcript_path`), so markers alone cannot fix it. The rule: for
**managed** sessions — the production bleed case — compact events are
model/cwd-inadmissible unconditionally (a managed model change is a
transition whose start event reports the model; anything else is
re-observed at the next start/resume — the flag self-heals, observed
live). `agent_id` / `agent_transcript_path` / `/subagents/` markers are
also honored as defense-in-depth for any payload shape that carries
them. Ordinary sessions keep compact-model reconciliation — that is
their in-session `/model` tracking path. **Accepted residual
(documented):** an ordinary session's subagent (a project agent with a
different model) could in principle bleed through an unmarked compact
event the same way; there is no discriminator at this pin, ordinary
compact-model is load-bearing, and no such case has been observed —
flagged in issues/008 rather than "fixed". A genuine native `/model`
switch in a managed session is now noticed at the next start/resume
event rather than at the next compact — delayed, never lost. A
fixture reproduction of a subagent compaction was attempted and did not
produce one (possibly subagents do not compact at 2.1.220 — consistent
with issue 007); the managed rule does not depend on that question.
`SubagentModelBleedTests` + `SubagentModelBleedOrdinaryTests` pin the
behavioral branches (compact inadmissibility, no-clear of a legit flag,
marker branch on synthetic shapes, ordinary preserved, resume still
observed).

**D45 — GLM-5.2 joins the qwen provider as a first-class model (2.9.0).**
Zhipu's GLM-5.2 is served by the same Alibaba Token Plan endpoint as
qwen3.8 (`apps/anthropic`, bearer `QWEN_CLAUDE_API_KEY`), so it lands as
a `glm52` model under the existing `qwen` provider — no new provider,
no new secret, no gateway topology change. Evidence: the HF model card
(1M context; evaluation configurations up to 163,840 generated tokens —
a serving-level output cap is not separately attested; text-only;
`reasoning_effort` parameter) plus a user-approved canary against the
production endpoint — HTTP 200 with a default-on thinking block for
wire id `glm-5.2`. Catalog: wire
model `glm-5.2`, selector `claude-multi-glm52-max[1m]`, capabilities
lead+agents, single lane **max** (`agent_effort: max`) carrying the new
`reasoning-effort-max` payload contract, which renders as a
`reasoning_effort: "max"` payload override bound to the glm52 alias in
the qwen `claude-api-key` section. Independence family is `alibaba`
(same as qwen38): cross-family review requirements are unaffected —
Sol/Kimi remain the cross reviewers for GLM-authored work. Context:
1M attested per the opus5 pattern (`provider_tokens` 1,000,000,
`validated_tokens` 200K floor, near-limit qualification text). No
CLIProxy registry patch is needed: third-party aliases enumerate through
`claude-api-key` sections, unlike the anthropic-registry opus-5 route.
Minimum tested Claude floor is 2.1.216 (the qwen38 floor — glm52 needs
nothing newer). User compositions `glm-sol` (GLM lead, Sol preferred,
GLM alternates — the qwen-sol shape) and `kimi-sol-qwen-glm` (kimi lead;
per role Sol preferred, then kimi-k3/glm52/qwen38 alternates) live in
`~/.config/claude-multi/compositions/` (0600); they resolve once catalog
12 is activated. **GLM/Qwen parity (operator):** glm52 and qwen38 carry
byte-identical routing hints and are both non-preferred alternates, so
neither is ranked — listing order is presentational only. When qwen3.8
ships production (the D21 revision sequence), qwen38 moves ahead of
glm52 as the preferred escalation. Pins: `test_catalog` (model set,
alias split, floor),
`test_render.test_glm52_alias_and_max_reasoning_override_rendered` +
the re-blessed gateway golden (alias block + override, nothing else
moved).

**D46 — Ordinary gateway sessions launch from the card (2.10.0).**
Fresh ordinary sessions were CLI-only (`claude-gateway` / `direct`);
the TUI is the main way in, so the card gains **G new gateway** (shared
keybar tail, available on every card state like S — it is explicitly an
escape from the current card, never an operation on it, which the label
and help wording state). The picker (`_OrdinaryScreen`) lists exactly
the compiler's accepted ordinary-lead domain — models `direct_context_profile`
accepts, enumerated by `compiler.ordinary_launch_models`, so agent-only
or profile-less models never appear and a row can never fail validation
after Enter. **Groups are the safety surface**: sections are ordinary
context profiles (the native /model fence of the launched session);
rows are models, not lanes (lanes switch in-session); the launch uses
the model's default selector and the cursor starts on `sol`, matching
the CLI default. Enter maps to `("perform", prepare_direct(fresh),
None)` with the card's passthrough threaded — curses tears down before
`perform`, the exact `_open_sessions` contract; Esc creates nothing.
Rejected alternatives: a preset (presets are compositions; ordinary has
no document — would break E/W/transition), a row inside S (S is
resume/manage; fresh launch belongs on the card), a flat `SelectList`
(no group headers, and its `enabled=False` is cosmetic — Enter still
activates; making disabled rows real meant changing a shared widget's
semantics for every existing caller, a bigger blast radius than a
self-contained screen), and remembering the last-picked model
(nondeterministic; revisit if usage data says so). **Availability is
honest about what it measures**: `(no secret)` rows reflect render-time
availability — the gateway omits providers rendered without their
secret (the shared `render.unavailable_providers` helper, parity-pinned
against the renderer's own report). It is not a live-serving guarantee
(the running gateway may still serve an older config), so Enter
**rechecks the secret file** and asks for explicit confirmation
(default Cancel) instead of a hard block; the marking speaks for the
initial model only — the in-session /model set is the whole profile by
design (dynamic scope filtering rejected: scope determinism, resume
identity). Rows stay narrow — the full reason lives on the selected
row's detail line. Two adjacent fixes shipped with it (design review
findings): the CLI `direct` path now warns (non-blocking, never on
`--print-launch`) before launching with a missing provider secret —
previously a silent guaranteed-broken session; and the line-mode S
hint now names the correct ordinary resume form (`claude-gateway
--resume`) alongside the managed one. Line mode gains `g` (grouped
listing + CLI hint). Catalog stays 12 (code-only); `bundle_sha256`
still rotates via version.json — expected record hash drift, not
catalog drift. Blueprint reviewed cross-family (Sol xhigh,
SOUND-WITH-ADJUSTMENTS) — all twelve findings addressed above; the
implementation review (same reviewer, BLOCK→fixed) added: the
advisory probe never raises — a malformed/unsafe secret env file
degrades to a static `secret env file unavailable or invalid`
marking on every direct provider instead of crashing the card, line
mode, or a direct CLI launch (OAuth providers skip the probe
entirely), and `parse_secret_env` now translates invalid UTF-8 into
`ProxyError` at the source, closing a pre-existing crash of the
managed plan path on non-UTF-8 secret files that the new tests
surfaced; the CLI warning flushes before `perform` (execve never
flushes Python buffers) and matches the modal's honest "may fail
unless the running gateway still serves an older config" wording;
detail/modal text wraps to the available width (worst-case reserve in
the floor formula, so the size floor is stable per width) — the
complete `env:NAME` reference survives a 44-column terminal.
Pins: `OrdinaryLaunchModelsTests`, `OrdinaryScreenTuiTests` (render,
navigation, confirm modal, recheck-at-Enter, floor boundary 16/17,
minimum-width reason, malformed-file degradation),
`OrdinaryCardKeyTests` (intent shape, record fields, passthrough,
transient notice, keybar/footer labels, line mode, CLI warning,
OAuth-skip, flush-before-launch spy),
`test_render.test_unavailable_providers_matches_renderer_report`.

**D47 — Operator-observation batch: picker display truth, delegation
contract completion, cwd-first sessions, distinguishable names (2.11.0).**
Five items from one operator report, each root-caused before any edit
(issues 009–013). **(009)** The in-session `/model` picker at 2.1.220
shows registry-derived rows (the Default row pinned by our env, any
allow-list-permitted built-in Anthropic rows like Fable 5) and always
appends the current model as a Custom model row; our custom selectors
never become rows (kimi/qwen/glm fail the opus|sonnet|haiku substring
rule, the opus aliases die at the registry-label lookup) — all
binary-verified, and the typed `/model <selector>` path validates
against the allow-list, so hidden selectors still switch in-session.
The allow-list is intact; only the
display is filtered, so the fix is documentation (USAGE both /model
sections, UX §1.5, G-picker help) plus deleting the stale "managed
/model menu is roster-shaped" claim — the same filter applies to managed
rosters. **(010)** Subagent TaskStop refusals are native ownership: the
main session stops any task; a descendant cannot stop another agent's
tasks (delegated children are not its own) and is refused — verified
against the binary's refusal paths. The D43 abandon rule said nothing
about descendants, so nested agents flailed; `cm-lead.md` now states the
boundary (descendants report stuck agents upward), and the non-lead
delegation clause notes their own background shell/monitor tasks are
unaffected. **(011)** `Agent(general-purpose)`
denials were our own native-agent policy working exactly as designed
(`--disallowedTools` in the argv goldens); the noise source was role
prompts that said "may delegate" without naming the legal types — a
delegation clause (cm-* types only; native generic agents are not valid
substitutes and may be denied by the effective policy — policy-neutral,
since the schema legitimately allows `explore: native` /
`general_purpose: on`) now sits in all three non-lead prompts.
**(012)** `_SessionsScreen` opens
cwd-filtered (harness-style; C widens); resume-cwd correctness was
already guaranteed by the fd-pinned `_CwdLease` (verified, no change).
**(013)** `--name` gains the project basename via
`compiler.session_display_name` (`cm:kimi-sol@project`,
`cg:glm52@project`; printable, 24-char cap; resume/transition thread the
RECORDED cwd; compiler callers without `session_cwd` keep the plain form
so goldens stand). Resume-by-name keeps working for the qualified form:
`_resolve_resume_target` matches the generated display name
(`cm:<composition>@<project>`) alongside UUIDs and plain composition
names, with the same ambiguity listing (review must-fix).
Prompt-bloat audit: lead 8.3K chars total, role
prompts ~2K each, --agents 14KB/6 variants — healthy, nothing trimmed.
Catalog 13 (role prompts are catalog content); goldens re-blessed for
the prompt additions only. Pins: `test_roles` keywords,
`CwdFilterToggleTests`, `SessionDisplayNameTests`,
`SessionNameWiringTests`, qualified-name `_resolve_resume_target` tests.

**D48 — Ordinary model switch from the sessions screen (2.12.0).**
The TUI had no model-switch path for ordinary sessions (T was refused
with "relaunch with claude-gateway …"), leaving the CLI as the only
route — a gap now that G makes ordinary sessions first-class. T on an
ordinary row: refuses fork-blocked rows, opens the G picker
(`_OrdinaryScreen`, purpose-aware: launch vs switch title/keybar/help/
confirm copy) preselected on the current model, no-ops a same-model
pick (and never asks the missing-secret confirm for it — no relaunch
happens), confirms old → new with the same/cross-profile line, and only
THEN runs the resume gate — picker/no-op/confirm are all cancellable
before the gate's repair/stop actions can mutate (review major), and
the full R gate modal gives repair/stop/force/transcript guidance
parity (review must-fix), threading the refreshed record and decision
into the resume intent whose new 4th element carries the picked model.
Both resume consumers (card `_open_sessions`, standalone
`_sessions_list_tui`) thread it into
`prepare_direct(resume, model_id=picked)`, so the switch
reuses the exact explicit-model relaunch the CLI performs. Same-model
picks are a no-op message; every cancel step stays in the picker. The
keybar reads "T switch" (composition for managed, model for ordinary);
the row hint and SESSIONS_HELP say both. This also completes the issue
009 answer: the in-session /model display filter is native, but the
launcher now offers three switch paths for ordinary sessions — typed
selector, T in the picker, CLI relaunch. Catalog stays 13 (code-only);
launcher 2.12.0. Pins: `OrdinaryModelSwitchTests` (flow, preselection,
no-op, cancels, daemon-owned gate resolution/cancel, force threading,
cross-profile confirm text, card + driver consumers), `_OrdinaryScreen`
purpose/fallback tests.

**D49 — Opus 5 unscoped to all roles; two roster-rich profiles (2.12.1).**
`compatible_roles` for opus5 widened from lead+reviewer to all four
roles (catalog 14): the D31 restriction was positioning, not a safety
boundary, and the operator asked for opus5 as a general agent option —
treated like any other model. Slot order and the absence of
analyst/implementer routing hints keep it deliberately understated
(cost sits with the router, not the gate). Two user compositions built
on it: `kimi-sol-qwen-glm-fable` (kimi lead; per role sol preferred →
opus5 → kimi → glm52 → qwen38, with **fable as the tail specialist** —
its existing implementer hint "exceptional end-to-end scope requiring
sustained coordination" plus the tail position route only the hardest
work there, reviewer-tail = finalizer) and `fable-sol-qwen-glm`
(fable lead, default-shaped; sol preferred; opus5/kimi/glm52/qwen38
alternates — the enriched sibling of the plain `fable` profile).
Naming follows the lead+preferred+pool convention; the trusted
`default` seed is deliberately untouched (a named profile is one Tab
away). No hint changes in models.json (they are global).

**D50 — Operator batch 2026-08-10: qwen3.8 production, MRU composition
order, on-the-fly ingestion, providers pane, TUI fixes (2.13.0).**
Blueprints `blueprints/014-018` (one folder per item). Five landings:

1. **qwen38 production flip (016, the D21 sequence executed).** Wire id
   `qwen3.8-max-preview` → `qwen3.8-max` (GA 2026-08-03; the official
   Token Plan Claude Code doc's examples now name the production id, and
   its 983616 context recommendation stands unchanged). Display drops
   "· Preview"; routing_note is lean production language (operator:
   configs stay production-ready, no process/dates residue). Effort
   tiers unchanged (xhigh stays the provider max). Per the D45 parity
   note, qwen38 slots now list **ahead of glm52** in the three
   claude-multi-authored profiles that carry both (slot order is
   presentation order — D45's "moves ahead" executed; `preferred` flags
   untouched, Sol stays the workhorse). Operator-authored compositions
   were not reordered (reported instead). Golden re-bless delta = the
   two intended lines only. The one live acceptance call stays
   approval-gated at activation.
2. **MRU composition pick order (015 D-a).** All pick surfaces — Tab/P
   cycling, transition/adopt choosers, `compose list` (which gains a
   last-used column) — order by most-recently-used, derived from session
   records (per-cwd tier first, then global, then never-launched
   alphabetical). **Zero new state** (D3): `last_seen_at` is
   hook-refreshed; records of deleted compositions never resurrect them;
   a composition used here AND elsewhere appears exactly once (review
   must-fix).
3. **On-the-fly ingestion: `--composition-file PATH|-`.** Launch-once
   semantics with file provenance: schema+version validated, resume
   rebuilds from the recorded snapshot (R1 P2), nothing is written to
   the store. `-` reads stdin and forces noninteractive; the read is
   bounded at the strict-JSON limit + 1 (review should-fix). Mutually
   exclusive with `--composition`; refused unconditionally with
   `-r`/`-c` (R1 P1 — a file is never verifiably the recorded intent,
   even when its name matches; review must-fix). Rejected: a TUI
   "pick model → generate composition" generator (availability mutation
   would be silent state change; the editor path is already short).
4. **Providers pane inside G (018).** Four-lens design evaluation
   (UX/gateway/critic + lead-finalized secrets lens) converged: no new
   card key, no global active-model state (a third intent authority),
   no gateway mutations, no management API, no live provider discovery.
   What landed: **P** in the G picker opens a read-only provider status
   pane (credential-source facts by name/count only, rendered/served
   selector counts, exact remediation) with **masked direct-key entry**
   into the standard `~/.config/secrets/claude.env` (operator steer;
   0600 atomic parse-preserving `proxy.set_secret_value`, value
   shape-validated, never echoed — confirmation shows name+length only)
   and OAuth login-command guidance. The missing-secret confirm modal
   and line-mode listing gain the exact connect instructions. Honesty
   vocabulary everywhere: present/rendered/served are separate claims;
   "served" never means "connected".
5. **Doctor gateway radar + small surfaces (015 C/D + sweep fixes).**
   `_gateway_snapshot` (shared by doctor and the providers pane):
   loopback `/v1/models` served-set vs rendered aliases — aliases ONLY
   (cross-family review + live verification proved CLIProxyAPI 7.2.80
   never serves direct wire names; the first cut would have BLOCKED
   forever on a healthy gateway); on-disk-config byte-drift vs fresh
   render (catches wire-only remappings like 016, which /v1/models
   cannot see); OAuth no-credential-record → login command instead of
   restart advice. G picker rows show typed `/model` selectors (009
   discoverability; the native display filter is binary-fixed).
   `compose list` last-used column; `models` gains selector ids;
   QUICK_HELP names H/U; gateway-down messages name the start command;
   secret problems name the env file path. Editor gains **^O** for the
   Save-or-launch menu (014; ^S rejected — IXON/XOFF; bare letters
   rejected — text rows own printable keys; reviewer empirically
   verified ^O delivery on pty). Dark-theme `dim` becomes readable
   (017: 256-color 245 dark / 240 light, 8-color fallbacks white/black —
   the bold-black black-on-black trap closed; ANSI mirror 38;5;245/240).
Suite: 1,509+ tests green; sandbox green; cross-family review
(sol-xhigh revise→fixed; glm52 approve; qwen38 sweep GOOD) with
adversarial verification of the critical finding.

**D51 — Post-release improvement pass; provider listing verified per-provider (2.14.0).**
A six-leg agent evaluation of the 2.13.0 surfaces (discoverability,
coherence, onboarding, safety, Kimi usability walk, lead friction pass)
drove a refinement batch (blueprint 019): first-run dead-ends closed
(BLOCKED cards point at G→P masked entry; OAuth no-record rows marked
`(sign in needed)` with the login command; line-mode H exists as
promised); secret writes hardened (FileLock + candidate re-parse + exact
export preservation); wording/runnability unified (`systemctl --user
restart cli-proxy-api` everywhere, restart-between-turns timing);
editor/doctor/pane feedback gaps closed (BLOCKED message visible, drift
banner, transient-skip line, drift early-return). Two structural
additions: (1) **`claude-multi discover PROVIDER`** — explicit-invocation
provider model listing; the endpoint matrix is now VERIFIED, not assumed:
Kimi answers Anthropic-shape `GET /coding/v1/models` (x-api-key), the
Qwen Token Plan's apps/anthropic path does not (404 "Not support"), OAuth
pools have no direct credential. The 015 D-d rejection of live discovery
was premised on unverified endpoints; explicit user invocation is the
approval mechanism, and output marks cataloged vs onboarding candidates
(discovery still cannot populate compositions directly — metadata gates).
Kimi's listing advertises `context_length 1048576` for k3 → the kimi-k3
qualification upgrades from user-attested to **provider-advertised** (the
not-benchmark-verified phrase stays). (2) **`claude-multi-dev model add
--like`** scaffold (mechanical fields inherited; QUALIFY markers at
judgment fields) plus `--help` and the promote runbook — the mechanical/
judgment split is now encoded instead of tribal.

**D52 — Custom providers & ordinary models: the operator registry (2.15.0).**
The "switch to newer models when released" funnel, completed: discover
(015) → mark in the TUI (this) → try in an ordinary session → adopt into
the catalog for compositions (019 scaffold). `custom.json` (0600, schema
`custom.schema.json`) holds operator-added **providers** (Anthropic-
compatible endpoint + key env var; type field is anthropic-only in v1) and
**models** (wire id + provider + the safety fact `context_tokens`).
Customs are ordinary-session-only by construction — never visible to
composition resolution (R1/D3: the trusted catalog is untouched; the
merge happens only in `Runtime.ordinary_docs` and the render path).
Safety mechanics: each distinct context bound is its own picker
group/fence (compaction can never strand a small model into a 1M policy);
the renderer emits them as plain direct routes so the doctor served/drift
radar covers them automatically (an unapplied add = config drift problem
naming init+restart). TUI flows (providers pane): **N** new provider
(id → endpoint → auth kind → env var name → masked key only when the
variable is unset), **A** add models (explicit fetch confirm = the
per-call approval; checkbox marking with advertised context; 404/unsupported
→ manual type-in fallback), **D** on a custom picker row removes (confirm),
Enter on a custom provider row offers key-replace/remove. Models browser
(M in G) lists customs as ordinary-only. The Kimi listing supplies real
context bounds (262144/1048576) — the provider-stated value flows into
the fence, never a guess.

**D53 — Deep system analysis hardening batch (2.16.0).**
A six-lane analysis workflow (core / tui / gateway / compose / hygiene /
security) with adversarial verification swept the whole integration after
the 2.15.0 funnel work (blueprint 021). No P0s; the landed set is the
P1/P2 findings, each with regression pins. Decisions worth recording:
(1) **Corrupt records are forgettable load-free** — the remedy for a
corrupt record must not dead-end on the same parse that defines the
corruption: scope removal + a by-id pointer sweep (re-read under the
FileLock before unlink) run without loading. (2) **A renamed project
directory is a resume gate, not a launch error**: transcript present
under the recorded slug + recorded cwd gone → the `cwd-missing` gate
names both exits (rename back / move transcript + relink) before any
launch attempt. (3) **`sessions forget` trusts the same liveness as
`sessions stop`** — forgetting a running session deleted its scope from
under it; the CLI refuses live (naming `sessions stop`) and self, with
the check running UNDER the lifecycle lock (`pre_delete_check` with a
fresh prefix scan — a pre-lock verdict is stale by deletion time); the
picker forget shares the same guard (live rows get stop-first guidance). (4) **The managed SessionStart equivalence mirrors the
ordinary resolver**: for ≥1M leads the canonical `wire+'[1m]'` report
form reconciles (a miss recorded false model drift); a recorded lead
missing from the catalog no longer KeyErrors the hook — the runtime-id
reconciliation still runs and the unverifiable report lands as
informational drift (R1 P2). (5) **A 401 from /v1/models is a doctor
problem**, not an advisory skip: the daemon holds an older config and
every session would 401 while healthz stays green — restart is the
action. (6) **Credential-carrying fetches never follow redirects**
(urllib forwards Authorization/x-api-key, including to a downgrade
target); a 3xx is just a failed listing. (7) **Registry mutations are
one FileLock transaction**; OAuth-pool providers can't back custom
models; header auth is fail-closed to `x-api-key` (the pinned gateway
silently falls back to Bearer otherwise). (8) **Hand-written registry
entries never shadow the catalog** — merge drops them loudly
(`merge_conflicts` → doctor attention). (9) **Cycling away from unsaved
composition edits asks first** (curses modal + line-mode [y/N]); a
committed-but-unconfirmed write reports "Committed, durability
unconfirmed", never "unsaved". (10) **`package.nix` filters
`__pycache__`** — test artifacts reached the store share tree and
churned the source hash.

**D54 — DeepSeek & OpenRouter providers; the 500K `grok` profile (2.17.0).**
Two Anthropic-compatible providers join the catalog (blueprint 022):
**deepseek** (`https://api.deepseek.com/anthropic`, x-api-key, family
deepseek) carrying `deepseek-flash` (wire `deepseek-v4-flash`, the
latest-alias) and **openrouter** (`https://openrouter.ai/api` Anthropic
skin, x-api-key, family x-ai — the review family tracks the model maker,
not the aggregator) carrying `grok45` (wire `x-ai/grok-4.5`). Decisions
worth recording: (1) **the Anthropic-path effort vocabulary is
`output_config.effort` {low, high, max}** — no xhigh, no medium,
`budget_tokens` ignored (verified docs); `reasoning_effort` is
OpenAI-path-only for DeepSeek. New render contract `output-config-high`;
flash lanes: high (default; the lead enters through it — the catalog
pins the top-level selector to the default lane) + max. (2) **A 500K
model gets its own ordinary profile** (`grok`): the fence is the bound
(neither `sol` 258K nor `large` 1M fits); the window comes from the
profile-derived scope env (`CLAUDE_CODE_AUTO_COMPACT_WINDOW=500000`),
never from a `[1m]` selector — grok45 is not 1M-class. Two schema enums
gained `"grok"` (models ordinary_profile, session context_profile).
(3) **Model ids stay squashed** (`grok45`, per qwen38/glm52 convention;
the composition slot pattern rejects dots) while wire_model patterns
(catalog + custom registry) gained exactly-one-`/segment` for OpenRouter
slugs. (4) **Listing becomes descriptor-driven** (`_LISTING_SUPPORT`):
status (verified/attempt/unsupported) + url/auth/shape overrides;
OpenRouter's listing is public (`auth: none` — no secret resolved; the
pane's query modal says so) and OpenAI-shaped (name/context_length/
top_provider.max_completion_tokens/reasoning.supported_efforts mapped);
deepseek lists via its documented OpenAI-shape `GET /models` (Bearer) —
verified 2026-08-12; the Anthropic path 404s. (5) **The Anthropic skin's
non-Anthropic behavior was probe-gated, then verified**: OpenRouter only
guarantees Anthropic first-party models; the 2026-08-12 probe battery
verified grok45 through the skin (well-formed tool_use, canonical
streaming block order, effort params accepted) — the third-party
empty-result failure mode did not reproduce. grok45's reasoning is
mandatory (high/medium/low; high is the top), so its single `high` lane
with no contract is the exact shape. (6) **grok45 tiered pricing** (>=200K prompts bill $4/$12
for ALL tokens) rides in the qualification/routing text. Compositions
(operator-level): `deepseek` (all-flash side-task rig) and
`grok-deepseek` (grok lead, flash agents, cross-family review). deepseek
in `large` keeps the profile window at 983616 (min member bound — the
fence protects the smallest member).

**D55 — Same model via multiple routes: one catalog entry per (model,
route), everything route-scoped.** A model reachable through two
providers (glm52 direct vs via OpenRouter; Sol via the codex pool vs a
hypothetical OpenRouter route) is TWO catalog entries, never one entry
with two routes: each entry carries its own provider, wire_model (the
provider's slug), selectors, lanes, contracts, and — critically — its
own context block. Route bounds genuinely differ (kimi's 262,144
provider-stated pre-canonical route vs the 1M canonical route; sol's
codex subscription budget vs the API maximum), so context/effort/
qualification are route-specific by construction. Conventions: the entry
id names the route only when ambiguous (`grok45` is the OpenRouter
route; a future direct-xAI entry would be `grok45-xai`); display names
carry the route on duplicates (`GLM-5.2 (OpenRouter)`); aliases stay
unique per entry (the served radar covers each); `discover`'s
wire→catalog mapping is provider-scoped (fixed in 022 — a same-wire
other-provider entry no longer misreports "already cataloged"); the
catalog rejects duplicate (provider, wire) pairs. `independence_family`
tracks the model MAKER, not the aggregator (OpenRouter's grok45 is x-ai;
a glm52-via-OpenRouter entry stays alibaba) — per-model family override
is deferred with a trigger: the first second-family model on an
aggregator provider.

**D56 — Sol codex-route budget correction (258,400) with a recorded
revert path.** Recurring mid-turn "Prompt is too long" failures on sol
subagents (the agent visibly worked, then died) were pinpointed to the
codex OAuth route's July 2026 budget cuts: the v0.144.5-era 372K figure
(500K total = ~372K input + 128K output reserve) no longer holds — the
server catalog advertises context_window 272000 at 95% effective
(~258,400; the 1.05M API maximum never applied to this route). Our
compaction trigger sat at ~316.8K, so sol agents grew past the real
ceiling before compacting. sol + gpt55 now fence at provider_tokens
258,400 (reactive trigger 214,560 — always inside the ceiling with a
turn of margin); provider_stated_limit_tokens records the 272,000 server
figure. Evidence class: server-catalog observation + convergent
third-party reports + the operator's recurring-failure report; NOT
benchmark-verified (a bounded approval-gated probe settles the true cap
if failures persist). REVERT is one block: restore
client/provider/scalar/validated to 372000 when the server catalog
returns to 372K+ (the qualification text carries these instructions).
This is the kimi lesson generalized: route bounds move; the catalog
entry is the route's verified truth, not the model's marketing maximum.

**D57 — Sol joins the 1M class (official subscription-route 1M
enablement, 2026-08-12).** OpenAI documented a 1M-token context window
for GPT-5.6 Sol on the ChatGPT/Codex subscription route (client-asserted
budget: `model_context_window=1000000`, compaction ~900K). This
supersedes D56's 258,400 correction (which was right for the cut state).
Sol now: client/provider 1,000,000, declared/provider-stated 1,050,000
(the documented window), validated 372,000 (the last historically-proven
bound — the 1M budget is provider-documented, not benchmark-verified;
the bounded acceptance probe settles it), selectors gain `[1m]`
(`gpt-multi-sol-high[1m]` / `gpt-multi-sol-xhigh[1m]`), and
ordinary_profile moves `sol`→`large` (the sol-only profile retires;
ordinary sol sessions join the 1M-class /model fence). Scalar null
(1M-class never constrains the process). gpt55 keeps the D56 fence (no
1M support for that model). Consequences: the all-1M default rig exports
NO process scalar (an inherited cap is actively unset); a mixed rig's
scalar derives from the remaining sub-1M members (qwen38: 983,616). The
trigger at the 1M lead window is 882,000 (ordinary sol sessions fence at
the large-profile min: 983,616 window, 867,254 trigger) — marginally more
conservative than the documented ~900K compaction point. Acceptance
verified 2026-08-18: 343,541 input tokens accepted through the codex
pool (bounded approval-gated probe); validated_tokens carries it.

**D58 — DeepSeek V4 Pro GA (0813) under the stable alias; Flash→Pro
pipeline.** DeepSeek-V4-Pro-0813 is the official release superseding
Preview; first-party API calls use the normal `deepseek-v4-pro` alias
(the pricing/model table pairs that id with version Pro-0813). The dated
`deepseek-v4-pro-0813` string is not a documented first-party callable
id. Flash remains `deepseek-v4-flash` and still resolves Flash-0731 — no
wire migration for either alias. New catalog entry `deepseek-pro`:
lead+agents/all roles, 1M `large` profile, scalar null, high+max lanes
using the existing DeepSeek Anthropic-path `output_config.effort`
contracts (no new provider/adapter/schema; no xhigh lane — it collapses
to high). Default lane high (the cheaper lane; compositions pin max for
implementation/finalization); validated_tokens stays 200000 after the
approval-gated route call because it is the docs floor and 1M was not probed.
The corrected Pro-max request (2026-08-19) omitted `tool_choice` and returned
HTTP 200 with thinking + one valid model-selected tool block.
Qwen3.8 Max and GPT-5.6 Sol stay stronger general options — Pro is a
specialized broad/low-error architecture, production-fix, and
finalization lane.

Routing evidence is tiered: official docs own GA/alias/context/effort/
features; the two-part single-codebase community benchmark informs use
cases only. Measured policy: Flash preferred for bug hunting, edge/error
paths, and cheap/consistent exploration; Pro preferred for architecture,
refactoring plans, production implementation, propagation, regression
coverage, and fact-check/finalization. Composition placement: `deepseek`
becomes Pro lead + Flash-preferred analysis + Pro-preferred max
implementation + Flash-preferred max review (same-family review honestly
reduced); `deepseek-flash` preserves the previous all-Flash cheap rig;
`grok-deepseek` becomes Grok lead → Flash scan → Pro max implement/finalize
→ Grok preferred cross-family review (Pro alternate); the existing
`sol-qwen-glm-deepseek-flash` keeps its compatibility name but gains Pro
max alternatives in every agent role. Trusted default unchanged.

Cost/variance stays out of the catalog contract: current Pro miss/output
prices are 3x Flash (cache hit ~3.14x), concurrency 500 vs 2500, peak
rates double 01:00–04:00 and 06:00–10:00 UTC. Community-observed Pro
run variance is medium-confidence guidance, not permanent routing fact.
Catalog 18→19; Pro itself is catalog-only (D59 subsequently raises the
combined batch launcher to 2.19.0 for OpenRouter output-config-xhigh).
Activation re-renders/restarts the gateway. Attempt 1 proved the rollback:
a forced named `tool_choice` was rejected by Pro thinking, so catalog19 AND
the catalog18 XDG composition set were restored (HM rollback alone is
insufficient). After the reviewed omitted-choice amendment, attempt 2 passed
the corrected Pro-max call and catalog19 was activated finally at generation
129. No gateway filter silently rewrites caller intent.

**D59 — Grok 4.6 replaces 4.5; exact OpenRouter slug, xhigh requested.**
Catalog19 removes `grok45`/`x-ai/grok-4.5` and adds `grok46`, wire
`x-ai/grok-4.6`, display `Grok 4.6`; no active 4.5 route/selector or
post-activation composition remains. Context/profile stay 500K/`grok`
(trigger 432K). Grok 4.6 is on par with GPT-5.6 Sol/Qwen3.8 Max and is
positioned for long-running agents, coding, knowledge work, and ambitious
interactive work. xAI documents low/medium/high/xhigh (default high), so
the catalog exposes high fallback + xhigh default. OpenRouter's Anthropic
Messages route is pinned explicitly with
`output_config.effort=high|xhigh`; the xhigh adapter contract is new.
Approval-gated exact-slug calls (2026-08-19) live-verified high/xhigh thinking
and tool blocks plus xhigh SSE ordering; 500K near-limit remains unprobed.

OpenRouter's moving alias `~x-ai/grok-latest` exists and currently resolves
4.6, but is deliberately NOT the trusted wire. This is D3, not syntax
preference: record=intent + catalog=trusted source + scope=pure function of
(record,catalog), no hidden state. A moving alias introduces a third mutable
upstream authority that can silently retarget to 4.7 while the record,
catalog hash, 500K profile, qualification, and compiled scope stay stale.
The upstream response model echo cannot repair this: local `served_models`
checks configured selectors only, SessionStart reconciles recorded client
selectors, and the response echo arrives after scope compilation. Exact
`x-ai/grok-4.6` is therefore the reproducible route; the tilde alias is
recorded as considered/current-target evidence only.

This is an in-place amendment to pending catalog19 (no catalog20).
`output-config-xhigh` changes renderer behavior, so launcher becomes
2.19.0; catalog remains 19. Planned catalog19 compositions replace
`grok45` with `grok46` and request xhigh for every Grok variant; catalog18
rollback fixtures retain 4.5 by design. Census before activation: zero
session/scope references to 4.5; recheck at activation. If any ordinary 4.5
record appears, explicit `--model grok46` is a same-profile re-pin; managed
4.5 snapshots need a composition transition, not repair-time refresh.

**D60 — Final outbound boundary strips `prompt_cache_retention` for all
non-Claude routes.**
The Sol/Codex subscription backend rejects the OpenAI Responses-platform
cache TTL control with HTTP 400 (`prompt_cache_retention is not supported
on this model`); the same field leaks through ordinary
`/v1/messages?beta=true` traffic. Route-support matrix (loopback-verified
against pinned CLIProxyAPI 7.2.80):

| Route | 7.2.80 unpatched | After the patch |
| --- | --- | --- |
| Codex `/responses` HTTP (stream + non-stream) | stripped early | stripped at boundary (`cacheHelper`) |
| Codex `/responses/compact` | leaked | stripped at boundary |
| Codex WebSocket non-stream `response.create` | stripped early | stripped at boundary |
| Codex WebSocket stream `response.create` | leaked | stripped at boundary |
| Third-party Claude-compatible `/v1/messages` (Kimi, Qwen/GLM, DeepSeek, OpenRouter; stream/non-stream/count_tokens) | leaked | stripped at boundary |
| Official Anthropic endpoint (resolved HTTPS `api.anthropic.com`, default/443 port) or default base URL | preserved | preserved (untouched) |
| xAI HTTP + WebSocket | stripped early | early cleanup removed, stripped at final preparation |
| OpenAI-compatible platform Responses | preserved (supported) | preserved (executor untouched) |

Rulings:

- **Final-boundary invariant, not early cleanup.** One shared idempotent
  helper (`stripPromptCacheRetention`) applied after every transformation
  that can mutate the body — Codex cache-key insertion/identity rewriting,
  Codex WebSocket frame construction, Claude translation/payload
  rules/normalization, xAI final preparation. Removes **all** top-level
  occurrences (duplicate keys included), preserves `prompt_cache_key` and
  nested fields, and fails closed with an enforced postcondition (the
  outbound body must be valid JSON and provably field-free, otherwise the
  request errors instead of sending an unprovable body; empty/nil bodies
  fail closed as invalid JSON). On the Claude message paths the strip
  runs **before CCH signing**, so the signature covers the sanitized body
  and no later transformation can reintroduce the field — two end-to-end
  tests (non-stream and stream, custom base URL + OAuth-shaped token)
  assert retention is absent and the emitted CCH recomputes exactly over
  the sanitized outbound body; `count_tokens` strips after its final
  sanitizer (it has no signing step).
- **Model-scoped/catalog filtering rejected.** No `gpt-5.6-sol`/alias/lane/
  context-profile conditions: the backend rejects the field regardless of
  model name, and a catalog of names cannot anticipate the next model or
  client. Route kind — which upstream family the body reaches — is the
  right discriminator.
- **Global stripping rejected.** Official OpenAI-compatible Responses
  routes support the field; `openai_compat_executor.go` is deliberately
  untouched and its preservation is the negative guard test against
  over-broad sanitization.
- **Third-party Claude-compatible detection is endpoint-first.** Claude
  paths preserve only the default base URL (resolved to
  `https://api.anthropic.com`) or a resolved official HTTPS
  `api.anthropic.com` endpoint with default/443 port (hostname matched
  case-insensitively, any path). Every custom, non-HTTPS, non-default-port,
  or malformed base URL fails closed as third-party **regardless of token
  shape**: an OAuth-shaped token never upgrades a non-official endpoint.
- **xAI early cleanup removed, not duplicated.** The pre-existing early
  delete is deleted; the single boundary strip is the one invariant, and
  the xAI tests exercise it as the only strip.
- **Patched tests must run in the Nix sandbox.** The upstream checkPhase
  tests only `subPackages` (cmd/server), so the module override adds an
  explicit network-free `postCheck` gate running
  `go test ./internal/runtime/executor` (loopback fakes, vendored modules,
  `GOPROXY=off`); the build log echoes the gate marker. Patch-manifest
  consistency compares exact ordered lists, never sets — the retention
  hunks are context-pinned to the sequentially patched source, so patch
  order is part of the contract.
- **Honest claim boundary.** The exact ordinary Codex live leakage function
  remains **unproven** (its early path already appears to strip the field;
  transcripts are never read). The invariant is accepted because it closes
  the class: five deterministic leak cells were reproduced and closed, and
  any future reintroduction path is caught at the same boundary. The
  server-level Codex `/alpha/search` passthrough, plugin management
  routes, and Claude OAuth refresh carry no messages/responses payloads
  and sit outside the executor boundaries; they are unmodified. The
  Claude pipeline repairs malformed client JSON before the boundary, so
  fail-closed there is proven where the boundary can observe malformed
  input (helper unit tests and the Codex `cacheHelper` end-to-end test);
  the Claude end-to-end test pins the no-leak outcome either way.

No model/provider/composition/context/schema change: the only catalog
delta is the patch-manifest line; render/compiler/scope goldens stay
byte-identical. Launcher 2.19.0 → 2.20.0 (new gateway patch contract);
catalog 19 → 20. Activated 2026-08-21 at HM generation 130 after the exact
Nix-tested gateway binary passed local health/model/doctor checks; no
provider call was required. Generation 129 is the full rollback anchor.

**D61 — Do not promote GLM-5.3 on Alibaba Token Plan until its exact
allowlist includes it.** GLM-5.3 itself is a valid Z.ai release (exact native
ID `glm-5.3`, 1M context, always-on low/high/max reasoning, Anthropic
Messages/tools/streaming), but product availability is route-specific. The
Alibaba Team Token Plan Singapore endpoint uses an exact-string allowlist whose
newest GLM is `glm-5.2`; GLM-5.3 exists on Alibaba only as Beijing
pay-as-you-go `ZHIPU/GLM-5.3` over a separate OpenAI-compatible workspace
route/credential/billing product, and on Zhipu's own Coding Plan.

A catalog21 candidate followed the Qwen production pattern: stable internal
identity `glm52` and selector `claude-multi-glm52-max[1m]`, wire/display flip
to `glm-5.3`/GLM-5.3, no record/composition migration, exact two-line golden
delta, full offline tests/builds, and Sol-xhigh review APPROVE. One approved
bounded disposable-gateway call then reached the configured Token Plan route
with exact wire/max/stream/tool shape and no fallback; Alibaba returned HTTP
400 `InvalidParameter: Model not exist` before model execution. Activation was
therefore blocked exactly as designed. Candidate commit `caaa641` was reverted
by `8db80c3`; catalog20/GLM-5.2 remains active and source-authoritative, with
no live service/state/transcript change.

Rejected:

- guessing an alternate Token Plan model ID or moving alias;
- substituting `ZHIPU/GLM-5.3` on the Singapore Token Plan host;
- silently switching to a Beijing pay-as-you-go workspace or Zhipu plan;
- retaining an unusable staged catalog wire merely because the model exists in
  another product.

Future trigger: when Alibaba publishes exact `glm-5.3` support in the Team
Token Plan allowlist, follow the canonical
[reactivation runbook](issues/027-glm-53-token-plan-unavailable/REAPPLY.md):
reuse the reviewed in-place promotion, position GLM-5.3 on par with Qwen3.8
Max and GPT-5.6 Sol, repeat one approved canary, and only then activate. The
failed canary proves no GLM-5.3 execution properties on Alibaba; the 200K
GLM-5.2 evidence floor remains unchanged.

**D62 — Astra (`gpt-6-astra`) + Meta muse-spark batch (catalog21→22,
activated gen 134).** Gateway registry backport patch adds `gpt-6-astra` to
the pinned 7.2.80 Codex tiers (272K ctx / 128K maxout metadata, mirroring the
Opus-5 patch pattern); Astra was hidden in upstream discovery so the entry is
required for routing. Catalog adds model `astra` (Codex OAuth route, 1M
client/provider, declared 1.05M, validated floor 200K, lanes high+xhigh via
the existing reasoning-effort contracts) plus provider `meta` (Anthropic
Messages at `https://api.meta.ai`, bearer `META_CLAUDE_API_KEY`,
`output_config.effort` high/xhigh — Meta has no max) with `muse-spark` and
`muse-spark-contributor` (both 1M/1M/declared 1,048,576/validated 200K,
`large` profile; contributor carries the outputs-train-Meta privacy caveat
and 100 RPM note). One operator-approved bounded codex-route probe returned
the expected reply (~310 input tokens): the route accepts Astra, but the 1M
fence is unverified near-limit, so Astra joined `large` without lowering its
fence (stays qwen38's 983,616) and `validated_tokens` stays 200,000. Astra
became lead-capable in catalog22 (2.22.0). Activated at HM generation 134;
`doctor --repair-all` converged 37 sessions; three Astra compositions
(`astra`, `astra-muse`, `astra-qwen-muse`) were created via CompositionStore.
Meta muse-spark is activated but NOT live-probed (approval-gated, still
pending). Full handoff: `HANDOFF-ASTRA-BATCH.md`. WS4 (single-model mode for
any model + `--no-subagents` + local Qwen adapter) was deliberately left
unimplemented; spec preserved in the handoff. Rollback anchor: gen 134.

**D63 — 1M-class operating window capped at 800K (2.23.0, catalog stays
22).** Operator direction: the 1M default is too aggressive (Astra's route in
particular is only acceptance-verified, not near-limit verified), so every
1M-class model operates at an 800,000-token window by default. Implemented as
one central clamp (`operating_window()` in `composition.py`, D63 ceiling
constant) applied to final managed capacity, non-null scalars, and the
ordinary-profile minimum — not as twelve per-model catalog edits. Rejected
alternatives: (a) catalog-only `provider_tokens` edits — would force
rewriting Kimi's `user_reported_tokens` attestation to satisfy the
attested-bound invariant, stale a dozen "1M fence" qualifications/displays,
and churn rendered `context-length` for direct providers; (b) ~900K/922K from
the documented 1.05M−128K arithmetic — exact for the public API's stated max
input, but Codex-OAuth-route equivalence is unverified and the operator set
800K as the default. Smaller bounds (Grok 500K, GPT-5.5 258K, Qwen's
tighter-than-client cases) are preserved: the clamp is `min()`, idempotent,
and `auto_compact_trigger()` stays transparent arithmetic. Result: 800K
window → 702K reactive trigger via the unchanged formula
((800000−20000)×90//100); `[1m]` selectors, client/provider/declared/
validated evidence, and Kimi's attestation are untouched. Rendered gateway
YAML is byte-identical (OAuth aliases carry no context-length), so no
gateway restart is required for correctness. The generated lead prompt gains
one operating-ceiling line when capacity sits below the lead's configured
bound; the ordinary large-group label reads "800K operating window". Same
durability boundary as any policy change: launcher-mediated
fresh/resume/repair inherit the cap; already-running processes keep their
environment until relaunched. Source-complete with full suite green (2
pre-existing environmental failures, proven on clean HEAD), package +
sandbox builds green; activation is a separate operator gate.

**D64 — WS4: single-model mode for any model, `--no-subagents`, local Qwen
(2.24.0/catalog23, source-complete, not activated).** Three sub-parts:

1. **Keyless OpenAI-compatible adapter.** New renderer adapter
   `cliproxy-openai-compat-v1` (empty payload contracts) + `direct-openai`
   transport branch (http + port allowed ONLY here with auth none; https and
   secrets rejected). Emits a pinned-shape `openai-compatibility` section
   (name/base-url/models with name/alias/display-name/force-mapping; no
   api-key-entries) — verified field-by-field against the pinned 7.2.80
   lineage (v7.2.80..v7.2.81 has zero diff in config + compat executor),
   including the keyless fallback (executor omits Authorization without a
   key). Selectors/availability treat direct-openai as always available;
   TUI panes show keyless-LAN labels and server-up guidance instead of key
   entry (which would KeyError on the missing secret_ref).
2. **Local Qwen.** Provider `llm-local` + model `qwen-flash-next` via the
   dev pipeline (draft → check → review → promote): fence 320032 (= 300032
   input budget + Claude's 20K reserve; trigger 270028), declared 431104,
   validated floor 200K, new `flash431` profile. The LAN listing probe
   (read-only `/v1/models`) confirmed max_model_len 431104 and text-only
   but ALSO found production serves the GGUF-path id with no alias — so the
   `qwen3.8-flash-next` wire needs a server `--alias` (loadtest.sh
   convention) or a wire change; recorded in both qualifications as
   unrouteable-until-then. Acceptance + near-limit LAN probes remain
   approval-gated.
3. **Single-model mode + `--no-subagents`.** Any catalog model launches
   (`prepare_direct` accepts all; unknown ids still fail closed):
   profile-less models (today: agents-only gpt55) run fenced to their own
   selectors under their own bound with a null `context_profile` (own
   window/trigger/scalar math, fail-closed against profile substitution);
   profiled models are byte-identical to before. The G picker gains a
   `single` section; hook reconciliation resolves single selectors to
   (model, None) but never re-pins ACROSS single models (shared no fence);
   doctor/repair/launch rebuild null-profile fences from the model;
   in-session switches stay within their fence class (profile↔profile keeps
   the confirmed rebuild; either crossing with single-model relaunches).
   `--no-subagents` (tri-state, SUPPRESS default) hard-denies the Agent
   tool in scope settings plus a native-agent env belt; recorded on fresh,
   re-applied silently on resume, explicit mismatch rejected. Lead-only
   managed compositions validate and compile (preset support proven).
   Catalog evidence, `[1m]` selectors, and existing providers' rendered
   YAML are untouched.

Rejected: per-model catalog window edits for the ceiling problem (D63
central clamp instead); catalog-only single-model fields (a central
code path covers future models); scope-persisted compaction numbers
(launch-environment authority stands). Source-complete with full suite
green (2 pre-existing environmental failures), package + sandbox green,
same-family review APPROVE-WITH-NITS (3 findings fixed); activation,
push, and all live probes are separate operator gates. Presets
`muse-direct`/`muse-contributor-direct`/`qwen-local-direct` are created
via CompositionStore at activation, not committed.

**D66 — Output tokens are delegated to the client, and the 800K ceiling's
coverage is verified complete (no release; docs + one test).** Operator
asked whether max output tokens are correctly configured for DeepSeek
V4.1-Flash, whether the 800K boundary really applies to every model, and
whether DeepSeek's thinking modes should map higher.

*800K coverage — verified complete.* `OPERATING_WINDOW_CEILING = 800_000`
(`composition.py:65`) is applied by `operating_window()` at all six
producers, and every launch path routes through one of them: managed
compositions, ordinary/direct, single-model (null-profile), custom-registry
models (which always receive an ordinary profile, so they clamp too),
managed resume, ordinary resume, repair/converge, and the legacy
non-durable argv form. Env is materialized once (`launch.py:978-981`,
unset-then-overlay), and reserved-key enforcement (`catalog.py:45-60`)
means no catalog or `lead.env` value can re-inject a window key. Confirmed
per-model: grok46 500000, gpt55 258400, qwen-flash-next 320032, qwen38
983616→800000, large profile 800000. Three asymmetries are deliberate or
inert: `probe.py` (dev-only harness, unreachable from a launch), the raw
provider/client evidence recorded in snapshots beside the clamped window,
and managed(True)/ordinary(inherited) `autoCompactEnabled` — the last is
the D63-era decision to keep respecting the user's setting for ordinary
sessions, unchanged here. **Gap fixed:** 320032 was asserted nowhere
(only the picker label mentioned it); now pinned with a below-ceiling
clamp-invariance assertion in `test_compiler.py`.

*Output tokens — no change, and none is justified yet.* The launcher never
sets `CLAUDE_CODE_MAX_OUTPUT_TOKENS`: it is a reserved key and is **unset**
on both launch paths (`compiler.py:412`, `:920`). No catalog field, schema
field, scope setting, or rendered gateway setting exists for output — the
dimension is not modelled at all. What governs is the client: the pinned
2.1.220 binary resolves `max_tokens` as the env value if set, else the
model-registry default, else the unknown-model fallback `Mxg=32000` (upper
bound `Oxg=128000`) — verified independently by string extraction. Our
`claude-multi-*` aliases are not in that registry, so **the client sends
`max_tokens = 32,000` on every lane**, comfortably inside DeepSeek's 384K
(393216) ceiling and above its 8K/64K/128K documented upstream defaults.
Pinning per-model output would mean a new catalog/schema field plus
compiling a currently-reserved key — exactly what the closed-allowlist rule
forbids without a demonstrated failure, and it could only *raise* the
sent value toward costlier territory.

*Thinking/effort — current mapping is correct.* Anthropic-path effort is
`output_config.effort`; our lanes are `high` (default) and `max`
(explicit), matching DeepSeek's documented ladder where requested
`high`→high and `max`→max and thinking is on by default at effort `high`.
The operator's "map to a reasonable maximum" is already served by the
existing `-max[1m]` lanes; moving `default_lane` to max would raise cost
and latency for every side task with no measured benefit, so it is
recommended against (and is a legal one-field catalog edit if invoked).

*Evidence:* official DeepSeek docs (8 pages, re-fetched 2026-09-10) for the
ceiling/defaults/effort ladder; the pinned binary's constant table for the
client's 32000 default; and **two bounded operator-approved probes** on the
`max` lane (`max_tokens=256` and `32000`). Both returned HTTP 200
`end_turn` with a thinking block AND a text block — the theorised
"small max_tokens truncates thinking and yields empty content" failure mode
did **not** reproduce, so it stays unconfirmed rather than asserted.
Unknowns left open on purpose: the Anthropic-path default `max_tokens`
ladder, whether that path requires the field, and whether reasoning tokens
count inside `max_tokens` (undocumented by DeepSeek).

**D65 — DeepSeek V4.1-Flash: canonical wire, and the Pro wire's silent
reroute (catalog 24→25, launcher stays 2.24.0).** DeepSeek released
V4.1-Flash on 2026-09-10 (552B MoE, 8B/16B active, native vision, smaller
KV cache, lower prices). Official docs now name **`deepseek-flash`** as the
canonical callable id — no dated id is documented — and list the retired
`deepseek-v4-flash` / `deepseek-v4-flash-vision-exp` as temporary
compatibility redirects with **no stated sunset**. Separately,
`deepseek-v4-pro` is rerouted to V4.1-Flash at V4.1-Flash prices from
**2026-09-14 04:00 UTC until V4.1-Pro launches**.

Auto-binding: none needed. The deepseek route is a **registry-free
config-driven passthrough** (CLIProxyAPI has no deepseek channel, allowlist,
or per-model validation), aliases derive from `client_selector` rather than
the wire, and `force-mapping: true` rewrites the upstream model name back to
our alias — so every `claude-multi-deepseek-*` selector already runs
V4.1-Flash, and a wrong wire is invisible to Claude Code except by its
effects. Hence the fix is about **truthfulness, not routing**.

Changes: flash `wire_model` → `deepseek-flash` (canonical; the legacy string
now appears only as documented history), display → "DeepSeek V4.1 Flash",
routing_note/qualification corrected (retired V4-Flash-0731 claim dropped;
vision noted as upstream-documented but unverified through this gateway);
pro routing_note/qualification/role_hint now state the 2026-09-14 reroute so
a composition can no longer believe it is calling Pro after that date;
provider support_note records both redirects.

Rejected: (a) **no wire migration at all** — viable but bets our route on an
undocumented expiring redirect while the provider's own docs point
elsewhere, and the doctrine's remedy for moving aliases (pin a version) is
unavailable because no dated callable id exists; (b) **removing the pro
entry** — it would break four compositions and dev policy forces new entries
to New · Off; keeping it with honest labels preserves the slot for V4.1-Pro;
(c) **renaming the pro display to name V4.1-Flash** — the pro slot's wire id
stays `deepseek-v4-pro` while its upstream target flips on a fixed date, so
that display would need reverting in weeks; routing truth lives in
routing_note/qualification by design. (This is pro-specific: the flash
display does carry the version, because that wire names no version and the
generation is the operative fact; it too is re-verified at each release); (d) **raising `validated_tokens`** for either entry — a docs
claim is not a measurement, and the evidence field moves only after a
separately approved live call (flash stays at its existing 1M docs floor,
pro at 200K; flash's 1M is inconsistent with the conservative-floor
convention and is flagged as a separate pre-existing question, not silently
changed here).

Evidence tier: official docs only (the 2026-09-10 release announcement plus
pricing/updates/vision/anthropic_api/thinking_mode/create-chat-completion/
list-models, all fetched 2026-09-10); context 1M and the low/high/max
`output_config.effort` tiers are unchanged, so no lane or contract edits.
Blast radius: catalog + four test modules + the render golden (four lines:
two wires, two display names) — no src or launcher change. The new wire is
served only after a gateway re-render + restart (`claude-multi-proxy init`
then `systemctl --user restart cli-proxy-api`; no hot-reload on 7.2.80);
until then the served *name* is stale but routing is not — the retired id
redirects to the same V4.1-Flash model, so there is no functional gap.

## User decision summary (what you're approving by accepting this design)

1. Selected agents become **real files** in a per-session scope; the failure
   mode that erased them is closed by documented reload semantics, with a
   kill-resume + takeover acceptance proof (yours to run).
2. Guarantees are **tiered and displayed**: availability/dispatch/model
   frontmatter = durable; managed `/model` is fenced, ordinary `/model` is
   profile-scoped; delegation choices and active-work mixing remain honest
   residuals.
3. Workflows default **on** per composition; off mode is one settings key +
   an effort mapping.
4. Legacy sessions keep working and upgrade on resume; `--legacy` is a
   compatibility escape hatch (sessions work until a takeover — it is not a
   durability answer); rollback never deletes transcripts, and v2-era
   transcripts stay recoverable via native `claude --resume <uuid>`.
5. Net codebase **shrinks** (~4.5k lines removed: P0, dead branches,
   ceremony) while gaining the durable-scope core, transitions, and Doctor
   visibility.
6. Commit + Home Manager activation happen **once, at the end**, with your
   explicit approval after seeing the full evidence and rollback plan.
