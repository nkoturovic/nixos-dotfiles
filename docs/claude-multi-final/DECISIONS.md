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
transient retries, no 60s abort, ~5min cap — a subagent or backgrounded
turn no longer dies waiting for a human to type "continue".
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
candidate (issue 005).

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
