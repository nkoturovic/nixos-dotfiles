"""User-facing CLI and quick-confirm flow for claude-multi v2.

Argument parsing, composition persistence, session choice, and terminal UX live
here.  Compilation and execution remain Phase 2 responsibilities and are
called through injectable boundaries.  Merely displaying or editing a plan
never checks gateway readiness, writes session state, or starts Claude.
"""

from __future__ import annotations

import argparse
import copy
import curses
import errno
import os
import shutil
import stat
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TextIO

from . import (
    catalog,
    compiler,
    composition,
    launch,
    proxy as proxy_mod,
    scope as scope_mod,
    sessions,
    state,
    strict_json,
    tui,
    validate as schema_validate,
)
from .tui import (
    EditorError,
    EditorOutcome,
    EditorState,
    workflow_guarantee_panel,
)


class CLIError(RuntimeError):
    """Actionable command or interaction failure."""


# UX §1 durability badge: new sessions always launch with per-session scope
# files. Text form only (no color-only meaning, UX §8).
DURABLE_BADGE = "durable scope (per-session files)"

# UX §6 legacy-resume note, verbatim.
LEGACY_RESUME_NOTE = (
    "this session predates durable scopes; resuming will attach a scope and "
    "keep the transcript. Use --legacy to keep old argv behavior."
)

# UX §5 evidence line, verbatim (U1 stays acceptance-pending until the user
# takeover proof runs; VERIFICATION §2).
EVIDENCE_ADD_DIR_CARRY = (
    "add-dir carry: backgrounding documented · takeover binary-consistent, "
    "acceptance-pending(U1)"
)

# R1 P1: resume is recorded-only. An override on ordinary resume bypasses the
# transition engine (diff, exited-confirm, generation bump), so it is refused
# with the transition command named as the one path (UX §6 style).
RESUME_OVERRIDE_REFUSAL = (
    "resume always uses the recorded composition; --composition {name!r} does "
    "not apply to session {uuid}. To change composition: `claude-multi "
    "sessions transition {uuid} --composition {name}`"
)

# R1 P1: shown on managed plans whose saved composition drifted, where the
# old "C current" choice used to be offered.
RESUME_RECORDED_NOTE = (
    "resume uses the recorded composition; to change it: `claude-multi "
    "sessions transition {uuid} --composition <name>`"
)

# R1 P1: informational cross-provider note when the SAVED composition now
# leads with another provider. Resume keeps the recorded provider; the
# warning never gates the launch.
RESUME_PROVIDER_DRIFT_WARNING = (
    "The saved composition now leads with {new_provider}; resume keeps the "
    "recorded {old_provider} lead, so hidden reasoning continuity is "
    "preserved. To switch providers: `claude-multi sessions transition "
    "{uuid} --composition <name>` after the process exits. For strict "
    "separation fork natively (`claude --resume OLD --fork-session`) and "
    "adopt with `claude-multi sessions link UUID`."
)

# R1 P4: legacy argv mode has no durable settings channel (package settings
# carry disableWorkflows:false and the env unsets clear the disable var), so
# an off-mode composition cannot be honored there. Fail closed at prepare.
LEGACY_WORKFLOWS_OFF_REFUSAL = (
    "workflows:off requires a durable session; drop --legacy or set "
    "workflows: native"
)

# R1 L3: transition preflight (binary verification + gateway readiness) runs
# before transition.execute may mutate scope or record; a failure aborts the
# transition with nothing touched (UX §6 style).
TRANSITION_PREFLIGHT_REFUSAL = (
    "transition preflight failed; the session record and scope are untouched: "
)


@dataclass(frozen=True)
class PreparedLaunch:
    result: compiler.CompileResult
    record: dict[str, Any]
    resolved: composition.ResolvedComposition
    document: dict[str, Any]


@dataclass
class QuickPlan:
    action: str
    document: dict[str, Any]
    source: str
    resolved: composition.ResolvedComposition | None
    errors: list[str]
    drift: list[str]
    record: dict[str, Any] | None = None
    current_document: dict[str, Any] | None = None
    cross_provider_warning: str | None = None
    passthrough_error: str | None = None
    editor_original_document: dict[str, Any] | None = None
    project_agent_count: int = 0
    project_collisions: list[str] = field(default_factory=list)
    legacy_requested: bool = False

    @property
    def ready(self) -> bool:
        return (
            not self.errors
            and self.passthrough_error is None
            and self.resolved is not None
        )


class CompositionStore:
    """XDG user-composition store backed by Phase 1 private atomic writes."""

    def __init__(
        self,
        root: Path | str,
        *,
        schema: dict[str, Any],
        seed: dict[str, Any],
    ):
        self.root = state.ensure_private_dir(Path(root))
        self.compositions_dir = state.ensure_private_dir(self.root / "compositions")
        self.schema = schema
        self.seed = copy.deepcopy(seed)

    def _path(self, name: str) -> Path:
        state.check_name(name)
        return self.compositions_dir / f"{name}.json"

    def has_user(self, name: str) -> bool:
        return os.path.lexists(self._path(name))

    def contains(self, name: str) -> bool:
        """Whether a name is already visible in the composition namespace."""

        state.check_name(name)
        return name == self.seed["name"] or self.has_user(name)

    def require_new_target(self, name: str) -> None:
        """Reject every visible target; Update is the only overwrite action."""

        if self.contains(name):
            raise CLIError(
                f"composition target {name!r} already exists; choose another name "
                "or use Update to overwrite the current composition"
            )

    def load(self, name: str) -> dict[str, Any]:
        path = self._path(name)
        if os.path.lexists(path):
            try:
                document = strict_json.loads(state.read_private(path))
                errors = schema_validate.validate(document, self.schema, "$")
                if errors:
                    raise ValueError("; ".join(errors))
                if document.get("version") != catalog.SUPPORTED_DATA_VERSION:
                    raise ValueError(
                        f"unsupported composition version {document.get('version')!r}"
                    )
                return document
            except (OSError, ValueError) as exc:
                raise CLIError(f"cannot load composition {name!r}: {exc}") from exc
        if name == self.seed["name"]:
            return copy.deepcopy(self.seed)
        raise CLIError(f"composition {name!r} does not exist")

    def names(self) -> list[str]:
        names = {self.seed["name"]}
        for path in self.compositions_dir.glob("*.json"):
            if path.is_symlink() or not path.is_file():
                continue
            name = path.stem
            try:
                state.check_name(name)
            except OSError:
                continue
            names.add(name)
        return sorted(names)

    def save(self, document: dict[str, Any], *, target: str | None = None) -> Path:
        candidate = copy.deepcopy(document)
        name = target or candidate["name"]
        state.check_name(name)
        candidate["name"] = name
        errors = schema_validate.validate(candidate, self.schema, "$")
        if errors:
            raise CLIError("cannot save invalid composition: " + "; ".join(errors))
        path = self._path(name)
        state.atomic_write(path, strict_json.canonical_file_bytes(candidate))
        return path

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.exists() and not path.is_symlink():
            return False
        # Validate the target before unlinking; never follow or remove a symlink.
        state.read_private(path)
        path.unlink()
        return True

    def duplicate(self, source: str, target: str) -> dict[str, Any]:
        self.require_new_target(target)
        document = self.load(source)
        document["name"] = target
        self.save(document)
        return document

    def rename(self, source: str, target: str) -> dict[str, Any]:
        self.require_new_target(target)
        document = self.load(source)
        document["name"] = target
        self.save(document)
        if self.has_user(source):
            self.delete(source)
        return document

    def restore_default(self) -> dict[str, Any]:
        document = copy.deepcopy(self.seed)
        self.save(document)
        return document


class Runtime:
    """Bound Phase 1/2 APIs with injectable compile/launch/doctor effects."""

    def __init__(
        self,
        *,
        asset_root: Path | str,
        environ: dict[str, str] | None = None,
        cwd: Path | str | None = None,
        compile_callback: Callable[..., compiler.CompileResult] = compiler.compile_launch,
        launch_callback: Callable[[PreparedLaunch], Any] | None = None,
        doctor_callback: Callable[["Runtime"], list[str]] | None = None,
        doctor_binary_callback: Callable[
            [dict[str, Any]], tuple[list[str], list[str]]
        ] = launch.doctor_binary_report,
        doctor_daemon_callback: Callable[
            [], launch.DaemonStatus
        ] = launch.inspect_shared_daemon,
    ):
        self.asset_root = Path(asset_root)
        self.environ = dict(os.environ if environ is None else environ)
        self.cwd = str(Path.cwd() if cwd is None else Path(cwd).resolve())
        self.catalog = catalog.load_catalog(self.asset_root)
        config = sessions.config_root(self.environ)
        state_path = sessions.state_root(self.environ)
        self.compositions = CompositionStore(
            config,
            schema=strict_json.load(
                self.asset_root / "schemas" / "composition.schema.json"
            ),
            seed=self.catalog.default_composition,
        )
        self.session_store = sessions.SessionStore(
            state_path,
            strict_json.load(self.asset_root / "schemas" / "session.schema.json"),
        )
        self.compile_callback = compile_callback
        self.launch_callback = launch_callback
        self.doctor_callback = doctor_callback
        self.doctor_binary_callback = doctor_binary_callback
        self.doctor_daemon_callback = doctor_daemon_callback

    @property
    def launcher_version(self) -> str:
        return self.catalog.docs["version"]["launcher_version"]

    @property
    def catalog_version(self) -> int:
        return self.catalog.docs["version"]["catalog_version"]

    def resolve_document(self, document: dict[str, Any]) -> composition.ResolvedComposition:
        return composition.resolve(self.catalog.docs, document)

    def current_hash(self, document: dict[str, Any]) -> str:
        return strict_json.bundle_digest(composition.snapshot(self.resolve_document(document)))

    def prepare(
        self,
        document: dict[str, Any],
        *,
        action: str,
        passthrough: list[str],
        session_id: str | None = None,
        legacy_requested: bool = False,
    ) -> PreparedLaunch:
        resolved = self.resolve_document(document)
        if legacy_requested and resolved.workflows == "off":
            # R1 P4: legacy argv mode has no durable settings channel for
            # disableWorkflows; fail closed before anything is compiled.
            raise CLIError(LEGACY_WORKFLOWS_OFF_REFUSAL)
        prior_record: dict[str, Any] | None = None
        if action == "fresh":
            launch_id = self.session_store.new_id()
            session_action = compiler.build_fresh(launch_id)
            forked_from = None
        elif action == "resume":
            if session_id is None:
                raise CLIError("resume requires a managed session ID")
            prior_record = self.session_store.load(session_id)
            launch_id = session_id
            session_action = compiler.build_resume(session_id)
            forked_from = prior_record["forked_from"]
        elif action == "fork":
            # The managed triple-flag fork path is deleted: fork natively and
            # adopt the resulting session (guidance stays fail-closed).
            raise CLIError(compiler.FORK_UNVERIFIED_GUIDANCE)
        else:
            raise CLIError(f"unknown launch action {action!r}")

        # SPEC 5: fresh and resume launch durable unless --legacy; legacy
        # records upgrade on resume (uniform rule).
        durable = not legacy_requested
        if durable:
            mode = "durable"
            if action == "resume" and prior_record["mode"] == "durable":
                generation = prior_record["scope_generation"]
            else:
                generation = 1
        elif prior_record is not None and prior_record["mode"] == "durable":
            # --legacy launch of a durable session: only this launch is
            # argv-mode; the scope and its lineage stay on the record.
            mode = "durable"
            generation = prior_record["scope_generation"]
        else:
            mode = "legacy"
            generation = 0

        snapshot = composition.snapshot(resolved)
        digest = strict_json.bundle_digest(snapshot)
        result = self.compile_callback(
            docs=self.catalog.docs,
            prompt_bodies=self.catalog.prompt_bodies,
            resolved=resolved,
            session_action=session_action,
            passthrough=passthrough,
            settings_path=self.asset_root / "settings.json",
            lead_prompt_path=compiler.lead_prompt_path(
                self.session_store.root, digest, session_action.session_id
            ),
            durable=durable,
            scope_dir=(
                scope_mod.scope_dir(self.session_store.root, launch_id)
                if durable
                else None
            ),
        )
        record = sessions.make_record(
            session_id=launch_id,
            cwd=self.cwd,
            composition_name=document["name"],
            snapshot=snapshot,
            catalog_version=self.catalog_version,
            catalog_hash=self.catalog.bundle_sha256,
            launcher_version=self.launcher_version,
            forked_from=forked_from,
            mode=mode,
            scope_generation=generation,
            workflows=resolved.workflows,
        )
        return PreparedLaunch(result, record, resolved, copy.deepcopy(document))

    def perform(self, prepared: PreparedLaunch) -> Any:
        if self.launch_callback is not None:
            return self.launch_callback(prepared)
        return launch.perform_launch(
            prepared.result,
            record=prepared.record,
            store=self.session_store,
            native_contract=self.catalog.docs["native-contract"],
            gateway=self.catalog.docs["gateway"],
            environ=self.environ,
            trusted=self.catalog,
        )


def default_asset_root() -> Path:
    return Path(__file__).resolve().parents[2]


def split_passthrough(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split launcher arguments from the exact post-``--`` Claude tail."""

    try:
        separator = argv.index("--")
    except ValueError:
        return list(argv), []
    return list(argv[:separator]), list(argv[separator + 1 :])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-multi",
        description="Compile and launch a trusted multi-model Claude composition.",
    )
    parser.add_argument("--composition", metavar="NAME", help="use a named composition")
    session_group = parser.add_mutually_exclusive_group()
    session_group.add_argument("-c", "--continue", dest="continue_last", action="store_true", help="continue the last managed session in this directory")
    session_group.add_argument("-r", "--resume", metavar="UUID", help="resume an exact managed session")
    parser.add_argument("--line", action="store_true", help="force the line-based UI (no full-screen curses interface)")
    parser.add_argument("--no-color", action="store_true", help="disable all color output (the NO_COLOR environment variable is also honored)")
    parser.add_argument("--legacy", action="store_true", help="launch with the pre-durable argv form (compatibility hatch; agents may vanish on supervisor restart)")
    parser.add_argument("--version", action="version", version="claude-multi 2.1.0")

    commands = parser.add_subparsers(dest="command")
    compose_parser = commands.add_parser("compose", help="manage saved compositions")
    compose_commands = compose_parser.add_subparsers(dest="compose_command", required=True)
    compose_commands.add_parser("list", help="list compositions")
    show_comp = compose_commands.add_parser("show", help="show a composition")
    show_comp.add_argument("name")
    for action in ("new", "edit", "delete"):
        item = compose_commands.add_parser(action, help=f"{action} a composition")
        item.add_argument("name")
    for action in ("duplicate", "rename"):
        item = compose_commands.add_parser(action, help=f"{action} a composition")
        item.add_argument("source")
        item.add_argument("target")
    compose_commands.add_parser("restore-default", help="restore the trusted default")
    template = compose_commands.add_parser("use-as-template", help="copy a composition as a new editable composition")
    template.add_argument("source")
    template.add_argument("target")

    sessions_parser = commands.add_parser("sessions", help="manage launcher-owned sessions")
    session_commands = sessions_parser.add_subparsers(dest="sessions_command", required=True)
    session_commands.add_parser("list", help="list managed sessions")
    for action in ("show", "forget"):
        item = session_commands.add_parser(action, help=f"{action} a managed session")
        item.add_argument("uuid")
    link = session_commands.add_parser("link", help="adopt a native session without inspecting Claude state")
    link.add_argument("uuid")
    link.add_argument("--composition", dest="link_composition")
    transition_parser = session_commands.add_parser(
        "transition",
        help="change a session's composition: semantic diff, exited-confirmation, relaunch",
    )
    transition_parser.add_argument("uuid")
    transition_parser.add_argument(
        "--composition",
        dest="transition_composition",
        required=True,
        help="target composition name",
    )
    transition_parser.add_argument(
        "--its-exited",
        action="store_true",
        help="confirm the target Claude process has EXITED (not merely idle); "
        "skips the interactive confirmation",
    )

    commands.add_parser("models", help="list trusted catalog models")
    show = commands.add_parser("show", help="show the effective composition summary")
    show.add_argument("show_composition", nargs="?")
    doctor_parser = commands.add_parser(
        "doctor", help="run local catalog and loopback readiness checks"
    )
    doctor_actions = doctor_parser.add_mutually_exclusive_group()
    doctor_actions.add_argument(
        "--repair",
        metavar="UUID",
        dest="doctor_repair",
        help="converge a session's scope to its record authority",
    )
    doctor_actions.add_argument(
        "--prune",
        action="store_true",
        dest="doctor_prune",
        help="remove stale staging dirs and scopes whose records are gone",
    )
    return parser


def snapshot_to_document(record: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct a composition selection from a managed resolved snapshot."""

    snap = record["snapshot"]
    slots: list[dict[str, Any]] = [
        {"role": catalog.LEAD_ROLE, "model": snap["lead"]["model"]}
    ]
    for variant in snap["variants"]:
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
        "availability": copy.deepcopy(snap["availability"]),
        "slots": slots,
        "native_agents": copy.deepcopy(snap["native_agents"]),
    }
    # The snapshot records workflows only when it differs from the default;
    # preserve it so a scope recompile (Doctor/repair) reproduces off-mode
    # settings instead of silently reverting to native.
    if "workflows" in snap:
        document["workflows"] = snap["workflows"]
    return document


def _provider_for_lead(runtime: Runtime, document: dict[str, Any]) -> str | None:
    try:
        lead = next(slot for slot in document["slots"] if slot["role"] == catalog.LEAD_ROLE)
        return runtime.catalog.models[lead["model"]]["provider"]
    except (KeyError, StopIteration):
        return None


def _project_agent_files(cwd: Path | str) -> list[Path]:
    """Project agent files visible from ``cwd`` (native precedence, Q10).

    Mirrors the ``find_cm_collisions`` walk read-only: every
    ``.claude/agents/`` directory from ``cwd`` up to and including the git
    root (first ancestor containing ``.git``) or the filesystem root,
    deduplicated by resolved path. Unreadable directories are skipped.
    """

    files: list[Path] = []
    seen: set[Path] = set()
    start = Path(cwd).resolve()
    for ancestor in (start, *start.parents):
        candidate = ancestor / ".claude" / "agents"
        if candidate.is_dir():
            try:
                key = candidate.resolve()
            except OSError:
                key = candidate
            if key not in seen:
                seen.add(key)
                try:
                    entries = sorted(candidate.iterdir())
                except OSError:
                    entries = []
                for entry in entries:
                    try:
                        if entry.is_file() and entry.suffix == ".md":
                            files.append(entry)
                    except OSError:
                        continue
        if (ancestor / ".git").exists():
            break
    return files


def _collision_error(path: Path, name: str, cwd: str) -> str:
    """UX §6 collision language, verbatim."""

    return (
        f"project agent {name!r} at {os.path.relpath(path, cwd)} collides with "
        "the managed cm-* namespace. Rename or remove it, or launch from a "
        "different directory."
    )


def build_quick_plan(
    runtime: Runtime,
    document: dict[str, Any],
    *,
    action: str,
    source: str,
    record: dict[str, Any] | None = None,
    current_document: dict[str, Any] | None = None,
    drift: list[str] | None = None,
    cross_provider_warning: str | None = None,
) -> QuickPlan:
    errors: list[str] = []
    resolved = None
    try:
        resolved = runtime.resolve_document(document)
    except (ValueError, KeyError) as exc:
        errors.append(str(exc))
    if resolved is not None:
        errors.extend(
            proxy_mod.selected_secret_problems(
                resolved,
                runtime.catalog.docs["models"]["models"],
                runtime.catalog.docs["providers"]["providers"],
                environ=runtime.environ,
            )
        )
    # R1 P2: a recorded intent re-resolved against a drifted installed catalog
    # is NOT an error — the record is the intent authority and the resume
    # recompiles against the installed catalog (drift lines stay
    # informational). Only a genuinely unresolvable intent (a role or model
    # deleted from the catalog) fails closed via the resolve error above.
    # Q10 visibility: project agents load natively; an exact cm-* collision
    # would silently shadow a guaranteed definition, so it blocks here with
    # the same fail-closed language the durable launch gate uses.
    project_files = _project_agent_files(runtime.cwd)
    project_collisions: list[str] = []
    if resolved is not None:
        generated = {variant.id for variant in resolved.variants}
        project_collisions = [
            _collision_error(path, name, runtime.cwd)
            for path, name in scope_mod.find_cm_collisions(
                runtime.cwd, [], generated
            )
        ]
    errors.extend(project_collisions)
    return QuickPlan(
        action=action,
        document=copy.deepcopy(document),
        source=source,
        resolved=resolved,
        errors=errors,
        drift=list(drift or []),
        record=record,
        current_document=copy.deepcopy(current_document),
        cross_provider_warning=cross_provider_warning,
        project_agent_count=len(project_files),
        project_collisions=project_collisions,
    )


def _current_document_for_record(runtime: Runtime, record: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return runtime.compositions.load(record["composition_name"])
    except CLIError:
        return None


def _recorded_document(record: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the record's composition intent from the record snapshot.

    Lane D's ``transition._document_from_record`` is the reference
    reconstruction (it also carries the recorded workflow-mode fallback);
    without the transition engine the local reconstruction is equivalent for
    launcher-written records, so resume never hard-depends on Lane D.
    """

    try:
        from . import transition
    except ImportError:
        return snapshot_to_document(record)
    return transition._document_from_record(record)


def managed_plan(runtime: Runtime, record: dict[str, Any]) -> QuickPlan:
    """The recorded-only resume plan (R1 P1/P2).

    Resume ALWAYS uses the recorded composition intent, re-resolved against
    the installed catalog; a changed or missing saved composition, and any
    catalog drift, is shown informationally and never blocks or redirects the
    resume. Changing composition is the transition engine's job
    (``claude-multi sessions transition <uuid> --composition <name>``).
    """

    recorded = _recorded_document(record)
    current = _current_document_for_record(runtime, record)
    drift: list[str] = []
    current_hash = "missing"
    if current is not None:
        try:
            current_hash = runtime.current_hash(current)
        except ValueError:
            current_hash = "invalid"
    if current_hash in ("missing", "invalid"):
        drift.append(f"composition {record['composition_name']!r} is {current_hash}")
    else:
        drift.extend(
            sessions.drift_report(
                record,
                catalog_hash=runtime.catalog.bundle_sha256,
                composition_hash=current_hash,
            )
        )

    warning = None
    if current is not None:
        old_provider = _provider_for_lead(runtime, recorded)
        new_provider = _provider_for_lead(runtime, current)
        if old_provider and new_provider and old_provider != new_provider:
            warning = RESUME_PROVIDER_DRIFT_WARNING.format(
                new_provider=runtime.catalog.providers[new_provider]["display"],
                old_provider=runtime.catalog.providers[old_provider]["display"],
                uuid=record["session_id"],
            )
    return build_quick_plan(
        runtime,
        recorded,
        action="resume",
        source="Recorded snapshot",
        record=record,
        current_document=current,
        drift=drift,
        cross_provider_warning=warning,
    )


def remembered_document(runtime: Runtime) -> tuple[dict[str, Any], str]:
    session_id = runtime.session_store.last(runtime.cwd)
    if session_id:
        try:
            record = runtime.session_store.load(session_id)
            return runtime.compositions.load(record["composition_name"]), "Last used in this directory"
        except (sessions.SessionError, CLIError):
            pass
    return runtime.compositions.load("default"), "User default" if runtime.compositions.has_user("default") else "Trusted default"


def _scope_summary(runtime: Runtime, document: dict[str, Any]) -> str:
    parts = []
    for provider_id, provider in sorted(runtime.catalog.providers.items()):
        scope = document["availability"]["providers"].get(provider_id, "off")
        parts.append(f"{provider['display']} {scope}")
    return " · ".join(parts)


def _policy_summary(runtime: Runtime, resolved: composition.ResolvedComposition) -> str:
    """UX §1 compact policy sentence, derived from the compiled native policy."""

    native = resolved.native_agents
    explore = {
        "replace": "Explore→cm-analyst",
        "native": "Explore native",
        "off": "Explore off",
    }[native["explore"]]
    plan_part = "Plan native" if native["plan"] == "native" else "Plan off"
    general = f"general-purpose {native['general_purpose']}"
    aliases = runtime.catalog.docs["native-contract"]["generic_agent_aliases"]["values"]
    _, denies = compiler.compile_native_policy(native, aliases)
    generic = (
        "generic denied"
        if any(f"Agent({alias})" in denies for alias in aliases)
        else "generic allowed"
    )
    return f"{explore} · {plan_part} · {general} · {generic}"


def _durability_badge(plan: QuickPlan) -> str:
    """UX §1 durability badge: which persistence class the session will have."""

    if plan.legacy_requested:
        if plan.record is not None and plan.record["mode"] == "durable":
            return "legacy argv (--legacy) · durable scope retained on disk"
        return "legacy argv (--legacy compatibility hatch)"
    if plan.record is None:
        return DURABLE_BADGE
    if plan.record["mode"] == "durable":
        return f"{DURABLE_BADGE} · generation {plan.record['scope_generation']}"
    return "legacy argv record · resume upgrades to durable scope"


def _project_summary(plan: QuickPlan) -> str:
    count = plan.project_agent_count
    noun = "project agent" if count == 1 else "project agents"
    discovered = f"{count} {noun} discovered" if count else "no project agents discovered"
    status = "none colliding" if not plan.project_collisions else "collision blocks launch"
    return f"{discovered} (native precedence; {status})"


def render_quick_confirm(
    runtime: Runtime,
    plan: QuickPlan,
    *,
    details: bool = False,
    width: int = 100,
) -> str:
    lines = ["claude-multi", ""]
    action = plan.action.title()
    if plan.record is not None:
        action += f" · {plan.record['session_id']}"
    lines.append(f"Action         {action}")
    lines.append(
        f"Composition    {tui.visible_text(plan.document.get('name', '<invalid>'))} · {plan.source}"
    )
    if plan.resolved is not None:
        resolved = plan.resolved
        lead_model = runtime.catalog.models[resolved.lead.model]
        provider = runtime.catalog.providers[lead_model["provider"]]
        lines.append(
            f"Lead           {resolved.lead.display} · {provider['display']} · effort {resolved.lead.effort} · workflows {resolved.workflows}"
        )
        roles = {variant.role for variant in resolved.variants}
        lines.append(f"Roles          {len(roles)} enabled · {len(resolved.variants)} variants")
        lines.append(f"Durability     {_durability_badge(plan)}")
        lines.append(f"Providers      {_scope_summary(runtime, plan.document)}")
        native = resolved.native_agents
        lines.append(
            "Native agents  "
            f"Explore {native['explore']} · Plan {native['plan']} · general-purpose {native['general_purpose']}"
        )
        lines.append(f"Policy         {_policy_summary(runtime, resolved)}")
        scalar = "unset" if resolved.scalar_context_tokens is None else f"{resolved.scalar_context_tokens:,}"
        lines.append(f"Scalar bound   {scalar}")
    else:
        has_lead = any(
            slot.get("role") == catalog.LEAD_ROLE
            for slot in plan.document.get("slots", [])
            if isinstance(slot, dict)
        )
        lines.append(
            "Lead           unresolved" if has_lead else "Lead           none selected"
        )
        lines.append(f"Durability     {_durability_badge(plan)}")
    lines.append(f"Project        {_project_summary(plan)}")
    if plan.record is not None:
        catalog_state = "changed" if plan.record["catalog_hash"] != runtime.catalog.bundle_sha256 else "same"
        lines.append(
            f"Catalog drift  catalog_hash {catalog_state} · recorded {plan.record['catalog_hash'][:18]}… · current {runtime.catalog.bundle_sha256[:18]}…"
        )
        current_hash = "unavailable"
        if plan.current_document is not None:
            try:
                current_hash = runtime.current_hash(plan.current_document)
            except ValueError:
                current_hash = "invalid"
        composition_state = "same" if current_hash == plan.record["composition_hash"] else "changed"
        lines.append(
            f"Composition    composition_hash {composition_state} · recorded {plan.record['composition_hash'][:18]}… · current {str(current_hash)[:18]}"
        )
        lines.append(
            "Live drift     Native temporary changes are not inspected; the chosen composition is reasserted."
        )
        lines.append(
            "Workers        Existing workers, if any, keep their original model and tools; fork for strict separation."
        )
        if composition_state == "changed":
            # R1 P1: the old "C current" choice lived here; the transition
            # engine is the one path to a changed composition.
            lines.append(
                f"Change         {RESUME_RECORDED_NOTE.format(uuid=plan.record['session_id'])}"
            )
        if plan.action == "resume" and plan.record["mode"] != "durable":
            lines.append(f"Note           {LEGACY_RESUME_NOTE}")
    lines.append(
        f"Drift          {'None' if not plan.drift else '; '.join(tui.visible_text(item) for item in plan.drift)}"
    )
    if plan.cross_provider_warning:
        lines.extend(("", "WARNING", f"  {tui.visible_text(plan.cross_provider_warning)}"))
    if details and plan.resolved is not None:
        lines.extend(("", "Variants"))
        for variant in plan.resolved.variants:
            preferred = " · preferred" if variant.preferred else ""
            lines.append(
                f"  {_role_label(variant.role)} · {variant.display} · {variant.lane}{preferred}"
            )
        lines.extend(("", "Availability"))
        for model_id, model in sorted(runtime.catalog.models.items()):
            scope = plan.document["availability"]["models"].get(model_id, "off")
            new = " · New · Off" if model_id not in plan.document["availability"]["models"] else ""
            lines.append(f"  {model['display']} · {scope}{new}")
    lines.extend(("", f"Status         {'Ready' if plan.ready else 'BLOCKED'}"))
    for error in plan.errors:
        lines.append(f"  - {tui.visible_text(error)}")
    if plan.passthrough_error is not None:
        lines.append(f"  - {tui.visible_text(plan.passthrough_error)}")
    wrapped: list[str] = []
    for line in lines:
        if not line or len(line) <= width:
            wrapped.append(line)
            continue
        indent = " " * (15 if not line.startswith("  ") else 4)
        wrapped.extend(
            textwrap.wrap(
                line,
                width=max(32, width),
                subsequent_indent=indent,
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return "\n".join(wrapped) + "\n"


def _role_label(role_id: str) -> str:
    return role_id.removeprefix("cm-").replace("-", " ").title()


def _read_key(stream: TextIO) -> str | None:
    try:
        line = stream.readline()
    except KeyboardInterrupt:
        return None
    if line == "":
        return None
    return line.strip().lower()


def _stream_width(stream: TextIO) -> int:
    try:
        return max(32, os.get_terminal_size(stream.fileno()).columns)
    except (AttributeError, OSError):
        return 100


def quick_footer(plan: QuickPlan) -> tuple[str, ...]:
    """Exact quick-confirm actions for Ready and BLOCKED plans.

    R1 P1: managed plans offer no editing and no recorded/current
    switching; resume is recorded-only and the transition engine owns
    composition changes (Enter on BLOCKED shows the transition path).
    """

    if plan.record is not None:
        primary = "Enter launch" if plan.ready else "Enter transition hint"
        return (f"{primary} · D details · S sessions · ? workflows · Q cancel",)
    primary = "Enter launch" if plan.ready else "Enter edit"
    return (f"{primary} · E edit · D details · S sessions · ? workflows · Q cancel",)


def validate_quick_passthrough(
    runtime: Runtime, plan: QuickPlan, passthrough: list[str]
) -> None:
    """Apply Phase 2 ownership validation before interactive Ready is shown."""

    try:
        compiler.validate_passthrough(passthrough)
    except compiler.CompilerError as exc:
        plan.passthrough_error = str(exc)
    else:
        plan.passthrough_error = None


def _apply_editor_outcome(
    runtime: Runtime,
    plan: QuickPlan,
    outcome: EditorOutcome,
) -> QuickPlan | None:
    action = outcome.action
    document = copy.deepcopy(outcome.document)
    if plan.record is not None:
        # R1 P1 invariant: the launchable intent of a managed resume is the
        # record, so editor outcomes are never applied to record-bearing
        # plans (the quick-confirm redirects before the editor opens).
        raise CLIError(
            "managed resume plans do not accept editor outcomes; "
            + RESUME_RECORDED_NOTE.format(uuid=plan.record["session_id"])
        )
    if action in ("save", "update"):
        runtime.resolve_document(document)
        runtime.compositions.save(document)
    elif action in ("save-as", "duplicate", "use-as-template"):
        if outcome.target is None:
            raise CLIError(f"{action} requires a target name")
        runtime.compositions.require_new_target(outcome.target)
        document["name"] = outcome.target
        runtime.resolve_document(document)
        runtime.compositions.save(document, target=outcome.target)
    elif action == "rename":
        if outcome.target is None:
            raise CLIError("rename requires a target name")
        document = runtime.compositions.rename(plan.document["name"], outcome.target)
    elif action == "delete":
        runtime.compositions.delete(plan.document["name"])
        return None
    elif action == "restore-default":
        document = runtime.compositions.restore_default()
    elif action != "launch-once":
        raise CLIError(f"unsupported editor outcome {action!r}")
    return build_quick_plan(
        runtime,
        document,
        action=plan.action,
        source="Unsaved launch" if action == "launch-once" else "Saved composition",
        record=plan.record,
        current_document=document,
        drift=plan.drift,
        cross_provider_warning=plan.cross_provider_warning,
    )


def _editor_failure_plan(
    runtime: Runtime,
    previous: QuickPlan,
    document: dict[str, Any],
    error: BaseException,
) -> QuickPlan:
    """Keep exact editor content after a failed save/persistence attempt."""

    failed = build_quick_plan(
        runtime,
        copy.deepcopy(document),
        action=previous.action,
        source="Unsaved editor changes",
        record=previous.record,
        current_document=copy.deepcopy(document),
        drift=previous.drift,
        cross_provider_warning=previous.cross_provider_warning,
    )
    failed.editor_original_document = copy.deepcopy(
        previous.editor_original_document or previous.document
    )
    message = str(error)
    if message and message not in failed.errors:
        failed.errors.append(message)
    return failed


def run_editor(
    state: EditorState,
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    force_line: bool = False,
    plan: QuickPlan | None = None,
    runtime: Runtime | None = None,
    no_color: bool = False,
) -> EditorOutcome | None:
    """The single composition-editor entry point.

    On a curses-capable terminal the form editor runs; otherwise there is no
    second interactive implementation — the plan is printed read-only
    (``--print`` style) together with the exact ``$EDITOR`` command for the
    composition JSON, and no outcome is produced.
    """

    if not force_line and tui.streams_curses_capable(input_stream, output_stream):
        try:
            return tui.run_form_editor(
                state,
                name_taken=runtime.compositions.contains if runtime else None,
                no_color=no_color,
                input_stream=input_stream,
                output_stream=output_stream,
            )
        except (curses.error, OSError):
            pass  # fall through to the printed guidance
    if runtime is not None and plan is not None:
        output_stream.write(
            render_quick_confirm(
                runtime, plan, details=True, width=_stream_width(output_stream)
            )
        )
    if runtime is not None:
        output_stream.write(
            tui.dumb_edit_guidance(
                runtime.compositions._path(state.name),
                name=state.name,
                has_user=runtime.compositions.has_user(state.name),
            )
        )
    return None


def _editor_state_for_plan(runtime: Runtime, plan: QuickPlan) -> EditorState:
    editor_state = EditorState(
        runtime.catalog.docs,
        plan.document,
        runtime.catalog.default_composition,
        original_document=plan.editor_original_document,
    )
    if plan.source == "Unsaved editor changes" and plan.errors:
        editor_state.message = plan.errors[-1]
    return editor_state


def _apply_outcome_or_failure(
    runtime: Runtime, plan: QuickPlan, outcome: EditorOutcome
) -> QuickPlan | None:
    """Apply an editor outcome; on failure return the failure-preserving plan.

    Raises nothing for the expected CLIError/ValueError/OSError family; the
    caller distinguishes "failure plan" from "deleted" via the plan source.
    """

    try:
        return _apply_editor_outcome(runtime, plan, outcome)
    except (CLIError, ValueError, OSError) as exc:
        return _editor_failure_plan(runtime, plan, outcome.document, exc)


class _QuickConfirmScreen:
    """Curses quick-confirm: composition card + KeyBar (UX section 1).

    Same flow semantics as the line loop: Enter launches a Ready plan (or
    opens the editor when BLOCKED), E edits, D toggles details, ? shows the
    guarantee panel as a Modal, Q/Esc cancels.  Managed plans are
    recorded-only (R1 P1): R/C/E and Enter-on-blocked show the recorded-only
    redirect Modal naming the transition command.  Returns ("perform",
    PreparedLaunch) or None; the actual launch happens after curses has been
    torn down.
    """

    def __init__(
        self,
        runtime: Runtime,
        plan: QuickPlan,
        *,
        passthrough: list[str],
        palette: tui.Palette,
    ):
        self.runtime = runtime
        self.plan = plan
        self.passthrough = passthrough
        self.palette = palette
        self.details = False

    # -- drawing -----------------------------------------------------------

    def _draw(self, win: Any) -> None:
        win.erase()
        palette = self.palette
        height, width = win.getmaxyx()
        plan = self.plan
        runtime = self.runtime
        row = 0
        tui.safe_add(win, row, 0, "claude-multi", palette.attr("accent") | curses.A_BOLD)
        action = plan.action.title()
        if plan.record is not None:
            action += f" · {plan.record['session_id'][:8]}…"
        tui.safe_add(
            win,
            row,
            13,
            f"— composition: {plan.document.get('name', '<invalid>')} · {plan.source} · {action}",
            palette.attr("normal"),
        )
        row += 1
        tui.safe_add(win, row, 0, "─" * min(width - 1, 60), palette.attr("dim"))
        row += 1
        if plan.resolved is not None:
            resolved = plan.resolved
            tui.safe_add(win, row, 0, "lead      ", palette.attr("dim"))
            lead_text = f"{resolved.lead.display} · effort {resolved.lead.effort}"
            tui.safe_add(win, row, 10, lead_text)
            tui.Badge(f"workflows: {resolved.workflows}", "accent").draw(
                win, row, 10 + len(lead_text) + 3, palette
            )
            row += 1
            roles = {variant.role for variant in resolved.variants}
            tui.safe_add(win, row, 0, "agents    ", palette.attr("dim"))
            tui.safe_add(
                win, row, 10, f"{len(resolved.variants)} selected · {len(roles)} roles · "
            )
            tui.Badge(_durability_badge(plan), "ok").draw(
                win, row, 10 + len(f"{len(resolved.variants)} selected · {len(roles)} roles · "), palette
            )
            row += 1
            table = tui.Table(
                ["role", "model", "lane", ""],
                [
                    [
                        _role_label(variant.role),
                        variant.display,
                        variant.lane,
                        ("★preferred" if variant.preferred else "")
                        + ("  (worktree)" if variant.isolation else ""),
                    ]
                    for variant in resolved.variants
                ],
                selected=-1,
            )
            max_rows = max(2, height - row - (9 if self.details else 6))
            row += table.draw(win, row, 2, width, palette, max_rows=max_rows)
        else:
            has_lead = any(
                slot.get("role") == catalog.LEAD_ROLE
                for slot in plan.document.get("slots", [])
                if isinstance(slot, dict)
            )
            tui.safe_add(win, row, 0, "lead      ", palette.attr("dim"))
            tui.safe_add(
                win, row, 10, "unresolved" if has_lead else "none selected", palette.attr("error")
            )
            row += 1
            tui.safe_add(win, row, 0, "agents    ", palette.attr("dim"))
            tui.Badge(_durability_badge(plan), "ok").draw(win, row, 10, palette)
            row += 1
        if plan.resolved is not None:
            tui.safe_add(win, row, 0, "policy    ", palette.attr("dim"))
            tui.safe_add(win, row, 10, _policy_summary(runtime, plan.resolved))
            row += 1
        tui.safe_add(win, row, 0, "project   ", palette.attr("dim"))
        project_role = "error" if plan.project_collisions else "normal"
        tui.safe_add(win, row, 10, _project_summary(plan), palette.attr(project_role))
        row += 1
        if plan.cross_provider_warning:
            tui.safe_add(win, row, 0, "warning   ", palette.attr("warn"))
            tui.safe_add(win, row, 10, plan.cross_provider_warning, palette.attr("warn"))
            row += 1
        if self.details:
            row += self._draw_details(win, row, height, width)
        row += 1
        if plan.ready:
            tui.Badge("Status  Ready", "ok").draw(win, row, 0, palette)
        else:
            tui.Badge("Status  BLOCKED", "error").draw(win, row, 0, palette)
        row += 1
        errors = [
            *plan.errors,
            *([plan.passthrough_error] if plan.passthrough_error else []),
        ]
        for error in errors:
            wrapped = textwrap.wrap(
                f"- {error}",
                width=max(32, width - 2),
                subsequent_indent="  ",
                break_long_words=False,
                break_on_hyphens=False,
            ) or ["-"]
            for line in wrapped:
                if row >= height - 2:
                    break
                tui.safe_add(win, row, 2, line, palette.attr("error"))
                row += 1
        keybar = self._keybar()
        keybar.draw(win, height - 1, palette)
        win.refresh()

    def _draw_details(self, win: Any, row: int, height: int, width: int) -> int:
        plan = self.plan
        runtime = self.runtime
        start = row
        palette = self.palette
        if row >= height - 4:
            return 0
        if plan.resolved is not None:
            scalar = (
                "unset"
                if plan.resolved.scalar_context_tokens is None
                else f"{plan.resolved.scalar_context_tokens:,}"
            )
            tui.safe_add(win, row, 0, "scalar    ", palette.attr("dim"))
            tui.safe_add(win, row, 10, f"bound {scalar}")
            row += 1
            tui.safe_add(win, row, 0, "providers ", palette.attr("dim"))
            tui.safe_add(win, row, 10, _scope_summary(runtime, plan.document))
            row += 1
        if plan.record is not None:
            catalog_state = (
                "changed"
                if plan.record["catalog_hash"] != runtime.catalog.bundle_sha256
                else "same"
            )
            tui.safe_add(win, row, 0, "catalog   ", palette.attr("dim"))
            tui.safe_add(
                win,
                row,
                10,
                f"hash {catalog_state} · recorded {plan.record['catalog_hash'][:18]}… · "
                f"current {runtime.catalog.bundle_sha256[:18]}…",
            )
            row += 1
            current_hash = "unavailable"
            if plan.current_document is not None:
                try:
                    current_hash = runtime.current_hash(plan.current_document)
                except ValueError:
                    current_hash = "invalid"
            composition_state = (
                "same" if current_hash == plan.record["composition_hash"] else "changed"
            )
            tui.safe_add(win, row, 0, "recorded  ", palette.attr("dim"))
            tui.safe_add(
                win,
                row,
                10,
                f"composition hash {composition_state} · recorded "
                f"{plan.record['composition_hash'][:18]}… · current {str(current_hash)[:18]}",
            )
            row += 1
            if composition_state == "changed":
                # R1 P1: transitions own composition changes; say so where
                # the old "C current" choice used to be advertised.
                tui.safe_add(win, row, 0, "change    ", palette.attr("dim"))
                tui.safe_add(
                    win,
                    row,
                    10,
                    RESUME_RECORDED_NOTE.format(uuid=plan.record["session_id"]),
                )
                row += 1
            tui.safe_add(win, row, 0, "live      ", palette.attr("dim"))
            tui.safe_add(
                win,
                row,
                10,
                "native temporary changes are not inspected; the chosen composition is reasserted.",
            )
            row += 1
            tui.safe_add(win, row, 0, "workers   ", palette.attr("dim"))
            tui.safe_add(
                win,
                row,
                10,
                "existing workers, if any, keep their original model and tools; fork for strict separation.",
            )
            row += 1
            if plan.action == "resume" and plan.record["mode"] != "durable":
                tui.safe_add(win, row, 0, "note      ", palette.attr("warn"))
                tui.safe_add(win, row, 10, LEGACY_RESUME_NOTE, palette.attr("warn"))
                row += 1
        tui.safe_add(win, row, 0, "drift     ", palette.attr("dim"))
        drift = "None" if not plan.drift else "; ".join(plan.drift)
        tui.safe_add(win, row, 10, drift)
        row += 1
        if plan.resolved is not None and row < height - 4:
            tui.safe_add(win, row, 0, "availability", palette.attr("accent"))
            row += 1
            for model_id, model in sorted(runtime.catalog.models.items()):
                if row >= height - 3:
                    break
                scope = plan.document["availability"]["models"].get(model_id, "off")
                new = (
                    " · New · Off"
                    if model_id not in plan.document["availability"]["models"]
                    else ""
                )
                tui.safe_add(win, row, 2, f"{model['display']} · {scope}{new}")
                row += 1
        return row - start

    def _keybar(self) -> tui.KeyBar:
        # R1 P1: no recorded/current switching and no editor on managed
        # plans; resume is recorded-only.
        if self.plan.record is not None:
            primary = (
                ("Enter", "launch") if self.plan.ready else ("Enter", "transition hint")
            )
            bindings: list[tuple[str, str]] = [primary]
        else:
            primary = ("Enter", "launch") if self.plan.ready else ("Enter", "edit")
            bindings = [primary, ("E", "edit")]
        bindings.extend((("D", "details"), ("S", "sessions"), ("?", "workflows"), ("Q", "cancel")))
        return tui.KeyBar(bindings)

    # -- sessions picker ----------------------------------------------------

    def _open_sessions(self, win: Any) -> tuple[str, Any] | None:
        """Open the sessions picker in place; None means stay on the card.

        Resume maps to ("perform", PreparedLaunch) so the outer launcher
        tears down curses before exec, exactly like Enter. A transition
        request returns ("transition", record, composition_name) for the
        caller to run after teardown. The picker's ``legacy_requested`` flag
        follows the originating plan.
        """

        result = _SessionsScreen(self.runtime, palette=self.palette).run(win)
        if result is None:
            return None
        if result[0] == "resume":
            record = result[1]
            plan = managed_plan(self.runtime, record)
            if not plan.ready:
                self.plan.errors.extend(plan.errors)
                return None
            prepared = self.runtime.prepare(
                plan.document,
                action="resume",
                passthrough=[],
                session_id=record["session_id"],
                legacy_requested=self.plan.legacy_requested,
            )
            return ("perform", prepared)
        if result[0] == "transition":
            return result
        return None

    # -- editor -------------------------------------------------------------

    def _recorded_only_modal(self, win: Any) -> None:
        """R1 P1: managed plans never switch or edit the launchable intent.

        The old recorded/current switcher and the editor both offered a
        composition override on resume; the redirect names the one path.
        """

        tui.Modal(
            "Recorded-only resume",
            textwrap.wrap(
                RESUME_RECORDED_NOTE.format(uuid=self.plan.record["session_id"]),
                width=60,
            ),
            buttons=(("Close", True),),
        ).run(win, self.palette)

    def _edit(self, win: Any) -> str | None:
        editor_state = _editor_state_for_plan(self.runtime, self.plan)
        screen = tui.FormEditorScreen(
            editor_state,
            palette=self.palette,
            name_taken=self.runtime.compositions.contains,
        )
        try:
            outcome = screen.run(win)
        except (EditorError, KeyError) as exc:
            self.plan = _editor_failure_plan(
                self.runtime, self.plan, editor_state.document, exc
            )
            return None
        if outcome is None:
            return None
        updated = _apply_outcome_or_failure(self.runtime, self.plan, outcome)
        if updated is None:
            return "exit"
        self.plan = updated
        return None

    # -- main loop -----------------------------------------------------------

    def run(self, win: Any) -> tuple[str, PreparedLaunch] | None:
        tui.hide_cursor()
        while True:
            validate_quick_passthrough(self.runtime, self.plan, self.passthrough)
            height, width = win.getmaxyx()
            if height < 12 or width < 48:
                win.erase()
                tui.safe_add(win, 0, 0, "Terminal too small.", self.palette.attr("error") | curses.A_BOLD)
                tui.safe_add(win, 2, 0, "Resize, or press Q to cancel.")
                win.refresh()
                key = tui.read_key(win)
                if key.kind == "esc" or (key.kind == "char" and key.ch == "q"):
                    return None
                if key.kind == "ctrl" and key.ch == "c":
                    raise KeyboardInterrupt
                continue
            self._draw(win)
            key = tui.read_key(win)
            if key.kind == "resize":
                continue
            if key.kind == "ctrl" and key.ch == "c":
                raise KeyboardInterrupt
            if key.kind == "esc" or (key.kind == "char" and key.ch == "q"):
                return None
            if key.kind == "char" and key.ch == "d":
                self.details = not self.details
                continue
            if key.kind == "char" and key.ch == "s":
                outcome = self._open_sessions(win)
                if outcome is None:
                    continue
                return outcome
            if key.kind == "char" and key.ch == "?":
                mode = (
                    self.plan.resolved.workflows
                    if self.plan.resolved is not None
                    else self.plan.document.get("workflows", "native")
                )
                tui.Modal(
                    "Workflow guarantees",
                    workflow_guarantee_panel(mode).splitlines(),
                    buttons=(("Close", True),),
                ).run(win, self.palette)
                continue
            if (
                self.plan.record is not None
                and key.kind == "char"
                and key.ch in ("r", "c")
            ):
                self._recorded_only_modal(win)
                continue
            if (key.kind == "char" and key.ch == "e") or (
                key.kind == "enter" and not self.plan.ready
            ):
                if self.plan.record is not None:
                    self._recorded_only_modal(win)
                    continue
                if self._edit(win) == "exit":
                    return None
                continue
            if key.kind == "enter" and self.plan.ready:
                try:
                    prepared = self.runtime.prepare(
                        self.plan.document,
                        action=self.plan.action,
                        passthrough=self.passthrough,
                        session_id=(
                            self.plan.record["session_id"] if self.plan.record else None
                        ),
                        legacy_requested=self.plan.legacy_requested,
                    )
                except (ValueError, CLIError) as exc:
                    self.plan.errors.append(str(exc))
                    continue
                return ("perform", prepared)


def _curses_quick_confirm(
    runtime: Runtime,
    plan: QuickPlan,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    passthrough: list[str],
    no_color: bool,
) -> Any:
    palette = tui.detect_palette(
        no_color=no_color, tty_in=input_stream, tty_out=output_stream
    )
    screen = _QuickConfirmScreen(runtime, plan, passthrough=passthrough, palette=palette)
    result = tui.run_curses_on_streams(
        screen.run, input_stream, output_stream, palette=palette
    )
    if result is None:
        return 0
    if result[0] == "transition":
        _, record, name = result
        namespace = argparse.Namespace(
            uuid=record["session_id"],
            transition_composition=name,
            its_exited=False,
        )
        return _sessions_transition(
            runtime,
            namespace,
            input_stream=input_stream,
            output_stream=output_stream,
            interactive=True,
            no_color=no_color,
        )
    _action, prepared = result
    return runtime.perform(prepared)


def quick_confirm(
    runtime: Runtime,
    plan: QuickPlan,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    passthrough: list[str],
    force_line: bool,
    no_color: bool = False,
) -> Any:
    if not force_line and tui.streams_curses_capable(input_stream, output_stream):
        try:
            return _curses_quick_confirm(
                runtime,
                plan,
                input_stream=input_stream,
                output_stream=output_stream,
                passthrough=passthrough,
                no_color=no_color,
            )
        except KeyboardInterrupt:
            return 0
        except (curses.error, OSError):
            pass  # fall back to the line-based UI below
    return _line_quick_confirm(
        runtime,
        plan,
        input_stream=input_stream,
        output_stream=output_stream,
        passthrough=passthrough,
        no_color=no_color,
    )


def _line_quick_confirm(
    runtime: Runtime,
    plan: QuickPlan,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    passthrough: list[str],
    no_color: bool = False,
) -> Any:
    details = False
    while True:
        validate_quick_passthrough(runtime, plan, passthrough)
        output_stream.write(
            render_quick_confirm(
                runtime,
                plan,
                details=details,
                width=_stream_width(output_stream),
            )
        )
        for footer_line in quick_footer(plan):
            output_stream.write(footer_line + "\n")
        output_stream.write("> ")
        output_stream.flush()
        key = _read_key(input_stream)
        if key is None or key in ("q", "quit", "cancel"):
            return 0
        if key == "d":
            details = not details
            continue
        if key == "?":
            mode = (
                plan.resolved.workflows
                if plan.resolved is not None
                else plan.document.get("workflows", "native")
            )
            output_stream.write(workflow_guarantee_panel(mode) + "\n")
            continue
        if key == "s":
            _print_sessions_listing(runtime, output_stream)
            output_stream.write(
                "resume with `claude-multi -r <uuid>` (or a name), or press S "
                "in the curses UI to pick interactively.\n"
            )
            continue
        if plan.record is not None and key in ("r", "c"):
            # R1 P1: the recorded/current switch is gone; resume always uses
            # the recorded composition. Name the one path to change it.
            output_stream.write(
                RESUME_RECORDED_NOTE.format(uuid=plan.record["session_id"]) + "\n"
            )
            continue
        if key == "e" or (key == "" and not plan.ready):
            if plan.record is not None:
                # R1 P1: managed plans never edit the launchable intent; the
                # editor on a managed resume was the same override vector.
                output_stream.write(
                    RESUME_RECORDED_NOTE.format(uuid=plan.record["session_id"]) + "\n"
                )
                continue
            editor_state = _editor_state_for_plan(runtime, plan)
            try:
                outcome = run_editor(
                    editor_state,
                    input_stream,
                    output_stream,
                    force_line=True,
                    plan=plan,
                    runtime=runtime,
                    no_color=no_color,
                )
            except (EditorError, KeyError) as exc:
                plan = _editor_failure_plan(
                    runtime, plan, editor_state.document, exc
                )
                continue
            if outcome is None:
                continue
            updated = _apply_outcome_or_failure(runtime, plan, outcome)
            if updated is None:
                return 0
            plan = updated
            continue
        if key == "" and plan.ready:
            session_id = plan.record["session_id"] if plan.record else None
            try:
                prepared = runtime.prepare(
                    plan.document,
                    action=plan.action,
                    passthrough=passthrough,
                    session_id=session_id,
                    legacy_requested=plan.legacy_requested,
                )
            except (ValueError, CLIError) as exc:
                plan.errors.append(str(exc))
                continue
            return runtime.perform(prepared)


def _session_records(runtime: Runtime) -> list[dict[str, Any]]:
    records = []
    for path in sorted(runtime.session_store.sessions_dir.glob("*.json")):
        session_id = path.stem
        if not sessions.UUID4.fullmatch(session_id):
            continue
        try:
            records.append(runtime.session_store.load(session_id))
        except sessions.SessionError:
            continue
    return records


# UX section 4 sessions screen strings (single source; tests pin them).
SESSIONS_TITLE = "sessions"
SESSIONS_KEYBAR = (
    ("R", "resume"),
    ("T", "transition"),
    ("F", "forget"),
    ("Q", "quit"),
)
SESSIONS_EMPTY = "(no recorded sessions)"
FORGET_MODAL_TITLE = "Forget session {short}?"
FORGET_MODAL_BODY = (
    "Deletes: session record + generated scope{scope_note}.\n"
    "Transcripts are never touched."
)
FORGET_DONE = "Forgot {session_id}; record + scope deleted. Transcripts are never touched."
RESUME_MODAL_TITLE = "Resume session {short}?"
TRANSITION_SELECT_TITLE = "Transition {short} to composition"
TRANSITION_MODAL_TITLE = "Confirm transition"
TRANSITION_MODAL_BODY = (
    "The target Claude process must have EXITED (not merely idle);\n"
    "exiting restarts the turn.\n"
    "\n"
    "Has the target process exited?"
)


class _SessionsScreen:
    """UX section 4 sessions Table: mode column + Modal-confirmed row actions.

    Returns ("resume", record) or ("transition", record, composition) for the
    CLI to execute after curses teardown; forget runs inline (no launch) and
    refreshes the table; Esc/Q returns None.
    """

    def __init__(self, runtime: Runtime, *, palette: tui.Palette):
        self.runtime = runtime
        self.palette = palette
        self.records = _session_records(runtime)
        self.selected = 0
        self.message = ""

    def _rows(self) -> list[list[str]]:
        return [
            [
                f"{record['session_id'][:12]}…",
                f"cm:{record['composition_name']}",
                _record_mode_label(record),
                record["cwd"],
                record["created_at"],
            ]
            for record in self.records
        ]

    def _draw(self, win: Any) -> None:
        win.erase()
        palette = self.palette
        height, width = win.getmaxyx()
        tui.safe_add(win, 0, 0, SESSIONS_TITLE, palette.attr("accent") | curses.A_BOLD)
        tui.safe_add(win, 1, 0, "─" * min(width - 1, 60), palette.attr("dim"))
        if not self.records:
            tui.safe_add(win, 3, 0, SESSIONS_EMPTY, palette.attr("dim"))
        else:
            table = tui.Table(
                ["session", "composition", "mode", "cwd", "created"],
                self._rows(),
                selected=self.selected,
                min_widths=[13, 10, 11, 8, 19],
            )
            table.draw(win, 3, 0, width, palette, max_rows=height - 7)
            record = self.records[table.selected]
            self.selected = table.selected
            tui.safe_add(win, height - 3, 0, _record_actions_label(record), palette.attr("dim"))
        if self.message:
            tui.safe_add(win, height - 2, 0, self.message, palette.attr("warn"))
        tui.KeyBar(SESSIONS_KEYBAR).draw(win, height - 1, palette)
        win.refresh()

    def _forget(self, win: Any, record: dict[str, Any]) -> None:
        session_id = record["session_id"]
        short = f"{session_id[:8]}…"
        scope_exists = scope_mod.scope_dir(
            self.runtime.session_store.root, session_id
        ).is_dir()
        scope_note = "" if scope_exists else " (no generated scope exists)"
        confirmed = tui.Modal(
            FORGET_MODAL_TITLE.format(short=short),
            FORGET_MODAL_BODY.format(scope_note=scope_note).splitlines(),
            buttons=(("Forget", True), ("Cancel", False)),
        ).run(win, self.palette)
        if not confirmed:
            self.message = "Forget cancelled."
            return
        # Same effects as `sessions forget` (remove_scope validates the name).
        scope_mod.remove_scope(self.runtime.session_store.root, session_id)
        self.runtime.session_store.forget(session_id)
        self.runtime.session_store.clear_last(self.runtime.cwd, session_id)
        self.records = _session_records(self.runtime)
        self.selected = min(self.selected, max(0, len(self.records) - 1))
        self.message = FORGET_DONE.format(session_id=session_id)

    def _choose_composition(self, win: Any, record: dict[str, Any]) -> str | None:
        short = f"{record['session_id'][:8]}…"
        names = self.runtime.compositions.names()
        chooser = tui.SelectList(
            TRANSITION_SELECT_TITLE.format(short=short),
            [tui.SelectItem(name) for name in names],
            footer=(("Enter", "choose"), ("Esc", "back")),
            selected=names.index(record["composition_name"])
            if record["composition_name"] in names
            else 0,
        )
        index = chooser.run(win, self.palette)
        if index is None:
            return None
        return names[index]

    def run(self, win: Any) -> tuple[str, dict[str, Any]] | tuple[str, dict[str, Any], str] | None:
        tui.hide_cursor()
        while True:
            self._draw(win)
            key = tui.read_key(win)
            if key.kind == "resize":
                continue
            if key.kind == "ctrl" and key.ch == "c":
                raise KeyboardInterrupt
            if key.kind == "esc" or (key.kind == "char" and key.ch == "q"):
                return None
            if not self.records:
                continue
            record = self.records[self.selected]
            if key.kind == "up" or (key.kind == "char" and key.ch == "k"):
                self.selected = (self.selected - 1) % len(self.records)
                continue
            if key.kind == "down" or (key.kind == "char" and key.ch == "j"):
                self.selected = (self.selected + 1) % len(self.records)
                continue
            if key.kind == "char" and key.ch == "r":
                lines: list[str] = []
                if record["mode"] != "durable":
                    lines = LEGACY_RESUME_NOTE.split("; ")
                confirmed = tui.Modal(
                    RESUME_MODAL_TITLE.format(short=f"{record['session_id'][:8]}…"),
                    lines,
                    buttons=(("Resume", True), ("Cancel", False)),
                ).run(win, self.palette)
                if confirmed:
                    return ("resume", record)
                self.message = "Resume cancelled."
                continue
            if key.kind == "char" and key.ch == "t":
                name = self._choose_composition(win, record)
                if name is not None:
                    return ("transition", record, name)
                continue
            if key.kind == "char" and key.ch == "f":
                self._forget(win, record)
                continue


class _TransitionScreen:
    """Semantic diff view + exited-confirmation Modal (TRANSITIONS section 3).

    The wording states the target process must have EXITED, not merely idle.
    Returns True only on explicit confirmation; Esc cancels (False).
    """

    KEYBAR = (("Enter", "confirm exited"), ("Esc", "cancel"))

    def __init__(self, diff: list[str], *, palette: tui.Palette):
        self.diff = diff
        self.palette = palette
        self.scroll = 0

    def _draw(self, win: Any) -> None:
        win.erase()
        palette = self.palette
        height, width = win.getmaxyx()
        tui.safe_add(win, 0, 0, "transition — semantic diff", palette.attr("accent") | curses.A_BOLD)
        tui.safe_add(win, 1, 0, "─" * min(width - 1, 60), palette.attr("dim"))
        visible = max(1, height - 4)
        self.scroll = max(0, min(self.scroll, max(0, len(self.diff) - visible)))
        for offset, line in enumerate(self.diff[self.scroll : self.scroll + visible]):
            tui.safe_add(win, 2 + offset, 0, line)
        if len(self.diff) > visible:
            tui.safe_add(
                win,
                height - 2,
                0,
                f"{self.scroll + 1}-{min(len(self.diff), self.scroll + visible)} of {len(self.diff)}",
                palette.attr("dim"),
            )
        tui.KeyBar(self.KEYBAR).draw(win, height - 1, palette)
        win.refresh()

    def run(self, win: Any) -> bool:
        tui.hide_cursor()
        while True:
            self._draw(win)
            key = tui.read_key(win)
            if key.kind == "resize":
                continue
            if key.kind == "ctrl" and key.ch == "c":
                raise KeyboardInterrupt
            if key.kind == "esc" or (key.kind == "char" and key.ch == "q"):
                return False
            if key.kind == "up" or (key.kind == "char" and key.ch == "k"):
                self.scroll -= 1
                continue
            if key.kind == "down" or (key.kind == "char" and key.ch == "j"):
                self.scroll += 1
                continue
            if key.kind == "enter":
                return bool(
                    tui.Modal(
                        TRANSITION_MODAL_TITLE,
                        TRANSITION_MODAL_BODY.splitlines(),
                        buttons=(("It has exited", True), ("Cancel", False)),
                    ).run(win, self.palette)
                )


def _transition_confirm(
    diff: list[str],
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    no_color: bool,
) -> bool:
    """Exited-confirmation: curses Modal when capable, else the [y/N] line."""

    if tui.streams_curses_capable(input_stream, output_stream):
        palette = tui.detect_palette(
            no_color=no_color, tty_in=input_stream, tty_out=output_stream
        )
        screen = _TransitionScreen(diff, palette=palette)
        try:
            return bool(
                tui.run_curses_on_streams(
                    screen.run, input_stream, output_stream, palette=palette
                )
            )
        except KeyboardInterrupt:
            return False
        except (curses.error, OSError):
            pass  # fall through to the line prompt
    output_stream.write(
        "The target Claude process must have EXITED (not merely idle); "
        "exiting restarts the turn.\n"
        "Has the target process exited? [y/N] "
    )
    output_stream.flush()
    answer = input_stream.readline()
    return answer.strip().lower() == "y"


def _sessions_list_tui(
    runtime: Runtime,
    args: argparse.Namespace,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    no_color: bool,
) -> int:
    """Interactive sessions screen; actions reuse the command flows verbatim."""

    palette = tui.detect_palette(
        no_color=no_color, tty_in=input_stream, tty_out=output_stream
    )
    screen = _SessionsScreen(runtime, palette=palette)
    result = tui.run_curses_on_streams(
        screen.run, input_stream, output_stream, palette=palette
    )
    if result is None:
        return 0
    if result[0] == "resume":
        record = result[1]
        plan = managed_plan(runtime, record)
        if not plan.ready:
            output_stream.write(render_quick_confirm(runtime, plan, width=_stream_width(output_stream)))
            output_stream.write(
                "composition is blocked: "
                + "; ".join(tui.visible_text(error) for error in plan.errors)
                + "\n"
            )
            return 2
        prepared = runtime.prepare(
            plan.document,
            action="resume",
            passthrough=[],
            session_id=record["session_id"],
        )
        return runtime.perform(prepared)
    if result[0] == "transition":
        _, record, name = result
        namespace = argparse.Namespace(
            uuid=record["session_id"],
            transition_composition=name,
            its_exited=False,
        )
        return _sessions_transition(
            runtime,
            namespace,
            input_stream=input_stream,
            output_stream=output_stream,
            interactive=True,
            no_color=no_color,
        )
    raise CLIError(f"unknown sessions action {result[0]!r}")


def _record_mode_label(record: dict[str, Any]) -> str:
    """UX §4 mode column: durable with generation, or legacy."""

    if record["mode"] == "durable":
        return f"durable(g{record['scope_generation']})"
    return "legacy"


def _record_actions_label(record: dict[str, Any]) -> str:
    """UX §4 per-row action hints; legacy rows state the one-time upgrade."""

    if record["mode"] == "durable":
        return "[r]esume [t]ransition [f]orget"
    return "[r]esume (upgrades to durable) [f]orget"


def _transition_module() -> Any:
    """Lane D's transition engine, imported lazily.

    The rest of the CLI must never depend on its presence; when it is absent
    the command fails closed with an actionable message rather than a
    traceback.
    """

    try:
        from . import transition
    except ImportError as exc:
        raise CLIError(
            "session transitions are unavailable in this build: "
            "claude_multi.transition is not installed"
        ) from exc
    return transition


def _transition_preflight_problems(runtime: Runtime) -> list[str]:
    """R1 L3: binary verification + gateway readiness, via Runtime accessors.

    The same accessors Doctor uses, so the transition can never proceed when
    Doctor would report BLOCKED: ``doctor_binary_callback`` wraps
    ``launch.resolve_claude`` (full-hash binary verification) and the
    readiness branch runs ``launch.check_readiness`` (loopback-only) unless a
    doctor override is injected.
    """

    problems, _info = runtime.doctor_binary_callback(
        runtime.catalog.docs["native-contract"]
    )
    problems = list(problems)
    if runtime.doctor_callback is not None:
        problems.extend(runtime.doctor_callback(runtime))
    else:
        try:
            launch.check_readiness(runtime.catalog.docs["gateway"])
        except launch.LaunchError as exc:
            problems.append(f"local gateway: {exc}")
    return problems


def _print_sessions_listing(runtime: Runtime, output_stream: TextIO) -> None:
    """The text sessions listing (line-mode fallback + quick-confirm S key)."""

    records = _session_records(runtime)
    output_stream.write("sessions\n")
    output_stream.write("----------------------------------------\n")
    for record in records:
        # Record fields (cwd, composition_name) are external text;
        # the whole row is sanitized single-line output.
        output_stream.write(
            tui.visible_text(
                f"{record['session_id']}  cm:{record['composition_name']}  "
                f"{_record_mode_label(record)}  {record['cwd']}  "
                f"{record['created_at']}  {_record_actions_label(record)}"
            )
            + "\n"
        )
    if not records:
        output_stream.write("(no recorded sessions)\n")
    output_stream.write(
        "actions: [r]esume `claude-multi -r <uuid>` · "
        "[t]ransition `claude-multi sessions transition <uuid> --composition <name>` · "
        "[f]orget `claude-multi sessions forget <uuid>` "
        "(deletes the record + generated scope; transcripts are never touched)\n"
    )


def _sessions_transition(
    runtime: Runtime,
    args: argparse.Namespace,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    interactive: bool,
    no_color: bool = False,
) -> int:
    """TRANSITIONS §3 flow: diff, exited-confirmation, then Lane D executes."""

    if not sessions.UUID4.fullmatch(args.uuid):
        raise CLIError(f"{args.uuid!r} is not a UUIDv4")
    transition = _transition_module()
    runtime.session_store.load(args.uuid)  # the record must exist
    document = runtime.compositions.load(args.transition_composition)
    try:
        plan = transition.prepare(
            runtime.session_store, args.uuid, document, runtime.catalog
        )
    except transition.TransitionError as exc:
        raise CLIError(str(exc)) from exc
    # TRANSITIONS §3 step 2: the semantic diff is printed before anything is
    # confirmed or mutated.  The curses path renders the same diff as a view.
    interactive_diff = (
        interactive
        and not args.its_exited
        and tui.streams_curses_capable(input_stream, output_stream)
    )
    if not interactive_diff:
        for line in plan.diff:
            output_stream.write(f"{tui.visible_text(line)}\n")
    if args.its_exited:
        confirm = True
    elif interactive:
        confirm = _transition_confirm(
            plan.diff,
            input_stream=input_stream,
            output_stream=output_stream,
            no_color=no_color,
        )
    else:
        confirm = False
    if confirm:
        # R1 L3: preflight BEFORE transition.execute may mutate scope or
        # record — binary verification and gateway readiness failures abort
        # the transition with nothing touched. Print-only dry runs (no
        # confirmation) stay available without a live gateway.
        problems = _transition_preflight_problems(runtime)
        if problems:
            raise CLIError(
                TRANSITION_PREFLIGHT_REFUSAL + "; ".join(problems)
            )
    try:
        outcome = transition.execute(
            plan, confirm_exited=confirm, environ=runtime.environ
        )
    except transition.TransitionError as exc:
        raise CLIError(str(exc)) from exc
    if outcome.kind == "print_only":
        # From inside the target session, or without confirmation: nothing
        # was mutated; print the exact post-exit command verbatim.
        command_text = tui.visible_text(outcome.command_text)
        output_stream.write(command_text)
        if not command_text.endswith("\n"):
            output_stream.write("\n")
        return 0
    if outcome.kind == "relaunch":
        prepared = PreparedLaunch(
            outcome.compile_result,
            outcome.record,
            plan.target_resolved,
            copy.deepcopy(document),
        )
        try:
            return runtime.perform(prepared)
        except OSError as exc:
            transition.restore_exec_failure(
                runtime.session_store,
                args.uuid,
                outcome.prior_record_bytes,
                expected_record_bytes=outcome.committed_record_bytes,
            )
            raise CLIError(
                f"relaunch exec failed ({exc}); the prior scope generation "
                f"and record were restored. Retry with: {plan.command_text}"
            ) from exc
    raise CLIError(f"unknown transition outcome kind {outcome.kind!r}")


def _print_composition(runtime: Runtime, document: dict[str, Any], stream: TextIO) -> None:
    plan = build_quick_plan(runtime, document, action="fresh", source="Saved composition")
    stream.write(render_quick_confirm(runtime, plan, details=True))


def _run_editor_command(
    runtime: Runtime,
    document: dict[str, Any],
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    force_line: bool,
    no_color: bool = False,
) -> int:
    plan = build_quick_plan(runtime, document, action="fresh", source="Editor")
    while True:
        editor_state = _editor_state_for_plan(runtime, plan)
        try:
            outcome = run_editor(
                editor_state,
                input_stream,
                output_stream,
                force_line=force_line,
                plan=plan,
                runtime=runtime,
                no_color=no_color,
            )
        except (EditorError, KeyError) as exc:
            plan = _editor_failure_plan(
                runtime, plan, editor_state.document, exc
            )
            output_stream.write(f"BLOCKED: {tui.visible_text(plan.errors[-1])}\n")
            continue
        if outcome is None:
            return 0
        updated = _apply_outcome_or_failure(runtime, plan, outcome)
        if updated is not None and updated.source == "Unsaved editor changes":
            output_stream.write(f"BLOCKED: {tui.visible_text(updated.errors[-1])}\n")
            plan = updated
            continue
        if updated is not None:
            output_stream.write(
                f"Composition {tui.visible_text(updated.document['name'])!r} ready.\n"
            )
        return 0


def handle_command(
    runtime: Runtime,
    args: argparse.Namespace,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    interactive: bool,
    no_color: bool = False,
) -> int:
    if args.command == "compose":
        command = args.compose_command
        if command == "list":
            for name in runtime.compositions.names():
                origin = "user" if runtime.compositions.has_user(name) else "trusted seed"
                output_stream.write(f"{tui.visible_text(name)}\t{origin}\n")
            return 0
        if command == "show":
            _print_composition(runtime, runtime.compositions.load(args.name), output_stream)
            return 0
        if command == "restore-default":
            runtime.compositions.restore_default()
            output_stream.write("Restored trusted default as user composition 'default'.\n")
            return 0
        if command == "delete":
            removed = runtime.compositions.delete(args.name)
            output_stream.write(
                f"{'Deleted' if removed else 'Not found'}: {tui.visible_text(args.name)}\n"
            )
            return 0
        if command in ("duplicate", "use-as-template"):
            runtime.compositions.duplicate(args.source, args.target)
            output_stream.write(
                f"Created {tui.visible_text(args.target)!r} from {tui.visible_text(args.source)!r}.\n"
            )
            return 0
        if command == "rename":
            runtime.compositions.rename(args.source, args.target)
            output_stream.write(
                f"Renamed {tui.visible_text(args.source)!r} to {tui.visible_text(args.target)!r}.\n"
            )
            return 0
        if not interactive:
            raise CLIError(f"compose {command} requires an interactive terminal")
        if command == "new":
            runtime.compositions.require_new_target(args.name)
            document = copy.deepcopy(runtime.catalog.default_composition)
            document["name"] = args.name
        else:
            document = runtime.compositions.load(args.name)
        return _run_editor_command(
            runtime,
            document,
            input_stream=input_stream,
            output_stream=output_stream,
            force_line=args.line,
            no_color=no_color,
        )

    if args.command == "sessions":
        command = args.sessions_command
        if command == "list":
            if (
                interactive
                and not args.line
                and tui.streams_curses_capable(input_stream, output_stream)
            ):
                try:
                    return _sessions_list_tui(
                        runtime,
                        args,
                        input_stream=input_stream,
                        output_stream=output_stream,
                        no_color=no_color,
                    )
                except KeyboardInterrupt:
                    return 0
                except (curses.error, OSError):
                    pass  # fall back to the text listing below
            _print_sessions_listing(runtime, output_stream)
            return 0
        if command == "show":
            record = runtime.session_store.load(args.uuid)
            output_stream.write(strict_json.canonical_file_bytes(record).decode("utf-8"))
            return 0
        if command == "forget":
            # remove_scope validates the name (fail closed on unsafe input)
            # before anything is unlinked.
            scope_removed = scope_mod.remove_scope(
                runtime.session_store.root, args.uuid
            )
            removed = runtime.session_store.forget(args.uuid)
            runtime.session_store.clear_last(runtime.cwd, args.uuid)
            if not removed:
                output_stream.write(f"Not found: {args.uuid}\n")
                return 0
            output_stream.write(f"Forgot: {args.uuid}\n")
            output_stream.write(
                "Deleted: session record + generated scope"
                + ("" if scope_removed else " (no generated scope existed)")
                + ". Transcripts are never touched.\n"
            )
            return 0
        if command == "transition":
            return _sessions_transition(
                runtime,
                args,
                input_stream=input_stream,
                output_stream=output_stream,
                interactive=interactive,
                no_color=no_color,
            )
        if command == "link":
            if not sessions.UUID4.fullmatch(args.uuid):
                raise CLIError(f"{args.uuid!r} is not a UUIDv4")
            name = args.link_composition
            if name is None:
                if not interactive:
                    raise CLIError("sessions link without a TTY requires --composition NAME")
                document, _ = remembered_document(runtime)
            else:
                document = runtime.compositions.load(name)
            resolved = runtime.resolve_document(document)
            record = sessions.make_record(
                session_id=args.uuid,
                cwd=runtime.cwd,
                composition_name=document["name"],
                snapshot=composition.snapshot(resolved),
                catalog_version=runtime.catalog_version,
                catalog_hash=runtime.catalog.bundle_sha256,
                launcher_version=runtime.launcher_version,
            )
            runtime.session_store.link(record)
            runtime.session_store.update_last(runtime.cwd, args.uuid)
            output_stream.write(f"Linked {args.uuid} to composition {document['name']!r}.\n")
            return 0

    if args.command == "models":
        for model_id, model in sorted(runtime.catalog.models.items()):
            provider = runtime.catalog.providers[model["provider"]]["display"]
            capabilities = ",".join(model["capabilities"])
            output_stream.write(
                f"{model_id}\t{model['display']}\t{provider}\t{capabilities}\t{model['context']['kind']}\n"
            )
        return 0

    if args.command == "show":
        name = args.show_composition or "default"
        _print_composition(runtime, runtime.compositions.load(name), output_stream)
        return 0

    if args.command == "doctor":
        if args.doctor_repair is not None:
            return _doctor_repair(runtime, args.doctor_repair, output_stream)
        if args.doctor_prune:
            return _doctor_prune(runtime, output_stream)
        problems: list[str] = []
        for name in runtime.compositions.names():
            try:
                resolved = runtime.resolve_document(runtime.compositions.load(name))
            except (CLIError, ValueError) as exc:
                problems.append(f"composition {name}: {exc}")
            else:
                for issue in proxy_mod.selected_secret_problems(
                    resolved,
                    runtime.catalog.docs["models"]["models"],
                    runtime.catalog.docs["providers"]["providers"],
                    environ=runtime.environ,
                ):
                    problems.append(f"composition {name}: {issue}")
        # G0': the same resolver as launch; Doctor can never report Ready when
        # binary verification would fail. Daemon status is informational only.
        binary_problems, binary_info = runtime.doctor_binary_callback(
            runtime.catalog.docs["native-contract"]
        )
        problems.extend(binary_problems)
        daemon = runtime.doctor_daemon_callback()
        info_lines = [*binary_info, f"Shared daemon: {daemon.summary}."]
        if runtime.doctor_callback is not None:
            problems.extend(runtime.doctor_callback(runtime))
        else:
            try:
                launch.check_readiness(runtime.catalog.docs["gateway"])
            except launch.LaunchError as exc:
                problems.append(f"local gateway: {exc}")
        # SPEC §7 additions: session/scope integrity, collisions, evidence.
        scope_info, scope_problems = _doctor_scope_report(runtime)
        info_lines.extend(scope_info)
        problems.extend(scope_problems)
        collision_info, collision_problems = _doctor_collision_report(runtime)
        info_lines.append(collision_info)
        problems.extend(collision_problems)
        info_lines.append(f"Evidence: {EVIDENCE_ADD_DIR_CARRY}")
        # Badge styling only when color is active (a tty, not NO_COLOR, not
        # --no-color); the line contract itself never changes (UX §5/§8).
        palette = _output_palette(input_stream, output_stream, no_color)
        if problems:
            output_stream.write(palette.ansi("BLOCKED", "error") + "\n")
            for problem in problems:
                output_stream.write(
                    palette.ansi(f"  - {tui.visible_text(problem)}", "error") + "\n"
                )
            for line in info_lines:
                output_stream.write(f"{tui.visible_text(line)}\n")
            return 1
        output_stream.write(palette.ansi("Ready", "ok") + "\n")
        for line in info_lines:
            output_stream.write(f"{tui.visible_text(line)}\n")
        output_stream.write("Catalog, compositions, and local gateway are valid.\n")
        return 0

    raise CLIError(f"unsupported command {args.command!r}")


def _output_palette(
    input_stream: TextIO, output_stream: TextIO, no_color: bool
) -> tui.Palette:
    """Palette for line-mode badge styling; mono unless the output is a tty."""

    try:
        is_tty = bool(output_stream.isatty())
    except (AttributeError, ValueError, OSError):
        is_tty = False
    if not is_tty:
        return tui.MONO_PALETTE
    return tui.detect_palette(
        no_color=no_color, tty_in=input_stream, tty_out=output_stream
    )


def _read_scope_disk_plan(live: Path) -> scope_mod.ScopePlan | None:
    """Read an on-disk scope as a plan for hash comparison. Read-only.

    Returns None when any expected surface is unreadable (a scope that cannot
    be read cannot be verified; the caller reports a mismatch).
    """

    agent_files: dict[str, bytes] = {}
    agents_dir = live / ".claude" / "agents"
    try:
        entries = sorted(agents_dir.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        try:
            if not entry.is_file() or entry.suffix != ".md":
                continue
            agent_files[f".claude/agents/{entry.name}"] = state.read_private(entry)
        except (OSError, state.StateError):
            return None
    try:
        settings = strict_json.loads(state.read_private(live / "settings.json"))
    except (OSError, state.StateError, strict_json.StrictJSONError):
        return None
    if not isinstance(settings, dict):
        return None
    return scope_mod.ScopePlan(agent_files=agent_files, settings=settings)


def _check_scope_integrity(runtime: Runtime, record: dict[str, Any]) -> tuple[str, bool]:
    """SPEC §7.1: on-disk scope vs the re-compiled expectation (read-only).

    Returns ``(line, is_problem)``. The scope is a pure function of (record
    composition, installed catalog), so equality of ``plan_hash`` values is
    the record↔scope generation check; catalog drift is displayed, never
    hidden.
    """

    session_id = record["session_id"]
    short = f"{session_id[:8]}…"
    generation = record["scope_generation"]
    repair = (
        f"Run claude-multi doctor --repair {session_id} to regenerate "
        "them from the record."
    )
    try:
        resolved = runtime.resolve_document(snapshot_to_document(record))
        expected = scope_mod.compile_scope(
            resolved,
            runtime.catalog.docs["roles"]["roles"],
            runtime.catalog.prompt_bodies,
            scope_mod.catalog_meta_from_docs(runtime.catalog.docs),
        )
    except (ValueError, KeyError) as exc:
        return (
            f"scope for {short} cannot be verified against the installed "
            f"catalog: {exc}. {repair}",
            True,
        )
    expected_paths = sorted(expected.agent_files) + ["settings.json"]
    total = len(expected_paths)
    live = scope_mod.scope_dir(runtime.session_store.root, session_id)
    if not live.is_dir():
        return f"scope files for {short} are missing (0/{total}). {repair}", True
    drift = ""
    if record["catalog_hash"] != runtime.catalog.bundle_sha256:
        drift = " · recompiled against the installed catalog (catalog drift)"
    actual = _read_scope_disk_plan(live)
    if actual is not None and scope_mod.plan_hash(actual) == scope_mod.plan_hash(expected):
        return f"Scope: {short} OK ({total} files, gen {generation}){drift}.", False
    present = sum(1 for relpath in expected_paths if (live / relpath).is_file())
    if present < total:
        return (
            f"scope files for {short} are missing ({present}/{total}). {repair}",
            True,
        )
    return (
        f"scope for {short} does not match generation {generation} in the "
        f"record (record↔scope mismatch). {repair}",
        True,
    )


def _doctor_scope_report(runtime: Runtime) -> tuple[list[str], list[str]]:
    """Session census plus per-durable-session scope integrity (SPEC §7.1)."""

    records = _session_records(runtime)
    durable = [record for record in records if record["mode"] == "durable"]
    legacy = len(records) - len(durable)
    info = [
        f"Sessions: {len(records)} recorded · {len(durable)} durable · "
        f"{legacy} legacy."
    ]
    problems: list[str] = []
    for record in durable:
        line, is_problem = _check_scope_integrity(runtime, record)
        if is_problem:
            problems.append(line)
        else:
            info.append(line)
    return info, problems


def _doctor_collision_report(runtime: Runtime) -> tuple[str, list[str]]:
    """SPEC §7.2: exact cm-* collision scan of the cwd project agent tree."""

    files = _project_agent_files(runtime.cwd)
    count = len(files)
    noun = "project agent" if count == 1 else "project agents"
    try:
        resolved = runtime.resolve_document(runtime.compositions.load("default"))
    except (CLIError, ValueError) as exc:
        return (
            f"Collisions: not checked (default composition unavailable: {exc}).",
            [],
        )
    generated = {variant.id for variant in resolved.variants}
    collisions = scope_mod.find_cm_collisions(runtime.cwd, [], generated)
    if collisions:
        problems = [
            _collision_error(path, name, runtime.cwd) for path, name in collisions
        ]
        return (
            f"Collisions: {len(collisions)} blocking ({count} {noun}).",
            problems,
        )
    return f"Collisions: none ({count} {noun}).", []


def _remove_scope_tree(path: Path) -> None:
    """Remove a scope tree; refuse symlinks and non-directories.

    Mirrors scope.py's own removal guards; used only for ``.<uuid>.prev``
    staging dirs, which have no store-level removal API.
    """

    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise state.StateError(errno.ELOOP, f"scope path {path} is a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise state.StateError(
            errno.ENOTDIR, f"scope path {path} is not a directory"
        )
    shutil.rmtree(path)


def _doctor_prune(runtime: Runtime, output_stream: TextIO) -> int:
    """SPEC §7.4: remove stale scopes. Generated files only.

    Prunes ``scopes/.<uuid>.new`` / ``scopes/.<uuid>.prev`` staging dirs and
    live scopes whose records are gone. Anything with a living record, and
    any entry that is not a recognized scope dir, is left untouched.
    """

    store = runtime.session_store
    scopes_root = store.root / "scopes"
    removed: list[str] = []
    if scopes_root.is_dir():
        for entry in sorted(scopes_root.iterdir()):
            name = entry.name
            if name.startswith("."):
                stem = name[1:]
                for suffix in (".new", ".prev"):
                    if stem.endswith(suffix) and sessions.UUID4.fullmatch(
                        stem[: -len(suffix)]
                    ):
                        _remove_scope_tree(entry)
                        removed.append(f"stale staging dir scopes/{name}")
                        break
            elif sessions.UUID4.fullmatch(name) and not store.exists(name):
                scope_mod.remove_scope(store.root, name)
                removed.append(f"scope for forgotten session {name}")
    if not removed:
        output_stream.write(
            "Prune: nothing stale; every scope has a living record.\n"
        )
        return 0
    output_stream.write("Pruned:\n")
    for line in removed:
        output_stream.write(f"  - {line}\n")
    return 0


def _doctor_repair(runtime: Runtime, uuid: str, output_stream: TextIO) -> int:
    """SPEC §7.1: converge a scope to its record authority via Lane D."""

    if not sessions.UUID4.fullmatch(uuid):
        raise CLIError(f"{uuid!r} is not a UUIDv4")
    transition = _transition_module()
    runtime.session_store.load(uuid)  # the record must exist
    try:
        report = transition.converge(
            runtime.session_store.root,
            runtime.session_store,
            uuid,
            runtime.catalog,
        )
    except transition.TransitionError as exc:
        raise CLIError(str(exc)) from exc
    for line in report:
        output_stream.write(f"{tui.visible_text(line)}\n")
    return 0


def _stdio_streams_are_ttys() -> bool:
    """Probe stdio TTY-ness, failing closed on missing/closed/broken streams."""

    try:
        return bool(
            sys.stdin is not None
            and sys.stdout is not None
            and sys.stdin.isatty()
            and sys.stdout.isatty()
        )
    except (AttributeError, ValueError, OSError):
        return False


def _open_tty_streams() -> tuple[TextIO, TextIO]:
    """Open the interactive (input, output) stream pair, preferring /dev/tty.

    /dev/tty is non-seekable and cannot be opened in update mode under
    supported Python, so separate read/write handles are opened. When
    /dev/tty cannot be opened (no controlling terminal), fall back to the
    standard streams only when both probe as real TTYs; otherwise fail closed
    for noninteractive handling. Only /dev/tty handles are caller-owned; the
    standard streams are never closed by the caller.
    """

    try:
        tty_in = open("/dev/tty", "r", encoding="utf-8", buffering=1)
        try:
            tty_out = open("/dev/tty", "w", encoding="utf-8", buffering=1)
        except OSError:
            tty_in.close()
            raise
        return tty_in, tty_out
    except OSError as exc:
        if _stdio_streams_are_ttys():
            return sys.stdin, sys.stdout
        raise CLIError(
            "no interactive terminal; use --composition NAME for noninteractive launch"
        ) from exc


def _validate_uuid(value: str) -> str:
    if not sessions.UUID4.fullmatch(value):
        raise CLIError(f"resume ID {value!r} is not a UUIDv4")
    return value


def _resolve_resume_target(runtime: Runtime, value: str) -> str:
    """Resolve a resume argument to a managed session UUID.

    Native Claude's exit hint prints ``claude --resume "cm:<composition>"``
    (the display name, not the UUID). Accept that form here: an exact UUIDv4
    resumes as before; otherwise the value (with an optional ``cm:`` prefix)
    matches managed sessions by composition name — exactly one match resumes,
    several list the candidates with their UUIDs, none fails with a pointer
    to ``claude-multi sessions list``.
    """

    if sessions.UUID4.fullmatch(value):
        return value
    name = value.removeprefix("cm:")
    matches = [
        record
        for record in _session_records(runtime)
        if record["composition_name"] == name
    ]
    matches.sort(key=lambda record: record["created_at"], reverse=True)
    if len(matches) == 1:
        return matches[0]["session_id"]
    if matches:
        lines = [
            f"resume name {value!r} matches {len(matches)} managed sessions; "
            "resume an exact UUID instead:"
        ]
        for record in matches:
            lines.append(
                f"  {record['session_id']}  "
                f"{tui.visible_text(record['composition_name'])}  "
                f"{record['created_at']}  {tui.visible_text(record['cwd'])}"
            )
        raise CLIError("\n".join(lines))
    raise CLIError(
        f"resume ID {value!r} is not a UUIDv4 and matches no managed session "
        "name; run `claude-multi sessions list` for resumable sessions and UUIDs"
    )


def _refuse_resume_override(
    composition_name: str | None, record: dict[str, Any]
) -> None:
    """R1 P1: ordinary resume never accepts a changed composition.

    A name equal to the recorded one is not an override (the recorded intent
    is re-resolved anyway); anything else is refused with the transition
    command named as the one path.
    """

    if composition_name is not None and composition_name != record["composition_name"]:
        raise CLIError(
            RESUME_OVERRIDE_REFUSAL.format(
                name=composition_name, uuid=record["session_id"]
            )
        )


def main(
    argv: list[str] | None = None,
    *,
    runtime: Runtime | None = None,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    interactive: bool | None = None,
) -> int:
    launcher_args, passthrough = split_passthrough(list(sys.argv[1:] if argv is None else argv))
    parser = build_parser()
    try:
        args = parser.parse_args(launcher_args)
    except SystemExit:
        raise

    runtime = runtime or Runtime(asset_root=default_asset_root())
    output = output_stream or sys.stdout
    owned_tty: tuple[TextIO, TextIO] | None = None
    tty_in: TextIO | None = None
    tty_out: TextIO | None = None
    try:
        if interactive is None:
            if input_stream is not None:
                interactive = True
            else:
                try:
                    tty_in, tty_out = _open_tty_streams()
                    if tty_in is not sys.stdin:
                        owned_tty = (tty_in, tty_out)
                    interactive = True
                except CLIError:
                    interactive = False
        inp = input_stream or tty_in or sys.stdin
        tty_output = output_stream or tty_out or output

        if args.command is not None:
            if passthrough:
                raise CLIError("passthrough arguments are accepted only for launch")
            return handle_command(
                runtime,
                args,
                input_stream=inp,
                output_stream=tty_output,
                interactive=bool(interactive),
                no_color=args.no_color,
            )

        if not interactive and args.composition is None:
            raise CLIError(
                "noninteractive launch requires --composition NAME; no default was selected"
            )

        if args.resume:
            session_id = _resolve_resume_target(runtime, args.resume)
            record = runtime.session_store.load(session_id)
            _refuse_resume_override(args.composition, record)
            plan = managed_plan(runtime, record)
            plan.legacy_requested = args.legacy
        elif args.continue_last:
            session_id = runtime.session_store.last(runtime.cwd)
            if session_id is None:
                raise CLIError(
                    "no managed session is recorded for this directory; use -r UUID or start fresh"
                )
            record = runtime.session_store.load(session_id)
            _refuse_resume_override(args.composition, record)
            plan = managed_plan(runtime, record)
            plan.legacy_requested = args.legacy
        else:
            if args.composition is not None:
                document = runtime.compositions.load(args.composition)
                source = "Explicit composition"
            else:
                document, source = remembered_document(runtime)
            plan = build_quick_plan(runtime, document, action="fresh", source=source)
            plan.legacy_requested = args.legacy

        if not interactive:
            if not plan.ready:
                raise CLIError("composition is blocked: " + "; ".join(plan.errors))
            prepared = runtime.prepare(
                plan.document,
                action=plan.action,
                passthrough=passthrough,
                session_id=plan.record["session_id"] if plan.record else None,
                legacy_requested=plan.legacy_requested,
            )
            return runtime.perform(prepared)

        return quick_confirm(
            runtime,
            plan,
            input_stream=inp,
            output_stream=tty_output,
            passthrough=passthrough,
            force_line=args.line,
            no_color=args.no_color,
        )
    except (CLIError, sessions.SessionError, state.StateError, catalog.CatalogError, compiler.CompilerError, composition.CompositionError, launch.LaunchError) as exc:
        output.write(f"claude-multi: {tui.visible_message(exc)}\n")
        output.flush()
        return 2
    finally:
        if owned_tty is not None:
            for handle in owned_tty:
                handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
