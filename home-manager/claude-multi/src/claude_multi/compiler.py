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
from .composition import LEAD_ID, ResolvedComposition

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
    """Session identity for one launch: fresh or resume."""

    kind: str  # "fresh" | "resume"
    session_id: str


def build_fresh(session_id: str) -> SessionAction:
    return SessionAction(kind="fresh", session_id=session_id)


def build_resume(session_id: str) -> SessionAction:
    return SessionAction(kind="resume", session_id=session_id)


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
    """Canonical Anthropic passthrough route for a family; fail closed if absent."""

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
    env_unset = [
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
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
    # M2 self-identification sentinel: the session's own agents and nested
    # CLI invocations read this to detect "from inside this managed session"
    # (TRANSITIONS section 3 step 4). Always overwritten per launch, so an
    # inherited value from a parent session can never leak through.
    env_set["CLAUDE_MULTI_SESSION_ID"] = session_action.session_id

    scope_plan: ScopePlan | None = None
    if durable:
        from . import scope as scope_mod

        scope_plan = scope_mod.compile_scope(
            resolved,
            docs["roles"]["roles"],
            prompt_bodies,
            scope_mod.catalog_meta_from_docs(docs),
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
        argv += ["--session-id", session_action.session_id]
    elif session_action.kind == "resume":
        argv += ["--resume", session_action.session_id]
    else:
        raise CompilerError(f"unknown session action {session_action.kind!r}")
    argv += ["--name", f"cm:{resolved.name}"]
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
