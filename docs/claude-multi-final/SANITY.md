# SANITY — design assessment of claude-multi (2026-07-24, v2.3.0)

An independent-design review of the whole product: managed compositions,
ordinary gateway mode, identity/lifecycle machinery, packaging, and the
standalone-Claude boundary. Each section poses the skeptic's question, gives
the evidence, and states a verdict. Findings that needed action were acted on;
the rest are named reservations, documented honestly.

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
The re-pin to 2.1.218 is queued as a bounded evidence task (HANDOFF open
items), not a design flaw.

## Q5. Is the gateway layering sound?

Loopback-only CLIProxyAPI (127.0.0.1:8317, systemd user service), token in a
mode-0600 user file, secrets rendered at runtime, never in the repo or argv.
Managed sessions get the token via env at exec; plain `claude` keeps normal
Anthropic auth (D19/D23 — no global env hijack). The 2026-07-24 activation
restart of the service was invisible to a running managed session beyond a
brief transport blip. **Verdict: sound.** Minor: two concurrent proxy
init/run invocations can split token/config (low, documented; single-user
systemd service in practice).

## Q6. Is the catalog + draft/review/promote pipeline proportionate?

Trusted JSON + closed schemas + an explicit promotion pipeline that can only
touch `models.json`/`providers.json`, with dummy secrets and exact-diff review.
It integrated Qwen without a code change — the intended payoff. Pretty
post-images (2.3.0) keep promote diffs reviewable; the raised review-load cap
removed the growth dead-end. **Verdict: proportionate** — the alternative
(hand-edits) is how catalogs rot.

## Q7. Complexity budget

Numbers: 17,195 source lines vs 19,294 test lines (>1.1 ratio, 1,166 tests).
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

1,166 host tests (unit, golden, PTY end-to-end incl. no-ctty fallback),
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
4. Two proxy low findings (token/config split under concurrent init/run;
   discarded availability report) — single-user service makes both
   theoretical.
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
