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
from typing import Any, Callable

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


def _mutate(environ: dict[str, str], mutate: Callable[[dict[str, Any]], None]) -> None:
    """Locked load-modify-save (FileLock covers the whole transaction)."""

    path = registry_path(environ)
    state.ensure_private_dir(path.parent)
    lock = state.FileLock(path)
    lock.acquire(blocking=True)
    try:
        registry = load_registry(environ)
        mutate(registry)
        save_registry(environ, registry)
    finally:
        lock.release()


def add_provider(
    environ: dict[str, str],
    provider_id: str,
    *,
    base_url: str,
    auth_kind: str,
    secret_env: str,
    header: str | None = None,
    display: str | None = None,
    catalog_providers: tuple[str, ...] | dict[str, Any] = (),
) -> None:
    state.check_name(provider_id)
    if provider_id in catalog_providers:
        raise CustomModelsError(
            f"provider {provider_id!r} exists in the trusted catalog — "
            "custom entries never shadow catalog ids"
        )
    if auth_kind == "header" and (header or "x-api-key").lower() != "x-api-key":
        # The pinned gateway build honors ONLY x-api-key header auth (the
        # kimi compat patch); anything else silently falls back to Bearer
        # and 401s upstream with every local radar green.
        raise CustomModelsError(
            "this gateway build honors only the x-api-key header for header "
            f"auth — {header!r} would silently fall back to Bearer upstream"
        )

    def _apply(registry: dict[str, Any]) -> None:
        registry["providers"][provider_id] = {
            "base_url": base_url,
            "auth_kind": auth_kind,
            "secret_env": secret_env,
            **({"header": header} if header else {}),
            **({"display": display} if display else {}),
        }

    _mutate(environ, _apply)


def add_model(
    environ: dict[str, str],
    model_id: str,
    *,
    wire_model: str,
    provider: str,
    context_tokens: int,
    display: str | None = None,
    created_via: str,
    catalog_providers: dict[str, Any] | None = None,
    catalog_models: tuple[str, ...] = (),
) -> None:
    """Insert or replace one model; provider may be catalog or custom.

    The id never shadows the trusted catalog (a custom with a catalog id
    would silently override it in the merged ordinary view).
    """

    catalog_providers = {} if catalog_providers is None else catalog_providers
    state.check_name(model_id)
    if model_id in catalog_models:
        raise CustomModelsError(
            f"model {model_id!r} exists in the trusted catalog — "
            "custom entries never shadow catalog ids"
        )
    if provider in catalog_providers:
        # Catalog-direct providers (kimi/qwen) are fine; OAuth pools are
        # not: a pool-backed custom alias renders past catalog admission
        # and fails only upstream (gateway-lane finding).
        kind = catalog_providers[provider]["transport"]["kind"]
        if kind == "oauth-pool":
            raise CustomModelsError(
                f"provider {provider!r} is an OAuth pool — custom models "
                "need a direct (key-based) provider; pool aliases are "
                "catalog admission's domain"
            )

    def _apply(registry: dict[str, Any]) -> None:
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

    _mutate(environ, _apply)


def remove_model(environ: dict[str, str], model_id: str) -> bool:
    outcome = {"removed": False}

    def _apply(registry: dict[str, Any]) -> None:
        if model_id in registry["models"]:
            del registry["models"][model_id]
            outcome["removed"] = True

    _mutate(environ, _apply)
    return outcome["removed"]


def remove_provider(environ: dict[str, str], provider_id: str) -> bool:
    outcome = {"removed": False}

    def _apply(registry: dict[str, Any]) -> None:
        if provider_id not in registry["providers"]:
            return
        if any(m["provider"] == provider_id for m in registry["models"].values()):
            raise CustomModelsError(
                f"provider {provider_id!r} still has custom models — remove them first"
            )
        del registry["providers"][provider_id]
        outcome["removed"] = True

    _mutate(environ, _apply)
    return outcome["removed"]


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


def merge_conflicts(docs: dict[str, Any], registry: dict[str, Any]) -> list[str]:
    """Custom ids that collide with the trusted catalog (never applied).

    Add-time guards refuse these, but a hand-edited or outdated registry
    can still contain them — the merge drops them loudly (doctor surfaces
    the list) instead of silently re-routing traffic and secrets.
    """

    catalog_providers = docs["providers"]["providers"]
    catalog_models = docs["models"]["models"]
    return sorted(
        [
            f"provider {provider_id}"
            for provider_id in registry.get("providers", {})
            if provider_id in catalog_providers
        ]
        + [
            f"model {model_id}"
            for model_id in registry.get("models", {})
            if model_id in catalog_models
        ]
    )


def merge_docs(docs: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    """Docs view with custom providers + models merged in (ordinary/render).

    The catalog always wins id collisions (merge_conflicts names the
    dropped custom entries for doctor); a custom can never shadow the
    trusted catalog, whatever the registry file says.
    """

    providers = {
        key: value
        for key, value in synthetic_providers(registry).items()
        if key not in docs["providers"]["providers"]
    }
    entries = {
        key: value
        for key, value in synthetic_entries(registry).items()
        if key not in docs["models"]["models"]
    }
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
