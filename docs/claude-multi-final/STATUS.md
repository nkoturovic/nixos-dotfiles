# STATUS — live tracker

## 2026-07-24 — v2.5.0: Opus 5 as the default lead (D31)

- **Opus 5 integrated** (Anthropic release 2026-07-24): new `opus5` model
  entry (wire `claude-opus-5`, selector `claude-multi-opus-5[1m]`, xhigh
  lane), anthropic fork-trusted routes extended (`claude-opus-5` before
  `claude-opus-4-8`, so the canonical Opus default is Opus 5), and the
  canonical fork-route set updated in code.
- **Default composition is now `opus5` + GPT 5.6 Sol** (Sol variants
  preferred, Kimi alternates, `opus5-xhigh` as the Claude-native reviewer
  alternate — the 4.8 slot is replaced). Opus 4.8 stays a full catalog
  entry for existing sessions and Anthropic's own safety fallback.
- **Fable 5 stays a named profile** (`fable` user composition = the
  previous default verbatim, one Tab away), no longer the default.
- **Honesty labeling kept:** the Opus 5 1M bound is family-attested
  (announcement states no bound) → qualification user-attested with a
  conservative validated floor; the lead appendix shows the qualification
  line, never "provider-safe".
- Evidence: **1,194 host tests OK** (all composition/editor/transition/
  render/scope/catalog expectations moved from fable to opus5; goldens
  re-blessed and reviewed; render golden regenerated with the new route
  set). New-default print-launch verified (`--model claude-multi-opus-5[1m]`,
  effort ultracode, 1M window, scalar 372000).
- **Named Opus 5 profiles** (user compositions): `opus-sol` (Opus 5 lead +
  GPT 5.6 Sol subagents, mirroring kimi-sol's pair shape) and `opus-kimi`
  (Opus 5 lead + Kimi K3 for every subagent role); the **default stays the
  mixed flagship** (opus5 lead, Sol preferred, Kimi alternates, opus5
  native reviewer alternate). Preset family: default, opus-sol, opus-kimi,
  fable, kimi-sol, qwen-sol, sol-direct — all verified resolving.
- catalog_version 3 → 4; launcher 2.4.1 → 2.5.0. Gateway re-render +
  serving of `claude-opus-5` verified post-activation.
- **Live verification: PASSED.** One consent-gated call through the
  gateway (`POST /v1/messages`, model `claude-multi-opus-5`, one real
  provider request, user-approved): alias accepted and routed, exact
  instruction followed ("OPUS5-OK"), clean `end_turn` completion with
  native usage fields. Full chain catalog → render → gateway → Anthropic
  (Opus 5) proven.
- **CLIProxy registry gap found and fixed:** the vendored model registry
  predated the release, hiding the new aliases from `/v1/models` (routing
  itself was unaffected). Third local patch
  (`cli-proxy-api-opus-5-model.patch`) registers claude-opus-5; the rebuilt
  gateway now serves both `claude-opus-5` and `claude-multi-opus-5`
  (verified live).

## 2026-07-24 — v2.4.1 ACTIVATED (gen 99): TUI health/update surface + CLIProxy review + final hardening

- **Activated:** HM generation 99; `claude-multi`/`claude-gateway` report
  2.4.1; gateway restarted cleanly (healthz 200); hook shim targets the
  activated package; doctor Ready with zero Attention lines.
- **TUI health/update surface (the notification answer):** the quick-confirm
  card now shows a **health strip** (gateway status from one loopback check
  per open, plus pin state incl. operator-override marker) and, when a newer
  Claude is installed, an **update badge** (`Claude X available · pinned Y ·
  press U to update`). **U** runs the whole evidence-gated update in place
  (suspend → inspect/promote/suite/override → resume with the catalog
  reloaded); **H** runs doctor in place with an optional repair-all prompt.
  Line mode shows the same update line + `u` command. KeyBar wraps upward
  instead of clipping keys.
- **CLIProxy integration verified end-to-end:** the live gateway config is
  byte-identical to a fresh render from the trusted catalog; every catalog
  selector's base alias is served (`/v1/models`); `[1m]` is confirmed
  client-side-only (stripped before the wire — proven by live 1M sessions).
- **Final review fixes:** proxy `run`/`login` now print unavailable-provider
  warnings (no silent gateway start with a missing secret); the gateway
  token+config write is serialized (no token/config split under concurrent
  init); `claude-multi-dev` main no longer escapes TypeError/KeyError/
  UnicodeDecodeError as tracebacks.
- **Workflow smoke (offline, all green):** fresh/resume/continue per
  composition (default, kimi-sol, qwen-sol, sol-direct), ordinary fresh,
  managed-vs-ordinary correct refusal, transition print-only from inside,
  session-event hook round-trip, doctor Ready, update idempotent.
- Evidence: **1,194 host tests OK** (1 skip); card health/update widget
  tests; keybar wrap; proxy warnings.

## 2026-07-24 — v2.4.0 ACTIVATED: fully clean baseline (gen 98)

- **Activated:** HM generation 98; profile package
  `/nix/store/01m7jfsk17cgil2iklk01jbwjna05i5i-claude-multi-2.4.0`;
  entrypoints report 2.4.0; gateway restarted cleanly (healthz 200).
- **Doctor is fully clean:** Ready with **zero** Attention lines —
  `Managed Claude 2.1.218 verified`, symlink resolves to the inspected
  artifact, **15 sessions · 15 durable · 0 legacy**, no override present.
- **Final review fixes landed pre-activation:** `update` removes redundant
  overrides, `update --activate` targets the correct flake root, New · Off
  covers the provider id; **1,189 host tests green**, package + sandbox
  green.
- **TUI conventions live:** Esc-only exits and the uniform column-2 margin
  verified via PTY on the card, sessions screen, and editor.
- **Second checkpoint created:** `checkpoints/2026-07-24-v2.4.1/` (entry
  point, state snapshot, open items); the v2.3.0 checkpoint is superseded.
- The update loop is now routine: doctor Attention on drift →
  `claude-multi update` (inspect → promote → full suite → override,
  instant effect; `--activate` for baseline refresh).

## 2026-07-24 — v2.4.0: re-pin to 2.1.218 + layered contract + `claude-multi update` (working tree)

- **Managed binary re-pinned to 2.1.218** (contract + version pins + docs):
  offline inspection (`--version`/`--help`/SHA-256), then the full offline
  suite against the candidate — **1,171 tests green**, with the real-binary
  probes confirming auto+manual compaction hooks and delegation on 2.1.218;
  takeover stays fail-closed by design (L2). catalog_version 2 → 3.
- **Layered native contract (D29):** the packaged contract stays the
  reviewed baseline; a strictly-newer **operator override**
  (`~/.config/claude-multi/native-contract.json`, 0600, schema-validated,
  secret-scanned) now wins while newer and is ignored+reported when stale.
  The packaged bundle hash never reflects the override; doctor prints the
  contract source. Effect: a re-pin no longer requires a rebuild or a
  service restart.
- **`claude-multi update` (new):** one command — detect candidate, offline
  inspect, promote into the source checkout, run the full offline suite as
  the evidence gate, write the override (instant effect), optionally
  `--activate` (home-manager switch). Fails closed: evidence failure
  restores the repo byte-identically and writes no override.
- **Doctor re-pin alert:** an Attention line now fires when the configured
  symlink is newer than the pinned contract, naming `claude-multi update` —
  the permanent drift early-warning that closes the update loop.
- **Hygiene:** the last legacy record (`9bc5fd42`) was forgotten (its
  transcript is untouched and stays natively resumable); doctor is fully
  clean (Ready, no Attention lines).
- Evidence: **1,171 host tests green** (+4: repin alert unit tests, doctor
  attention, upgrade flow, catalog override loader), package + sandbox
  builds green. Activation pending user approval (2.3.0 profile still runs
  the 2.1.217 contract; the override layer takes effect from 2.4.0).

## 2026-07-24 — Checkpoints, wikis, and the documentation map (self-discovery)

- **Checkpoint system created** (`docs/claude-multi-final/checkpoints/`):
  convention README + first checkpoint `2026-07-24-v2.3.0/handoff/`
  (entry point, state snapshot, open items). A fresh agent now starts at the
  latest checkpoint and needs zero verbal briefing.
- **Every landing spot wired:** repo root README (projects section), product
  `AGENTS.md`/`USAGE.md`/`README.md`, design-package README ("Documentation
  map" — one canonical home per topic + update triggers), `~/.claude/AGENTS.md`
  (placeholders filled), `~/.agents/wiki/projects/claude.md` (refreshed from
  stale 2.1.0/gen-91 to 2.3.0/gen-97), `~/.claude/.agents/wiki/` (index/
  overview/log as routing layers). Product `.agents/` created as a scratch
  workspace (verified NOT nix-packaged).
- **Replicability codified:** the maintenance rules are standing rules now —
  STATUS at milestones, checkpoint at meaningful boundaries, pointers never
  copies, one canonical home per topic (design README "Documentation map" +
  AGENTS.md rule 7).
- **Coherence pass:** HANDOFF.md retitled to daily-ops (dated qwen-sol
  framing and completed repair steps replaced with current state + pointers);
  DECISIONS gains D26 (hook shim), D27 (doctor severity + repair-all), D28
  (managed compaction pin); milestone ledger updated; every Markdown link in
  the doc set machine-verified resolvable.
- Evidence: host suite 1,167 green before this docs-only round; sandbox
  `/nix/store/kqyr9f8ixvgyk6xx8g48rbni61gmcrxd-claude-multi-tests` green
  with the product `.agents/` present.

## 2026-07-24 — Documentation package + design sanity assessment

- **AGENTS.md** (product root): full agent-facing development guide —
  architecture, the 10 doctrine invariants, module contract map, workflow
  (test/bless/build/sandbox), how-to-change recipes, rules of engagement,
  debugging tools, known limitations.
- **USAGE.md** (product root): the simple human guide — the three
  entrypoints, daily flows (launch/resume/picker/transition/adopt/forget),
  doctor tiers, six supported use cases, FAQ/troubleshooting.
- **SANITY.md** (design package): the full design assessment — Q1–Q13 with
  evidence. Verdict: sound design, correctly implemented after 2.3.0;
  reservations named (cli.py/probe.py concentration, honor-based exited gate,
  hook-delivery invisibility, wide-char TUI math). Includes the 2026-07-24
  live-transition case study (honor gate violated; state converged correctly
  by construction).
- **HM generation 95 resolved:** the generation link was removed
  (`home-manager remove-generations 95`); gen-96 (2.2.0 rollback) and gen-97
  (2.3.0) remain. The 7qisrz store path stays on disk until the user's next
  routine GC, so the running a24fc875 session's start-time hooks keep working
  until it exits; even past GC the cost is one advisory SessionEnd miss.
  Nothing else depended on gen-95.
- Also in this pass: prune rule extended to orphaned pre-2.2 digest-only lead
  prompts (a9b2842), and my own session was observed mid-flight being
  transitioned from another terminal — the record/scope converged correctly
  with zero repair (documented as the SANITY case study).

## 2026-07-24 — v2.3.0 lifecycle hardening: stable hook shim, repair-all, session UX (working tree)

Independent audit (12 parallel lanes over the uncommitted v2.2 tree plus the
live state root) found the v2.2 lifecycle work sound in design but carrying
one systemic defect and a set of smaller correctness bugs. All fixed with
regression coverage; **1,160 host tests green**.

- **Hook command root cause (critical).** The lifecycle hook command was
  computed four different ways: launch used the wrapper path
  (`$out/bin/claude-multi` via `CLAUDE_MULTI_HOOK_COMMAND`), while
  transition/execute, `converge`, and the L4 resume-failure cleanup recomputed
  the inner script path (`$out/share/claude-multi/bin/claude-multi`), and the
  durable compiler silently fell back to a PATH-relative `claude-multi` when
  none was given. Two scopes of the same generation diverged by spelling, and
  every Home Manager rebuild changed the expected scope bytes for ALL durable
  sessions (live: 20 record↔scope mismatches, 14 of them pure path drift; 3
  sessions' hooks one garbage-collection away from dangling; the inner-script
  spelling additionally runs unpinned `env python3` and dies without the
  wrapper's PYTHONPATH). **Fix:** a single authority
  (`scope.resolve_hook_command`) plus a stable indirection —
  `<state_root>/bin/claude-multi-hook`, an executable shim refreshed by every
  launcher invocation, preferring the resolved launcher and falling back to
  PATH. Compiled scopes now embed the constant shim path, so scope bytes are
  rebuild-stable; the durable compiler fails closed when no hook command is
  supplied. A behavioral test pins scope-byte stability across simulated
  rebuilds.
- **Launch rollback data-loss bug (high).** A resume failing a pre-mutation
  guard (pending fork, stale authority, repair-needed) ran the BaseException
  rollback anyway: it deleted the session's per-CWD pointer and rewrote the
  valid live scope (with the wrong hook spelling). The rollback is now
  mutation-aware (`committed_token is None` ⇒ nothing to roll back);
  regression test proves record/scope/pointer are byte-preserved.
- **Noninteractive resume/continue (medium).** The `--composition` guard ran
  before the resume/continue branches, so scripted `claude-multi -r UUID` and
  `-c` always failed even though the record determines the composition. The
  guard now applies only to fresh launches; both paths are pinned by tests.
- **Doctor severity + bulk repair.** Doctor no longer aborts when an ordinary
  record's profile leaves the catalog (degrades to a named problem line), and
  by-design lazy state no longer BLOCKs: legacy context snapshots and
  legacy-mode records are an **Attention** tier (exit 0) with the exact fix
  command, while real damage (unreadable records, identity repair, missing or
  mismatched scopes) stays BLOCKED. New `doctor --repair-all`: converges every
  durable record under its lifecycle lock (managed and ordinary), refreshes
  managed record snapshots against the installed catalog (context fields
  filled, drift absorbed — `sessions.refresh_record_snapshot` fails closed on
  any composition change), skips legacy records with a note, and continues
  past per-record failures.
- **Managed compaction pin.** Compiled managed settings now include
  `autoCompactEnabled: true`: a user-level `autoCompactEnabled: false`
  silently defeated the documented capacity/trigger model and would wedge 1M
  sessions at the hard context wall. Ordinary gateway scopes leave the user
  setting alone. The generated lead appendix now also states the exported
  process scalar (`CLAUDE_CODE_MAX_CONTEXT_TOKENS`) explicitly instead of
  omitting it from the context policy.
- **TUI/output correctness.** Report subcommands (`doctor`, `models`,
  `show`, `compose list/show`, `sessions list/show/forget/link/relink-runtime`)
  now honor stdout redirection instead of always writing to `/dev/tty`;
  interactive-first flows (editor, transition, direct, bare launch) keep the
  terminal. `TextInput` no longer reports the cursor on the closing bracket
  at end-of-input. An Alt-chord heuristic was implemented, then **reverted
  with cause** (ncurses splits Alt+chords by design; re-merging misclassifies
  fast human input and broke the key-script model — documented in
  `read_key`). A cross-profile ordinary relaunch now prints the
  live-process caution that managed transitions gate on.
- **Launch robustness one-liners:** success-path `update_last` is now
  blocking (a won launch can never silently lose `-c` registration); the
  execve-failure fresh cleanup's `clear_last` matches the pre-exec path's
  blocking form; a `cwd_lease.restore()` failure can no longer mask the exec
  error or skip state convergence.
- **Dev pipeline.** Catalog post-images are written in the repo's
  human-editable pretty format (promote no longer buries semantic changes in
  a single-line reformat), and the promote review-record reload raises its
  string cap so large catalog diffs cannot dead-end the pipeline. A claimed
  `check_draft` scratch-dir ownership bug was refuted by its pinned tests
  (the caller-provided parent is always a scratch tree by contract).
- **PTY flake instrumentation.** The rare
  `test_no_controlling_terminal_still_reaches_quick_confirm` timeout (two
  recorded occurrences, output byte-identical to a clean exit) now arms a
  20s `faulthandler` stack dump so the next occurrence is self-diagnosing.
- **Evidence:** 1,166 host tests green (was 1,135; +31 regression tests), the
  offline package build and Nix sandbox suite are green, and
  `git diff --check` is clean. Cross-family review (cm-reviewer-sol-xhigh,
  read-only, at d65218d) returned REVISE with zero must-fix and two
  should-fix items (shim exec-bit repair, repair-all failure isolation);
  both were fixed with regression tests in ae228a1 along with the actionable
  nits. No real provider, transcript, or live supervisor was touched.
- **Activated + hygiene done (2026-07-24):** Home Manager generation 97 is
  current; all profile entrypoints report 2.3.0 and the gateway restarted
  cleanly. Post-activation `doctor` is **Ready** (one Attention line for the
  last legacy record, 9bc5fd42, which keeps its transcript). Full hygiene
  executed with user approval: 15 records verified transcriptless (fresh
  check) were forgotten, `doctor --prune` swept their lead prompts, and the
  prune rule was extended to orphaned pre-2.2 digest-only lead prompts
  (a9b2842; the two stragglers were swept from the repo checkout and the rule
  lands in the next natural activation). Sessions screen now shows 16 real,
  resumable sessions. Only remaining expiry: Home Manager generation 95 after
  the running a24fc875 session exits (its start-time hooks reference the
  gen-95 store path; SessionEnd is advisory, so an early expiry costs at most
  one relink-runtime).

## 2026-07-23 — v2.2 lifecycle identity + ordinary gateway integration (working tree)

- **Identity fixed at the data-model boundary:** schema-v3 records separate
  stable `managed_id` from authoritative Claude `runtime_session_id`, retain
  bounded runtime aliases, distinguish managed-composition vs
  ordinary-gateway sessions, and migrate v1/v2 records in memory without
  rewriting on read.
- **Official lifecycle reconciliation:** every durable scope compiles
  synchronous metadata-only `SessionStart` and advisory `SessionEnd` hooks.
  Startup/resume/clear/compact updates the runtime UUID atomically; monotonic
  launch epochs reject delayed hooks from older launches, and forks never
  overwrite the parent identity. `transcript_path` is ignored and never stored.
  Hook commands always read stdin rather than `/dev/tty`.
- **Resume/CWD correctness:** native `--resume` now targets the runtime UUID,
  while scopes/pointers/locks remain keyed by the stable ID. The original CWD
  is opened and entered successfully before any state commit; missing or
  inaccessible CWD fails closed. Transition and exec-failure CAS semantics are
  preserved.
- **Picker/adoption correctness:** native UUIDs are deduplicated before the row
  limit; managed runtime IDs/aliases are excluded from unmanaged discovery;
  slash-to-dash ambiguity no longer chooses a nondeterministic shallow path.
  Adoption mints a stable ID and records the native UUID separately.
- **Ordinary multi-model mode:** `claude-gateway` / `claude-multi direct`
  launches normal gateway-backed Claude Code with no generated `cm-*` agents
  or composition policy. Native `/model` is available within a context-safe
  profile; cross-profile changes are explicit resume/relaunch operations.
  Upstream bare `claude` remains untouched by default.
- **Managed model fence:** `availableModels` contains only the compiled lead;
  composition transitions are the only supported lead change. A lifecycle
  observation of a different model marks the record repair-needed.
- **Context/compaction corrected:** explicit catalog fields separate client
  classification, provider bound, process scalar, and ordinary profile. The
  compiler treats `CLAUDE_CODE_AUTO_COMPACT_WINDOW` as capacity and sets
  `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=90`. Pinned Claude Code 2.1.217 reserves up
  to 20K output tokens before applying that percentage: deterministic reactive
  thresholds are Sol 316800, managed 1M process 882000, and Qwen/ordinary-large
  867254. Proactive summary preparation is runtime-controlled and may occur
  earlier. Mixed processes retain Sol/GPT protection through their client caps;
  an extended selector with a tighter provider bound narrows the shared capacity,
  so Qwen lowers a 1M process to 983616. Qwen's `[1m]` selector strips to exact
  `qwen3.8-max-preview`; Kimi remains the intended, user-attested 1M route,
  explicitly labeled unverified rather than provider-safe until near-limit live
  acceptance.
  Metadata-only pinned probes now prove both manual and automatic compaction
  emit `SessionStart(source=compact)` using only the loopback fake provider.
- **Safety/polish:** the probe live-domain tripwire is strictly observe-only;
  the fake provider supports `/v1/models`; expected connection resets are
  quiet; PTY pipe handles close deterministically.
- **Concurrency and cleanup:** mutation tokens preserve rollback ownership
  across lifecycle-only hooks; relink/adoption/forget are serialized and
  failure-aware; managed and ordinary `--continue` pointers are independent.
- **Evidence:** **1,135 host tests green** with one intentionally gated
  real-provider/native-contract skip (81.305s). The pinned loopback-only probes
  observed both automatic and manual compact lifecycle hooks; the offline
  package build, Nix sandbox suite, and `git diff --check` are green. No real
  provider, transcript, or live supervisor was touched. Final standalone package:
  `/nix/store/v1jvhlp93whsdysi6gn6cfaqicz0ckj7-claude-multi-2.2.0`; sandbox
  evidence: `/nix/store/vrdmfsb8dk697zlhx1w1cs98cknnxa9z-claude-multi-tests`.
  The first final sandbox attempt hit one no-controlling-terminal PTY timeout;
  the immediate clean retry passed.
- **Activated for acceptance:** Home Manager generation 95 is current at
  `/nix/store/ks2l3v96ax8fz8smks95imczcali50l5-home-manager-generation`, with
  claude-multi package
  `/nix/store/7qisrz1b5i7kc92f91sgzlzsnja5nvcp-claude-multi-2.2.0`.
  Post-activation verification found and fixed one packaging-only defect: the
  new `claude-gateway` source file had been absent from the dirty flake input.
  The sandbox now executes all three packaged `--version` entrypoints, and
  activated `claude-multi`, `claude-gateway`, and `claude-multi-proxy` all report
  2.2.0. The loopback gateway is running. `doctor` correctly blocks on 18
  early-schema-v3 durable records whose snapshots predate explicit context
  fields; each upgrades on resume/transition. No commit or push has been
  performed.

## 2026-07-22 — M0 design package

**Done**
- Full orientation: handoff, AGENTS/wiki rules, SIMPLICITY/VELOCITY, frozen
  C\* docs, live runtime (HM gen 76, package `sjkxlr3…`, Claude 2.1.217,
  daemon log), 10-doc corpus (classified: 6 official, 1 Anthropic guidance,
  3 community), whole-product source map (16-agent parallel sweep).
- Root cause pinned to documented semantics: `--agents` never saved to disk;
  supervisor 2.1.216→2.1.217 takeover rebuilt the registry from persisted
  state; carry-through argv set documented (agent-view L340–349) and
  binary-consistent.
- Architecture decided: per-session durable scope via `--add-dir` + compiled
  settings; 5 options evaluated; simplicity pass applied (≥4,500 lines slated
  for removal: P0 sandbox, dead branches, schema ceremony).
- Package written: README, BLUEPRINT, SPEC, TRANSITIONS, UX, PLAN,
  VERIFICATION, MIGRATION-ROLLBACK, DECISIONS, STATUS.

**Open unknowns**: U1 takeover carry of `--add-dir` (M1 stop-gate, then
acceptance L2), U2 appendix survival, U3 add-dir watcher, U4 add-dir
precedence, U5 availableModels fence, U6 nested default, U7 fork
refusal/carry, U8 settings reload timing, U9 deny merge precedence.

**R0 checkpoint (cm-reviewer-sol-xhigh)**: REVISE with 12 must-fix findings —
all resolved in one revision on 2026-07-22:
1. U1 language corrected (backgrounding documented; takeover binary-consistent)
   and promoted to M1 stop-gate; `--legacy` relabeled compatibility hatch.
2. State model fixed: record + catalog as two explicit authorities.
3. Scope staging moved to sibling dirs (`scopes/.<uuid>.new|.prev`); all crash
   windows enumerated.
4. exec-failure cleanup made action-aware (fresh/resume/upgrade/transition).
5. Transitions redefined: never mutate a live session's scope; exited
   confirmation; from-inside = print-only; v1 relaunch-only (hot path dropped).
6. Workflow family classification: lead-family by default; routed stages
   unknown/mixed; never an independent verdict.
7. Appendix-loss floor: all load-bearing rules duplicated into durable
   descriptions/settings; degradation not disappearance.
8. Collision gate extended to project tree + user `--add-dir`s + managed
   agents dir.
9. F1–F4 gated on per-root daemon-domain verification; otherwise move to
   acceptance; F7 takeover probe defined as the U1 stop-gate.
10. `worktree.baseRef:"head"` claim corrected (commits, not uncommitted work).
11. Linked sessions follow the uniform legacy-upgrade rule.
12. Rollback truth: old launcher can't read v2 records; native resume +
    retained store path documented.
Also: deny merge precedence downgraded to U9; wrong-composition rollback row
corrected. Verdict after revision: architecture stands (Option 1), proceed to
M1. No second loop per REVIEW-STRATEGY (no architecture change).

**Next**: daily use. U1 takeover proof expected naturally (Claude 2.1.218
appeared — the doctor's advisory shows the symlink moved).

## 2026-07-23 — compaction race + adopt/resume + cwd filter (`b07d2d4`)

- **Compaction root cause revised by later binary evidence:** the original
  scalar-clamped trigger interpretation was incomplete. The window variable is
  capacity; pinned 2.1.217 caps it per model, reserves up to 20K output tokens,
  and applies the percentage to the remaining prompt budget. Proactive
  preparation uses a runtime-controlled fraction and is deliberately not
  reported as an exact invariant. Synchronized loopback probes prove the
  automatic compact lifecycle hook without claiming its preparation point.
- **Adopt/resume fixed**: adopted records now carry the decoded original
  project cwd (prefix-matching slug decoder); resume enters it. Records
  adopted before this fix need forget + re-adopt.
- **Sessions screen**: C toggles a cwd filter (default: all).

## 2026-07-23 — FINAL audit + hardening (`bdaae16`, activated)

Final independent audit (Sol xhigh ×2 shards, read-only): 12 findings, all
fixed same-pass. Lifecycle: stale-relaunch generation guard in
`perform_launch`, cleanup fully CAS-by-own-write (pointer restore only while
owned; fresh branch compares committed bytes), scopes-parent symlink
validation in destructive paths, mandatory ownership token in
`restore_exec_failure`. Product: sessions screen non-empty-section landing +
windowed tables + adopt pointer update, case-insensitive command keys,
single-sourced `version.json` for cli + proxy `--version` (staleness
confirmed fixed live), README/HANDOFF/STATUS wording made strictly honest
(smoke-test, preview lifecycle, durability proof tiers). Also: editor `?`
before text input, keybars read "help", uniform padding, transition
title/rule collision. **1015 tests green; sandbox suite builds.** The
product is final for handoff: every audit layer (R0 design, finisher ×2,
R1 + delta, qwen review, final ×2) resolved with zero open findings.

## 2026-07-23 — Qwen live verification: PASSED

Single consent-gated call through the gateway (`POST /v1/messages`,
model `claude-multi-qwen38-max`, one real provider request, user-approved):
wire model + alias accepted, bearer auth works against the Token Plan
endpoint, native thinking block returned (always-on reasoning, no filter),
clean completion with usage fields. Full chain catalog → render → gateway →
Qwen proven. Note: `claude-multi-dev smoke-test` has no provider transport
wired (M3 removed the dead branch) — the direct gateway call is the
verification path; a bounded transport may be added later if wanted.

## 2026-07-23 — Qwen Cloud provider integration (`c0cb1b3`, activated)

Qwen Cloud (Token Plan) integrated via the product's own pipeline
(draft → review → promote): provider `qwen` (family alibaba,
Anthropic-compatible `apps/anthropic` on the token-plan host, **bearer**
auth — new auth kind; header emission conditional), model `qwen38`
(Qwen3.8 Max Preview, wire `qwen3.8-max-preview`, context **983616** per
official docs), `reasoning_effort` pinned **xhigh** (provider maximum)
via the new `reasoning-effort-xhigh` payload contract — not Kimi's
output-config-max; no filter-thinking (native thinking always on).
Selector is preview-free so the selector survives the production swap; the revision steps are the D21 sequence (wire_model, context, effort tiers, one live call, display).
Model id `qwen38` avoids `-max-max` variant IDs. **qwen-sol** user
composition: qwen lead ultracode + sol preferred variants + qwen-max
alternates (mirrors kimi-sol structure). Evidence: 1006 tests green
host + sandbox, package builds, installed catalog verified, gateway
config renders the qwen section. Also fixed a post-R1 sandbox gap the
candidate build caught (transition PTY preflight injection). Pending:
first real verification call (user approval boundary).

## 2026-07-22 — acceptance fix: name-based resume (`2dbbd1c` + follow-ups, activated)

User acceptance hit a dead end: native Claude's exit hint prints
`claude --resume "cm:<composition>"` (display name), while `-r` required a
UUIDv4. Fixed: `-r` accepts UUIDs, composition names, and `cm:`-prefixed
forms — unique match resumes, several list candidates newest-first with
UUIDs (verified live), none errors with a `claude-multi sessions list`
pointer. Follow-up fix: the error writer's sanitizer collapsed launcher
newlines into literal `^J`; added `tui.visible_message` (per-line
neutralizing, newline-preserving) and interpolation-time sanitizing of
candidate fields. 986 tests green; generation active
(`/nix/store/4pdik20i…` then follow-up switches). User has 5+ durable(g1)
sessions from first launches.

## 2026-07-22 — M5 boundary: COMMITTED + ACTIVATED

- Commits: `7df42e2` (source, 56 files, +17,211/−1,809), `7253e85` (docs,
  36 files). Branch `feature/term-only`, tree clean.
- Flake eval: exit 0. **HM generation 77** active:
  `/nix/store/375i78cr7dqdh2gssphlhqk3xlixhxji-home-manager-generation`
  (rollback: generation 76).
- Package: `/nix/store/9qg71y5b111v0kdhjifqvmkicaybkjb0-claude-multi-2.1.0`.
  One environment fix required: a stale **direct nix-profile entry**
  (claude-multi 2.0.0, installed out-of-band earlier) shadowed HM's 2.1.0 —
  removed via `nix profile remove claude-multi`; resolution now 2.1.0.
- Post-activation `claude-multi doctor`: **Ready** — binary 2.1.217 verified,
  symlink matches, gateway valid, 6 recorded sessions (all legacy, incl. the
  rethink session itself), no collisions, U1 evidence line honest.
- Gateway restarted by activation without issue.

## 2026-07-22 — R1 remediation + delta confirmation: ALL-RESOLVED

All 9 R1 findings fixed in two lanes (L1-L4 lifecycle, P1-P5 product) plus
lead production wiring (`trusted=` and `expected_record_bytes=` into the
live call sites). Cross-family delta auditor (Sol xhigh, fresh bounded
pass): **all-resolved 9/9** with file:line evidence, suite re-run by the
auditor. **978 tests green host + sandbox.** Design refinements recorded in
DECISIONS D20.

## 2026-07-22 — R1 independent audit (Sol xhigh, read-only ×2)

**Verdict: findings-material** (9 findings, 0 architectural). Recorded in
DECISIONS D20. Lifecycle: stale-authority races across the lock boundary
(prepare→execute, converge, resume, link), unguarded exec-failure cleanup,
transition committing scope/record before preflight, durable-resume failure
deleting a valid scope. Product: resume bypassing the transition engine,
catalog drift stranding sessions, terminal-escape injection in rendered
external text, `--legacy`+workflows:off silent inversion, README rollback
note. Positives: no token leaks, no lock-fd leaks/deadlocks, durable
settings shape exactly per SPEC, versioning consistent, packaging sane.

## 2026-07-22 — M3.5 TUI rework (user-requested): DONE

**Evidence**: full discovery **928 tests OK, 1 intentional skip** (from 830).
Package builds as `claude-multi-2.1.0` (version now tracked from
version.json). Sandbox suite re-run in flight.

**Delivered**: `tui.py` (2,087 lines) — stdlib-curses widget layer (Label,
Badge, KeyBar, Checkbox(+groups), SelectList single/multi, TextInput with
real cursor editing, Modal with focused buttons + Esc-cancel, Table) +
light/dark/mono palette detection (COLORFGBG → OSC 11 → dark default;
NO_COLOR + `--no-color`; every colorized element has a text form, pinned by
an across-palettes equality test). All screens rebuilt on widgets with flow
semantics unchanged: quick-confirm composition card, ONE form-based editor
(TextInputs/SelectLists/checkboxes/Modals; Ctrl+G `$EDITOR` as the only
external-editor integration), sessions Table with Modal-confirmed actions,
transition diff + EXITED-wording Modal, doctor badge styling (line contract
byte-identical for scripts). `TERM=dumb` → printed plan + exact `$EDITOR`
command (no second interactive editor). **editor.py deleted (−1,130 lines
dual-mode duplication)**; line-mode contracts ('Status Ready', footers,
doctor lines) byte-preserved. 112 new/updated tests (41 widget, 52 form,
19 PTY incl. Ctrl+G `$EDITOR` end-to-end and /dev/tty routing).
UX.md §2 and DECISIONS D14 updated to match.

## 2026-07-22 — Finisher pass (cross-family, Sol xhigh ×2): APPROVE

Two bounded sharded lanes (first attempt context-overflowed on the full diff;
sharding fixed it). 8 findings, all fixed with regression coverage:
- Lifecycle: rollback order (record before scope), UUID/path validation
  everywhere, fresh/resume preconditions, exact pointer compare-and-restore,
  scope drift detection (shape/symlinks/owner/modes), durable deletes.
- Compile: collision-gate recursion + YAML-comment name parsing;
  `CLAUDE_CODE_DISABLE_WORKFLOWS` reserved against lead env.
- One architecture finding (reported, not fixed): same-UUID lifecycle
  operations unserialized.
**Lead integration after the pass**: per-UUID **lifecycle lock**
(`sessions.lifecycle_lock`) across launch scope/record/pointer mutation and
transition staging/swap/save/repair — released before execve so no lock fd
leaks into Claude; PTY flake fixed (exit deadline 6s→30s under suite load);
**830/830 tests green**. package.nix now derives its version from
version.json (2.1.0); product README rewritten for the durable architecture.

## 2026-07-22 — M3 deletions + catalog slimming: DONE

**Evidence**: full discovery **809 tests OK, 1 intentional skip** (count down
from 885 by design: P0's 63 tests and ceremony tests removed with their
machinery). Probe import verified lazy (`claude_multi.dev` import no longer
loads `claude_multi.probe`).

**Removed (net −3,100 lines)**: P0 namespace sandbox (p0.py 776, p0inner.py
793, test_p0.py 994 — reference archive intact in handoff artifacts);
same-launch lead mode + `--agent cm-lead` machinery; triple-flag fork
compiler path (fork now fails with the native-fork guidance);
~130-line hardened daemon-metadata parser → minimal existence/pid check;
probe fixture daemon scaffolding; ADAPTER_IDS + dead smoke branch;
native-contract 8 capability objects → one map + U-numbered acceptance map;
roles.json contract-string duplication (prompts canonical).
**Updated**: prompts restored to bounded delegation (byte-identical to the
live session's proven wording) + reviewer finisher clause (D7); goldens
re-blessed (only prompt-embedding goldens changed); fork requests fail with
the verbatim native-fork guidance.

## 2026-07-22 — M2 transitions + TUI: DONE

**Evidence**: full discovery **885 tests OK, 1 intentional skip** (from 792 at M1).

**Delivered (Lane D)**: `transition.py` — v1 relaunch-only transitions per
TRANSITIONS.md: semantic diff, exited-confirmation (and from-inside print-only
via new `CLAUDE_MULTI_SESSION_ID` env), sibling-generation swap with fsync,
every crash window converging to record authority, exec-failure restore of
exact prior bytes, `converge()` for doctor --repair. 46 tests.
**Delivered (Lane C)**: quick-confirm badges (durability/workflow/policy/
project-agents), editor workflows row (curses+line), sessions screen
(mode column, per-row actions, forget-with-scope-removal), `sessions
transition` wiring, Doctor (scope integrity, --repair, --prune, collisions,
evidence line).
**Lead integration**: durable-by-default in `Runtime.prepare` (fresh/resume;
legacy records upgrade; fork stays legacy), `--legacy` escape hatch with
lineage preservation, badge forms, `SessionStore` UUID guards, 9 integrator
tests.
**Notable**: mid-M2 the shared supervisor restarted again (14:50Z) and this
session's argv `--agents` vanished live — the third observed instance of the
exact failure this design eliminates. Cross-family review now runs via
workflow `model` override (gpt-multi-sol-xhigh) since cm-* types are gone.

## 2026-07-22 — M1 durable-scope core: DONE

**Evidence**
- Full discovery: **792 tests OK, 1 intentional skip** (pre-existing native-contract real-probe gate). Lane-focused: 186/186.
- **F1 PROVEN against the real 2.1.217 binary** (fake provider, live-domain tripwire armed): scripted delegation to a marker agent in an `--add-dir` scope **accepted**; the subagent's own request carried the marker frontmatter model — on-disk discovery through `--add-dir` works on the pinned binary. 5 requests, `/tmp/cc-daemon-1000` byte-identical pre/post.
- **Daemon-domain precondition VERIFIED** for headless sessions: no live-daemon contact; `-p` sessions create no daemon domain.
- **U1 (takeover) FAILS CLOSED in automation as designed**: headless sessions run no resident supervisor → takeover carry-through moves to user acceptance **L2**. M2+ proceeds on the documented-backgrounding basis per VERIFICATION §2.
- Integrator fix: probe.py retained-versions dir now derives from the contract's `resolved_path` (hygiene gate green).

**Delivered (Lane A)**: `scope.py` (pure scope compiler + collision gate), composition `workflows` field (+ultracode→xhigh derivation), record v2 (v1 loads unchanged), durable-mode compiler (no `--agents`/`--disallowedTools`), launch action-aware cleanup, `bless.py`, durable goldens, version 2.1.0.
**Delivered (Lane B)**: probe daemon-domain gate + live-domain tripwire, scripted-delegation harness, F7 takeover skeleton (fail-closed), 60 new probe tests.

## Milestone ledger

| M | Scope | State | Evidence |
| --- | --- | --- | --- |
| M0 | design package | done (R0 resolved) | this folder |
| M1 | durable-scope core | done | 792 tests OK; F1 proven on 2.1.217; U1→L2 |
| M2 | transitions + TUI | **done** | 885 tests OK; transition matrix; badges/Doctor |
| M3 | deletions + catalog | **done** | 809 tests OK; −3,100 net lines; lazy probe |
| M3.5 | TUI rework (user-requested) | **done** | 928 tests OK; editor.py −1,130 lines |
| M4 | integrated verification | **done** | 978 host + 978 sandbox green; package 2.1.0 builds |
| M5 | boundary: commit/activation/acceptance | **committed+activated** | gen 77; doctor Ready; acceptance pending user |
| + | Qwen Cloud integration | **done** | 1006 tests OK; pipeline promote; live call PASSED |
| + | v2.2 lifecycle identity + ordinary gateway | **done** | schema-v3 two-UUID identity; hooks; gateway mode |
| + | v2.3.0 hardening + activation | **done** | 1,167 tests OK; gen 97; doctor Ready; review resolved |
| + | docs + checkpoints + hygiene | **done** | AGENTS/USAGE/SANITY; first checkpoint; 16 sessions |
| + | v2.4.x update loop + TUI health surface | **done** | 1,194 tests OK; layered contract; update cmd; gen 99 |
| + | v2.5.0 Opus 5 default + gateway patch | **done** | gen 101; live call PASSED; 7 profiles verified |
| + | v2.6.0 fork UX + durable gateway routing | **done** | 1,230 tests OK; incident-driven (D32–D35); review resolved |
| + | v2.6.1 evidence-gate fix + 2.1.220 re-pin | **done** | 1,237 tests OK; D36; real update green end-to-end; pin 2.1.220 |
| + | v2.6.2 adversarial hardening (D37) | **done** | 8-reviewer fan-out (35 findings) + fix set + final cross-family gate; 1,319 tests OK |
| + | v2.6.3 final gate resolution | **done** | gate Revise→approve: SF1/SF2/N1/N2 resolved; 1,323 tests OK; gen 106 |
| + | v2.7.0 sessions stop (lifecycle action) | **done** | upstream `claude stop` via verified binary; CLI + TUI E; transition live warning |
| + | v2.7.1 subagent routing fix (D39) | **done** | availableModels = lead + roster; wire-model probe guard + negative control; incident triple-confirmed |
