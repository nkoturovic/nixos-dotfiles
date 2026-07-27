"""Composition transitions for managed sessions (TRANSITIONS.md, v1).

v1 is relaunch-only: every composition change — variants, lead model/effort,
workflow mode, permission denies — is applied by compiling generation N+1 and
relaunching the same session UUID with ``claude --resume <uuid>`` after the
user confirms the target Claude process has exited. No mixed old/new
composition is ever applied to a running process, and the shared daemon is
never contacted.

Authority model (TRANSITIONS section 2): the record is intent (composition
snapshot + catalog hash), the installed catalog is the trusted source of
bodies/selectors, and the scope is a pure function of both. The record stays
the authority in every crash window; the scope is always re-derivable from
record+catalog.

Flow (TRANSITIONS section 3):

1. :func:`prepare` validates — the record exists and is durable, the target
   composition resolves against the installed catalog — and computes the
   semantic diff (:func:`build_diff`).
2. :func:`execute` requires ``confirm_exited=True``; without it (and always
   from inside the target session, detected via the
   ``CLAUDE_MULTI_SESSION_ID`` sentinel) it returns a ``print_only`` outcome
   carrying the diff and the exact post-exit command, and mutates nothing.
3. On confirmation it compiles generation N+1, stages the scope into the
   sibling ``scopes/.<uuid>.new/``, swaps (live -> ``.<uuid>.prev``, then
   ``.<uuid>.new`` -> live, fsyncing ``scopes/`` after each rename), and
   saves the generation N+1 record. The returned outcome carries the
   launch-ready ``compile_result`` and ``record``; the caller performs the
   actual exec via ``launch.perform_launch`` with action kind ``transition``.
4. If that execve raises ``OSError`` the caller invokes
   :func:`restore_exec_failure`, which restores the prior scope generation
   (``.<uuid>.prev`` -> live) and the exact pre-read prior record bytes —
   never regenerated content. The restore is ownership-guarded
   (compare-and-swap on the exact generation N+1 record bytes the outcome
   carries): when a newer attempt has committed since, the restore is a
   no-op and the newer attempt owns the state.

Lifecycle authority (audit L1): every record/scope mutation path re-reads
its authority AFTER acquiring the per-session lifecycle lock, never before.
:func:`execute` re-validates the plan's ``(scope_generation,
composition_hash)`` against the current record inside the lock and aborts
fail-closed when a concurrent transition advanced the session;
:func:`converge` loads the record inside the lock;
:meth:`sessions.SessionStore.link` rechecks existence and saves under the
lock; ``launch.perform_launch`` captures its existence guards and pre-launch
bytes under the lock.

Crash convergence (TRANSITIONS section 4): :func:`converge` detects every
window state — staged ``.new`` before the first rename, missing live with
``.prev`` present between renames, live at N+1 with the record still at N
after promotion, live matching the record after the record save — and
restores record-authoritative state deterministically, recompiling the scope
from record+installed catalog when content drifted.

Transitions relaunch with no passthrough argv: cwd and ``--add-dir`` set
changes are out of scope for v1 (TRANSITIONS section 1); the exact-cm-*
collision gate re-runs against the target scope before any mutation so a new
project-level collision fails closed with nothing touched.
"""

from __future__ import annotations

import copy
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import compiler, composition, scope, sessions, state, strict_json
from .catalog import Catalog
from .composition import ResolvedComposition


class TransitionError(RuntimeError):
    """Raised when a transition cannot proceed (fail closed, nothing mutated)."""


RELAUNCH = "relaunch"
PRINT_ONLY = "print_only"

# Environment sentinel compiled into every managed launch (compiler.py); the
# session's own agents and nested CLI invocations self-identify against it.
SESSION_ENV_VAR = "CLAUDE_MULTI_MANAGED_ID"
LEGACY_SESSION_ENV_VAR = "CLAUDE_MULTI_SESSION_ID"

# Exact post-exit command printed by every print-only outcome. Lane C's CLI
# renders this verbatim.
COMMAND_TEMPLATE = "claude-multi sessions transition {session_id} --composition {name}"


@dataclass(frozen=True)
class CatalogMeta:
    """Installed-catalog facts the semantic diff needs beyond the resolution."""

    installed_hash: str
    generic_agent_aliases: tuple[str, ...]


def catalog_meta_from_catalog(trusted: Catalog) -> CatalogMeta:
    """Build :class:`CatalogMeta` from the loaded trusted bundle."""

    return CatalogMeta(
        installed_hash=trusted.bundle_sha256,
        generic_agent_aliases=tuple(
            trusted.docs["native-contract"]["generic_agent_aliases"]["values"]
        ),
    )


def build_diff(
    record: dict[str, Any],
    target_resolved: ResolvedComposition,
    catalog_meta: CatalogMeta,
) -> list[str]:
    """Semantic diff between the recorded composition and the target. Pure.

    Lines cover variants added/removed/changed (by variant ID with per-field
    detail), lead model/client_selector/effort, workflow mode, permission
    deny changes, and — last — the catalog-drift line when the record's
    catalog hash differs from the installed hash. A transition with no
    semantic changes yields exactly ``["no semantic composition changes"]``
    (plus the drift line when applicable).
    """

    snapshot = record["snapshot"]
    lines: list[str] = []

    old_variants = {variant["id"]: variant for variant in snapshot["variants"]}
    new_variants = {variant.id: variant for variant in target_resolved.variants}
    for variant_id in sorted(new_variants):
        if variant_id in old_variants:
            continue
        variant = new_variants[variant_id]
        lines.append(
            f"variant added: {variant_id} (role {variant.role}, "
            f"model {variant.model}, lane {variant.lane}, "
            f"selector {variant.client_selector})"
        )
    for variant_id in sorted(old_variants):
        if variant_id in new_variants:
            continue
        variant = old_variants[variant_id]
        lines.append(
            f"variant removed: {variant_id} (role {variant['role']}, "
            f"model {variant['model']}, lane {variant['lane']}, "
            f"selector {variant['client_selector']})"
        )
    for variant_id in sorted(set(old_variants) & set(new_variants)):
        old = old_variants[variant_id]
        new = new_variants[variant_id]
        changes: list[str] = []
        for field_name, new_value in (
            ("role", new.role),
            ("model", new.model),
            ("lane", new.lane),
            ("client_selector", new.client_selector),
            ("preferred", new.preferred),
        ):
            if old[field_name] != new_value:
                changes.append(f"{field_name} {old[field_name]!r} -> {new_value!r}")
        if changes:
            lines.append(f"variant changed: {variant_id}: " + "; ".join(changes))

    old_lead = snapshot["lead"]
    new_lead = target_resolved.lead
    if old_lead["model"] != new_lead.model:
        lines.append(f"lead model: {old_lead['model']} -> {new_lead.model}")
    if old_lead["client_selector"] != new_lead.client_selector:
        lines.append(
            f"lead client_selector: {old_lead['client_selector']} -> "
            f"{new_lead.client_selector}"
        )
    if old_lead["effort"] != new_lead.effort:
        lines.append(f"lead effort: {old_lead['effort']} -> {new_lead.effort}")

    old_workflows = snapshot.get("workflows", "native")
    if old_workflows != target_resolved.workflows:
        lines.append(f"workflows: {old_workflows} -> {target_resolved.workflows}")

    _, old_denies = compiler.compile_native_policy(
        snapshot["native_agents"], catalog_meta.generic_agent_aliases
    )
    _, new_denies = compiler.compile_native_policy(
        target_resolved.native_agents, catalog_meta.generic_agent_aliases
    )
    for deny in new_denies:
        if deny not in old_denies:
            lines.append(f"permission deny added: {deny}")
    for deny in old_denies:
        if deny not in new_denies:
            lines.append(f"permission deny removed: {deny}")

    if not lines:
        lines.append("no semantic composition changes")
    if record["catalog_hash"] != catalog_meta.installed_hash:
        lines.append(
            "catalog drift: the installed trusted catalog "
            f"({catalog_meta.installed_hash}) differs from the catalog the "
            f"record was compiled against ({record['catalog_hash']}); the "
            "relaunch recompiles against the installed catalog"
        )
    return lines


@dataclass(frozen=True)
class Plan:
    """A validated transition: both record generations, the diff, the deps.

    ``prior_record`` is the loaded generation-N record; ``new_record`` is the
    generation N+1 record (saved only by :func:`execute`). ``store`` and
    ``trusted`` are carried so :func:`execute` is self-contained.
    """

    session_id: str
    prior_record: dict[str, Any]
    new_record: dict[str, Any]
    target_resolved: ResolvedComposition
    diff: tuple[str, ...]
    command_text: str
    store: sessions.SessionStore
    trusted: Catalog


def prepare(
    store: sessions.SessionStore,
    session_id: str,
    target_document: dict[str, Any],
    trusted: Catalog,
) -> Plan:
    """Validate a transition and return its plan. No effects; fail closed.

    Checks: the session ID is a managed-session UUID, the record exists and
    is durable (legacy sessions upgrade via resume first), and the target
    composition resolves against the installed catalog. The diff is computed
    against the record snapshot — the intent authority.
    """

    if not sessions.UUID4.fullmatch(session_id):
        raise TransitionError(
            f"session id {session_id!r} is not a managed-session UUID"
        )
    # Resolve either the stable managed ID or a current/historical runtime ID.
    record = store.resolve(session_id)
    stable_id = sessions.managed_id(record)
    if record["session_type"] != sessions.SESSION_TYPE_MANAGED:
        raise TransitionError("ordinary gateway sessions have no composition to transition")
    if record["mode"] != "durable":
        raise TransitionError(
            f"session {stable_id} is {record['mode']}; v1 transitions require "
            "a durable session — resume it once to upgrade, then transition"
        )
    try:
        resolved = composition.resolve(trusted.docs, target_document)
    except (composition.CompositionError, KeyError, TypeError) as exc:
        raise TransitionError(f"target composition does not resolve: {exc}") from exc
    snapshot = composition.snapshot(resolved)
    version_doc = trusted.docs["version"]
    try:
        new_record = sessions.transition_record(
            record,
            snapshot=snapshot,
            composition_name=resolved.name,
            workflows=resolved.workflows,
            catalog_version=version_doc["catalog_version"],
            catalog_hash=trusted.bundle_sha256,
            launcher_version=version_doc["launcher_version"],
        )
    except sessions.SessionError as exc:
        raise TransitionError(str(exc)) from exc
    diff = build_diff(record, resolved, catalog_meta_from_catalog(trusted))
    return Plan(
        session_id=stable_id,
        prior_record=record,
        new_record=new_record,
        target_resolved=resolved,
        diff=tuple(diff),
        command_text=COMMAND_TEMPLATE.format(
            session_id=stable_id, name=resolved.name
        ),
        store=store,
        trusted=trusted,
    )


@dataclass(frozen=True)
class Outcome:
    """Result of :func:`execute`.

    ``print_only`` carries the diff and the exact post-exit command text and
    guarantees nothing was mutated. ``relaunch`` carries the launch-ready
    ``compile_result`` and generation N+1 ``record`` (both already persisted:
    scope swapped, record saved) plus the exact ``prior_record_bytes`` the
    caller needs for :func:`restore_exec_failure` if the exec raises
    ``OSError`` and the exact ``committed_record_bytes`` this execute wrote —
    the ownership token for the restore's compare-and-swap guard (audit L2).
    """

    kind: str  # "relaunch" | "print_only"
    diff: list[str]
    command_text: str | None = None
    compile_result: compiler.CompileResult | None = None
    record: dict[str, Any] | None = None
    prior_record_bytes: bytes | None = None
    committed_record_bytes: bytes | None = None


def _check_real_scope_dir(path: Path, what: str) -> None:
    """Fail closed when an existing scope path is a symlink or not a directory."""

    if not os.path.lexists(path):
        return
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise TransitionError(f"{what} scope path {path} is a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise TransitionError(f"{what} scope path {path} is not a directory")


def _stage_scope(
    state_root: Path | str, session_id: str, plan: scope.ScopePlan
) -> Path:
    """Stage ``scopes/.<uuid>.new/`` without touching the live scope.

    Mirrors the staging half of ``scope.write_scope`` (per-file
    ``state.atomic_write``, 0700 dirs, stale staging removed first); the swap
    renames are the caller's job so the crash windows stay explicit.
    """

    scope._check_session_id(session_id)
    scopes_root = state.ensure_private_dir(Path(state_root) / "scopes")
    staging = scopes_root / f".{session_id}.new"
    if os.path.lexists(staging):
        scope._remove_tree(staging)
    state.ensure_private_dir(staging)
    for relpath, data in sorted(plan.agent_files.items()):
        relative = scope._check_relpath(relpath)
        target = staging.joinpath(*relative.parts)
        state.ensure_private_dir(target.parent)
        state.atomic_write(target, data)
    state.atomic_write(
        staging / "settings.json", strict_json.canonical_file_bytes(plan.settings)
    )
    return staging


def execute(
    plan: Plan,
    *,
    confirm_exited: bool,
    environ: dict[str, str] | None = None,
    dir_fsync: Callable[[Path], None] | None = None,
) -> Outcome:
    """Apply a prepared transition. Print-only paths never mutate.

    From inside the target session (``CLAUDE_MULTI_SESSION_ID`` matches) the
    outcome is always ``print_only`` regardless of ``confirm_exited``
    (TRANSITIONS section 3 step 4). Otherwise ``confirm_exited=False`` yields
    ``print_only`` with the exact post-exit command. On confirmation: compile
    generation N+1 (pure — a compile error touches nothing), re-run the
    exact-cm-* gate, then — inside the lifecycle lock — re-read the current
    record and re-validate the plan's ``(scope_generation, composition_hash)``
    against it, aborting fail-closed when a concurrent transition advanced
    the session (audit L1); only then capture the prior record bytes, stage
    ``.new``, swap with ``.prev`` (fsync after each rename), and save the
    generation N+1 record. The caller performs the actual exec.
    """

    env = os.environ if environ is None else environ
    if (
        env.get(SESSION_ENV_VAR) == plan.session_id
        or env.get(LEGACY_SESSION_ENV_VAR) == plan.session_id
    ):
        return Outcome(
            kind=PRINT_ONLY, diff=list(plan.diff), command_text=plan.command_text
        )
    if not confirm_exited:
        return Outcome(
            kind=PRINT_ONLY, diff=list(plan.diff), command_text=plan.command_text
        )

    sync = dir_fsync if dir_fsync is not None else scope._fsync_directory
    store = plan.store
    session_id = plan.session_id
    trusted = plan.trusted
    digest = strict_json.bundle_digest(plan.new_record["snapshot"])

    # Pure compile first: a compile error leaves nothing touched (section 5).
    result = compiler.compile_launch(
        docs=trusted.docs,
        prompt_bodies=trusted.prompt_bodies,
        resolved=plan.target_resolved,
        session_action=compiler.build_resume(
            session_id, sessions.runtime_session_id(plan.prior_record)
        ),
        passthrough=[],
        settings_path=trusted.root / "settings.json",
        lead_prompt_path=compiler.lead_prompt_path(store.root, digest, session_id),
        durable=True,
        scope_dir=scope.scope_dir(store.root, session_id),
        hook_command=str(
            scope.ensure_hook_shim(
                store.root, scope.resolve_hook_command(env, trusted.root)
            )
        ),
        token_helper_command=scope.ensure_token_helper_command(store.root, env),
        launch_epoch=plan.new_record.get("launch_epoch", 0),
    )
    if result.scope_plan is None:
        raise TransitionError("durable compile produced no scope plan")

    # Exact-cm-* gate against the target scope, before any mutation.
    collisions = scope.find_cm_collisions(
        plan.prior_record["cwd"], (), result.scope_plan.agent_names
    )
    if collisions:
        formatted = "; ".join(
            f"{path} (agent name {name!r})" for path, name in collisions
        )
        raise TransitionError(
            "exact cm-* agent name collision outside the managed scope: "
            f"{formatted}; rename the colliding project agent before "
            "transitioning"
        )

    # The lifecycle lock serializes the staging/swap/save mutation against a
    # concurrent launcher or transition on the same UUID (finisher finding).
    lock = store.lifecycle_lock(session_id)
    lock.acquire(blocking=True)
    try:
        # Re-validate the plan's authority AFTER acquiring the lock (audit
        # L1): the record prepare sampled is stale the moment the lock is
        # contended. A concurrent transition that already committed bumps the
        # generation/hash; this plan must abort before anything mutates.
        prior_bytes = store.read_record_bytes(session_id)
        if prior_bytes is None:
            raise TransitionError(
                f"session record {session_id} vanished mid-transition"
            )
        current = store.load(session_id)
        if (
            sessions.managed_id(current) != session_id
            or current["session_type"] != sessions.SESSION_TYPE_MANAGED
        ):
            raise TransitionError(
                "session identity changed concurrently; re-run the transition"
            )
        if (
            current["scope_generation"] != plan.prior_record["scope_generation"]
            or current["composition_hash"] != plan.prior_record["composition_hash"]
        ):
            raise TransitionError(
                "session composition advanced concurrently; re-run the transition"
            )
        if (
            current.get("launch_epoch", 0)
            != plan.prior_record.get("launch_epoch", 0)
            or current.get("mutation_token")
            != plan.prior_record.get("mutation_token")
            or plan.new_record.get("launch_epoch", 0)
            != current.get("launch_epoch", 0) + 1
        ):
            raise TransitionError(
                "session launch authority advanced concurrently; re-run the transition"
            )
        if sessions.runtime_session_id(current) != sessions.runtime_session_id(
            plan.prior_record
        ):
            raise TransitionError(
                "session runtime identity changed concurrently; re-run the transition"
            )
        if current["cwd"] != plan.prior_record["cwd"]:
            raise TransitionError(
                "session CWD changed concurrently; re-run the transition"
            )
        identity_state = current.get(
            "identity_state", sessions.IDENTITY_UNVERIFIED
        )
        if current.get("pending_forks"):
            raise TransitionError(sessions.pending_fork_message(current))
        model_repair = (
            identity_state == sessions.IDENTITY_REPAIR_NEEDED
            and "observed_model" in current
            and "observed_cwd" not in current
        )
        if identity_state == sessions.IDENTITY_REPAIR_NEEDED and not model_repair:
            raise TransitionError(
                "session runtime/CWD identity needs repair before transition"
            )
        committed_record = sessions.carry_lifecycle_state(plan.new_record, current)
        if model_repair:
            committed_record.pop("observed_model", None)
            committed_record["identity_state"] = sessions.IDENTITY_UNVERIFIED
        committed_record = {
            **committed_record,
            "mutation_token": sessions.new_mutation_token(),
        }

        scopes_root = state.ensure_private_dir(Path(store.root) / "scopes")
        live = scopes_root / session_id
        prev = scopes_root / f".{session_id}.prev"
        _check_real_scope_dir(live, "live")
        _check_real_scope_dir(prev, "previous-generation")
        staging = _stage_scope(store.root, session_id, result.scope_plan)
        live_moved = False
        staging_moved = False
        try:
            if os.path.lexists(prev):
                # The previous successful transition's rollback copy is
                # superseded (TRANSITIONS section 3 step 7).
                scope._remove_tree(prev)
                sync(scopes_root)
            if os.path.lexists(live):
                os.rename(live, prev)
                live_moved = True
                sync(scopes_root)
            os.rename(staging, live)
            staging_moved = True
            sync(scopes_root)
        except OSError as exc:
            try:
                if staging_moved and os.path.lexists(live):
                    scope._remove_tree(live)
                if live_moved and os.path.lexists(prev):
                    os.rename(prev, live)
                if os.path.lexists(staging):
                    scope._remove_tree(staging)
                sync(scopes_root)
            except OSError as rollback_exc:
                raise TransitionError(
                    "scope transition failed and rollback durability could not "
                    f"be confirmed: {exc}; rollback: {rollback_exc}; run "
                    f"`claude-multi doctor --repair {session_id}`"
                ) from exc
            raise TransitionError(
                f"scope transition failed before the record commit: {exc}; "
                "the prior scope was restored"
            ) from exc

        def restore_prior_scope() -> None:
            if os.path.lexists(prev):
                if os.path.lexists(live):
                    scope._remove_tree(live)
                os.rename(prev, live)
            elif os.path.lexists(live):
                scope._remove_tree(live)
            if os.path.lexists(staging):
                scope._remove_tree(staging)
            sync(scopes_root)

        try:
            store.save(committed_record)
        except state.CommittedStateError:
            current_bytes = store.read_record_bytes(session_id)
            if current_bytes == strict_json.canonical_file_bytes(committed_record):
                store.restore_record_bytes(session_id, prior_bytes)
                restore_prior_scope()
            raise
        except BaseException as exc:
            try:
                restore_prior_scope()
            except OSError as rollback_exc:
                raise TransitionError(
                    "record commit failed before replacement and scope rollback "
                    f"could not be confirmed: {exc}; rollback: {rollback_exc}; run "
                    f"`claude-multi doctor --repair {session_id}`"
                ) from exc
            raise
        # The exact bytes this execute committed: the restore guard's
        # ownership token (CAS-by-own-write, audit L2).
        committed_bytes = store.read_record_bytes(session_id)
    finally:
        lock.release()
    return Outcome(
        kind=RELAUNCH,
        diff=list(plan.diff),
        compile_result=result,
        record=committed_record,
        prior_record_bytes=prior_bytes,
        committed_record_bytes=committed_bytes,
    )


def restore_exec_failure(
    store: sessions.SessionStore,
    session_id: str,
    prior_record_bytes: bytes,
    *,
    expected_record_bytes: bytes,
    dir_fsync: Callable[[Path], None] | None = None,
) -> bool:
    """Restore prior state after the relaunch execve raised ``OSError``.

    Restores the exact pre-read prior record bytes first, so the record remains
    the authority even if the process crashes during the following filesystem
    rollback. Then restores the prior scope generation: ``.<uuid>.prev`` ->
    live when the swap had moved a prior live scope aside; otherwise the staged
    N+1 live scope is removed (the prior generation had no live scope).

    Ownership guard (CAS-by-own-write, audit L2): when
    ``expected_record_bytes`` is given — the exact generation N+1 bytes the
    failing execute committed, carried on its outcome — the whole restore is
    a no-op unless the current on-disk record still equals those bytes. A
    newer attempt that committed in between owns the state; nothing is
    restored or removed. Callers that predate the guard omit the argument and
    restore unconditionally.
    """

    if not sessions.UUID4.fullmatch(session_id):
        raise TransitionError(
            f"session id {session_id!r} is not a managed-session UUID"
        )
    if expected_record_bytes is None:
        raise TransitionError(
            "restore_exec_failure requires the exact committed record bytes "
            "(ownership token); refusing an unconditional rollback"
        )
    lock = store.lifecycle_lock(session_id)
    lock.acquire(blocking=True)
    try:
        current_bytes = store.read_record_bytes(session_id)
        try:
            expected = strict_json.loads(expected_record_bytes)
            current = store.load(session_id)
        except (strict_json.StrictJSONError, sessions.SessionError):
            return False
        if (
            not isinstance(expected, dict)
            or expected.get("mutation_token") is None
            or current.get("mutation_token") != expected.get("mutation_token")
        ):
            # A newer attempt committed since the failing execute; it owns
            # the record and the scope now — restore nothing.
            return False
        if current_bytes == expected_record_bytes:
            store.restore_record_bytes(session_id, prior_record_bytes)
        else:
            try:
                prior = strict_json.loads(prior_record_bytes)
                if not isinstance(prior, dict):
                    return False
                prior = sessions._normalize_legacy_record(prior)
                store.save(sessions.carry_lifecycle_state(prior, current))
            except (strict_json.StrictJSONError, sessions.SessionError):
                return False
        sync = dir_fsync if dir_fsync is not None else scope._fsync_directory
        # Validate the scopes parent before destructive work beneath it: a
        # symlinked ancestor fails closed here (final audit L3).
        scopes_root = state.ensure_private_dir(Path(store.root) / "scopes")
        live = scopes_root / session_id
        prev = scopes_root / f".{session_id}.prev"
        staging = scopes_root / f".{session_id}.new"
        _check_real_scope_dir(live, "live")
        _check_real_scope_dir(prev, "previous-generation")
        if os.path.lexists(prev):
            if os.path.lexists(live):
                scope._remove_tree(live)
            os.rename(prev, live)
            sync(scopes_root)
        elif os.path.lexists(live):
            scope._remove_tree(live)
            sync(scopes_root)
        if os.path.lexists(staging):
            scope._remove_tree(staging)
            sync(scopes_root)
        return True
    finally:
        lock.release()


def _document_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a resolvable composition document from a record snapshot.

    The reconstruction resolves against the installed catalog, which is
    exactly the section 2 authority rule: repair recompiles from
    record+current catalog. Unlike ``cli.snapshot_to_document`` this also
    carries the workflow mode recorded in the snapshot.
    """

    snapshot = record["snapshot"]
    slots: list[dict[str, Any]] = [
        {"role": composition.LEAD_ID, "model": snapshot["lead"]["model"]}
    ]
    for variant in snapshot["variants"]:
        slots.append(
            {
                "role": variant["role"],
                "model": variant["model"],
                "lane": variant["lane"],
                "preferred": variant["preferred"],
            }
        )
    document: dict[str, Any] = {
        "version": 1,
        "name": record["composition_name"],
        "description": "Recorded managed-session composition",
        "availability": copy.deepcopy(snapshot["availability"]),
        "slots": slots,
        "native_agents": copy.deepcopy(snapshot["native_agents"]),
    }
    workflows = snapshot.get("workflows", record.get("workflows", "native"))
    if workflows != "native":
        document["workflows"] = workflows
    return document


def _ordinary_expected_plan(
    record: dict[str, Any],
    trusted: Catalog,
    hook_command: str,
    token_helper_command: str,
) -> scope.ScopePlan:
    """The record-authoritative ordinary gateway scope."""

    model = trusted.docs["models"]["models"].get(record["ordinary_model"])
    if model is None:
        raise TransitionError(
            f"ordinary model {record['ordinary_model']!r} is no longer in the "
            "installed catalog; resume with an explicit supported `--model`"
        )
    try:
        selectors = compiler.direct_profile_selectors(
            trusted.docs, record["context_profile"]
        )
    except compiler.CompilerError as exc:
        raise TransitionError(str(exc)) from exc
    return scope.compile_ordinary_scope(
        managed_id=sessions.managed_id(record),
        hook_command=hook_command,
        available_models=selectors,
        default_model=model["client_selector"],
        launch_epoch=record.get("launch_epoch", 0),
        gateway_base_url=trusted.docs["gateway"]["gateway"]["base_url"],
        token_helper_command=token_helper_command,
    )


def _expected_plan(
    record: dict[str, Any],
    trusted: Catalog,
    *,
    state_root: Path | str | None = None,
    hook_command: str | None = None,
    token_helper_command: str | None = None,
) -> scope.ScopePlan:
    """The record-authoritative scope: record composition + installed catalog."""

    document = _document_from_record(record)
    resolved = composition.resolve(trusted.docs, document)
    if hook_command is None or token_helper_command is None:
        if state_root is None:
            raise TransitionError(
                "record-authoritative compile requires state_root or hook_command"
            )
        hook_command = str(
            scope.ensure_hook_shim(
                state_root, scope.resolve_hook_command(None, trusted.root)
            )
        )
        token_helper_command = scope.ensure_token_helper_command(state_root)
    return scope.compile_scope(
        resolved,
        trusted.docs["roles"]["roles"],
        trusted.prompt_bodies,
        scope.catalog_meta_from_docs(trusted.docs),
        managed_id=sessions.managed_id(record),
        hook_command=hook_command,
        launch_epoch=record.get("launch_epoch", 0),
        token_helper_command=token_helper_command,
    )


def _live_drift(live: Path, plan: scope.ScopePlan) -> list[str]:
    """Why the on-disk live scope differs from the expected plan (empty == match)."""

    expected: dict[str, bytes] = {
        **plan.agent_files,
        "settings.json": strict_json.canonical_file_bytes(plan.settings),
    }
    expected_dirs: set[str] = set()
    for relpath in expected:
        parent = Path(relpath).parent
        while parent != Path("."):
            expected_dirs.add(parent.as_posix())
            parent = parent.parent

    problems: list[str] = []
    seen: set[str] = set()
    owner = os.geteuid()
    root_info = os.lstat(live)
    if root_info.st_uid != owner:
        problems.append("scope root is not owner-controlled")
    if stat.S_IMODE(root_info.st_mode) != 0o700:
        problems.append("scope root mode is not 0700")

    for dirpath, dirnames, filenames in os.walk(live, topdown=True, followlinks=False):
        directory = Path(dirpath)
        for dirname in sorted(list(dirnames)):
            full = directory / dirname
            relpath = full.relative_to(live).as_posix()
            try:
                info = os.lstat(full)
            except OSError as exc:
                problems.append(f"cannot inspect directory {relpath}: {exc}")
                dirnames.remove(dirname)
                continue
            if stat.S_ISLNK(info.st_mode):
                problems.append(f"directory {relpath} is a symlink")
                dirnames.remove(dirname)
                continue
            if not stat.S_ISDIR(info.st_mode):
                problems.append(f"{relpath} is not a directory")
                dirnames.remove(dirname)
                continue
            if relpath not in expected_dirs:
                problems.append(f"unexpected directory {relpath}")
                dirnames.remove(dirname)
                continue
            unsafe = False
            if info.st_uid != owner:
                problems.append(f"directory {relpath} is not owner-controlled")
                unsafe = True
            if stat.S_IMODE(info.st_mode) != 0o700:
                problems.append(f"directory {relpath} mode is not 0700")
                unsafe = True
            if unsafe:
                dirnames.remove(dirname)

        for filename in sorted(filenames):
            full = directory / filename
            relpath = full.relative_to(live).as_posix()
            seen.add(relpath)
            data = expected.get(relpath)
            if data is None:
                problems.append(f"unexpected file {relpath}")
                continue
            try:
                info = os.lstat(full)
            except OSError as exc:
                problems.append(f"cannot inspect file {relpath}: {exc}")
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                problems.append(f"{relpath} is not a regular file")
                continue
            if info.st_uid != owner:
                problems.append(f"{relpath} is not owner-controlled")
                continue
            if stat.S_IMODE(info.st_mode) != 0o600:
                problems.append(f"{relpath} mode is not 0600")
                continue
            try:
                actual = full.read_bytes()
            except OSError as exc:
                problems.append(f"cannot read file {relpath}: {exc}")
                continue
            if actual != data:
                problems.append(f"{relpath} content differs")
    for relpath in sorted(set(expected) - seen):
        problems.append(f"missing file {relpath}")
    return problems


def converge(
    state_root: Path | str,
    store: sessions.SessionStore,
    session_id: str,
    trusted: Catalog,
    *,
    dir_fsync: Callable[[Path], None] | None = None,
) -> list[str]:
    """Converge a session's scope state to record authority (section 4).

    Deterministic repair used by ``doctor --repair <uuid>``: the record
    (generation N) is always the authority. A stale ``.new`` staging dir is
    removed; a live scope missing while ``.prev`` exists (crash between the
    swap renames) is restored by renaming ``.prev`` back; a missing or
    drifted live scope — including live at N+1 with the record still at N —
    is recompiled from record+installed catalog. A retained ``.prev`` is
    reported, not removed (``doctor --prune`` owns that). The record is loaded
    after the lifecycle lock is acquired, so repair can never act on a stale
    authority sample (audit L1). Returns human report lines; raises
    :class:`TransitionError` (or ``SessionError`` for a missing/corrupt
    record) fail-closed.
    """

    if Path(state_root) != Path(store.root):
        raise TransitionError("state_root does not match the session store root")
    if not sessions.UUID4.fullmatch(session_id):
        raise TransitionError(
            f"session id {session_id!r} is not a managed-session UUID"
        )

    sync = dir_fsync if dir_fsync is not None else scope._fsync_directory
    lock = store.lifecycle_lock(session_id)
    lock.acquire(blocking=True)
    try:
        # The record is loaded AFTER acquiring the lock (audit L1): converge
        # mutates the scope against record authority, so the authority must be
        # sampled inside the serialized region, never before it.
        record = store.load(session_id)
        if record["mode"] != "durable":
            raise TransitionError(
                f"session {session_id} is {record['mode']}; repair handles durable "
                "sessions — resume a legacy session to upgrade it first"
            )
        scopes_root = state.ensure_private_dir(Path(store.root) / "scopes")
        live = scopes_root / session_id
        prev = scopes_root / f".{session_id}.prev"
        staging = scopes_root / f".{session_id}.new"
        _check_real_scope_dir(live, "live")
        _check_real_scope_dir(prev, "previous-generation")
        report = [f"record generation {record['scope_generation']} is authoritative"]

        hook_command = str(
            scope.ensure_hook_shim(
                store.root, scope.resolve_hook_command(None, trusted.root)
            )
        )
        token_helper_command = scope.ensure_token_helper_command(store.root)
        if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
            expected = _ordinary_expected_plan(
                record, trusted, hook_command, token_helper_command
            )
        else:
            # Repair-time record refresh: re-resolve the recorded composition
            # against the installed catalog and absorb catalog-derived drift
            # (context fields, selectors) into the record. The composition
            # itself can never change here — refresh_record_snapshot fails
            # closed on any slot/lead difference (that is a transition).
            try:
                resolved = composition.resolve(
                    trusted.docs, _document_from_record(record)
                )
            except composition.CompositionError as exc:
                raise TransitionError(
                    f"record composition no longer resolves against the "
                    f"installed catalog: {exc}"
                ) from exc
            fresh_snapshot = composition.snapshot(resolved)
            if fresh_snapshot != record["snapshot"]:
                version_doc = trusted.docs["version"]
                refreshed = sessions.refresh_record_snapshot(
                    record,
                    snapshot=fresh_snapshot,
                    catalog_version=version_doc["catalog_version"],
                    catalog_hash=trusted.bundle_sha256,
                    launcher_version=version_doc["launcher_version"],
                )
                store.save(refreshed)
                record = refreshed
                report.append(
                    "record snapshot refreshed against the installed catalog "
                    "(catalog-derived fields absorbed; composition unchanged)"
                )
            expected = _expected_plan(
                record,
                trusted,
                state_root=store.root,
                hook_command=hook_command,
                token_helper_command=token_helper_command,
            )

        if not os.path.lexists(live) and os.path.lexists(prev):
            # Crash between the two swap renames: the record still names the
            # prior generation, so the .prev content is truth.
            os.rename(prev, live)
            sync(scopes_root)
            report.append(
                "live scope was missing; restored .prev as the live scope "
                "(crash between swap renames)"
            )
        if not os.path.lexists(live):
            scope.write_scope(store.root, session_id, expected)
            report.append(
                "live scope was missing; recompiled from the record and the "
                "installed catalog"
            )
        else:
            drift = _live_drift(live, expected)
            if drift:
                scope.write_scope(store.root, session_id, expected)
                report.append(
                    "live scope drifted from record authority "
                    f"({'; '.join(drift)}); recompiled from the record and the "
                    "installed catalog"
                )
            else:
                report.append("live scope matches the record-authoritative compile")
        if os.path.lexists(staging):
            scope._remove_tree(staging)
            sync(scopes_root)
            report.append("removed stale staged scope .new from an interrupted transition")
    finally:
        lock.release()
    if os.path.lexists(prev):
        report.append("prior generation retained at .prev; doctor --prune removes it")
    return report
