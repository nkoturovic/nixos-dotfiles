"""Composition loading and deterministic resolution for claude-multi v2.

Resolves a validated composition against the trusted catalog into an immutable
resolved form: exactly one lead, ordered variants with deterministic IDs,
default lanes, preferred markers, availability, and the process-wide scalar
context (minimum explicit ``scalar_tokens`` across selected models).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from . import catalog as catalog_mod
from . import strict_json


class CompositionError(ValueError):
    """Raised when a composition cannot be loaded or resolved."""


LEAD_ID = "cm-lead"


@dataclass(frozen=True)
class ResolvedVariant:
    id: str
    role: str
    model: str
    lane: str
    client_selector: str
    agent_effort: str
    proxy_effort_contract: str | None
    preferred: bool
    routing_hint: str | None
    isolation: str | None
    display: str
    family: str


@dataclass(frozen=True)
class ResolvedLead:
    model: str
    client_selector: str
    effort: str
    env: dict[str, str]
    display: str
    family: str
    client_context_tokens: int
    provider_context_tokens: int
    provider_context_validated: bool
    auto_compact_tokens: int


WORKFLOWS_VOCABULARY = ("native", "off")
AUTO_COMPACT_PERCENT = 90
AUTO_COMPACT_OUTPUT_RESERVE = 20_000
AUTO_COMPACT_REACTIVE_HEADROOM = 13_000
# D63 operating default: 1M-class routes are capped to an 800K operating
# window (reactive trigger 702K via auto_compact_trigger). This is a local
# operating policy, not a route-capability claim: catalog client/provider/
# declared/validated evidence fields stay untouched, so smaller route bounds
# still narrow the window further and future/custom models are covered.
OPERATING_WINDOW_CEILING = 800_000


def operating_window(window_tokens: int) -> int:
    """Clamp a derived operating window to the D63 ceiling (idempotent)."""

    return min(window_tokens, OPERATING_WINDOW_CEILING)


@dataclass(frozen=True)
class ResolvedComposition:
    name: str
    description: str
    lead: ResolvedLead
    variants: tuple[ResolvedVariant, ...]
    native_agents: dict[str, str]
    availability: dict[str, Any]
    scalar_context_tokens: int | None
    auto_compact_window_tokens: int
    workflows: str = "native"


def variant_id(role: str, model: str, lane: str) -> str:
    """Deterministic generated agent ID from the (role, model, lane) triple."""

    return f"{role}-{model}-{lane}"


def compute_scalar(selected_models: list[dict[str, Any]]) -> int | None:
    """Minimum explicit process scalar across selected models (D63-capped)."""

    values = [
        model["context"]["scalar_tokens"]
        for model in selected_models
        if model["context"]["scalar_tokens"] is not None
    ]
    return operating_window(min(values)) if values else None


def auto_compact_trigger(window_tokens: int) -> int:
    """Pinned-client reactive compact threshold for one process capacity.

    Claude Code 2.1.218 reserves up to 20K output tokens and applies the
    percentage to the remaining prompt budget. Proactive preparation is not
    returned here because its experiment-controlled fraction can vary at
    runtime; the reactive override is deterministic.
    """

    if window_tokens <= AUTO_COMPACT_OUTPUT_RESERVE + AUTO_COMPACT_REACTIVE_HEADROOM:
        raise CompositionError(
            "auto-compaction capacity is too small for the pinned client policy"
        )
    prompt_budget = window_tokens - AUTO_COMPACT_OUTPUT_RESERVE
    return min(
        prompt_budget * AUTO_COMPACT_PERCENT // 100,
        prompt_budget - AUTO_COMPACT_REACTIVE_HEADROOM,
    )


def compute_auto_compact_capacity(
    lead_model: dict[str, Any], selected_models: list[dict[str, Any]]
) -> int:
    """Safe process capacity without needlessly shrinking scalar models.

    The environment value is process-wide. A model whose provider bound equals
    its client classification is already protected by Claude Code's per-model
    cap. Extended selectors such as Qwen can advertise a larger client window
    than the provider accepts, so those stricter provider bounds narrow the
    shared capacity for every thread in the process.
    """

    capacity = lead_model["context"]["provider_tokens"]
    for model in selected_models:
        context = model["context"]
        if context["provider_tokens"] < context["client_tokens"]:
            capacity = min(capacity, context["provider_tokens"])
    return operating_window(capacity)


def _lead_context_policy(model: dict[str, Any]) -> tuple[int, int, int]:
    """Claude-client context, provider bound, and initial reactive trigger.

    The trigger is provisional (resolve() recomputes it from the shared
    D63-capped capacity); the returned client/provider values stay raw
    catalog evidence.
    """

    context = model["context"]
    client_tokens = context["client_tokens"]
    provider_tokens = context["provider_tokens"]
    return (
        client_tokens,
        provider_tokens,
        auto_compact_trigger(operating_window(provider_tokens)),
    )


def validate_document(
    document: dict[str, Any], schema: dict[str, Any]
) -> dict[str, Any]:
    """Schema + version validation shared by file and stdin ingestion."""

    from . import validate as schema_validate

    problems = schema_validate.validate(document, schema, "$")
    if problems:
        raise CompositionError("; ".join(problems))
    if document.get("version") != catalog_mod.SUPPORTED_DATA_VERSION:
        raise CompositionError(
            f"unsupported composition version {document.get('version')!r}"
        )
    return document


def load_composition_file(
    path: Path | str, schema: dict[str, Any]
) -> dict[str, Any]:
    """Strictly load and schema-validate a composition document."""

    return validate_document(strict_json.load(path), schema)


def resolve(docs: dict[str, Any], composition: dict[str, Any]) -> ResolvedComposition:
    """Validate and resolve a composition against trusted catalog documents."""

    models = docs["models"]["models"]
    roles = docs["roles"]["roles"]
    providers = docs["providers"]["providers"]

    problems = catalog_mod.validate_composition(composition, models, roles, providers)
    if problems:
        raise CompositionError("; ".join(problems))

    lead: ResolvedLead | None = None
    variants: list[ResolvedVariant] = []
    workflows = composition.get("workflows", "native")
    for slot in composition["slots"]:
        role_id = slot["role"]
        model_id = slot["model"]
        model = models[model_id]
        provider = providers[model["provider"]]
        if role_id == LEAD_ID:
            lead_block = model["lead"]
            lead_effort = lead_block["effort"]
            if workflows == "off" and lead_effort == "ultracode":
                # D8 compile-time derivation (composition level only): with
                # native workflows off, lead ultracode maps to xhigh. The
                # derived value is what the snapshot records.
                lead_effort = "xhigh"
            client_context, provider_context, compact_trigger = _lead_context_policy(
                model
            )
            lead = ResolvedLead(
                model=model_id,
                client_selector=model["client_selector"],
                effort=lead_effort,
                env=dict(lead_block["env"]),
                display=model["display"],
                family=provider["independence_family"],
                client_context_tokens=client_context,
                provider_context_tokens=provider_context,
                provider_context_validated=(
                    model["context"]["validated_tokens"] >= provider_context
                ),
                auto_compact_tokens=compact_trigger,
            )
            continue
        lane_id = slot.get("lane", model["default_lane"])
        lane = model["lanes"][lane_id]
        variants.append(
            ResolvedVariant(
                id=variant_id(role_id, model_id, lane_id),
                role=role_id,
                model=model_id,
                lane=lane_id,
                client_selector=lane["client_selector"],
                agent_effort=lane["agent_effort"],
                proxy_effort_contract=lane["proxy_effort_contract"],
                preferred=bool(slot.get("preferred", False)),
                routing_hint=model["role_hints"].get(role_id),
                isolation=roles[role_id]["isolation"],
                display=model["display"],
                family=provider["independence_family"],
            )
        )

    if lead is None:  # validate_composition guarantees this is unreachable
        raise CompositionError("composition has no lead slot")

    selected = [models[lead.model]] + [models[v.model] for v in variants]
    auto_compact_window = compute_auto_compact_capacity(
        models[lead.model], selected
    )
    lead = replace(
        lead, auto_compact_tokens=auto_compact_trigger(auto_compact_window)
    )
    return ResolvedComposition(
        name=composition["name"],
        description=composition.get("description", ""),
        lead=lead,
        variants=tuple(variants),
        native_agents=dict(composition["native_agents"]),
        availability={
            "providers": dict(composition["availability"]["providers"]),
            "models": dict(composition["availability"]["models"]),
        },
        scalar_context_tokens=compute_scalar(selected),
        auto_compact_window_tokens=auto_compact_window,
        workflows=workflows,
    )


def snapshot(resolved: ResolvedComposition) -> dict[str, Any]:
    """Resolved composition semantics for session snapshots (no secrets).

    ``workflows`` is recorded only when it differs from the default
    (``"native"``): the composition hash still covers the workflow mode both
    directions, while a native-mode snapshot stays byte-identical to the
    pre-rethink form (legacy argv parity).
    """

    document: dict[str, Any] = {
        "lead": {
            "model": resolved.lead.model,
            "client_selector": resolved.lead.client_selector,
            "effort": resolved.lead.effort,
            "env": dict(resolved.lead.env),
            "client_context_tokens": resolved.lead.client_context_tokens,
            "provider_context_tokens": resolved.lead.provider_context_tokens,
            "auto_compact_tokens": resolved.lead.auto_compact_tokens,
        },
        "variants": [
            {
                "id": variant.id,
                "role": variant.role,
                "model": variant.model,
                "lane": variant.lane,
                "client_selector": variant.client_selector,
                "preferred": variant.preferred,
            }
            for variant in resolved.variants
        ],
        "native_agents": dict(resolved.native_agents),
        "availability": {
            "providers": dict(resolved.availability["providers"]),
            "models": dict(resolved.availability["models"]),
        },
        "scalar_context_tokens": resolved.scalar_context_tokens,
        "auto_compact_window_tokens": resolved.auto_compact_window_tokens,
    }
    if resolved.workflows != "native":
        document["workflows"] = resolved.workflows
    return document
