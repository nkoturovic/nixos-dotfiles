# Open items — 2026-07-27 · v2.6.0

Ordered. Each entry names its context and done-criteria. The standing rules
below are inherited from the previous checkpoint and still binding.

## Now

- **U1 takeover proof, routing level (acceptance L2).** D33 made gateway
  routing durable (apiKeyHelper + base URL in compiled settings). The
  remaining proof: observe one post-takeover managed session make a
  **successful model call** (roster intact was already observed; the
  2026-07-27 incident proved files ≠ routing). Record the observation in
  STATUS.md.
- **Re-pin to Claude 2.1.220 when ready:** `claude-multi update` (or U on
  the card) — now with live phase narration. Dogfoods D34.
- **Opus 5 near-limit acceptance:** unchanged — when an Opus-5-led session
  approaches the 1M capacity, confirm reactive compaction at the bound,
  then promote `qualification` in `catalog/models.json` (Kimi waits on the
  same shape).

## Soon

- **Same-context-family `/model` (candidate D36, parked).** Widening the
  managed `/model` fence to all catalog models is rejected (compaction
  triggers are per-model — a smaller-context model behind a 1M trigger is a
  session killer). The narrow version — allowing models that share the
  lead's exact context profile, with reconcile absorbing the switch into
  the record — is safe-in-principle but touches composition identity
  (record name vs actual lead). Only pick this up with a demonstrated
  need; `sessions transition` is the supported path until then.
- **Qwen preview → production:** unchanged (DECISIONS D21 pipeline).
- **Push `feature/term-only`:** 20+ local commits, nothing pushed — backup
  hygiene; needs the operator's explicit go.

## Watch (accepted reservations — act only if they start paying rent)

- `cli.py` module size; `probe.py` weight; wide-char TUI cell math;
  hook-delivery invisibility (hooks are metadata-only by design).
- Daemon pty-socket scan for the ● marker is a native-internals heuristic
  (D35) — if upstream moves the socket layout the marker silently degrades
  to absent; that is the designed fallback, not a bug.
- Machine health (operator-owned, not the project's): `/tmp` tmpfs pressure
  + full swap caused a host-suite EDQUOT once; run the suite with a
  disk-backed `TMPDIR` on this box (AGENTS.md §4 documents the var).

## Standing rules (inherited, binding)

1. No real-provider calls without explicit user approval, per call.
2. Never touch the live Claude daemon/supervisor; never read user
   transcripts; never delete anything under `~/.claude` or a
   transcript-bearing root. Rollback never deletes state.
3. Tests before claims: full discovery + package build + sandbox suite.
4. Coherent commits on `feature/term-only`; no push without approval;
   Home Manager activation needs user approval.
5. One batched cross-family review at meaningful boundaries (Sol reviews
   Kimi-authored work and vice versa).
6. Simplicity budget: no new mode/schema/daemon/state without a
   demonstrated failure case. Prefer deletion over addition.
