# 015 — Dynamic composition/model selection

## Context

Operator goal (paraphrased with granted design latitude): a more flexible,
dynamic way of picking compositions and models — including /model switching —
while provably keeping everything correct. Sub-asks: list models the provider
APIs support; pick any of them easily; on-the-fly compositions (+ optional
save); MRU ordering; compositions preserved per session.

## What the investigation established (read-only recon, 4 agents)

1. **Three distinct model lists exist — conflating them is the classic bug.**
   - *Catalog models* (`catalog/models.json`): the only composition-ready
     records (selector, wire id, context, lanes, role gates — required by
     `schemas/models.schema.json`).
   - *Gateway-served models* (loopback `GET /v1/models` on 127.0.0.1:8317):
     CLIProxyAPI's in-process registry = what this gateway serves NOW. Local,
     not a provider call (gateway-ops skill). `--local-model` disables upstream
     catalog updates, so served == rendered config in steady state.
   - *Provider-advertised models*: OpenAI/Anthropic have documented list APIs
     but our routes are OAuth pools (unproven for listing); Qwen Token Plan
     and Kimi Coding list endpoints are unverified/absent. Raw discovery
     **cannot** populate compositions (no context/lane/role metadata).
2. **/model is display-filtered natively (issue 009, binary-verified)** — the
   picker shows Default/Fable/current; typed `/model <selector>` works for
   every allow-list entry. The fence (availableModels) is intact; same-profile
   ordinary switches work in-session; cross-profile = relaunch (D24); managed
   lead changes = transition only (R1 P1).
3. **MRU needs no new state** — records carry `composition_name`,
   `last_seen_at` (hook-refreshed), `cwd`; derivation verified live.
   `remembered_document` already makes the fresh card start per-cwd last-used.
4. **On-the-fly already works in the editor** (launch-once writes no file;
   records snapshot what launched; resume rebuilds from the snapshot). Missing:
   a non-editor ingestion path (`--composition-file`).
5. **Slot order is presentation-only**; preference is the explicit per-role
   `preferred` boolean (exactly one per role, catalog-validated).

## Design decisions

### D-a. MRU pick order (derived, zero new state)

`composition_pick_order(runtime)` (new, cli.py near `_session_records`):
1. compositions used in **this cwd**, recency desc (max `_record_last_seen`
   per name), name asc on ties;
2. compositions used only elsewhere, global recency desc, name asc;
3. never-launched compositions, alphabetical.
Intersected with `compositions.names()` (deleted compositions never
resurrect); ordinary records ignored; unreadable records skipped.

Applied to: **Tab/P cycling** (`_cycle_preset`), **transition + adopt
choosers** (`_choose_composition`, `_adopt`), **`compose list`** (order +
new `last-used` column; `name`/`origin` keep field positions 1–2).
The card's initial selection is unchanged (`remembered_document` already does
per-cwd last-used). QUICK_HELP notes "(most-recently-used first)".

### D-b. On-the-fly ingestion: `--composition-file PATH` (`-` = stdin)

Reuses `composition.load_composition_file` (strict JSON + schema + version);
stdin shares an extracted `validate_document`. Flows into
`build_quick_plan(action="fresh", source="Composition file …")` — launch-once
semantics with file provenance: the record snapshots the resolved composition,
resume works even if never saved ("missing" drift line is informational).
Mutually exclusive with `--composition`; refused with `-r/-c` (transition owns
composition change on recorded sessions); noninteractive launch accepts it as
the explicit composition. "+ save" = existing editor Update/Save-as.

**Rejected**: TUI "pick a model → generate composition" generator. The seed
default has provider qwen off — a naive lead swap BLOCKS; silently mutating
availability is exactly the non-obvious state change the editor makes explicit
today. The existing path is short: MRU-cycle → E → lead pick → ^O →
launch-once. Deferred with trigger: an "enable provider+model and set lead"
confirm inside `_open_lead` if availability-first proves to be the papercut.

### D-c. /model typed-selector discoverability (ordinary sessions)

The G picker's detail block gains `in-session: /model <client_selector>` (+ per
lane) for the selected row; `_print_ordinary_listing` prints selectors;
ORDINARY_HELP_SHARED notes it. `_detail_reserve` recomputes worst case (H6).
Nothing new for managed sessions (transitions own cross-model change; variant
selectors exist for agent resolution honesty, D39).

### D-d. "Models the API supports" → doctor served cross-check (loopback only)

`launch.served_models(...)`: loopback-guarded `GET /v1/models`, bearer token,
1.5s timeout, `data[].id` set. Wired into `_collect_doctor_reports` via a new
`Runtime.doctor_served_callback` seam (mirrors existing doctor callbacks).
Compared against the freshly rendered alias set (parity-tested accessor):
- rendered − served → **problem** naming the restart rule (stale daemon after
  re-render — the skill's #1 failure mode, currently invisible);
- served − rendered → **info**: "onboarding candidates; catalog edits own
  composition admission";
- connection failure → silence (readiness already reports it); non-200 → one
  info line. Advisory, never blocks.

**Rejected**: `claude-multi discover PROVIDER` live list calls — endpoints
unverified for 2 of 4 providers, OAuth pools unproven, output has no consumer
(discovery can't become compositions), and it needs the per-call approval
machinery. Documented so the question stays answered.

### D-e. Session composition history — skip the schema bump

No `prior_compositions` field. Nothing that ran is ever lost (every launch
snapshots the resolved composition into the record; resume rebuilds from it),
recency is MRU-derived, and a name list couldn't enable rollback anyway (prior
snapshots aren't retained). Speculative schema without a demonstrated failure
case. True rollback would belong to the transition engine retaining prior
snapshots — a different feature, deferred with trigger.

## Staging (one 2.13.0 batch)

| # | Change | Anchors |
|---|--------|---------|
| 1 | MRU pick order + cycle/choosers/`compose list` | cli.py ~2608 helpers; 1367; 3167; 3184; 5030; QUICK_HELP 1594 |
| 2 | `--composition-file` + `validate_document` extraction | cli.py 724/6129/6125/5959; composition.py 141 |
| 3 | Typed /model selectors in G picker + listing | cli.py 3586-3684; 3516; 3457 |
| 4 | Doctor served cross-check | launch.py ~338; cli.py 4914 + Runtime callback ~281 |

tui.py untouched (widgets reused; the ordinary picker lives in cli.py).

## Correctness proof plan

New tests per item (FakeWindow key scripts; Runtime callback injection;
fabricated records via `sessions.make_record`): MRU ordering matrix
(per-cwd > global > never-used; ties; deletions; ordinary ignored); cycling in
MRU order + wrap + R1 P1 no-cycle pin; chooser order + preselect; compose
list columns; `--composition-file` (valid/stdin/invalid/version/mutual
exclusion/resume refusal/noninteractive/print-launch/resume-after-unsaved);
picker selector detail + floor stability; doctor cross-check (missing→problem,
extra→info, non-200→info, down→silent) + renderer parity. Invariants: full
suite green; no scope.py change; every document passes schema +
validate_composition + resolve (fail-closed context capacity); no provider
network calls (loopback guard asserted).

## Risks

- `compose list` order/column = visible contract change (documented; fields
  1–2 stable).
- Doctor gains one loopback call (1.5s worst case; same class as readiness).
- File-loaded document whose name shadows a store entry: source string
  disambiguates; later resume shows the standard "changed" drift line.
