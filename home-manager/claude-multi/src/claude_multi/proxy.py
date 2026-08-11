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
import sys
import re
import secrets
import shutil
import urllib.request
from pathlib import Path
from typing import Any, Callable

from . import catalog as catalog_mod
from . import custom as custom_mod
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


def parse_secret_env_bytes(raw: bytes, path: Path) -> dict[str, str]:
    """Strict assignment parsing of env-file BYTES (shared read/write rule).

    Read path (``parse_secret_env``) and write path (``set_secret_value``'s
    candidate validation) go through this one parser so the two can never
    diverge. Never shell-sourced; error messages carry line numbers, never
    secret bytes.
    """

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        # Same failure class as an unreadable file: consumers must see one
        # exception type, and the codec message never carries secret bytes.
        raise ProxyError(f"secret env file {path} is not valid UTF-8") from exc
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
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


def parse_secret_env(path: Path) -> dict[str, str]:
    """Strict assignment parsing of the secret env file. Never shell-sourced."""

    try:
        raw = state.read_private(path)
    except state.StateError as exc:
        raise ProxyError(f"secret env file {path} unavailable or unsafe: {exc}") from exc
    return parse_secret_env_bytes(raw, path)


def resolve_secret(
    name: str, *, environ: dict[str, str] | None = None
) -> str | None:
    """Resolve an env:NAME reference from the secret env file; None if absent."""

    path = secret_env_path(environ)
    if not path.exists():
        return None
    return parse_secret_env(path).get(name)


# Verified provider model-listing support (2026-08-10, approval-gated probes):
# kimi answers the Anthropic-shape GET {base}/v1/models; the Qwen Token Plan
# apps/anthropic path returns 404 "Not support"; OAuth pools have no direct
# API credential to list with. Any other DIRECT provider (incl. customs) is
# attempted with the same Anthropic shape and falls back to manual entry.
_LISTING_SUPPORT = {
    "kimi": "anthropic-v1-models",
    "qwen": "unsupported",
}


def list_provider_models(
    provider_id: str,
    providers: dict[str, Any],
    *,
    environ: dict[str, str] | None = None,
    fetch: Callable[[str, dict[str, str]], bytes] | None = None,
    timeout: float = 20.0,
) -> list[dict[str, Any]]:
    """List the models a provider advertises — an EXPLICIT provider call.

    Runs only on explicit operator invocation (the `discover` command, or a
    confirmed fetch in the providers pane) — the invocation is the per-call
    approval; never from doctor or any automatic path. Secrets are read for
    the request and never logged, returned, or embedded in errors.
    """

    provider = providers[provider_id]
    transport = provider["transport"]
    support = _LISTING_SUPPORT.get(provider_id)
    if support == "unsupported":
        raise ProxyError(
            f"provider {provider_id!r} does not support model listing "
            "(verified 2026-08-10: its Anthropic path answers 404 'Not support')"
        )
    if transport["kind"] == "oauth-pool":
        raise ProxyError(
            f"provider {provider_id!r} is an OAuth pool — there is no "
            "direct API credential to list models with"
        )
    # support is "anthropic-v1-models" (verified) or None (attempt).
    secret_ref = transport["auth"]["secret_ref"]
    env_name = secret_ref.removeprefix("env:")
    secret = resolve_secret(env_name, environ=environ)
    if secret is None:
        raise ProxyError(
            f"provider {provider_id!r} listing needs {secret_ref} in the "
            "secret env file first"
        )
    url = transport["base_url"].rstrip("/") + "/v1/models"
    auth = transport["auth"]
    if auth["kind"] == "bearer":
        headers = {"Authorization": f"Bearer {secret}"}
    else:
        headers = {auth["header"]: secret}
    headers["anthropic-version"] = "2023-06-01"

    def _fetch(target: str, request_headers: dict[str, str]) -> bytes:
        request = urllib.request.Request(target, headers=request_headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            # Bound the remote body: strict_json's 4 MiB limit must not be
            # reachable only after an unbounded allocation.
            limit = strict_json.DEFAULT_LIMITS.max_bytes
            return response.read(limit + 1)

    try:
        raw = (fetch or _fetch)(url, headers)
    except Exception as exc:
        # Never interpolate the exception: a crafted or odd error could
        # carry request details. Type name only; the secret never leaves.
        raise ProxyError(
            f"provider {provider_id!r} model listing failed "
            f"({type(exc).__name__})"
        ) from exc
    payload = strict_json.loads(raw)
    entries = []
    for item in payload.get("data", []):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        entry = {
            "id": item["id"],
            "display_name": item.get("display_name", ""),
            "context_length": item.get("context_length"),
        }
        efforts = item.get("think_efforts")
        if isinstance(efforts, dict) and efforts.get("valid_efforts"):
            entry["think_efforts"] = list(efforts["valid_efforts"])
        entries.append(entry)
    return entries


def set_secret_value(path: Path, name: str, value: str) -> int:
    """Insert or replace ``NAME=value`` in the secret env file (018).

    The merge collapses every existing assignment of the name (a file with
    duplicates is rejected by the strict parser, so a save repairs it to
    one), preserves each line's ``export`` prefix and spacing exactly, and
    leaves unrelated lines untouched (line endings are LF-normalized and a
    final newline is guaranteed). The candidate is re-validated through the
    strict env parser before the atomic 0600 write, so a "saved" result is
    always a consumable file. The whole read-modify-write runs under the
    file's ``state.FileLock`` — concurrent saves never lose each other's
    keys. The value is shape-validated and never logged or returned — only
    its length, so callers can confirm without echoing.
    """

    if not re.fullmatch(r"[A-Z0-9_]+", name):
        raise ProxyError(f"invalid secret variable name {name!r}")
    if not value or not _VALUE_SHAPE.fullmatch(value):
        raise ProxyError(
            "secret value has an unsupported shape "
            "(letters, digits, and . _ ~ + / = @ : - only)"
        )
    state.ensure_private_dir(path.parent)
    lock = state.FileLock(path)
    lock.acquire(blocking=True)
    try:
        out_lines: list[str] = []
        if os.path.lexists(path):
            try:
                raw = state.read_private(path)
            except state.StateError as exc:
                raise ProxyError(
                    f"secret env file unavailable or unsafe: {exc}"
                ) from exc
            try:
                out_lines = raw.decode("utf-8").splitlines()
            except UnicodeDecodeError as exc:
                raise ProxyError(
                    f"secret env file {path} is not valid UTF-8"
                ) from exc
        replaced = False
        drop: set[int] = set()
        for index, line in enumerate(out_lines):
            match = _ASSIGNMENT.match(line)
            if match and match.group(1) == name:
                if replaced:
                    # Collapse duplicate assignments of the repaired key —
                    # the strict parser rejects duplicates, so keeping a
                    # second line would leave the file broken after "saved".
                    drop.add(index)
                    continue
                prefix = line[: match.start(1)]  # export/tab/spacing exactly
                out_lines[index] = f"{prefix}{name}={value}"
                replaced = True
        out_lines = [line for index, line in enumerate(out_lines) if index not in drop]
        if not replaced:
            out_lines.append(f"{name}={value}")
        candidate = ("\n".join(out_lines) + "\n").encode("utf-8")
        # A successful save must yield a consumable file: run the strict
        # parser over the candidate (catches pre-existing malformed or
        # duplicate lines of OTHER keys) before writing anything.
        parse_secret_env_bytes(candidate, path)
        state.atomic_write(path, candidate)
        return len(value)
    finally:
        lock.release()


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
                f"unavailable (secret env file {path} missing)"
            )
            continue
        if parsed is None and parse_error is None:
            try:
                parsed = parse_secret_env(path)
            except ProxyError as exc:
                parse_error = str(exc)
        if parse_error is not None:
            problems.append(
                f"provider {display} ({provider_id}): secret env file {path} "
                f"unsafe or malformed: {parse_error}"
            )
            continue
        if name not in parsed:
            problems.append(
                f"provider {display} ({provider_id}): required variable {name} "
                f"missing from the secret env file {path}"
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
        try:
            token = state.read_private(token_path).decode("utf-8").strip()
        except state.StateError as exc:
            # A wrong-mode or symlinked token file must surface as a clean
            # one-line error, not a traceback that crash-loops the unit (N1).
            raise ProxyError(f"gateway key file is unusable: {exc}") from exc
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
    """Render the complete gateway config atomically (mode 0600, outside repo).

    Token creation and config write are serialized under one lock: two
    concurrent init/run invocations can never split a fresh token from the
    config that carries it.
    """

    state.ensure_private_dir(config_dir(home))
    lock = state.FileLock(config_dir(home) / "api-key")
    lock.acquire(blocking=True)
    try:
        bundle = catalog_mod.load_catalog(assets_root(environ))
        token = ensure_token(home)
        resolve = resolver or (lambda name: resolve_secret(name, environ=environ))
        # Custom providers/models (020) render as ordinary direct routes;
        # the merge keeps the served/drift radar covering them.
        docs = custom_mod.merge_docs(
            bundle.docs,
            custom_mod.load_registry(
                dict(os.environ if environ is None else environ)
            ),
        )
        result = render_mod.render_config(
            docs["gateway"],
            docs["providers"]["providers"],
            docs["models"]["models"],
            home=home,
            gateway_token=token,
            resolve_secret=resolve,
        )
        target = config_dir(home) / "config.yaml"
        state.atomic_write(target, result.yaml.encode("utf-8"))
        return target, result
    finally:
        lock.release()


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
    print(
        "note: the daemon does not hot-reload a replaced config on 7.2.80 — "
        "apply it with `systemctl --user restart cli-proxy-api` "
        "(Home Manager switch does this for you).",
        file=sys.stderr,
    )
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
    target, result = render_runtime_config(home, environ=environ)
    for item in result.unavailable:
        # Never silently start the gateway with a provider missing its
        # secret — cmd_init surfaces this; run/login must too.
        print(
            f"provider unavailable: {item['provider']} ({item['reason']})",
            file=sys.stderr,
        )
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
    target, result = render_runtime_config(home, environ=environ)
    for item in result.unavailable:
        print(
            f"provider unavailable: {item['provider']} ({item['reason']})",
            file=sys.stderr,
        )
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
    except state.StateError as exc:
        # Filesystem-hardening refusals (wrong modes, symlinked paths) are
        # one-line errors too — never a traceback under the systemd unit.
        print(f"claude-multi-proxy: {exc}", file=sys.stderr)
        return 1
