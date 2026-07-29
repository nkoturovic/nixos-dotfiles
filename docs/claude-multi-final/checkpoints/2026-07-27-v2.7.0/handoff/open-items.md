# Open items — 2026-07-29 · v2.8.3

Ordered. Each entry names its context and done-criteria. The standing rules
below are inherited from the previous checkpoint and still binding.

## Now (operator decisions, not code)

- **5ee2f942 (issue 006):** transcript verifiably gone from every project
  dir; the 2.8.0 resume gate names the state before exec. Choose: restore
  from a backup (then `claude-multi -r` works — the record is healthy) or
  `claude-multi sessions forget 5ee2f942-a367-4e96-a113-72eb4ecbd84c`.
- **Key rotation (hygiene):** a reviewer transcript once echoed
  `KIMI_CLAUDE_API_KEY`/`QWEN_CLAUDE_API_KEY` locally. Same exposure as
  `~/.config/secrets/claude.env` itself — rotate only if transcripts are
  ever synced/shared, then `claude-multi-proxy init` + restart the gateway.

## Soon (world-triggered acceptances)

- **U1 takeover proof, routing level (L2).** First post-2.6.x daemon
  takeover of a managed session making a **successful model call** via the
  apiKeyHelper path (roster intact was observed; calls not yet formally
  accepted). Record the observation in STATUS.md.
- **Opus 5 near-limit acceptance:** when an Opus-5-led session approaches
  the 1M capacity, confirm reactive compaction at the bound, then promote
  `qualification` in `catalog/models.json` (Kimi waits on the same shape).
- **Qwen preview → production:** unchanged (DECISIONS D21 pipeline) when
  `qwen3.8-max` ships.

## Watch (accepted reservations — act only if they start paying rent)

- **Ordinary unmarked-compact bleed (D44 residual):** ordinary sessions
  could still take a subagent model/cwd via an unmarked compact event; no
  discriminator exists at 2.1.220 and ordinary compact-model is
  load-bearing. Documented in issues/008. Act if it is ever observed.
- **Issue 005 (conditional isolation):** implementer variants cannot spawn
  from a non-repo session cwd (harness); mitigation is lead-contract
  guidance. Machinery (record-time isolation decision) only if the manual
  fallback starts hurting in practice.
- **Issue 007 (subagent context exhaustion):** recovery = resume-with-steer
  (D43 contract, live-proven). A harness-level fix is upstream's; the
  question whether subagents compact at all is open — the D44 fixture
  reproduction never produced one.
- **Lead-slot lane support:** ultracode lead keeps the sol-high wire
  contract (effort mapping). Candidate only if wire-level lead effort
  mapping is ever wanted.
- **Same-context-family `/model` (parked):** only with a demonstrated need;
  `sessions transition` is the supported path.
- **Two live-session prompt lags:** sessions resumed before 2.8.1–2.8.3
  carry older lead prompts until their next resume (self-healing; nothing
  to do).
- `cli.py` module size; `probe.py` weight; wide-char TUI cell math;
  hook-delivery invisibility (metadata-only by design); the daemon
  pty-socket ● marker is a native-internals heuristic (degrades to absent,
  never wrong); one PTY test flake seen once, never reproduced.

## Done this cycle (D40–D44, all activated)

- D40 outside-in worktree review contract (2.7.2, gen 109)
- D41 resume operability: actionable relink + bare-relink semantics +
  resume gate with TUI modals (2.8.0, gen 110)
- D42 recovery policy: watchdog retry pin + resume-over-redispatch (2.8.0)
- D43 subagent recovery: probe-bounded two-layer, 5-consecutive rule,
  SubagentStop radar (2.8.2, gen 112)
- D44 identity evidence admissibility: managed compact events + agent
  contexts no longer pollute the record (2.8.3, gen 113)

## Standing rules (inherited, still binding)

1. No real-provider calls without explicit operator approval, per call.
2. Never touch the live daemon/supervisor; never read transcripts; never
   delete anything under `~/.claude` or a transcript-bearing root.
3. Tests before claims: full discovery + package build + sandbox suite.
4. Coherent commits on `feature/term-only`; push and Home Manager
   activation need the operator's explicit approval.
5. One batched cross-family review at meaningful boundaries; no loops.
6. Simplicity budget: no new mode/schema/daemon/state without a
   demonstrated failure case.
