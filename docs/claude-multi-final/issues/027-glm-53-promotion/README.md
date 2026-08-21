# 027 — GLM-5.2 → GLM-5.3 promotion (stable identity, catalog21)

**Status: in release** · committed on `feature/term-only`, catalog21;
live route acceptance and activation remain separately approval-gated.
Design rationale: [blueprints/027-glm-53-promotion](../../blueprints/027-glm-53-promotion/),
decision [D61](../../DECISIONS.md).

## Report

GLM-5.3 supersedes GLM-5.2 on the existing Qwen Token Plan route; the
user attests their Alibaba subscription exposes it. The Alibaba public
allowlists fetched 2026-08-21 still stop at GLM-5.2 and lag the release,
so this change ships the source migration from official Z.ai contract
facts + user-attested availability, while the live route acceptance stays
a separate approval gate before activation.

## Change

One trusted catalog entry promoted in place:

- wire `glm-5.2` → `glm-5.3`; display `GLM-5.2` → `GLM-5.3`
- declared context 1,048,576 → exact official 1,000,000
- qualification/routing evidence rewritten with official Z.ai GLM-5.3
  facts, user-attested availability, the stale-allowlist caveat, and the
  pending live-acceptance boundary
- unchanged: catalog key `glm52`, selector `claude-multi-glm52-max[1m]`,
  gateway alias `claude-multi-glm52-max`, provider `qwen`, 1M
  client/provider context, 200K validated floor, `large` profile, single
  `max` lane, `reasoning-effort-max`, roles, family, minimum-tested
  versions
- no second GLM model, no schema/provider/endpoint/secret/payload change
- catalog 20 → 21; launcher stays 2.20.0
- routing position: GLM-5.3 is a peer of Qwen3.8 Max and GPT-5.6 Sol;
  identical GLM/Qwen role hints and existing preferred flags mean no
  composition reorder or prompt expansion
- no composition or state migration; D45, historical docs, checkpoints,
  and catalog18 rollback fixtures keep saying GLM-5.2.

## Verification

- Catalog/render/compiler/context/CLI suites green; the disposable
  loopback Qwen route proves bearer auth, alias → exact `glm-5.3` wire,
  max effort, tool preservation, no invented `tool_choice`, and D60
  retention stripping with zero Alibaba contact.
- Render golden delta is exactly the two intended route lines; compiler/
  scope goldens byte-identical.
- Full Python discovery: 1,683 tests OK (2 skips); package, sandbox, and
  fresh HM activation-package builds green. One known PTY timeout passed in
  isolation and the full rerun was clean. Independent Sol-xhigh review:
  APPROVE after staged/live wording and zero-live cutover gate fixes.

## Activation (separate approval gates)

1. Bounded live Alibaba acceptance call (`glm-5.3`, `reasoning_effort:
   max`, streaming, marker tool, no forced `tool_choice`); failure blocks
   activation, no GLM-5.2 fallback.
2. Metadata-only liveness census must return zero live/mid-turn GLM ordinary
   sessions, GLM leads, or managed sessions with enabled GLM variants; any
   nonzero result aborts activation until the process exits or is stopped
   safely. Then Home Manager activation → gateway re-render/restart →
   health/config parity/exact wire/stable alias checks → `doctor --repair-all`
   → catalog21 checkpoint evidence.

## Rollback

Pre-use: generation 130 restores GLM-5.2; no composition restoration.
Post-use: semantically sensitive (stable `glm52` would map back to
GLM-5.2) — stop GLM-bearing sessions, prefer fixing forward. Never
delete or rewrite records, scopes, credentials, compositions, or
transcripts.
