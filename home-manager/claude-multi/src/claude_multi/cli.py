"""User-facing CLI and quick-confirm flow for claude-multi v2.

Argument parsing, composition persistence, session choice, and terminal UX live
here.  Compilation and execution remain Phase 2 responsibilities and are
called through injectable boundaries.  Merely displaying or editing a plan
never checks gateway readiness, writes session state, or starts Claude.
"""

from __future__ import annotations

import argparse
import copy
import os
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TextIO

from . import (
    catalog,
    compiler,
    composition,
    launch,
    proxy as proxy_mod,
    sessions,
    state,
    strict_json,
    validate as schema_validate,
)
from .editor import EditorError, EditorOutcome, EditorState, run_editor


class CLIError(RuntimeError):
    """Actionable command or interaction failure."""


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
    ) -> PreparedLaunch:
        resolved = self.resolve_document(document)
        if action == "fresh":
            launch_id = self.session_store.new_id()
            session_action = compiler.build_fresh(launch_id)
            forked_from = None
        elif action == "resume":
            if session_id is None:
                raise CLIError("resume requires a managed session ID")
            existing_record = self.session_store.load(session_id)
            launch_id = session_id
            session_action = compiler.build_resume(session_id)
            forked_from = existing_record["forked_from"]
        elif action == "fork":
            if session_id is None:
                raise CLIError("fork requires a managed source session ID")
            launch_id = self.session_store.new_id()
            session_action = compiler.build_fork(
                self.catalog.docs["native-contract"], session_id, launch_id
            )
            forked_from = session_id
        else:
            raise CLIError(f"unknown launch action {action!r}")

        snapshot = composition.snapshot(resolved)
        digest = strict_json.bundle_digest(snapshot)
        result = self.compile_callback(
            docs=self.catalog.docs,
            prompt_bodies=self.catalog.prompt_bodies,
            resolved=resolved,
            session_action=session_action,
            passthrough=passthrough,
            settings_path=self.asset_root / "settings.json",
            lead_prompt_path=compiler.lead_prompt_path(self.session_store.root, digest),
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
    parser.add_argument("--line", action="store_true", help="use the numbered editor instead of curses")
    parser.add_argument("--version", action="version", version="claude-multi 2.0.0")

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

    commands.add_parser("models", help="list trusted catalog models")
    show = commands.add_parser("show", help="show the effective composition summary")
    show.add_argument("show_composition", nargs="?")
    commands.add_parser("doctor", help="run local catalog and loopback readiness checks")
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
    return {
        "version": 1,
        "name": record["composition_name"],
        "description": "Recorded managed-session composition",
        "availability": copy.deepcopy(snap["availability"]),
        "slots": slots,
        "native_agents": copy.deepcopy(snap["native_agents"]),
    }


def _provider_for_lead(runtime: Runtime, document: dict[str, Any]) -> str | None:
    try:
        lead = next(slot for slot in document["slots"] if slot["role"] == catalog.LEAD_ROLE)
        return runtime.catalog.models[lead["model"]]["provider"]
    except (KeyError, StopIteration):
        return None


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
    if (
        record is not None
        and source == "Recorded snapshot"
        and resolved is not None
        and composition.snapshot(resolved) != record["snapshot"]
    ):
        errors.append(
            "recorded resolved selectors or policy differ from the current trusted "
            "catalog; choose the current composition or edit instead of silently changing it"
        )
    if action == "fork":
        status = runtime.catalog.docs["native-contract"]["acceptance"]["fork_triple_flag"]["status"]
        if status != "verified":
            errors.append(compiler.FORK_UNVERIFIED_GUIDANCE)
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
    )


def _current_document_for_record(runtime: Runtime, record: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return runtime.compositions.load(record["composition_name"])
    except CLIError:
        return None


def managed_plan(
    runtime: Runtime,
    record: dict[str, Any],
    *,
    choice: str = "recorded",
    current_override: dict[str, Any] | None = None,
) -> QuickPlan:
    recorded = snapshot_to_document(record)
    current = (
        copy.deepcopy(current_override)
        if current_override is not None
        else _current_document_for_record(runtime, record)
    )
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

    selected = recorded
    source = "Recorded snapshot"
    action = "resume"
    warning = None
    if choice in ("current", "fork", "override"):
        if current is None:
            return build_quick_plan(
                runtime,
                recorded,
                action="resume",
                source="Recorded snapshot",
                record=record,
                current_document=None,
                drift=drift,
                cross_provider_warning="Current saved composition is unavailable.",
            )
        selected = current
        source = "Current saved composition"
        old_provider = _provider_for_lead(runtime, recorded)
        new_provider = _provider_for_lead(runtime, current)
        if old_provider and new_provider and old_provider != new_provider:
            warning = (
                f"Lead provider changes from {runtime.catalog.providers[old_provider]['display']} "
                f"to {runtime.catalog.providers[new_provider]['display']}; hidden reasoning "
                "continuity may be lost. Fork is the default."
            )
            action = "resume" if choice == "override" else "fork"
            if choice == "override":
                warning += " Deliberate resume override selected."
        elif choice == "fork":
            action = "fork"
    return build_quick_plan(
        runtime,
        selected,
        action=action,
        source=source,
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
    lines.append(f"Composition    {plan.document.get('name', '<invalid>')} · {plan.source}")
    if plan.resolved is not None:
        resolved = plan.resolved
        lead_model = runtime.catalog.models[resolved.lead.model]
        provider = runtime.catalog.providers[lead_model["provider"]]
        lines.append(
            f"Lead           {resolved.lead.display} · {provider['display']} · effort {resolved.lead.effort}"
        )
        roles = {variant.role for variant in resolved.variants}
        lines.append(f"Roles          {len(roles)} enabled · {len(resolved.variants)} variants")
        lines.append(f"Providers      {_scope_summary(runtime, plan.document)}")
        native = resolved.native_agents
        lines.append(
            "Native agents  "
            f"Explore {native['explore']} · Plan {native['plan']} · general-purpose {native['general_purpose']}"
        )
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
    lines.append(f"Drift          {'None' if not plan.drift else '; '.join(plan.drift)}")
    if plan.cross_provider_warning:
        lines.extend(("", "WARNING", f"  {plan.cross_provider_warning}"))
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
        lines.append(f"  - {error}")
    if plan.passthrough_error is not None:
        lines.append(f"  - {plan.passthrough_error}")
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
    """Exact quick-confirm actions for Ready and BLOCKED plans."""

    primary = "Enter launch" if plan.ready else "Enter edit"
    if plan.record is not None:
        lines = [
            f"{primary} · R recorded · C current · F fork · E edit · D details · Q cancel"
        ]
        if plan.cross_provider_warning:
            lines.append("O resume current without fork")
        return tuple(lines)
    return (f"{primary} · E edit · D details · Q cancel",)


def validate_quick_passthrough(
    runtime: Runtime, plan: QuickPlan, passthrough: list[str]
) -> None:
    """Apply Phase 2 ownership validation before interactive Ready is shown."""

    lead_mode = compiler.effective_lead_mode(
        runtime.catalog.docs["native-contract"]
    )
    try:
        compiler.validate_passthrough(passthrough, lead_mode=lead_mode)
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


def quick_confirm(
    runtime: Runtime,
    plan: QuickPlan,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    passthrough: list[str],
    force_line: bool,
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
        if plan.record is not None and key == "r":
            plan = managed_plan(runtime, plan.record, choice="recorded")
            continue
        if plan.record is not None and key == "c":
            plan = managed_plan(runtime, plan.record, choice="current")
            continue
        if plan.record is not None and key == "f":
            plan = managed_plan(runtime, plan.record, choice="fork")
            continue
        if plan.record is not None and key == "o" and plan.cross_provider_warning:
            plan = managed_plan(
                runtime,
                plan.record,
                choice="override",
                current_override=plan.current_document,
            )
            continue
        if key == "e" or (key == "" and not plan.ready):
            editor_state = EditorState(
                runtime.catalog.docs,
                plan.document,
                runtime.catalog.default_composition,
                original_document=plan.editor_original_document,
            )
            if plan.source == "Unsaved editor changes" and plan.errors:
                editor_state.message = plan.errors[-1]
            try:
                outcome = run_editor(
                    editor_state,
                    input_stream,
                    output_stream,
                    force_line=force_line,
                )
            except (EditorError, KeyError) as exc:
                plan = _editor_failure_plan(
                    runtime, plan, editor_state.document, exc
                )
                continue
            if outcome is None:
                continue
            try:
                updated = _apply_editor_outcome(runtime, plan, outcome)
            except (CLIError, ValueError, OSError) as exc:
                plan = _editor_failure_plan(
                    runtime, plan, outcome.document, exc
                )
                continue
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
) -> int:
    plan = build_quick_plan(runtime, document, action="fresh", source="Editor")
    while True:
        editor_state = EditorState(
            runtime.catalog.docs,
            plan.document,
            runtime.catalog.default_composition,
            original_document=plan.editor_original_document,
        )
        if plan.source == "Unsaved editor changes" and plan.errors:
            editor_state.message = plan.errors[-1]
        try:
            outcome = run_editor(
                editor_state,
                input_stream,
                output_stream,
                force_line=force_line,
            )
        except (EditorError, KeyError) as exc:
            plan = _editor_failure_plan(
                runtime, plan, editor_state.document, exc
            )
            output_stream.write(f"BLOCKED: {plan.errors[-1]}\n")
            continue
        if outcome is None:
            return 0
        try:
            updated = _apply_editor_outcome(runtime, plan, outcome)
        except (CLIError, ValueError, OSError) as exc:
            plan = _editor_failure_plan(
                runtime, plan, outcome.document, exc
            )
            output_stream.write(f"BLOCKED: {plan.errors[-1]}\n")
            continue
        if updated is not None:
            output_stream.write(
                f"Composition {updated.document['name']!r} ready.\n"
            )
        return 0


def handle_command(
    runtime: Runtime,
    args: argparse.Namespace,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    interactive: bool,
) -> int:
    if args.command == "compose":
        command = args.compose_command
        if command == "list":
            for name in runtime.compositions.names():
                origin = "user" if runtime.compositions.has_user(name) else "trusted seed"
                output_stream.write(f"{name}\t{origin}\n")
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
            output_stream.write(f"{'Deleted' if removed else 'Not found'}: {args.name}\n")
            return 0
        if command in ("duplicate", "use-as-template"):
            runtime.compositions.duplicate(args.source, args.target)
            output_stream.write(f"Created {args.target!r} from {args.source!r}.\n")
            return 0
        if command == "rename":
            runtime.compositions.rename(args.source, args.target)
            output_stream.write(f"Renamed {args.source!r} to {args.target!r}.\n")
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
        )

    if args.command == "sessions":
        command = args.sessions_command
        if command == "list":
            for record in _session_records(runtime):
                output_stream.write(
                    f"{record['session_id']}\t{record['composition_name']}\t{record['created_at']}\t{record['cwd']}\n"
                )
            return 0
        if command == "show":
            record = runtime.session_store.load(args.uuid)
            output_stream.write(strict_json.canonical_file_bytes(record).decode("utf-8"))
            return 0
        if command == "forget":
            removed = runtime.session_store.forget(args.uuid)
            runtime.session_store.clear_last(runtime.cwd, args.uuid)
            output_stream.write(f"{'Forgot' if removed else 'Not found'}: {args.uuid}\n")
            return 0
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
        if runtime.doctor_callback is not None:
            problems.extend(runtime.doctor_callback(runtime))
        else:
            try:
                launch.check_readiness(runtime.catalog.docs["gateway"])
            except launch.LaunchError as exc:
                problems.append(f"local gateway: {exc}")
        if problems:
            output_stream.write("BLOCKED\n")
            for problem in problems:
                output_stream.write(f"  - {problem}\n")
            return 1
        output_stream.write("Ready\nCatalog, compositions, and local gateway are valid.\n")
        return 0

    raise CLIError(f"unsupported command {args.command!r}")


def _open_tty() -> TextIO:
    try:
        return open("/dev/tty", "r+", encoding="utf-8", buffering=1)
    except OSError as exc:
        raise CLIError("no interactive terminal; use --composition NAME for noninteractive launch") from exc


def _validate_uuid(value: str) -> str:
    if not sessions.UUID4.fullmatch(value):
        raise CLIError(f"resume ID {value!r} is not a UUIDv4")
    return value


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
    owned_tty: TextIO | None = None
    try:
        if interactive is None:
            if input_stream is not None:
                interactive = True
            else:
                try:
                    owned_tty = _open_tty()
                    interactive = True
                except CLIError:
                    interactive = False
        inp = input_stream or owned_tty or sys.stdin
        tty_output = output_stream or owned_tty or output

        if args.command is not None:
            if passthrough:
                raise CLIError("passthrough arguments are accepted only for launch")
            return handle_command(
                runtime,
                args,
                input_stream=inp,
                output_stream=tty_output,
                interactive=bool(interactive),
            )

        if not interactive and args.composition is None:
            raise CLIError(
                "noninteractive launch requires --composition NAME; no default was selected"
            )

        if args.resume:
            session_id = _validate_uuid(args.resume)
            record = runtime.session_store.load(session_id)
            override = runtime.compositions.load(args.composition) if args.composition else None
            plan = managed_plan(
                runtime,
                record,
                choice="current" if override is not None else "recorded",
                current_override=override,
            )
        elif args.continue_last:
            session_id = runtime.session_store.last(runtime.cwd)
            if session_id is None:
                raise CLIError(
                    "no managed session is recorded for this directory; use -r UUID or start fresh"
                )
            record = runtime.session_store.load(session_id)
            override = runtime.compositions.load(args.composition) if args.composition else None
            plan = managed_plan(
                runtime,
                record,
                choice="current" if override is not None else "recorded",
                current_override=override,
            )
        else:
            if args.composition is not None:
                document = runtime.compositions.load(args.composition)
                source = "Explicit composition"
            else:
                document, source = remembered_document(runtime)
            plan = build_quick_plan(runtime, document, action="fresh", source=source)

        if not interactive:
            if not plan.ready:
                raise CLIError("composition is blocked: " + "; ".join(plan.errors))
            prepared = runtime.prepare(
                plan.document,
                action=plan.action,
                passthrough=passthrough,
                session_id=plan.record["session_id"] if plan.record else None,
            )
            return runtime.perform(prepared)

        return quick_confirm(
            runtime,
            plan,
            input_stream=inp,
            output_stream=tty_output,
            passthrough=passthrough,
            force_line=args.line,
        )
    except (CLIError, sessions.SessionError, state.StateError, catalog.CatalogError, compiler.CompilerError, composition.CompositionError, launch.LaunchError) as exc:
        output.write(f"claude-multi: {exc}\n")
        output.flush()
        return 2
    finally:
        if owned_tty is not None:
            owned_tty.close()


if __name__ == "__main__":
    raise SystemExit(main())
