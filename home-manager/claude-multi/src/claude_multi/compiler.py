"""Deterministic compilation of resolved compositions into native launch form.

Produces the pure compile result: generated agent definitions, the dynamic
`cm-lead` appendix, native built-in policy (env/denies), process environment,
validated passthrough, and exact argv. No effects happen here: no state
writes, no health checks, no exec.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import catalog as catalog_mod
from . import strict_json
from .composition import (
    AUTO_COMPACT_PERCENT,
    LEAD_ID,
    ResolvedComposition,
    auto_compact_trigger,
    operating_window,
)

if TYPE_CHECKING:  # avoid the runtime cycle: scope.py imports this module
    from .scope import ScopePlan


class CompilerError(ValueError):
    """Raised when compilation cannot proceed (fail closed)."""


# Launcher-owned structural flags (blueprint CON-012). Aliases included.
_LONG_BLOCKED_BASE = frozenset(
    {
        "--model",
        "--effort",
        "--agent",
        "--agents",
        "--session-id",
        "--resume",
        "--fork-session",
        "--name",
        "--settings",
        "--disallowedTools",
        "--disallowed-tools",
        "--plugin-dir",
        "--plugin-url",
        "--fallback-model",
        "--continue",
        "--disable-slash-commands",
    }
)
# Contingency lead delivery (the only mode) owns the prompt flags.
_LONG_BLOCKED_CONTINGENCY = frozenset(
    {"--system-prompt", "--append-system-prompt", "--append-system-prompt-file"}
)

_FLAG_OWNERS = {
    "--model": "composition lead/model",
    "--effort": "composition lead effort",
    "--agent": "managed sessions keep the lead on the main thread",
    "--agents": "generated variant definitions",
    "--session-id": "launcher session identity",
    "--resume": "launcher session identity",
    "--fork-session": "launcher session identity",
    "--name": "launcher display metadata",
    "--settings": "trusted settings asset",
    "--disallowedTools": "compiled native-agent policy",
    "--disallowed-tools": "compiled native-agent policy",
    "--plugin-dir": "v2 has no generated plugin",
    "--plugin-url": "v2 has no generated plugin",
    "--fallback-model": "automatic fallback is forbidden",
    "--continue": "launcher per-CWD session memory",
    # D18: sessions started with --disable-slash-commands never watch agent
    # dirs (SA L186) — a silent durability loss for managed sessions.
    "--disable-slash-commands": "agent-directory watching is load-bearing for managed sessions (D18)",
    "--system-prompt": "contingency lead delivery",
    "--append-system-prompt": "contingency lead delivery",
    "--append-system-prompt-file": "contingency lead delivery",
}

FORK_UNVERIFIED_GUIDANCE = (
    "fork with --resume OLD --fork-session --session-id NEW is unverified for "
    "the pinned Claude CLI; fork natively (`claude --resume OLD "
    "--fork-session`) and adopt the resulting session with "
    "`claude-multi sessions link UUID` instead"
)


@dataclass(frozen=True)
class SessionAction:
    """Stable claude-multi identity plus the native UUID targeted by argv."""

    kind: str  # "fresh" | "resume"
    managed_id: str
    runtime_session_id: str

    @property
    def session_id(self) -> str:
        """Compatibility alias for scope/prompt identity during migration."""

        return self.managed_id


def build_fresh(
    managed_id: str, runtime_session_id: str | None = None
) -> SessionAction:
    return SessionAction(
        kind="fresh",
        managed_id=managed_id,
        runtime_session_id=runtime_session_id or managed_id,
    )


def build_resume(
    managed_id: str, runtime_session_id: str | None = None
) -> SessionAction:
    return SessionAction(
        kind="resume",
        managed_id=managed_id,
        runtime_session_id=runtime_session_id or managed_id,
    )


def variant_description(
    role: dict[str, Any], model: dict[str, Any], variant: Any
) -> str:
    """Role summary + model display + routing hint + preferred marker."""

    parts = [role["summary"], f"Model: {model['display']} (lane {variant.lane})."]
    if variant.routing_hint:
        parts.append(variant.routing_hint)
    if variant.preferred:
        parts.append(f"Preferred {variant.role} variant.")
    return " ".join(parts)


def generate_agent_definitions(
    resolved: ResolvedComposition,
    roles: dict[str, Any],
    prompt_bodies: dict[str, bytes],
) -> dict[str, Any]:
    """Generated definitions for selected variants.

    Variants of one role share the byte-identical canonical role prompt;
    definitions differ only in description/model/effort/isolation.
    """

    definitions: dict[str, Any] = {}
    for variant in resolved.variants:
        definition: dict[str, Any] = {
            "description": variant_description(
                roles[variant.role],
                {"display": variant.display},
                variant,
            ),
            "prompt": prompt_bodies[variant.role].decode("utf-8"),
            "model": variant.client_selector,
            "effort": variant.agent_effort,
        }
        if variant.isolation is not None:
            definition["isolation"] = variant.isolation
        definitions[variant.id] = definition
    return definitions


def generate_lead_appendix(
    resolved: ResolvedComposition,
    providers: dict[str, Any],
    *,
    session_id: str,
    composition_name: str,
) -> str:
    """Dynamic cm-lead content: inventory, native policy, independence, rules."""

    lines: list[str] = []
    lines.append("## Effective inventory (generated)")
    lines.append("")
    for variant in resolved.variants:
        marker = " · preferred" if variant.preferred else ""
        hint = f" {variant.routing_hint}" if variant.routing_hint else ""
        lines.append(
            f"- `{variant.id}` — {variant.role} · {variant.display} · "
            f"lane {variant.lane}{marker}.{hint}"
        )
    lines.append("")
    lines.append("## Native-agent policy (generated)")
    lines.append("")
    policy = resolved.native_agents
    explore = policy["explore"]
    if explore == "replace":
        lines.append("- Explore: replaced by `cm-analyst-*` variants (native Explore denied).")
    elif explore == "native":
        lines.append("- Explore: native, inheriting the lead-derived model.")
    else:
        lines.append("- Explore: disabled without replacement.")
    lines.append(f"- Plan: {policy['plan']}.")
    lines.append(f"- general-purpose: {policy['general_purpose']}.")
    lines.append("")
    lines.append("## Review independence (generated)")
    lines.append("")
    families = sorted({variant.family for variant in resolved.variants} | {resolved.lead.family})
    reviewer_families = sorted(
        {variant.family for variant in resolved.variants if variant.role == "cm-reviewer"}
    )
    lines.append(f"Enabled provider families: {', '.join(families)}.")
    for family in families:
        outside = [item for item in reviewer_families if item != family]
        article = "an" if family[0] in "aeiou" else "a"
        if outside:
            lines.append(
                f"- A change authored by {article} {family}-family variant must not "
                f"receive its sole verdict or final review from another "
                f"{family}-family variant while a reviewer from "
                f"{'/'.join(outside)} is enabled."
            )
        else:
            lines.append(
                f"- No enabled cross-family reviewer for {family}-authored changes; "
                "label the review as same-family (reduced independence) instead of "
                "blocking."
            )
    lines.append("")
    lines.append("## Context policy (generated)")
    lines.append("")
    bound_label = (
        "validated provider bound"
        if resolved.lead.provider_context_validated
        else "user-attested configured provider bound"
    )
    lines.append(
        f"- Lead context: {resolved.lead.client_context_tokens} client tokens; "
        f"{bound_label} {resolved.lead.provider_context_tokens}; process "
        f"compaction capacity {resolved.auto_compact_window_tokens}; deterministic "
        f"reactive trigger {resolved.lead.auto_compact_tokens}. Proactive summary "
        "preparation is runtime-controlled and may occur earlier."
    )
    if resolved.auto_compact_window_tokens < resolved.lead.provider_context_tokens:
        lines.append(
            "- Operating ceiling: the process capacity above is capped below the "
            "lead's configured provider bound by local operating policy (D63); "
            "it is not a route-capability claim."
        )
    if resolved.scalar_context_tokens is not None:
        lines.append(
            f"- Process scalar: CLAUDE_CODE_MAX_CONTEXT_TOKENS="
            f"{resolved.scalar_context_tokens} is exported for this mixed "
            "process; it bounds lower-context delegated variants, while the "
            "lead thread keeps the capacity and trigger above."
        )
    if not resolved.lead.provider_context_validated:
        lines.append(
            "- Context qualification: this configured provider bound is not "
            "near-limit benchmark-verified. It follows explicit route/operator "
            "attestation; live acceptance must confirm it before it is described "
            "as provider-safe."
        )
    if resolved.lead.client_context_tokens < 1_000_000 and (
        resolved.native_agents["explore"] == "native"
        or resolved.native_agents["plan"] == "native"
        or resolved.native_agents["general_purpose"] == "on"
    ):
        lines.append(
            "- Native agents inherit this lower-context lead and the process-wide "
            "compaction capacity. Keep delegated prompts and loaded skills bounded; "
            "an enabled 1M cm-* selector does not raise this process capacity. Use "
            "a large-context lead/composition or a separate ordinary large-profile "
            "session for broad context."
        )
    lines.append("")
    lines.append("## Standing rules (generated)")
    lines.append("")
    lines.append("- One writer owns an overlapping file scope at a time.")
    lines.append(
        "- Invoke generated agents by exact ID; never pass a per-invocation model override."
    )
    lines.append("")
    lines.append("## Session sentinel (generated)")
    lines.append("")
    lines.append(f"- Managed session: {session_id} (composition `{composition_name}`).")
    lines.append(
        "- If a selected cm-* type is unavailable, stop delegation. "
        "Never substitute a native or generic agent."
    )
    lines.append(
        "- Exact relaunch after interruption: ask the user to run "
        f"`claude-multi --composition {composition_name} -r {session_id}`."
    )
    lines.append("")
    return "\n".join(lines)


def compile_native_policy(
    policy: dict[str, str], generic_aliases: Iterable[str] = ()
) -> tuple[dict[str, str], list[str]]:
    """Compile built-in policy plus contract-recorded generic native aliases.

    Deny order is deterministic: built-ins (Explore, Plan, general-purpose)
    followed by the sorted unique contract generic aliases (e.g.
    ``Agent(claude)``). Recording an alias compiles a deny only; it asserts no
    functional verification beyond policy compilation.
    """

    env: dict[str, str] = {}
    denies: list[str] = []
    explore_native = policy["explore"] == "native"
    plan_native = policy["plan"] == "native"
    if not explore_native and not plan_native:
        env["CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS"] = "1"
    else:
        if not explore_native:
            denies.append("Agent(Explore)")
        if not plan_native:
            denies.append("Agent(Plan)")
    if policy["general_purpose"] == "off":
        denies.append("Agent(general-purpose)")
    for alias in sorted(set(generic_aliases)):
        deny = f"Agent({alias})"
        if deny not in denies:
            denies.append(deny)
    return env, denies


def validate_passthrough(args: list[str]) -> list[str]:
    """Reject launcher-owned structural flags; pass everything else unchanged."""

    blocked = set(_LONG_BLOCKED_BASE) | _LONG_BLOCKED_CONTINGENCY
    for token in args:
        if token.startswith("--"):
            flag = token.split("=", 1)[0]
            if flag in blocked:
                owner = _FLAG_OWNERS.get(flag, "launcher-owned")
                raise CompilerError(
                    f"passthrough argument {token!r} is launcher-owned ({owner}); "
                    "remove it or use the owning launcher feature"
                )
        elif token.startswith("-") and token != "-":
            if token == "-c" or token.startswith("-r") or token.startswith("-n"):
                raise CompilerError(
                    f"passthrough argument {token!r} is launcher-owned "
                    "(launcher session identity/display); use `claude-multi -c`, "
                    "`claude-multi -r UUID`, or the launcher --name equivalent"
                )
    return list(args)


def _canonical_route(routes: list[str], family: str) -> str:
    """Canonical Anthropic passthrough route for a family; fail closed if absent.

    Exact ``claude-<family>-<N>`` name first, then the first name containing
    the family (legacy compat) — never an accidental substring hit on an
    unrelated route (e.g. "opus" inside a future non-Opus name).
    """

    prefix = f"claude-{family}-"
    for name in routes:
        if name.startswith(prefix):
            return name
    for name in routes:
        if family in name:
            return name
    raise CompilerError(
        f"missing canonical Anthropic {family!r} passthrough route; "
        "the trusted provider profile is incomplete"
    )


def compile_environment(
    gateway: dict[str, Any],
    providers: dict[str, Any],
    resolved: ResolvedComposition,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Process environment: set values and explicit unsets (token excluded)."""

    for key in resolved.lead.env:
        if key in catalog_mod.RESERVED_LEAD_ENV_KEYS:
            raise CompilerError(
                f"lead environment key {key!r} is compiler-owned and reserved"
            )

    anthropic_routes = [
        route["name"] for route in providers["anthropic"]["passthrough_routes"]
    ]
    fable_default = _canonical_route(anthropic_routes, "fable") + "[1m]"
    opus_default = _canonical_route(anthropic_routes, "opus") + "[1m]"

    env_set: dict[str, str] = {
        "ANTHROPIC_BASE_URL": gateway["gateway"]["base_url"],
        "ANTHROPIC_DEFAULT_FABLE_MODEL": fable_default,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": opus_default,
        "CLAUDE_MULTI_GATEWAY": "1",
        # G0' updater hygiene: non-load-bearing; no shared-daemon claim.
        "DISABLE_AUTOUPDATER": "1",
    }
    if resolved.scalar_context_tokens is not None:
        env_set["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(resolved.scalar_context_tokens)
    # Trusted lead environment is applied only after the explicit unsets.
    env_set.update(resolved.lead.env)
    # AUTO_COMPACT_WINDOW is a capacity, not the trigger itself. Claude Code
    # caps it at each model's actual context, reserves up to 20K output tokens,
    # prepares a summary at the earlier 20% buffer, then applies the percentage
    # to the remaining prompt budget for reactive compaction. The provider bound
    # narrows Qwen's capacity to 983,616 before those calculations.
    env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(
        resolved.auto_compact_window_tokens
    )
    env_set["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = str(AUTO_COMPACT_PERCENT)
    env_unset = [
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
        # G0' reservations: an inherited config-dir or nested-spawn control
        # must never leak into a managed legacy launch while the nested
        # decision chain is pending.
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH",
        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS",
        # Compiled from native-agent policy; an inherited value could
        # contradict the compiled policy truth. Unset first; when policy
        # requires it, env_set applies the compiled value afterwards.
        "CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS",
        # Workflow mode is compiled into the durable session settings; an
        # inherited override must never contradict them.
        "CLAUDE_CODE_DISABLE_WORKFLOWS",
    ]
    if resolved.scalar_context_tokens is None:
        # No scalar selection: an inherited context cap must never leak in.
        env_unset.append("CLAUDE_CODE_MAX_CONTEXT_TOKENS")
    return env_set, tuple(env_unset)


@dataclass(frozen=True)
class CompileResult:
    """Pure launch plan; effects (state writes, readiness, exec) happen later."""

    argv: list[str]
    env_set: dict[str, str]
    env_unset: tuple[str, ...]
    policy_env: dict[str, str]
    agents_json: str
    lead_prompt: str
    lead_prompt_path: Path
    session_action: SessionAction
    snapshot: dict[str, Any]
    composition_name: str
    durable: bool = False
    scope_plan: ScopePlan | None = None
    scope_dir: Path | None = None
    passthrough_add_dirs: tuple[str, ...] = ()
    write_lead_prompt: bool = True


def lead_prompt_path(
    state_root: Path, composition_hash: str, session_id: str
) -> Path:
    """Session-scoped lead prompt path: composition digest + exact session UUID.

    Deterministic per (composition, session): fresh launches with different
    UUIDs never overwrite each other's sentinel, resume of the same UUID is
    stable, and a composition transition for the same UUID lands on a new
    digest-named file. The launcher never prunes a prompt file, so a file
    that could belong to an active session is never removed.
    """

    digest = composition_hash.removeprefix("sha256:")[:16]
    return state_root / f"lead-prompt-{digest}-{session_id}.md"


def _extract_add_dirs(args: list[str]) -> tuple[str, ...]:
    """Passthrough ``--add-dir`` values (split and equals forms), in order."""

    add_dirs: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--add-dir":
            if index + 1 >= len(args):
                raise CompilerError("passthrough --add-dir requires a value")
            add_dirs.append(args[index + 1])
            index += 2
            continue
        if token.startswith("--add-dir="):
            add_dirs.append(token.split("=", 1)[1])
        index += 1
    return tuple(add_dirs)


def session_display_name(prefix: str, cwd: Path | str | None) -> str:
    """Session ``--name`` with the project basename appended (issue 013).

    Sessions across projects shared one generic name in the Claude UI
    (``cm:<composition>`` / ``cg:<model>``); the basename makes them
    distinguishable while staying short and printable.
    """

    if cwd is None:
        return prefix
    base = "".join(ch for ch in Path(cwd).name if ch.isprintable()).strip()
    if not base:
        return prefix
    return f"{prefix}@{base[:24]}"


def compile_launch(
    *,
    docs: dict[str, Any],
    prompt_bodies: dict[str, bytes],
    resolved: ResolvedComposition,
    session_action: SessionAction,
    passthrough: list[str],
    settings_path: Path,
    lead_prompt_path: Path,
    durable: bool = False,
    scope_dir: Path | None = None,
    hook_command: str | None = None,
    launch_epoch: int = 0,
    token_helper_command: str | None = None,
    session_cwd: Path | str | None = None,
) -> CompileResult:
    """Compile the pure launch plan. No effects; fail closed on conflicts.

    One ``durable`` flag selects the argv/env shape: legacy mode emits exactly
    the v2 argv (``--agents`` + ``--disallowedTools``); durable mode drops both
    and instead points ``--settings``/``--add-dir`` at the per-session scope,
    whose content is compiled here into ``scope_plan`` (written by the launch
    path). In durable mode ``settings_path`` remains the package base consumed
    by the scope compile, not an argv argument. The cm-lead prompt is always
    delivered by contingency (``--append-system-prompt-file``).
    """

    native_contract = docs["native-contract"]
    safe_args = validate_passthrough(passthrough)
    add_dirs = _extract_add_dirs(safe_args)
    if durable and scope_dir is None:
        raise CompilerError("durable mode requires a scope directory")
    if durable and not hook_command:
        # Never mask a missing lifecycle hook command with a PATH-relative
        # fallback: durable scopes embed it verbatim, and a bare name only
        # resolves by environment luck.
        raise CompilerError("durable mode requires a lifecycle hook command")

    from .composition import snapshot as build_snapshot

    snap = build_snapshot(resolved)
    lead_prompt = (
        prompt_bodies[LEAD_ID].decode("utf-8")
        + "\n"
        + generate_lead_appendix(
            resolved,
            docs["providers"]["providers"],
            session_id=session_action.session_id,
            composition_name=resolved.name,
        )
    )

    policy_env, denies = compile_native_policy(
        resolved.native_agents,
        native_contract["generic_agent_aliases"]["values"],
    )
    env_set, env_unset = compile_environment(
        docs["gateway"], docs["providers"]["providers"], resolved
    )
    env_set.update(policy_env)
    # Stable self-identification sentinel: lifecycle hooks, transitions, and
    # nested invocations key claude-multi state by managed_id even when Claude's
    # authoritative runtime UUID differs. Keep the old name as a compatibility
    # alias until migrated prompts/scopes have been replaced.
    env_set["CLAUDE_MULTI_MANAGED_ID"] = session_action.managed_id
    env_set["CLAUDE_MULTI_SESSION_ID"] = session_action.managed_id
    env_set["CLAUDE_MULTI_LAUNCH_EPOCH"] = str(launch_epoch)

    scope_plan: ScopePlan | None = None
    if durable:
        from . import scope as scope_mod

        scope_plan = scope_mod.compile_scope(
            resolved,
            docs["roles"]["roles"],
            prompt_bodies,
            scope_mod.catalog_meta_from_docs(docs),
            managed_id=session_action.managed_id,
            hook_command=hook_command,
            launch_epoch=launch_epoch,
            token_helper_command=token_helper_command,
        )
        agents_json = ""
    else:
        definitions = generate_agent_definitions(
            resolved,
            docs["roles"]["roles"],
            prompt_bodies,
        )
        agents_json = strict_json.canonical_bytes(definitions).decode("utf-8")

    argv: list[str] = []
    if session_action.kind == "fresh":
        argv += ["--session-id", session_action.runtime_session_id]
    elif session_action.kind == "resume":
        argv += ["--resume", session_action.runtime_session_id]
    else:
        raise CompilerError(f"unknown session action {session_action.kind!r}")
    argv += ["--name", session_display_name(f"cm:{resolved.name}", session_cwd)]
    if durable:
        argv += [
            "--settings",
            str(scope_dir / "settings.json"),
            "--model",
            resolved.lead.client_selector,
            "--effort",
            resolved.lead.effort,
            "--add-dir",
            str(scope_dir),
        ]
    else:
        argv += [
            "--settings",
            str(settings_path),
            "--model",
            resolved.lead.client_selector,
            "--effort",
            resolved.lead.effort,
            "--agents",
            agents_json,
        ]
    argv += ["--append-system-prompt-file", str(lead_prompt_path)]
    if not durable and denies:
        argv += ["--disallowedTools", " ".join(denies)]
    argv += safe_args

    return CompileResult(
        argv=argv,
        env_set=env_set,
        env_unset=env_unset,
        policy_env=policy_env,
        agents_json=agents_json,
        lead_prompt=lead_prompt,
        lead_prompt_path=lead_prompt_path,
        session_action=session_action,
        snapshot=snap,
        composition_name=resolved.name,
        durable=durable,
        scope_plan=scope_plan,
        scope_dir=scope_dir,
        passthrough_add_dirs=add_dirs,
    )


def direct_context_profile(docs: dict[str, Any], model_id: str) -> str:
    """Catalog-owned ordinary profile for one lead-capable model."""

    model = docs["models"]["models"].get(model_id)
    if model is None or "lead" not in model["capabilities"]:
        raise CompilerError(
            f"model {model_id!r} is not supported as an ordinary gateway lead"
        )
    profile = model["context"]["ordinary_profile"]
    if profile is None:
        raise CompilerError(
            f"model {model_id!r} is not supported as an ordinary gateway lead"
        )
    return profile


def ordinary_launch_models(docs: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Ordinary profile -> sorted lead-capable model ids in that profile.

    The launch picker's catalog enumeration (D46): exactly the models
    ``direct_context_profile`` accepts, grouped by their ordinary profile
    so the picker can render the /model fence as section headers.
    """

    groups: dict[str, list[str]] = {}
    for model_id in sorted(docs["models"]["models"]):
        try:
            profile = direct_context_profile(docs, model_id)
        except CompilerError:
            continue
        groups.setdefault(profile, []).append(model_id)
    return {profile: tuple(ids) for profile, ids in sorted(groups.items())}


def ordinary_picker_groups(docs: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    """Picker sections: profile groups plus the single-model section.

    ``ordinary_launch_models`` stays profile-pure (its pins and the
    profile-math callers depend on it); the picker appends profile-less
    models under the synthetic ``"single"`` section. Each single model
    runs fenced to itself — cross-section switches are refused, relaunches
    are not.
    """

    groups = ordinary_launch_models(docs)
    singles = single_model_launch_models(docs)
    if singles:
        groups = {**groups, "single": singles}
    return groups


def direct_model_for_selector(
    docs: dict[str, Any], selector: str
) -> tuple[str, str | None] | None:
    """Resolve a trusted ordinary selector to its catalog model and profile.

    Profile-less (single-model) selectors resolve with a None profile.
    """

    for model_id, model in docs["models"]["models"].items():
        if "lead" not in model["capabilities"]:
            continue
        try:
            profile = direct_context_profile(docs, model_id)
        except CompilerError:
            continue
        candidates = {model["wire_model"], model["client_selector"]}
        candidates.update(lane["client_selector"] for lane in model["lanes"].values())
        if model["context"]["client_tokens"] >= 1_000_000:
            # Claude Code may report the canonical full model selector even when
            # the picker entered through a gateway alias.
            candidates.add(model["wire_model"] + "[1m]")
        if selector in candidates:
            return model_id, profile
    for model_id in single_model_launch_models(docs):
        model = docs["models"]["models"][model_id]
        candidates = {model["wire_model"], model["client_selector"]}
        candidates.update(lane["client_selector"] for lane in model["lanes"].values())
        if selector in candidates:
            return model_id, None
    return None


def single_model_launch_models(docs: dict[str, Any]) -> tuple[str, ...]:
    """Catalog models with no ordinary profile: single-model sessions only.

    These are the models ``direct_context_profile`` rejects (lead-incapable
    or profile-less, e.g. agents-only gpt55). Each runs fenced to its own
    selectors under its own provider bound — never a shared profile fence.
    """

    ids: list[str] = []
    for model_id in sorted(docs["models"]["models"]):
        try:
            direct_context_profile(docs, model_id)
        except CompilerError:
            ids.append(model_id)
    return tuple(ids)


def direct_single_model_selectors(docs: dict[str, Any], model_id: str) -> tuple[str, ...]:
    """The model's own selectors: client selector plus every lane selector."""

    model = docs["models"]["models"].get(model_id)
    if model is None:
        raise CompilerError(f"model {model_id!r} is not in the catalog")
    selectors = {model["client_selector"]}
    selectors.update(lane["client_selector"] for lane in model["lanes"].values())
    return tuple(sorted(selectors))


def direct_single_model_context(
    docs: dict[str, Any], model_id: str
) -> tuple[int | None, int, int]:
    """Process scalar, capacity, and reactive trigger for one profile-less model.

    Profiled models must use ``direct_profile_context`` (their fence is the
    shared profile minimum); this is fail-closed for them so the two paths
    can never silently substitute for each other.
    """

    try:
        direct_context_profile(docs, model_id)
    except CompilerError:
        pass
    else:
        raise CompilerError(
            f"model {model_id!r} has an ordinary profile; use its profile context"
        )
    context = docs["models"]["models"][model_id]["context"]
    window = operating_window(context["provider_tokens"])
    scalar = context["scalar_tokens"]
    process_scalar = operating_window(scalar) if scalar is not None else None
    return process_scalar, window, auto_compact_trigger(window)


def direct_profile_selectors(docs: dict[str, Any], profile: str) -> tuple[str, ...]:
    """Trusted selectors safe under one process-wide compaction policy."""

    selectors: set[str] = set()
    for model_id, model in docs["models"]["models"].items():
        if "lead" not in model["capabilities"]:
            continue
        try:
            candidate = direct_context_profile(docs, model_id)
        except CompilerError:
            continue
        if candidate != profile:
            continue
        selectors.add(model["client_selector"])
        selectors.update(lane["client_selector"] for lane in model["lanes"].values())
    if not selectors:
        raise CompilerError(f"ordinary gateway profile {profile!r} has no models")
    return tuple(sorted(selectors))


def direct_profile_context(
    docs: dict[str, Any], profile: str
) -> tuple[int | None, int, int]:
    """Process scalar, capacity, and deterministic reactive trigger.

    The D63 operating ceiling applies to the profile minimum exactly as it
    does to managed capacity; smaller member bounds still win.
    """

    members = []
    for model_id, model in docs["models"]["models"].items():
        if "lead" not in model["capabilities"]:
            continue
        try:
            candidate = direct_context_profile(docs, model_id)
        except CompilerError:
            # A lead-capable model with no ordinary profile never joins a
            # profile's context math (matches direct_profile_selectors).
            continue
        if candidate == profile:
            members.append(model)
    if not members:
        raise CompilerError(f"ordinary gateway profile {profile!r} has no models")
    provider_window = min(model["context"]["provider_tokens"] for model in members)
    client_windows = {model["context"]["client_tokens"] for model in members}
    scalar_values = [
        model["context"]["scalar_tokens"]
        for model in members
        if model["context"]["scalar_tokens"] is not None
    ]
    process_scalar = (
        operating_window(min(scalar_values))
        if scalar_values and max(client_windows) < 1_000_000
        else None
    )
    provider_window = operating_window(provider_window)
    trigger = auto_compact_trigger(provider_window)
    return process_scalar, provider_window, trigger


def compile_direct_launch(
    *,
    docs: dict[str, Any],
    session_action: SessionAction,
    model_id: str,
    passthrough: list[str],
    scope_dir: Path,
    hook_command: str,
    state_root: Path,
    pin_model: bool = True,
    launch_epoch: int = 0,
    token_helper_command: str | None = None,
    session_cwd: Path | str | None = None,
    no_subagents: bool = False,
) -> CompileResult:
    """Compile an ordinary gateway-backed Claude session with no composition."""

    models = docs["models"]["models"]
    model = models.get(model_id)
    if model is None:
        raise CompilerError(f"model {model_id!r} is not in the catalog")
    try:
        profile = direct_context_profile(docs, model_id)
    except CompilerError:
        profile = None
    if profile is None:
        # Single-model path: profile-less models run fenced to their own
        # selectors under their own provider bound. Byte-identical env/scope
        # shape to the profile path, only the numbers' provenance differs.
        selectors = direct_single_model_selectors(docs, model_id)
        process_scalar, compact_window, _compact_trigger = (
            direct_single_model_context(docs, model_id)
        )
    else:
        selectors = direct_profile_selectors(docs, profile)
        process_scalar, compact_window, _compact_trigger = direct_profile_context(
            docs, profile
        )
    safe_args = validate_passthrough(passthrough)
    add_dirs = _extract_add_dirs(safe_args)

    providers = docs["providers"]["providers"]
    anthropic_routes = [
        route["name"] for route in providers["anthropic"]["passthrough_routes"]
    ]
    env_set: dict[str, str] = {
        "ANTHROPIC_BASE_URL": docs["gateway"]["gateway"]["base_url"],
        "ANTHROPIC_DEFAULT_FABLE_MODEL": _canonical_route(
            anthropic_routes, "fable"
        )
        + "[1m]",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": _canonical_route(anthropic_routes, "opus")
        + "[1m]",
        "CLAUDE_MULTI_GATEWAY": "1",
        "CLAUDE_MULTI_MANAGED_ID": session_action.managed_id,
        "CLAUDE_MULTI_SESSION_ID": session_action.managed_id,
        "CLAUDE_MULTI_LAUNCH_EPOCH": str(launch_epoch),
        "DISABLE_AUTOUPDATER": "1",
    }
    if process_scalar is not None:
        env_set["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(process_scalar)
    env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = str(compact_window)
    env_set["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"] = str(AUTO_COMPACT_PERCENT)
    if no_subagents:
        # Belt to the scope's permissions deny: the explore/plan native
        # agents stay compiled out even if scope settings are bypassed.
        # env_unset still names this key (inherited values are cleared
        # first at launch); the overlay below applies afterwards.
        env_set["CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS"] = "1"

    env_unset = (
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
        "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
        "CLAUDE_CONFIG_DIR",
        "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH",
        "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS",
        "CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS",
        "CLAUDE_CODE_DISABLE_WORKFLOWS",
    )
    from . import scope as scope_mod

    scope_plan = scope_mod.compile_ordinary_scope(
        managed_id=session_action.managed_id,
        hook_command=hook_command,
        available_models=selectors,
        default_model=model["client_selector"],
        launch_epoch=launch_epoch,
        gateway_base_url=docs["gateway"]["gateway"]["base_url"],
        token_helper_command=token_helper_command,
        no_subagents=no_subagents,
    )
    argv: list[str] = []
    if session_action.kind == "fresh":
        argv += ["--session-id", session_action.runtime_session_id]
    elif session_action.kind == "resume":
        argv += ["--resume", session_action.runtime_session_id]
    else:
        raise CompilerError(f"unknown session action {session_action.kind!r}")
    argv += [
        "--name",
        session_display_name(f"cg:{model_id}", session_cwd),
        "--settings",
        str(scope_dir / "settings.json"),
    ]
    if pin_model:
        if model["lead"] is not None:
            lead_effort = model["lead"]["effort"]
        else:
            # Single-model path for agents-only models: no lead block, so
            # pin the default lane's effort for the main thread.
            lead_effort = model["lanes"][model["default_lane"]]["agent_effort"]
        argv += [
            "--model",
            model["client_selector"],
            "--effort",
            lead_effort,
        ]
    argv += safe_args
    return CompileResult(
        argv=argv,
        env_set=env_set,
        env_unset=env_unset,
        policy_env={},
        agents_json="",
        lead_prompt="",
        lead_prompt_path=state_root / f"ordinary-{session_action.managed_id}.unused",
        session_action=session_action,
        snapshot={"ordinary_model": model_id, "context_profile": profile},
        composition_name="ordinary-gateway",
        durable=True,
        scope_plan=scope_plan,
        scope_dir=scope_dir,
        passthrough_add_dirs=add_dirs,
        write_lead_prompt=False,
    )
