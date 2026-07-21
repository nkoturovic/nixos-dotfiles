"""Deterministic compilation of resolved compositions into native launch form.

Produces the pure compile result: generated agent definitions, the dynamic
`cm-lead` appendix, native built-in policy (env/denies), process environment,
validated passthrough, and exact argv. No effects happen here: no state
writes, no health checks, no exec.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import catalog as catalog_mod
from . import strict_json
from .composition import LEAD_ID, ResolvedComposition


class CompilerError(ValueError):
    """Raised when compilation cannot proceed (fail closed)."""


SAME_LAUNCH_MODE = "same-launch-agent"
CONTINGENCY_MODE = "main-thread-append-system-prompt-file"

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
    }
)
_LONG_BLOCKED_CONTINGENCY = frozenset(
    {"--system-prompt", "--append-system-prompt", "--append-system-prompt-file"}
)

_FLAG_OWNERS = {
    "--model": "composition lead/model",
    "--effort": "composition lead effort",
    "--agent": "generated cm-lead selection",
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
    """Session identity for one launch: fresh, resume, or verified fork."""

    kind: str  # "fresh" | "resume" | "fork"
    session_id: str
    resume_id: str | None = None
    new_id: str | None = None


def build_fresh(session_id: str) -> SessionAction:
    return SessionAction(kind="fresh", session_id=session_id)


def build_resume(session_id: str) -> SessionAction:
    return SessionAction(kind="resume", session_id=session_id)


def build_fork(native_contract: dict[str, Any], old_id: str, new_id: str) -> SessionAction:
    """Fork fails closed while the triple-flag acceptance is unverified."""

    status = native_contract["acceptance"]["fork_triple_flag"]["status"]
    if status != "verified":
        raise CompilerError(
            f"cannot compile fork: {FORK_UNVERIFIED_GUIDANCE} "
            f"(acceptance status: {status})"
        )
    return SessionAction(kind="fork", session_id=new_id, resume_id=old_id, new_id=new_id)


def effective_lead_mode(native_contract: dict[str, Any]) -> str:
    """Same-launch lead only with verified acceptance; else reviewed contingency."""

    acceptance = native_contract["acceptance"]["same_launch_agents_and_agent_cm_lead"][
        "status"
    ]
    delivery = native_contract["lead_delivery"]
    if (
        acceptance == "verified"
        and delivery["status"] == "verified"
        and delivery["mode"] == SAME_LAUNCH_MODE
    ):
        return SAME_LAUNCH_MODE
    return CONTINGENCY_MODE


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
    lead_prompt: str,
    lead_mode: str,
    lead_selector: str,
    lead_effort: str,
) -> dict[str, Any]:
    """Generated definitions for selected variants (and cm-lead in same-launch).

    Variants of one role share the byte-identical canonical role prompt;
    definitions differ only in description/model/effort/isolation.
    """

    definitions: dict[str, Any] = {}
    if lead_mode == SAME_LAUNCH_MODE:
        definitions[LEAD_ID] = {
            "description": roles[LEAD_ID]["summary"],
            "prompt": lead_prompt,
            "model": lead_selector,
            "effort": lead_effort,
        }
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
    resolved: ResolvedComposition, providers: dict[str, Any]
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
    return "\n".join(lines)


def compile_native_policy(policy: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Compile built-in policy into env and one consolidated exact deny list."""

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
    return env, denies


def validate_passthrough(args: list[str], *, lead_mode: str) -> list[str]:
    """Reject launcher-owned structural flags; pass everything else unchanged."""

    blocked = set(_LONG_BLOCKED_BASE)
    if lead_mode == CONTINGENCY_MODE:
        blocked |= _LONG_BLOCKED_CONTINGENCY
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
    lead_mode: str
    lead_prompt: str | None
    lead_prompt_path: Path | None
    session_action: SessionAction
    snapshot: dict[str, Any]
    composition_name: str


def lead_prompt_path(state_root: Path, composition_hash: str) -> Path:
    digest = composition_hash.removeprefix("sha256:")[:16]
    return state_root / f"lead-prompt-{digest}.md"


def compile_launch(
    *,
    docs: dict[str, Any],
    prompt_bodies: dict[str, bytes],
    resolved: ResolvedComposition,
    session_action: SessionAction,
    passthrough: list[str],
    settings_path: Path,
    lead_prompt_path: Path | None,
) -> CompileResult:
    """Compile the pure launch plan. No effects; fail closed on conflicts."""

    native_contract = docs["native-contract"]
    lead_mode = effective_lead_mode(native_contract)
    safe_args = validate_passthrough(passthrough, lead_mode=lead_mode)

    from .composition import snapshot as build_snapshot

    snap = build_snapshot(resolved)
    lead_prompt = (
        prompt_bodies[LEAD_ID].decode("utf-8")
        + "\n"
        + generate_lead_appendix(resolved, docs["providers"]["providers"])
    )
    definitions = generate_agent_definitions(
        resolved,
        docs["roles"]["roles"],
        prompt_bodies,
        lead_prompt,
        lead_mode,
        resolved.lead.client_selector,
        resolved.lead.effort,
    )
    agents_json = strict_json.canonical_bytes(definitions).decode("utf-8")

    policy_env, denies = compile_native_policy(resolved.native_agents)
    env_set, env_unset = compile_environment(
        docs["gateway"], docs["providers"]["providers"], resolved
    )
    env_set.update(policy_env)

    argv: list[str] = []
    if session_action.kind == "fresh":
        argv += ["--session-id", session_action.session_id]
    elif session_action.kind == "resume":
        argv += ["--resume", session_action.session_id]
    elif session_action.kind == "fork":
        argv += [
            "--resume",
            session_action.resume_id,
            "--fork-session",
            "--session-id",
            session_action.new_id,
        ]
    else:
        raise CompilerError(f"unknown session action {session_action.kind!r}")
    argv += [
        "--name",
        f"cm:{resolved.name}",
        "--settings",
        str(settings_path),
        "--model",
        resolved.lead.client_selector,
        "--effort",
        resolved.lead.effort,
        "--agents",
        agents_json,
    ]
    if lead_mode == SAME_LAUNCH_MODE:
        argv += ["--agent", LEAD_ID]
    else:
        if lead_prompt_path is None:
            raise CompilerError("contingency lead mode requires a lead prompt path")
        argv += ["--append-system-prompt-file", str(lead_prompt_path)]
    if denies:
        argv += ["--disallowedTools", " ".join(denies)]
    argv += safe_args

    return CompileResult(
        argv=argv,
        env_set=env_set,
        env_unset=env_unset,
        policy_env=policy_env,
        agents_json=agents_json,
        lead_mode=lead_mode,
        lead_prompt=lead_prompt if lead_mode == CONTINGENCY_MODE else None,
        lead_prompt_path=lead_prompt_path if lead_mode == CONTINGENCY_MODE else None,
        session_action=session_action,
        snapshot=snap,
        composition_name=resolved.name,
    )
