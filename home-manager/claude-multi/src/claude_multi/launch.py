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

from . import scope, sessions, state, strict_json
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


def _version_tuple(value: str) -> tuple[int, ...] | None:
    """Parse an ``X.Y[.Z]`` version name into a comparable tuple, else None."""

    parts = value.split(".")
    if not 2 <= len(parts) <= 3 or not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def repin_suggestion(native_contract: dict[str, Any]) -> str | None:
    """Attention-tier line when the configured symlink is NEWER than the pin.

    The pinned artifact stays the trust anchor either way; this is the
    standing early-warning that a deliberate re-pin (probe + contract
    promotion) is due. Returns None when the binary cannot be verified (the
    problem report carries that instead), when the symlink matches, or when
    the target is not a parseable newer version.
    """

    try:
        status = resolve_claude(native_contract)
    except (LaunchError, KeyError, TypeError, AttributeError):
        return None
    target = status.configured_target
    if target is None or status.configured_matches:
        return None
    pinned = _version_tuple(status.validated_version)
    available = _version_tuple(target.name)
    if pinned is None or available is None or available <= pinned:
        return None
    return (
        f"Claude {target.name} is available at {status.configured_path} while "
        f"the pinned trust anchor is {status.validated_version}; run "
        "`claude-multi update` to re-pin with evidence (offline inspection + "
        "the offline probe suite), effective immediately via the operator "
        "contract override"
    )


def repin_hint(native_contract: dict[str, Any]) -> tuple[str, str] | None:
    """Cheap update-available hint: ``(pinned, available)`` or None.

    Basename + version comparison only — no hashing (the full verification
    runs when the update itself is triggered), so this is safe to compute on
    every TUI render. Returns None when the symlink is missing/unresolvable,
    matches the pin, or is not a parseable newer version.
    """

    try:
        record = native_contract["claude"]
        pinned = _version_tuple(record["validated_version"])
        configured = Path(record["executable"]["configured_path"])
    except (KeyError, TypeError):
        return None
    if pinned is None or not os.path.lexists(configured):
        return None
    target_name = Path(os.path.realpath(configured)).name
    available = _version_tuple(target_name)
    if available is None or available <= pinned:
        return None
    return record["validated_version"], target_name


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
    environ: dict[str, str] | None = None,
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
    if prior_record.get("session_type") == sessions.SESSION_TYPE_ORDINARY:
        if trusted is None:
            scope.remove_scope(store.root, session_id)
            return
        from . import compiler

        try:
            model = trusted.docs["models"]["models"][prior_record["ordinary_model"]]
            expected = scope.compile_ordinary_scope(
                managed_id=session_id,
                hook_command=str(
                    scope.ensure_hook_shim(
                        store.root,
                        scope.resolve_hook_command(environ, trusted.root),
                    )
                ),
                available_models=compiler.direct_profile_selectors(
                    trusted.docs, prior_record["context_profile"]
                ),
                default_model=model["client_selector"],
                launch_epoch=prior_record.get("launch_epoch", 0),
            )
        except Exception:
            scope.remove_scope(store.root, session_id)
        else:
            scope.write_scope(store.root, session_id, expected)
        return

    if trusted is not None:
        # Local import: launch stays layered below transition.
        from . import transition

        try:
            expected = transition._expected_plan(
                prior_record, trusted, state_root=store.root
            )
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


@dataclass
class _CwdLease:
    """Open directory fds make resume-CWD validation race resistant."""

    original_fd: int
    target_fd: int
    original_path: str
    target_path: str
    closed: bool = False

    @classmethod
    def prepare(cls, target: Any) -> "_CwdLease":
        if not isinstance(target, str) or not target.startswith("/"):
            raise LaunchError(
                f"recorded project directory {target!r} is not an absolute path"
            )
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
        original_path = os.getcwd()
        original_fd = os.open(".", flags)
        try:
            target_fd = os.open(target, flags)
        except OSError as exc:
            os.close(original_fd)
            raise LaunchError(
                f"cannot open the session's original project directory {target}: {exc}; "
                "repair the recorded CWD before resuming"
            ) from exc
        lease = cls(original_fd, target_fd, original_path, target)
        try:
            # Prove the directory is enterable before any launch state commit.
            os.fchdir(target_fd)
            os.fchdir(original_fd)
        except OSError as exc:
            lease.close()
            raise LaunchError(
                f"cannot enter the session's original project directory {target}: {exc}; "
                "repair the recorded CWD before resuming"
            ) from exc
        return lease

    def enter(self) -> None:
        os.fchdir(self.target_fd)

    def restore(self) -> None:
        if not self.closed:
            os.fchdir(self.original_fd)

    def close(self) -> None:
        if self.closed:
            return
        os.close(self.target_fd)
        os.close(self.original_fd)
        self.closed = True


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
    allow_model_relaunch: bool = False,
    expected_launch_epoch: int | None = None,
    expected_mutation_token: str | None = None,
    expected_source_scope_generation: int | None = None,
    expected_source_composition_hash: str | None = None,
    precommitted: bool = False,
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
    try:
        stable_id = sessions.managed_id(record)
        runtime_id = sessions.runtime_session_id(record)
    except sessions.SessionError as exc:
        raise LaunchError(str(exc)) from exc
    if action_kind not in {"fresh", "resume"}:
        raise LaunchError(f"unsupported session action {action_kind!r}")
    if result.session_action.managed_id != stable_id:
        raise LaunchError(
            f"compiled session action targets managed session "
            f"{result.session_action.managed_id!r}, but the record belongs to "
            f"{stable_id!r}"
        )
    if result.session_action.runtime_session_id != runtime_id:
        raise LaunchError(
            f"compiled action targets runtime session "
            f"{result.session_action.runtime_session_id!r}, but the record targets "
            f"{runtime_id!r}"
        )

    durable = result.durable
    if durable:
        if result.scope_plan is None or result.scope_dir is None:
            raise LaunchError(
                "durable launch requires a compiled scope plan and scope directory"
            )
        expected_scope = scope.scope_dir(store.root, stable_id)
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

    # Validate and successfully enter the authoritative original CWD before
    # committing prompt/scope/record/pointer state. Open fds keep the later
    # exec-side chdir valid even if a path component is renamed concurrently.
    cwd_lease = _CwdLease.prepare(record.get("cwd"))

    # The lifecycle lock serializes scope/record/pointer mutation against a
    # concurrent launcher operating on the same UUID (finisher finding). The
    # existence guards and the pre-launch byte captures run INSIDE the lock
    # (audit L1): authority sampled before the lock is stale the moment a
    # concurrent attempt commits. The lock fd is O_CLOEXEC, so a successful
    # execve closes it inside the Claude process; the OSError cleanup still
    # holds it and restores via compare-and-swap on the exact record bytes
    # this launch wrote, so a newer launch always wins.
    scope_written = False
    committed_bytes: bytes | None = None
    committed_token: str | None = None
    pre_existing: bytes | None = None
    prior_pointer: bytes | None = None
    lock = store.lifecycle_lock(stable_id)
    lock.acquire(blocking=True)
    try:
        # Pre-launch authority, captured under the lock before any
        # prompt/scope/record mutation. Fresh IDs must still be unused;
        # resumes require a pre-existing record and preserve the exact prior
        # pointer state if execve fails.
        pre_existing = store.read_record_bytes(stable_id)
        committed_record = record
        if action_kind == "fresh":
            if pre_existing is not None:
                raise LaunchError(f"fresh session {stable_id} already has a record")
        else:
            if pre_existing is None:
                raise LaunchError(f"resume session {stable_id} has no managed record")
            try:
                current = store.load(stable_id)
            except sessions.SessionError as exc:
                raise LaunchError(str(exc)) from exc
            if sessions.managed_id(current) != stable_id:
                raise LaunchError(f"session {stable_id} changed identity concurrently")
            if current["session_type"] != record["session_type"]:
                raise LaunchError(f"session {stable_id} changed type concurrently")
            if expected_launch_epoch is not None:
                current_epoch = current.get("launch_epoch", 0)
                current_token = current.get("mutation_token")
                if (
                    current_epoch != expected_launch_epoch
                    or current_token != expected_mutation_token
                ):
                    raise LaunchError(
                        f"session {stable_id} authority changed after preparation; "
                        "prepare the launch again"
                    )
                target_epoch = record.get("launch_epoch", 0)
                expected_target = current_epoch if precommitted else current_epoch + 1
                if target_epoch != expected_target:
                    raise LaunchError(
                        f"session {stable_id} launch epoch is stale; prepare it again"
                    )
            current_runtime = sessions.runtime_session_id(current)
            if current_runtime != result.session_action.runtime_session_id:
                raise LaunchError(
                    f"session {stable_id} now targets runtime {current_runtime}; "
                    "this launch was compiled for "
                    f"{result.session_action.runtime_session_id} — prepare it again"
                )
            if current["cwd"] != record["cwd"]:
                raise LaunchError(
                    f"session {stable_id} CWD changed concurrently from "
                    f"{record['cwd']!r} to {current['cwd']!r}; prepare it again"
                )
            if expected_source_scope_generation is not None:
                if (
                    current.get("scope_generation", 0)
                    != expected_source_scope_generation
                ):
                    raise LaunchError(
                        f"session {stable_id} source generation changed after "
                        "preparation; prepare it again"
                    )
            elif current.get("scope_generation", 0) > record["scope_generation"]:
                raise LaunchError(
                    f"session {stable_id} advanced to generation "
                    f"{current['scope_generation']} concurrently; this launch "
                    f"was compiled for generation {record['scope_generation']} "
                    "— re-run the transition"
                )
            if current["session_type"] == sessions.SESSION_TYPE_MANAGED:
                if expected_source_composition_hash is not None:
                    if (
                        current.get("composition_hash")
                        != expected_source_composition_hash
                    ):
                        raise LaunchError(
                            f"session {stable_id} source composition changed after "
                            "preparation; prepare it again"
                        )
                elif (
                    current.get("scope_generation")
                    == record.get("scope_generation")
                    and current.get("composition_hash")
                    != record.get("composition_hash")
                ):
                    raise LaunchError(
                        f"session {stable_id} composition changed concurrently; "
                        "prepare it again"
                    )
            identity_state = current.get(
                "identity_state", sessions.IDENTITY_UNVERIFIED
            )
            if current.get("pending_forks"):
                raise LaunchError(
                    f"session {stable_id} has an unresolved native fork; adopt the "
                    "fork UUID before resuming the parent"
                )
            if identity_state == sessions.IDENTITY_REPAIR_NEEDED and not (
                allow_model_relaunch
                and "observed_model" in current
                and "observed_cwd" not in current
            ):
                raise LaunchError(
                    f"session {stable_id} needs runtime/CWD repair before launch; "
                    "use `claude-multi sessions relink-runtime`"
                )

            committed_record = sessions.carry_lifecycle_state(record, current)
            if current["session_type"] == sessions.SESSION_TYPE_ORDINARY:
                if allow_model_relaunch:
                    committed_record.pop("observed_model", None)
                    committed_record["identity_state"] = sessions.IDENTITY_UNVERIFIED
                else:
                    if (
                        current["context_profile"] != record["context_profile"]
                        or current["ordinary_model"] != record["ordinary_model"]
                    ):
                        raise LaunchError(
                            f"session {stable_id} ordinary model changed concurrently; "
                            "prepare the resume again"
                        )
                    committed_record["ordinary_model"] = current["ordinary_model"]
                    committed_record["context_profile"] = current["context_profile"]
            elif allow_model_relaunch:
                committed_record.pop("observed_model", None)
                committed_record["identity_state"] = sessions.IDENTITY_UNVERIFIED
            if sessions.runtime_session_id(committed_record) != runtime_id:
                raise LaunchError(
                    f"session {stable_id} runtime identity changed during preparation"
                )
            if committed_record["cwd"] != record["cwd"]:
                raise LaunchError(
                    f"session {stable_id} CWD changed during preparation"
                )
            prior_pointer = store.read_pointer_bytes(
                committed_record["cwd"],
                session_type=committed_record["session_type"],
            )

        committed_token = sessions.new_mutation_token()
        committed_record = {
            **committed_record,
            "mutation_token": committed_token,
        }

        # Managed compositions use the contingency lead appendix; ordinary
        # gateway sessions deliberately have no injected composition prompt.
        if result.write_lead_prompt:
            state.atomic_write(
                result.lead_prompt_path, result.lead_prompt.encode("utf-8")
            )

        if durable:
            scope.write_scope(store.root, stable_id, result.scope_plan)
            scope_written = True
        store.save(committed_record)
        # The exact bytes this launch committed: the cleanup's ownership
        # token (CAS-by-own-write, audit L2).
        committed_bytes = store.read_record_bytes(stable_id)
        # Blocking: the pointer lock is only ever held for one atomic write,
        # so the bounded wait is safe, and a successful launch must never
        # silently lose its `-c` registration to momentary contention.
        store.update_last(
            committed_record["cwd"],
            stable_id,
            session_type=committed_record["session_type"],
            blocking=True,
        )
    except BaseException:
        # Any failure after scope/record/pointer mutation but before exec must
        # roll back while this launch still owns the lifecycle lock. A failure
        # before this attempt committed anything (guards, lead-prompt write)
        # must NOT roll back: the pointer and the live scope belong to the
        # pre-existing session, and this attempt never touched them.
        try:
            if committed_token is None:
                raise
            if action_kind == "resume" and pre_existing is not None:
                current_bytes = store.read_record_bytes(stable_id)
                try:
                    current = store.load(stable_id)
                except sessions.SessionError:
                    current = None
                if (
                    current is not None
                    and committed_token is not None
                    and current.get("mutation_token") == committed_token
                ):
                    store.restore_record_bytes(stable_id, pre_existing)
                elif current_bytes != pre_existing:
                    # Unknown authority: do not guess at rollback ownership.
                    raise
                if durable and scope_written:
                    # Only converge the scope when this attempt actually
                    # rewrote it (mirrors the execve-failure path); a failure
                    # before the scope write leaves the valid scope untouched.
                    _resume_failure_scope(
                        store, stable_id, pre_existing, result, trusted, environ
                    )
                store.restore_pointer_bytes(
                    record["cwd"],
                    stable_id,
                    prior_pointer,
                    session_type=record["session_type"],
                )
            elif action_kind == "fresh":
                try:
                    current = store.load(stable_id)
                except sessions.SessionError:
                    current = None
                if (
                    current is not None
                    and committed_token is not None
                    and current.get("mutation_token") == committed_token
                ):
                    store._forget_unlocked(stable_id)
                store.clear_last(
                    record["cwd"],
                    stable_id,
                    session_type=record["session_type"],
                    blocking=True,
                )
                if durable:
                    scope.remove_scope(store.root, stable_id)
        finally:
            cwd_lease.close()
            lock.release()
        raise

    base_environ = dict(os.environ if environ is None else environ)
    for key in result.env_unset:
        base_environ.pop(key, None)
    final_env = {**base_environ, **result.env_set, "ANTHROPIC_AUTH_TOKEN": token}
    argv = [str(executable), *result.argv]

    # Claude locates transcripts under the session's ORIGINAL project
    # directory. The fd lease was validated before state commit and cannot be
    # invalidated by a pathname rename. A returning injected exec boundary is
    # restored to the caller's original CWD; a real successful exec never
    # returns and O_CLOEXEC closes both lease fds.
    try:
        cwd_lease.enter()
        outcome = execve(str(executable), argv, final_env)
        cwd_lease.restore()
        cwd_lease.close()
        lock.release()
        return outcome
    except OSError:
        try:
            try:
                cwd_lease.restore()
            except OSError:
                # A CWD-restore failure must never mask the exec error or
                # skip the state convergence below.
                pass
        finally:
            cwd_lease.close()
        # The session never started; converge state per action kind while the
        # launch still owns the lifecycle lock, then re-raise.
        try:
            try:
                current = store.load(stable_id)
            except sessions.SessionError:
                current = None
            owns_mutation = (
                current is not None
                and committed_token is not None
                and current.get("mutation_token") == committed_token
            )
            if action_kind == "resume":
                # The mutation token survives lifecycle-only hook updates but
                # changes on every newer launch/transition attempt.
                if owns_mutation:
                    record_restored = False
                    if store.read_record_bytes(stable_id) == committed_bytes:
                        store.restore_record_bytes(stable_id, pre_existing)
                        record_restored = True
                    else:
                        try:
                            prior = strict_json.loads(pre_existing)
                            if not isinstance(prior, dict):
                                raise ValueError("prior record is not an object")
                            prior = sessions._normalize_legacy_record(prior)
                            restored = sessions.carry_lifecycle_state(prior, current)
                            store.save(restored)
                            record_restored = True
                        except Exception:
                            # Never replace the original exec error with a
                            # cleanup parse failure; leave record authority for
                            # doctor/converge rather than guessing.
                            pass
                    if record_restored:
                        if scope_written:
                            _resume_failure_scope(
                                store, stable_id, pre_existing, result, trusted,
                                environ,
                            )
                        store.restore_pointer_bytes(
                            record["cwd"], stable_id, prior_pointer,
                            session_type=record["session_type"],
                        )
            else:
                if owns_mutation:
                    store._forget_unlocked(stable_id)
                    store.clear_last(
                        record["cwd"], stable_id,
                        session_type=record["session_type"],
                        blocking=True,
                    )
                    if scope_written:
                        scope.remove_scope(store.root, stable_id)
        finally:
            lock.release()
        raise
    except BaseException:
        try:
            cwd_lease.restore()
        finally:
            cwd_lease.close()
            lock.release()
        raise
