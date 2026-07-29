# 004 — API Error "An error occurred while processing"

**Status: in release** · addressed in 2.8.0 (D42)

## Report (2026-07-29, operator)

Mid-session in occams-agent-flow (composition default, lead
claude-multi-opus-5[1m], subagents running), the client showed:

```
● API Error: An error occurred while processing
```

Recurs; operator associated it with the Sol agent. Wish: automatic
recovery/retry instead of manually typing "continue".

## Resolution (D42, evidence-verified)

**What it is:** the upstream's 500 body, displayed only after the pinned
client's own retries run out (default 10, exponential ≤32s backoff,
terminal abort when any wait exceeds 60s). It is not rate limiting
(zero 429/529 in 12h of journal), and the incident window's wire traffic
was all-Kimi — the Sol attribution was a UI line merge.

**What shipped:**
- Managed scopes pin `CLAUDE_CODE_RETRY_WATCHDOG=1` in the durable
  settings env (doctrine #8; the D33-proven channel): ~300 transient
  retries, no 60s abort — subagents and backgrounded turns keep
  recovering instead of dying for a typed "continue".
- Lead contract (cm-lead): a delegated agent that dies on infrastructure
  failure is first continued by message (it failed + why + this is a
  continuation — context survives); fresh dispatch only when the
  approach/context was the problem or it died twice. (Operator rule,
  encoded.)

**Rejected with evidence:** gateway `request-retry` at 7.2.80 — one
credential per provider, rotation has nothing to rotate to.

**Unfixable at this pin (documented):** mid-stream-after-content errors
have no retry path in client or gateway.

**Operator levers (documented, not automated):** Kimi routes carry no
provider content guardrails; routing security-heavy dispatches to Kimi
variants is a manual option — non-deterministic flagging makes
content-based auto-routing the wrong fix (and it would erode
cross-family review independence).

## Investigation findings (journal mining, verified-first-pass)

- **Zero 429/529** rate-limit responses in 12h of gateway journal.
- Non-2xx in 12h: 15 irrelevant HEAD `/api/hello` probe 404s, one
  Sol-routed **400**, one Kimi-routed **500** (previous evening — NOT in
  the incident window).
- **Incident-window wire traffic was all Kimi** (`claude-multi-kimi-k3`,
  8/8 selector records for the session) — not Sol. The UI line that
  looked like a Kimi agent on `gpt-multi-sol-xhigh` was a display merge
  of two agent lines.
- The journal contains **no** "An error occurred while processing" /
  api_error / rate_limit_error text; CLIProxy does not log upstream
  error bodies. **No non-2xx was logged for the session in the window.**
- Client binary (2.1.220) contains no such string (it renders upstream
  bodies); retry knobs verified at the pin (MAX_RETRIES clamp 15,
  watchdog 300, backoff/60s abort, subagent retry protection).
