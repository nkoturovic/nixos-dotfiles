"""Composition loading and deterministic resolution for claude-multi v2.

Resolves a validated composition against the trusted catalog into an immutable
resolved form: exactly one lead, ordered variants with deterministic IDs,
default lanes, preferred markers, availability, and the process-wide scalar
context (minimum ``validated_tokens`` across selected scalar-kind models only).
"""

from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class ResolvedComposition:
    name: str
    description: str
    lead: ResolvedLead
    variants: tuple[ResolvedVariant, ...]
    native_agents: dict[str, str]
    availability: dict[str, Any]
    scalar_context_tokens: int | None


def variant_id(role: str, model: str, lane: str) -> str:
    """Deterministic generated agent ID from the (role, model, lane) triple."""

    return f"{role}-{model}-{lane}"


def compute_scalar(selected_models: list[dict[str, Any]]) -> int | None:
    """Minimum validated_tokens across selected scalar-kind models.

    Selector-1m models are excluded and ``declared_tokens`` is never used;
    returns None when no selected model is scalar-classified.
    """

    values = [
        model["context"]["validated_tokens"]
        for model in selected_models
        if model["context"]["kind"] == "scalar"
    ]
    return min(values) if values else None


def load_composition_file(
    path: Path | str, schema: dict[str, Any]
) -> dict[str, Any]:
    """Strictly load and schema-validate a composition document."""

    from . import validate as schema_validate

    document = strict_json.load(path)
    problems = schema_validate.validate(document, schema, "$")
    if problems:
        raise CompositionError("; ".join(problems))
    if document.get("version") != catalog_mod.SUPPORTED_DATA_VERSION:
        raise CompositionError(
            f"unsupported composition version {document.get('version')!r}"
        )
    return document


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
    for slot in composition["slots"]:
        role_id = slot["role"]
        model_id = slot["model"]
        model = models[model_id]
        provider = providers[model["provider"]]
        if role_id == LEAD_ID:
            lead_block = model["lead"]
            lead = ResolvedLead(
                model=model_id,
                client_selector=model["client_selector"],
                effort=lead_block["effort"],
                env=dict(lead_block["env"]),
                display=model["display"],
                family=provider["independence_family"],
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
    )


def snapshot(resolved: ResolvedComposition) -> dict[str, Any]:
    """Resolved composition semantics for session snapshots (no secrets)."""

    return {
        "lead": {
            "model": resolved.lead.model,
            "client_selector": resolved.lead.client_selector,
            "effort": resolved.lead.effort,
            "env": dict(resolved.lead.env),
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
    }
