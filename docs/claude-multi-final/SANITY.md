# SANITY — design assessment of claude-multi (2026-07-24, v2.3.0; addendum through v2.6.3)

An independent-design review of the whole product: managed compositions,
ordinary gateway mode, identity/lifecycle machinery, packaging, and the
standalone-Claude boundary. Each section poses the skeptic's question, gives
the evidence, and states a verdict. Findings that needed action were acted on;
the rest are named reservations, documented honestly.


## Addendum (2026-07-27, v2.6.1 → v2.6.3)

Two events closed the release arc, both validating the review model this
assessment has been built on:

- **The update flow failed its own gate — and the gate was the point.** The
  first real candidate run (2.1.218→2.1.220) red-lit itself: promotion
  changed the contract, then the suite's deliberate version pins failed
  against it. The fail-closed restore worked perfectly (byte-identical,
  twice) — the machinery did its job *including* the failure being
  reportable and safe. The fix (D36: promotion syncs the pins in the same
  transaction) was then proven end-to-end on the real candidate. Lesson
  folded in: **flows that are only exercised through fakes are unverified
  by definition** — the candidate path had 100% mocked coverage and zero
  real runs. Real-run dogfooding is now part of the release evidence model.
- **Two adversarial review rounds paid for themselves.** An 8-reviewer
  fan-out found 25 real defects (fork credential revocation, token-path
  divergence, the override-deletion downgrade, the card's missing bottom
  reservation). The final gate then reviewed the *fixes* and caught four
  more — including a promotion-ordering regression (lexical vs numeric
  versions) introduced by one of the fixes, and a heal loop whose advice
  couldn't execute. The two-round model (fan-out → fix → gate the fix
  set) is now the documented release shape for high-risk changes; the
  verdict chain (Revise → resolution → approve criteria) worked exactly
  as designed.
- **Assessment holds otherwise:** epoch discipline kept forks from ever
  retargeting authority by itself; the simplicity budget survived (one
  small command + one small mechanism per proven failure; dead code
  deleted); the daemon boundary stayed read-only.

## Addendum (2026-07-27, v2.5.0 → v2.6.0)

A live incident three days after 2.5.0 stress-tested exactly the surfaces
this assessment called resilient — and found two genuine gaps, both closed
with the failure case on record (D32–D35):

- **Native forks were tracked but not *operable*.** The identity machinery
  correctly observed the fork and correctly blocked an ambiguous resume —
  then stranded the operator: the message named no fork and no remedy, and
  the picker filtered the fork out of existence (it hid any id equal to a
  record's runtime). Worse, a stale `pending_forks` marker could survive
  its own resolution (authority later landed ON the fork) and self-block
  the record forever. The tracking design was sound; the *operator
  surface* was missing. Now: self-clearing on authority, Attention +
  `--repair-all` convergence, a discard command, one actionable message
  builder everywhere, ⚠/● markers and an X action in the picker.
- **Daemon takeovers kept files but lost routing.** The durable floor
  passed its roster check on takeover, but nobody had verified *model
  calls* post-takeover: the daemon scrubs `ANTHROPIC_*` from its children,
  so the taken-over session was a zombie (`invalid model`). Lesson folded
  into the acceptance model: "survives takeover" now means routing too —
  compiled settings carry the non-secret base URL plus an `apiKeyHelper`
  shim (token still never in files). Verified against the live process
  tree (daemon env vs child env, count-only inspection).
- **Update UX failed the human test.** The evidence gate itself held
  (fail-closed, byte-identical restore after the interrupted run), but a
  2-minute silent suite behind a live curses screen reads as a hang.
  Long actions now narrate themselves; concurrent runs serialize.
- **Assessment holds otherwise:** the fork hook never retargeted authority
  (epoch discipline worked); rollback never touched transcripts; the
  daemon boundary (never configured, never touched) survived intact.

## Addendum (2026-07-24, v2.4.x → v2.5.0)

The assessments above held through the rest of the day, with three
confirmations and one new registry-level gap found and closed:

- **D26–D30 in production:** the hook shim survived two Home Manager
  activations with zero scope mismatches (the original incident class is
  gone); the layered contract made the 2.1.218 re-pin instant via the
  operator override; `claude-multi update` proved idempotent; the TUI
  conventions (Esc-only exits, uniform margin, wrapping keybars, health
  strip, update badge) shipped with PTY evidence.
- **Q4 follow-through:** the native contract is now layered exactly as the
  assessment recommended; the re-pin procedure is a product command, not a
  manual recipe.
- **New gap class — vendored registry lag:** CLIProxyAPI's embedded model
  registry predated the Opus 5 release, dropping the new aliases from
  `/v1/models` (routing was unaffected — the registry is listing-only plus
  max_tokens defaults). Closed with a third local registry patch; the
  pattern for future same-day model releases is recorded in AGENTS.md §5.
- **Composition curation:** Opus 5 leads the default (Sol preferred, Kimi
  alternates, opus5 native reviewer alternate); Fable 5 and the opus
  pairs (`opus-sol`, `opus-kimi`) coexist as named profiles; Opus 4.8 was
  deliberately kept in the catalog (existing sessions + Anthropic's own
  safety fallback) rather than given a profile. The 1M bound for Opus 5 is
  honestly labeled user-attested until near-limit acceptance.

## Verdict in one paragraph

The design is **sound and, after the 2.3.0 hardening pass, correctly
implemented**. The central bet — durable per-session files over carry-through
argv — was validated by the failure it was built to fix (agents vanishing on
supervisor takeover) and by the 2.1.218 supervisor now running in production.
The identity/lifecycle machinery is complex, but each mechanism answers a
demonstrated failure, and the failure modes compose (CAS, epochs, lock
ordering) instead of piling up. The main reservations are: dev-tooling
outweighs the production core (accepted, gated), `cli.py` concentrates the
whole product surface (accepted, tested per-screen), the exited-confirmation
gate for transitions is honor-based (documented; outcome stayed safe when
violated), and hook delivery is invisible when Claude never fires one
(undetectable from our side; documented, not faked).

## Q1. Is durable-scope the right architecture?

Alternatives considered (DECISIONS D1/D19): keep `--agents` argv (vanishes on
takeover — the original incident, observed live three times); global
`~/.claude` configuration (pollutes plain `claude` with managed agents,
denies, and gateway routing; makes concurrent compositions and atomic
transitions impossible); per-UUID config roots (relocates transcripts/auth);
plugin dir (field restrictions); argv re-supply wrapper (unenforceable
against supervisor-owned respawn). Per-session scope via documented
carry-through flags (`--add-dir`, `--settings`) is the minimal shape that
survives the documented respawn semantics. **Verdict: right call, proven in
production** (2.1.218 supervisor has taken over backgrounded managed sessions;
rosters persist on disk).

## Q2. Is the two-UUID identity model over-engineered?

Each mechanism answers a demonstrated failure: `managed_id` vs
`runtime_session_id` (native resume retargets after compact/clear — stable
keying must not follow it); bounded aliases (old IDs stay resolvable);
`launch_epoch` (delayed hooks from older launches must not retarget the newer
authority — observed class); `mutation_token` + CAS-by-own-write (concurrent
launchers and exec-failure rollbacks — the 2.3.0 data-loss bug lived exactly
here); pending-fork tracking (native forks must not overwrite the parent).
The migration chain (v1→v2→v3 in-memory, rewrite only on explicit mutation)
kept every historical session loadable through three schema generations.
**Verdict: complex but justified; no mechanism lacks a failure case.** The
simplicity budget holds: nothing here exists "for symmetry".

## Q3. Are SessionStart/SessionEnd hooks the right reconciliation channel?

Alternatives: transcript sniffing (prohibited — transcripts are never read),
daemon API (prohibited — the shared supervisor is never contacted), polling
(adds a daemon — rejected by the budget). Official metadata-only hooks are
synchronous (5s), stdin-fed, and were proven against the pinned binary for
startup/resume/clear/compact with loopback fixtures. **Verdict: right
channel.** Residual: if Claude never *invokes* a hook (one observed case: a
transcript written with zero events), the record looks healthy-at-rest and
nothing downstream can tell. Hook stderr is not capturable by the launcher.
Documented as a limitation; no fake fix added.

## Q4. Is the pinned-binary trust model right?

Every launch rehashes the full binary against the native contract (~1s); the
configured symlink is advisory-only (the 2.1.218 drift proved the value: the
tool kept launching the verified 2.1.217 while `claude` moved on). Accepted
boundary: path-based verification has a local-attacker TOCTOU window; an
fd/memfd redesign was deliberately rejected (G0'). **Verdict: proportionate.**
The 2.1.218 re-pin itself landed 2026-07-24 with the full offline evidence
suite, and D29 (2.4.0) makes the whole class routine: the doctor Attention
line detects drift, `claude-multi update` re-pins with evidence and takes
effect instantly via the layered (packaged + operator-override) contract.
The upstream auto-updater stays on for plain `claude`; the managed side
anchors instead of chasing.

## Q5. Is the gateway layering sound?

Loopback-only CLIProxyAPI (127.0.0.1:8317, systemd user service), token in a
mode-0600 user file, secrets rendered at runtime, never in the repo or argv.
Managed sessions get the token via env at exec; plain `claude` keeps normal
Anthropic auth (D19/D23 — no global env hijack). The 2026-07-24 activation
restart of the service was invisible to a running managed session beyond a
brief transport blip. **Verdict: sound.** (The earlier minor — two concurrent
proxy init/run invocations splitting token/config — is closed: token creation
and config write are serialized under one `state.FileLock` since v2.4.1.)

## Q6. Is the catalog + draft/review/promote pipeline proportionate?

Trusted JSON + closed schemas + an explicit promotion pipeline that can only
touch `models.json`/`providers.json`, with dummy secrets and exact-diff review.
It integrated Qwen without a code change — the intended payoff. Pretty
post-images (2.3.0) keep promote diffs reviewable; the raised review-load cap
removed the growth dead-end. **Verdict: proportionate** — the alternative
(hand-edits) is how catalogs rot.

## Q7. Complexity budget

Numbers: 17,195 source lines vs 19,294 test lines (>1.1 ratio, 1,167 tests).
`cli.py` is 4,458 lines — the entire product surface (commands, screens,
doctor); split-per-screen would add indirection without changing the
coupling; accepted, with per-screen test classes. `probe.py` (2,579) +
`dev.py` (980) outweigh several production modules — but they are dev-only,
never imported on the production path (verified: importing `claude_multi.cli`
loads neither), and they are the evidence machine behind the pinned-binary
claims. Goldens pin byte-exact compiler output and caught real regressions
during the 2.3.0 pass. **Verdict: within budget, with two named
concentrations to watch (cli.py growth; probe.py size if it stops paying
rent).**

## Q8. Is ordinary mode coherent with managed mode?

Ordinary sessions reuse the same records, scopes, hooks, locks, pointers,
doctor, repair, and adoption machinery with a different compile
(`compile_ordinary_scope`): no composition semantics, a context-safe model
fence, user settings otherwise respected. The shared machinery is exactly why
`--repair-all` converges both kinds uniformly. **Verdict: coherent, good
reuse, correct boundary (D23: first-class, never hijacks bare `claude`).**

## Q9. Error-handling philosophy

Fail-closed everywhere (collision gate, unknown keys, unresolvable intent),
atomic writes with `CommittedStateError` recovery, CAS-by-own-write
rollbacks, single lock ordering (index → lifecycle → pointer), staging+rename
for scopes. The 2.3.0 audit found the philosophy right but the application
incomplete in three spots (rollback running without mutation, four-way hook
command computation, doctor severity); all three were philosophy-consistent
fixes, not new machinery. Cross-family review then found two more of the same
kind (chmod window, catch width). **Verdict: the philosophy is right and
self-reinforcing; the bug classes it produced were application gaps, not
design errors, and are now regression-pinned.**

## Q10. Test strategy

1,167 host tests (unit, golden, PTY end-to-end incl. no-ctty fallback),
offline package build, Nix sandbox suite, loopback probe evidence for
binary-behavior claims (compaction hooks, delegation). The one recurring
flake class (PTY timeout under extreme load) is now self-diagnosing
(faulthandler) rather than padded. **Verdict: strong, honest about what it
doesn't test (real providers, live daemon, transcripts — by policy).**

## Q11. Case study: a transition ran against a live session (2026-07-24)

At 14:08:41 a `sessions transition` for the session that was actively running
this work was confirmed from another terminal while the process was alive.
The honor-based exited gate (D9, TRANSITIONS §3) allowed it. The relaunched
process died 23s later against the live transcript holder; the transition
engine's guards left record/scope **converged and authoritative** (doctor
Ready), the running process was untouched (settings are process-start-read),
and its now-stale-epoch hooks are correctly ignored (epoch protocol working
as designed). **Lesson:** the honor gate is documented and its failure was
survivable by construction; a transcript-liveness preflight could tighten it
but cannot be made reliable (no safe liveness signal), so the gate stays as
designed. State needed no repair.

## Q12. Standalone-Claude boundary

Plain `claude` remains fully upstream: no managed agents, no denies, no
gateway routing, normal auth (verified: the only claude-multi-managed surface
is per-session). The "standalone works well" requirement is met by *not*
configuring it, plus `claude-gateway` as the opt-in multi-model variant.
**Verdict: correct call (D19/D23).**

## Residual reservations (accepted, documented)

1. `cli.py` size and `probe.py` weight — watch; split only if they stop
   paying rent.
2. Wide-char cell math in the TUI (cosmetic; sanitizer handles escapes).
3. Hook-failure invisibility when Claude never fires one (Q3).
4. Discarded availability report (proxy low) — single-user service makes it
   theoretical. (The token/config split under concurrent init/run, the other
   half of the original finding, is closed by the render-path FileLock.)
5. HM generation expiry is now purely disk hygiene (Q13).

## Q13. Home Manager generation 95 expiry — resolved

With the 2.3.0 hook shim, package rebuilds no longer invalidate scopes, so
old generations are no longer load-bearing for hooks *compiled by 2.3.0+*.
The only remaining consumer of the gen-95 store path is the **running**
`a24fc875` session, whose process-start settings (in memory) still name it
for SessionEnd — an advisory hook. Options:

- **Clean:** after `a24fc875` exits, run
  `home-manager remove-generations 95` (then `nix-collect-garbage`).
- **Also safe, now:** expire it today. Worst case: that one session's
  SessionEnd fails once (advisory; the record still resumes fine — if its
  next resume ever complains, `claude-multi sessions relink-runtime` fixes
  it in one command). Its scope was already shim-repaired, so its next
  launch uses the shim regardless.

Either is correct; nothing else depends on gen-95.
