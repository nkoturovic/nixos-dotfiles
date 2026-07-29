"""Launcher-owned session state for claude-multi v2.2.

Stable-managed-ID records under XDG state, per-CWD last-session pointers with
advisory locking, and adoption (`link`) records. All persistence uses the
Phase 1 atomic, symlink-safe, mode-0600 primitives. No private Claude files
are ever read.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
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


class _JSONPairs(list):
    """Object pairs preserved for conservative invalid-record inspection."""


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


RECORD_VERSION = 3
LEGACY_RECORD_VERSIONS = frozenset({1, 2})
SESSION_TYPE_MANAGED = "managed-composition"
SESSION_TYPE_ORDINARY = "ordinary-gateway"
IDENTITY_AUTHORITATIVE = "authoritative"
IDENTITY_UNVERIFIED = "unverified"
IDENTITY_REPAIR_NEEDED = "repair-needed"
IDENTITY_PENDING_FORK = "pending-fork"
_MAX_RUNTIME_ALIASES = 16
_LIFECYCLE_FIELDS = (
    "runtime_session_id",
    "runtime_aliases",
    "identity_state",
    "last_event_source",
    "last_seen_at",
    "pending_forks",
    "observed_cwd",
    "observed_model",
    "last_end_reason",
)

# v1 records load with these v2 defaults before their in-memory v3 migration.
_V2_DEFAULTS = {"mode": "legacy", "scope_generation": 0, "workflows": "native"}


def managed_id(record: dict[str, Any]) -> str:
    """Stable claude-multi identity for a normalized or legacy record."""

    value = record.get("managed_id", record.get("session_id"))
    if not isinstance(value, str) or not UUID4.fullmatch(value):
        raise SessionError(f"record managed_id {value!r} is not a UUIDv4")
    return value


def runtime_session_id(record: dict[str, Any]) -> str:
    """Current native Claude runtime UUID for a normalized record."""

    value = record.get("runtime_session_id", record.get("session_id"))
    if not isinstance(value, str) or not UUID4.fullmatch(value):
        raise SessionError(f"record runtime_session_id {value!r} is not a UUIDv4")
    return value


def new_mutation_token() -> str:
    """Unique ownership token for one record/scope mutation attempt."""

    return str(uuid.uuid4())


def carry_lifecycle_state(
    target: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Overlay the latest hook-owned fields onto a prepared record mutation."""

    merged = copy.deepcopy(target)
    for key in _LIFECYCLE_FIELDS:
        merged.pop(key, None)
        if key in current:
            merged[key] = copy.deepcopy(current[key])
    return merged


def _derived_identity_state(record: dict[str, Any]) -> str:
    """Derive state from unresolved lifecycle evidence after a start event."""

    if "observed_cwd" in record or "observed_model" in record:
        return IDENTITY_REPAIR_NEEDED
    if record.get("pending_forks"):
        return IDENTITY_PENDING_FORK
    return IDENTITY_AUTHORITATIVE


def drop_resolved_pending_forks(record: dict[str, Any]) -> dict[str, Any] | None:
    """Copy of ``record`` with authority-holding pending forks removed.

    A pending fork that IS the current runtime authority is already resolved
    by reality — the live runtime is that fork, so there is nothing left to
    adopt or discard. Returns ``None`` when nothing changed.
    """

    pending = record.get("pending_forks", [])
    if not pending:
        return None
    runtime_id = record.get("runtime_session_id")
    kept = [dict(item) for item in pending if item.get("session_id") != runtime_id]
    if len(kept) == len(pending):
        return None
    updated = {**record, "pending_forks": kept}
    if record.get("identity_state") != IDENTITY_REPAIR_NEEDED:
        updated["identity_state"] = _derived_identity_state(updated)
    return updated


def pending_fork_message(record: dict[str, Any]) -> str:
    """Actionable fork-blocked language: names the fork(s) and the remedies.

    Every resume/transition guard and the card uses this so the operator
    never sees an unnamed "adopt the fork UUID" dead end.
    """

    stable_id = managed_id(record)
    pending = record.get("pending_forks", [])
    fork_ids = [item.get("session_id", "?") for item in pending]
    fork_list = ", ".join(fork_ids)
    if record["session_type"] == SESSION_TYPE_ORDINARY:
        adopt = f"claude-multi sessions link {fork_ids[0]} --model MODEL"
    else:
        adopt = (
            f"claude-multi sessions link {fork_ids[0]} --composition "
            f"{record.get('composition_name', 'NAME')}"
        )
    remedy = (
        f"adopt it with `{adopt}`, or discard the marker with `claude-multi "
        f"sessions resolve-fork {stable_id} {fork_ids[0]}`"
    )
    if len(fork_ids) > 1:
        link_flags = (
            "--model MODEL"
            if record["session_type"] == SESSION_TYPE_ORDINARY
            else "--composition NAME"
        )
        remedy = (
            f"adopt or discard each with `claude-multi sessions link <fork-uuid> "
            f"{link_flags}` / `claude-multi sessions resolve-fork "
            f"{stable_id} <fork-uuid>`"
        )
    return (
        f"session {stable_id} has an unresolved native fork (runtime "
        f"{fork_list}); {remedy} — the fork transcript is kept either way"
    )


def relink_message(record: dict[str, Any]) -> str:
    """Actionable repair-needed language: the exact relink command.

    Every resume/transition guard, the card, the sessions list, and doctor
    use this so the operator never sees a bare command name that cannot
    run as printed (issue 002).
    """

    stable_id = managed_id(record)
    runtime_id = runtime_session_id(record)
    base = f"claude-multi sessions relink-runtime {stable_id} {runtime_id}"
    observed_cwd = record.get("observed_cwd")
    if not observed_cwd:
        observed_model = record.get("observed_model")
        if observed_model:
            recorded_model = (
                record.get("snapshot", {})
                .get("lead", {})
                .get("client_selector", "the recorded model")
            )
            return (
                f"session {stable_id} identity is repair-needed (observed "
                f"model {observed_model} differs from the recorded "
                f"{recorded_model}); resume through the launcher to "
                f"reconcile the recorded model (`claude-multi -r "
                f"{stable_id}`), or relink only if the runtime UUID itself "
                f"changed: `{base}`"
            )
        return (
            f"session {stable_id} identity is repair-needed (runtime/model "
            f"evidence conflicts with the record); repair it with `{base}`"
        )
    recorded_cwd = record.get("cwd", "?")
    return (
        f"session {stable_id} identity is repair-needed (a resume was "
        f"observed from {observed_cwd}, conflicting with the recorded "
        f"project dir {recorded_cwd}); repair it with `{base} --cwd "
        f"{shlex.quote(recorded_cwd)}` to keep the recorded dir (the "
        f"common case), or `{base} --cwd {shlex.quote(observed_cwd)}` "
        f"only if the session was intentionally re-homed there"
    )


def make_record(
    *,
    managed_id: str | None = None,
    session_id: str | None = None,
    runtime_session_id: str | None = None,
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
    identity_state: str = IDENTITY_UNVERIFIED,
    launch_epoch: int = 0,
) -> dict[str, Any]:
    """Build a managed-composition v3 record without accepting secrets.

    ``session_id`` remains a source-compatibility keyword for callers being
    migrated. New code should pass ``managed_id`` explicitly. A fresh record's
    requested native UUID initially equals the stable ID; SessionStart replaces
    it with the authoritative runtime UUID before later resumes.
    """

    stable = managed_id if managed_id is not None else session_id
    if stable is None:
        raise SessionError("make_record requires managed_id")
    if session_id is not None and managed_id is not None and session_id != managed_id:
        raise SessionError("managed_id and legacy session_id disagree")
    runtime_id = runtime_session_id or stable
    timestamp = now or _now()
    return {
        "version": RECORD_VERSION,
        "managed_id": stable,
        "runtime_session_id": runtime_id,
        "runtime_aliases": [],
        "session_type": SESSION_TYPE_MANAGED,
        "identity_state": identity_state,
        "last_event_source": None,
        "last_seen_at": timestamp,
        "pending_forks": [],
        "launch_epoch": launch_epoch,
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
        "created_at": timestamp,
        "forked_from": forked_from,
        "migrated_from_version": None,
    }


def make_ordinary_record(
    *,
    managed_id: str,
    runtime_session_id: str | None,
    cwd: str,
    model: str,
    context_profile: str,
    catalog_version: int,
    catalog_hash: str,
    launcher_version: str,
    now: str | None = None,
    identity_state: str = IDENTITY_UNVERIFIED,
    launch_epoch: int = 0,
    mode: str = "durable",
    scope_generation: int = 1,
) -> dict[str, Any]:
    """Build an ordinary gateway v3 record with no composition semantics."""

    timestamp = now or _now()
    return {
        "version": RECORD_VERSION,
        "managed_id": managed_id,
        "runtime_session_id": runtime_session_id or managed_id,
        "runtime_aliases": [],
        "session_type": SESSION_TYPE_ORDINARY,
        "identity_state": identity_state,
        "last_event_source": None,
        "last_seen_at": timestamp,
        "pending_forks": [],
        "launch_epoch": launch_epoch,
        "cwd": cwd,
        "ordinary_model": model,
        "context_profile": context_profile,
        "mode": mode,
        "scope_generation": scope_generation,
        "catalog_version": catalog_version,
        "catalog_hash": catalog_hash,
        "launcher_version": launcher_version,
        "created_at": timestamp,
        "forked_from": None,
        "migrated_from_version": None,
    }


def _snapshot_shape(snapshot: dict[str, Any]) -> tuple[Any, frozenset[tuple[Any, ...]]]:
    """The composition-defining shape of a snapshot: lead model + variant slots."""

    variants = frozenset(
        (
            item.get("role"),
            item.get("model"),
            item.get("lane"),
            bool(item.get("preferred")),
        )
        for item in snapshot.get("variants", [])
    )
    return snapshot.get("lead", {}).get("model"), variants


def refresh_record_snapshot(
    prior: dict[str, Any],
    *,
    snapshot: dict[str, Any],
    catalog_version: int,
    catalog_hash: str,
    launcher_version: str,
) -> dict[str, Any]:
    """Absorb installed-catalog drift into a durable record's snapshot.

    Repair-time metadata refresh: the recorded composition (lead model and
    variant slots) must be identical — only catalog-derived content (context
    fields, selectors, effort, availability text) may change. A different
    composition is a transition, never a refresh. Identity, lifecycle, CWD,
    generation, and lineage carry over untouched; the composition and catalog
    hashes advance to the refreshed truth.
    """

    if prior.get("version") != RECORD_VERSION:
        raise SessionError(
            f"cannot refresh a version {prior.get('version')!r} record"
        )
    if prior.get("session_type") != SESSION_TYPE_MANAGED:
        raise SessionError("only managed-composition records carry a snapshot")
    if prior.get("mode") != "durable":
        raise SessionError("only durable records can be refreshed in repair")
    if _snapshot_shape(prior["snapshot"]) != _snapshot_shape(snapshot):
        raise SessionError(
            "snapshot refresh refuses a composition change; use "
            "`claude-multi sessions transition` for an intentional change"
        )
    return {
        **prior,
        "composition_hash": strict_json.bundle_digest(snapshot),
        "snapshot": snapshot,
        "catalog_version": catalog_version,
        "catalog_hash": catalog_hash,
        "launcher_version": launcher_version,
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

    Stable/runtime identity, CWD, timestamps, and fork lineage carry over
    unchanged. ``scope_generation`` bumps by one and the catalog metadata
    records the installed catalog the relaunch compiles against. No secrets are
    accepted; the record remains the intent authority.
    """

    if prior.get("version") != RECORD_VERSION:
        raise SessionError(
            f"cannot transition a version {prior.get('version')!r} record"
        )
    if prior.get("session_type") != SESSION_TYPE_MANAGED:
        raise SessionError("only managed-composition sessions can transition")
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
        "launch_epoch": prior.get("launch_epoch", 0) + 1,
        "workflows": workflows,
        "catalog_version": catalog_version,
        "catalog_hash": catalog_hash,
        "launcher_version": launcher_version,
    }


def drift_report(
    record: dict[str, Any], *, catalog_hash: str, composition_hash: str
) -> list[str]:
    """Human-readable drift between a managed record and current data."""

    report: list[str] = []
    if record["catalog_hash"] != catalog_hash:
        report.append("trusted catalog changed since session creation")
    if record["composition_hash"] != composition_hash:
        report.append(
            f"composition {record['composition_name']!r} changed since session creation"
        )
    return report


def _normalize_legacy_record(record: dict[str, Any]) -> dict[str, Any]:
    """Return an in-memory v3 view of a v1/v2 managed record.

    Loading is side-effect free: the original bytes remain on disk until a
    later explicit launch, transition, hook reconciliation, or repair saves the
    normalized record.
    """

    version = record.get("version")
    if version == RECORD_VERSION:
        normalized = dict(record)
        normalized.setdefault("launch_epoch", 0)
        return normalized
    if version not in LEGACY_RECORD_VERSIONS:
        return record
    old = dict(record)
    if version == 1:
        old.update({key: old.get(key, default) for key, default in _V2_DEFAULTS.items()})
    stable = old.get("session_id")
    timestamp = old.get("created_at")
    return {
        "version": RECORD_VERSION,
        "managed_id": stable,
        "runtime_session_id": stable,
        "runtime_aliases": [],
        "session_type": SESSION_TYPE_MANAGED,
        "identity_state": IDENTITY_UNVERIFIED,
        "last_event_source": None,
        "last_seen_at": timestamp,
        "pending_forks": [],
        "launch_epoch": 0,
        "cwd": old.get("cwd"),
        "composition_name": old.get("composition_name"),
        "composition_hash": old.get("composition_hash"),
        "snapshot": old.get("snapshot"),
        "mode": old.get("mode"),
        "scope_generation": old.get("scope_generation"),
        "workflows": old.get("workflows"),
        "catalog_version": old.get("catalog_version"),
        "catalog_hash": old.get("catalog_hash"),
        "launcher_version": old.get("launcher_version"),
        "created_at": timestamp,
        "forked_from": old.get("forked_from"),
        "migrated_from_version": version,
    }


def _validate_record_invariants(record: dict[str, Any], origin: str) -> None:
    version = record.get("version")
    if version in LEGACY_RECORD_VERSIONS:
        value = record.get("session_id")
        if not isinstance(value, str) or not UUID4.fullmatch(value):
            raise SessionError(f"{origin}: legacy session_id {value!r} is not a UUIDv4")
        if version == 2:
            for key in _V2_DEFAULTS:
                if key not in record:
                    raise SessionError(
                        f"{origin}: version 2 record is missing {key!r}"
                    )
        return
    if version != RECORD_VERSION:
        raise SessionError(f"{origin}: unsupported session record version {version!r}")
    for key in (
        "managed_id",
        "runtime_session_id",
        "runtime_aliases",
        "session_type",
        "identity_state",
        "last_event_source",
        "last_seen_at",
        "pending_forks",
        "migrated_from_version",
    ):
        if key not in record:
            raise SessionError(f"{origin}: version 3 record is missing {key!r}")
    stable = managed_id(record)
    runtime_session_id(record)
    token = record.get("mutation_token")
    if token is not None and (not isinstance(token, str) or not UUID4.fullmatch(token)):
        raise SessionError(f"{origin}: mutation_token {token!r} is not a UUIDv4")
    epoch = record.get("launch_epoch", 0)
    if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
        raise SessionError(f"{origin}: launch_epoch {epoch!r} is invalid")
    if record.get("session_type") not in {SESSION_TYPE_MANAGED, SESSION_TYPE_ORDINARY}:
        raise SessionError(f"{origin}: invalid session_type {record.get('session_type')!r}")
    aliases = record.get("runtime_aliases")
    if not isinstance(aliases, list) or len(aliases) > _MAX_RUNTIME_ALIASES:
        raise SessionError(f"{origin}: runtime_aliases is invalid")
    alias_ids: set[str] = set()
    for alias in aliases:
        if not isinstance(alias, dict):
            raise SessionError(f"{origin}: runtime alias is not an object")
        alias_id = alias.get("session_id")
        if not isinstance(alias_id, str) or not UUID4.fullmatch(alias_id):
            raise SessionError(f"{origin}: runtime alias {alias_id!r} is not a UUIDv4")
        if alias_id in alias_ids or alias_id == record["runtime_session_id"]:
            raise SessionError(f"{origin}: duplicate/current runtime alias {alias_id}")
        alias_ids.add(alias_id)
    if record["session_type"] == SESSION_TYPE_MANAGED:
        for key in (
            "composition_name",
            "composition_hash",
            "snapshot",
            "mode",
            "scope_generation",
            "workflows",
        ):
            if key not in record:
                raise SessionError(f"{origin}: managed record is missing {key!r}")
    else:
        for key in ("ordinary_model", "context_profile"):
            if key not in record:
                raise SessionError(f"{origin}: ordinary record is missing {key!r}")
        if any(key in record for key in ("composition_name", "snapshot", "workflows")):
            raise SessionError(f"{origin}: ordinary record carries composition fields")
    if stable == record.get("forked_from"):
        raise SessionError(f"{origin}: session cannot be forked from itself")


def reconcile_runtime_record(
    record: dict[str, Any],
    *,
    observed_runtime_id: str,
    source: str,
    cwd: str | None = None,
    model: str | None = None,
    model_profile: str | None = None,
    observed_model: str | None = None,
    launch_epoch: int | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Return an idempotently reconciled v3 record from SessionStart metadata."""

    if not UUID4.fullmatch(observed_runtime_id):
        raise SessionError(f"runtime session_id {observed_runtime_id!r} is not a UUIDv4")
    current = _normalize_legacy_record(record)
    _validate_record_invariants(current, "reconcile")
    timestamp = now or _now()
    current_epoch = current.get("launch_epoch", 0)
    observed_epoch = 0 if launch_epoch is None else launch_epoch
    if (
        not isinstance(observed_epoch, int)
        or isinstance(observed_epoch, bool)
        or observed_epoch < 0
    ):
        raise SessionError(f"launch_epoch {observed_epoch!r} is invalid")
    if source != "repair" and observed_epoch < current_epoch:
        return dict(current)
    alias_ids = {
        item.get("session_id") for item in current.get("runtime_aliases", [])
    }
    if (
        source not in {"fork", "repair"}
        and observed_epoch <= current_epoch
        and observed_runtime_id in alias_ids
    ):
        # A delayed hook from an older runtime must never retarget authority or
        # contribute model/CWD evidence to the newer launch.
        return dict(current)
    updated = dict(current)
    updated["launch_epoch"] = observed_epoch
    updated["last_event_source"] = source
    updated["last_seen_at"] = timestamp
    if cwd is not None and cwd != current["cwd"]:
        updated["observed_cwd"] = cwd
    if source == "fork":
        pending = list(current.get("pending_forks", []))
        if not any(item.get("session_id") == observed_runtime_id for item in pending):
            if len(pending) >= _MAX_RUNTIME_ALIASES:
                # Never evict a genuine marker silently (review H19): the
                # operator was told about every pending fork by name, so
                # dropping the oldest would orphan a fork they were asked to
                # resolve. Fail the hook visibly instead.
                raise SessionError(
                    f"session {managed_id(current)} already tracks "
                    f"{_MAX_RUNTIME_ALIASES} unresolved native forks; resolve "
                    "some (adopt or resolve-fork) before more can be tracked"
                )
            pending.append({"session_id": observed_runtime_id, "observed_at": timestamp})
        updated["pending_forks"] = pending[-_MAX_RUNTIME_ALIASES:]
        if current.get("identity_state") == IDENTITY_REPAIR_NEEDED:
            updated["identity_state"] = IDENTITY_REPAIR_NEEDED
        else:
            updated["identity_state"] = _derived_identity_state(updated)
        return updated

    prior_runtime = current["runtime_session_id"]
    aliases = [
        dict(item)
        for item in current.get("runtime_aliases", [])
        if item.get("session_id") not in {observed_runtime_id, prior_runtime}
    ]
    if prior_runtime != observed_runtime_id:
        aliases.append(
            {
                "session_id": prior_runtime,
                "source": current.get("last_event_source") or "previous",
                "observed_at": timestamp,
            }
        )
    updated["runtime_session_id"] = observed_runtime_id
    updated["runtime_aliases"] = aliases[-_MAX_RUNTIME_ALIASES:]
    pending = [
        dict(item)
        for item in current.get("pending_forks", [])
        if item.get("session_id") != observed_runtime_id
    ]
    if len(pending) != len(current.get("pending_forks", [])):
        # Authority landing on a pending fork resolves it: the live runtime
        # IS that fork, so there is nothing left to adopt or discard.
        updated["pending_forks"] = pending
    if current["session_type"] == SESSION_TYPE_ORDINARY:
        if observed_model and model is None:
            updated["observed_model"] = observed_model
        elif model:
            if model_profile != current["context_profile"]:
                updated["observed_model"] = observed_model or model
            else:
                updated["ordinary_model"] = model
                updated.pop("observed_model", None)
    elif model:
        if model != current["snapshot"]["lead"]["client_selector"]:
            updated["observed_model"] = observed_model or model
        else:
            updated.pop("observed_model", None)
    updated["identity_state"] = _derived_identity_state(updated)
    return updated


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
        _validate_record_invariants(record, origin)
        return record

    def save(self, record: dict[str, Any]) -> Path:
        self._validate(record, "save")
        stable = managed_id(record)
        path = self._record_path(stable)
        state.atomic_write(path, strict_json.canonical_file_bytes(record))
        return path

    def load(self, managed_session_id: str) -> dict[str, Any]:
        if not UUID4.fullmatch(managed_session_id):
            raise SessionError(f"managed_id {managed_session_id!r} is not a UUIDv4")
        path = self._record_path(managed_session_id)
        try:
            raw = state.read_private(path)
        except state.StateError as exc:
            raise SessionError(
                f"cannot read session record {managed_session_id}: {exc}"
            ) from exc
        try:
            record = strict_json.loads(raw)
        except strict_json.StrictJSONError as exc:
            raise SessionError(
                f"corrupt session record {managed_session_id}: {exc}; "
                "use `claude-multi sessions forget` to remove it"
            ) from exc
        if not isinstance(record, dict):
            raise SessionError(
                f"corrupt session record {managed_session_id}: not an object"
            )
        record = self._validate(record, f"session {managed_session_id}")
        embedded = managed_id(record)
        if embedded != managed_session_id:
            raise SessionError(
                f"corrupt session record {managed_session_id}: embedded managed_id "
                f"{embedded!r} does not match its record path"
            )
        normalized = _normalize_legacy_record(record)
        self._validate(normalized, f"session {managed_session_id} (normalized)")
        return normalized

    def _unreadable_record_claims_runtime(
        self, path: Path, identifier: str
    ) -> bool:
        """Conservatively detect runtime ownership in an invalid record."""

        try:
            raw = state.read_private(path)
        except state.StateError as exc:
            raise SessionError(
                f"cannot determine runtime ownership while session record "
                f"{path.stem} is unreadable: {exc}"
            ) from exc
        try:
            document = strict_json.loads(raw)
        except strict_json.StrictJSONError as strict_exc:
            # Strict JSON rejects duplicate keys. Decode once with preserved
            # object pairs so escaped UUIDs and every duplicate ownership field
            # remain visible. If even that fails, ownership is unknowable and
            # all new runtime assignment must fail closed.
            try:
                diagnostic = json.loads(raw, object_pairs_hook=_JSONPairs)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise SessionError(
                    f"cannot determine runtime ownership while session record "
                    f"{path.stem} is malformed: {strict_exc}"
                ) from exc
            if isinstance(diagnostic, _JSONPairs):
                aliases: list[Any] = []
                for key, value in diagnostic:
                    if key in {"runtime_session_id", "session_id"} and value == identifier:
                        return True
                    if key == "runtime_aliases" and isinstance(value, list):
                        aliases.extend(value)
                for alias in aliases:
                    if isinstance(alias, _JSONPairs):
                        if any(
                            key == "session_id" and value == identifier
                            for key, value in alias
                        ):
                            return True
                    elif alias == identifier:
                        return True
            return False
        if not isinstance(document, dict):
            return False
        if document.get("runtime_session_id") == identifier:
            return True
        if document.get("session_id") == identifier:
            return True
        aliases = document.get("runtime_aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, dict) and alias.get("session_id") == identifier:
                    return True
                if alias == identifier:
                    return True
        return False

    def resolve(self, identifier: str) -> dict[str, Any]:
        """Resolve a unique stable ID, runtime ID, or historical runtime alias."""

        if not UUID4.fullmatch(identifier):
            raise SessionError(f"session identifier {identifier!r} is not a UUIDv4")
        if self.exists(identifier):
            return self.load(identifier)
        matches: list[dict[str, Any]] = []
        for path in sorted(self.sessions_dir.glob("*.json")):
            if not UUID4.fullmatch(path.stem):
                continue
            try:
                record = self.load(path.stem)
            except SessionError as exc:
                if self._unreadable_record_claims_runtime(path, identifier):
                    raise SessionError(
                        f"unreadable session record {path.stem} may claim runtime "
                        f"session {identifier}: {exc}; repair or forget that record "
                        "before assigning this runtime UUID"
                    ) from exc
                continue
            ids = {record["runtime_session_id"]} | {
                item["session_id"] for item in record.get("runtime_aliases", [])
            }
            if identifier in ids:
                matches.append(record)
        if not matches:
            raise SessionError(f"no managed session matches {identifier}")
        if len(matches) > 1:
            choices = ", ".join(sorted(managed_id(item) for item in matches))
            raise SessionError(
                f"runtime session id {identifier} maps to multiple managed records: "
                f"{choices}; repair the duplicate mapping"
            )
        return matches[0]

    def reconcile_runtime(
        self,
        stable_id: str,
        *,
        observed_runtime_id: str,
        source: str,
        cwd: str | None = None,
        model: str | None = None,
        model_profile: str | None = None,
        observed_model: str | None = None,
        launch_epoch: int | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Atomically apply one metadata-only lifecycle observation."""

        index_lock = self.runtime_index_lock()
        lock = self.lifecycle_lock(stable_id)
        index_lock.acquire(blocking=True)
        lock.acquire(blocking=True)
        try:
            current = self.load(stable_id)
            try:
                owner = self.resolve(observed_runtime_id)
            except SessionError as exc:
                if "no managed session matches" not in str(exc):
                    raise
                owner = None
            if owner is not None and managed_id(owner) != stable_id:
                if source != "fork":
                    raise SessionError(
                        f"runtime session {observed_runtime_id} is already owned "
                        f"by managed session {managed_id(owner)}"
                    )
                # A delayed/retried fork hook after adoption is already
                # resolved. Remove any stale pending entry instead of
                # re-blocking the parent.
                pending = current.get("pending_forks", [])
                filtered = [
                    dict(item)
                    for item in pending
                    if item.get("session_id") != observed_runtime_id
                ]
                if len(filtered) != len(pending):
                    updated = {**current, "pending_forks": filtered}
                    if current.get("identity_state") != IDENTITY_REPAIR_NEEDED:
                        updated["identity_state"] = _derived_identity_state(updated)
                    self.save(updated)
                    return updated
                return current
            if (
                source == "fork"
                and owner is not None
                and managed_id(owner) == stable_id
            ):
                return current
            updated = reconcile_runtime_record(
                current,
                observed_runtime_id=observed_runtime_id,
                source=source,
                cwd=cwd,
                model=model,
                model_profile=model_profile,
                observed_model=observed_model,
                launch_epoch=launch_epoch,
                now=now,
            )
            self.save(updated)
            return updated
        finally:
            lock.release()
            index_lock.release()

    def relink_runtime(
        self,
        stable_id: str,
        *,
        observed_runtime_id: str,
        cwd: str | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Atomically repair runtime ownership and optional authoritative CWD."""

        if not UUID4.fullmatch(observed_runtime_id):
            raise SessionError(
                f"runtime session_id {observed_runtime_id!r} is not a UUIDv4"
            )
        index_lock = self.runtime_index_lock()
        lock = self.lifecycle_lock(stable_id)
        index_lock.acquire(blocking=True)
        lock.acquire(blocking=True)
        try:
            current = self.load(stable_id)
            try:
                owner = self.resolve(observed_runtime_id)
            except SessionError as exc:
                if "no managed session matches" not in str(exc):
                    raise
            else:
                if managed_id(owner) != stable_id:
                    raise SessionError(
                        f"runtime session {observed_runtime_id} is already owned "
                        f"by managed session {managed_id(owner)}"
                    )
            repaired = {
                **current,
                "launch_epoch": current.get("launch_epoch", 0) + 1,
                "mutation_token": new_mutation_token(),
            }
            # A relink always re-asserts the runtime's home directory: an
            # explicit --cwd re-homes, a bare relink re-asserts the recorded
            # cwd — either way the stale observed_cwd evidence is resolved
            # by the operator's assertion (issue 002). A wrong bare assert
            # degrades to a native "No conversation found", which the
            # resume gate pre-detects; observed_model is untouched.
            if cwd is not None:
                repaired["cwd"] = cwd
            repaired.pop("observed_cwd", None)
            updated = reconcile_runtime_record(
                repaired,
                observed_runtime_id=observed_runtime_id,
                source="repair",
                cwd=repaired["cwd"],
                launch_epoch=repaired["launch_epoch"],
                now=now,
            )
            if updated["cwd"] == current["cwd"]:
                prior_record = self.read_record_bytes(stable_id)
                try:
                    self.save(updated)
                except state.CommittedStateError:
                    if prior_record is not None:
                        self.restore_record_bytes(stable_id, prior_record)
                    raise
                return updated

            session_type = current["session_type"]
            old_pointer = self._pointer_path(current["cwd"], session_type)
            new_pointer = self._pointer_path(updated["cwd"], session_type)
            pointer_locks = [
                state.FileLock(path)
                for path in sorted({old_pointer, new_pointer}, key=str)
            ]
            for pointer_lock in pointer_locks:
                pointer_lock.acquire(blocking=True)
            prior_record = self.read_record_bytes(stable_id)
            prior_pointers: dict[Path, bytes | None] = {}
            try:
                prior_pointers = {
                    old_pointer: state.read_private(old_pointer)
                    if os.path.lexists(old_pointer)
                    else None,
                    new_pointer: state.read_private(new_pointer)
                    if os.path.lexists(new_pointer)
                    else None,
                }
                self.save(updated)
                if self.last(current["cwd"], session_type=session_type) == stable_id:
                    state.remove_private(old_pointer)
                payload = {
                    "cwd": updated["cwd"],
                    "session_id": stable_id,
                    "session_type": session_type,
                }
                state.atomic_write(
                    new_pointer, strict_json.canonical_file_bytes(payload)
                )
                return updated
            except BaseException:
                if prior_record is not None:
                    self.restore_record_bytes(stable_id, prior_record)
                for pointer, data in prior_pointers.items():
                    if data is None:
                        state.remove_private(pointer)
                    else:
                        state.atomic_write(pointer, data)
                raise
            finally:
                for pointer_lock in reversed(pointer_locks):
                    pointer_lock.release()
        finally:
            lock.release()
            index_lock.release()

    def record_session_end(
        self,
        stable_id: str,
        *,
        observed_runtime_id: str,
        reason: str,
        launch_epoch: int | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        """Record advisory SessionEnd metadata without changing resume identity."""

        if not UUID4.fullmatch(observed_runtime_id):
            raise SessionError(
                f"runtime session_id {observed_runtime_id!r} is not a UUIDv4"
            )
        lock = self.lifecycle_lock(stable_id)
        lock.acquire(blocking=True)
        try:
            current = self.load(stable_id)
            observed_epoch = 0 if launch_epoch is None else launch_epoch
            if observed_epoch != current.get("launch_epoch", 0):
                return current
            updated = {
                **current,
                "last_event_source": "end",
                "last_end_reason": reason,
                "last_seen_at": now or _now(),
            }
            # SessionEnd is advisory. Delayed ends from a historical runtime or
            # a pending fork never invalidate the current resume authority.
            self.save(updated)
            return updated
        finally:
            lock.release()

    def exists(self, session_id: str) -> bool:
        return os.path.lexists(self._record_path(session_id))

    def _forget_unlocked(self, session_id: str) -> bool:
        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        return state.remove_private(self._record_path(session_id))

    def converge_pending_forks(self, managed_session_id: str) -> bool:
        """Drop pending forks that hold resume authority; True when changed."""

        lock = self.lifecycle_lock(managed_session_id)
        lock.acquire(blocking=True)
        try:
            current = self.load(managed_session_id)
            updated = drop_resolved_pending_forks(current)
            if updated is None:
                return False
            self.save(updated)
            return True
        finally:
            lock.release()

    def resolve_fork(
        self, managed_session_id: str, fork_runtime_id: str
    ) -> dict[str, Any]:
        """Discard one pending fork marker (metadata-only; transcript stays).

        The fork's transcript remains on disk as a native session and can be
        adopted later with `sessions link`. Refuses when the fork holds the
        resume authority (that case converges via `converge_pending_forks`)
        or when the id is not actually pending.
        """

        if not UUID4.fullmatch(fork_runtime_id):
            raise SessionError(
                f"fork runtime id {fork_runtime_id!r} is not a UUIDv4"
            )
        lock = self.lifecycle_lock(managed_session_id)
        lock.acquire(blocking=True)
        try:
            current = self.load(managed_session_id)
            pending = current.get("pending_forks", [])
            if not any(
                item.get("session_id") == fork_runtime_id for item in pending
            ):
                raise SessionError(
                    f"session {managed_session_id} has no pending fork "
                    f"{fork_runtime_id}"
                )
            if current.get("runtime_session_id") == fork_runtime_id:
                raise SessionError(
                    f"fork {fork_runtime_id} holds the resume authority of "
                    f"session {managed_session_id}; it is already resolved "
                    "(run `claude-multi doctor --repair-all` to clear the marker)"
                )
            kept = [
                dict(item)
                for item in pending
                if item.get("session_id") != fork_runtime_id
            ]
            updated = {
                **current,
                "pending_forks": kept,
                # Same revocation as adoption (H9): the discarded fork's baked
                # epoch goes stale, so its later hooks cannot claim the
                # parent's authority. A still-running parent app reconciles
                # on its next launcher resume.
                "launch_epoch": current.get("launch_epoch", 0) + 1,
            }
            if current.get("identity_state") != IDENTITY_REPAIR_NEEDED:
                updated["identity_state"] = _derived_identity_state(updated)
            self.save(updated)
            return updated
        finally:
            lock.release()

    def forget(self, session_id: str) -> bool:
        """Remove one record under its lifecycle lock."""

        lock = self.lifecycle_lock(session_id)
        lock.acquire(blocking=True)
        try:
            return self._forget_unlocked(session_id)
        finally:
            lock.release()

    def forget_session(self, stable_id: str) -> tuple[bool, bool]:
        """Serialize generated-scope, record, and pointer removal."""

        from . import scope

        lock = self.lifecycle_lock(stable_id)
        lock.acquire(blocking=True)
        try:
            if not self.exists(stable_id):
                return False, False
            current = self.load(stable_id)
            # Validate/remove generated state first. The record is the final
            # irreversible delete so a scope-safety failure remains repairable.
            scope_removed = scope.remove_scope(self.root, stable_id)
            self.clear_last(
                current["cwd"],
                stable_id,
                session_type=current["session_type"],
                blocking=True,
            )
            removed = self._forget_unlocked(stable_id)
            return removed, scope_removed
        finally:
            lock.release()

    def link(self, record: dict[str, Any]) -> Path:
        """Failure-atomically adopt a native runtime and resolve its parent."""

        stable = managed_id(record)
        runtime_id = runtime_session_id(record)
        index_lock = self.runtime_index_lock()
        new_lock = self.lifecycle_lock(stable)
        parent_locks: list[state.FileLock] = []
        parent_updates: list[tuple[str, bytes, dict[str, Any]]] = []
        applied_parents: list[tuple[str, bytes]] = []
        pointer = self._pointer_path(record["cwd"], record["session_type"])
        pointer_lock = state.FileLock(pointer)
        prior_pointer: bytes | None = None
        pointer_locked = False
        linked = False
        index_lock.acquire(blocking=True)
        try:
            new_lock.acquire(blocking=True)
            if self.exists(stable):
                raise SessionError(f"session {stable} is already managed")
            try:
                existing = self.resolve(runtime_id)
            except SessionError as exc:
                if "no managed session matches" not in str(exc):
                    raise
            else:
                raise SessionError(
                    f"runtime session {runtime_id} is already managed as "
                    f"{managed_id(existing)}"
                )

            # Collect every matching parent while holding its lifecycle lock.
            # Unrelated corrupt records are ignored rather than making adoption
            # partially succeed after the new owner is written.
            for path in sorted(self.sessions_dir.glob("*.json")):
                parent_id = path.stem
                if parent_id == stable or not UUID4.fullmatch(parent_id):
                    continue
                parent_lock = self.lifecycle_lock(parent_id)
                parent_lock.acquire(blocking=True)
                try:
                    try:
                        current = self.load(parent_id)
                    except SessionError:
                        parent_lock.release()
                        continue
                    pending = current.get("pending_forks", [])
                    filtered = [
                        dict(item)
                        for item in pending
                        if item.get("session_id") != runtime_id
                    ]
                    if len(filtered) == len(pending):
                        parent_lock.release()
                        continue
                    prior_bytes = self.read_record_bytes(parent_id)
                    if prior_bytes is None:
                        parent_lock.release()
                        continue
                    updated = {
                        **current,
                        "pending_forks": filtered,
                        # Revoke the adopted fork's baked credential: its scope
                        # carries the parent's managed-id + this epoch, so its
                        # later hooks could otherwise migrate the parent's
                        # authority into the fork's lineage (review H9). A
                        # still-running parent app's hooks go stale until its
                        # next launcher resume — the relink-runtime trade-off.
                        "launch_epoch": current.get("launch_epoch", 0) + 1,
                    }
                    if current.get("identity_state") != IDENTITY_REPAIR_NEEDED:
                        updated["identity_state"] = _derived_identity_state(updated)
                    parent_locks.append(parent_lock)
                    parent_updates.append((parent_id, prior_bytes, updated))
                except BaseException:
                    parent_lock.release()
                    raise

            pointer_lock.acquire(blocking=True)
            pointer_locked = True
            prior_pointer = (
                state.read_private(pointer) if os.path.lexists(pointer) else None
            )
            try:
                for parent_id, prior_bytes, updated in parent_updates:
                    try:
                        self.save(updated)
                    except state.CommittedStateError:
                        if self.read_record_bytes(parent_id) == strict_json.canonical_file_bytes(
                            updated
                        ):
                            applied_parents.append((parent_id, prior_bytes))
                        raise
                    else:
                        applied_parents.append((parent_id, prior_bytes))
                try:
                    linked_path = self.save(record)
                except state.CommittedStateError:
                    linked = self.read_record_bytes(stable) == strict_json.canonical_file_bytes(
                        record
                    )
                    raise
                linked = True
                payload = {
                    "cwd": record["cwd"],
                    "session_id": stable,
                    "session_type": record["session_type"],
                }
                state.atomic_write(pointer, strict_json.canonical_file_bytes(payload))
                return linked_path
            except BaseException:
                if linked:
                    self._forget_unlocked(stable)
                for parent_id, prior_bytes in reversed(applied_parents):
                    self.restore_record_bytes(parent_id, prior_bytes)
                try:
                    if prior_pointer is None:
                        state.remove_private(pointer)
                    else:
                        state.atomic_write(pointer, prior_pointer)
                except OSError:
                    pass
                raise
        finally:
            if pointer_locked:
                pointer_lock.release()
            for parent_lock in reversed(parent_locks):
                parent_lock.release()
            new_lock.release()
            index_lock.release()

    # Exact-byte pre-read/restore (action-aware execve cleanup) -----------

    def runtime_index_lock(self) -> state.FileLock:
        """Serialize mutations that can claim a native runtime UUID."""

        locks_dir = state.ensure_private_dir(self.root / "locks")
        return state.FileLock(locks_dir / "runtime-index")

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

    def read_pointer_bytes(
        self, cwd: str, *, session_type: str = SESSION_TYPE_MANAGED
    ) -> bytes | None:
        """Exact on-disk pointer bytes; None when no pointer exists."""

        pointer = self._pointer_path(cwd, session_type)
        if not os.path.lexists(pointer):
            return None
        return state.read_private(pointer)

    def restore_pointer_bytes(
        self,
        cwd: str,
        session_id: str,
        data: bytes | None,
        *,
        session_type: str = SESSION_TYPE_MANAGED,
    ) -> bool:
        """Compare-and-restore a type-specific pointer changed by a launch."""

        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        pointer = self._pointer_path(cwd, session_type)
        lock = state.FileLock(pointer)
        if not lock.acquire(blocking=False):
            return False
        try:
            if self.last(cwd, session_type=session_type) != session_id:
                return False
            if data is None:
                return state.remove_private(pointer)
            state.atomic_write(pointer, data)
            return True
        finally:
            lock.release()

    def _pointer_path(self, cwd: str, session_type: str = SESSION_TYPE_MANAGED) -> Path:
        if session_type not in {SESSION_TYPE_MANAGED, SESSION_TYPE_ORDINARY}:
            raise SessionError(f"invalid session_type {session_type!r}")
        digest = strict_json.sha256_hex(cwd.encode("utf-8"))[:32]
        suffix = "" if session_type == SESSION_TYPE_MANAGED else ".ordinary"
        return self.pointers_dir / f"{digest}{suffix}.json"

    def update_last(
        self,
        cwd: str,
        session_id: str,
        *,
        session_type: str = SESSION_TYPE_MANAGED,
        blocking: bool = False,
    ) -> bool:
        """Record the per-CWD last session for one session type."""

        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        pointer = self._pointer_path(cwd, session_type)
        lock = state.FileLock(pointer)
        if not lock.acquire(blocking=blocking):
            return False
        try:
            payload = {
                "cwd": cwd,
                "session_id": session_id,
                "session_type": session_type,
            }
            state.atomic_write(pointer, strict_json.canonical_file_bytes(payload))
            return True
        finally:
            lock.release()

    def last(
        self, cwd: str, *, session_type: str = SESSION_TYPE_MANAGED
    ) -> str | None:
        pointer = self._pointer_path(cwd, session_type)
        if not pointer.exists():
            return None
        try:
            payload = strict_json.loads(state.read_private(pointer))
        except (state.StateError, strict_json.StrictJSONError):
            return None
        if not isinstance(payload, dict):
            return None
        stored_type = payload.get("session_type", SESSION_TYPE_MANAGED)
        if stored_type != session_type:
            return None
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not UUID4.fullmatch(session_id):
            return None
        return session_id

    def clear_last(
        self,
        cwd: str,
        session_id: str,
        *,
        session_type: str = SESSION_TYPE_MANAGED,
        blocking: bool = False,
    ) -> bool:
        """Clear a type-specific pointer only when it names session_id."""

        if not UUID4.fullmatch(session_id):
            raise SessionError(f"session_id {session_id!r} is not a UUIDv4")
        pointer = self._pointer_path(cwd, session_type)
        lock = state.FileLock(pointer)
        if not lock.acquire(blocking=blocking):
            return False
        try:
            if self.last(cwd, session_type=session_type) != session_id:
                return False
            return state.remove_private(pointer)
        finally:
            lock.release()
