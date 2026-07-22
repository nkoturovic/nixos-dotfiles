"""Launcher-owned session state for claude-multi v2.

UUID-keyed session records under XDG state, per-CWD last-session pointers with
advisory locking, and adoption (`link`) records. All persistence uses the
Phase 1 atomic, symlink-safe, mode-0600 primitives. No private Claude files
are ever read.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import state, strict_json, validate as schema_validate


class SessionError(RuntimeError):
    """Raised on session state failures, including corrupt/stale records."""


UUID4 = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)

_COLLISION_RETRIES = 8


def state_root(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path(env.get("HOME", str(Path.home()))) / ".local" / "state"
    return base / "claude-multi"


def config_root(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    xdg = env.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path(env.get("HOME", str(Path.home()))) / ".config"
    return base / "claude-multi"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


RECORD_VERSION = 2

# v1 records load with these v2 defaults (one read path, no rewrite on disk).
_V2_DEFAULTS = {"mode": "legacy", "scope_generation": 0, "workflows": "native"}


def make_record(
    *,
    session_id: str,
    cwd: str,
    composition_name: str,
    snapshot: dict[str, Any],
    catalog_version: int,
    catalog_hash: str,
    launcher_version: str,
    forked_from: str | None = None,
    mode: str = "legacy",
    scope_generation: int = 0,
    workflows: str = "native",
    now: str | None = None,
) -> dict[str, Any]:
    """Build a session record (schema-shaped). No secrets are accepted."""

    return {
        "version": RECORD_VERSION,
        "session_id": session_id,
        "cwd": cwd,
        "composition_name": composition_name,
        "composition_hash": strict_json.bundle_digest(snapshot),
        "snapshot": snapshot,
        "mode": mode,
        "scope_generation": scope_generation,
        "workflows": workflows,
        "catalog_version": catalog_version,
        "catalog_hash": catalog_hash,
        "launcher_version": launcher_version,
        "created_at": now or _now(),
        "forked_from": forked_from,
    }


def transition_record(
    prior: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    composition_name: str,
    workflows: str,
    catalog_version: int,
    catalog_hash: str,
    launcher_version: str,
) -> dict[str, Any]:
    """Build the generation N+1 record for a composition transition.

    Identity fields (``session_id``, ``cwd``, ``created_at``, ``forked_from``)
    carry over unchanged — a transition never changes the session UUID or fork
    lineage (TRANSITIONS section 6). ``scope_generation`` bumps by one and the
    catalog metadata records the installed catalog the relaunch compiles
    against. No secrets are accepted; the record remains the intent authority.
    """

    if prior.get("version") != RECORD_VERSION:
        raise SessionError(
            f"cannot transition a version {prior.get('version')!r} record"
        )
    if prior.get("mode") != "durable":
        raise SessionError(
            f"cannot transition a {prior.get('mode')!r} record; v1 transitions "
            "require a durable session"
        )
    generation = prior.get("scope_generation")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise SessionError("durable record has no scope generation to bump")
    return {
        **prior,
        "composition_name": composition_name,
        "composition_hash": strict_json.bundle_digest(snapshot),
        "snapshot": snapshot,
        "scope_generation": generation + 1,
        "workflows": workflows,
        "catalog_version": catalog_version,
        "catalog_hash": catalog_hash,
        "launcher_version": launcher_version,
    }


def drift_report(
    record: dict[str, Any], *, catalog_hash: str, composition_hash: str
) -> list[str]:
    """Human-readable drift between a record and current trusted/saved data."""

    report: list[str] = []
    if record["catalog_hash"] != catalog_hash:
        report.append("trusted catalog changed since session creation")
    if record["composition_hash"] != composition_hash:
        report.append(
            f"composition {record['composition_name']!r} changed since session creation"
        )
    return report


class SessionStore:
    """UUID-keyed session records plus per-CWD last-session pointers."""

    def __init__(self, root: Path | str, schema: dict[str, Any]):
        self.root = state.ensure_private_dir(Path(root))
        self.schema = schema
        self.sessions_dir = state.ensure_private_dir(self.root / "sessions")
        self.pointers_dir = state.ensure_private_dir(self.root / "last-session-by-cwd")

    def _record_path(self, session_id: str) -> Path:
        if not isinstance(session_id, str) or not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        return self.sessions_dir / f"{session_id}.json"

    def new_id(self) -> str:
        """Mint a UUIDv4, retrying on the (astronomically unlikely) collision."""

        for _ in range(_COLLISION_RETRIES):
            candidate = str(uuid.uuid4())
            if not os.path.lexists(self._record_path(candidate)):
                return candidate
        raise SessionError("could not mint a collision-free session UUID")

    def _validate(self, record: dict[str, Any], origin: str) -> dict[str, Any]:
        problems = schema_validate.validate(record, self.schema, "$")
        if problems:
            raise SessionError(f"{origin}: invalid session record: {'; '.join(problems)}")
        return record

    def save(self, record: dict[str, Any]) -> Path:
        self._validate(record, "save")
        session_id = record["session_id"]
        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        path = self._record_path(session_id)
        state.atomic_write(path, strict_json.canonical_file_bytes(record))
        return path

    def load(self, session_id: str) -> dict[str, Any]:
        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        path = self._record_path(session_id)
        try:
            raw = state.read_private(path)
        except state.StateError as exc:
            raise SessionError(f"cannot read session record {session_id}: {exc}") from exc
        try:
            record = strict_json.loads(raw)
        except strict_json.StrictJSONError as exc:
            raise SessionError(
                f"corrupt session record {session_id}: {exc}; "
                "use `claude-multi sessions forget` to remove it"
            ) from exc
        if not isinstance(record, dict):
            raise SessionError(f"corrupt session record {session_id}: not an object")
        record = self._validate(record, f"session {session_id}")
        if record["session_id"] != session_id:
            raise SessionError(
                f"corrupt session record {session_id}: embedded session_id "
                f"{record['session_id']!r} does not match its record path"
            )
        if record["version"] == 1:
            # v1 record: apply the v2 defaults in memory only; the on-disk
            # record is never rewritten until the session is resumed/upgraded.
            return {**record, **_V2_DEFAULTS}
        for key, default in _V2_DEFAULTS.items():
            if key not in record:
                raise SessionError(
                    f"corrupt session record {session_id}: version 2 record "
                    f"is missing {key!r}"
                )
        return record

    def exists(self, session_id: str) -> bool:
        return os.path.lexists(self._record_path(session_id))

    def forget(self, session_id: str) -> bool:
        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        return state.remove_private(self._record_path(session_id))

    def link(self, record: dict[str, Any]) -> Path:
        """Adopt an unmanaged native session; never reads private Claude files.

        The exists-recheck and the save run inside the session's lifecycle
        lock (audit L1): an existence check sampled outside the lock is stale
        the moment a concurrent launch or transition on the same UUID commits,
        so the check-then-save pair must be serialized as one critical section.
        """

        session_id = record["session_id"]
        if not UUID4.fullmatch(session_id):
            raise SessionError(f"cannot adopt {session_id!r}: not a UUIDv4")
        lock = self.lifecycle_lock(session_id)
        lock.acquire(blocking=True)
        try:
            if self.exists(session_id):
                raise SessionError(f"session {session_id} is already managed")
            return self.save(record)
        finally:
            lock.release()

    # Exact-byte pre-read/restore (action-aware execve cleanup) -----------

    def lifecycle_lock(self, session_id: str) -> state.FileLock:
        """Per-session lifecycle lock serializing record/scope mutation.

        Held by launch (scope write + record save + pointer) and transition
        (staging/swap/save) so concurrent launchers on the same UUID cannot
        interleave mutations. Always released before execve so the fd never
        leaks into the Claude process; cleanup paths re-acquire and use
        compare-and-restore so a newer launch always wins.
        """

        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        locks_dir = state.ensure_private_dir(self.root / "locks")
        return state.FileLock(locks_dir / f"{session_id}.lifecycle")

    def read_record_bytes(self, session_id: str) -> bytes | None:
        """Exact on-disk record bytes; None when no record exists.

        Used by the launch path to capture the pre-launch record so an
        execve failure can restore it byte-for-byte (never regenerated).
        """

        path = self._record_path(session_id)
        if not os.path.lexists(path):
            return None
        return state.read_private(path)

    def restore_record_bytes(self, session_id: str, data: bytes) -> None:
        """Restore exact pre-read record bytes (never regenerated content)."""

        state.atomic_write(self._record_path(session_id), data)

    # Per-CWD last-session pointers -------------------------------------

    def read_pointer_bytes(self, cwd: str) -> bytes | None:
        """Exact on-disk pointer bytes; None when no pointer exists."""

        pointer = self._pointer_path(cwd)
        if not os.path.lexists(pointer):
            return None
        return state.read_private(pointer)

    def restore_pointer_bytes(
        self, cwd: str, session_id: str, data: bytes | None
    ) -> bool:
        """Compare-and-restore a pointer changed by a failed resume launch.

        The restore only runs while the pointer still names ``session_id``;
        a newer concurrent launch therefore wins. ``data`` is the exact
        pre-launch pointer content, or None when the pointer was absent.
        """

        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        pointer = self._pointer_path(cwd)
        lock = state.FileLock(pointer)
        if not lock.acquire(blocking=False):
            return False
        try:
            if self.last(cwd) != session_id:
                return False
            if data is None:
                return state.remove_private(pointer)
            state.atomic_write(pointer, data)
            return True
        finally:
            lock.release()

    def _pointer_path(self, cwd: str) -> Path:
        digest = strict_json.sha256_hex(cwd.encode("utf-8"))[:32]
        return self.pointers_dir / f"{digest}.json"

    def update_last(self, cwd: str, session_id: str) -> bool:
        """Record the per-CWD last session; lock failure skips the update."""

        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        pointer = self._pointer_path(cwd)
        lock = state.FileLock(pointer)
        if not lock.acquire(blocking=False):
            return False
        try:
            payload = {"cwd": cwd, "session_id": session_id}
            state.atomic_write(pointer, strict_json.canonical_file_bytes(payload))
            return True
        finally:
            lock.release()

    def last(self, cwd: str) -> str | None:
        pointer = self._pointer_path(cwd)
        if not pointer.exists():
            return None
        try:
            payload = strict_json.loads(state.read_private(pointer))
        except (state.StateError, strict_json.StrictJSONError):
            return None
        if not isinstance(payload, dict):
            return None
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not UUID4.fullmatch(session_id):
            return None
        return session_id

    def clear_last(self, cwd: str, session_id: str) -> bool:
        """Clear the per-CWD pointer only if it currently points at session_id.

        Compare-and-clear under the advisory lock; never clears another
        session's pointer. Returns True when the pointer was removed.
        """

        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        pointer = self._pointer_path(cwd)
        lock = state.FileLock(pointer)
        if not lock.acquire(blocking=False):
            return False
        try:
            if self.last(cwd) != session_id:
                return False
            return state.remove_private(pointer)
        finally:
            lock.release()
