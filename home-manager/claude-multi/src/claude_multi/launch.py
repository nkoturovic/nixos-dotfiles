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

G0' trust model: the contract's recorded ``resolved_path`` is verified
directly — absolute, existing, regular, non-symlink, executable, basename
equal to the validated version, full SHA-256 match. The configured symlink is
resolved only for an informational advisory; a moved symlink never blocks
launch while the inspected artifact remains intact, and a missing, symlinked,
non-executable, wrong-basename, or hash-mismatched artifact blocks before any
readiness check, state write, prompt write, or pointer update.

Accepted limitation: verification is path-based, so a determined local
attacker could swap the artifact between verification and execve (TOCTOU).
This is the blueprint's accepted trust-model boundary; an fd/memfd-based
redesign is deliberately out of scope for G0'.

Cleanup contract: if ``os.execve`` raises ``OSError``, cleanup is
action-aware (SPEC section 3) and ownership-guarded (audit L2/L4). Fresh
launches forget the just-persisted record, compare-and-clear a per-CWD
pointer set by this launch, and remove the attempted scope, so a nonexistent
session is never offered later; the fresh UUID is unknowable, so this path
is inherently safe. Resume launches (including a legacy record's durable
upgrade) preserve the pointer via compare-and-restore and restore the exact
pre-launch record bytes — but only while the on-disk record still equals the
exact bytes this launch committed (CAS-by-own-write, captured at save time);
when a newer attempt has committed, it owns the record and the scope and
cleanup touches neither. Scope handling on a resume restore converges to
record authority (audit L4): a durable prior record's scope is recompiled
from the restored record and the installed catalog and rewritten — never
deleted — while a legacy upgrade removes the attempted scope (no prior scope
existed). ``SystemExit``/signal identity passes through untouched, and
non-OS errors are not caught.
"""

from __future__ import annotations

import hashlib
import http.client
import os
import re
import stat
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from . import scope, state, strict_json
from .compiler import CompileResult
from .sessions import UUID4, SessionStore

if TYPE_CHECKING:
    from .catalog import Catalog


class LaunchError(RuntimeError):
    """Raised when launch cannot proceed (fail closed, no effects leaked)."""


_TOKEN_SHAPE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class BinaryStatus:
    """Immutable trust status for the inspected Claude binary (G0')."""

    inspected_path: Path
    validated_version: str
    sha256: str
    configured_path: Path
    configured_target: Path | None
    configured_matches: bool
    advisory: str | None


def resolve_claude(native_contract: dict[str, Any]) -> BinaryStatus:
    """Verify the contract's recorded ``resolved_path`` directly (G0').

    The inspected artifact must be an absolute, existing, regular,
    non-symlink, executable file whose basename equals the validated version
    and whose full SHA-256 matches the trusted record; any deviation fails
    closed before any readiness check or state write. The configured symlink
    is resolved only for the informational status object: a moved symlink
    never blocks while the inspected artifact remains intact.
    """

    record = native_contract["claude"]
    executable = record["executable"]
    expected = Path(executable["resolved_path"])
    if not expected.is_absolute():
        raise LaunchError(f"inspected Claude path {expected} is not absolute")
    if not os.path.lexists(expected):
        raise LaunchError(
            f"inspected Claude artifact {expected} is missing; re-run the "
            "native-contract inspection against the installed version"
        )
    if expected.is_symlink():
        raise LaunchError(
            f"inspected Claude artifact {expected} is a symlink; the trusted "
            "record must name the regular installed file"
        )
    if not expected.is_file():
        raise LaunchError(
            f"inspected Claude artifact {expected} is not a regular file"
        )
    if expected.name != record["validated_version"]:
        raise LaunchError(
            f"inspected Claude version {expected.name!r} does not match trusted "
            f"version {record['validated_version']!r}"
        )
    # Cheap checks before the deliberate full-file hash.
    if not os.access(expected, os.X_OK):
        raise LaunchError(f"inspected Claude artifact {expected} is not executable")
    digest = hashlib.sha256(expected.read_bytes()).hexdigest()
    if digest != executable["sha256"]:
        raise LaunchError(
            "inspected Claude artifact content hash does not match the trusted "
            "native-contract record; re-run the native-contract inspection"
        )

    configured = Path(executable["configured_path"])
    target: Path | None = None
    if os.path.lexists(configured):
        target = Path(os.path.realpath(configured))
    matches = target == expected
    advisory: str | None = None
    if not matches:
        shown = "unresolvable" if target is None else str(target)
        advisory = (
            f"configured symlink {configured} resolves to {shown}, not the "
            f"inspected artifact {expected}"
        )
    return BinaryStatus(
        inspected_path=expected,
        validated_version=record["validated_version"],
        sha256=digest,
        configured_path=configured,
        configured_target=target,
        configured_matches=matches,
        advisory=advisory,
    )


def doctor_binary_report(
    native_contract: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """(problems, info) for the managed binary via the same resolver as launch.

    Doctor parity: any failure here is exactly the failure launch would hit,
    so Doctor never reports Ready when binary verification would fail.
    """

    try:
        status = resolve_claude(native_contract)
    except LaunchError as exc:
        return [f"managed Claude binary: {exc}"], []
    except (KeyError, TypeError, AttributeError) as exc:
        # Direct-API callers may pass a malformed record; catalog loading
        # still enforces the closed schema on the normal path.
        return [
            "managed Claude binary: malformed native-contract record "
            f"({exc!r}); re-run the native-contract inspection"
        ], []
    info = [
        f"Managed Claude {status.validated_version} verified "
        f"(sha256 {status.sha256[:12]}..., {status.inspected_path})."
    ]
    if status.configured_matches:
        info.append(
            f"Configured symlink {status.configured_path} resolves to the "
            "inspected artifact."
        )
    else:
        target = (
            "unresolvable"
            if status.configured_target is None
            else str(status.configured_target)
        )
        note = ""
        if (
            status.configured_target is not None
            and status.configured_target.name != status.validated_version
        ):
            note = (
                f"; newer/different unqualified version "
                f"{status.configured_target.name!r} present"
            )
        info.append(
            f"Configured symlink {status.configured_path} resolves to {target} "
            f"(advisory drift only{note}); the inspected artifact remains the "
            "trust anchor."
        )
    return [], info


@dataclass(frozen=True)
class DaemonStatus:
    """Best-effort shared-daemon status: existence/pid inspection only."""

    state: str  # "present" | "absent" | "unsupported"
    summary: str
    pid: int | None = None
    version: str | None = None


def _metadata_pid(path: Path) -> int | None:
    """Trivially read a daemon pid: plain JSON, informational-only.

    Any failure (missing, unreadable, unparseable, wrong shape) yields None;
    this line never gates anything, so no defensive parsing is warranted.
    """

    try:
        document = strict_json.loads(path.read_bytes())
    except Exception:
        return None
    pid = document.get("pid") if isinstance(document, dict) else None
    if not isinstance(pid, int) or isinstance(pid, bool) or pid < 1:
        return None
    return pid


def inspect_shared_daemon(
    *,
    domain_dir: Path | None = None,
    uid: int | None = None,
) -> DaemonStatus:
    """Best-effort shared-daemon status: existence plus pid when exposed.

    Never sends a request to the daemon and never reads transcripts. The
    domain must be a real directory owned by the effective uid to count as
    present; anything else reports absent. Informational only.
    """

    if uid is None:
        uid_getter = getattr(os, "geteuid", None) or getattr(os, "getuid", None)
        if uid_getter is None:
            return DaemonStatus(
                state="unsupported",
                summary="daemon status inspection is unsupported on this platform",
            )
        uid = uid_getter()
    base = domain_dir if domain_dir is not None else Path(f"/tmp/cc-daemon-{uid}")
    try:
        base_stat = os.lstat(base)
    except OSError:
        return DaemonStatus(
            state="absent",
            summary=f"no shared-daemon domain at {base}; status not exposed",
        )
    if not stat.S_ISDIR(base_stat.st_mode) or base_stat.st_uid != uid:
        return DaemonStatus(
            state="absent",
            summary=f"no uid-owned shared-daemon domain at {base}; status not exposed",
        )
    pid = _metadata_pid(base / "metadata.json")
    detail = f"pid {pid}" if pid is not None else "pid not exposed"
    return DaemonStatus(
        state="present",
        summary=f"shared-daemon domain present at {base} ({detail})",
        pid=pid,
    )


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


def _resume_failure_scope(
    store: SessionStore,
    session_id: str,
    prior_record_bytes: bytes,
    result: CompileResult,
    trusted: "Catalog | None",
) -> None:
    """Converge the live scope after a failed durable resume exec (audit L4).

    Runs inside the lifecycle lock after the prior record bytes have been
    restored (CAS matched). A durable prior record keeps a scope: with the
    installed ``trusted`` catalog the scope is recompiled from the restored
    record (record authority) and rewritten, never deleted. Without a
    catalog, the attempted scope is kept only when it already embodies the
    restored record's composition (same composition digest); a scope that
    contradicts the record is removed and left for ``converge`` to rebuild.
    A legacy prior record has no scope authority: the attempted scope is this
    launch's own creation and is removed, restoring the pre-launch state.
    """

    prior_record: dict[str, Any] | None = None
    try:
        parsed = strict_json.loads(prior_record_bytes)
    except Exception:
        # The cleanup path must never mask the exec's OSError; an unreadable
        # prior record degrades to the legacy branch below.
        parsed = None
    if isinstance(parsed, dict):
        prior_record = parsed

    if prior_record is None or prior_record.get("mode") != "durable":
        scope.remove_scope(store.root, session_id)
        return

    if trusted is not None:
        # Local import: launch stays layered below transition.
        from . import transition

        try:
            expected = transition._expected_plan(prior_record, trusted)
        except Exception:
            # The installed catalog no longer resolves the recorded
            # composition: fail closed to record-without-scope (converge
            # rebuilds deterministically) instead of masking the OSError.
            scope.remove_scope(store.root, session_id)
        else:
            scope.write_scope(store.root, session_id, expected)
        return

    if prior_record.get("composition_hash") == strict_json.bundle_digest(
        result.snapshot
    ):
        # Compiled from the record's own composition against the installed
        # catalog, the attempted scope already IS the record-authoritative
        # scope; deleting it would destroy the valid pre-existing scope.
        return
    scope.remove_scope(store.root, session_id)


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
    trusted: "Catalog | None" = None,
) -> Any:
    """Execute a compiled launch: readiness → state → execve. No return on success.

    Ordering contract: the external executable is verified first, loopback
    readiness runs before any session state write, the exact-``cm-*``
    collision gate and the durable scope write sit between readiness and the
    record save, the snapshot is persisted atomically, and only then does
    execve replace this process. The fresh/resume existence guards and the
    pre-launch record/pointer byte captures run inside the per-session
    lifecycle lock (audit L1) so a concurrent attempt can never commit
    between the check and the mutation. ``trusted`` (the installed catalog)
    enables the audit-L4 record-authoritative scope recompile when a durable
    resume's exec fails.
    """

    status = resolve_claude(native_contract)
    executable = status.inspected_path
    token = readiness(gateway, home=home, health_get=health_get)

    action_kind = result.session_action.kind
    session_id = record.get("session_id")
    if not isinstance(session_id, str) or not UUID4.fullmatch(session_id):
        raise LaunchError(f"record session_id {session_id!r} is not a UUIDv4")
    if action_kind not in {"fresh", "resume"}:
        raise LaunchError(f"unsupported session action {action_kind!r}")
    if result.session_action.session_id != session_id:
        raise LaunchError(
            f"compiled session action targets {result.session_action.session_id!r}, "
            f"but the record targets {session_id!r}"
        )

    durable = result.durable
    if durable:
        if result.scope_plan is None or result.scope_dir is None:
            raise LaunchError(
                "durable launch requires a compiled scope plan and scope directory"
            )
        expected_scope = scope.scope_dir(store.root, session_id)
        if result.scope_dir != expected_scope:
            raise LaunchError(
                f"compiled scope directory {result.scope_dir} does not match the "
                f"session store scope {expected_scope}"
            )
        collisions = scope.find_cm_collisions(
            record["cwd"],
            result.passthrough_add_dirs,
            result.scope_plan.agent_names,
        )
        if collisions:
            formatted = "; ".join(
                f"{path} (agent name {name!r})" for path, name in collisions
            )
            raise LaunchError(
                "exact cm-* agent name collision outside the managed scope: "
                f"{formatted}; rename the colliding project agent — it would "
                "silently shadow a guaranteed managed definition"
            )

    # The lifecycle lock serializes scope/record/pointer mutation against a
    # concurrent launcher operating on the same UUID (finisher finding). The
    # existence guards and the pre-launch byte captures run INSIDE the lock
    # (audit L1): authority sampled before the lock is stale the moment a
    # concurrent attempt commits. The lock is released BEFORE execve so the
    # lock fd never leaks into the Claude process; the OSError cleanup
    # re-acquires it and restores via compare-and-swap on the exact record
    # bytes this launch wrote, so a newer launch always wins.
    scope_written = False
    committed_bytes: bytes | None = None
    lock = store.lifecycle_lock(session_id)
    lock.acquire(blocking=True)
    try:
        # Pre-launch authority, captured under the lock before any
        # prompt/scope/record mutation. Fresh IDs must still be unused;
        # resumes require a pre-existing record and preserve the exact prior
        # pointer state if execve fails.
        pre_existing: bytes | None = store.read_record_bytes(session_id)
        prior_pointer: bytes | None = None
        if action_kind == "fresh":
            if pre_existing is not None:
                raise LaunchError(f"fresh session {session_id} already has a record")
        else:
            if pre_existing is None:
                raise LaunchError(f"resume session {session_id} has no managed record")
            # Stale-relaunch guard (final audit L1): the record on disk must
            # not have advanced beyond the one this launch was compiled for.
            current = strict_json.loads(pre_existing)
            if (
                isinstance(current, dict)
                and current.get("scope_generation", 0) > record["scope_generation"]
            ):
                raise LaunchError(
                    f"session {session_id} advanced to generation "
                    f"{current['scope_generation']} concurrently; this launch "
                    f"was compiled for generation {record['scope_generation']} "
                    "— re-run the transition"
                )
            prior_pointer = store.read_pointer_bytes(record["cwd"])

        # Contingency is the only lead delivery: the prompt file is always
        # written — after the guards, so a refused launch leaves nothing.
        state.atomic_write(
            result.lead_prompt_path, result.lead_prompt.encode("utf-8")
        )

        if durable:
            scope.write_scope(store.root, session_id, result.scope_plan)
            scope_written = True
        store.save(record)
        # The exact bytes this launch committed: the cleanup's ownership
        # token (CAS-by-own-write, audit L2).
        committed_bytes = store.read_record_bytes(session_id)
        store.update_last(record["cwd"], session_id)
    finally:
        lock.release()

    base_environ = dict(os.environ if environ is None else environ)
    for key in result.env_unset:
        base_environ.pop(key, None)
    final_env = {**base_environ, **result.env_set, "ANTHROPIC_AUTH_TOKEN": token}
    argv = [str(executable), *result.argv]

    # Claude locates transcripts under the session's ORIGINAL project
    # directory; resuming from another cwd reports the session as missing.
    # Adopted records carry the decoded original cwd; managed records carry
    # their launch cwd. Enter it before exec when it differs. A returning
    # execve (injected/test boundary) restores the launcher's cwd; the real
    # one never returns.
    original_cwd = os.getcwd()
    record_cwd = record.get("cwd")
    if (
        isinstance(record_cwd, str)
        and record_cwd
        and record_cwd != original_cwd
        and os.path.isdir(record_cwd)
    ):
        try:
            os.chdir(record_cwd)
        except OSError as exc:
            raise LaunchError(
                f"cannot enter the session's project directory "
                f"{record_cwd}: {exc}"
            ) from exc
    try:
        outcome = execve(str(executable), argv, final_env)
        os.chdir(original_cwd)
        return outcome
    except OSError:
        # The session never started; converge state per action kind, then
        # re-raise. See the module docstring for the cleanup contract.
        lock.acquire(blocking=True)
        try:
            if action_kind == "resume":
                # CAS-by-own-write (audit L2): restore only while the record
                # is still exactly what THIS launch committed. A newer
                # attempt that committed in between owns the record AND the
                # scope — touch neither.
                if store.read_record_bytes(session_id) == committed_bytes:
                    store.restore_record_bytes(session_id, pre_existing)
                    if scope_written:
                        _resume_failure_scope(
                            store, session_id, pre_existing, result, trusted
                        )
                    # The pointer rollback belongs to the same ownership
                    # check: when a newer attempt owns the record, its
                    # pointer update stands (final audit L2).
                    store.restore_pointer_bytes(
                        record["cwd"], session_id, prior_pointer
                    )
            else:
                # Fresh UUIDs are unknowable, but compare anyway before
                # deleting: a record this launch no longer owns is left alone.
                if store.read_record_bytes(session_id) == committed_bytes:
                    store.forget(session_id)
                    store.clear_last(record["cwd"], session_id)
                    if scope_written:
                        scope.remove_scope(store.root, session_id)
        finally:
            lock.release()
        raise
