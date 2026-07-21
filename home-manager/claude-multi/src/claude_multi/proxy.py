"""Runtime proxy control for claude-multi v2.

Preserves the v1 control surface — ``init``, ``status``, ``run``,
``claude-login``, ``codex-device-login`` — on top of the Phase 4 renderer.
The existing auth root (``~/.local/share/claude-multi``) and OAuth records
are preserved untouched. Provider ``env:NAME`` references resolve from the
existing mode-restricted secret env file via strict assignment parsing — no
shell sourcing, no eval, no value ever printed. The complete gateway config
renders atomically to a mode-0600 artifact outside the repository; ``run``
and the login helpers initialize safely, then ``execve`` the pinned
CLIProxyAPI with no resident wrapper. The obsolete v1 validation-resume
commands and state are gone.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
from pathlib import Path
from typing import Any, Callable

from . import catalog as catalog_mod
from . import launch as launch_mod
from . import render as render_mod
from . import state, strict_json


class ProxyError(RuntimeError):
    """Raised on any proxy-control failure (fail closed, secrets redacted)."""


COMMANDS = ("init", "status", "run", "claude-login", "codex-device-login")
LOGIN_FLAGS = {
    "claude-login": "--claude-login",
    "codex-device-login": "--codex-device-login",
}

_ASSIGNMENT = re.compile(r"^(?:export[ \t]+)?([A-Z0-9_]+)[ \t]*=[ \t]*(.*?)[ \t]*$")
_VALUE_SHAPE = re.compile(r"^[A-Za-z0-9._~+/=@:-]+$")
_TOKEN_SHAPE = re.compile(r"^[0-9a-f]{64}$")


def _home(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("HOME", str(Path.home())))


def state_dir(home: Path) -> Path:
    return home / ".local" / "share" / "claude-multi"


def config_dir(home: Path) -> Path:
    return home / ".config" / "claude-multi"


def secret_env_path(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("CLAUDE_MULTI_SECRET_ENV")
    if override:
        return Path(override)
    return _home(env) / ".config" / "secrets" / "claude.env"


def assets_root(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("CLAUDE_MULTI_ASSETS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2]


def parse_secret_env(path: Path) -> dict[str, str]:
    """Strict assignment parsing of the secret env file. Never shell-sourced."""

    try:
        raw = state.read_private(path)
    except state.StateError as exc:
        raise ProxyError(f"secret env file {path} unavailable or unsafe: {exc}") from exc
    values: dict[str, str] = {}
    for number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if not match:
            raise ProxyError(f"{path}:{number}: malformed assignment line")
        name, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not value or not _VALUE_SHAPE.fullmatch(value):
            raise ProxyError(f"{path}:{number}: unsupported characters in value")
        if name in values:
            raise ProxyError(f"{path}:{number}: duplicate assignment for {name}")
        values[name] = value
    return values


def resolve_secret(
    name: str, *, environ: dict[str, str] | None = None
) -> str | None:
    """Resolve an env:NAME reference from the secret env file; None if absent."""

    path = secret_env_path(environ)
    if not path.exists():
        return None
    return parse_secret_env(path).get(name)


def selected_secret_problems(
    resolved: Any,
    models: dict[str, Any],
    providers: dict[str, Any],
    *,
    environ: dict[str, str] | None = None,
) -> list[str]:
    """Offline secret readiness for providers selected by lead/variants.

    Only providers actually selected are checked; direct transports need a
    resolvable ``env:NAME`` while OAuth pools and no-secret providers stay
    usable. Errors are redacted: display/reference names only, never values.
    No provider call, config write, token read/create, or readiness probe.
    """

    selected: list[str] = []
    seen: set[str] = set()
    for model_id in [resolved.lead.model, *(variant.model for variant in resolved.variants)]:
        provider_id = models[model_id]["provider"]
        if provider_id not in seen:
            seen.add(provider_id)
            selected.append(provider_id)

    path = secret_env_path(environ)
    parsed: dict[str, str] | None = None
    parse_error: str | None = None
    problems: list[str] = []
    for provider_id in selected:
        provider = providers[provider_id]
        transport = provider["transport"]
        if transport["kind"] != "direct":
            continue
        display = provider["display"]
        secret_ref = transport["auth"]["secret_ref"]
        name = secret_ref.removeprefix("env:")
        if not path.exists():
            problems.append(
                f"provider {display} ({provider_id}): required secret {secret_ref} "
                "unavailable (secret env file missing)"
            )
            continue
        if parsed is None and parse_error is None:
            try:
                parsed = parse_secret_env(path)
            except ProxyError as exc:
                parse_error = str(exc)
        if parse_error is not None:
            problems.append(
                f"provider {display} ({provider_id}): secret env file unsafe or "
                f"malformed: {parse_error}"
            )
            continue
        if name not in parsed:
            problems.append(
                f"provider {display} ({provider_id}): required variable {name} "
                "missing from the secret env file"
            )
    return problems


def ensure_directories(home: Path) -> None:
    """Create/validate owner-private dirs; existing auth records stay intact."""

    state.ensure_private_dir(state_dir(home))
    state.ensure_private_dir(state_dir(home) / "auth")
    state.ensure_private_dir(state_dir(home) / "traces")
    state.ensure_private_dir(config_dir(home))


def ensure_token(home: Path) -> str:
    """Read or create the mode-0600 local gateway token. Never printed."""

    token_path = config_dir(home) / "api-key"
    if token_path.exists():
        token = state.read_private(token_path).decode("utf-8").strip()
        if not _TOKEN_SHAPE.fullmatch(token):
            raise ProxyError("gateway key file has an invalid token shape")
        return token
    token = secrets.token_hex(32)
    state.atomic_write(token_path, (token + "\n").encode("utf-8"))
    return token


def render_runtime_config(
    home: Path,
    *,
    resolver: Callable[[str], str | None] | None = None,
    environ: dict[str, str] | None = None,
) -> tuple[Path, render_mod.RenderResult]:
    """Render the complete gateway config atomically (mode 0600, outside repo)."""

    bundle = catalog_mod.load_catalog(assets_root(environ))
    token = ensure_token(home)
    resolve = resolver or (lambda name: resolve_secret(name, environ=environ))
    result = render_mod.render_config(
        bundle.docs["gateway"],
        bundle.docs["providers"]["providers"],
        bundle.docs["models"]["models"],
        home=home,
        gateway_token=token,
        resolve_secret=resolve,
    )
    target = config_dir(home) / "config.yaml"
    state.atomic_write(target, result.yaml.encode("utf-8"))
    return target, result


def resolve_proxy_binary(environ: dict[str, str] | None = None) -> Path:
    """Pinned proxy binary: explicit env override, else PATH lookup."""

    env = os.environ if environ is None else environ
    configured = env.get("CLAUDE_MULTI_PROXY_BIN")
    candidate = Path(configured) if configured else None
    if candidate is None:
        found = shutil.which("cli-proxy-api")
        candidate = Path(found) if found else None
    if candidate is None:
        raise ProxyError("CLIProxyAPI binary not found (CLAUDE_MULTI_PROXY_BIN/PATH)")
    resolved = Path(os.path.realpath(candidate))
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise ProxyError(f"CLIProxyAPI binary {resolved} is not an executable file")
    return resolved


def _summarize(result: render_mod.RenderResult, target: Path) -> list[str]:
    digest = strict_json.sha256_hex(result.yaml.encode("utf-8"))[:16]
    lines = [
        f"config: {target} (sha256:{digest})",
        f"providers available: {', '.join(result.available_providers) or 'none'}",
    ]
    for item in result.unavailable:
        lines.append(
            f"provider unavailable: {item['provider']} ({item['reason']})"
        )
    return lines


def cmd_init(args: list[str], *, environ: dict[str, str] | None = None) -> int:
    home = _home(environ)
    ensure_directories(home)
    target, result = render_runtime_config(home, environ=environ)
    for line in _summarize(result, target):
        print(line)
    return 0


def cmd_status(
    args: list[str],
    *,
    environ: dict[str, str] | None = None,
    health_get: Callable[[str, str], int] | None = None,
) -> int:
    """Loopback-only status; never initializes and never prints secrets."""

    home = _home(environ)
    initialized = (
        state_dir(home).is_dir()
        and (state_dir(home) / "auth").is_dir()
        and (config_dir(home) / "api-key").is_file()
        and (config_dir(home) / "config.yaml").is_file()
    )
    print(f"runtime: {'initialized' if initialized else 'not initialized'}")
    bundle = catalog_mod.load_catalog(assets_root(environ))
    base_url = bundle.docs["gateway"]["gateway"]["base_url"]
    health_path = bundle.docs["gateway"]["gateway"]["health_path"]
    getter = health_get or launch_mod._default_health_get
    try:
        status = getter(base_url, health_path)
        print(f"proxy: {'running' if status == 200 else f'unhealthy ({status})'}")
    except Exception:
        print("proxy: stopped")
    return 0


def cmd_run(
    args: list[str],
    *,
    environ: dict[str, str] | None = None,
    execve: Callable[[str, list[str], dict[str, str]], Any] = os.execve,
) -> Any:
    """Initialize safely, then execve the pinned proxy. No resident wrapper."""

    home = _home(environ)
    ensure_directories(home)
    target, _result = render_runtime_config(home, environ=environ)
    binary = resolve_proxy_binary(environ)
    env = dict(os.environ if environ is None else environ)
    return execve(str(binary), [str(binary), "--config", str(target), "--local-model"], env)


def cmd_login(
    command: str,
    args: list[str],
    *,
    environ: dict[str, str] | None = None,
    execve: Callable[[str, list[str], dict[str, str]], Any] = os.execve,
) -> Any:
    """Exec the pinned proxy's supported OAuth/device flow via existing auth paths."""

    home = _home(environ)
    ensure_directories(home)
    target, _result = render_runtime_config(home, environ=environ)
    binary = resolve_proxy_binary(environ)
    env = dict(os.environ if environ is None else environ)
    flag = LOGIN_FLAGS[command]
    return execve(
        str(binary), [str(binary), "--config", str(target), "--local-model", flag], env
    )


def main(
    argv: list[str] | None = None,
    *,
    environ: dict[str, str] | None = None,
    execve: Callable[[str, list[str], dict[str, str]], Any] = os.execve,
    health_get: Callable[[str, str], int] | None = None,
) -> Any:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    command, rest = args[0], args[1:]
    if command in ("-v", "--version"):
        from . import __version__

        print(f"claude-multi-proxy {__version__}")
        return 0
    try:
        if command == "init":
            return cmd_init(rest, environ=environ)
        if command == "status":
            return cmd_status(rest, environ=environ, health_get=health_get)
        if command == "run":
            return cmd_run(rest, environ=environ, execve=execve)
        if command in LOGIN_FLAGS:
            return cmd_login(command, rest, environ=environ, execve=execve)
        print(f"claude-multi-proxy: unknown command: {command}", file=sys.stderr)
        return 1
    except ProxyError as exc:
        print(f"claude-multi-proxy: {exc}", file=sys.stderr)
        return 1
