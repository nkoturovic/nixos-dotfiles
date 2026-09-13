# Open items — ordered, with entry points and done-criteria

Nothing here is blocked on effort. Each item is gated on **operator approval**
(real provider calls; per `AGENTS.md` §6 rule 1), **operator direction**
(open-ended redesign), or is a **self-contained engineering task** you can start
immediately (marked ▶).

> **Operator state at handoff:** the operator has taken testing over and asked
> that testing not be over-done. Treat "run the full suite" as *their* call
> unless a change requires it.

---

## 1. ▶ The Claude Code re-pin (issue 029) — highest-value actionable item

**Status:** investigating; two fix attempts failed; **pin stays 2.1.220**.
**Issue:** `docs/claude-multi-final/issues/029-repin-trust-dialog-default-flip/README.md`

**Why it matters:** the operator wants to upgrade Claude Code. The evidence
gate refuses to promote 2.1.261 because our harness can't drive its onboarding.

**What is established:**
- The failing marker is `probe PTY timed out waiting for b'WARNING'`, raised at
  `src/claude_multi/probe.py:1386` (`read_more`), at two sites:
  `tests/test_scope_probe.py:1124-1128` and `:1205-1208`.
- Interaction table row 3 is `PTYInteraction(b"Quick safety check", <answer>)`;
  row 4 waits for `WARNING` (the bypass-permissions dialog that follows
  onboarding) and times out.
- **2.1.261 flipped the workspace-trust dialog** to
  `cancelFirst:!0, focus:"cancel", hideIndexes:!0` — the focused option is
  "No, exit", so the old hard-coded `1\r` answered *cancel*. Intentional
  upstream safety change, not a launcher defect.
- **Attempt 1** (`ce033cf`) added `probe.trust_dialog_answer()` — version-aware
  (≥ 2.1.261 → `\x1b[B\r`, else `1\r`, fail-closed for unparseable). 2.1.220
  stayed green (8 probe tests OK), but 2.1.261 **still failed**; the capture
  showed Down *did* register (focus moved to "Yes") yet focus reverted, so
  Enter did not confirm.
- **Attempt 2** — nine keystroke variants (`Down+CR`, `Down+LF`, `Down+space`,
  split Down/CR, `j+CR`, `2+CR`, `Tab+CR`, `Right+CR`, `Down+ESC[13~`) —
  **all nine failed identically**. So **the keystroke is NOT the root cause**;
  do not start from the interaction table again.
- The failing capture ends with the terminal-capability query
  `\x1b[>0q\x1b[c` (XTVERSION + DA1) in the stream — the client may be waiting
  on a terminal response before servicing input, or the harness's write/render
  interleaving differs from a real terminal.

**NEW LEAD (strong, recommended next step): pre-seed trust instead of scripting the dialog.**
- `hasTrustDialogAccepted` exists in **both** binaries (2.1.220: 10 hits;
  2.1.261: 9). Both embed the remedy text: *"accept the trust dialog here once
  interactively, or set `projects[…].hasTrustDialogAccepted: true` in …"*.
- The 2.1.261 per-project default object is
  `{allowedTools:[],mcpContextUris:[],mcpServers:{},enabledMcpjsonServers:[],
  disabledMcpjsonServers:[],hasTrustDialogAccepted:!1,…}`.
- Config path resolution (from the 2.1.261 bundle):
  `$CLAUDE_CONFIG_DIR/.claude.json` (or `<home>/.claude.json`), and
  `<base>/.config.json` wins if it already exists.
- `probe.build_fixture` (`probe.py:540-567`) creates `home`, `xdg/*`,
  `runtime`, `claude-config` and **writes no files**;
  `ProbeFixture.environ()` points `CLAUDE_CONFIG_DIR` at `<root>/claude-config`;
  `run_native_pty` launches with `cwd=fixture.home` (`probe.py:1358`), so the
  project key should be the canonicalized `fixture.home`.
- **Proposed:** after `build_fixture`, write
  `<fixture.claude_config_dir>/.claude.json` with
  `{"projects": {"<realpath(fixture.home)>": {"hasTrustDialogAccepted": true}}}`.
  Best placed as a helper owned by `build_fixture` (it already owns the layout
  and the no-live-root invariants), invoked by the `RealPinnedBinaryTests`
  setup so both probe sites inherit it — then the two trust-dialog rows fall
  out of the table entirely.

**Verify empirically, do not assume:** (a) `.config.json` vs `.claude.json`
precedence; (b) whether the project key is the realpath or the literal cwd;
(c) that the seed stays strictly inside the fixture root so
`_assert_real_run_allowed` (`probe.py:1133`) still passes — a seed resolving to
a live config must be refused.

**Done-criteria:** the 2.1.261 gate run passes end-to-end, the contract's
`capabilities.onboarding_trust` moves from `pending` **only with that evidence**
(never by assertion), 2.1.220 stays green, and the pin is promoted.

**Alternative if the seed fails:** diff a full raw PTY transcript of 2.1.261
against 2.1.220 to find the first screen-sequence divergence, per issue 029's
"revised next steps".

**Keep or revert?** `ce033cf` (`trust_dialog_answer`) is correct on its own
terms, fail-closed, tested, and non-regressing — but it did **not** fix the
gate. Keeping it is fine; the operator was asked and has not objected.

---

## 2. ▶ `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` is unmodeled — a second roster-flattening vector

**This is the highest-value defensive fix available.** New finding from the
2.1.261 binary (0 occurrences in 2.1.220 → **14** in 2.1.261).

**Why it matters:** per the 2.1.257 changelog (embedded in the binary; the
wiki snapshot stops at 2.1.211):

> Added `CLAUDE_CODE_SUBAGENT_MODEL_FORCE` to apply `CLAUDE_CODE_SUBAGENT_MODEL`
> (or the main model) to every subagent, ignoring per-spawn and agent-definition
> model overrides

That is exactly the failure claude-multi exists to prevent (D39: a roster
silently degrading to one model). D39's fix made the lead fence roster-aware,
and `DECISIONS.md:479-483` names the settings-env override as "the one
remaining roster-flattening vector … post-fix it would recreate the incident."
`_FORCE` is a **new second entrance** into that chain and is currently
unmodeled everywhere. Corroborating bundle strings exist
(`'Workflow agent model "…" ignored: CLAUDE_CODE_SUBAGENT_MODEL_FORCE is set'`).

**Exact change list:**
1. **Doctor radar** — `_subagent_model_override_paths`
   (`src/claude_multi/cli.py:6542-6570`, called from the doctor report at
   `cli.py:6893-6902`) currently matches only `CLAUDE_CODE_SUBAGENT_MODEL` in
   three settings files' `env` blocks. Add `CLAUDE_CODE_SUBAGENT_MODEL_FORCE`
   (it is a *flag*, not a model id, so the hit predicate and the Attention
   wording at `cli.py:6896-6901` need a second branch). Tests:
   `SubagentModelRadarTests` (`tests/test_cli.py:7212-7224`) gain a `_FORCE` case.
2. **Compiler/launch defence-in-depth** — add it to `RESERVED_LEAD_ENV_KEYS`
   (`catalog.py:45-59`), **both** `env_unset` tuples (`compiler.py:410` and
   `:918`), and the preflight clear at `launch.py:979-980`.
3. **Goldens** — add it to `tests/goldens/default/env.json` `unset`, then
   re-bless with diff review.

**Done-criteria:** full suite green (modulo the 2 known failures), goldens
reviewed, `STATUS.md` + a DECISIONS entry recording the finding. Needs no
provider call and no activation beyond the normal HM switch.

**Caveat:** the precise resolution semantics are read from minified strings,
not behaviour — treat the exact interaction with `availableModels` as
unverified.

---

## 3. ⏰ TIME-CRITICAL — the DeepSeek Pro wire reroutes on 2026-09-14 04:00 UTC

`deepseek-v4-pro` starts serving **V4.1-Flash at Flash prices** from
**2026-09-14 04:00 UTC** (this handoff is written 2026-09-13), until V4.1-Pro
ships. Four presets route slots to that wire:

- **`deepseek`** — `deepseek-pro` is the **lead**, plus analyst/max,
  implementer/max, reviewer/max → the whole preset collapses onto the Flash wire.
- `sol-qwen-deepseek`, `grok-deepseek`, `qwen-deepseek` — Pro slots in various roles.

After the flip, their "Pro" slots provide **no model diversity** (the catalog's
own hint already says "the Flash entry is the clearer choice"). The other 24
presets are unaffected.

**Action:** re-check after the date; decide whether to keep the Pro entry,
relabel it, or drop the Pro slots from those presets. Record the decision.
The catalog already states the reroute truthfully in
`deepseek-pro.context.qualification` + `routing_note` + the cm-implementer
`role_hint`, so nothing is *wrong* today — it is a curation question.

---

## 4. Operator-gated: live probes

All require **explicit per-call approval** (`AGENTS.md` §6 rule 1).

| Probe | Purpose | Notes |
|---|---|---|
| **Meta muse-spark** (muse-spark + `-contributor`) | Validate auth, thinking-always-on, high/xhigh efforts, the extra `refusal` stop_reason — **and now a tools/schema case** for issue 028 | Then flip the Meta listing descriptor `attempt` → `verified` |
| **LAN near-limit** (bt-lab-02) | Validate the 320,032 fence / recall at depth | Only meaningful after §5 below |
| **Astra near-limit** | The 1M fence is still unverified near-limit | Optional; D63's 800K ceiling already mitigates |
| **Any `validated_tokens` move** | The evidence field moves only after an approved live call | D58/D65 doctrine |

---

## 5. ▶ (or operator) `qwen-flash-next` is recorded unrouteable

The local Qwen model is fully configured (fence 320032, profile `flash431`) and
the LAN acceptance pings **passed** (direct + via gateway; the upstream server
accepts the `qwen3.8-flash-next` name), **but production serves the GGUF-path
id with no alias**, so a real session cannot route until either:

- the server adds `--alias qwen3.8-flash-next` (the `loadtest.sh` convention —
  recommended, stable across profile swaps), **or**
- the catalog wire changes to the GGUF-path id (works today, rots on the next
  profile change).

**Note:** the operator asked that the LAN host **not** be modified in that
session — it is another agent's work. Confirm ownership before touching
bt-lab-02. The `qwen-local-direct` preset depends on this.
Catalog truth is already recorded in both the model qualification and the
provider support_note.

---

## 6. Issue 028 — Meta rejects strict-incomplete tool schemas (HTTP 400)

**Status:** investigating, probe-gated.
**Issue:** `docs/claude-multi-final/issues/028-meta-strict-tool-schema/README.md`

A Stop-hook/goal-evaluator call through the Meta route returned
`400 'required' is required to be supplied and to be an array including every
key in properties. Missing 'impossible'.` Evidence is the gateway journal
(`POST /v1/messages?beta=true → 400`, fast rejection = upstream); the exact
offending schema was **not** observed (inference, not proof). Route-specific,
not model-specific.

**Impact:** normal lead/agent traffic is unaffected; but on Meta-led sessions
the session goal cannot auto-clear (the evaluator 400s, so the stop hook errors
each stop). Workaround: clear the goal manually.

**Next step:** fold a tools/schema case into the Meta probe (§4). **Do not**
"fix" it by stripping evaluator properties or special-casing model names; the
gateway-side normalization idea is flagged HIGH RISK (rewriting `required`
changes tool-call semantics) and needs the same adversarial care as D60.

---

## 7. Open-ended: TUI / interface reconsideration

The operator's standing holistic ask — "reconsider the setup in entirety,
especially the TUI/interface". **Currently blocked on operator direction**: no
scope has been given, so it is not yet an actionable item. Candidate areas if
direction arrives: composition/picker ergonomics, the models/providers pane,
single-model session UX, or a general review.

---

## 8. Output tokens — revisit only on evidence

**Current, decided (D66):** output tokens are **delegated to the client**. The
launcher never sets `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (a *reserved* key, unset on
both launch paths); no catalog/schema/scope/gateway field exists for output.
The pinned client resolves `max_tokens` as env → registry → unknown-model
fallback **32,000** (`Mxg`; upper `Oxg=128000`), and our aliases are
registry-unknown, so **32,000 is what is sent**, inside DeepSeek's 384K ceiling.

**Do not** model or set output tokens without a demonstrated failure. The
trigger that would justify it: an observed `stop_reason: max_tokens` (or
visibly truncated output) on a DeepSeek lane under real work.

---

## 9. Documentation fixes (opportunistic)

See `README.md` §6 for seven verified staleness items (checkpoint index,
top-level README status line, issue-029 index label, the "four patches"
comment in `claude-multi.nix`, DECISIONS D63/D64 activation headers,
`HANDOFF.md`'s pre-D65 DeepSeek text, and the emergent `flash431` window).
Fix them if you are already touching those files; none is load-bearing.
