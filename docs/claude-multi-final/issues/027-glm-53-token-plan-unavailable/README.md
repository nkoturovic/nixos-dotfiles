# 027 — GLM-5.3 is not available on Alibaba Token Plan

**Status: blocked upstream** · active route remains GLM-5.2 (D61)

## Report

The operator expected GLM-5.3 to replace GLM-5.2 in their Alibaba/Qwen Token
Plan subscription. A complete catalog21 candidate was built and reviewed using
the safest in-place promotion shape: stable internal identity `glm52` and
selector `claude-multi-glm52-max[1m]`, new wire/display `glm-5.3`/GLM-5.3.

One separately approved bounded call then exercised that exact candidate through
a disposable locally rendered gateway:

- existing Team Edition Singapore Token Plan Anthropic endpoint
- bearer credential from the normal Qwen secret reference
- stable alias force-mapped to exact wire `glm-5.3`
- `reasoning_effort: max`, streaming, one offered marker tool
- no forced `tool_choice`, no fallback, one provider request only.

Alibaba returned HTTP 400:

```text
InvalidParameter: Model not exist.
```

No Home Manager activation, live gateway change, record/scope migration, or
transcript access occurred. Commit `caaa641` contained the candidate; commit
`8db80c3` reverted it after the failed canary. Live/source catalog20 therefore
continues to serve GLM-5.2 correctly.

## Root cause

This is an exact allowlist/product-boundary rejection, not a local renderer,
authentication, context, effort, or fallback defect.

Official Alibaba Token Plan documentation uses exact-string allowlists and
currently lists:

- Team Edition GLM models: `glm-5.2`, `glm-5.1`, `glm-5`
- Personal Edition GLM model: `glm-5.2`

It explicitly forbids version-compatibility inference. There is no Token Plan
alias, region switch, feature flag, or published rollout date for GLM-5.3.

Alibaba does offer GLM-5.3 through a **different product**:

- model `ZHIPU/GLM-5.3`
- pay-as-you-go third-party direct supply
- Beijing workspace endpoint
- OpenAI-compatible protocol only
- separate workspace, credential, region, and billing.

Zhipu's own GLM Coding Plan also supports `glm-5.3`, but it is not Alibaba
Token Plan. Neither route may be silently substituted under D3.

## Decision

- Keep active `glm52` → wire `glm-5.2`, display GLM-5.2.
- Do not guess `glm-5.3`, `ZHIPU/GLM-5.3`, moving aliases, alternate regions,
  or endpoints on the current Token Plan credential.
- Do not retain the unusable catalog21 candidate in source.
- When Alibaba's exact Team Token Plan allowlist includes `glm-5.3`, repeat the
  already-designed in-place promotion: stable identity/selector, wire/display
  flip, official 1M contract, offline route tests, one approved canary, then
  activation.
- At that future promotion GLM-5.3 should be positioned as a peer of Qwen3.8
  Max and GPT-5.6 Sol; explicit preferred flags remain authoritative.

## Evidence boundary

The failed call establishes that this account/endpoint rejected the exact
candidate at the model allowlist/entitlement stage. It does not validate
GLM-5.3 thinking, tools, streaming, max effort, context, or output limits on
Alibaba because the request never reached model execution.

## Sources

- [Alibaba Token Plan Team overview](https://www.alibabacloud.com/help/en/model-studio/token-plan-team-overview)
- [Qianwen Token Plan exact allowlist](https://platform.qianwenai.com/docs/token-plan/team/token-plan-team-overview)
- [Alibaba Token Plan FAQ](https://help.aliyun.com/zh/model-studio/token-plan-faq)
- [Qianwen model changelog](https://platform.qianwenai.com/docs/changelog/models)
- [Alibaba/Bailian model catalog](https://help.aliyun.com/zh/model-studio/models)
- [Zhipu GLM Coding Plan](https://docs.bigmodel.cn/cn/coding-plan/overview)
