# HANDOFF — Astra batch (for the Astra model taking over this work)

**Audience:** the Astra model resuming this session. Read this fully before
acting. It captures everything done, everything planned, the architecture, the
standing rules, and the open questions — including the parts you are expected
to reconsider in the round.

**Repo:** `/home/kotur/personal/nixos-dotfiles` · branch `feature/term-only`
(nothing pushed; do not push without approval).
**Product source:** `home-manager/claude-multi/`.
**Design docs:** `docs/claude-multi-final/` (STATUS ledger, DECISIONS,
HANDOFF, blueprints/, issues/, checkpoints/).
**Current live:** launcher **2.22.0 / catalog 22**, Home Manager **gen 134**,
Claude pin 2.1.220. Doctor **Ready**, 37 durable sessions.

---

## 1. TL;DR — what is true right now

- **Astra (`gpt-6-astra`) is integrated, lead-capable, and activated.** It is
  in the catalog as model key `astra`, provider `openai` (Codex OAuth route),
  capabilities `lead+agents`, `large` ordinary profile, lanes high+xhigh via
  `reasoning-effort-high/xhigh` contracts. Route acceptance was verified with a
  bounded probe (returned `ASTRA-OK`, end_turn).
- **Three Astra-based compositions exist** (mode 0600 in
  `~/.config/claude-multi/compositions/`), ready to switch to:
  - `astra` — Astra single-model lead, native subagents available.
  - `astra-muse` — Astra lead + Muse Spark 1.3 agents (openai lead reviewed by
    meta family).
  - `astra-qwen-muse` — Astra lead + Qwen3.8 Max (alibaba) workhorse + Muse
    Spark (meta) cross-family reviewer.
- **Meta Model API provider (`meta`) + `muse-spark` / `muse-spark-contributor`
  models are in the catalog** (Anthropic Messages adapter at
  `https://api.meta.ai`, bearer `META_CLAUDE_API_KEY`, `output_config.effort`
  high/xhigh — Meta has **no `max` effort**). Route **not yet live-probed**.
- **The Sol `prompt_cache_retention` 400 bug is fixed and activated** (2.20.0,
  D60): a route-scoped final-boundary sanitizer strips the unsupported field
  for Codex + third-party Claude-compatible routes.
- **GLM-5.3 was investigated and is NOT available on Alibaba Token Plan.** It
  was reverted; GLM-5.2 is the active GLM. A reactivation runbook exists for
  if/when Alibaba enables it.
- **WS4 (single-model mode for ANY model + `--no-subagents` + local
  Qwen3.8-Flash-Next adapter) is PLANNED but NOT implemented.** This is your
  main implementation task. Full spec in §6.

**Stop point:** Astra is usable now. The operator will switch their session to
an Astra-based composition. Your job is to continue with the pending work below
— chiefly WS4 — and to reconsider the setup holistically (§9).

---

## 2. What was DONE this run (chronological)

### A. Sol `prompt_cache_retention` 400 → fixed (2.20.0/catalog20, D60, activated)
- Symptom: Sol-led sessions intermittently failed with
  `API Error: 400 prompt_cache_retention is not supported on this model`.
- Root cause: `prompt_cache_retention` is an OpenAI Responses-platform cache-TTL
  field that the ChatGPT/Codex subscription backend and third-party
  Claude-compatible routes reject. CLIProxyAPI 7.2.80 only stripped it in some
  paths, so it leaked through others.
- Fix (commit `4a3b07b`): a **route-scoped final-boundary sanitizer** in the
  gateway — strips the field for Codex + third-party Claude-compatible routes,
  preserves it for official Anthropic and OpenAI-compatible-platform routes.
  No model-name conditions; route-scoped only.
- Activated (gen 130). No live probe was needed for this fix.

### B. GLM-5.3 Token Plan investigation → reverted (D61/issue 027)
- The operator asked to promote GLM-5.2→GLM-5.3. Investigation found GLM-5.3 is
  **not on the Alibaba Token Plan allowlist** (Team Edition allowlist stops at
  `glm-5.2`; Personal at `glm-5.2`). GLM-5.3 exists elsewhere (Zhipu Coding
  Plan, Bailian pay-as-you-go) but not on Token Plan.
- A candidate catalog21 promotion was made (commit `caaa641`) then **reverted**
  (commit `8db80c3`). GLM-5.2 stays active.
- A **reactivation runbook** was written for if/when Alibaba enables GLM-5.3:
  `docs/claude-multi-final/issues/027-glm-53-token-plan-unavailable/` (README +
  REAPPLY.md), plus commits `10a710d`, `13989d8`, `879a78f`.

### C. Astra integration (WS1 + WS2/WS3 + lead flip) — **activated**
- **WS1** (commit `7056b02`): backported `gpt-6-astra` into the CLIProxyAPI
  gateway registry (new patch `home-manager/cli-proxy-api-astra-registry.patch`,
  wired into `claude-multi.nix` + `catalog/gateway.json` manifest + packaging
  tests). Astra was hidden from discovery upstream, so the registry entry is
  required for the gateway to route it.
- **WS2/WS3** (commit `8c47f19`, "catalog21 staging"): added
  - `astra` catalog model (then agents-only, 1M-class candidate), and
  - `meta` provider + `muse-spark` + `muse-spark-contributor` models.
- **Version bump** (commit `0f6f32a`): 2.21.0/catalog21.
- **Astra probe** (operator-approved, real call): bounded call through the codex
  route returned `ASTRA-OK`, end_turn, 310 input tokens. **Route accepts Astra.**
- **Astra lead-capable flip** (commit `84cdd83`, 2.22.0/catalog22): `astra`
  became `lead+agents`, joined `large` ordinary profile (its 1M provider_tokens
  does not lower the large fence, which stays qwen38's 983616). Updated the
  large-profile selector tuple + picker navigation test pins.
- **Activated** via `home-manager switch` (gen 134), then
  `doctor --repair-all` converged 37 sessions; doctor Ready.
- **Created 3 Astra compositions** (validated via CompositionStore, mode 0600):
  `astra`, `astra-muse`, `astra-qwen-muse` (details in §1).

### D. What was NOT done (deliberately)
- **WS4 was started then reverted** to keep a clean green baseline for this
  handoff. The full spec is preserved in §6 and in the plan file
  `/home/kotur/.claude/plans/iridescent-sauteeing-lampson.md`. You implement it.
- **Meta muse-spark live probe** not run (needs operator approval per call).
- **Astra near-limit context validation** not run (only ~310 tokens exercised;
  the 1M fence is unverified near-limit).
- **Presets** `muse-direct`, `muse-contributor-direct`, `qwen-local-direct`,
  `deepseek-flash`-style locals, etc. not all created (only the 3 Astra ones).

---

## 3. Standing rules (MUST follow)

- **No real provider calls without explicit per-call operator approval.** Live
  probes are operator-gated. Never call a model without approval.
- **Never touch the live Claude daemon/supervisor; never read user transcripts;
  never delete anything under `~/.claude` or a transcript-bearing root.**
- **Rollback never deletes state.** Transcripts are metadata-only; forget
  removes only record+scope, never transcript.
- **Secrets by name/count/length only — never print values.**
- **Tests before claims:** full discovery + package build + sandbox before any
  claim of green.
- **Commits on `feature/term-only`, coherent; no push without approval.**
- **Activation needs the operator green light** (`home-manager switch`).
- **Cross-family review at meaningful boundaries; no review loops.** A change
  authored by one provider family must not get its sole verdict from the same
  family.
- **Simplicity budget:** no new mode/schema/daemon/state without a demonstrated
  failure. Prefer deletion over addition.

---

## 4. Architecture overview (so you can reconsider it)

`claude-multi` is a stdlib-only Python launcher that:
1. Compiles a **composition** (lead model + generated `cm-*` agent roster) into
   a per-session **durable scope** (`~/.local/state/claude-multi/scopes/<id>/`
   with `.claude/agents/*.md` + `settings.json`), then `execve`s Claude Code
   with `--add-dir` + `--settings`.
2. Routes every model call through a **local CLIProxyAPI gateway**
   (`127.0.0.1:8317`, loopback-only), which proxies to the real providers
   (Anthropic, OpenAI/Codex OAuth, Kimi, Qwen, GLM, DeepSeek, OpenRouter, Meta).

**State model (D3):** record = intent, catalog = trusted source,
scope = pure function of (record, catalog). No hidden mutable authority.
Reconciliation is record-authoritative (doctor/repair/converge).

**Key files:**
- `catalog/models.json` — trusted model catalog (14 models incl. `astra`,
  `muse-spark`, `muse-spark-contributor`). Per model: provider, wire_model,
  capabilities, context (client/provider/declared/validated tokens,
  ordinary_profile), lanes (effort contracts), lead effort, routing_note,
  role_hints, independence family.
- `catalog/providers.json` — provider routes (transport kind/URL/auth, payload
  contracts, independence_family).
- `src/claude_multi/render.py` — renders the gateway YAML (model aliases,
  payload override/filter contracts) from the catalog.
  `ADAPTER_PAYLOAD_CONTRACTS` maps effort contracts to upstream params.
- `src/claude_multi/compiler.py` — compiles compositions + ordinary-direct
  launches; `direct_context_profile`, `direct_profile_selectors`,
  `direct_profile_context` (profile fence math).
- `src/claude_multi/composition.py` — `resolve()` (composition→resolved),
  `auto_compact_trigger()` (reactive trigger math).
- `src/claude_multi/scope.py` — writes the durable scope.
- `src/claude_multi/sessions.py` — session records (v3 schema).
- `src/claude_multi/cli.py` — CLI/TUI; the `Runtime`, ordinary picker,
  sessions screen, doctor.
- `src/claude_multi/proxy.py` — gateway control + provider listing
  (`_LISTING_SUPPORT` descriptors).
- `src/claude_multi/upgrade.py` — evidence-gated Claude Code update.
- Schemas: `schemas/{models,providers,composition,session,custom}.schema.json`.
- Tests: `tests/test_*.py`; goldens in `tests/goldens/`; re-bless via
  `PYTHONPATH=src:tests python3 tests/bless.py`.

**Context/profile model:** each ordinary model belongs to an `ordinary_profile`
(`large`, `grok`, custom). A profile's fence = min provider_tokens over its
members. Reactive trigger = `floor((window-20000)*0.90)`. 1M-class models use
`[1m]` selectors.

**Effort contracts:** `reasoning-effort-high/xhigh` (Codex), `output-config-
high/xhigh/max` (Claude-compatible `output_config.effort`), `filter-thinking`.
Meta supports only high/xhigh (no max). Astra uses reasoning-effort-high/xhigh.

---

## 5. Decisions ledger (relevant recent ones)

- **D58** — DeepSeek V4 Pro GA under stable alias (catalog 19).
- **D59** — Grok 4.6 replaces 4.5, exact slug, xhigh (catalog 19).
- **D60** — Route-scoped final-boundary sanitizer for `prompt_cache_retention`
  (2.20.0). Fixes the Sol 400. Route-scoped, not model-scoped.
- **D61** — GLM-5.3 not available on Token Plan; reverted; runbook preserved.
- **D62** (this batch) — Astra + Meta integration; Astra lead-capable after
  probe; Meta muse-spark pair. See STATUS top entry + commits.
- Full ledger: `docs/claude-multi-final/DECISIONS.md`.

---

## 6. PLANNED / PENDING — your work

### 6a. WS4 — single-model mode for ANY model + `--no-subagents` + local Qwen
This is the main implementation task. Spec (from the plan file):

**Goal:** let ANY catalog model run as a single-model ordinary session
(`claude-gateway --model <m>`), even if it has no ordinary profile; add a
durable `--no-subagents` flag (settings `permissions.deny:["Agent"]`); add the
local Qwen3.8-Flash-Next server as a provider.

Sub-parts:
1. **New renderer adapter `cliproxy-openai-compat-v1`** — for keyless
   OpenAI-compatible upstreams. Emits a `openai-compatibility` gateway section
   (name/base-url/models with name/alias/display-name/force-mapping; NO
   api-key-entries/headers/prefix/thinking). Extend `rendered_selectors()` and
   `provider_selectors()`/availability to treat `direct-openai` as always
   available. Add a `direct-openai` transport branch to
   `schemas/providers.schema.json` (http URL allowed ONLY here, auth none).
2. **Catalog:** provider `llm-local` (adapter cliproxy-openai-compat-v1,
   base `http://bt-lab-02.lan:8010/v1`, auth none, independence_family local,
   support_note re keyless LAN trust) + model `qwen-flash-next`
   (wire `qwen3.8-flash-next`, capabilities `["lead"]`, single high lane,
   context client/provider 320032, declared 431104, validated 200000,
   ordinary_profile `flash431`). Add `flash431` to the ordinary_profile enums.
   Fence math: server window 431104 − 131072 completion reserve = 300032 input
   budget; fence 320032 = budget + Claude's 20000 reserve; trigger 270028.
   (A 431104 window/trigger 369993 would overshoot the budget — rejected.)
3. **Single-model mode:** `direct_single_model_context()` (fence = own
   selectors, window = provider_tokens, trigger = auto_compact_trigger(window),
   scalar passthrough); `single_model_launch_models()`; `prepare_direct`
   accepts ANY model (profile-less ones record `context_profile: null`); G
   picker gains a single-model section; `_switch_ordinary_model` within-class
   only. `make_ordinary_record` accepts `context_profile=None`; session schema
   `context_profile` oneOf gains `{"type":"null"}`. `launch.py` resume
   recompute handles null profile.
4. **`--no-subagents`:** cli flag; optional boolean `no_subagents` in the
   session record; `compile_ordinary_scope` sets
   `settings.permissions.deny=["Agent"]`; `compile_direct_launch` moves
   `CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS=1` to env_set. Resume re-applies
   from the record; mismatching explicit flag on resume is rejected.
5. **Tests:** extend test_render (new section golden + selector parity +
   keyless/always-available + re-bless), test_compiler (single-model math incl
   astra/gpt55 numbers + env move), test_scope (ordinary deny settings),
   test_cli (flag parse/record/resume/mismatch; single-model picker section;
   cross-class switch refusal; astra now appears in the ordinary picker),
   test_sessions (nullable profile + optional no_subagents), test_launch
   (null-profile resume). Update picker row/index assertions that shift.
6. **Version:** this is a launcher-src change → bump to 2.23.0/catalog23 (or
   whatever is next). Update test pins.

**Notes for you:** This was started and reverted to give you a clean baseline.
You may redesign it if you find a better approach — the operator wants you to
reconsider holistically. The local Qwen server details are in
`~/projects/llm-serving/` and the global wiki
(`~/.agents/wiki/domain/bt-lab-02-*.md`): OpenAI-compatible, keyless,
`bt-lab-02.lan:8010`, model string `qwen3.8-flash-next`, context 431104.

### 6b. Meta muse-spark live probe (needs operator approval)
Probe `muse-spark-1.3` (and optionally `-contributor`) through the Meta
Anthropic Messages route to validate: auth, thinking-always-on, tool/stream,
high/xhigh efforts, the extra `refusal` stop_reason. Then flip the Meta listing
descriptor from `attempt` to `verified`. **Approval-gated.**

### 6c. Astra near-limit context validation (optional, approval-gated)
The Astra probe only exercised ~310 tokens. If you want to validate the 1M
fence, run a bounded larger-context probe (approval-gated). If it rejects above
272000, drop Astra's fence to 272000 (catalog-only). Otherwise keep 1M.

### 6d. Remaining presets (optional)
Create any still-wanted presets: `muse-direct`, `muse-contributor-direct`,
`qwen-local-direct` (needs WS4), etc. The 3 Astra ones already exist.

### 6e. Holistic reconsideration (operator's explicit ask)
The operator wants you to **reconsider the setup in entirety** — how everything
integrates, and how to improve claude-multi — **especially the TUI/interface**.
Think about: the composition/picker UX, single-model mode ergonomics, the
provider/model management TUI, whether the current model roster and profiles
make sense, and any simplifications. Propose improvements; implement the
high-value ones within the standing rules.

---

## 7. Verification commands

```bash
cd /home/kotur/personal/nixos-dotfiles/home-manager/claude-multi
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .   # full suite
PYTHONPATH=src:tests python3 tests/bless.py                        # re-bless goldens
nix-build --no-out-link package.nix                                # package build
nix build --no-link --file tests/default.nix                       # sandbox
claude-multi doctor                                                # health
claude-multi models                                                # model list
```
Current baseline: **1683 tests OK (skipped=2)**, doctor Ready.

Activation (operator-gated): `home-manager switch --flake
/home/kotur/personal/nixos-dotfiles#kotur`. Rollback anchor before this batch's
next changes: **gen 134**.

---

## 8. Key facts to not forget

- Astra is **lead-capable** and activated (2.22.0/catalog22, gen 134). 3 Astra
  compositions exist and are ready to switch to.
- Astra's 1M fence is **unverified near-limit** (probe was ~310 tokens);
  validated_tokens stays 200000.
- Meta muse-spark is **activated but NOT live-probed**. Meta has no `max`
  effort (high/xhigh only).
- GLM-5.3 is **NOT** on Token Plan; GLM-5.2 is active. Runbook preserved.
- WS4 is **not implemented** — it is your main task (§6a).
- The operator is switching their session to an Astra-based composition; do not
  disturb that. Continue with pending work.
- Nothing pushed; branch `feature/term-only`.

---

## 9. Where to stop / what "done" looks like for you

Astra is usable now (that was the immediate goal). Your continuation:
1. Implement WS4 (§6a) — single-model mode + `--no-subagents` + local Qwen.
2. Get operator approval for, then run, the Meta probe (§6b).
3. Optionally validate Astra near-limit (§6c) and add remaining presets (§6d).
4. Reconsider the setup holistically and improve the TUI/interface (§6e).
Follow the standing rules (§3). Tests before claims. No provider calls without
approval. No push without approval.
