"""Trusted catalog loading and reference validation for claude-multi v2.

Loads versioned JSON assets, validates them against the closed-vocabulary
schemas, and enforces semantic invariants: reference integrity, availability
narrowing, provider fork policy, selector/route conflicts, role compatibility,
prompt identity, secret prohibition, and the deterministic bundle hash.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import strict_json, validate as schema_validate


SUPPORTED_DATA_VERSION = 1

SCOPES = ("lead+agents", "lead", "agents", "off")
_SCOPE_USES = {
    "lead+agents": frozenset({"lead", "agents"}),
    "lead": frozenset({"lead"}),
    "agents": frozenset({"agents"}),
    "off": frozenset(),
}

ROLE_ID = re.compile(r"^cm-[a-z][a-z0-9-]*$")
LEAD_ROLE = "cm-lead"

# fork: true passthrough is trusted only on canonical first-party routes.
CANONICAL_FORK_ROUTES = {
    "anthropic": frozenset({"claude-fable-5", "claude-opus-5", "claude-opus-4-8"}),
}

# Compiler-owned environment keys: never settable from a model lead.env block.
# Context capacity and the proactive compaction percentage are derived from the
# catalog's explicit context policy; model-specific env cannot contradict them.
# G0' additionally reserves the config-dir, updater, and nested-spawn keys
# against lead.env while the nested decision chain is pending.
# CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS is compiled from native-agent
# policy; a lead.env value could contradict the appended policy truth.
RESERVED_LEAD_ENV_KEYS = frozenset(
    {
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
        "CLAUDE_CONFIG_DIR",
        "DISABLE_AUTOUPDATER",
        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH",
        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS",
        "CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS",
        "CLAUDE_CODE_DISABLE_WORKFLOWS",
    }
)

_SELECTOR_SUFFIX = "[1m]"

_HASH_FIELDS = frozenset(
    {"sha256", "composition_hash", "catalog_hash", "pre_image_hash", "post_image_hash"}
)
_ENV_REF = re.compile(r"^env:[A-Z0-9_]+$")
_SECRET_SHAPES = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(
        r"(?i)\b(api[_-]?key|secret|password|bearer)\b\s*[:=]\s*['\"]?[A-Za-z0-9._~+/=@:-]{8,}"
    ),
)

SCHEMA_NAMES = (
    "gateway",
    "providers",
    "models",
    "roles",
    "native-contract",
    "composition",
    "session",
    "draft",
    "review",
)

SETTINGS_ALLOWED_KEYS = frozenset(
    {"disableWorkflows", "workflowSizeGuideline", "workflowKeywordTriggerEnabled"}
)


class CatalogError(ValueError):
    """Raised when trusted catalog data fails loading or validation."""


@dataclass(frozen=True)
class Catalog:
    """Immutable view of the validated trusted asset bundle.

    ``contract_source`` records which native contract is in effect:
    ``"packaged"`` (the catalog file), ``"override"`` (a newer operator
    contract from the config root, written by `claude-multi update`), or
    ``"override-ignored-stale"`` (an override exists but is not newer than
    the packaged contract and is ignored).
    """

    root: Path
    docs: dict[str, Any]
    prompt_bodies: dict[str, bytes]
    bundle: dict[str, Any]
    bundle_sha256: str
    contract_source: str = "packaged"

    @property
    def providers(self) -> dict[str, Any]:
        return self.docs["providers"]["providers"]

    @property
    def models(self) -> dict[str, Any]:
        return self.docs["models"]["models"]

    @property
    def roles(self) -> dict[str, Any]:
        return self.docs["roles"]["roles"]

    @property
    def default_composition(self) -> dict[str, Any]:
        return self.docs["compositions/default"]

    @property
    def effort_vocabulary(self) -> tuple[str, ...]:
        return tuple(self.docs["native-contract"]["effort_vocabulary"]["values"])


def _selector_base(selector: str) -> str:
    if selector.endswith(_SELECTOR_SUFFIX):
        return selector[: -len(_SELECTOR_SUFFIX)]
    return selector


def _secret_scan(node: Any, path: str, errors: list[str], key: str | None = None) -> None:
    if isinstance(node, dict):
        for child_key in sorted(node):
            _secret_scan(node[child_key], f"{path}.{child_key}", errors, child_key)
        return
    if isinstance(node, list):
        for index, item in enumerate(node):
            _secret_scan(item, f"{path}[{index}]", errors, key)
        return
    if not isinstance(node, str):
        return
    if key in _HASH_FIELDS or _ENV_REF.fullmatch(node) or node.startswith("sha256:"):
        return
    for shape in _SECRET_SHAPES:
        if shape.search(node):
            errors.append(f"{path}: secret-like value is forbidden in trusted data")
            return


def load_raw(root: Path | str) -> dict[str, Any]:
    """Load schemas, documents, and prompt bodies without semantic checks.

    Raises CatalogError on any strict-JSON or schema violation. Semantic
    reference validation runs separately in :func:`validate_catalog` so tests
    can exercise mutated copies.
    """

    root = Path(root)
    if not root.is_dir():
        raise CatalogError(f"{root}: catalog root is not a directory")

    errors: list[str] = []
    schemas: dict[str, Any] = {}
    for name in SCHEMA_NAMES:
        relative = f"schemas/{name}.schema.json"
        try:
            schema = strict_json.load(root / relative)
        except strict_json.StrictJSONError as exc:
            raise CatalogError(f"{relative}: {exc}") from exc
        try:
            schema_validate.check_schema(schema)
        except schema_validate.SchemaError as exc:
            raise CatalogError(f"{relative}: {exc}") from exc
        schemas[name] = schema

    docs: dict[str, Any] = {}
    # (relative path, schema name, versioned): settings.json is consumed
    # directly by Claude Code and is not a versioned catalog document.
    documents = {
        "version": ("version.json", None, True),
        "settings": ("settings.json", None, False),
        "gateway": ("catalog/gateway.json", "gateway", True),
        "providers": ("catalog/providers.json", "providers", True),
        "models": ("catalog/models.json", "models", True),
        "roles": ("catalog/roles.json", "roles", True),
        "native-contract": ("catalog/native-contract.json", "native-contract", True),
        "compositions/default": ("catalog/compositions/default.json", "composition", True),
    }
    for key, (relative, schema_name, versioned) in documents.items():
        try:
            document = strict_json.load(root / relative)
        except strict_json.StrictJSONError as exc:
            raise CatalogError(f"{relative}: {exc}") from exc
        if schema_name is not None:
            problems = schema_validate.validate(document, schemas[schema_name], "$")
            if problems:
                raise CatalogError(f"{relative}: " + "; ".join(problems))
        if not isinstance(document, dict):
            raise CatalogError(f"{relative}: top-level document must be an object")
        if versioned and document.get("version") != SUPPORTED_DATA_VERSION:
            raise CatalogError(
                f"{relative}: unsupported data version {document.get('version')!r}"
            )
        docs[key] = document

    prompt_bodies: dict[str, bytes] = {}
    catalog_dir = root / "catalog"
    for role_id, role in sorted(docs["roles"]["roles"].items()):
        prompt_relative = role["prompt_file"]
        prompt_path = catalog_dir / prompt_relative
        resolved = prompt_path.resolve()
        if not resolved.is_relative_to(catalog_dir.resolve()):
            raise CatalogError(
                f"roles.{role_id}.prompt_file: escapes the trusted catalog root"
            )
        if prompt_path.is_symlink() or not prompt_path.is_file():
            raise CatalogError(
                f"roles.{role_id}.prompt_file: not a regular file in the catalog"
            )
        prompt_bodies[role_id] = prompt_path.read_bytes()

    return {"root": root, "docs": docs, "prompt_bodies": prompt_bodies, "schemas": schemas}


def _check_versions_settings(docs: dict[str, Any], errors: list[str]) -> None:
    version_doc = docs["version"]
    for key in ("version", "launcher_version", "catalog_version"):
        if key not in version_doc:
            errors.append(f"version.json: missing {key!r}")
    settings = docs["settings"]
    if "version" in settings:
        errors.append("settings.json: must not contain a 'version' key")
    for key in sorted(settings):
        if key not in SETTINGS_ALLOWED_KEYS:
            errors.append(f"settings.json: unverified settings key {key!r}")


def _uses(scope: str) -> frozenset[str]:
    return _SCOPE_USES[scope]


def validate_composition(
    composition: dict[str, Any],
    models: dict[str, Any],
    roles: dict[str, Any],
    providers: dict[str, Any],
) -> list[str]:
    """Semantic composition validation against trusted models/roles/providers."""

    errors: list[str] = []
    availability = composition["availability"]
    provider_scope = availability["providers"]
    model_scope = availability["models"]

    for key in sorted(provider_scope):
        if key not in providers:
            errors.append(f"availability.providers: unknown provider {key!r}")
    for key in sorted(model_scope):
        if key not in models:
            errors.append(f"availability.models: unknown model {key!r}")
            continue
        scope = model_scope[key]
        model = models[key]
        provider = model["provider"]
        granted = provider_scope.get(provider, "off")
        if not _uses(scope) <= _uses(granted):
            errors.append(
                f"availability.models.{key}: scope {scope!r} exceeds provider "
                f"{provider!r} scope {granted!r}"
            )
        if not _uses(scope) <= set(model["capabilities"]):
            errors.append(
                f"availability.models.{key}: scope {scope!r} exceeds immutable "
                f"catalog capability {model['capabilities']!r}"
            )

    lead_slots = [slot for slot in composition["slots"] if slot["role"] == LEAD_ROLE]
    if len(lead_slots) != 1:
        errors.append(f"slots: exactly one {LEAD_ROLE} slot required, found {len(lead_slots)}")

    seen_triples: set[tuple[str, str, str]] = set()
    preferred_per_role: dict[str, int] = {}
    analyst_available = 0

    for index, slot in enumerate(composition["slots"]):
        where = f"slots[{index}]"
        role_id = slot["role"]
        model_id = slot["model"]
        if role_id not in roles:
            errors.append(f"{where}: unknown role {role_id!r}")
            continue
        if model_id not in models:
            errors.append(f"{where}: unknown model {model_id!r}")
            continue
        model = models[model_id]
        if role_id not in model["compatible_roles"]:
            errors.append(f"{where}: model {model_id!r} is not compatible with {role_id!r}")
        lane = slot.get("lane", model["default_lane"])
        if lane not in model["lanes"]:
            errors.append(f"{where}: model {model_id!r} has no lane {lane!r}")
            continue
        triple = (role_id, model_id, lane)
        if triple in seen_triples:
            errors.append(f"{where}: duplicate slot (role, model, lane) triple {triple!r}")
        seen_triples.add(triple)

        if role_id == LEAD_ROLE:
            if slot.get("preferred") is not None:
                errors.append(f"{where}: preferred does not apply to the lead slot")
            if slot.get("lane") is not None:
                errors.append(f"{where}: lane does not apply to the lead slot")
            use = "lead"
        else:
            use = "agents"
            preferred_per_role[role_id] = preferred_per_role.get(role_id, 0) + (
                1 if slot.get("preferred") else 0
            )

        scope = model_scope.get(model_id, "off")
        if use not in _uses(scope):
            errors.append(
                f"{where}: model {model_id!r} scope {scope!r} does not allow use as {use}"
            )
        if use == "lead":
            if "lead" not in model["capabilities"]:
                errors.append(f"{where}: model {model_id!r} lacks the lead capability")
            if model["lead"] is None:
                errors.append(f"{where}: model {model_id!r} has no lead block")
        elif role_id == "cm-analyst":
            analyst_available += 1

    for role_id in sorted(preferred_per_role):
        if preferred_per_role[role_id] != 1:
            errors.append(
                f"slots: role {role_id!r} must have exactly one preferred variant, "
                f"found {preferred_per_role[role_id]}"
            )

    policy = composition["native_agents"]
    if policy["explore"] == "replace" and analyst_available == 0:
        errors.append(
            "native_agents.explore: 'replace' requires at least one available "
            "cm-analyst variant"
        )

    workflows = composition.get("workflows", "native")
    if workflows not in ("native", "off"):
        errors.append(f"workflows: invalid value {workflows!r}")

    return errors


def validate_catalog(raw: dict[str, Any]) -> list[str]:
    """Semantic reference validation over loaded catalog data."""

    errors: list[str] = []
    docs = raw["docs"]
    providers = docs["providers"]["providers"]
    models = docs["models"]["models"]
    roles = docs["roles"]["roles"]
    native_contract = docs["native-contract"]
    efforts = tuple(native_contract["effort_vocabulary"]["values"])

    _check_versions_settings(docs, errors)

    for role_id in sorted(roles):
        if not ROLE_ID.fullmatch(role_id):
            errors.append(f"roles: invalid role ID {role_id!r}")
        body = raw["prompt_bodies"].get(role_id)
        if body is None:
            errors.append(f"roles.{role_id}: prompt body was not loaded")
        elif not body.strip():
            errors.append(f"roles.{role_id}: prompt body is empty")

    prompt_hashes: dict[str, str] = {}
    for role_id, body in sorted(raw["prompt_bodies"].items()):
        digest = strict_json.sha256_hex(body)
        if digest in prompt_hashes:
            errors.append(
                f"roles.{role_id}: prompt body duplicates {prompt_hashes[digest]!r}"
            )
        prompt_hashes[digest] = role_id

    for provider_id in sorted(providers):
        provider = providers[provider_id]
        canonical = CANONICAL_FORK_ROUTES.get(provider_id, frozenset())
        seen_routes: set[str] = set()
        for route in provider["passthrough_routes"]:
            name = route["name"]
            if name in seen_routes:
                errors.append(f"providers.{provider_id}: duplicate passthrough route {name!r}")
            seen_routes.add(name)
            if route["fork"] and name not in canonical:
                errors.append(
                    f"providers.{provider_id}: fork:true is trusted only on canonical "
                    f"routes {sorted(canonical) or '[]'}, not {name!r}"
                )
            if not route["fork"]:
                errors.append(
                    f"providers.{provider_id}: passthrough route {name!r} must carry "
                    "the fork:true policy"
                )
        for missing in sorted(canonical - seen_routes):
            errors.append(
                f"providers.{provider_id}: missing canonical passthrough route "
                f"{missing!r}"
            )

    passthrough_names = {
        provider_id: {route["name"] for route in provider["passthrough_routes"]}
        for provider_id, provider in providers.items()
    }

    lane_selectors: dict[str, str] = {}
    for model_id in sorted(models):
        model = models[model_id]
        where = f"models.{model_id}"
        provider_id = model["provider"]
        if provider_id not in providers:
            errors.append(f"{where}.provider: unknown provider {provider_id!r}")
            continue
        provider = providers[provider_id]
        context = model["context"]
        client_tokens = context["client_tokens"]
        provider_tokens = context["provider_tokens"]
        scalar_tokens = context["scalar_tokens"]
        if provider_tokens > client_tokens:
            errors.append(
                f"{where}.context.provider_tokens: {provider_tokens} exceeds "
                f"client_tokens {client_tokens}"
            )
        if provider_tokens > context["declared_tokens"]:
            errors.append(
                f"{where}.context.provider_tokens: {provider_tokens} exceeds "
                f"declared_tokens {context['declared_tokens']}"
            )
        if context["validated_tokens"] > context["declared_tokens"]:
            errors.append(
                f"{where}.context.validated_tokens: {context['validated_tokens']} "
                f"exceeds declared_tokens {context['declared_tokens']}"
            )
        if (
            context["validated_tokens"] < provider_tokens
            and "user_reported_tokens" in context
        ):
            if context["user_reported_tokens"] != provider_tokens:
                errors.append(
                    f"{where}.context.provider_tokens: a user-attested configured "
                    "bound must match user_reported_tokens"
                )
            if "not benchmark-verified" not in context["qualification"]:
                errors.append(
                    f"{where}.context.qualification: a user-attested configured "
                    "bound must say it is not benchmark-verified"
                )
        if scalar_tokens is not None and scalar_tokens > provider_tokens:
            errors.append(
                f"{where}.context.scalar_tokens: {scalar_tokens} exceeds "
                f"provider_tokens {provider_tokens}"
            )
        extended_selector = model["client_selector"].endswith("[1m]")
        if extended_selector != (client_tokens >= 1_000_000):
            errors.append(
                f"{where}.context.client_tokens: [1m] selector classification and "
                f"client_tokens={client_tokens} disagree"
            )
        ordinary_profile = context["ordinary_profile"]
        if "lead" in model["capabilities"] and ordinary_profile is None:
            errors.append(
                f"{where}.context.ordinary_profile: lead-capable models must belong "
                "to an ordinary gateway profile"
            )
        if "lead" not in model["capabilities"] and ordinary_profile is not None:
            errors.append(
                f"{where}.context.ordinary_profile: agents-only models cannot be "
                "ordinary gateway leads"
            )
        if ordinary_profile == "large" and client_tokens < 1_000_000:
            errors.append(
                f"{where}.context.ordinary_profile: large profile requires a 1M "
                "client context"
            )

        if model["default_lane"] not in model["lanes"]:
            errors.append(
                f"{where}.default_lane: {model['default_lane']!r} is not a lane of this model"
            )
        for role_id in model["compatible_roles"]:
            if role_id not in roles:
                errors.append(f"{where}.compatible_roles: unknown role {role_id!r}")
        for role_id in model["role_hints"]:
            if role_id not in model["compatible_roles"]:
                errors.append(
                    f"{where}.role_hints: hint for incompatible role {role_id!r}"
                )
        for lane_id, lane in sorted(model["lanes"].items()):
            lane_where = f"{where}.lanes.{lane_id}"
            selector = lane["client_selector"]
            base = _selector_base(selector)
            if selector in lane_selectors:
                errors.append(
                    f"{lane_where}: client selector {selector!r} duplicates "
                    f"{lane_selectors[selector]}"
                )
            else:
                lane_selectors[selector] = lane_where
            if base in passthrough_names.get(provider_id, set()):
                pass  # canonical ride: lane may select a provider passthrough route
            elif base in {name for names in passthrough_names.values() for name in names}:
                errors.append(
                    f"{lane_where}: selector {selector!r} rides another provider's "
                    f"passthrough route {base!r}"
                )
            effort = lane["agent_effort"]
            if effort not in efforts:
                errors.append(
                    f"{lane_where}: effort {effort!r} is outside the trusted effort "
                    f"vocabulary {list(efforts)}"
                )
            contract = lane["proxy_effort_contract"]
            if contract is not None and contract not in provider["payload_contracts"]:
                errors.append(
                    f"{lane_where}: proxy effort contract {contract!r} is not declared "
                    f"by provider {provider_id!r}"
                )
        if (
            model["default_lane"] in model["lanes"]
            and model["client_selector"]
            != model["lanes"][model["default_lane"]]["client_selector"]
        ):
            errors.append(
                f"{where}.client_selector: must equal the default lane's client selector"
            )

        lead = model["lead"]
        if lead is not None:
            if lead["effort"] not in efforts:
                errors.append(
                    f"{where}.lead.effort: {lead['effort']!r} is outside the trusted "
                    f"effort vocabulary {list(efforts)}"
                )
            for env_key, env_value in lead["env"].items():
                if env_key in RESERVED_LEAD_ENV_KEYS:
                    errors.append(
                        f"{where}.lead.env: {env_key!r} is compiler-owned and reserved"
                    )
                elif not env_key.startswith("CLAUDE_CODE_"):
                    errors.append(f"{where}.lead.env: unexpected variable {env_key!r}")
                if env_value.isdigit() and int(env_value) <= 0:
                    errors.append(
                        f"{where}.lead.env: {env_key!r} must be a positive integer"
                    )
        if "lead" in model["capabilities"] and lead is None:
            errors.append(f"{where}: lead capability requires a lead block")

    for document_key in ("gateway", "providers", "models", "roles", "native-contract", "compositions/default"):
        _secret_scan(docs[document_key], f"$.{document_key}", errors)

    errors.extend(
        validate_composition(docs["compositions/default"], models, roles, providers)
    )
    return errors


def _version_key(name: Any) -> tuple[int, ...] | None:
    if not isinstance(name, str):
        return None
    parts = name.split(".")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def _load_contract_override(
    path: Path, schema: dict[str, Any], packaged: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    """Load the operator contract override, strictly, failing closed.

    Returns ``(document, source)`` — ``(None, "override-ignored-stale")``
    when the override is not newer than the packaged contract, and raises
    CatalogError when the override exists but is unreadable, invalid, or
    malformed (an operator trust anchor must never degrade silently).
    """

    from . import state  # local import: state hardening for the config root

    try:
        raw = state.read_private(path)
    except state.StateError as exc:
        raise CatalogError(f"contract override {path}: {exc}") from exc
    try:
        document = strict_json.loads(raw)
    except strict_json.StrictJSONError as exc:
        raise CatalogError(f"contract override {path}: {exc}") from exc
    problems = schema_validate.validate(document, schema, "$")
    if problems:
        raise CatalogError(
            f"contract override {path}: invalid native contract: "
            + "; ".join(problems)
        )
    secret_errors: list[str] = []
    _secret_scan(document, "$", secret_errors)
    if secret_errors:
        raise CatalogError(
            f"contract override {path}: " + "; ".join(secret_errors)
        )
    packaged_key = _version_key(packaged["claude"]["validated_version"])
    override_key = _version_key(document["claude"]["validated_version"])
    if override_key is None or packaged_key is None or override_key <= packaged_key:
        return None, "override-ignored-stale"
    return document, "override"


def load_catalog(root: Path | str, *, contract_override: Path | None = None) -> Catalog:
    """Load, schema-validate, and semantically validate the trusted bundle.

    When ``contract_override`` names an operator contract file (written by
    `claude-multi update` after its evidence gate), a strictly newer
    validated version replaces the packaged native contract — the layered
    trust anchor. The packaged bundle and its hash never change; only the
    effective contract document does.
    """

    raw = load_raw(root)
    problems = validate_catalog(raw)
    if problems:
        raise CatalogError("; ".join(problems))

    docs = raw["docs"]
    prompt_hashes = {
        role_id: "sha256:" + strict_json.sha256_hex(body)
        for role_id, body in sorted(raw["prompt_bodies"].items())
    }
    bundle = {
        "version": docs["version"],
        "settings": docs["settings"],
        "gateway": docs["gateway"],
        "providers": docs["providers"],
        "models": docs["models"],
        "roles": docs["roles"],
        "native_contract": docs["native-contract"],
        "prompts": prompt_hashes,
        "compositions/default": docs["compositions/default"],
    }
    contract_source = "packaged"
    if contract_override is not None and os.path.lexists(contract_override):
        override_doc, contract_source = _load_contract_override(
            Path(contract_override), raw["schemas"]["native-contract"], docs["native-contract"]
        )
        if override_doc is not None:
            docs = {**docs, "native-contract": override_doc}
    return Catalog(
        root=raw["root"],
        docs=docs,
        prompt_bodies=raw["prompt_bodies"],
        bundle=bundle,
        bundle_sha256=strict_json.bundle_digest(bundle),
        contract_source=contract_source,
    )
