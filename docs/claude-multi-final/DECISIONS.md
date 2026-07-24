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
