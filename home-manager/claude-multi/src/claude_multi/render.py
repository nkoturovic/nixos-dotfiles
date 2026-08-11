"""Pure deterministic CLIProxyAPI config renderer for claude-multi v2.

Consumes only trusted ``gateway.json``/``providers.json``/``models.json``
facts and emits the complete integration YAML with a small schema-specific
emitter — no YAML library, no anchors/tags, stable ordering, terminal
newline. Secret references stay opaque: ``env:NAME`` values are resolved only
through the injected ``resolve_secret`` callable (dev/check passes dummy
values; the runtime render passes the real resolver). When a required
provider secret is unavailable, that provider's routes/aliases and payload
contracts are omitted atomically and reported, never half-rendered.

Adapter protocol contracts below are the pinned payload semantics of the
trusted adapters. Changing them is a reviewed adapter change (blueprint
REQ-046), not data drift; they are not hidden route tables.
"""

from __future__ import annotations

import json
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class RenderError(ValueError):
    """Raised when trusted facts cannot be rendered deterministically."""


# Pinned adapter payload contracts (trusted in-process protocol semantics).
ADAPTER_PAYLOAD_CONTRACTS: dict[str, dict[str, dict[str, Any]]] = {
    "cliproxy-oauth-codex-v1": {
        "reasoning-effort-high": {
            "kind": "override",
            "protocol": "codex",
            "params": {"reasoning.effort": "high"},
        },
        "reasoning-effort-xhigh": {
            "kind": "override",
            "protocol": "codex",
            "params": {"reasoning.effort": "xhigh"},
        },
    },
    "cliproxy-claude-compatible-v1": {
        "output-config-high": {
            "kind": "override",
            "protocol": "claude",
            "params": {"output_config.effort": "high"},
        },
        "output-config-max": {
            "kind": "override",
            "protocol": "claude",
            "params": {"output_config.effort": "max"},
        },
        "reasoning-effort-xhigh": {
            "kind": "override",
            "protocol": "claude",
            "params": {"reasoning_effort": "xhigh"},
        },
        "reasoning-effort-max": {
            "kind": "override",
            "protocol": "claude",
            "params": {"reasoning_effort": "max"},
        },
        "filter-thinking": {
            "kind": "filter",
            "protocol": "claude",
            "params": ["thinking"],
        },
    },
}

_SELECTOR_SUFFIX = "[1m]"


def _selector_base(selector: str) -> str:
    if selector.endswith(_SELECTOR_SUFFIX):
        return selector[: -len(_SELECTOR_SUFFIX)]
    return selector


def _emit_scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    if value is None:
        return "null"
    if isinstance(value, bool):
        raise RenderError(f"unsupported scalar {value!r}")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        # JSON double-quoted form is valid, safe YAML for this subset.
        return json.dumps(value, ensure_ascii=False)
    raise RenderError(f"unsupported scalar type {type(value).__name__}")


def _emit_key(key: str) -> str:
    if not isinstance(key, str) or not key:
        raise RenderError(f"unsupported mapping key {key!r}")
    if all(char.isalnum() or char in "-_" for char in key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _emit_lines(node: Any, indent: int) -> list[str]:
    pad = " " * indent
    lines: list[str] = []
    if isinstance(node, dict):
        if not node:
            return [f"{pad}{{}}"]
        for key, value in node.items():
            key = _emit_key(key)
            if isinstance(value, dict):
                if not value:
                    lines.append(f"{pad}{key}: {{}}")
                else:
                    lines.append(f"{pad}{key}:")
                    lines.extend(_emit_lines(value, indent + 2))
            elif isinstance(value, list):
                if not value:
                    lines.append(f"{pad}{key}: []")
                else:
                    lines.append(f"{pad}{key}:")
                    lines.extend(_emit_lines(value, indent + 2))
            else:
                lines.append(f"{pad}{key}: {_emit_scalar(value)}")
        return lines
    if isinstance(node, list):
        for item in node:
            if isinstance(item, dict):
                if not item:
                    lines.append(f"{pad}- {{}}")
                    continue
                keys = list(item)
                first = _emit_key(keys[0])
                first_value = item[keys[0]]
                if isinstance(first_value, (dict, list)):
                    lines.append(f"{pad}- {first}:")
                    lines.extend(_emit_lines(first_value, indent + 4))
                else:
                    lines.append(f"{pad}- {first}: {_emit_scalar(first_value)}")
                for raw_key in keys[1:]:
                    key = _emit_key(raw_key)
                    value = item[raw_key]
                    if isinstance(value, dict):
                        if not value:
                            lines.append(f"{pad}  {key}: {{}}")
                        else:
                            lines.append(f"{pad}  {key}:")
                            lines.extend(_emit_lines(value, indent + 4))
                    elif isinstance(value, list):
                        if not value:
                            lines.append(f"{pad}  {key}: []")
                        else:
                            lines.append(f"{pad}  {key}:")
                            lines.extend(_emit_lines(value, indent + 4))
                    else:
                        lines.append(f"{pad}  {key}: {_emit_scalar(value)}")
            elif isinstance(item, list):
                lines.append(f"{pad}-")
                lines.extend(_emit_lines(item, indent + 2))
            else:
                lines.append(f"{pad}- {_emit_scalar(item)}")
        return lines
    raise RenderError("document root must be a mapping")


def emit_yaml(document: dict[str, Any]) -> str:
    """Emit the restricted YAML form: deterministic, newline-terminated."""

    if not isinstance(document, dict):
        raise RenderError("document root must be a mapping")
    return "\n".join(_emit_lines(document, 0)) + "\n"


@dataclass(frozen=True)
class RenderResult:
    """Rendered YAML plus the structured availability report."""

    yaml: str
    available_providers: tuple[str, ...]
    unavailable: tuple[dict[str, str], ...] = field(default_factory=tuple)


def unavailable_providers(
    providers: dict[str, Any],
    *,
    resolve_secret: Callable[[str], str | None],
) -> tuple[dict[str, str], ...]:
    """Providers whose routes are omitted, as {"provider", "reason"} entries.

    The rule mirrors the omission pass in ``build_config_document`` exactly:
    a direct provider whose secret resolves to None is unavailable; OAuth
    pools always render. UIs that mark provider availability (the ordinary
    gateway picker, D46) share it so their marking can never drift from
    what the renderer actually serves; a parity test pins the two together.
    """

    unavailable: list[dict[str, str]] = []
    for provider_id in sorted(providers):
        transport = providers[provider_id]["transport"]
        if transport["kind"] != "direct":
            continue
        secret_ref = transport["auth"]["secret_ref"]
        if resolve_secret(secret_ref.removeprefix("env:")) is None:
            unavailable.append(
                {
                    "provider": provider_id,
                    "reason": f"missing required secret {secret_ref}",
                }
            )
    return tuple(unavailable)


def _alias_entries(
    provider_id: str,
    provider: dict[str, Any],
    models: dict[str, Any],
) -> list[dict[str, Any]]:
    """Passthrough routes plus lane-derived aliases for one OAuth pool.

    Fork semantics come from the validated provider-profile passthrough rule
    (``route["fork"]``), never recomputed from the transport pool.
    """

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    fork_by_route: dict[str, bool] = {}
    for route in provider["passthrough_routes"]:
        fork_by_route[route["name"]] = bool(route["fork"])
        entry = {
            "name": route["name"],
            "alias": route["name"],
            "force-mapping": True,
            "fork": bool(route["fork"]),
        }
        key = (entry["name"], entry["alias"])
        if key not in seen:
            entries.append(entry)
            seen.add(key)
    for model_id in sorted(models):
        model = models[model_id]
        if model["provider"] != provider_id:
            continue
        for lane_id in sorted(model["lanes"]):
            base = _selector_base(model["lanes"][lane_id]["client_selector"])
            if base == model["wire_model"]:
                continue
            entry = {
                "name": model["wire_model"],
                "alias": base,
                "force-mapping": True,
                "fork": fork_by_route.get(model["wire_model"], False),
            }
            key = (entry["name"], entry["alias"])
            if key not in seen:
                entries.append(entry)
                seen.add(key)
    return entries


def provider_selectors(
    provider_id: str,
    provider: dict[str, Any],
    models: dict[str, Any],
    *,
    available: bool = True,
) -> frozenset[str]:
    """The selectors one provider renders (015/018 shared rule).

    OAuth pools always render (passthrough names + lane-derived aliases);
    direct providers render their lane aliases only when available (secret
    resolved) — matching the config sections exactly.
    """

    if provider["transport"]["kind"] == "oauth-pool":
        return frozenset(
            entry["alias"] for entry in _alias_entries(provider_id, provider, models)
        )
    if not available:
        return frozenset()
    return frozenset(
        _selector_base(lane["client_selector"])
        for model in models.values()
        if model["provider"] == provider_id
        for lane in model["lanes"].values()
    )


def rendered_selectors(document: dict[str, Any]) -> frozenset[str]:
    """Every public selector a rendered config document serves (015 D-d).

    OAuth sections serve each entry's alias (passthrough names alias
    themselves); direct sections serve the lane alias only — the gateway
    registers the alias as the public id, NOT the upstream wire name
    (verified live against a disposable loopback proxy: k3/qwen3.8-max/
    glm-5.2 do not appear in /v1/models). The doctor served-models
    cross-check compares this set against the running gateway's /v1/models.
    """

    selectors: set[str] = set()
    for entries in document.get("oauth-model-alias", {}).values():
        for entry in entries:
            selectors.add(entry["alias"])
    for section in document.get("claude-api-key", []):
        for entry in section.get("models", []):
            selectors.add(entry["alias"])
    return frozenset(selectors)


def build_config_document(
    gateway: dict[str, Any],
    providers: dict[str, Any],
    models: dict[str, Any],
    *,
    home: Path,
    gateway_token: str,
    resolve_secret: Callable[[str], str | None],
) -> tuple[dict[str, Any], tuple[str, ...], tuple[dict[str, str], ...]]:
    """Build the ordered config document and availability report."""

    gateway_info = gateway["gateway"]
    parts = urllib.parse.urlsplit(gateway_info["base_url"])
    static = gateway_info["cliproxy_static"]

    # Secret availability: a direct provider without its secret is omitted.
    available: dict[str, dict[str, Any]] = {}
    unavailable: list[dict[str, str]] = []
    resolved_secrets: dict[str, str] = {}
    for provider_id in sorted(providers):
        provider = providers[provider_id]
        transport = provider["transport"]
        if transport["kind"] == "direct":
            secret_ref = transport["auth"]["secret_ref"]
            env_name = secret_ref.removeprefix("env:")
            value = resolve_secret(env_name)
            if value is None:
                unavailable.append(
                    {
                        "provider": provider_id,
                        "reason": f"missing required secret {secret_ref}",
                    }
                )
                continue
            resolved_secrets[provider_id] = value
        available[provider_id] = provider

    document: dict[str, Any] = {}
    document["host"] = parts.hostname
    document["port"] = parts.port
    document["tls"] = static["tls"]
    document["remote-management"] = static["remote-management"]
    document["auth-dir"] = str(home / gateway_info["auth_dir"])
    document["api-keys"] = [gateway_token]
    document["debug"] = static["debug"]
    document["pprof"] = static["pprof"]
    document["plugins"] = static["plugins"]
    document["commercial-mode"] = static["commercial-mode"]
    document["logging-to-file"] = static["logging-to-file"]
    document["usage-statistics-enabled"] = static["usage-statistics-enabled"]
    document["proxy-url"] = static["proxy-url"]
    document["passthrough-headers"] = static["passthrough-headers"]
    document["request-retry"] = static["request-retry"]
    document["max-retry-credentials"] = static["max-retry-credentials"]
    document["max-retry-interval"] = static["max-retry-interval"]
    document["disable-cooling"] = static["disable-cooling"]
    document["save-cooldown-status"] = static["save-cooldown-status"]
    document["quota-exceeded"] = static["quota-exceeded"]
    document["routing"] = static["routing"]
    document["ws-auth"] = static["ws-auth"]

    # OAuth alias sections keyed by pool, in provider order.
    alias_sections: dict[str, list[dict[str, Any]]] = {}
    for provider_id in sorted(available):
        provider = available[provider_id]
        transport = provider["transport"]
        if transport["kind"] != "oauth-pool":
            continue
        pool = transport["pool"]
        entries = _alias_entries(provider_id, provider, models)
        if entries:
            alias_sections.setdefault(pool, []).extend(entries)
    document["oauth-model-alias"] = alias_sections

    direct_sections: list[dict[str, Any]] = []
    for provider_id in sorted(available):
        provider = available[provider_id]
        transport = provider["transport"]
        if transport["kind"] != "direct":
            continue
        section_models: list[dict[str, Any]] = []
        seen_aliases: set[str] = set()
        for model_id in sorted(models):
            model = models[model_id]
            if model["provider"] != provider_id:
                continue
            # Every trusted lane selector is rendered, deduplicated stably.
            for lane_id in sorted(model["lanes"]):
                alias = _selector_base(model["lanes"][lane_id]["client_selector"])
                if alias in seen_aliases:
                    continue
                seen_aliases.add(alias)
                section_models.append(
                    {
                        "name": model["wire_model"],
                        "alias": alias,
                        "display-name": model["display"],
                        "owned-by": provider["independence_family"],
                        "context-length": model["context"]["provider_tokens"],
                        "force-mapping": True,
                    }
                )
        section: dict[str, Any] = {
            "api-key": resolved_secrets[provider_id],
            "base-url": transport["base_url"],
        }
        # Header-style auth (e.g. Kimi's x-api-key) emits the override in its
        # documented position; bearer auth leaves the adapter's default
        # Authorization behavior.
        if transport["auth"]["kind"] == "header":
            section["auth-header"] = transport["auth"]["header"]
        section["cloak"] = {"mode": "never"}
        section["models"] = section_models
        direct_sections.append(section)
    document["claude-api-key"] = direct_sections

    overrides: list[dict[str, Any]] = []
    filters: list[dict[str, Any]] = []
    for provider_id in sorted(available):
        provider = available[provider_id]
        contracts = ADAPTER_PAYLOAD_CONTRACTS.get(provider["adapter"], {})
        for contract_id in provider["payload_contracts"]:
            contract = contracts.get(contract_id)
            if contract is None:
                raise RenderError(
                    f"provider {provider_id!r} declares unknown payload contract "
                    f"{contract_id!r} for adapter {provider['adapter']!r}"
                )
            aliases: list[str] = []
            if contract["kind"] == "filter":
                # Route-bound filters apply to every lane alias of the provider.
                aliases = sorted(
                    {
                        _selector_base(lane["client_selector"])
                        for model_id, model in models.items()
                        if model["provider"] == provider_id
                        for lane in model["lanes"].values()
                    }
                )
            else:
                aliases = sorted(
                    {
                        _selector_base(lane["client_selector"])
                        for model_id, model in models.items()
                        if model["provider"] == provider_id
                        for lane in model["lanes"].values()
                        if lane["proxy_effort_contract"] == contract_id
                    }
                )
            if not aliases:
                continue
            entry = {
                "models": [
                    {"name": alias, "protocol": contract["protocol"]}
                    for alias in aliases
                ],
                "params": contract["params"],
            }
            if contract["kind"] == "override":
                overrides.append(entry)
            else:
                filters.append(entry)
    document["payload"] = {"override": overrides, "filter": filters}

    return document, tuple(sorted(available)), tuple(unavailable)


def render_config(
    gateway: dict[str, Any],
    providers: dict[str, Any],
    models: dict[str, Any],
    *,
    home: Path,
    gateway_token: str,
    resolve_secret: Callable[[str], str | None],
) -> RenderResult:
    """Render the complete deterministic CLIProxyAPI YAML configuration."""

    document, available, unavailable = build_config_document(
        gateway,
        providers,
        models,
        home=home,
        gateway_token=gateway_token,
        resolve_secret=resolve_secret,
    )
    return RenderResult(
        yaml=emit_yaml(document),
        available_providers=available,
        unavailable=unavailable,
    )
