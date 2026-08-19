# 025 — Grok 4.6 replaces Grok 4.5 (catalog19 amendment)

## Context

xAI released Grok 4.6 on 2026-08-12 as the successor to 4.5, focused on
long-running agents, coding, knowledge work, and interactive/visual
projects; official evals place it on par with GPT-5.6 Sol. OpenRouter
provides exact slug `x-ai/grok-4.6` (500K; pricing starts $2/$6 per 1M,
but prompts >=200K bill $4/$12 for ALL tokens) and a moving alias
`~x-ai/grok-latest` that currently resolves 4.6.

The operator requires active replacement (do not retain 4.5), maximum
available reasoning, and completion of the pending catalog19 Pro batch.
Historical docs and catalog18 rollback fixtures retain 4.5 as audit/rollback
truth only—zero active 4.5 route after activation.

## Alias decision: exact pin, not the tilde router

Use exact `x-ai/grok-4.6`; document but do not wire
`~x-ai/grok-latest`.

- OpenRouter latest aliases can retarget at any time and expose only the
  newest eligible family model.
- That conflicts directly with D3: record=intent, catalog=trusted source,
  scope=pure function(record,catalog), no hidden state. A moving alias would
  silently turn a `grok46` record into 4.7 while catalog hash, 500K profile,
  qualification, and scope remain unchanged—a third hidden authority.
- The upstream response `model` echo cannot fix this at authority time:
  local `served_models` checks configured selectors, SessionStart reconciles
  recorded selectors, and the response echo arrives after scope compilation.
- Current wire schemas also reject leading `~`; broadening them would solve
  syntax while worsening authority semantics.

`~x-ai/grok-latest` is therefore correctly included as a considered/current
alias in D59/qualification, not as the trusted wire.

## Model replacement

Delete active `grok45`; add `grok46`:

- wire `x-ai/grok-4.6`, display `Grok 4.6`, selector base
  `claude-multi-grok46`;
- 500K client/provider/declared, profile `grok`, scalar null; validated
  200K until the fresh approval-gated route call; max completion unpublished;
- high fallback lane plus **xhigh default lane**; both explicitly override
  the documented OpenRouter Anthropic Messages field
  `output_config.effort=high|xhigh`. The existing high contract is reused;
  new `output-config-xhigh` is added (launcher 2.19.0) and OpenRouter declares
  both. xAI docs support low/medium/high/xhigh; OpenRouter lists xhigh, but
  live Anthropic-skin 4.6 acceptance remains call-gated;
- lead/all roles; routing for long-running agents/coding/knowledge work,
  on par with Qwen3.8 Max/Sol; role hints keep x-ai independent review.

This amends already-pending catalog19 (no catalog20). The explicit
`output-config-xhigh` renderer contract is a launcher behavior change:
launcher 2.18.0→2.19.0; catalog stays 19.

## Composition/state migration

- Replace `grok45`→`grok46` in all `024-planned` files; every Grok agent
  variant requests xhigh. DeepSeek-only planned files update their off key.
- Keep `024-rollback-catalog18` untouched with grok45 (it is restored with
  generation 125). Tests schema/structure-pin rollback instead of resolving
  it against catalog19.
- Live XDG catalog18 files/config continue to contain grok45 until the atomic
  activation boundary. Census (read-only, pre-activation): 36 records, zero
  session/scope grok45 references—recheck immediately before activation.
- If an ordinary grok45 record appears: explicit `--model grok46` is a
  same-profile re-pin (500K/grok). If a managed grok45 snapshot appears:
  transition to the updated composition; repair-time snapshot refresh is not
  shape-compatible.

## Verification

- Exact catalog shape: no active grok45, exact 4.6 wire, high+xhigh/default
  xhigh, D3/latest-alias qualification, OpenRouter xhigh contract.
- Compiler/context: grok selectors high+xhigh, 500K/432K, no [1m], wire and
  both selector resolver forms.
- Render golden: exact 4.6 route + xhigh override; zero active 4.5.
- Discovery: 4.6 cataloged, 4.5 not registered.
- Direct launch + synthetic stale-record same-profile migration.
- Exact planned files resolve and contain only 4.6; rollback files retain
  4.5 structurally.
- Full host/package/sandbox + combined Pro/Grok deep review.

## Activation/live boundary

Fold into blueprint 024's catalog19 activation. Before activation recheck
record/scope census. Install catalog19, immediately save all four planned
composition fixtures 0600, re-render/restart gateway, doctor/repair-all,
verify served selectors. Then separately approved bounded calls:

1. DeepSeek Pro max wire/auth/thinking/tool contract.
2. OpenRouter Grok 4.6 exact slug: tool blocks, stream ordering, high and
   xhigh mapping (and optionally confirm `~x-ai/grok-latest` response echo
   resolves 4.6—but never promote the moving alias into the trusted catalog).

Attempt 1 stopped at the DeepSeek Pro check and rolled back before either Grok
call, so no Grok 4.6 live evidence was produced or approval consumed.

Failure rollback is blueprint 024's complete catalog+XDG path: activate HM
gen125/catalog18, restore `024-rollback-catalog18` 0600, delete the new
`deepseek-flash.json`, re-render/restart, doctor/repair-all. This
intentionally restores Grok 4.5 as the prior generation. **Expected
rollback degradation:** if any catalog19 durable session was launched before
rollback, its record/transcript is retained but catalog18 may report removed
models (`deepseek-pro`/`grok46`) as unresolvable and repair-all may return
FAILED for those records. That is not transcript loss: resume/transition via
the retained catalog19 package or forget the obsolete record; do not rewrite
or delete state as part of rollback. The direct acceptance HTTP calls
normally create no session records.
