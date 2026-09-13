# State snapshot — 2026-09-13, commit `7525b31`, live 2.24.0 / catalog 26 at HM gen 139

Everything below was gathered read-only on 2026-09-13 by a 12-leg analysis
pass. Nothing in this file was inferred beyond what the sources state;
explicit unknowns are marked.

---

## 1. Live system state

| Thing | Value |
|---|---|
| Home Manager generation | **139** → `/nix/store/2js338kjwg9k0lnksmac8y24hfl53zgm-home-manager-generation` (built 2026-09-10 10:22) |
| Prior generations | 138 → `p5s4yiyk23m1h3m9949qd94qlqfj3rwv`; 137 → `7yw6lbnggl1smbsi1r9gpm1dpwrj0f9y` |
| Launcher / catalog | **2.24.0 / 26** (`home-manager/claude-multi/version.json`) |
| Gateway | `cli-proxy-api` **7.2.80** (`/nix/store/1azvv2xl63fhin5yimz85n8wq4ckdf0j-cli-proxy-api-7.2.80`) |
| Gateway health | `curl -sf http://127.0.0.1:8317/healthz` → `{"status":"ok"}` |
| Service | `cli-proxy-api.service` active (running), enabled, ~4h uptime at capture, handling live traffic |
| Trust anchor (Claude) | **2.1.220**, sha256 `674f61f2…c863`, `/home/kotur/.local/share/claude/versions/2.1.220` |
| Configured symlink | `/home/kotur/.local/bin/claude` → `…/versions/2.1.261` (advisory drift; NOT the anchor) |
| Sessions | **42** recorded · 42 durable · 0 legacy |
| Presets | **28** files in `~/.config/claude-multi/compositions/` (all mode 0600) |
| Served selectors | **44** lane aliases on the running gateway |

`doctor` status word: **Ready**. Attention lines (verbatim, all by-design):

1. `Claude 2.1.261 is available at /home/kotur/.local/bin/claude while the pinned trust anchor is 2.1.220; run claude-multi update to re-pin with evidence (offline inspection + the offline probe suite), effective immediately via the operator contract override`
2. `Configured symlink /home/kotur/.local/bin/claude resolves to /home/kotur/.local/share/claude/versions/2.1.261 (advisory drift only; newer/different unqualified version '2.1.261' present); the inspected artifact remains the trust anchor.`
3. `Shared daemon: no shared-daemon domain at /tmp/cc-daemon-1000; status not exposed.`

No problem (BLOCK) lines. `Collisions: none (0 project agents)`.

**Unresolved observation:** doctor session lines reference compositions
`sol-qwen-deepseek-flash` (sessions 5303183f, 57348f2f) and
`sol-qwen-glm-deepseek-flash` (58c87cef) — neither matches an on-disk preset
(the store has `sol-qwen-deepseek`, without `-flash`). Their scopes still
report `OK … recompiled against the installed catalog (catalog drift)`, so it
is not a hard failure. Not investigated.

---

## 2. Session changelog — the 19 commits `d03744a..7525b31`

Branch `feature/term-only`, **pushed** (`git rev-list --left-right --count
ssh/feature/term-only...HEAD` = `0 0`; `git ls-remote origin` = `7525b31`).
One unstaged, unrelated working-tree change:
`home-manager/kotur.dotfiles/profile` (adds a `~/.bun/bin` PATH block and
`export LLAMA_CPP_BASE_URL=http://bt-lab-02.lan:8010`).

### Version transitions

| Commit | launcher | catalog |
|---|---|---|
| `d03744a` (base) | 2.22.0 | 22 |
| `17b4e39` (D63) | 2.22.0 → **2.23.0** | 22 |
| `8d520f8` (WS4) | 2.23.0 → **2.24.0** | 22 → **23** |
| `46e0aad` (LAN probe) | 2.24.0 | 23 → **24** |
| `c34c6de` (D65) | 2.24.0 | 24 → **25** |
| `5c85b88` (catalog26) | 2.24.0 | 25 → **26** |

### D63 — 800K ceiling

- **`17b4e39`** `claude-multi 2.23.0: cap 1M-class operating window at 800K (D63)` — adds
  `OPERATING_WINDOW_CEILING = 800_000` + `operating_window()` in `composition.py`;
  applies at `compute_scalar`, `compute_auto_compact_capacity`, `_lead_context_policy`;
  applies in `compiler.py` `direct_profile_context` (scalar + window); adds an
  "Operating ceiling" line to the lead appendix; `cli.py` large-profile label becomes
  "800K operating window". Goldens refreshed.
- **`9896c5d`** checkpoint `2026-09-06-v2.23.0`.
- **`141d0eb`** activation record (gen 135).
- **`675d5a1`** the D66 audit (which is also the D63 coverage verification).

### WS4 — single-model mode, `--no-subagents`, local Qwen

- **`4859ab6`** `WS4a+4b: OpenAI-compat adapter, llm-local provider, qwen-flash-next` —
  `cliproxy-openai-compat-v1` adapter + `direct-openai` transport branch (http+port
  only, auth none; https and secrets rejected); new provider `llm-local`, new model
  `qwen-flash-next` (fence 320032, declared 431104, profile `flash431`); new
  `openai-compatibility` gateway section; schema branches; goldens.
- **`8d520f8`** `2.24.0/catalog23: WS4 single-model mode, no-subagents, local Qwen (D64)` —
  the big one: single-model mode for any model (null `context_profile`, own-model fence,
  G-picker `single` section, class-confined switches, null handling in
  hook/doctor/repair/resume); `--no-subagents` tri-state flag with scope `Agent` deny +
  env belt + mismatch rejection; lead-only composition support.
- **`fe38269`** checkpoint `2026-09-06-v2.24.0`; **`32704a5`** activation record (gen 136).
- **`46e0aad`** `catalog24`: records the LAN acceptance probe outcome + the residual
  (production serves the GGUF-path id with **no alias**).

### D65 — DeepSeek V4.1-Flash

- **`c34c6de`** `catalog25`: flash `wire_model` → `deepseek-flash` (canonical), display
  → "DeepSeek V4.1 Flash", routing_note/qualification corrected; pro
  routing_note/qualification/role_hint state the **2026-09-14 04:00 UTC reroute**;
  provider support_note updated; AGENTS/USAGE amended; render golden delta exactly 4
  lines.
- **`1fe831c`** activation record (gen 138).
- **`5c85b88`** `catalog26`: `deepseek-flash` `validated_tokens` 1,000,000 → **200,000**.
- **`06c630d`** activation record (gen 139).

### D66 — audit

- **`675d5a1`** `verify 800K coverage, pin flash431 window, record output-token policy`
  — docs (DECISIONS +61) + one test. No `version.json` change, no src change.

### Issues / docs

- **`e0a81b6`** opens **issue 028** (Meta strict tool-schema 400).
- **`89a1996`** opens **issue 029** (2.1.261 re-pin blocked by the trust dialog).
- **`ce033cf`** `version-aware trust-dialog answer for the re-pin gate` — adds
  `probe.trust_dialog_answer()` + `TrustDialogAnswerTests`; 2.1.220 stayed green.
- **`0b1bb36`** issue 029 attempt 1 failed (Down registers, Enter does not confirm).
- **`a44e2a2`** issue 029 attempt 2 — **nine keystrokes all fail; keystroke ruled out**.
- **`7525b31`** checkpoint `2026-09-10-deepseek-v41`.

---

## 3. Catalog reference (catalog_version 26)

15 models, 8 providers. Every lead-capable model has `lead.effort = "ultracode"`,
`lead.env = {}`; `gpt55` has `lead: null`.

| id | provider | wire_model | display | caps | default lane | lanes (effort → selector → contract) | ctx client/provider/declared/validated/scalar/profile |
|---|---|---|---|---|---|---|---|
| astra | openai | gpt-6-astra | GPT-6 Astra | lead,agents | high | high → `gpt-multi-astra-high[1m]` → reasoning-effort-high; xhigh → `-xhigh[1m]` → reasoning-effort-xhigh | 1000000/1000000/1050000/200000/null/large |
| deepseek-flash | deepseek | **deepseek-flash** | DeepSeek V4.1 Flash | lead,agents | high | high → `claude-multi-deepseek-flash-high[1m]` → output-config-high; max → `-max[1m]` → output-config-max | 1000000/1000000/1000000/**200000**/null/large |
| deepseek-pro | deepseek | deepseek-v4-pro | DeepSeek V4 Pro | lead,agents | high | high/max → `claude-multi-deepseek-pro-{high,max}[1m]` | 1000000/1000000/1000000/200000/null/large |
| fable | anthropic | claude-fable-5 | Fable 5 · 1M selector | lead,agents | max | max → `claude-fable-5[1m]` → null | 1000000/1000000/1048576/1000000/null/large |
| glm52 | qwen | glm-5.2 | GLM-5.2 | lead,agents | max | max → `claude-multi-glm52-max[1m]` → reasoning-effort-max | 1000000/1000000/1048576/200000/null/large |
| gpt55 | openai | gpt-5.5 | GPT-5.5 | **agents only** | high | high → `gpt-multi-gpt55-high` → reasoning-effort-high | 258400/258400/272000/258400/**258400**/null |
| grok46 | openrouter | x-ai/grok-4.6 | Grok 4.6 | lead,agents | xhigh | high/xhigh → `claude-multi-grok46-{high,xhigh}` | 500000/500000/500000/200000/null/**grok** |
| kimi-k3 | kimi | k3 | Kimi K3 · 1M selector | lead,agents | max | max → `claude-multi-kimi-k3[1m]` → output-config-max | 1000000/1000000/1048576/208034/null/large |
| muse-spark | meta | muse-spark-1.3 | Muse Spark 1.3 | lead,agents | xhigh | high/xhigh → `claude-multi-muse-spark-{high,xhigh}[1m]` | 1000000/1000000/1048576/200000/null/large |
| muse-spark-contributor | meta | muse-spark-1.3-contributor | Muse Spark 1.3 Contributor | lead,agents | high | high/xhigh → `-contributor-{high,xhigh}[1m]` | 1000000/1000000/1048576/200000/null/large |
| opus | anthropic | claude-opus-4-8 | Opus 4.8 · 1M selector | lead,agents | xhigh | xhigh → `claude-multi-opus-4-8[1m]` → null | 1000000/1000000/1048576/1000000/null/large |
| opus5 | anthropic | claude-opus-5 | Opus 5 · 1M selector | lead,agents | xhigh | xhigh → `claude-multi-opus-5[1m]` → null | 1000000/1000000/1048576/200000/null/large |
| qwen-flash-next | llm-local | qwen3.8-flash-next | Qwen3.8 Flash Next (local) | **lead only** | high | high → `claude-multi-qwen-flash-next` → null | 320032/320032/431104/200000/null/**flash431** |
| qwen38 | qwen | qwen3.8-max | Qwen3.8 Max | lead,agents | max | max → `claude-multi-qwen38-max[1m]` → reasoning-effort-xhigh | 1000000/**983616**/983616/983616/**983616**/large |
| sol | openai | gpt-5.6-sol | GPT-5.6 Sol | lead,agents | high | high/xhigh → `gpt-multi-sol-{high,xhigh}[1m]` | 1000000/1000000/1050000/343541/null/large |

Extra context keys: `gpt55.provider_stated_limit_tokens: 272000`;
`kimi-k3.provider_stated_limit_tokens: 262144` + `user_reported_tokens: 1000000`;
`sol.provider_stated_limit_tokens: 1050000`. `minimum_tested` is `2.1.216/7.2.80`
for all except `opus5` (`2.1.218/7.2.80`).

| Provider | Adapter | Transport / base_url / auth | Family | Payload contracts | Support |
|---|---|---|---|---|---|
| anthropic | cliproxy-oauth-claude-v1 | oauth-pool `claude` | anthropic | — | anthropic-supported |
| deepseek | cliproxy-claude-compatible-v1 | direct `https://api.deepseek.com/anthropic`, header `x-api-key` ← `env:DEEPSEEK_CLAUDE_API_KEY` | deepseek | output-config-high, -max | locally-validated-experimental |
| kimi | cliproxy-claude-compatible-v1 | direct `https://api.kimi.com/coding`, header `x-api-key` | moonshot | output-config-max, **filter-thinking** | locally-validated-experimental |
| llm-local | **cliproxy-openai-compat-v1** | **direct-openai** `http://bt-lab-02.lan:8010/v1`, auth **none** (keyless) | local | — | locally-validated-experimental |
| meta | cliproxy-claude-compatible-v1 | direct `https://api.meta.ai`, bearer `env:META_CLAUDE_API_KEY` | meta | output-config-high, -xhigh | locally-validated-experimental |
| openai | cliproxy-oauth-codex-v1 | oauth-pool `codex` | openai | reasoning-effort-high, -xhigh | locally-validated-experimental |
| openrouter | cliproxy-claude-compatible-v1 | direct `https://openrouter.ai/api`, header `x-api-key` | x-ai | output-config-high, -xhigh | locally-validated-experimental |
| qwen | cliproxy-claude-compatible-v1 | direct `…maas.aliyuncs.com/apps/anthropic`, bearer | alibaba | reasoning-effort-xhigh, -max | locally-validated-experimental |

`anthropic` is the only provider with `passthrough_routes` (fork=true):
`claude-fable-5`, `claude-opus-5`, `claude-opus-4-8`.

**Catalog delta this session:** model set 14 → 15 (gained `qwen-flash-next`),
provider set 7 → 8 (gained `llm-local`), catalog 22 → 26; launcher 2.22.0 → 2.24.0.

---

## 4. Presets — all 28, resolved read-only

`~/.config/claude-multi/compositions/`; all regular files, mode 0600, no
symlinks. Repo `catalog/` is byte-identical to the deployed nix-store asset
root. Trigger = `min((window−20000)×0.9, window−33000)`.

| name | lead | variants | window / trigger |
|---|---|---|---|
| **astra-deepseek-flash** | astra | 3 (analyst/impl deepseek-flash high P; reviewer deepseek-flash max P) | 800000 / 702000 |
| astra-muse | astra | 7 | 800000 / 702000 |
| astra-qwen-muse | astra | 6 | 800000 / 702000 |
| astra | astra | 0 (lead-only) | 800000 / 702000 |
| deepseek-flash | deepseek-flash | 3 | 800000 / 702000 |
| deepseek | deepseek-pro | 6 | 800000 / 702000 |
| fable | fable | 6 | 800000 / 702000 |
| fable-sol-glm-qwen-opus | fable | 7 | 800000 / 702000 |
| fable-sol-glm-qwen | fable | 9 | 800000 / 702000 |
| fable-sol-qwen-glm | fable | 15 | 800000 / 702000 |
| glm-sol | glm52 | 6 | 800000 / 702000 |
| grok-deepseek | grok46 | 6 | **500000 / 432000** |
| kimi-sol | kimi-k3 | 6 | 800000 / 702000 |
| kimi-sol-qwen | kimi-k3 | 9 | 800000 / 702000 |
| kimi-sol-qwen-glm | kimi-k3 | 12 | 800000 / 702000 |
| kimi-sol-qwen-glm-fable | kimi-k3 | 18 | 800000 / 702000 |
| muse | muse-spark-contributor | 4 | 800000 / 702000 |
| muse-contributor-direct | muse-spark-contributor | 0 | 800000 / 702000 |
| muse-direct | muse-spark | 0 | 800000 / 702000 |
| opus48-sol-glm-qwen | opus | 9 | 800000 / 702000 |
| opus-kimi | opus5 | 4 | 800000 / 702000 |
| opus-sol | opus5 | 4 | 800000 / 702000 |
| qwen-deepseek | qwen38 | 8 | 800000 / 702000 |
| **qwen-local-direct** | qwen-flash-next | 0 | **320032 / 270028** |
| qwen-muse | qwen38 | 6 | 800000 / 702000 |
| qwen-sol | qwen38 | 6 | 800000 / 702000 |
| sol-direct | sol | 0 | 800000 / 702000 |
| sol-qwen-deepseek | sol | 13 | 800000 / 702000 |

`scalar_context_tokens` is non-null (800000) only where the agent set includes
`qwen38` (provider 983616 < client 1M). Only two presets differ from the 1M
class: `grok-deepseek` (500K lead) and `qwen-local-direct` (320K lead). All
leads resolve at effort `ultracode`.

**Created/changed in this session:** `astra-deepseek-flash` (created
2026-09-10 11:02), `deepseek-flash` (description fixed 11:08),
`muse-direct`/`muse-contributor-direct`/`qwen-local-direct` (batch, 2026-09-06
09:39), and the three astra presets (2026-09-05/09-08).

**Affected by the 2026-09-14 deepseek-v4-pro reroute:** `deepseek` (pro is the
**lead**), `sol-qwen-deepseek`, `grok-deepseek`, `qwen-deepseek`. After the
flip their Pro slots serve the same model as their Flash slots.

---

## 5. Source map and source changes

**Modules (`src/claude_multi/`, 19):** `__init__` version · `state` atomic
0600 writes + locks (the only write path) · `strict_json` canonical bytes ·
`validate` closed-vocabulary schema validator · `catalog` load/validate/
invariants/bundle hash · `composition` resolve + context & compaction math +
**D63 clamp** · `custom` operator registry · `compiler` pure compile →
agents/argv/env · `scope` durable scope dirs + `permissions.deny` fence ·
`sessions` records/pointers/reconciliation · `launch` exec boundary +
resume-failure scope rebuild · `transition` relaunch-only composition changes ·
`render` deterministic CLIProxyAPI YAML · `proxy` gateway control/secrets ·
`probe` dev-only loopback harness + **`trust_dialog_answer`** · `upgrade`
evidence-gated re-pin · `dev` draft→check→review→promote · `tui` curses
widgets/editor · `cli` parsing, screens, doctor.

**Source diff since `d03744a`:** 9 files, ≈ +550/−85.

**D63 clamp — 7 call sites:** `composition.py:101` (scalar), `:141`
(capacity), `:158` (provisional trigger; final recomputed at `:263`);
`compiler.py:782` + `:784` (`direct_single_model_context` window + scalar),
`:839` + `:843` (`direct_profile_context` scalar + window). User-visible note
emitted at `compiler.py:242-246`.

**WS4 single-model path:** `compiler.py:735-749` `single_model_launch_models`;
`:752-760` `direct_single_model_selectors`; `:763-785`
`direct_single_model_context` (**fail-closed** — raises if the model *has* a
profile); `:686-700` `ordinary_picker_groups` (keeps `ordinary_launch_models`
profile-pure, adds a synthetic `"single"` section); `:703-732`
`direct_model_for_selector` widened to `tuple[str, str | None]`;
`:848-972` `compile_direct_launch` branched. Null-profile handling threads
through `sessions.py:306,678-692`, `launch.py:494-518`,
`transition.py:730-748`, `cli.py:618-637,7592-7600,7628-7635,7721-7785,3704-3740,6947-6958`.

**`--no-subagents`:** `cli.py:797` (SUPPRESS default → tri-state),
`cli.py:566-627` (fresh/resume/mismatch), `sessions.py:318,337` (recorded),
`compiler.py:911-917` (env belt) + `:928` (still unset first),
`scope.py:497,524-525` (`settings["permissions"] = {"deny": ["Agent"]}`,
assigned after the lifecycle merge so nothing overwrites it),
`transition.py:748` + `launch.py:517` (re-applied on rebuild).

**Compat adapter:** `render.py:34` (empty contracts), `:446-483`
(`openai-compatibility` section), `:293-294` (direct-openai always
available), `:324-326` (selectors include compat aliases); `cli.py`
keyless-provider branches (`_provider_kind_label`, `_connect_hint`,
`_provider_facts`, providers-screen modal).

**Invariants these protect:** profile vs single-model paths are mutually
exclusive and fail closed; a null profile shares no fence, so a different
observed model stays an *observation*, never an implicit re-pin; the
subagent policy is durable and cannot be flipped mid-transcript; no credential
is ever fabricated for a keyless route.

---

## 6. Gateway reference

**Patch list** (`claude-multi.nix`, applied in order ≈ `catalog/gateway.json:10-16`):

1. `cli-proxy-api-loopback-oauth.patch` — binds OAuth callback servers to `127.0.0.1`.
2. `cli-proxy-api-kimi-claude-compat.patch` — x-api-key / `context-length` / `owned-by` machinery.
3. `cli-proxy-api-opus-5-model.patch` — registry entry for `claude-opus-5`.
4. `cli-proxy-api-astra-registry.patch` — registry entry for `gpt-6-astra` (3 codex tiers).
5. `cli-proxy-api-non-claude-cache-retention.patch` — fail-closed strip of
   `prompt_cache_retention` at final outbound boundaries (1,452 lines).

Patches 3+4 both edit the same vendored `models.json`, so **order matters**.
`doCheck`/`postCheck` run `go test ./internal/runtime/executor` (28 tests) —
the only place the retention suite executes in the sandbox.

**Rendered config:** `~/.config/claude-multi/config.yaml` (0600), token
`~/.config/claude-multi/api-key`, auth records `~/.local/share/claude-multi/auth`.
Sections in order: statics → `oauth-model-alias` (keyed by pool) →
`claude-api-key` (one per *direct* provider; carries `context-length` from
`provider_tokens` and per-model `force-mapping`) → `openai-compatibility`
(one per *direct-openai* provider, keyless) → `payload` (`override` bound to a
lane's contract; `filter` expands to every lane alias of the provider).

**No hot-reload on 7.2.80** — every catalog/config edit must be applied with:

```bash
claude-multi-proxy init                 # re-render
systemctl --user restart cli-proxy-api  # reload
```

`home-manager switch` does both. The unit runs `claude-multi-proxy run`,
which itself re-renders before exec — so a bare restart re-renders too.
Stale-daemon symptoms: old aliases served, new model 404, or a 401 from
`/v1/models` while `/healthz` stays green. `doctor` runs the byte-drift and
served-alias cross-checks.

**Loopback posture:** `127.0.0.1:8317`, TLS off, pprof loopback-only,
`remote-management.allow-remote: false`, `ws-auth: true`, usage stats off.
**Token:** `secrets.token_hex(32)`, shape-validated, never printed, written
atomically under the same lock as the config.

**Registry-free implication (important):** for a `direct` / `direct-openai`
provider, adding or re-pointing a wire id is a **catalog-only** change +
re-render + restart — **no** registry patch, no HM rebuild. Only `oauth-pool`
routes (`claude-opus-5`, `gpt-6-astra`) need a registry patch.

---

## 7. Verification apparatus

```bash
cd /home/kotur/personal/nixos-dotfiles/home-manager/claude-multi
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .   # full suite, ~150s (required before "done")
PYTHONPATH=src:tests python3 -m unittest tests.<module>           # focused
PYTHONPATH=src:tests python3 tests/bless.py                       # re-bless default goldens — review the diff
nix-build --no-out-link package.nix                               # package build
nix build --no-link --file tests/default.nix                      # sandbox suite
git diff --check
```

Memory-tight fallback: `TMPDIR=~/.cache/claude-multi-test-tmp …` (tmpfs
fixture copies can hit `Errno 122`).

**Count:** **1,716** discoverable tests at HEAD. (Older checkpoints: 1,710 at
v2.24.0, 1,687 at v2.23.0.)

**The 2 known pre-existing environmental failures** (both in `tests/test_cli.py`):

1. `ImprovementBatchTests.test_line_mode_h_prints_doctor` — asserts
   `"claude-multi doctor: Ready"`, but a real `claude` **2.1.261** on disk vs
   the **2.1.220** pin makes doctor report `Attention`, not `Ready`.
2. `BrokenOverrideDegradationTests.test_update_removes_the_broken_override` —
   the update flow resolves its source checkout from `CLAUDE_MULTI_SOURCE_REPO`
   or `$HOME/personal/nixos-dotfiles`, which does not exist under the test's
   isolated `HOME`.

Both are **proven failing on clean HEAD** (documented in `STATUS.md` D63 entry
and repeated in the v2.23.0/v2.24.0/2026-09-10 checkpoints). Separate from
these: a known **timing flake** in `RealPinnedBinaryTests` under machine-wide
load — re-run the class in isolation before distrusting the pin.

**Goldens.** `tests/bless.py` regenerates **only** `tests/goldens/default/`
(argv ×4, `env.json`, `agents-contingency.json`, `lead-appendix.md`, and the
whole `scope/` tree). The **render golden**
`tests/goldens/render/gateway-default.yaml` has **no bless writer** — it is
compared byte-exact by `test_render.py` and must be regenerated by hand when
the catalog changes (D65's delta was exactly 4 lines).

**Real-binary probes** (`tests/test_scope_probe.py::RealPinnedBinaryTests`):
the only place real pinned binaries run; gated on the native contract's
presence + path + SHA-256 match, so they self-skip in the sandbox. They verify
durable argv/`--add-dir`, the `availableModels` fence (D39), compaction hook
metadata, supervisor takeover, and subagent-stop protocol.

**Dev pipeline** (`claude-multi-dev`): draft → check → review (exact
diff/hash) → promote; writes trusted JSON only, never builds/activates/
restarts/reads secrets. `check` is the gate (runs the sandbox builds in a
candidate tree).
