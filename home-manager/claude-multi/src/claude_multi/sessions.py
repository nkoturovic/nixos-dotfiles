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
    now: str | None = None,
) -> dict[str, Any]:
    """Build a session record (schema-shaped). No secrets are accepted."""

    return {
        "version": 1,
        "session_id": session_id,
        "cwd": cwd,
        "composition_name": composition_name,
        "composition_hash": strict_json.bundle_digest(snapshot),
        "snapshot": snapshot,
        "catalog_version": catalog_version,
        "catalog_hash": catalog_hash,
        "launcher_version": launcher_version,
        "created_at": now or _now(),
        "forked_from": forked_from,
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
        return self.sessions_dir / f"{session_id}.json"

    def new_id(self) -> str:
        """Mint a UUIDv4, retrying on the (astronomically unlikely) collision."""

        for _ in range(_COLLISION_RETRIES):
            candidate = str(uuid.uuid4())
            if not self._record_path(candidate).exists():
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
        return self._validate(record, f"session {session_id}")

    def exists(self, session_id: str) -> bool:
        return self._record_path(session_id).exists()

    def forget(self, session_id: str) -> bool:
        path = self._record_path(session_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def link(self, record: dict[str, Any]) -> Path:
        """Adopt an unmanaged native session; never reads private Claude files."""

        session_id = record["session_id"]
        if not UUID4.fullmatch(session_id):
            raise SessionError(f"cannot adopt {session_id!r}: not a UUIDv4")
        if self.exists(session_id):
            raise SessionError(f"session {session_id} is already managed")
        return self.save(record)

    # Per-CWD last-session pointers -------------------------------------

    def _pointer_path(self, cwd: str) -> Path:
        digest = strict_json.sha256_hex(cwd.encode("utf-8"))[:32]
        return self.pointers_dir / f"{digest}.json"

    def update_last(self, cwd: str, session_id: str) -> bool:
        """Record the per-CWD last session; lock failure skips the update."""

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
        return session_id if isinstance(session_id, str) else None

    def clear_last(self, cwd: str, session_id: str) -> bool:
        """Clear the per-CWD pointer only if it currently points at session_id.

        Compare-and-clear under the advisory lock; never clears another
        session's pointer. Returns True when the pointer was removed.
        """

        pointer = self._pointer_path(cwd)
        lock = state.FileLock(pointer)
        if not lock.acquire(blocking=False):
            return False
        try:
            if self.last(cwd) != session_id:
                return False
            if pointer.exists():
                pointer.unlink()
                return True
            return False
        finally:
            lock.release()
