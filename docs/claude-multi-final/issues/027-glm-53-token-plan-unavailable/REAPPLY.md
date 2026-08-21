# GLM-5.3 Alibaba Token Plan reactivation runbook

**Canonical home for reactivation.** Issue status remains **blocked upstream**.
Do not stage, probe, activate, or migrate anything merely because GLM-5.3 exists
in another product. Start only when the trigger below is satisfied.

Related records: [issue entry point](README.md),
[decision D61](../../DECISIONS.md), [live ledger](../../STATUS.md), and the
[Qwen preview→production precedent](../../blueprints/016-qwen38-production/README.md).

## 1. Immutable evidence and historical correction

The following facts are historical evidence, not hypotheses to retest by
repeating provider calls:

- GLM-5.2 was the repository's **first** GLM catalog entry (D45, commit
  `fdf86b0`). There was no GLM-5.1→GLM-5.2 repository migration. Do not invent
  or document one.
- The applicable migration precedent is Qwen3.8 Max Preview→production (D50,
  commit `d7099f9`): keep the internal identity and selector stable while
  changing the upstream wire/display and revalidating the contract.
- The reviewed GLM-5.3 candidate kept internal model key `glm52`, selector
  `claude-multi-glm52-max[1m]`, gateway alias
  `claude-multi-glm52-max`, generated variant IDs, records, scopes, and
  compositions stable. It changed the provider wire/display to
  `glm-5.3`/`GLM-5.3`. The point-in-time census found 17 records/scopes and 11
  live composition files pinned to `glm52`; stable identity avoided rewriting
  any of them.
- The candidate passed an 805-test focused review run, full discovery with
  1,683 tests OK (2 skips), package/sandbox/fresh Home Manager
  activation-package builds, exact two-line render-golden review, and
  independent Sol-xhigh review. One known PTY timeout passed immediately in
  isolation and the clean full rerun passed. These counts are historical; use
  the then-current full gates at reactivation.
- Exactly one separately approved, bounded call used the Team Token Plan
  Singapore Anthropic route, its normal bearer credential, exact upstream
  `glm-5.3`, max reasoning, streaming, and one offered marker tool, with no
  forced `tool_choice`, retry, fallback, activation, or state migration.
  Alibaba rejected it before model execution with HTTP 400:

  ```text
  InvalidParameter: Model not exist.
  ```

- That rejection proves only that the exact account/route entitlement did not
  accept `glm-5.3` at that time. It proves nothing about GLM-5.3 thinking,
  tools, streaming, max effort, context, or output limits on Alibaba because
  model execution never began.
- No Home Manager activation, live gateway replacement, record/scope/
  composition/credential mutation, transcript access, or transcript deletion
  occurred. Source and live state stayed on GLM-5.2.

### Evidence commits

| Commit | Meaning | How to use it |
| --- | --- | --- |
| `caaa641` | Complete reviewed catalog21 GLM-5.3 candidate, tests, and then-current staged docs | Historical implementation patch. Inspect or selectively reapply implementation/test paths; never cherry-pick it wholesale because its staged docs predate the upstream rejection. |
| `8db80c3` | Exact revert after the failed canary | Proves the candidate was removed and GLM-5.2 restored. Do not cherry-pick it onto a future candidate. |
| `10a710d` | D61/issue evidence: rejection, exact product boundary, safe future trigger | Immutable incident record. |
| `13989d8` | Pins `caaa641` and `8db80c3` as the reusable candidate/revert references | Evidence-reference correction and current baseline when this runbook was written. |

Safe read-only inspection:

```bash
repo=/home/kotur/personal/nixos-dotfiles

git -C "$repo" show --stat --summary caaa641
git -C "$repo" show --stat --summary 8db80c3
git -C "$repo" show --stat --summary 10a710d
git -C "$repo" show --stat --summary 13989d8

git -C "$repo" diff caaa641^ caaa641 -- \
  home-manager/claude-multi/catalog/models.json \
  home-manager/claude-multi/src/claude_multi/render.py \
  home-manager/claude-multi/tests \
  home-manager/claude-multi/version.json

# No output/exit 0 means the revert restored these implementation paths.
git -C "$repo" diff --exit-code caaa641^ 8db80c3 -- \
  home-manager/claude-multi/catalog/models.json \
  home-manager/claude-multi/src/claude_multi/render.py \
  home-manager/claude-multi/tests \
  home-manager/claude-multi/version.json
```

## 2. Current state and the rejection's cause

Point-in-time state at D61 (2026-08-21):

- source/installed launcher: 2.20.0;
- source/installed trusted catalog: catalog20;
- active Home Manager generation: 130; generation 129 was its full prior
  release rollback, while generation 130 is the pre-GLM-5.3 catalog anchor;
- model key `glm52` routes exact wire `glm-5.2`, display `GLM-5.2`;
- selector `claude-multi-glm52-max[1m]` and alias
  `claude-multi-glm52-max` remain active;
- validated context floor remains 200,000 tokens; and
- `claude-multi doctor` was Ready after the candidate revert.

The documentation audit rechecked that state read-only on 2026-08-21:
launcher 2.20.0, Home Manager generation 130 current, gateway active,
`/healthz` 200, 37 loopback-served IDs with stable
`claude-multi-glm52-max` present and raw `glm-5.2`/`glm-5.3` hidden, 36 durable
records, and doctor Ready. No provider request was made.

Future operators must re-read current `version.json`, the latest checkpoint,
and `STATUS.md`; do not assume those version/generation numbers are still
current.

The failure was an upstream exact-allowlist/product-entitlement rejection, not
a renderer, authentication, context, effort, tool, streaming, or fallback
defect. At the time, Team Token Plan named `glm-5.2`, `glm-5.1`, and `glm-5`
while Personal Token Plan named `glm-5.2`; neither named `glm-5.3`.
Exact-string allowlists prohibit inferring support from a newer Z.ai release.

## 3. Product and route boundaries

Keep these products separate. A model existing in one does not authorize it in
another.

| Product | Route/credential | Relevant model ID | Rule |
| --- | --- | --- | --- |
| **Alibaba/Qwen Team Token Plan used here** | Singapore `https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic`, dedicated Token Plan bearer credential, Anthropic Messages shape | Current `glm-5.2`; expected future exact ID **`glm-5.3`** | This is the only route covered by this runbook. On Token Plan the future wire is lowercase exact `glm-5.3`—never `ZHIPU/GLM-5.3`. |
| Alibaba Model Studio third-party/pay-as-you-go | Historically researched as a separate Beijing workspace, OpenAI-compatible route, separate key/region/billing | Historical finding: `ZHIPU/GLM-5.3`; reverify its current existence/name | Never place this ID on the Token Plan host or credential. It is not evidence of Token Plan entitlement. |
| Zhipu/Z.ai GLM Coding Plan | Zhipu-owned endpoint, account, key, quota, and billing | `glm-5.3` | Separate provider onboarding if ever desired; never silently substitute it under provider `qwen`. |
| Z.ai native model documentation | Z.ai's own API documentation and routes | `glm-5.3` | Establishes model contract facts only, not Alibaba Token Plan availability. |

No alternate region, endpoint, alias, moving alias, feature flag, provider, key,
or billing product may be substituted to make the canary pass. Such a change is
a new provider/route decision, not this in-place promotion.

## 4. Preflight trigger: when work may begin

Begin reactivation only after **one** of these authoritative signals explicitly
names exact lowercase `glm-5.3` for the existing Alibaba Team Token Plan:

1. the official Team Token Plan supported-model allowlist; or
2. the authenticated Token Plan console for this exact Singapore Team
   subscription/credential.

A Z.ai release post, Alibaba pay-as-you-go catalog entry, Zhipu Coding Plan
page, reseller announcement, model family name, or version-compatibility guess
is not a trigger. Capture the URL or a non-secret console observation and date
in `STATUS.md` before changing source.

There is no usable Qwen Token Plan listing endpoint today:
`GET /apps/anthropic/v1/models` returned HTTP 404 `Not support`. Therefore:

- do not wait for or poll a listing endpoint;
- do not treat `claude-multi discover qwen` as the trigger; and
- if Alibaba later documents a listing endpoint, using it is a real provider
  request and requires explicit approval for that individual call.

Every real provider request has a separate approval boundary. Approval for the
single acceptance call does not approve a retry, a second shape, a near-limit
probe, activation, or post-activation traffic. Loopback health and local
`/v1/models` checks are not provider calls.

## 5. Safe selective reapplication

Do not run `git cherry-pick caaa641`. Besides implementation/tests, that commit
contains now-obsolete staged documentation asserting route availability before
the failed canary. Reapply only the reviewed implementation/test paths, then
adapt them to intervening catalog and version drift.

Start from a clean, current branch after the trigger is recorded:

```bash
repo=/home/kotur/personal/nixos-dotfiles
cd "$repo"

test -z "$(git status --porcelain)" || {
  printf '%s\n' 'refusing: worktree is not clean' >&2
  exit 1
}

git switch -c glm53-token-plan-reactivation

paths=(
  home-manager/claude-multi/catalog/models.json
  home-manager/claude-multi/src/claude_multi/render.py
  home-manager/claude-multi/tests/goldens/render/gateway-default.yaml
  home-manager/claude-multi/tests/test_catalog.py
  home-manager/claude-multi/tests/test_cli.py
  home-manager/claude-multi/tests/test_compiler.py
  home-manager/claude-multi/tests/test_context.py
  home-manager/claude-multi/tests/test_proxy.py
  home-manager/claude-multi/tests/test_render.py
  home-manager/claude-multi/version.json
)

patch_file=$(mktemp)
trap 'rm -f "$patch_file"' EXIT

git diff --binary caaa641^ caaa641 -- "${paths[@]}" >"$patch_file"
git apply --check "$patch_file"
git apply --3way "$patch_file"
rm -f "$patch_file"
trap - EXIT

git diff -- "${paths[@]}"
```

If `git apply --check` or the three-way apply fails, stop and manually port the
same semantic delta; do not force, overwrite newer files with `git restore
--source=caaa641`, or resolve conflicts by accepting the old file wholesale.
In particular:

- increment the **then-current** catalog version by one; do not blindly reset it
  to 21;
- leave the launcher version unchanged unless current release rules require a
  behavior-version bump for some separate, reviewed code change;
- preserve any newer models, routing notes, tests, provider contracts, or
  gateway patches; and
- update current living docs from this runbook rather than restoring the
  candidate's deleted `blueprints/027-glm-53-promotion` or old issue folder.

## 6. Intended catalog delta

The promotion remains in-place catalog drift:

| Field | Required result |
| --- | --- |
| catalog key | `glm52` unchanged |
| selector | `claude-multi-glm52-max[1m]` unchanged |
| gateway alias | `claude-multi-glm52-max` unchanged |
| generated variant IDs/session shorthand | `*-glm52-max`, `cg:glm52`, etc. unchanged |
| provider/family | `qwen` / `alibaba` unchanged |
| wire | exact **`glm-5.3`** |
| display | **`GLM-5.3`** |
| declared context | exact official **1,000,000** |
| client/provider context | 1,000,000 unchanged |
| scalar/profile | `null` / `large` unchanged |
| validated floor | 200,000 unchanged; a ~512-token canary does not validate a larger context floor |
| lanes | one `max` lane, default `max`, `agent_effort: max` unchanged |
| payload contract | `reasoning-effort-max` unchanged |
| roles/compositions/records/scopes | unchanged; no migration or reorder |
| routing position | peer general-capability tier with Qwen3.8 Max and GPT-5.6 Sol; explicit `preferred` flags remain authoritative |

No provider, endpoint, secret reference, schema, compatibility subsystem,
second GLM entry, fallback, or hidden substitution is added.

### Exact intended render golden delta

Only these two values change in
`tests/goldens/render/gateway-default.yaml`:

```diff
-      - name: "glm-5.2"
+      - name: "glm-5.3"
         alias: "claude-multi-glm52-max"
-        display-name: "GLM-5.2"
+        display-name: "GLM-5.3"
```

Alias, owner, context length, force mapping, lane override, compiler goldens,
and scope goldens must stay byte-identical. Any additional golden change needs
an explicit explanation and review.

## 7. Full offline gates

Run all gates from the product directory with no provider or live-daemon
contact:

```bash
cd /home/kotur/personal/nixos-dotfiles/home-manager/claude-multi

# Focused contract and disposable-loopback route suites.
PYTHONPATH=src:tests python3 -m unittest \
  tests.test_catalog \
  tests.test_render \
  tests.test_compiler \
  tests.test_context \
  tests.test_cli \
  tests.test_proxy

# Re-render intentional goldens, then inspect every changed byte.
PYTHONPATH=src:tests python3 tests/bless.py
git diff -- tests/goldens

# Complete host suite.
PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .

# Offline package and sandbox suite.
nix-build --no-out-link package.nix
nix build --no-link --file tests/default.nix

# Whole Home Manager activation package, build only.
cd /home/kotur/personal/nixos-dotfiles
nix build --offline --no-link .#homeConfigurations.kotur.activationPackage

git diff --check
git status --short
```

Required focused assertions:

- key/selector/alias/variant IDs remain stable;
- exact wire/display/context fields match section 6;
- no active catalog or rendered `glm-5.2` wire/display remains;
- disposable fake-Qwen route maps the stable alias to exact `glm-5.3`, uses
  bearer auth, preserves the offered tool, sends max effort, invents no
  `tool_choice`, and preserves the D60 cache-retention boundary;
- ordinary model ID remains `glm52`, session shorthand remains `cg:glm52`, and
  large-profile/1M/882K compiler policy remains stable;
- golden diff is exactly the two value changes above; and
- no composition, record, scope, provider, credential, or live config changes
  occur during offline validation.

A future test count will differ from the historical 1,683. Record the current
count; do not weaken gates merely to reproduce the old number.

## 8. One-call live acceptance gate

Stop after offline review and ask for explicit approval for **one** real Alibaba
Token Plan request. The approval request must state the exact endpoint, model,
maximum tokens, streaming/tool shape, and that there will be no retry or
fallback.

Use the existing Team Token Plan bearer secret by environment reference. Never
print it, pass it on the command line except inside the authorization header,
run with shell tracing, or persist it in evidence. The exact request body is:

```json
{
  "model": "glm-5.3",
  "max_tokens": 512,
  "stream": true,
  "reasoning_effort": "max",
  "messages": [
    {
      "role": "user",
      "content": "Call glm53_acceptance_marker exactly once with marker glm53-token-plan-ok. Do not answer in prose before calling the tool."
    }
  ],
  "tools": [
    {
      "name": "glm53_acceptance_marker",
      "description": "Marks the single GLM-5.3 Token Plan acceptance call.",
      "input_schema": {
        "type": "object",
        "properties": {
          "marker": {"type": "string"}
        },
        "required": ["marker"],
        "additionalProperties": false
      }
    }
  ]
}
```

Send it once to:

```text
POST https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1/messages
Authorization: Bearer <QWEN_CLAUDE_API_KEY by environment reference>
anthropic-version: 2023-06-01
content-type: application/json
```

After the operator has loaded `QWEN_CLAUDE_API_KEY` through the normal secret
environment mechanism and approved this exact call, the reproducible one-call
command is:

```bash
set +x
: "${QWEN_CLAUDE_API_KEY:?load the standard Qwen Token Plan secret first}"
payload=$(mktemp)
headers=$(mktemp)
events=$(mktemp)
trap 'rm -f "$payload" "$headers" "$events"; unset QWEN_CLAUDE_API_KEY' EXIT

python3 - "$payload" <<'PY'
import json
import sys

payload = {
    "model": "glm-5.3",
    "max_tokens": 512,
    "stream": True,
    "reasoning_effort": "max",
    "messages": [{
        "role": "user",
        "content": (
            "Call glm53_acceptance_marker exactly once with marker "
            "glm53-token-plan-ok. Do not answer in prose before calling the tool."
        ),
    }],
    "tools": [{
        "name": "glm53_acceptance_marker",
        "description": "Marks the single GLM-5.3 Token Plan acceptance call.",
        "input_schema": {
            "type": "object",
            "properties": {"marker": {"type": "string"}},
            "required": ["marker"],
            "additionalProperties": False,
        },
    }],
}
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(payload, stream, separators=(",", ":"))
PY

status=$(curl --retry 0 -sS -N \
  -D "$headers" \
  -o "$events" \
  -w '%{http_code}' \
  -H "Authorization: Bearer $QWEN_CLAUDE_API_KEY" \
  -H 'anthropic-version: 2023-06-01' \
  -H 'content-type: application/json' \
  --data-binary @"$payload" \
  https://token-plan.ap-southeast-1.maas.aliyuncs.com/apps/anthropic/v1/messages)
printf 'http=%s\n' "$status"

# Inspect $headers/$events once against the criteria below, record only a
# redacted summary, then exit the shell so the trap removes all temporary data.
```

There is deliberately no `tool_choice`, retry option, fallback model, alternate
route, or thinking-disable field. The command uses `--retry 0`, keeps `set +x`,
refuses an absent secret, sends exactly one request, and removes temporary
payload/header/event files on shell exit.

Pass only if all of these hold in the one response:

1. HTTP 200 and valid `text/event-stream` framing.
2. `message_start.message.model` identifies exact `glm-5.3`; no event, header,
   or result identifies GLM-5.2 or another provider/model.
3. A thinking/reasoning content block is present and well-ordered.
4. Max effort is accepted without parameter rejection.
5. Exactly one valid `tool_use` block names `glm53_acceptance_marker` and its
   parsed input is exactly `{"marker":"glm53-token-plan-ok"}`.
6. Stream ordering is valid (`message_start`, content block start/deltas/stop,
   message delta, `message_stop`) and the stop reason is appropriate for tool
   use.
7. No fallback, retry, provider substitution, second upstream request, or
   hidden endpoint change occurred.

Any failure blocks activation. Record the redacted status/model/event/tool
criteria in `STATUS.md`; do not claim context/output-limit validation from this
small call. A retry or altered payload is another provider call and needs new
explicit approval.

## 9. Zero-live GLM census before cutover

The stable alias must never change models underneath a running conversation.
Immediately before activation, require zero active/background/mid-turn managed
records that use GLM as an ordinary model, lead, or enabled variant.

This metadata-only script reads launcher records, daemon socket names, and only
the `CLAUDE_MULTI_MANAGED_ID` entry from same-user process environments. It does
not open transcripts or print command lines/environment values:

```bash
python3 - <<'PY'
import json
import os
from pathlib import Path

home = Path.home()
records_dir = home / ".local/state/claude-multi/sessions"
daemon_root = Path(f"/tmp/cc-daemon-{os.getuid()}")

prefixes = {
    p.name[:-5]
    for p in daemon_root.glob("*/pty/*.sock")
    if p.name.endswith(".sock")
}

attached = set()
for env_path in Path("/proc").glob("[0-9]*/environ"):
    try:
        fields = env_path.read_bytes().split(b"\0")
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        continue
    for field in fields:
        if field.startswith(b"CLAUDE_MULTI_MANAGED_ID="):
            attached.add(field.split(b"=", 1)[1].decode("ascii", "strict"))

blocked = []
errors = []
for path in sorted(records_dir.glob("*.json")):
    try:
        record = json.loads(path.read_text())
        managed = record.get("managed_id", record.get("session_id", path.stem))
        ids = [managed, record.get("runtime_session_id")]
        ids += [a.get("session_id") for a in record.get("runtime_aliases", [])]
        daemon_live = any(
            isinstance(value, str) and value.startswith(prefix)
            for value in ids
            for prefix in prefixes
        )
        process_live = managed in attached

        ordinary = record.get("ordinary_model") == "glm52"
        snapshot = record.get("snapshot", {})
        lead = snapshot.get("lead", {}).get("model") == "glm52"
        variant = any(v.get("model") == "glm52" for v in snapshot.get("variants", []))
        if (daemon_live or process_live) and (ordinary or lead or variant):
            blocked.append((managed, ordinary, lead, variant, daemon_live, process_live))
    except Exception as exc:
        errors.append((path.name, type(exc).__name__))

if errors:
    for name, kind in errors:
        print(f"BLOCK unreadable record {name}: {kind}")
    raise SystemExit(2)
if blocked:
    for row in blocked:
        print(
            "BLOCK GLM-bearing live record "
            f"id={row[0]} ordinary={row[1]} lead={row[2]} variant={row[3]} "
            f"background={row[4]} attached={row[5]}"
        )
    raise SystemExit(1)
print("PASS zero live GLM-bearing managed records")
PY

# Independent supported display check. Any GLM-bearing row marked ● blocks.
claude-multi sessions list
```

Also exit the current attached session if it is GLM-bearing. Any nonzero or
ambiguous result blocks activation until the process exits or the operator uses
the supported `claude-multi sessions stop <managed-id>` operation. Do not kill
processes directly, edit records, rewrite scopes, or delete state/transcripts.
Run the census again after stopping anything.

## 10. Activation and local acceptance

Activation is a separate explicit approval boundary after the live call passes,
the final diff/review is accepted, and the zero-live census is green.

Before switching, record the current source commit, launcher/catalog versions,
Home Manager generation, package paths, doctor state, and the rollback
generation. Then:

```bash
repo=/home/kotur/personal/nixos-dotfiles
product="$repo/home-manager/claude-multi"

# Re-run section 9 immediately before this command.
home-manager switch --flake "$repo#kotur"

claude-multi --version
home-manager generations
systemctl --user is-active cli-proxy-api
curl -sS -o /dev/null -w 'health: %{http_code}\n' \
  http://127.0.0.1:8317/healthz

set +x
TOKEN=$(<"$HOME/.config/claude-multi/api-key")
curl -sS -o /dev/null -w 'bearer models: %{http_code}\n' \
  -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8317/v1/models
curl -sS -o /dev/null -w 'x-api-key models: %{http_code}\n' \
  -H "x-api-key: $TOKEN" \
  http://127.0.0.1:8317/v1/models

curl -sS -H "Authorization: Bearer $TOKEN" \
  http://127.0.0.1:8317/v1/models \
  | python3 -c 'import json,sys; ids={m["id"] for m in json.load(sys.stdin)["data"]}; expected="claude-multi-glm52-max"; assert expected in ids; assert "glm-5.2" not in ids and "glm-5.3" not in ids; print("PASS stable GLM alias served, raw wires hidden")'
unset TOKEN

cd "$product"
PYTHONPATH=src python3 - <<'PY'
from pathlib import Path
from claude_multi import catalog, render

bundle = catalog.load_catalog(Path("catalog"))
model = bundle.docs["models"]["models"]["glm52"]
assert model["wire_model"] == "glm-5.3"
assert model["display"] == "GLM-5.3"
assert model["context"]["declared_tokens"] == 1_000_000
assert model["context"]["client_tokens"] == 1_000_000
assert model["context"]["provider_tokens"] == 1_000_000
assert model["context"]["validated_tokens"] == 200_000
assert model["default_lane"] == "max"

result = render.render_config(
    bundle.docs["gateway"],
    bundle.docs["providers"],
    bundle.docs["models"],
    home=Path.home(),
    gateway_token="<token>",
    resolve_secret=lambda _name: "<secret>",
)
text = result.yaml
assert 'name: "glm-5.3"' in text
assert 'alias: "claude-multi-glm52-max"' in text
assert 'display-name: "GLM-5.3"' in text
assert 'name: "glm-5.2"' not in text
print("PASS catalog/render GLM-5.3 contract")
PY

# Doctor verifies installed/rendered config parity and served aliases.
claude-multi doctor

# Only now converge catalog-derived snapshots/scopes; never transcripts.
claude-multi doctor --repair-all
claude-multi doctor
```

Expected local result:

- gateway active; loopback health 200;
- both local model-list auth forms 200;
- served stable alias `claude-multi-glm52-max` present;
- raw provider wires absent from `/v1/models` as designed;
- catalog/render exact wire `glm-5.3`, display `GLM-5.3`, unchanged max lane,
  1M contract, and 200K evidence floor;
- no config drift or missing aliases in doctor;
- `doctor --repair-all` refreshes only catalog-derived managed snapshots/scopes;
- final doctor Ready; and
- no additional provider request. Wire-name remaps are not visible through
  local `/v1/models`; catalog/render assertions plus doctor's byte-drift check
  are the local proof.

## 11. Post-activation checkpoint

Create the next checkpoint immediately after successful activation, for example
`checkpoints/<YYYY-MM-DD>-v<launcher>-catalog<catalog>/`, following
[the checkpoint convention](../../checkpoints/README.md). Record:

- source branch and commit, catalog/launcher versions, and exact reviewed diff;
- trigger URL or redacted console evidence and verification date;
- offline focused/full test counts and package/sandbox/activation-package
  results;
- the one approved call's redacted HTTP/model/thinking/tool/stream criteria;
- the zero-live census before activation;
- prior/current Home Manager generations and package/store paths;
- health, both local model-list auth forms, served alias, render assertions,
  repair-all result, and final doctor state;
- whether any post-activation request has used the stable GLM alias; and
- the semantic rollback rule below.

Update `STATUS.md`, `HANDOFF.md`, D61, issue 027, `AGENTS.md`, `USAGE.md`, the
issues index, latest routing wikis, and `gateway-ops` with compact links/status.
Do not copy this full procedure elsewhere; this file remains canonical.

## 12. Semantic rollback boundary

### Before the first post-activation request through the stable GLM alias

The prior Home Manager generation is a clean rollback anchor. Re-run the
zero-live census, activate the recorded pre-promotion generation, and let Home
Manager restart the gateway. No composition restoration, record rewrite, scope
surgery, credential change, or state/transcript deletion is needed: internal
identity `glm52` exists on both sides.

The pre-activation direct acceptance canary does not remap an existing managed
conversation. The semantic boundary begins when the activated stable alias has
served a GLM-5.3 request.

### After any post-activation GLM-5.3 use

Rollback becomes semantically sensitive: the same durable `glm52` identity
would resolve to GLM-5.2 after rollback. Therefore:

1. stop or exit every GLM-bearing attached/background session through supported
   lifecycle operations;
2. rerun the zero-live census and require PASS;
3. prefer fixing the GLM-5.3 route forward when safe;
4. if rollback is still required, explicitly record that future resumes of
   `glm52` records will use GLM-5.2, activate the prior generation, verify the
   gateway/doctor, and communicate the semantic downgrade; and
5. never delete or rewrite records, scopes, compositions, credentials, state,
   or transcripts to make rollback look clean.

There is no automatic or hidden fallback in either direction.

## 13. Sources and drift checks

These URLs are evidence entry points, not timeless truth. Re-open them at the
future trigger and record access dates:

- [Z.ai GLM-5.3 model guide](https://docs.z.ai/guides/llm/glm-5.3) — reverify
  exact ID, 1M context, 128K output, always-on reasoning, effort values/default,
  Anthropic Messages support, tools, streaming, and current endpoint/account
  caveats.
- [Alibaba Token Plan Team overview](https://www.alibabacloud.com/help/en/model-studio/token-plan-team-overview)
  — reverify Team product, region, route/protocol, key type, and exact supported
  GLM IDs.
- [Qianwen Team Token Plan exact allowlist](https://platform.qianwenai.com/docs/token-plan/team/token-plan-team-overview)
  — this or the authenticated console must explicitly name `glm-5.3`; reverify
  exact-string/version-compatibility language.
- [Alibaba Token Plan FAQ](https://help.aliyun.com/zh/model-studio/token-plan-faq)
  — reverify product/key/base-URL separation, entitlement rules, and whether a
  supported model-list endpoint has been added.
- [Qianwen model changelog](https://platform.qianwenai.com/docs/changelog/models)
  — reverify rollout date/scope; a changelog entry alone is not entitlement.
- [Alibaba/Bailian model catalog](https://help.aliyun.com/zh/model-studio/models)
  — reverify any `ZHIPU/GLM-5.3` pay-as-you-go listing, region, protocol, and
  billing; never treat it as Token Plan support.
- [Zhipu GLM Coding Plan](https://docs.bigmodel.cn/cn/coding-plan/overview) —
  reverify its current GLM-5.3 support and separate endpoint/key/quota; never
  substitute it under the Alibaba Token Plan provider.

If sources disagree, the exact Team Token Plan allowlist or authenticated
console for the existing subscription wins the preflight decision. Ambiguity
means remain on GLM-5.2 and keep this issue blocked.
