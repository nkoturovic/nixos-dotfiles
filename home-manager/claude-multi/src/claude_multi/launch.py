"""Launch execution for claude-multi v2.

Consumes a pure CompileResult, resolves and verifies the externally installed
Claude executable against the trusted native-contract record (offline
path/hash inspection only in this workflow), performs loopback-only readiness
checks after explicit confirmation, persists the session snapshot atomically,
and replaces the process via an injectable ``os.execve`` boundary. No resident
wrapper survives the exec.

Hashing contract: ``resolve_claude`` rehashes the full external binary
(≈267 MB, roughly 0.3–1 s) on every launch as the trust anchor. This is
deliberate; any cache must be separately reviewed and must still rehash fully
whenever the resolved-path identity changes.

Cleanup contract: if ``os.execve`` raises ``OSError``, the just-persisted
session record is forgotten and a per-CWD pointer set by this launch is
cleared (compare-and-clear), so a nonexistent session is never offered later.
``SystemExit``/signal identity passes through untouched, and non-OS errors
are not caught.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from . import state
from .compiler import CompileResult
from .sessions import SessionStore


class LaunchError(RuntimeError):
    """Raised when launch cannot proceed (fail closed, no effects leaked)."""


_TOKEN_SHAPE = re.compile(r"^[0-9a-f]{64}$")


def resolve_claude(native_contract: dict[str, Any]) -> Path:
    """Resolve and verify the external Claude executable against the record.

    Symlinks are resolved, then the resolved path, version basename, and
    content hash must match the trusted record exactly; any mismatch fails
    closed before any state write or health check.
    """

    record = native_contract["claude"]
    executable = record["executable"]
    configured = Path(executable["configured_path"])
    resolved = Path(os.path.realpath(configured))
    expected = Path(executable["resolved_path"])
    if resolved != expected:
        raise LaunchError(
            f"Claude executable resolves to {resolved}, expected {expected}; "
            "the external installation drifted from the trusted native-contract "
            "record"
        )
    if not resolved.is_file():
        raise LaunchError(f"Claude executable {resolved} is not a regular file")
    if resolved.name != record["validated_version"]:
        raise LaunchError(
            f"Claude executable version {resolved.name!r} does not match trusted "
            f"version {record['validated_version']!r}"
        )
    digest = hashlib.sha256(resolved.read_bytes()).hexdigest()
    if digest != executable["sha256"]:
        raise LaunchError(
            "Claude executable content hash does not match the trusted "
            "native-contract record; re-run the native-contract inspection"
        )
    if not os.access(resolved, os.X_OK):
        raise LaunchError(f"Claude executable {resolved} is not executable")
    return resolved


def _default_health_get(base_url: str, health_path: str, timeout: float = 1.5) -> int:
    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme != "http" or parts.hostname != "127.0.0.1":
        raise LaunchError(f"gateway base_url {base_url!r} is not loopback http")
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
    try:
        connection.request("GET", health_path)
        return connection.getresponse().status
    finally:
        connection.close()


def check_readiness(
    gateway: dict[str, Any],
    *,
    home: Path | None = None,
    health_get: Callable[[str, str], int] | None = None,
) -> str:
    """Loopback-only readiness: gateway token file + /healthz. Never upstream."""

    gateway_info = gateway["gateway"]
    base = home if home is not None else Path.home()
    token_path = base / gateway_info["token_file"]
    try:
        raw = state.read_private(token_path)
    except state.StateError as exc:
        raise LaunchError(f"gateway key file unavailable or unsafe: {exc}") from exc
    token = raw.decode("utf-8").strip()
    if not _TOKEN_SHAPE.fullmatch(token):
        raise LaunchError("gateway key file has an invalid token shape")
    getter = health_get or _default_health_get
    try:
        status = getter(gateway_info["base_url"], gateway_info["health_path"])
    except LaunchError:
        raise
    except Exception as exc:  # connection refused, timeout, etc.
        raise LaunchError(f"local gateway health check failed: {exc}") from exc
    if status != 200:
        raise LaunchError(f"local gateway health check returned status {status}")
    return token


def perform_launch(
    result: CompileResult,
    *,
    record: dict[str, Any],
    store: SessionStore,
    native_contract: dict[str, Any],
    gateway: dict[str, Any],
    readiness: Callable[..., str] = check_readiness,
    execve: Callable[[str, list[str], dict[str, str]], Any] = os.execve,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    health_get: Callable[[str, str], int] | None = None,
) -> Any:
    """Execute a compiled launch: readiness → state → execve. No return on success.

    Ordering contract: the external executable is verified first, loopback
    readiness runs before any session state write, the snapshot is persisted
    atomically, and only then does execve replace this process.
    """

    executable = resolve_claude(native_contract)
    token = readiness(gateway, home=home, health_get=health_get)

    if result.lead_prompt is not None and result.lead_prompt_path is not None:
        state.atomic_write(
            result.lead_prompt_path, result.lead_prompt.encode("utf-8")
        )
    store.save(record)
    store.update_last(record["cwd"], record["session_id"])

    base_environ = dict(os.environ if environ is None else environ)
    for key in result.env_unset:
        base_environ.pop(key, None)
    final_env = {**base_environ, **result.env_set, "ANTHROPIC_AUTH_TOKEN": token}
    argv = [str(executable), *result.argv]
    try:
        return execve(str(executable), argv, final_env)
    except OSError:
        # The session never started: forget only this launch's record and
        # clear only a pointer that still refers to it, then re-raise.
        store.forget(record["session_id"])
        store.clear_last(record["cwd"], record["session_id"])
        raise
