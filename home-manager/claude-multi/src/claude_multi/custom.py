"""Custom providers & ordinary models (020): the operator's registry.

``~/.config/claude-multi/custom.json`` holds operator-added providers
(Anthropic-compatible endpoint + key env var) and ordinary-session models
— provider-listed (verified endpoints, e.g. Kimi) or manually typed.
Composition admission stays a catalog act: customs carry no lanes/roles/
qualification and never appear near a composition. Each distinct context
bound forms its own ordinary profile (group = shared /model fence + one
compaction policy, D24 mechanics).

The registry merges into the docs view consumed by the ordinary launch and
render paths only; the trusted catalog never sees it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import sessions, state, strict_json
from . import validate as schema_validate


class CustomModelsError(ValueError):
    """Raised on any custom-registry failure (fail closed)."""


def registry_path(environ: dict[str, str]) -> Path:
    return sessions.config_root(environ) / "custom.json"


def _schema() -> dict[str, Any]:
    return strict_json.load(
        Path(__file__).resolve().parents[2] / "schemas" / "custom.schema.json"
    )


def _empty() -> dict[str, Any]:
    return {"version": 1, "providers": {}, "models": {}}


def load_registry(environ: dict[str, str]) -> dict[str, Any]:
    """Load + validate the registry; an absent file means an empty one."""

    path = registry_path(environ)
    if not path.exists():
        return _empty()
    try:
        document = strict_json.loads(state.read_private(path))
    except (state.StateError, ValueError) as exc:
        raise CustomModelsError(f"cannot load the custom registry: {exc}") from exc
    if "providers" not in document:  # tolerate a models-only v1 file
        document = {"version": 1, "providers": {}, **document}
    problems = schema_validate.validate(document, _schema(), "$")
    if problems:
        raise CustomModelsError(
            f"invalid custom registry: {'; '.join(problems)}"
        )
    for key in [*document["providers"], *document["models"]]:
        state.check_name(key)
    return document


def save_registry(environ: dict[str, str], document: dict[str, Any]) -> Path:
    """Validate and atomically write the registry (0600, private dir)."""

    problems = schema_validate.validate(document, _schema(), "$")
    if problems:
        raise CustomModelsError(
            f"refusing to save an invalid registry: {'; '.join(problems)}"
        )
    for key in [*document["providers"], *document["models"]]:
        state.check_name(key)
    path = registry_path(environ)
    state.ensure_private_dir(path.parent)
    state.atomic_write(path, strict_json.pretty_file_bytes(document))
    return path


def add_provider(
    environ: dict[str, str],
    provider_id: str,
    *,
    base_url: str,
    auth_kind: str,
    secret_env: str,
    header: str | None = None,
    display: str | None = None,
    catalog_providers: tuple[str, ...] = (),
) -> None:
    state.check_name(provider_id)
    if provider_id in catalog_providers:
        raise CustomModelsError(
            f"provider {provider_id!r} exists in the trusted catalog — "
            "custom entries never shadow catalog ids"
        )
    registry = load_registry(environ)
    registry["providers"][provider_id] = {
        "base_url": base_url,
        "auth_kind": auth_kind,
        "secret_env": secret_env,
        **({"header": header} if header else {}),
        **({"display": display} if display else {}),
    }
    save_registry(environ, registry)


def add_model(
    environ: dict[str, str],
    model_id: str,
    *,
    wire_model: str,
    provider: str,
    context_tokens: int,
    display: str | None = None,
    created_via: str,
    catalog_providers: tuple[str, ...] = (),
    catalog_models: tuple[str, ...] = (),
) -> None:
    """Insert or replace one model; provider may be catalog or custom.

    The id never shadows the trusted catalog (a custom with a catalog id
    would silently override it in the merged ordinary view).
    """

    state.check_name(model_id)
    if model_id in catalog_models:
        raise CustomModelsError(
            f"model {model_id!r} exists in the trusted catalog — "
            "custom entries never shadow catalog ids"
        )
    registry = load_registry(environ)
    if provider not in registry["providers"] and provider not in catalog_providers:
        raise CustomModelsError(
            f"provider {provider!r} is neither a custom nor a catalog provider"
        )
    registry["models"][model_id] = {
        "wire_model": wire_model,
        "provider": provider,
        "context_tokens": context_tokens,
        "created_via": created_via,
        **({"display": display} if display else {}),
    }
    save_registry(environ, registry)


def remove_model(environ: dict[str, str], model_id: str) -> bool:
    registry = load_registry(environ)
    if model_id not in registry["models"]:
        return False
    del registry["models"][model_id]
    save_registry(environ, registry)
    return True


def remove_provider(environ: dict[str, str], provider_id: str) -> bool:
    registry = load_registry(environ)
    if provider_id not in registry["providers"]:
        return False
    if any(m["provider"] == provider_id for m in registry["models"].values()):
        raise CustomModelsError(
            f"provider {provider_id!r} still has custom models — remove them first"
        )
    del registry["providers"][provider_id]
    save_registry(environ, registry)
    return True


def profile_for(context_tokens: int) -> str:
    """Ordinary profile id for a custom bound (group = one fence)."""

    return f"custom-{context_tokens}"


def profile_label(profile: str) -> str:
    """Picker section label for a custom profile id."""

    if not profile.startswith("custom-"):
        return profile
    tokens = int(profile.removeprefix("custom-"))
    return f"custom · {tokens // 1024}K context (operator-declared)"


def synthetic_providers(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Catalog-provider-shaped entries for custom providers (render input).

    Direct Anthropic-compatible transport only (v1); no payload contracts
    (no effort overrides), no passthrough routes, family 'custom'.
    """

    providers: dict[str, dict[str, Any]] = {}
    for provider_id in sorted(registry.get("providers", {})):
        spec = registry["providers"][provider_id]
        auth: dict[str, Any] = {
            "kind": spec["auth_kind"],
            "secret_ref": f"env:{spec['secret_env']}",
        }
        if spec["auth_kind"] == "header":
            auth["header"] = spec.get("header", "x-api-key")
        providers[provider_id] = {
            "id": provider_id,
            "display": spec.get("display", provider_id),
            "independence_family": "custom",
            "support": "operator-custom",
            "support_note": "Operator-added provider (custom.json); ordinary sessions only.",
            "adapter": "cliproxy-claude-compatible-v1",
            "transport": {
                "kind": "direct",
                "base_url": spec["base_url"],
                "auth": auth,
            },
            "passthrough_routes": [],
            "payload_contracts": [],
        }
    return providers


def synthetic_entries(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Catalog-model-shaped entries for custom models (render/compile input).

    The shape satisfies every field the renderer and the ordinary compiler
    path read; customs are ordinary-only by construction (no roles, one
    custom lane, profile = their bound).
    """

    entries: dict[str, dict[str, Any]] = {}
    for model_id in sorted(registry.get("models", {})):
        spec = registry["models"][model_id]
        context = spec["context_tokens"]
        selector = f"custom-{model_id}"
        entries[model_id] = {
            "provider": spec["provider"],
            "display": spec.get("display", model_id),
            "capabilities": ["lead"],
            "compatible_roles": [],
            "client_selector": selector,
            "wire_model": spec["wire_model"],
            "context": {
                "client_tokens": context,
                "declared_tokens": context,
                "provider_tokens": context,
                "scalar_tokens": None,
                "ordinary_profile": profile_for(context),
                "qualification": "operator-declared custom ordinary model",
                "validated_tokens": context,
            },
            "default_lane": "custom",
            "lanes": {
                "custom": {
                    "agent_effort": "high",
                    "client_selector": selector,
                    "proxy_effort_contract": None,
                }
            },
            "lead": {"effort": "high", "env": {}},
            "routing_note": "Custom ordinary model (operator-added).",
            "role_hints": {},
            "minimum_tested": {"claude_code": "2.1.216", "cliproxyapi": "7.2.80"},
        }
    return entries


def merge_docs(docs: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    """Docs view with custom providers + models merged in (ordinary/render)."""

    providers = synthetic_providers(registry)
    entries = synthetic_entries(registry)
    if not providers and not entries:
        return docs
    merged = dict(docs)
    if providers:
        providers_doc = dict(docs["providers"])
        providers_doc["providers"] = {**docs["providers"]["providers"], **providers}
        merged["providers"] = providers_doc
    if entries:
        models_doc = dict(docs["models"])
        models_doc["models"] = {**docs["models"]["models"], **entries}
        merged["models"] = models_doc
    return merged
