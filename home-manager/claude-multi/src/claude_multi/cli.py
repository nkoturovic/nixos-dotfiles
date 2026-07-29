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
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
from . import __version__ as _pkg_version
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
    resolved: composition.ResolvedComposition | None
    document: dict[str, Any]
    model_relaunch: bool = False
    expected_launch_epoch: int | None = None
    expected_mutation_token: str | None = None
    expected_source_scope_generation: int | None = None
    expected_source_composition_hash: str | None = None
    precommitted: bool = False


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
        state.atomic_write(path, strict_json.pretty_file_bytes(candidate))
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
        self.cwd = str(Path.cwd() if cwd is None else Path(cwd).resolve()
        )
        config = sessions.config_root(self.environ)
        state_path = sessions.state_root(self.environ)
        self.broken_override_error: str | None = None
        override_path = config / "native-contract.json"
        try:
            self.catalog = catalog.load_catalog(
                self.asset_root, contract_override=override_path
            )
        except catalog.CatalogError as exc:
            if not os.path.lexists(override_path):
                raise
            # An invalid override is never applied (D29) — but it must not
            # brick every command either. Degrade to the packaged baseline
            # and report: doctor surfaces it, and `claude-multi update`
            # removes the broken file (its unreadable-override branch).
            self.broken_override_error = str(exc)
            self.catalog = catalog.load_catalog(self.asset_root)
        # Single hook-command authority: the stable shim under the state root.
        # Compiled scopes embed the shim's constant path (never a package or
        # store path), so scope bytes survive package rebuilds; the shim is
        # refreshed here so hooks always reach the newest resolved launcher.
        self.resolved_hook_command = scope_mod.resolve_hook_command(
            self.environ, self.asset_root
        )
        self.hook_command = str(
            scope_mod.ensure_hook_shim(state_path, self.resolved_hook_command)
        )
        self.token_helper_command = scope_mod.ensure_token_helper_command(
            state_path, self.environ
        )
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

    def reload_catalog(self) -> None:
        """Re-load the catalog after an operator contract override changed.

        `claude-multi update` writes the override and promotes the checkout;
        without this the running process keeps the stale packaged contract
        (launch would verify against the old pin). Cheap: the catalog is
        small and fully re-validated on load.
        """

        override_path = sessions.config_root(self.environ) / "native-contract.json"
        try:
            self.catalog = catalog.load_catalog(
                self.asset_root,
                contract_override=override_path,
            )
        except catalog.CatalogError as exc:
            if not os.path.lexists(override_path):
                raise
            # Same degradation as __init__: never applied, always reported.
            self.broken_override_error = str(exc)
            self.catalog = catalog.load_catalog(self.asset_root)
        else:
            self.broken_override_error = None

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
        model_relaunch = False
        if action == "fresh":
            launch_id = self.session_store.new_id()
            session_action = compiler.build_fresh(launch_id)
            forked_from = None
        elif action == "resume":
            if session_id is None:
                raise CLIError("resume requires a managed session ID")
            prior_record = self.session_store.resolve(session_id)
            if prior_record["session_type"] != sessions.SESSION_TYPE_MANAGED:
                raise CLIError(
                    "ordinary gateway sessions must be resumed with "
                    "`claude-multi direct --resume` or `claude-gateway --resume`"
                )
            launch_id = sessions.managed_id(prior_record)
            identity_state = prior_record.get(
                "identity_state", sessions.IDENTITY_UNVERIFIED
            )
            if sessions.drop_resolved_pending_forks(prior_record) is not None:
                # A marker the live runtime already resolved self-heals here
                # too (the picker and doctor converge it; review H16) — a
                # stale marker must never block a noninteractive resume.
                self.session_store.converge_pending_forks(launch_id)
                prior_record = self.session_store.load(launch_id)
            if prior_record.get("pending_forks"):
                raise CLIError(pending_fork_message(prior_record))
            if identity_state == sessions.IDENTITY_REPAIR_NEEDED:
                if "observed_cwd" in prior_record or "observed_model" not in prior_record:
                    raise CLIError(sessions.relink_message(prior_record))
                model_relaunch = True
            session_action = compiler.build_resume(
                launch_id, sessions.runtime_session_id(prior_record)
            )
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

        launch_epoch = 1 if prior_record is None else prior_record.get("launch_epoch", 0) + 1
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
            hook_command=self.hook_command,
            launch_epoch=launch_epoch,
            token_helper_command=self.token_helper_command,
        )
        if prior_record is None:
            record = sessions.make_record(
                managed_id=launch_id,
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
                launch_epoch=launch_epoch,
            )
        else:
            record = {
                **prior_record,
                "composition_name": document["name"],
                "composition_hash": strict_json.bundle_digest(snapshot),
                "snapshot": snapshot,
                "mode": mode,
                "scope_generation": generation,
                "launch_epoch": launch_epoch,
                "workflows": resolved.workflows,
                "catalog_version": self.catalog_version,
                "catalog_hash": self.catalog.bundle_sha256,
                "launcher_version": self.launcher_version,
            }
        return PreparedLaunch(
            result,
            record,
            resolved,
            copy.deepcopy(document),
            model_relaunch=model_relaunch,
            expected_launch_epoch=(
                prior_record.get("launch_epoch", 0)
                if prior_record is not None
                else None
            ),
            expected_mutation_token=(
                prior_record.get("mutation_token")
                if prior_record is not None
                else None
            ),
            expected_source_scope_generation=(
                prior_record.get("scope_generation")
                if prior_record is not None
                else None
            ),
            expected_source_composition_hash=(
                prior_record.get("composition_hash")
                if prior_record is not None
                else None
            ),
        )

    def prepare_direct(
        self,
        *,
        action: str,
        model_id: str | None,
        passthrough: list[str],
        session_id: str | None = None,
    ) -> PreparedLaunch:
        """Prepare an ordinary gateway session with no composition semantics."""

        prior: dict[str, Any] | None = None
        pin_model = action == "fresh" or model_id is not None
        model_relaunch = action == "resume" and model_id is not None
        if action == "fresh":
            stable_id = self.session_store.new_id()
            runtime_id = stable_id
            selected_model = model_id or "sol"
            session_action = compiler.build_fresh(stable_id, runtime_id)
        elif action == "resume":
            if session_id is None:
                raise CLIError("direct resume requires a session identifier")
            prior = self.session_store.resolve(session_id)
            if prior["session_type"] != sessions.SESSION_TYPE_ORDINARY:
                raise CLIError(
                    "managed compositions must be resumed with `claude-multi -r`"
                )
            stable_id = sessions.managed_id(prior)
            identity_state = prior.get(
                "identity_state", sessions.IDENTITY_UNVERIFIED
            )
            if sessions.drop_resolved_pending_forks(prior) is not None:
                # Same self-heal as the managed resume guard (review H16).
                self.session_store.converge_pending_forks(stable_id)
                prior = self.session_store.load(stable_id)
            if prior.get("pending_forks"):
                raise CLIError(pending_fork_message(prior))
            if identity_state == sessions.IDENTITY_REPAIR_NEEDED:
                if "observed_cwd" in prior or "observed_model" not in prior:
                    raise CLIError(sessions.relink_message(prior))
                if model_id is None:
                    raise CLIError(
                        f"session {stable_id} observed an unsafe model/profile change; "
                        "explicitly relaunch it with `claude-gateway -r "
                        f"{stable_id} --model {prior['ordinary_model']}`"
                    )
                model_relaunch = True
            runtime_id = sessions.runtime_session_id(prior)
            selected_model = model_id or prior["ordinary_model"]
            session_action = compiler.build_resume(stable_id, runtime_id)
        else:
            raise CLIError(f"unknown direct launch action {action!r}")

        launch_epoch = 1 if prior is None else prior.get("launch_epoch", 0) + 1
        profile = compiler.direct_context_profile(self.catalog.docs, selected_model)
        scope_dir = scope_mod.scope_dir(self.session_store.root, stable_id)
        result = compiler.compile_direct_launch(
            docs=self.catalog.docs,
            session_action=session_action,
            model_id=selected_model,
            passthrough=passthrough,
            scope_dir=scope_dir,
            hook_command=self.hook_command,
            state_root=self.session_store.root,
            pin_model=pin_model,
            launch_epoch=launch_epoch,
            token_helper_command=self.token_helper_command,
        )
        if prior is None:
            record = sessions.make_ordinary_record(
                managed_id=stable_id,
                runtime_session_id=runtime_id,
                cwd=self.cwd,
                model=selected_model,
                context_profile=profile,
                catalog_version=self.catalog_version,
                catalog_hash=self.catalog.bundle_sha256,
                launcher_version=self.launcher_version,
                launch_epoch=launch_epoch,
            )
        else:
            record = {
                **prior,
                "ordinary_model": selected_model,
                "context_profile": profile,
                "mode": "durable",
                "scope_generation": prior.get("scope_generation", 0) + 1,
                "launch_epoch": launch_epoch,
                "catalog_version": self.catalog_version,
                "catalog_hash": self.catalog.bundle_sha256,
                "launcher_version": self.launcher_version,
            }
        return PreparedLaunch(
            result,
            record,
            None,
            {"name": "ordinary-gateway"},
            model_relaunch=model_relaunch,
            expected_launch_epoch=(
                prior.get("launch_epoch", 0) if prior is not None else None
            ),
            expected_mutation_token=(
                prior.get("mutation_token") if prior is not None else None
            ),
            expected_source_scope_generation=(
                prior.get("scope_generation") if prior is not None else None
            ),
        )

    def perform(
        self, prepared: PreparedLaunch, *, resume_decision: str | None = None
    ) -> Any:
        if self.launch_callback is not None:
            return self.launch_callback(prepared)
        self._enforce_resume_gate(prepared, resume_decision)
        return launch.perform_launch(
            prepared.result,
            record=prepared.record,
            store=self.session_store,
            native_contract=self.catalog.docs["native-contract"],
            gateway=self.catalog.docs["gateway"],
            environ=self.environ,
            trusted=self.catalog,
            allow_model_relaunch=prepared.model_relaunch,
            expected_launch_epoch=prepared.expected_launch_epoch,
            expected_mutation_token=prepared.expected_mutation_token,
            expected_source_scope_generation=(
                prepared.expected_source_scope_generation
            ),
            expected_source_composition_hash=(
                prepared.expected_source_composition_hash
            ),
            precommitted=prepared.precommitted,
        )

    def _enforce_resume_gate(
        self, prepared: PreparedLaunch, resume_decision: str | None
    ) -> None:
        """Mandatory resume-gate backstop for every real launch (issue 003).

        Interactive surfaces present the gate as a modal and thread the
        operator's decision; noninteractive paths get the actionable
        refusal here. `force` bypasses ONLY the daemon-owned branch (the
        liveness signal is heuristic); repair-needed and transcript
        problems are never bypassed. Precommitted transition relaunches
        are exempt: the transition flow already warns on live sessions
        and the operator confirmed there (review A3 nuance).
        """

        action = prepared.result.session_action
        if action.kind != "resume" or not prepared.record:
            return
        if prepared.precommitted:
            return
        gate = _evaluate_resume_gate(
            self, prepared.record, live_prefixes=self._live_prefixes()
        )
        if gate.kind == "ok":
            return
        if gate.kind == "daemon-owned" and resume_decision == "force":
            return
        raise CLIError(_resume_gate_refusal(gate))

    def _live_prefixes(self) -> frozenset[str]:
        """Liveness scan seam (tests inject here, never at machine state)."""

        return _live_background_prefixes()


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
    session_group.add_argument("-r", "--resume", nargs="?", const="", metavar="UUID", help="resume a managed session (exact UUID or name; no value opens the sessions picker)")
    parser.add_argument("--force", action="store_true", help="resume despite a background-liveness marker (only bypasses the heuristic ● check; identity/transcript guards still apply)")
    parser.add_argument("--line", action="store_true", help="force the line-based UI (no full-screen curses interface)")
    parser.add_argument("--no-color", action="store_true", help="disable all color output (the NO_COLOR environment variable is also honored)")
    parser.add_argument("--legacy", action="store_true", help="launch with the pre-durable argv form (compatibility hatch; agents may vanish on supervisor restart)")
    parser.add_argument("--print-launch", action="store_true", help="print the exact Claude argv and env summary instead of launching (the gateway token is never shown)")
    parser.add_argument("--version", action="version", version=f"claude-multi {_pkg_version}")

    commands = parser.add_subparsers(dest="command")
    direct_parser = commands.add_parser(
        "direct", help="launch an ordinary gateway session without a composition"
    )
    direct_parser.add_argument("--model", dest="direct_model")
    direct_parser.add_argument("--force", action="store_true", help="resume despite a background-liveness marker (only bypasses the heuristic ● check)")
    direct_parser.add_argument("--print-launch", action="store_true")
    direct_identity = direct_parser.add_mutually_exclusive_group()
    direct_identity.add_argument(
        "-c", "--continue", dest="direct_continue", action="store_true"
    )
    direct_identity.add_argument(
        "-r", "--resume", dest="direct_resume", metavar="UUID"
    )

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
    link = session_commands.add_parser(
        "link", help="adopt a native session (must exist in local Claude metadata)"
    )
    link.add_argument("uuid", nargs="?")
    link_target = link.add_mutually_exclusive_group()
    link_target.add_argument("--composition", dest="link_composition")
    link_target.add_argument("--model", dest="link_model")
    link.add_argument(
        "--cwd",
        dest="link_cwd",
        help="authoritative original project directory (validated against metadata)",
    )
    relink = session_commands.add_parser(
        "relink-runtime",
        help="repair a managed record with the authoritative native runtime UUID",
    )
    relink.add_argument("uuid", help="stable managed ID (or an existing alias)")
    relink.add_argument("runtime_uuid", help="UUID shown by native /status or /resume")
    relink.add_argument(
        "--cwd", dest="repair_cwd", help="also replace the recorded original project CWD"
    )
    resolve_fork = session_commands.add_parser(
        "resolve-fork",
        help="discard a pending native-fork marker (the fork transcript is kept)",
    )
    resolve_fork.add_argument("uuid", help="stable managed ID of the parent session")
    resolve_fork.add_argument(
        "fork_uuid", help="runtime UUID of the native fork to stop tracking"
    )
    stop = session_commands.add_parser(
        "stop",
        help="stop a live background-owned session (upstream `claude stop`; "
        "the conversation is always kept)",
    )
    stop.add_argument("uuid", help="stable managed ID (or an existing alias)")
    stop.add_argument(
        "--yes",
        action="store_true",
        help="skip the interactive confirmation (required non-interactively)",
    )
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

    event_parser = commands.add_parser("session-event", help=argparse.SUPPRESS)
    event_parser.add_argument("event", choices=("start", "end"))
    event_parser.add_argument("--managed-id", required=True)
    event_parser.add_argument("--launch-epoch", type=int, default=None)

    commands.add_parser("models", help="list trusted catalog models")
    update_parser = commands.add_parser(
        "update",
        help="evidence-gated re-pin of the managed Claude binary (inspect, "
        "offline evidence suite, promote, optionally activate)",
    )
    update_parser.add_argument(
        "--activate",
        action="store_true",
        help="run `home-manager switch` after the evidence passes",
    )
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
        "--repair-all",
        action="store_true",
        dest="doctor_repair_all",
        help="converge every durable session's scope (and refresh its record "
        "snapshot) against the installed catalog",
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


def pending_fork_message(record: dict[str, Any]) -> str:
    """Alias kept next to the card/guard call sites (canonical: sessions)."""

    return sessions.pending_fork_message(record)


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
    if record is not None and action == "resume":
        # The operator is resuming: a marker the live runtime already
        # resolved self-heals at this action path (review H16) instead of
        # blocking the plan with a stale record. Display-only paths (the
        # picker list, doctor) keep the marker visible until acted on.
        stable_id = sessions.managed_id(record)
        if sessions.drop_resolved_pending_forks(record) is not None:
            runtime.session_store.converge_pending_forks(stable_id)
            record = runtime.session_store.load(stable_id)
    if record is not None:
        stable_id = sessions.managed_id(record)
        identity_state = record.get("identity_state", sessions.IDENTITY_UNVERIFIED)
        if record.get("pending_forks"):
            errors.append(pending_fork_message(record))
        elif identity_state == sessions.IDENTITY_REPAIR_NEEDED and (
            "observed_cwd" in record or "observed_model" not in record
        ):
            errors.append(sessions.relink_message(record))
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
    plan_cwd = record["cwd"] if record is not None else runtime.cwd
    project_files = _project_agent_files(plan_cwd)
    project_collisions: list[str] = []
    if resolved is not None:
        generated = {variant.id for variant in resolved.variants}
        project_collisions = [
            _collision_error(path, name, plan_cwd)
            for path, name in scope_mod.find_cm_collisions(plan_cwd, [], generated)
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
                uuid=sessions.managed_id(record),
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
            if record["session_type"] == sessions.SESSION_TYPE_MANAGED:
                return (
                    runtime.compositions.load(record["composition_name"]),
                    "Last used in this directory",
                )
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


def _cwd_sessions_summary(runtime: Runtime) -> str | None:
    """One-line hint of managed sessions recorded for this cwd (card line)."""

    here = [
        record
        for record in _session_records(runtime)
        if record["cwd"] == runtime.cwd
    ]
    if not here:
        return None
    newest = max(here, key=lambda record: record["created_at"])
    noun = "session" if len(here) == 1 else "sessions"
    return f"{len(here)} {noun} here · newest {_record_age(newest)} · S to pick"


def render_quick_confirm(
    runtime: Runtime,
    plan: QuickPlan,
    *,
    details: bool = False,
    width: int = 100,
    update_hint: tuple[str, str] | None = None,
) -> str:
    lines = ["claude-multi", ""]
    action = plan.action.title()
    if plan.record is not None:
        action += f" · {sessions.managed_id(plan.record)}"
    lines.append(f"Action         {action}")
    if update_hint is not None:
        pinned, available = update_hint
        lines.append(
            f"Update         Claude {available} available · pinned {pinned} · U to update"
        )
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
    cwd_hint = _cwd_sessions_summary(runtime)
    if cwd_hint is not None:
        lines.append(f"Sessions       {cwd_hint}")
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
                f"Change         {RESUME_RECORDED_NOTE.format(uuid=sessions.managed_id(plan.record))}"
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


def _toggle_workflows(runtime: Runtime, plan: QuickPlan) -> QuickPlan:
    """Flip workflows native<->off on a fresh plan (the `w` key).

    Same spirit as preset cycling: the composition document is updated in
    memory and the plan rebuilt — the editor stays the place for structural
    edits. Managed plans stay recorded-only (R1 P1). ``native`` removes the
    key so documents stay byte-minimal (mirrors composition.snapshot).
    """

    if plan.record is not None or plan.action != "fresh":
        return plan
    document = copy.deepcopy(plan.document)
    current = document.get("workflows", "native")
    if current == "off":
        document.pop("workflows", None)
    else:
        document["workflows"] = "off"
    toggled = build_quick_plan(
        runtime,
        document,
        action="fresh",
        source=plan.source,
    )
    toggled.legacy_requested = plan.legacy_requested
    return toggled


def _cycle_preset(
    runtime: Runtime, plan: QuickPlan, delta: int
) -> QuickPlan:
    """Cycle the quick-confirm through saved composition presets.

    Fresh plans only: managed (record-bearing) plans stay recorded-only —
    composition changes there belong to the transition engine (R1 P1). The
    rebuild keeps ``legacy_requested`` and marks the source with the cycle
    position so the card shows where in the preset list you are.
    """

    if plan.record is not None or plan.action != "fresh":
        return plan
    names = runtime.compositions.names()
    if len(names) < 2:
        return plan
    current = plan.document.get("name")
    try:
        index = names.index(current)
    except ValueError:
        index = -1 if delta > 0 else 0
    index = (index + delta) % len(names)
    document = runtime.compositions.load(names[index])
    cycled = build_quick_plan(
        runtime,
        document,
        action="fresh",
        source=f"Selected preset {index + 1}/{len(names)}",
    )
    cycled.legacy_requested = plan.legacy_requested
    return cycled


def _cycle_key_delta(key_kind: str) -> int | None:
    """tab/right cycle forward, btab/left cycle backward; else None."""

    if key_kind in ("tab", "right"):
        return 1
    if key_kind in ("btab", "left"):
        return -1
    return None


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


def quick_footer(plan: QuickPlan, *, update_hint: tuple[str, str] | None = None) -> tuple[str, ...]:
    """Exact quick-confirm actions for Ready and BLOCKED plans.

    R1 P1: managed plans offer no editing and no recorded/current
    switching; resume is recorded-only and the transition engine owns
    composition changes (Enter on BLOCKED shows the transition path).
    """

    update = " · U update" if update_hint is not None else ""
    if plan.record is not None:
        primary = "Enter launch" if plan.ready else "Enter transition hint"
        return (f"{primary} · D details · S sessions · ? workflows{update} · Q cancel",)
    primary = "Enter launch" if plan.ready else "Enter edit"
    return (f"{primary} · E edit · D details · S sessions · ? workflows · P preset · W wf on/off{update} · Q cancel",)


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
            + RESUME_RECORDED_NOTE.format(uuid=sessions.managed_id(plan.record))
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


QUICK_HELP = (
    "Enter — launch this composition (durable scope).\n"
    "E — edit the composition (form editor; ^G opens the JSON editor there).\n"
    "Tab / Shift-Tab — cycle composition presets.\n"
    "W — toggle native workflows on/off for this launch.\n"
    "D — details (scalar, providers, catalog hashes, workers).\n"
    "S — sessions: managed + native picker (resume, switch comp, adopt).\n"
    "P (line mode) — cycle presets.\n"
    "? — this help, then the workflow guarantees below.\n"
    "Esc — cancel (everywhere; in text fields Esc is also the way out).\n"
    "\n"
    "-- workflow guarantees --------------------------------------------"
)


class _QuickConfirmScreen:
    """Curses quick-confirm: composition card + KeyBar (UX section 1).

    Same flow semantics as the line loop: Enter launches a Ready plan (or
    opens the editor when BLOCKED), E edits, D toggles details, ? shows the
    guarantee panel as a Modal, Esc cancels.  Managed plans are
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
        update_hint: tuple[str, str] | None = None,
        gateway_problem: str | None = None,
        gateway_checked: bool = False,
        gateway_check: Any | None = None,
        upgrade_runner: Any | None = None,
        hint_detector: Any | None = None,
        tty_in: Any | None = None,
        tty_out: Any | None = None,
    ):
        self.runtime = runtime
        self.plan = plan
        self.passthrough = passthrough
        self.palette = palette
        self.details = False
        self.update_hint = update_hint
        self.gateway_problem = gateway_problem
        self.gateway_checked = gateway_checked
        self.gateway_check = gateway_check
        self.upgrade_runner = upgrade_runner
        self.hint_detector = (
            hint_detector if hint_detector is not None else launch.repin_hint
        )
        self.tty_in = tty_in if tty_in is not None else sys.stdin
        self.tty_out = tty_out if tty_out is not None else sys.stdout

    def _gateway_status(self) -> str | None:
        """Loopback gateway problem line, or None when healthy."""

        if self.gateway_check is not None:
            return self.gateway_check()
        try:
            launch.check_readiness(self.runtime.catalog.docs["gateway"])
        except launch.LaunchError as exc:
            return str(exc)
        except Exception as exc:  # never let a health hint break the card
            return f"gateway check failed: {exc}"
        return None

    def _refresh_health(self) -> None:
        """Recompute pin/gateway state (after an in-TUI update)."""

        self.runtime.reload_catalog()
        self.update_hint = self.hint_detector(
            self.runtime.catalog.docs["native-contract"]
        )
        self.gateway_problem = self._gateway_status()
        self.gateway_checked = True

    # -- drawing -----------------------------------------------------------

    def _draw(self, win: Any) -> None:
        win.erase()
        palette = self.palette
        height, width = win.getmaxyx()
        plan = self.plan
        runtime = self.runtime
        keybar = self._keybar()
        # Reserve the keybar zone plus the Status block up front: optional
        # rows drop first at small sizes; Status and the exit binding are
        # never sacrificed (hardening review H3).
        bar_rows = keybar.rows(width)
        bottom = height - bar_rows
        status_reserve = 2  # blank line + Status badge
        row = 1
        tui.safe_add(win, row, 2, "claude-multi", palette.attr("accent") | curses.A_BOLD)
        action = plan.action.title()
        if plan.record is not None:
            action += f" · {sessions.managed_id(plan.record)[:8]}…"
        tui.safe_add(
            win,
            row,
            15,
            f"— composition: {plan.document.get('name', '<invalid>')} · {plan.source} · {action}",
            palette.attr("normal"),
        )
        row += 1
        tui.safe_add(win, row, 2, "─" * min(width - 1, 60), palette.attr("dim"))
        row += 1
        if plan.resolved is not None:
            resolved = plan.resolved
            tui.safe_add(win, row, 2, "lead      ", palette.attr("dim"))
            lead_text = f"{resolved.lead.display} · effort {resolved.lead.effort}"
            tui.safe_add(win, row, 12, lead_text)
            tui.Badge(f"workflows: {resolved.workflows}", "accent").draw(
                win, row, 12 + len(lead_text) + 3, palette
            )
            row += 1
            roles = {variant.role for variant in resolved.variants}
            tui.safe_add(win, row, 2, "agents    ", palette.attr("dim"))
            tui.safe_add(
                win, row, 12, f"{len(resolved.variants)} selected · {len(roles)} roles · "
            )
            tui.Badge(_durability_badge(plan), "ok").draw(
                win, row, 12 + len(f"{len(resolved.variants)} selected · {len(roles)} roles · "), palette
            )
            row += 1
            table = tui.Table(
                ["role", "model", "lane", ""],
                [
                    [
                        _role_label(variant.role),
                        variant.display,
                        variant.lane,
                        ("★ preferred" if variant.preferred else "")
                        + ("  (worktree)" if variant.isolation else ""),
                    ]
                    for variant in resolved.variants
                ],
                selected=-1,
            )
            max_rows = max(1, bottom - row - status_reserve - (1 if plan.errors else 0))
            row += table.draw(win, row, 2, width, palette, max_rows=max_rows)
        else:
            has_lead = any(
                slot.get("role") == catalog.LEAD_ROLE
                for slot in plan.document.get("slots", [])
                if isinstance(slot, dict)
            )
            tui.safe_add(win, row, 2, "lead      ", palette.attr("dim"))
            tui.safe_add(
                win, row, 12, "unresolved" if has_lead else "none selected", palette.attr("error")
            )
            row += 1
            tui.safe_add(win, row, 2, "agents    ", palette.attr("dim"))
            tui.Badge(_durability_badge(plan), "ok").draw(win, row, 12, palette)
            row += 1
        if plan.resolved is not None and row < bottom - status_reserve:
            tui.safe_add(win, row, 2, "policy    ", palette.attr("dim"))
            tui.safe_add(win, row, 12, _policy_summary(runtime, plan.resolved))
            row += 1
        if row < bottom - status_reserve:
            tui.safe_add(win, row, 2, "project   ", palette.attr("dim"))
            project_role = "error" if plan.project_collisions else "normal"
            tui.safe_add(win, row, 12, _project_summary(plan), palette.attr(project_role))
            row += 1
        cwd_hint = _cwd_sessions_summary(runtime)
        if cwd_hint is not None and row < bottom - status_reserve:
            tui.safe_add(win, row, 2, "sessions  ", palette.attr("dim"))
            tui.safe_add(win, row, 12, cwd_hint, palette.attr("accent"))
            row += 1
        if plan.cross_provider_warning and row < bottom - status_reserve:
            tui.safe_add(win, row, 2, "warning   ", palette.attr("warn"))
            tui.safe_add(win, row, 12, plan.cross_provider_warning, palette.attr("warn"))
            row += 1
        if self.gateway_problem is not None and row < bottom - status_reserve:
            tui.safe_add(win, row, 2, "health    ", palette.attr("dim"))
            tui.safe_add(
                win, row, 12,
                f"gateway unreachable — launches will fail: {self.gateway_problem}",
                palette.attr("error"),
            )
            row += 1
        elif self.gateway_checked and row < bottom - status_reserve:
            pin = runtime.catalog.docs["native-contract"]["claude"]["validated_version"]
            note = (
                " (operator override)"
                if runtime.catalog.contract_source == "override"
                else ""
            )
            tui.safe_add(win, row, 2, "health    ", palette.attr("dim"))
            tui.safe_add(win, row, 12, f"gateway ok · pin {pin}{note}", palette.attr("dim"))
            row += 1
        if self.update_hint is not None and row < bottom - status_reserve:
            pinned, available = self.update_hint
            tui.safe_add(win, row, 2, "update    ", palette.attr("warn"))
            tui.safe_add(
                win, row, 12,
                f"Claude {available} available · pinned {pinned} · press U to update",
                palette.attr("warn"),
            )
            row += 1
        if self.details and row < bottom - status_reserve:
            row += self._draw_details(win, row, width, bottom - status_reserve)
        # Status after content (one blank when possible), never below the
        # reserved slot; error lines flow under it and clip at the keybar.
        row = min(row + (0 if plan.errors else 1), bottom - status_reserve)
        if plan.ready:
            tui.Badge("Status  Ready", "ok").draw(win, row, 2, palette)
        else:
            tui.Badge("Status  BLOCKED", "error").draw(win, row, 2, palette)
        row += 1
        errors = [
            *plan.errors,
            *([plan.passthrough_error] if plan.passthrough_error else []),
        ]
        for error in errors:
            wrapped = textwrap.wrap(
                f"- {error}",
                width=max(32, width - 6),
                subsequent_indent="  ",
                break_long_words=False,
                break_on_hyphens=False,
            ) or ["-"]
            for line in wrapped:
                if row >= bottom:
                    break
                tui.safe_add(win, row, 4, line, palette.attr("error"))
                row += 1
        keybar.draw(win, height - 1, palette)
        win.refresh()

    def _draw_details(self, win: Any, row: int, width: int, limit: int) -> int:
        plan = self.plan
        runtime = self.runtime
        start = row
        palette = self.palette
        if row >= limit:
            return 0
        if plan.resolved is not None:
            scalar = (
                "unset"
                if plan.resolved.scalar_context_tokens is None
                else f"{plan.resolved.scalar_context_tokens:,}"
            )
            tui.safe_add(win, row, 2, "scalar    ", palette.attr("dim"))
            tui.safe_add(win, row, 12, f"bound {scalar}")
            row += 1
            tui.safe_add(win, row, 2, "providers ", palette.attr("dim"))
            tui.safe_add(win, row, 12, _scope_summary(runtime, plan.document))
            row += 1
        if plan.record is not None:
            catalog_state = (
                "changed"
                if plan.record["catalog_hash"] != runtime.catalog.bundle_sha256
                else "same"
            )
            tui.safe_add(win, row, 2, "catalog   ", palette.attr("dim"))
            tui.safe_add(
                win,
                row,
                12,
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
            tui.safe_add(win, row, 2, "recorded  ", palette.attr("dim"))
            tui.safe_add(
                win,
                row,
                12,
                f"composition hash {composition_state} · recorded "
                f"{plan.record['composition_hash'][:18]}… · current {str(current_hash)[:18]}",
            )
            row += 1
            if composition_state == "changed":
                # R1 P1: transitions own composition changes; say so where
                # the old "C current" choice used to be advertised.
                tui.safe_add(win, row, 2, "change    ", palette.attr("dim"))
                tui.safe_add(
                    win,
                    row,
                    12,
                    RESUME_RECORDED_NOTE.format(uuid=sessions.managed_id(plan.record)),
                )
                row += 1
            tui.safe_add(win, row, 2, "live      ", palette.attr("dim"))
            tui.safe_add(
                win,
                row,
                12,
                "native temporary changes are not inspected; the chosen composition is reasserted.",
            )
            row += 1
            tui.safe_add(win, row, 2, "workers   ", palette.attr("dim"))
            tui.safe_add(
                win,
                row,
                12,
                "existing workers, if any, keep their original model and tools; fork for strict separation.",
            )
            row += 1
            if plan.action == "resume" and plan.record["mode"] != "durable":
                tui.safe_add(win, row, 2, "note      ", palette.attr("warn"))
                tui.safe_add(win, row, 12, LEGACY_RESUME_NOTE, palette.attr("warn"))
                row += 1
        if row >= limit:
            return row - start
        tui.safe_add(win, row, 2, "drift     ", palette.attr("dim"))
        drift = "None" if not plan.drift else "; ".join(plan.drift)
        tui.safe_add(win, row, 12, drift)
        row += 1
        if plan.resolved is not None and row < limit:
            tui.safe_add(win, row, 2, "availability", palette.attr("accent"))
            row += 1
            for model_id, model in sorted(runtime.catalog.models.items()):
                if row >= limit:
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
        # plans; resume is recorded-only. Preset cycling is a fresh-plan
        # affordance only.
        if self.plan.record is not None:
            primary = (
                ("Enter", "launch") if self.plan.ready else ("Enter", "transition hint")
            )
            bindings: list[tuple[str, str]] = [primary]
        else:
            primary = ("Enter", "launch") if self.plan.ready else ("Enter", "edit")
            bindings = [primary, ("E", "edit")]
            if len(self.runtime.compositions.names()) > 1:
                bindings.append(("Tab", "preset"))
            bindings.append(("W", "wf on/off"))
        if self.update_hint is not None:
            bindings.append(("U", "update"))
        bindings.extend((("D", "details"), ("S", "sessions"), ("?", "help"), ("H", "health"), ("Esc", "cancel")))
        return tui.KeyBar(bindings)

    # -- health / update actions ----------------------------------------------

    def _pause_for_lines(self, win: Any, header: str) -> None:
        with tui.suspended_curses(win):
            self.tty_out.write(header + "\n")

    def _resume_note(self, win: Any) -> None:
        with tui.suspended_curses(win):
            self.tty_out.write("\nPress Enter to return to claude-multi.")
            self.tty_out.flush()
            try:
                self.tty_in.readline()
            except KeyboardInterrupt:
                pass

    def _run_update(self, win: Any) -> None:
        from . import upgrade as upgrade_mod

        runner = self.upgrade_runner
        environ_repo = self.runtime.environ.get("CLAUDE_MULTI_SOURCE_REPO")
        source_repo = Path(
            environ_repo
            or (Path(self.runtime.environ.get("HOME", str(Path.home()))) / "personal" / "nixos-dotfiles")
        )
        failed = False
        # One suspended block for the whole flow: curses must be released
        # BEFORE the minutes-long evidence suite runs, and every phase line
        # is written + flushed as it happens so the run never looks frozen.
        with tui.suspended_curses(win):
            self.tty_out.write("claude-multi update — evidence-gated re-pin:\n")
            self.tty_out.flush()

            def _progress(line: str) -> None:
                self.tty_out.write(f"  {tui.visible_text(line)}\n")
                self.tty_out.flush()

            messages: list[str]
            post_override = False
            try:
                if runner is not None:
                    messages = list(runner())
                else:
                    outcome = upgrade_mod.run_upgrade(
                        checkout_root=source_repo / "home-manager" / "claude-multi",
                        native_contract=self.runtime.catalog.docs["native-contract"],
                        override_path=sessions.config_root(self.runtime.environ) / "native-contract.json",
                        today=sessions._now()[:10],
                        progress=_progress,
                        packaged_contract=_packaged_contract(self.runtime),
                        override_broken=self.runtime.broken_override_error is not None,
                    )
                    messages = list(outcome.messages)
            except KeyboardInterrupt:
                failed = True
                messages = [
                    "update interrupted (Ctrl-C) — check `claude-multi doctor` "
                    "for the effective pin state"
                ]
            except Exception as exc:
                failed = True
                post_override = getattr(exc, "post_override", False)
                messages = [f"update failed: {exc}"]
            for line in messages:
                self.tty_out.write(f"  {tui.visible_text(line)}\n")
            if failed and not post_override:
                self.tty_out.write("Nothing was promoted; the pin is unchanged.\n")
            self.tty_out.flush()
        if not failed:
            self._refresh_health()
        self._resume_note(win)

    def _run_health(self, win: Any) -> None:
        self._pause_for_lines(win, "claude-multi doctor:")
        problems, info_lines, attention = _collect_doctor_reports(self.runtime)
        with tui.suspended_curses(win):
            if problems:
                self.tty_out.write("BLOCKED\n")
                for line in problems:
                    self.tty_out.write(f"  - {tui.visible_text(line)}\n")
            else:
                self.tty_out.write("Ready\n")
            if attention:
                self.tty_out.write("Attention\n")
                for line in attention:
                    self.tty_out.write(f"  - {tui.visible_text(line)}\n")
            for line in info_lines:
                self.tty_out.write(f"{tui.visible_text(line)}\n")
            if problems:
                self.tty_out.write("\nRun `doctor --repair-all` now? [y/N] ")
                self.tty_out.flush()
                try:
                    answer = self.tty_in.readline().strip().lower()
                except KeyboardInterrupt:
                    answer = ""
                if answer in ("y", "yes"):
                    code = _doctor_repair_all(self.runtime, self.tty_out)
                    self.tty_out.write(f"(repair-all exit {code})\n")
        self._refresh_health()
        self._resume_note(win)

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
            decision = result[2] if len(result) > 2 else None
            plan = managed_plan(self.runtime, record)
            if not plan.ready:
                self.plan.errors.extend(plan.errors)
                return None
            prepared = self.runtime.prepare(
                plan.document,
                action="resume",
                passthrough=self.passthrough,
                session_id=sessions.managed_id(record),
                legacy_requested=self.plan.legacy_requested,
            )
            return ("perform", prepared, decision)
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
                RESUME_RECORDED_NOTE.format(uuid=sessions.managed_id(self.plan.record)),
                width=60,
            ),
            buttons=(("Close", True),),
        ).run(win, self.palette, background=self._draw)

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
                tui.safe_add(win, 2, 0, "Resize, or press Esc to cancel.")
                win.refresh()
                key = tui.read_key(win)
                if key.kind == "esc":
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
            if key.kind == "esc":
                return None
            if key.kind == "char" and key.ch.lower() == "d":
                self.details = not self.details
                continue
            if key.kind == "char" and key.ch.lower() == "s":
                outcome = self._open_sessions(win)
                if outcome is None:
                    continue
                return outcome
            preset_delta = _cycle_key_delta(key.kind)
            if preset_delta is not None:
                self.plan = _cycle_preset(self.runtime, self.plan, preset_delta)
                continue
            if key.kind == "char" and key.ch.lower() == "w":
                self.plan = _toggle_workflows(self.runtime, self.plan)
                continue
            if key.kind == "char" and key.ch.lower() == "u" and self.update_hint is not None:
                self._run_update(win)
                continue
            if key.kind == "char" and key.ch.lower() == "h":
                self._run_health(win)
                continue
            if key.kind == "char" and key.ch == "?":
                mode = (
                    self.plan.resolved.workflows
                    if self.plan.resolved is not None
                    else self.plan.document.get("workflows", "native")
                )
                tui.Modal(
                    "quick-confirm — help",
                    QUICK_HELP.splitlines()
                    + workflow_guarantee_panel(mode).splitlines(),
                    buttons=(("Close", True),),
                ).run(win, self.palette, background=self._draw)
                continue
            if (
                self.plan.record is not None
                and key.kind == "char"
                and key.ch in ("r", "c")
            ):
                self._recorded_only_modal(win)
                continue
            if (key.kind == "char" and key.ch.lower() == "e") or (
                key.kind == "enter" and not self.plan.ready
            ):
                if self.plan.record is not None:
                    self._recorded_only_modal(win)
                    continue
                if self._edit(win) == "exit":
                    return None
                continue
            if key.kind == "enter" and self.plan.ready:
                decision: str | None = None
                if self.plan.record is not None and self.plan.action == "resume":
                    gate = _evaluate_resume_gate(self.runtime, self.plan.record)
                    if gate.kind != "ok":
                        try:
                            resolved_gate = _run_resume_gate_modal(
                                self.runtime,
                                self.plan.record,
                                gate,
                                win,
                                self.palette,
                                background=self._draw,
                            )
                        except (CLIError, sessions.SessionError) as exc:
                            self.plan.errors.append(str(exc))
                            continue
                        if resolved_gate is None:
                            continue
                        _, record, decision = resolved_gate
                        self.plan.record = record
                try:
                    prepared = self.runtime.prepare(
                        self.plan.document,
                        action=self.plan.action,
                        passthrough=self.passthrough,
                        session_id=(
                            sessions.managed_id(self.plan.record) if self.plan.record else None
                        ),
                        legacy_requested=self.plan.legacy_requested,
                    )
                except (ValueError, CLIError) as exc:
                    self.plan.errors.append(str(exc))
                    continue
                return ("perform", prepared, decision)


def _curses_quick_confirm(
    runtime: Runtime,
    plan: QuickPlan,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    passthrough: list[str],
    no_color: bool,
    update_hint: tuple[str, str] | None = None,
    gateway_problem: str | None = None,
    gateway_checked: bool = False,
) -> Any:
    palette = tui.detect_palette(
        no_color=no_color, tty_in=input_stream, tty_out=output_stream
    )
    screen = _QuickConfirmScreen(
        runtime,
        plan,
        passthrough=passthrough,
        palette=palette,
        update_hint=update_hint,
        gateway_problem=gateway_problem,
        gateway_checked=gateway_checked,
    )
    result = tui.run_curses_on_streams(
        screen.run, input_stream, output_stream, palette=palette
    )
    if result is None:
        return 0
    if result[0] == "transition":
        _, record, name = result
        namespace = argparse.Namespace(
            uuid=sessions.managed_id(record),
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
    _action, prepared, *rest = result
    decision = rest[0] if rest else None
    return runtime.perform(prepared, resume_decision=decision)


def quick_confirm(
    runtime: Runtime,
    plan: QuickPlan,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    passthrough: list[str],
    force_line: bool,
    no_color: bool = False,
    update_hint: tuple[str, str] | None = None,
    gateway_problem: str | None = None,
    gateway_checked: bool = False,
    resume_decision: str | None = None,
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
                update_hint=update_hint,
                gateway_problem=gateway_problem,
                gateway_checked=gateway_checked,
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
        update_hint=update_hint,
        resume_decision=resume_decision,
    )


def _line_quick_confirm(
    runtime: Runtime,
    plan: QuickPlan,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    passthrough: list[str],
    no_color: bool = False,
    update_hint: tuple[str, str] | None = None,
    resume_decision: str | None = None,
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
                update_hint=update_hint,
            )
        )
        for footer_line in quick_footer(plan, update_hint=update_hint):
            output_stream.write(footer_line + "\n")
        output_stream.write("> ")
        output_stream.flush()
        key = _read_key(input_stream)
        if key is None or key in ("q", "quit", "cancel"):
            return 0
        if key == "u" and update_hint is not None:
            from . import upgrade as upgrade_mod

            environ_repo = runtime.environ.get("CLAUDE_MULTI_SOURCE_REPO")
            source_repo = Path(
                environ_repo
                or (Path(runtime.environ.get("HOME", str(Path.home()))) / "personal" / "nixos-dotfiles")
            )
            try:
                outcome = upgrade_mod.run_upgrade(
                    checkout_root=source_repo / "home-manager" / "claude-multi",
                    native_contract=runtime.catalog.docs["native-contract"],
                    override_path=sessions.config_root(runtime.environ) / "native-contract.json",
                    today=sessions._now()[:10],
                    packaged_contract=_packaged_contract(runtime),
                override_broken=runtime.broken_override_error is not None,
                    progress=lambda line: (
                        output_stream.write(f"… {tui.visible_text(line)}\n"),
                        output_stream.flush(),
                    ),
                )
                for line in outcome.messages:
                    output_stream.write(f"{tui.visible_text(line)}\n")
                runtime.reload_catalog()
                update_hint = launch.repin_hint(runtime.catalog.docs["native-contract"])
            except upgrade_mod.UpgradeError as exc:
                output_stream.write(f"update failed: {tui.visible_message(exc)}\n")
            except KeyboardInterrupt:
                output_stream.write(
                    "update interrupted (Ctrl-C) — check `claude-multi doctor` "
                    "for the effective pin state\n"
                )
            except OSError as exc:
                output_stream.write(f"update failed: {tui.visible_message(exc)}\n")
            continue
        if key == "d":
            details = not details
            continue
        if key == "?":
            mode = (
                plan.resolved.workflows
                if plan.resolved is not None
                else plan.document.get("workflows", "native")
            )
            output_stream.write(
                QUICK_HELP + workflow_guarantee_panel(mode) + "\n"
            )
            continue
        if key == "s":
            _print_sessions_listing(runtime, output_stream)
            output_stream.write(
                "resume with `claude-multi -r <uuid>` (or a name), or press S "
                "in the curses UI to pick interactively.\n"
            )
            continue
        if key in ("p", "P"):
            cycled = _cycle_preset(runtime, plan, 1 if key == "p" else -1)
            if cycled is plan:
                output_stream.write(
                    "preset cycling needs a fresh plan and at least two saved "
                    "compositions (`claude-multi compose list`).\n"
                )
                continue
            plan = cycled
            continue
        if key == "w":
            toggled = _toggle_workflows(runtime, plan)
            if toggled is plan:
                output_stream.write(
                    "workflow toggle applies to fresh plans; a managed resume "
                    "keeps the recorded composition.\n"
                )
                continue
            plan = toggled
            continue
        if plan.record is not None and key in ("r", "c"):
            # R1 P1: the recorded/current switch is gone; resume always uses
            # the recorded composition. Name the one path to change it.
            output_stream.write(
                RESUME_RECORDED_NOTE.format(uuid=sessions.managed_id(plan.record)) + "\n"
            )
            continue
        if key == "e" or (key == "" and not plan.ready):
            if plan.record is not None:
                # R1 P1: managed plans never edit the launchable intent; the
                # editor on a managed resume was the same override vector.
                output_stream.write(
                    RESUME_RECORDED_NOTE.format(uuid=sessions.managed_id(plan.record)) + "\n"
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
            session_id = sessions.managed_id(plan.record) if plan.record else None
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
            return runtime.perform(prepared, resume_decision=resume_decision)


def _session_record_scan(
    runtime: Runtime,
) -> tuple[list[dict[str, Any]], list[str], set[str]]:
    """Readable records, load problems, and every UUID-shaped record key."""

    records: list[dict[str, Any]] = []
    problems: list[str] = []
    record_ids: set[str] = set()
    for path in sorted(runtime.session_store.sessions_dir.glob("*.json")):
        session_id = path.stem
        if not sessions.UUID4.fullmatch(session_id):
            continue
        record_ids.add(session_id)
        try:
            records.append(runtime.session_store.load(session_id))
        except sessions.SessionError as exc:
            problems.append(f"session record {session_id} is unreadable: {exc}")
    return records, problems, record_ids


def _session_records(runtime: Runtime) -> list[dict[str, Any]]:
    records, _problems, _record_ids = _session_record_scan(runtime)
    return records


def _discover_native_sessions(
    runtime: Runtime, *, limit: int = 20, cwd_filter: str | None = None
) -> list[dict[str, Any]]:
    """Metadata-only, UUID-deduplicated discovery of unmanaged sessions."""

    home = Path(runtime.environ.get("HOME") or Path.home())
    projects = home / ".claude" / "projects"
    records, _record_problems, record_ids = _session_record_scan(runtime)
    managed_runtime_ids: set[str] = set(record_ids)
    for record in records:
        managed_runtime_ids.add(record["runtime_session_id"])
        managed_runtime_ids.update(
            item["session_id"] for item in record.get("runtime_aliases", [])
        )
    fork_of: dict[str, str] = {}
    for record in records:
        for item in record.get("pending_forks", []):
            fork_id = item.get("session_id")
            if fork_id and fork_id not in managed_runtime_ids:
                fork_of.setdefault(fork_id, sessions.managed_id(record))
    by_id: dict[str, dict[str, Any]] = {}
    try:
        for project_dir in projects.iterdir():
            if not project_dir.is_dir():
                continue
            for entry in project_dir.glob("*.jsonl"):
                session_id = entry.stem
                if (
                    not sessions.UUID4.fullmatch(session_id)
                    or session_id in managed_runtime_ids
                ):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                item = by_id.setdefault(
                    session_id,
                    {"session_id": session_id, "slugs": set(), "mtime": mtime},
                )
                item["slugs"].add(project_dir.name)
                item["mtime"] = max(item["mtime"], mtime)
    except OSError:
        return []

    found: list[dict[str, Any]] = []
    for item in by_id.values():
        slugs = tuple(sorted(item["slugs"]))
        current_slug = _native_project_slug(runtime.cwd)
        cwd_candidates = {
            path
            for slug in slugs
            for path in _decode_project_slug_candidates(slug)
        }
        if current_slug in slugs:
            cwd = runtime.cwd
        else:
            cwd = str(next(iter(cwd_candidates))) if len(cwd_candidates) == 1 else None
        if cwd_filter is not None and cwd != cwd_filter:
            continue
        found.append(
            {
                "session_id": item["session_id"],
                "slugs": slugs,
                "slug": slugs[0] if len(slugs) == 1 else "(ambiguous)",
                "cwd": cwd,
                "mtime": item["mtime"],
                "fork_of": fork_of.get(item["session_id"]),
            }
        )
    found.sort(key=lambda item: item["mtime"], reverse=True)
    return found[:limit]


# UX section 4 sessions screen strings (single source; tests pin them).
SESSIONS_TITLE = "sessions"
SESSIONS_KEYBAR = (
    ("R", "resume"),
    ("T", "switch comp"),
    ("X", "resolve fork"),
    ("E", "end session"),
    ("F", "forget"),
    ("L", "adopt"),
    ("C", "cwd filter"),
    ("?", "help"),
    ("Esc", "quit"),
)

SESSIONS_HELP = (
    "managed (claude-multi): sessions launched here or adopted; their agents,\n"
    "policy, and workflow mode are durable files that survive Claude restarts.\n"
    "  R resume — reopen with the same transcript and composition.\n"
    "  T switch comp — transition: same transcript, different composition\n"
    "    (semantic diff first; the session must be exited; relaunches exactly).\n"
    "  X resolve fork — clear/discard a native-fork marker that blocks resume\n"
    "    (the fork transcript is kept; exact commands: sessions show <uuid>).\n"
    "  E end session — stop a live (●) background-owned session with\n"
    "    upstream `claude stop` (conversation always kept; resume with R).\n"
    "  F forget — delete the launcher record + generated scope; the Claude\n"
    "    transcript is never touched.\n"
    "\n"
    "row markers: ● live — the session is owned by the background daemon right\n"
    "  now (reattaching to it from a Claude menu forks natively; exit it first\n"
    "  or resume after it exits) · ⚠ fork-blocked — a native fork awaits your\n"
    "  adopt/discard decision (X).\n"
    "\n"
    "native (unmanaged): plain-Claude sessions discovered by name/time only —\n"
    "the launcher never opens their files. They have no managed guarantees\n"
    "until adopted. Rows marked (fork) are native forks of a managed session.\n"
    "  L adopt — link one into a composition you choose; it becomes managed\n"
    "    (resume and switch comp then apply).\n"
    "\n"
    "Arrow keys move between and within sections; Esc closes this panel."
)
SESSIONS_EMPTY = "(no recorded sessions)"
FORGET_MODAL_TITLE = "Forget session {short}?"
FORGET_MODAL_BODY = (
    "Deletes: session record + generated scope{scope_note}.\n"
    "If the session is currently running, its agents lose their definition\n"
    "files until a resume recompiles them. Transcripts are never touched."
)
FORK_MODAL_TITLE = "Resolve fork on {short}?"
FORK_MODAL_BODY = (
    "Discard the pending-fork marker for runtime {fork_id}?\n"
    "The fork transcript stays on disk as a native session; adopt it later\n"
    "with `claude-multi sessions link {fork_id} --composition NAME` if needed."
)
STOP_MODAL_TITLE = "Stop live session {short}?"
STOP_MODAL_BODY = (
    "The session is live in the background (daemon-owned, ●).\n"
    "It will be stopped with upstream `claude stop {runtime_id}` — never a\n"
    "signal, never the transcript. The conversation is always kept and\n"
    "resumes later with R."
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


def _windowed(
    rows: list[list[str]], selected: int, max_rows: int
) -> tuple[list[list[str]], int]:
    """Slice rows so `selected` stays visible; returns (slice, translated)."""

    if len(rows) <= max_rows:
        return rows, selected
    start = min(max(0, selected - max_rows + 1), len(rows) - max_rows)
    return rows[start : start + max_rows], selected - start


def _record_age(record: dict[str, Any], *, now: datetime | None = None) -> str:
    """Relative session age for display ("2h ago"); ISO string on parse failure."""

    try:
        created = datetime.strptime(record["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (KeyError, ValueError):
        return str(record.get("created_at", "?"))
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int((current - created).total_seconds()))
    return _age_from_seconds(seconds)


def _mtime_age(mtime: float, *, now: datetime | None = None) -> str:
    """Relative age from a filesystem mtime (native session rows)."""

    current = now or datetime.now(timezone.utc)
    seconds = max(0, int(current.timestamp() - mtime))
    return _age_from_seconds(seconds)


def _age_from_seconds(seconds: int) -> str:
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


class _SessionsScreen:
    """UX section 4 sessions screen: managed + native (unmanaged) sections.

    Managed rows support resume/transition/forget as before. Native rows are
    discovered metadata-only (names + times; files never opened) and support
    exactly one action: ``l`` adopt (sessions link with a composition
    chooser), after which the row moves into the managed section.
    Returns ("resume", record) or ("transition", record, composition) for the
    CLI to execute after curses teardown; forget/adopt run inline.
    """

    def __init__(self, runtime: Runtime, *, palette: tui.Palette):
        self.runtime = runtime
        self.palette = palette
        self.section = "managed"
        self.selected = 0
        self.message = ""
        self.cwd_filter = False
        self._reload()

    def _reload(self) -> None:
        self.records = sorted(
            _session_records(self.runtime),
            key=lambda record: record["created_at"],
            reverse=True,
        )
        self.native = _discover_native_sessions(
            self.runtime,
            cwd_filter=self.runtime.cwd if self.cwd_filter else None,
        )
        self.live_prefixes = _live_background_prefixes()
        if self.cwd_filter:
            self.records = [
                record for record in self.records if record["cwd"] == self.runtime.cwd
            ]
        # Land on a non-empty section (zero-managed with native present, or
        # after forgetting the last managed record).
        if self.section == "managed" and not self.records and self.native:
            self.section = "native"
        elif self.section == "native" and not self.native and self.records:
            self.section = "managed"
        active = self.records if self.section == "managed" else self.native
        self.selected = min(self.selected, max(0, len(active) - 1))

    def _managed_rows(self) -> list[list[str]]:
        return [
            [
                _record_state_marker(record, self.live_prefixes)
                + _record_identity_label(record, short=True),
                _record_target_label(record),
                _record_mode_label(record),
                record["cwd"],
                _record_age(record),
            ]
            for record in self.records
        ]

    def _native_rows(self) -> list[list[str]]:
        return [
            [
                ("● " if _native_is_live(item, self.live_prefixes) else "")
                + f"{item['session_id'][:12]}…",
                "(fork)" if item.get("fork_of") else "(native)",
                "unmanaged",
                item["slug"],
                _mtime_age(item["mtime"]),
            ]
            for item in self.native
        ]

    def _active(self) -> list[dict[str, Any]]:
        return self.records if self.section == "managed" else self.native

    def _draw(self, win: Any) -> None:
        win.erase()
        palette = self.palette
        height, width = win.getmaxyx()
        keybar = tui.KeyBar(SESSIONS_KEYBAR)
        bar_rows = keybar.rows(width)
        if height < 14 or width < 44:
            # Minimum-size floor (review H6): below this the tables cannot
            # render honestly, so show the floor note instead of overdrawing.
            tui.safe_add(win, 1, 2, SESSIONS_TITLE, palette.attr("accent") | curses.A_BOLD)
            tui.safe_add(
                win, 3, 2, "terminal too small for the sessions screen;",
                palette.attr("warn"),
            )
            tui.safe_add(
                win, 4, 2, "resize, or use `claude-multi sessions list` (text).",
                palette.attr("dim"),
            )
            keybar.draw(win, height - 1, palette)
            win.refresh()
            return
        # Bottom block (keybar may wrap to 2 rows): actions label, message,
        # then the bar itself — reserve all of it so nothing is overdrawn.
        actions_row = height - bar_rows - 2
        message_row = height - bar_rows - 1
        title = SESSIONS_TITLE + (
            " · cwd filter ON" if self.cwd_filter else ""
        )
        tui.safe_add(win, 1, 2, title, palette.attr("accent") | curses.A_BOLD)
        tui.safe_add(win, 2, 2, "─" * min(width - 1, 62), palette.attr("dim"))
        row = 3
        managed_rows = self._managed_rows()
        if not managed_rows:
            tui.safe_add(win, row, 2, SESSIONS_EMPTY, palette.attr("dim"))
            row += 2
        else:
            tui.safe_add(win, row - 1, 2, "managed (claude-multi)", palette.attr("dim"))
            managed_max = min(len(managed_rows), actions_row - row - 3)
            managed_max = max(managed_max, 1)
            shown_rows, managed_selected = _windowed(
                managed_rows, self.selected if self.section == "managed" else -1, managed_max
            )
            table = tui.Table(
                ["session", "composition", "mode", "cwd", "created"],
                shown_rows,
                selected=managed_selected,
                min_widths=[27, 10, 11, 8, 19],
            )
            table.draw(win, row, 2, width - 2, palette, max_rows=managed_max)
            row += managed_max + 2
        if self.native:
            tui.safe_add(
                win,
                row,
                2,
                "native (unmanaged, discovered names+times only) · press L to adopt",
                palette.attr("dim"),
            )
            row += 1
            native_max = max(1, actions_row - row - 1)
            shown_rows, native_selected = _windowed(
                self._native_rows(),
                self.selected if self.section == "native" else -1,
                native_max,
            )
            table = tui.Table(
                ["session", "", "", "project", "active"],
                shown_rows,
                selected=native_selected,
                min_widths=[13, 8, 9, 8, 19],
            )
            table.draw(
                win,
                row,
                2,
                width - 2,
                palette,
                max_rows=native_max,
            )
        active = self._active()
        if active:
            item = active[self.selected]
            if self.section == "managed":
                label = _record_actions_label(item)
                if _record_is_live(item, self.live_prefixes):
                    label += " · [e] end (live ●)"
            else:
                label = "L adopt into a composition · then resume/transition apply"
            tui.safe_add(win, actions_row, 2, label, palette.attr("dim"))
        if self.message:
            tui.safe_add(win, message_row, 2, self.message, palette.attr("warn"))
        keybar.draw(win, height - 1, palette)
        win.refresh()

    def _stop_live(self, win: Any, record: dict[str, Any]) -> None:
        stable_id = sessions.managed_id(record)
        runtime_id = sessions.runtime_session_id(record)
        refusal = _stop_precheck(self.runtime, record)
        if refusal is not None:
            self.message = refusal + "."
            return
        confirmed = tui.Modal(
            STOP_MODAL_TITLE.format(short=f"{stable_id[:8]}…"),
            STOP_MODAL_BODY.format(runtime_id=runtime_id).splitlines(),
            buttons=(("Stop", True), ("Cancel", False)),
        ).run(win, self.palette, background=self._draw)
        if not confirmed:
            self.message = "Stop cancelled."
            return
        problem = _stop_runtime(self.runtime, runtime_id)
        if problem is not None:
            self.message = f"stop failed: {problem[:60]}"
            return
        self._reload()
        self.message = (
            f"stopped {stable_id[:8]}… · conversation kept; R resumes when ready"
        )

    def _resolve_fork(self, win: Any, record: dict[str, Any]) -> None:
        stable_id = sessions.managed_id(record)
        pending = record.get("pending_forks", [])
        if not pending:
            self.message = "no pending fork on this session."
            return
        if sessions.drop_resolved_pending_forks(record) is not None:
            try:
                self.runtime.session_store.converge_pending_forks(stable_id)
            except sessions.SessionError as exc:
                # The record changed/went away between screen load and here;
                # degrade to a message like the neighboring actions.
                self._reload()
                self.message = str(exc)
                return
            self._reload()
            remaining = len(
                self.runtime.session_store.load(stable_id).get("pending_forks", [])
            )
            self.message = "fork marker cleared (runtime already resolved it)"
            if remaining:
                self.message += f" · {remaining} genuine pending (X again)"
            else:
                self.message += "; resume unblocked"
            return
        fork_id = pending[0]["session_id"]
        confirmed = tui.Modal(
            FORK_MODAL_TITLE.format(short=f"{stable_id[:8]}…"),
            FORK_MODAL_BODY.format(fork_id=fork_id).splitlines(),
            buttons=(("Discard marker", True), ("Cancel", False)),
        ).run(win, self.palette, background=self._draw)
        if not confirmed:
            self.message = "Resolve fork cancelled."
            return
        try:
            self.runtime.session_store.resolve_fork(stable_id, fork_id)
        except sessions.SessionError as exc:
            # Concurrent resolve/link/forget changed the pending set behind
            # the modal; show why instead of crashing the picker.
            self._reload()
            self.message = str(exc)
            return
        self._reload()
        remaining = len(pending) - 1
        self.message = f"fork {fork_id[:12]}… marker discarded; transcript kept"
        if remaining:
            self.message += f" · {remaining} more pending (X again)"

    def _forget(self, win: Any, record: dict[str, Any]) -> None:
        session_id = sessions.managed_id(record)
        short = f"{session_id[:8]}…"
        scope_exists = scope_mod.scope_dir(
            self.runtime.session_store.root, session_id
        ).is_dir()
        scope_note = "" if scope_exists else " (no generated scope exists)"
        confirmed = tui.Modal(
            FORGET_MODAL_TITLE.format(short=short),
            FORGET_MODAL_BODY.format(scope_note=scope_note).splitlines(),
            buttons=(("Forget", True), ("Cancel", False)),
        ).run(win, self.palette, background=self._draw)
        if not confirmed:
            self.message = "Forget cancelled."
            return
        # Same serialized effects as `sessions forget`.
        self.runtime.session_store.forget_session(session_id)
        self._reload()
        self.selected = min(self.selected, max(0, len(self._active()) - 1))
        self.message = FORGET_DONE.format(session_id=session_id)

    def _choose_composition(self, win: Any, record: dict[str, Any]) -> str | None:
        short = f"{sessions.managed_id(record)[:8]}…"
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

    def _adopt(self, win: Any, item: dict[str, Any]) -> None:
        """Adopt a native session into the managed set (sessions link inline)."""

        names = self.runtime.compositions.names()
        chooser = tui.SelectList(
            f"Adopt {item['session_id'][:8]}… into composition",
            [tui.SelectItem(name) for name in names],
            footer=(("Enter", "adopt"), ("Esc", "back")),
        )
        index = chooser.run(win, self.palette)
        if index is None:
            self.message = "Adopt cancelled."
            return
        document = self.runtime.compositions.load(names[index])
        resolved = self.runtime.resolve_document(document)
        stable_id = self.runtime.session_store.new_id()
        record = sessions.make_record(
            managed_id=stable_id,
            runtime_session_id=item["session_id"],
            cwd=_original_cwd_for_adopt(self.runtime, item["session_id"]),
            composition_name=document["name"],
            snapshot=composition.snapshot(resolved),
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )
        self.runtime.session_store.link(record)
        self.message = (
            f"Adopted {item['session_id'][:8]}… into cm:{document['name']}; "
            "it is now managed — resume or transition apply"
        )
        self.section = "managed"
        self._reload()
        self.selected = next(
            (
                i
                for i, record in enumerate(self.records)
                if record["runtime_session_id"] == item["session_id"]
            ),
            0,
        )

    def run(self, win: Any) -> tuple[str, dict[str, Any]] | tuple[str, dict[str, Any], str] | None:
        tui.hide_cursor()
        while True:
            self._draw(win)
            key = tui.read_key(win)
            if key.kind == "resize":
                continue
            if key.kind == "ctrl" and key.ch == "c":
                raise KeyboardInterrupt
            if key.kind == "esc":
                return None
            active = self._active()
            if key.kind == "char" and key.ch == "?":
                tui.Modal(
                    "sessions — help",
                    SESSIONS_HELP.splitlines(),
                    buttons=(("Close", True),),
                ).run(win, self.palette, background=self._draw)
                continue
            if key.kind == "char" and key.ch.lower() == "c":
                self.cwd_filter = not self.cwd_filter
                self._reload()
                self.message = (
                    "showing only this directory" if self.cwd_filter
                    else "showing all sessions"
                )
                continue
            if not active:
                continue
            if key.kind == "up" or (key.kind == "char" and key.ch == "k"):
                if self.selected > 0:
                    self.selected -= 1
                elif self.section == "native" and self.records:
                    self.section = "managed"
                    self.selected = len(self.records) - 1
                continue
            if key.kind == "down" or (key.kind == "char" and key.ch == "j"):
                if self.selected < len(active) - 1:
                    self.selected += 1
                elif self.section == "managed" and self.native:
                    self.section = "native"
                    self.selected = 0
                continue
            item = active[self.selected]
            if self.section == "native":
                if key.kind == "char" and key.ch.lower() == "l":
                    self._adopt(win, item)
                elif key.kind == "char" and key.ch in ("r", "t", "f"):
                    self.message = (
                        "adopt this session first (L); resume/transition/forget "
                        "apply to managed sessions"
                    )
                elif key.kind == "char" and key.ch.lower() == "e":
                    self.message = (
                        "end session applies to managed sessions; adopt first (L) "
                        "or use `claude stop " + item["session_id"] + "` directly"
                    )
                continue
            record = item
            if key.kind == "char" and key.ch.lower() == "r":
                if record.get("pending_forks"):
                    self.message = (
                        "resume is fork-blocked — press X to resolve the fork "
                        "(exact commands: sessions show)"
                    )
                    continue
                gate = _evaluate_resume_gate(self.runtime, record)
                if gate.kind != "ok":
                    try:
                        resolved = _run_resume_gate_modal(
                            self.runtime,
                            record,
                            gate,
                            win,
                            self.palette,
                            background=self._draw,
                        )
                    except (CLIError, sessions.SessionError) as exc:
                        self.message = str(exc)
                        continue
                    if resolved is None:
                        self.message = "Resume cancelled."
                        continue
                    return resolved
                lines: list[str] = []
                if record["mode"] != "durable":
                    lines = LEGACY_RESUME_NOTE.split("; ")
                confirmed = tui.Modal(
                    RESUME_MODAL_TITLE.format(short=f"{sessions.managed_id(record)[:8]}…"),
                    lines,
                    buttons=(("Resume", True), ("Cancel", False)),
                ).run(win, self.palette, background=self._draw)
                if confirmed:
                    return ("resume", record)
                self.message = "Resume cancelled."
                continue
            if key.kind == "char" and key.ch.lower() == "t":
                if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
                    self.message = (
                        "ordinary gateway sessions have no composition; relaunch "
                        "with claude-gateway --resume ID --model MODEL"
                    )
                    continue
                name = self._choose_composition(win, record)
                if name is not None:
                    return ("transition", record, name)
                continue
            if key.kind == "char" and key.ch.lower() == "f":
                self._forget(win, record)
                continue
            if key.kind == "char" and key.ch.lower() == "x":
                self._resolve_fork(win, record)
                continue
            if key.kind == "char" and key.ch.lower() == "e":
                self._stop_live(win, record)
                continue


TRANSITION_HELP = (
    "A transition changes a session's composition while keeping its transcript:\n"
    "agents, models, effort, workflow mode, and policy are recomputed and the\n"
    "session relaunches with `claude --resume <uuid>` — same conversation, new\n"
    "composition.\n"
    "\n"
    "Rules: review the semantic diff above first. The session's process must\n"
    "have EXITED (not merely idle) before anything is mutated — exit the TUI,\n"
    "then confirm. If the relaunch fails, the previous composition and record\n"
    "are restored and an exact recovery command is shown."
)


class _TransitionScreen:
    """Semantic diff view + exited-confirmation Modal (TRANSITIONS section 3).

    The wording states the target process must have EXITED, not merely idle.
    Returns True only on explicit confirmation; Esc cancels (False).
    """

    KEYBAR = (("Enter", "confirm exited"), ("?", "help"), ("Esc", "cancel"))

    def __init__(
        self,
        diff: list[str],
        *,
        palette: tui.Palette,
        live_note: str | None = None,
    ):
        self.diff = diff
        self.palette = palette
        self.scroll = 0
        self.live_note = live_note

    def _draw(self, win: Any) -> None:
        win.erase()
        palette = self.palette
        height, width = win.getmaxyx()
        keybar = tui.KeyBar(self.KEYBAR)
        bar_rows = keybar.rows(width)
        bottom = height - bar_rows
        tui.safe_add(win, 1, 2, "transition — semantic diff", palette.attr("accent") | curses.A_BOLD)
        tui.safe_add(win, 2, 2, "─" * min(width - 3, 60), palette.attr("dim"))
        first = 3
        if self.live_note is not None:
            tui.safe_add(win, first, 2, self.live_note, palette.attr("warn"))
            first += 1
        visible = max(1, bottom - first - 1)
        self.scroll = max(0, min(self.scroll, max(0, len(self.diff) - visible)))
        # Diff lines start below the separator (review H13), clipped at the
        # reserved scroll-indicator/keybar zone.
        for offset, line in enumerate(self.diff[self.scroll : self.scroll + visible]):
            tui.safe_add(win, first + offset, 2, line)
        if len(self.diff) > visible:
            tui.safe_add(
                win,
                bottom - 1,
                2,
                f"{self.scroll + 1}-{min(len(self.diff), self.scroll + visible)} of {len(self.diff)}",
                palette.attr("dim"),
            )
        keybar.draw(win, height - 1, palette)
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
            if key.kind == "char" and key.ch == "?":
                tui.Modal(
                    "transition — help",
                    TRANSITION_HELP.splitlines(),
                    buttons=(("Close", True),),
                ).run(win, self.palette, background=self._draw)
                continue
            if key.kind == "esc":
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
                    ).run(win, self.palette, background=self._draw)
                )


def _transition_confirm(
    diff: list[str],
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    no_color: bool,
    live_note: str | None = None,
) -> bool:
    """Exited-confirmation: curses Modal when capable, else the [y/N] line."""

    if tui.streams_curses_capable(input_stream, output_stream):
        palette = tui.detect_palette(
            no_color=no_color, tty_in=input_stream, tty_out=output_stream
        )
        screen = _TransitionScreen(diff, palette=palette, live_note=live_note)
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
    passthrough: list[str] | None = None,
    legacy_requested: bool = False,
) -> int:
    """Interactive sessions screen; actions reuse the command flows verbatim.

    Launch intent threads through: ``passthrough`` (claude-side tail args)
    and ``legacy_requested`` follow the resume the user picks here, the same
    as if they had typed ``-r <uuid>`` directly.
    """

    passthrough = passthrough if passthrough is not None else []
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
        decision = result[2] if len(result) > 2 else None
        if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
            prepared = runtime.prepare_direct(
                action="resume",
                model_id=None,
                passthrough=passthrough,
                session_id=sessions.managed_id(record),
            )
            return runtime.perform(prepared, resume_decision=decision)
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
            passthrough=passthrough,
            session_id=sessions.managed_id(record),
            legacy_requested=legacy_requested,
        )
        return runtime.perform(prepared, resume_decision=decision)
    if result[0] == "transition":
        _, record, name = result
        namespace = argparse.Namespace(
            uuid=sessions.managed_id(record),
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


def _live_background_prefixes(root: Path | None = None) -> frozenset[str]:
    """Session-id prefixes currently hosted by the Claude background daemon.

    Best-effort and read-only: the daemon's pty sockets are named
    ``<session-id-prefix>.sock`` under ``/tmp/cc-daemon-<uid>/``. A session
    that is live there is owned by another process — reattaching to it from
    a menu forks natively. Any error degrades to "nothing live".
    """

    if root is None:
        getuid = getattr(os, "getuid", None)
        if getuid is None:
            return frozenset()
        root = Path(f"/tmp/cc-daemon-{getuid()}")
    try:
        roots = list(root.glob("*/pty/*.sock"))
    except OSError:
        return frozenset()
    return frozenset(sock.name[: -len(".sock")] for sock in roots if sock.name)


def _record_is_live(record: dict[str, Any], prefixes: frozenset[str]) -> bool:
    if not prefixes:
        return False
    candidates = [sessions.managed_id(record), record["runtime_session_id"]]
    candidates.extend(
        item["session_id"] for item in record.get("runtime_aliases", [])
    )
    return any(
        candidate.startswith(prefix)
        for candidate in candidates
        for prefix in prefixes
    )


def _stop_runtime(
    runtime: Runtime,
    runtime_id: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> str | None:
    """Upstream `claude stop <id>` via the verified binary; error text or None.

    This is the only process-lifecycle action claude-multi takes, and it goes
    through upstream's own public CLI — never a signal, never daemon
    internals. The conversation is always kept (upstream guarantee).
    """

    status = launch.resolve_claude(runtime.catalog.docs["native-contract"])
    run = subprocess.run if runner is None else runner
    try:
        outcome = run(
            [str(status.inspected_path), "stop", runtime_id],
            capture_output=True,
            text=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin", "HOME": runtime.environ.get("HOME", "/")},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return str(exc)
    if outcome.returncode != 0:
        tail = ((outcome.stdout or "") + (outcome.stderr or "")).strip()
        return tail or f"exit code {outcome.returncode}"
    return None


def _stop_precheck(runtime: Runtime, record: dict[str, Any]) -> str | None:
    """Shared stop guards; a refusal message, or None when stop is possible."""

    stable_id = sessions.managed_id(record)
    if runtime.environ.get("CLAUDE_MULTI_MANAGED_ID") == stable_id:
        return "refusing to stop the session you are running inside"
    if not _record_is_live(record, _live_background_prefixes()):
        return (
            "not live in the background — nothing to stop (if it is attached "
            "in a terminal, exit it there)"
        )
    return None


def _native_is_live(item: dict[str, Any], prefixes: frozenset[str]) -> bool:
    return any(item["session_id"].startswith(prefix) for prefix in prefixes)


def _record_state_marker(record: dict[str, Any], prefixes: frozenset[str]) -> str:
    """Compact row prefix: ● live (background-owned) · ⚠ fork-blocked · ! repair-needed."""

    marker = ""
    if _record_is_live(record, prefixes):
        marker += "● "
    if record.get("pending_forks"):
        marker += "⚠ "
    identity_state = record.get("identity_state", sessions.IDENTITY_UNVERIFIED)
    if identity_state == sessions.IDENTITY_REPAIR_NEEDED and (
        "observed_cwd" in record or "observed_model" not in record
    ):
        marker += "! "
    return marker


def _record_identity_label(record: dict[str, Any], *, short: bool = False) -> str:
    stable = sessions.managed_id(record)
    runtime_id = record["runtime_session_id"]
    same = stable == runtime_id
    if short:
        stable = stable[:8] + "…"
        runtime_id = runtime_id[:8] + "…"
    if same:
        return stable
    return f"{stable} → runtime {runtime_id}"


def _record_target_label(record: dict[str, Any]) -> str:
    if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
        return f"gateway:{record['ordinary_model']}"
    return f"cm:{record['composition_name']}"


# -- resume gate (issue 003) -------------------------------------------------
#
# One pure evaluator consumed by every resume surface: the sessions picker,
# the quick-confirm card, line mode, and Runtime.perform (mandatory
# backstop). The gate is metadata-only, takes no locks, and never mutates —
# stop/relink stay explicit operator-confirmed actions in the UI adapters.


@dataclass(frozen=True)
class ResumeGate:
    """Action-needed state for a resume target, or kind "ok"."""

    kind: str  # "ok" | "repair-needed" | "daemon-owned" | "transcript-elsewhere" | "transcript-missing"
    title: str
    lines: tuple[str, ...]
    actions: tuple[tuple[str, str], ...]  # (value, label); Cancel/Esc always exists


def _resume_transcript_status(
    runtime: Runtime, record: dict[str, Any]
) -> tuple[str, str]:
    """Metadata-only: is the runtime transcript where the record points?

    Returns ("present", path) | ("elsewhere", slug-dirs) | ("missing", path).
    Never opens a transcript; filename checks only.
    """

    runtime_id = sessions.runtime_session_id(record)
    home = Path(runtime.environ.get("HOME") or Path.home())
    expected = (
        home
        / ".claude"
        / "projects"
        / _native_project_slug(record["cwd"])
        / f"{runtime_id}.jsonl"
    )
    if expected.exists():
        return ("present", str(expected))
    found = _slugs_for_session(runtime, runtime_id)
    if found:
        return ("elsewhere", ", ".join(found))
    return ("missing", str(expected))


def _evaluate_resume_gate(
    runtime: Runtime,
    record: dict[str, Any],
    *,
    live_prefixes: frozenset[str] | None = None,
) -> ResumeGate:
    """Pure resume-gate evaluation; re-scans liveness unless injected."""

    stable_id = sessions.managed_id(record)
    identity_state = record.get("identity_state", sessions.IDENTITY_UNVERIFIED)
    if identity_state == sessions.IDENTITY_REPAIR_NEEDED and (
        "observed_cwd" in record or "observed_model" not in record
    ):
        return ResumeGate(
            kind="repair-needed",
            title="Session needs identity repair",
            lines=tuple(
                textwrap.wrap(sessions.relink_message(record), width=60)
            ),
            actions=(("repair-resume", "Repair & resume"),),
        )
    prefixes = (
        live_prefixes if live_prefixes is not None else _live_background_prefixes()
    )
    if _record_is_live(record, prefixes):
        runtime_id = sessions.runtime_session_id(record)
        text = (
            f"session {stable_id} is live in the background (●). Resuming a "
            "background-owned session natively either fails or forks it — "
            "the fork path is what caused the original incident. The "
            "supported route is to stop it first "
            f"(`claude-multi sessions stop {stable_id}`), then resume. The "
            "marker is a best-effort heuristic — if you are sure it is "
            "stale, Resume anyway."
        )
        return ResumeGate(
            kind="daemon-owned",
            title="Session is live in the background",
            lines=tuple(textwrap.wrap(text, width=60)),
            actions=(
                ("stop-resume", "Stop & resume"),
                ("force", "Resume anyway"),
            ),
        )
    status, detail = _resume_transcript_status(runtime, record)
    if status == "elsewhere":
        runtime_id = sessions.runtime_session_id(record)
        text = (
            f"the transcript for runtime {runtime_id} was not found under "
            f"the recorded project dir ({record['cwd']}), but a file with "
            f"the same name exists in: {detail}. If the session was "
            "intentionally re-homed, repair the record with "
            f"`claude-multi sessions relink-runtime {stable_id} {runtime_id} "
            "--cwd <that project directory>`; otherwise resume from the "
            "recorded dir after moving the transcript back."
        )
        return ResumeGate(
            kind="transcript-elsewhere",
            title="Transcript found in a different project",
            lines=tuple(textwrap.wrap(text, width=60)),
            actions=(),
        )
    if status == "missing":
        text = (
            f"no transcript file exists for runtime "
            f"{sessions.runtime_session_id(record)} anywhere under "
            "~/.claude/projects (expected at "
            f"{detail}). Resume cannot work — Claude resumes from that "
            "file, and claude-multi never deletes transcripts. Restore it "
            "from a backup if one exists; otherwise forget the record with "
            f"`claude-multi sessions forget {stable_id}`."
        )
        return ResumeGate(
            kind="transcript-missing",
            title="Transcript not found",
            lines=tuple(textwrap.wrap(text, width=60)),
            actions=(),
        )
    return ResumeGate(kind="ok", title="", lines=(), actions=())


def _resume_gate_refusal(gate: ResumeGate) -> str:
    """Text-mode backstop text for a non-ok gate (perform enforcement)."""

    return gate.title + " — " + " ".join(gate.lines)


def _run_resume_gate_modal(
    runtime: Runtime,
    record: dict[str, Any],
    gate: ResumeGate,
    win: Any,
    palette: Any,
    *,
    background: Any = None,
) -> tuple[str, dict[str, Any], str | None] | None:
    """Present a non-ok resume gate as a Modal and resolve the choice.

    Returns ("resume", record, decision) when the operator resolved the
    gate (decision is "force" only for the daemon-owned bypass), or None
    when cancelled/failed (the caller stays on its screen). Stop and
    repair go through the existing store/stop machinery — never manual
    record edits.
    """

    buttons = [(label, value) for value, label in gate.actions]
    buttons.append(("Cancel", None))
    choice = tui.Modal(
        gate.title, list(gate.lines), buttons=tuple(buttons)
    ).run(win, palette, background=background)
    if choice is None:
        return None
    stable_id = sessions.managed_id(record)
    runtime_id = sessions.runtime_session_id(record)
    if choice == "repair-resume":
        runtime.session_store.relink_runtime(
            stable_id, observed_runtime_id=runtime_id
        )
        return ("resume", runtime.session_store.load(stable_id), None)
    if choice == "stop-resume":
        refusal = _stop_precheck(runtime, record)
        if refusal is not None:
            raise CLIError(refusal)
        error = _stop_runtime(runtime, runtime_id)
        if error is not None:
            raise CLIError(f"stop failed: {error}")
        return ("resume", record, "force")
    if choice == "force":
        return ("resume", record, "force")
    return None



def _record_mode_label(record: dict[str, Any]) -> str:
    """UX §4 mode column: durable with generation, or legacy."""

    if record["mode"] == "durable":
        return f"durable(g{record['scope_generation']})"
    return "legacy"


def _record_actions_label(record: dict[str, Any]) -> str:
    """UX §4 per-row action hints; legacy rows state the one-time upgrade."""

    if record.get("pending_forks"):
        return (
            "[x] resolve fork (resume blocked until then) [f]orget · fork UUID "
            "and exact commands: `claude-multi sessions show "
            f"{sessions.managed_id(record)}`"
        )
    identity_state = record.get("identity_state", sessions.IDENTITY_UNVERIFIED)
    if identity_state == sessions.IDENTITY_REPAIR_NEEDED and (
        "observed_cwd" in record or "observed_model" not in record
    ):
        return (
            "repair needed (resume blocked) · exact command: "
            "`claude-multi sessions show " f"{sessions.managed_id(record)}`"
        )
    if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
        return "[r]esume [f]orget · cross-profile model changes relaunch explicitly"
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


def _print_launch_plan(prepared: PreparedLaunch, output_stream: TextIO) -> None:
    """--print-launch: exact argv + env summary; the token is never shown."""

    result = prepared.result
    output_stream.write("claude argv (after the verified executable):\n")
    for token in result.argv:
        output_stream.write(f"  {tui.visible_text(token)}\n")
    output_stream.write("environment (effective at exec):\n")
    for key in sorted(result.env_set):
        output_stream.write(f"  set {key}\n")
    for key in result.env_unset:
        output_stream.write(f"  unset {key}\n")
    output_stream.write(
        "  set ANTHROPIC_AUTH_TOKEN (from the private gateway key file; "
        "value never shown)\n"
    )
    output_stream.write(
        f"launch mode: {'durable' if result.durable else 'legacy argv'} · "
        f"record mode: {prepared.record['mode']} · target "
        f"{tui.visible_text(_record_target_label(prepared.record))} · session "
        f"{_record_identity_label(prepared.record)}\n"
    )


def _print_sessions_listing(runtime: Runtime, output_stream: TextIO) -> None:
    """The text sessions listing (line-mode fallback + quick-confirm S key)."""

    records = sorted(
        _session_records(runtime),
        key=lambda record: record["created_at"],
        reverse=True,
    )
    output_stream.write("sessions\n")
    output_stream.write("----------------------------------------\n")
    live = _live_background_prefixes()
    for record in records:
        # Record fields (cwd, composition_name) are external text;
        # the whole row is sanitized single-line output.
        output_stream.write(
            tui.visible_text(
                f"{_record_state_marker(record, live)}"
                f"{_record_identity_label(record)}  {_record_target_label(record)}  "
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
        "[e]nd a live session `claude-multi sessions stop <uuid>` · "
        "[f]orget `claude-multi sessions forget <uuid>` "
        "(deletes the record + generated scope; transcripts are never touched)\n"
    )
    output_stream.write(
        "not seeing a session? only managed (launcher-started or adopted) sessions "
        "are listed — adopt native ones with `claude-multi sessions link <uuid>` "
        "(run it bare for the discovery guide)\n"
    )
    native = _discover_native_sessions(runtime)
    if native:
        live = _live_background_prefixes()
        output_stream.write("native (unmanaged, discovered names+times only)\n")
        output_stream.write("----------------------------------------\n")
        for item in native:
            marker = "● " if _native_is_live(item, live) else ""
            kind = (
                f"(fork of {item['fork_of'][:8]}…)"
                if item.get("fork_of")
                else "(native)"
            )
            output_stream.write(
                tui.visible_text(
                    f"{marker}{item['session_id']}  {kind}  {item['slug']}  "
                    f"{_mtime_age(item['mtime'])}"
                )
                + "\n"
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
    live_note: str | None = None
    if _record_is_live(plan.prior_record, _live_background_prefixes()):
        live_note = (
            "the target session is LIVE in the background (●, daemon-owned) — "
            "a transition on a live session forks it; stop it first with "
            "`claude-multi sessions stop "
            f"{sessions.managed_id(plan.prior_record)}`"
        )
        if not interactive_diff:
            output_stream.write(f"{tui.visible_text(live_note)}\n")
    if args.its_exited:
        confirm = True
    elif interactive:
        confirm = _transition_confirm(
            plan.diff,
            input_stream=input_stream,
            output_stream=output_stream,
            no_color=no_color,
            live_note=live_note,
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
            expected_launch_epoch=outcome.record.get("launch_epoch", 0),
            expected_mutation_token=outcome.record.get("mutation_token"),
            expected_source_scope_generation=outcome.record.get("scope_generation"),
            expected_source_composition_hash=outcome.record.get("composition_hash"),
            precommitted=True,
        )
        try:
            return runtime.perform(prepared)
        except Exception as exc:
            try:
                restored = transition.restore_exec_failure(
                    runtime.session_store,
                    plan.session_id,
                    outcome.prior_record_bytes,
                    expected_record_bytes=outcome.committed_record_bytes,
                )
            except Exception:
                # A failing restore must never mask the launch failure (H17).
                restored = False
            kind = "exec failed" if isinstance(exc, OSError) else "launch failed"
            if restored:
                note = (
                    "the prior scope generation and record were restored"
                )
            else:
                note = (
                    "state was NOT restored (a newer attempt owns it or the "
                    "restore failed); run `claude-multi doctor --repair "
                    f"{plan.session_id}` before retrying"
                )
            raise CLIError(
                f"relaunch {kind} ({exc}); {note}. Retry with: {plan.command_text}"
            ) from exc
    raise CLIError(f"unknown transition outcome kind {outcome.kind!r}")


def _print_composition(runtime: Runtime, document: dict[str, Any], stream: TextIO) -> None:
    plan = build_quick_plan(runtime, document, action="fresh", source="Saved composition")
    stream.write(render_quick_confirm(runtime, plan, details=True))




def _packaged_contract(runtime: Runtime) -> dict[str, Any]:
    """The installed baseline contract (asset root), pre-override (H2)."""

    return strict_json.load(runtime.asset_root / "catalog" / "native-contract.json")

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


_SESSION_START_SOURCES = frozenset({"startup", "resume", "clear", "compact", "fork"})


def _direct_model_for_selector(
    runtime: Runtime, selector: str
) -> tuple[str, str] | None:
    return compiler.direct_model_for_selector(runtime.catalog.docs, selector)


def _write_session_start_context(output_stream: TextIO, message: str) -> None:
    response = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": message,
        }
    }
    output_stream.write(strict_json.canonical_file_bytes(response).decode("utf-8"))


def _handle_session_event(
    runtime: Runtime,
    args: argparse.Namespace,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
) -> int:
    """Consume one official hook event without opening transcript_path."""

    try:
        payload = strict_json.loads(input_stream.read())
    except strict_json.StrictJSONError as exc:
        raise CLIError(f"invalid session hook JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CLIError("session hook payload is not an object")
    stable_id = args.managed_id
    if not sessions.UUID4.fullmatch(stable_id):
        raise CLIError(f"managed id {stable_id!r} is not a UUIDv4")
    launch_epoch = args.launch_epoch
    if launch_epoch is not None and launch_epoch < 0:
        raise CLIError("launch epoch must be non-negative")
    observed = payload.get("session_id")
    if not isinstance(observed, str) or not sessions.UUID4.fullmatch(observed):
        raise CLIError(f"hook session_id {observed!r} is not a UUIDv4")

    if args.event == "start":
        event_name = payload.get("hook_event_name")
        if event_name not in (None, "SessionStart"):
            raise CLIError(f"expected SessionStart payload, got {event_name!r}")
        source = payload.get("source")
        if source not in _SESSION_START_SOURCES:
            raise CLIError(f"unknown SessionStart source {source!r}")
        cwd = payload.get("cwd")
        if cwd is not None and (not isinstance(cwd, str) or not cwd.startswith("/")):
            raise CLIError(f"hook cwd {cwd!r} is not an absolute path")
        model = payload.get("model")
        if model is not None and not isinstance(model, str):
            raise CLIError("hook model must be a string when present")
        current = runtime.session_store.load(stable_id)
        reconciled_model = model
        reconciled_profile: str | None = None
        if model and current["session_type"] == sessions.SESSION_TYPE_ORDINARY:
            resolved_model = _direct_model_for_selector(runtime, model)
            if resolved_model is None:
                reconciled_model = None
            else:
                reconciled_model, reconciled_profile = resolved_model
        elif model:
            lead_id = current["snapshot"]["lead"]["model"]
            lead_model = runtime.catalog.models[lead_id]
            equivalent = {
                lead_model["wire_model"],
                current["snapshot"]["lead"]["client_selector"],
            }
            if model in equivalent:
                reconciled_model = current["snapshot"]["lead"]["client_selector"]
        record = runtime.session_store.reconcile_runtime(
            stable_id,
            observed_runtime_id=observed,
            source=source,
            cwd=cwd,
            model=reconciled_model,
            model_profile=reconciled_profile,
            observed_model=model,
            launch_epoch=launch_epoch,
        )
        if source == "fork" and any(
            item.get("session_id") == observed
            for item in record.get("pending_forks", [])
        ):
            if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
                adopt_hint = f"claude-multi sessions link {observed} --model MODEL"
            else:
                adopt_hint = (
                    f"claude-multi sessions link {observed} --composition "
                    f"{record.get('composition_name', 'NAME')}"
                )
            _write_session_start_context(
                output_stream,
                "This native fork does not yet have an independent durable "
                "claude-multi scope, and the parent is fork-blocked until you "
                f"decide. Exit this fork, then either adopt it: `{adopt_hint}`, "
                f"or discard the marker: `claude-multi sessions resolve-fork "
                f"{stable_id} {observed}` (the fork transcript is kept either "
                "way). Note: adopting or discarding advances the parent's "
                "launch epoch, so this fork's later hooks can no longer claim "
                "the parent.",
            )
        elif (
            record["identity_state"] == sessions.IDENTITY_REPAIR_NEEDED
            and "observed_model" in record
        ):
            if record["session_type"] == sessions.SESSION_TYPE_MANAGED:
                _write_session_start_context(
                    output_stream,
                    "Claude reported model "
                    f"{record['observed_model']!r}, which differs from the recorded "
                    "managed lead. Exit and resume the recorded composition with "
                    f"`claude-multi -r {stable_id}`; use `claude-multi sessions "
                    f"transition {stable_id} --composition NAME` for an intentional "
                    "lead change.",
                )
            else:
                _write_session_start_context(
                    output_stream,
                    "Claude reported model "
                    f"{record['observed_model']!r}, which is not safe under the "
                    f"active {record['context_profile']!r} context/compaction profile. "
                    "Exit and explicitly relaunch with a supported catalog model, "
                    f"for example `claude-gateway -r {stable_id} --model "
                    f"{record['ordinary_model']}`.",
                )
        return 0

    event_name = payload.get("hook_event_name")
    if event_name not in (None, "SessionEnd"):
        raise CLIError(f"expected SessionEnd payload, got {event_name!r}")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason:
        raise CLIError("SessionEnd reason is missing")
    runtime.session_store.record_session_end(
        stable_id,
        observed_runtime_id=observed,
        reason=reason,
        launch_epoch=launch_epoch,
    )
    return 0


def _subagent_model_override_paths(runtime: Runtime) -> list[str]:
    """Settings files whose env block sets CLAUDE_CODE_SUBAGENT_MODEL.

    Read-only audit (D19/D23 — upstream files are never written). That env
    var sits at step 1 of the subagent model resolution chain, ahead of
    agent frontmatter (SA L242-251): when set, it flattens every roster to
    one model — silently. Pre-D39 the lead-only fence made a leak inert;
    post-D39 it would silently re-create the incident.
    """

    home = runtime.environ.get("HOME", str(Path.home()))
    candidates = (
        Path(home) / ".claude" / "settings.json",
        Path(runtime.cwd) / ".claude" / "settings.json",
        Path(runtime.cwd) / ".claude" / "settings.local.json",
    )
    hits: list[str] = []
    for path in candidates:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        env = doc.get("env") if isinstance(doc, dict) else None
        if isinstance(env, dict) and env.get("CLAUDE_CODE_SUBAGENT_MODEL"):
            hits.append(str(path))
    return hits


def _collect_doctor_reports(
    runtime: Runtime,
) -> tuple[list[str], list[str], list[str]]:
    """(problems, info, attention) for doctor — shared by CLI and the TUI.

    Same checks, same lines: composition validation and secrets, binary
    verification, daemon, contract source, gateway readiness, scope/session
    integrity, collisions, the re-pin early-warning.
    """

    problems: list[str] = []
    if runtime.broken_override_error is not None:
        # Real damage: the operator contract override is invalid and was
        # ignored (the packaged baseline is in effect). The fix is one
        # command: `claude-multi update` removes the broken file.
        problems.append(
            "the contract override is invalid and was IGNORED (packaged "
            f"baseline in effect): {runtime.broken_override_error}; run "
            "`claude-multi update` to remove it"
        )
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
    binary_problems, binary_info = runtime.doctor_binary_callback(
        runtime.catalog.docs["native-contract"]
    )
    problems.extend(binary_problems)
    daemon = runtime.doctor_daemon_callback()
    source_label = runtime.catalog.contract_source
    contract_note = {
        "packaged": None,
        "override": "operator override (written by `claude-multi update`) is in effect",
        "override-ignored-stale": "a stale operator override exists but is ignored (the packaged contract is newer or equal)",
    }.get(source_label)
    info_lines = [*binary_info, f"Shared daemon: {daemon.summary}."]
    if contract_note is not None:
        info_lines.append(f"Contract: {contract_note}.")
    stale_override_attention = (
        "stale contract override ignored: the packaged native contract is "
        "newer or equal; `claude-multi update` rebases or removes it"
        if source_label == "override-ignored-stale"
        else None
    )
    if runtime.doctor_callback is not None:
        problems.extend(runtime.doctor_callback(runtime))
    else:
        try:
            launch.check_readiness(runtime.catalog.docs["gateway"])
        except launch.LaunchError as exc:
            problems.append(f"local gateway: {exc}")
    scope_info, scope_problems, scope_attention = _doctor_scope_report(runtime)
    info_lines.extend(scope_info)
    problems.extend(scope_problems)
    # Radar for the last roster-flattening vector (D39): a
    # CLAUDE_CODE_SUBAGENT_MODEL in user/project settings env overrides
    # every agent's frontmatter model — silently, session-wide.
    override_paths = _subagent_model_override_paths(runtime)
    if override_paths:
        scope_attention.append(
            "CLAUDE_CODE_SUBAGENT_MODEL is set in "
            + ", ".join(override_paths)
            + " — it overrides every agent's frontmatter model and flattens "
            "composition rosters to one model; remove it to keep "
            "composition routing intact"
        )
    if stale_override_attention is not None:
        scope_attention.append(stale_override_attention)
    repin = launch.repin_suggestion(runtime.catalog.docs["native-contract"])
    if repin is not None:
        scope_attention.append(repin)
    collision_info, collision_problems = _doctor_collision_report(runtime)
    info_lines.append(collision_info)
    problems.extend(collision_problems)
    info_lines.append(f"Evidence: {EVIDENCE_ADD_DIR_CARRY}")
    return problems, info_lines, scope_attention


def handle_command(
    runtime: Runtime,
    args: argparse.Namespace,
    *,
    input_stream: TextIO,
    output_stream: TextIO,
    interactive: bool,
    no_color: bool = False,
    passthrough: list[str] | None = None,
) -> int:
    if args.command == "session-event":
        return _handle_session_event(
            runtime,
            args,
            input_stream=input_stream,
            output_stream=output_stream,
        )

    if args.command == "direct":
        identifier = args.direct_resume
        action = "fresh"
        if args.direct_continue:
            identifier = runtime.session_store.last(
                runtime.cwd, session_type=sessions.SESSION_TYPE_ORDINARY
            )
            if identifier is None:
                raise CLIError(
                    "no remembered ordinary gateway session in this directory"
                )
            action = "resume"
        elif identifier is not None:
            action = "resume"
        if action == "resume" and args.direct_model is not None:
            # Cross-profile relaunch swaps the scope's fence/compaction policy:
            # surface the same live-process caution a managed transition gates.
            prior_record = runtime.session_store.resolve(identifier)
            new_profile = compiler.direct_context_profile(
                runtime.catalog.docs, args.direct_model
            )
            if new_profile != prior_record["context_profile"]:
                output_stream.write(
                    f"note: cross-profile relaunch ({prior_record['context_profile']}"
                    f" -> {new_profile}) replaces the session scope before the new "
                    "process starts; make sure the previous process has exited.\n"
                )
        prepared = runtime.prepare_direct(
            action=action,
            model_id=args.direct_model,
            passthrough=list(passthrough or []),
            session_id=identifier,
        )
        if args.print_launch:
            _print_launch_plan(prepared, output_stream)
            return 0
        return runtime.perform(
            prepared,
            resume_decision="force" if getattr(args, "force", False) else None,
        )

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
            record = runtime.session_store.resolve(args.uuid)
            if record.get("pending_forks"):
                output_stream.write(
                    sessions.pending_fork_message(record) + "\n"
                )
            elif record.get("identity_state") == sessions.IDENTITY_REPAIR_NEEDED:
                output_stream.write(sessions.relink_message(record) + "\n")
            output_stream.write(strict_json.canonical_file_bytes(record).decode("utf-8"))
            return 0
        if command == "forget":
            try:
                record = runtime.session_store.resolve(args.uuid)
            except sessions.SessionError as exc:
                if "no managed session matches" not in str(exc):
                    raise
                output_stream.write(f"Not found: {args.uuid}\n")
                return 0
            stable_id = sessions.managed_id(record)
            removed, scope_removed = runtime.session_store.forget_session(stable_id)
            if not removed:
                output_stream.write(f"Not found: {args.uuid}\n")
                return 0
            output_stream.write(f"Forgot: {stable_id}\n")
            output_stream.write(
                "Deleted: session record + generated scope"
                + ("" if scope_removed else " (no generated scope existed)")
                + ". Transcripts are never touched.\n"
            )
            return 0
        if command == "relink-runtime":
            if not sessions.UUID4.fullmatch(args.runtime_uuid):
                raise CLIError(f"{args.runtime_uuid!r} is not a UUIDv4")
            record = runtime.session_store.resolve(args.uuid)
            stable_id = sessions.managed_id(record)
            repaired_cwd: str | None = None
            if args.repair_cwd is not None:
                repaired_cwd = str(Path(args.repair_cwd).resolve())
                if not Path(repaired_cwd).is_dir():
                    raise CLIError(
                        f"repair CWD {repaired_cwd!r} is not an accessible directory"
                    )
            updated = runtime.session_store.relink_runtime(
                stable_id,
                observed_runtime_id=args.runtime_uuid,
                cwd=repaired_cwd,
            )
            if updated.get("mode") == "durable":
                try:
                    _doctor_repair(runtime, stable_id, io.StringIO())
                except CLIError as exc:
                    output_stream.write(
                        f"Reconciled managed {stable_id} to runtime "
                        f"{updated['runtime_session_id']}, but scope reconverge "
                        f"failed: {tui.visible_message(exc)}; run "
                        f"`claude-multi doctor --repair {stable_id}`\n"
                    )
                    return 1
            output_stream.write(
                f"Reconciled managed {stable_id} to runtime "
                f"{updated['runtime_session_id']} at CWD {updated['cwd']}.\n"
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
        if command == "resolve-fork":
            record = runtime.session_store.resolve(args.uuid)
            stable_id = sessions.managed_id(record)
            try:
                updated = runtime.session_store.resolve_fork(stable_id, args.fork_uuid)
            except sessions.SessionError as exc:
                raise CLIError(str(exc)) from exc
            output_stream.write(
                f"Resolved fork {args.fork_uuid} on session {stable_id}: the "
                "marker is discarded, the fork transcript stays on disk as a "
                "native session (adopt it later with `claude-multi sessions "
                f"link {args.fork_uuid} --composition NAME` if you ever need "
                "it). Identity is now "
                f"{updated.get('identity_state', sessions.IDENTITY_UNVERIFIED)}.\n"
            )
            return 0
        if command == "stop":
            record = runtime.session_store.resolve(args.uuid)
            stable_id = sessions.managed_id(record)
            runtime_id = sessions.runtime_session_id(record)
            refusal = _stop_precheck(runtime, record)
            if refusal is not None:
                output_stream.write(f"session {stable_id}: {refusal}.\n")
                return 0
            if not args.yes:
                if not interactive:
                    raise CLIError(
                        "sessions stop requires --yes when non-interactive"
                    )
                output_stream.write(
                    f"Stop live background session {stable_id} (runtime "
                    f"{runtime_id}) with upstream `claude stop`? The "
                    "conversation is always kept. [y/N] "
                )
                output_stream.flush()
                answer = (input_stream.readline() or "").strip().lower()
                if answer not in ("y", "yes"):
                    output_stream.write("Stop cancelled.\n")
                    return 0
            problem = _stop_runtime(runtime, runtime_id)
            if problem is not None:
                raise CLIError(
                    f"upstream stop failed for {runtime_id}: {problem}"
                )
            output_stream.write(
                f"Stopped session {stable_id} (runtime {runtime_id}). The "
                "conversation is kept — resume it with `claude-multi -r "
                f"{stable_id}` when ready.\n"
            )
            return 0
        if command == "link":
            if args.uuid is None:
                output_stream.write(
                    "Only sessions launched through claude-multi (or adopted) appear in\n"
                    "the sessions list. To adopt a native/plain-Claude session:\n"
                    "  managed: claude-multi sessions link <uuid> --composition NAME\n"
                    "  ordinary: claude-multi sessions link <uuid> --model MODEL\n"
                    "Add `--cwd PATH` when native project-slug decoding is ambiguous.\n"
                )
                return 0
            if not sessions.UUID4.fullmatch(args.uuid):
                raise CLIError(f"{args.uuid!r} is not a UUIDv4")
            if args.link_model is not None:
                profile = compiler.direct_context_profile(
                    runtime.catalog.docs, args.link_model
                )
                adopted_cwd = _original_cwd_for_adopt(
                    runtime, args.uuid, explicit_cwd=args.link_cwd
                )
                stable_id = runtime.session_store.new_id()
                record = sessions.make_ordinary_record(
                    managed_id=stable_id,
                    runtime_session_id=args.uuid,
                    cwd=adopted_cwd,
                    model=args.link_model,
                    context_profile=profile,
                    catalog_version=runtime.catalog_version,
                    catalog_hash=runtime.catalog.bundle_sha256,
                    launcher_version=runtime.launcher_version,
                    identity_state=sessions.IDENTITY_AUTHORITATIVE,
                    mode="legacy",
                    scope_generation=0,
                )
                runtime.session_store.link(record)
                output_stream.write(
                    f"Linked runtime {args.uuid} as ordinary {stable_id} with "
                    f"model {args.link_model!r} in profile {profile!r}.\n"
                )
                return 0

            name = args.link_composition
            if name is None:
                if not interactive:
                    raise CLIError(
                        "sessions link without a TTY requires --composition NAME "
                        "or --model MODEL"
                    )
                document, _ = remembered_document(runtime)
            else:
                document = runtime.compositions.load(name)
            adopted_cwd = _original_cwd_for_adopt(
                runtime, args.uuid, explicit_cwd=args.link_cwd
            )
            stable_id = runtime.session_store.new_id()
            resolved = runtime.resolve_document(document)
            record = sessions.make_record(
                managed_id=stable_id,
                runtime_session_id=args.uuid,
                cwd=adopted_cwd,
                composition_name=document["name"],
                snapshot=composition.snapshot(resolved),
                catalog_version=runtime.catalog_version,
                catalog_hash=runtime.catalog.bundle_sha256,
                launcher_version=runtime.launcher_version,
                identity_state=sessions.IDENTITY_AUTHORITATIVE,
            )
            runtime.session_store.link(record)
            output_stream.write(
                f"Linked runtime {args.uuid} as managed {stable_id} to "
                f"composition {document['name']!r}.\n"
            )
            return 0

    if args.command == "update":
        from . import upgrade as upgrade_mod

        environ_repo = runtime.environ.get("CLAUDE_MULTI_SOURCE_REPO")
        source_repo = Path(
            environ_repo
            or (Path(runtime.environ.get("HOME", str(Path.home()))) / "personal" / "nixos-dotfiles")
        )
        try:
            outcome = upgrade_mod.run_upgrade(
                checkout_root=source_repo / "home-manager" / "claude-multi",
                native_contract=runtime.catalog.docs["native-contract"],
                override_path=sessions.config_root(runtime.environ) / "native-contract.json",
                today=sessions._now()[:10],
                activate=bool(args.activate),
                packaged_contract=_packaged_contract(runtime),
                override_broken=runtime.broken_override_error is not None,
                progress=lambda line: (
                    output_stream.write(f"… {tui.visible_text(line)}\n"),
                    output_stream.flush(),
                ),
            )
        except upgrade_mod.UpgradeError as exc:
            raise CLIError(str(exc)) from exc
        except KeyboardInterrupt:
            raise CLIError(
                "update interrupted (Ctrl-C) — check `claude-multi doctor` "
                "for the effective pin state"
            ) from None
        except OSError as exc:
            raise CLIError(f"update failed: {exc}") from exc
        for line in outcome.messages:
            output_stream.write(f"{tui.visible_text(line)}\n")
        return 0

    if args.command == "models":
        for model_id, model in sorted(runtime.catalog.models.items()):
            provider = runtime.catalog.providers[model["provider"]]["display"]
            capabilities = ",".join(model["capabilities"])
            context = model["context"]
            scalar = context["scalar_tokens"] or "none"
            profile = context["ordinary_profile"] or "agents-only"
            output_stream.write(
                f"{model_id}\t{model['display']}\t{provider}\t{capabilities}\t"
                f"client={context['client_tokens']} provider={context['provider_tokens']} "
                f"scalar={scalar} profile={profile}\n"
            )
        return 0

    if args.command == "show":
        name = args.show_composition or "default"
        _print_composition(runtime, runtime.compositions.load(name), output_stream)
        return 0

    if args.command == "doctor":
        if args.doctor_repair is not None:
            return _doctor_repair(runtime, args.doctor_repair, output_stream)
        if args.doctor_repair_all:
            return _doctor_repair_all(runtime, output_stream)
        if args.doctor_prune:
            return _doctor_prune(runtime, output_stream)
        problems, info_lines, scope_attention = _collect_doctor_reports(runtime)
        # Badge styling only when color is active (a tty, not NO_COLOR, not
        # --no-color); the line contract itself never changes (UX §5/§8).
        palette = _output_palette(input_stream, output_stream, no_color)
        if problems:
            output_stream.write(palette.ansi("BLOCKED", "error") + "\n")
            for problem in problems:
                output_stream.write(
                    palette.ansi(f"  - {tui.visible_text(problem)}", "error") + "\n"
                )
            if scope_attention:
                output_stream.write(palette.ansi("Attention", "warn") + "\n")
                for line in scope_attention:
                    output_stream.write(
                        palette.ansi(f"  - {tui.visible_text(line)}", "warn") + "\n"
                    )
            for line in info_lines:
                output_stream.write(f"{tui.visible_text(line)}\n")
            return 1
        if scope_attention:
            output_stream.write(palette.ansi("Ready", "ok") + "\n")
            output_stream.write(palette.ansi("Attention", "warn") + "\n")
            for line in scope_attention:
                output_stream.write(
                    palette.ansi(f"  - {tui.visible_text(line)}", "warn") + "\n"
                )
            for line in info_lines:
                output_stream.write(f"{tui.visible_text(line)}\n")
            output_stream.write(
                "Catalog, compositions, and local gateway are valid; attention "
                "items are by-design lazy state, not damage.\n"
            )
            return 0
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

    session_id = sessions.managed_id(record)
    short = f"{session_id[:8]}…"
    generation = record["scope_generation"]
    repair = (
        f"Run claude-multi doctor --repair {session_id} to regenerate "
        "them from the record."
    )
    try:
        if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
            expected = scope_mod.compile_ordinary_scope(
                managed_id=session_id,
                hook_command=runtime.hook_command,
                available_models=compiler.direct_profile_selectors(
                    runtime.catalog.docs, record["context_profile"]
                ),
                default_model=runtime.catalog.models[record["ordinary_model"]][
                    "client_selector"
                ],
                launch_epoch=record.get("launch_epoch", 0),
                gateway_base_url=runtime.catalog.docs["gateway"]["gateway"][
                    "base_url"
                ],
                token_helper_command=runtime.token_helper_command,
            )
        else:
            resolved = runtime.resolve_document(snapshot_to_document(record))
            expected = scope_mod.compile_scope(
                resolved,
                runtime.catalog.docs["roles"]["roles"],
                runtime.catalog.prompt_bodies,
                scope_mod.catalog_meta_from_docs(runtime.catalog.docs),
                managed_id=session_id,
                hook_command=runtime.hook_command,
                launch_epoch=record.get("launch_epoch", 0),
                token_helper_command=runtime.token_helper_command,
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


def _doctor_scope_report(runtime: Runtime) -> tuple[list[str], list[str], list[str]]:
    """Session census plus per-durable-session scope integrity (SPEC §7.1).

    Returns ``(info, problems, attention)``: problems block (something is
    actually broken — unreadable records, identity damage, missing/mismatched
    scopes); attention lines describe by-design lazy state that one command
    clears (legacy context snapshots, legacy-mode records).
    """

    records, record_problems, record_ids = _session_record_scan(runtime)
    durable = [record for record in records if record["mode"] == "durable"]
    legacy = len(records) - len(durable)
    unreadable = len(record_problems)
    suffix = f" · {unreadable} unreadable" if unreadable else ""
    info = [
        f"Sessions: {len(record_ids)} recorded · {len(durable)} durable · "
        f"{legacy} legacy{suffix}."
    ]
    problems: list[str] = list(record_problems)
    attention: list[str] = []
    if legacy:
        attention.append(
            f"{legacy} legacy (pre-durable) record(s) upgrade on resume: "
            "resume each once, or `sessions forget` the ones you no longer need"
        )
    for record in records:
        identity = _record_identity_label(record)
        state_label = record.get("identity_state", sessions.IDENTITY_UNVERIFIED)
        if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
            try:
                _scalar, window, trigger = compiler.direct_profile_context(
                    runtime.catalog.docs, record["context_profile"]
                )
                target = (
                    f"ordinary model {record['ordinary_model']} · profile "
                    f"{record['context_profile']} · compact capacity {window} / "
                    f"reactive trigger {trigger}"
                )
            except compiler.CompilerError:
                # A catalog update that renamed/removed the profile is exactly
                # the drift doctor exists to report — never abort the run.
                target = (
                    f"ordinary model {record['ordinary_model']} · profile "
                    f"{record['context_profile']} (no longer in the installed catalog)"
                )
                problems.append(
                    f"session {sessions.managed_id(record)} records ordinary "
                    f"profile {record['context_profile']!r}, which the installed "
                    "catalog no longer provides; resume it with an explicit "
                    "supported `--model` to re-pin its profile"
                )
        else:
            snapshot = record["snapshot"]
            lead = snapshot["lead"]
            target = (
                f"composition {record['composition_name']} · lead {lead['model']} · "
                f"context {lead.get('client_context_tokens', 'legacy')} · compact "
                f"capacity {snapshot.get('auto_compact_window_tokens', 'legacy')} / "
                f"reactive trigger {lead.get('auto_compact_tokens', 'legacy')}"
            )
            missing_context = [
                key
                for key in (
                    "client_context_tokens",
                    "provider_context_tokens",
                    "auto_compact_tokens",
                )
                if key not in lead
            ]
            if "auto_compact_window_tokens" not in snapshot:
                missing_context.append("auto_compact_window_tokens")
            if missing_context and record["mode"] == "durable":
                attention.append(
                    f"session {sessions.managed_id(record)} has a legacy context "
                    f"snapshot missing {', '.join(missing_context)}; "
                    "`claude-multi doctor --repair-all` refreshes it in place "
                    "(or resume/transition it)"
                )
        info.append(
            f"Session {identity}: {target} · identity {state_label} · cwd {record['cwd']}."
        )
        if state_label in {
            sessions.IDENTITY_REPAIR_NEEDED,
            sessions.IDENTITY_PENDING_FORK,
        }:
            if sessions.drop_resolved_pending_forks(record) is not None:
                attention.append(
                    f"session {sessions.managed_id(record)} holds a fork marker "
                    "its live runtime already resolved; `claude-multi doctor "
                    "--repair-all` clears it in place"
                )
            elif state_label == sessions.IDENTITY_PENDING_FORK:
                problems.append(
                    f"session {sessions.managed_id(record)} identity is "
                    f"{state_label}; {sessions.pending_fork_message(record)}"
                )
            elif state_label == sessions.IDENTITY_REPAIR_NEEDED:
                problems.append(sessions.relink_message(record))
            else:
                problems.append(
                    f"session {sessions.managed_id(record)} identity is "
                    f"{state_label}; inspect `claude-multi sessions show` and "
                    "resume/adopt through the supported launcher before relying "
                    "on it"
                )
    scope_mismatch = 0
    for record in durable:
        line, is_problem = _check_scope_integrity(runtime, record)
        if is_problem:
            scope_mismatch += 1
            problems.append(line)
        else:
            info.append(line)
    if scope_mismatch:
        problems.append(
            f"{scope_mismatch} scope(s) diverged from record authority; run "
            "`claude-multi doctor --repair-all` to converge them in one pass"
        )
    return info, problems, attention


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
    if os.path.lexists(scopes_root):
        state.ensure_private_dir(scopes_root)
        for entry in sorted(scopes_root.iterdir()):
            name = entry.name
            session_id: str | None = None
            staging = False
            staging_suffix: str | None = None
            if name.startswith("."):
                stem = name[1:]
                for suffix in (".new", ".prev"):
                    candidate = stem[: -len(suffix)] if stem.endswith(suffix) else ""
                    if candidate and sessions.UUID4.fullmatch(candidate):
                        session_id = candidate
                        staging = True
                        staging_suffix = suffix
                        break
            elif sessions.UUID4.fullmatch(name):
                session_id = name
            if session_id is None:
                continue
            lock = store.lifecycle_lock(session_id)
            lock.acquire(blocking=True)
            try:
                if not os.path.lexists(entry):
                    continue
                if staging:
                    if staging_suffix == ".prev" and store.exists(session_id):
                        # Retain rollback state until the session is forgotten;
                        # there is no separate post-exec success signal.
                        continue
                    _remove_scope_tree(entry)
                    removed.append(f"stale staging dir scopes/{name}")
                elif not store.exists(session_id):
                    scope_mod.remove_scope(store.root, session_id)
                    removed.append(f"scope for forgotten session {session_id}")
            finally:
                lock.release()
    # Generated per-session files outside scopes/ follow the same rule:
    # pruned only when their record is gone (never a living session's).
    for entry in sorted(store.root.glob("lead-prompt-*-*.md")):
        stem = entry.name.removeprefix("lead-prompt-").removesuffix(".md")
        # lead-prompt-<digest16>-<uuid>: the UUID is the LAST 36 chars.
        session_id = stem[-36:]
        if not sessions.UUID4.fullmatch(session_id):
            continue
        lock = store.lifecycle_lock(session_id)
        lock.acquire(blocking=True)
        try:
            if entry.exists() and not store.exists(session_id):
                state.remove_private(entry)
                removed.append(f"lead prompt for forgotten session {session_id}")
        finally:
            lock.release()
    # Pre-2.2 lead prompts were named lead-prompt-<digest>.md with no session
    # suffix; the launcher has not written that form since, so every such
    # file is an orphan by construction.
    for entry in sorted(store.root.glob("lead-prompt-*.md")):
        stem = entry.name.removeprefix("lead-prompt-").removesuffix(".md")
        if "-" in stem:
            continue  # session-suffixed form, handled above
        if entry.exists():
            state.remove_private(entry)
            removed.append(f"orphaned pre-2.2 lead prompt {entry.name}")
    # Lifecycle lock files are permanent synchronization identities. Unlinking
    # one while another process holds its inode would create two independent
    # locks for the same session.
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
    record = runtime.session_store.resolve(uuid)
    stable_id = sessions.managed_id(record)
    transition = _transition_module()
    try:
        fork_converged = runtime.session_store.converge_pending_forks(stable_id)
        report = transition.converge(
            runtime.session_store.root,
            runtime.session_store,
            stable_id,
            runtime.catalog,
        )
    except transition.TransitionError as exc:
        raise CLIError(str(exc)) from exc
    if fork_converged:
        output_stream.write(
            "cleared a fork marker the live runtime already resolved\n"
        )
    for line in report:
        output_stream.write(f"{tui.visible_text(line)}\n")
    return 0


def _doctor_repair_all(runtime: Runtime, output_stream: TextIO) -> int:
    """Bulk converge: every durable record repaired under its lifecycle lock.

    Legacy (pre-durable) records are reported, not touched — a resume upgrades
    them. Unreadable records and per-session failures never stop the pass;
    each is reported and the pass continues. Returns 1 when anything failed.
    """

    records, record_problems, _ids = _session_record_scan(runtime)
    transition = _transition_module()
    failures: list[str] = list(record_problems)
    skipped: list[str] = []
    repaired = 0
    for record in records:
        stable_id = sessions.managed_id(record)
        if record["mode"] != "durable":
            skipped.append(
                f"{stable_id}: legacy record — resume it once to upgrade "
                f"(claude-multi -r {stable_id})"
            )
            continue
        try:
            fork_converged = runtime.session_store.converge_pending_forks(stable_id)
            report = transition.converge(
                runtime.session_store.root,
                runtime.session_store,
                stable_id,
                runtime.catalog,
            )
        except (
            transition.TransitionError,
            sessions.SessionError,
            composition.CompositionError,
            scope_mod.ScopeError,
            state.StateError,
        ) as exc:
            failures.append(f"{stable_id}: {exc}")
            continue
        repaired += 1
        if fork_converged:
            output_stream.write(
                f"{stable_id[:8]}… cleared a fork marker the live runtime "
                "already resolved\n"
            )
        for line in report:
            output_stream.write(f"{stable_id[:8]}… {tui.visible_text(line)}\n")
    output_stream.write(
        f"Repair-all: {repaired} durable session(s) converged to record "
        "authority.\n"
    )
    for line in skipped:
        output_stream.write(f"  - skipped {tui.visible_text(line)}\n")
    for line in failures:
        output_stream.write(f"  - FAILED {tui.visible_text(line)}\n")
    if failures:
        return 1
    output_stream.write(
        "Re-run `claude-multi doctor` to confirm; `doctor --prune` collects "
        "retained .prev generations afterwards.\n"
    )
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
        if record["session_type"] == sessions.SESSION_TYPE_MANAGED
        and record["composition_name"] == name
    ]
    matches.sort(key=lambda record: record["created_at"], reverse=True)
    if len(matches) == 1:
        return sessions.managed_id(matches[0])
    if matches:
        lines = [
            f"resume name {value!r} matches {len(matches)} managed sessions; "
            "resume an exact UUID instead:"
        ]
        for record in matches:
            lines.append(
                f"  {sessions.managed_id(record)}  "
                f"{tui.visible_text(record['composition_name'])}  "
                f"{_record_mode_label(record)}  "
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
                name=composition_name, uuid=sessions.managed_id(record)
            )
        )


# Commands whose output is a report: honor stdout redirection/pipes instead
# of writing to the controlling terminal. Interactive-first commands (compose
# editor, sessions transition, direct, bare launch) keep the tty stream.
_STDOUT_REPORT_COMMANDS = frozenset(
    {
        ("doctor", None),
        ("models", None),
        ("show", None),
        ("update", None),
        ("session-event", None),
        ("compose", "list"),
        ("compose", "show"),
        ("compose", "delete"),
        ("compose", "duplicate"),
        ("compose", "rename"),
        ("compose", "restore-default"),
        ("compose", "use-as-template"),
        ("sessions", "list"),
        ("sessions", "show"),
        ("sessions", "forget"),
        ("sessions", "link"),
        ("sessions", "relink-runtime"),
    }
)


def _report_output_stream(args: argparse.Namespace, tty_stream: TextIO, std_stream: TextIO) -> TextIO:
    """stdout for report commands; the tty stream for interactive ones."""

    if args.command == "direct" and getattr(args, "print_launch", False):
        # --print-launch is a pure report even on the interactive direct path.
        return std_stream
    sub = getattr(args, "compose_command", None) or getattr(
        args, "sessions_command", None
    )
    if (args.command, sub) in _STDOUT_REPORT_COMMANDS:
        return std_stream
    return tty_stream


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
        if args.command == "session-event" and interactive is None:
            # Hooks deliver JSON on stdin even when Claude itself owns a TTY.
            # Never open /dev/tty for this internal command or it can block
            # waiting for terminal input instead of consuming the event pipe.
            interactive = False
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
            if passthrough and args.command != "direct":
                raise CLIError("passthrough arguments are accepted only for launch")
            return handle_command(
                runtime,
                args,
                input_stream=inp,
                output_stream=_report_output_stream(args, tty_output, output_stream or output),
                interactive=bool(interactive),
                no_color=args.no_color,
                passthrough=passthrough,
            )

        if args.resume == "":
            # Bare -r: open the sessions picker (or print it when piped).
            namespace = argparse.Namespace(line=args.line)
            if (
                interactive
                and not args.line
                and tui.streams_curses_capable(inp, tty_output)
            ):
                try:
                    return _sessions_list_tui(
                        runtime,
                        namespace,
                        input_stream=inp,
                        output_stream=tty_output,
                        no_color=args.no_color,
                        passthrough=passthrough,
                        legacy_requested=args.legacy,
                    )
                except KeyboardInterrupt:
                    return 0
                except (curses.error, OSError):
                    pass
            _print_sessions_listing(runtime, output_stream or output)
            return 0

        if args.resume:
            session_id = _resolve_resume_target(runtime, args.resume)
            record = runtime.session_store.resolve(session_id)
            if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
                raise CLIError(
                    "this is an ordinary gateway session; resume it with "
                    "`claude-gateway --resume ID` or `claude-multi direct --resume ID`"
                )
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
            if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
                raise CLIError(
                    "the remembered session is an ordinary gateway session; use "
                    "`claude-gateway --continue` or `claude-multi direct --continue`"
                )
            _refuse_resume_override(args.composition, record)
            plan = managed_plan(runtime, record)
            plan.legacy_requested = args.legacy
        else:
            # Only a FRESH launch can need an explicit composition: resume and
            # continue take the recorded intent, so they must never demand one.
            if not interactive and args.composition is None:
                raise CLIError(
                    "noninteractive launch requires --composition NAME; no default was selected"
                )
            if args.composition is not None:
                document = runtime.compositions.load(args.composition)
                source = "Explicit composition"
            else:
                document, source = remembered_document(runtime)
            plan = build_quick_plan(runtime, document, action="fresh", source=source)
            plan.legacy_requested = args.legacy

        if args.print_launch:
            if not plan.ready:
                raise CLIError("composition is blocked: " + "; ".join(plan.errors))
            prepared = runtime.prepare(
                plan.document,
                action=plan.action,
                passthrough=passthrough,
                session_id=sessions.managed_id(plan.record) if plan.record else None,
                legacy_requested=plan.legacy_requested,
            )
            _print_launch_plan(prepared, output_stream or output)
            return 0

        if not interactive:
            if not plan.ready:
                raise CLIError("composition is blocked: " + "; ".join(plan.errors))
            prepared = runtime.prepare(
                plan.document,
                action=plan.action,
                passthrough=passthrough,
                session_id=sessions.managed_id(plan.record) if plan.record else None,
                legacy_requested=plan.legacy_requested,
            )
            return runtime.perform(prepared, resume_decision="force" if args.force else None)

        update_hint = launch.repin_hint(runtime.catalog.docs["native-contract"])
        gateway_problem: str | None = None
        gateway_checked = False
        if interactive:
            # One loopback health check per card open (never per redraw).
            try:
                launch.check_readiness(runtime.catalog.docs["gateway"])
                gateway_checked = True
            except launch.LaunchError as exc:
                gateway_problem = str(exc)
                gateway_checked = True
        return quick_confirm(
            runtime,
            plan,
            input_stream=inp,
            output_stream=tty_output,
            passthrough=passthrough,
            force_line=args.line,
            no_color=args.no_color,
            update_hint=update_hint,
            gateway_problem=gateway_problem,
            gateway_checked=gateway_checked,
            resume_decision="force" if args.force else None,
        )
    except (CLIError, sessions.SessionError, state.StateError, catalog.CatalogError, compiler.CompilerError, composition.CompositionError, launch.LaunchError) as exc:
        output.write(f"claude-multi: {tui.visible_message(exc)}\n")
        output.flush()
        return 2
    finally:
        if owned_tty is not None:
            for handle in owned_tty:
                handle.close()


def _js_utf16_units(text: str) -> list[int]:
    raw = text.encode("utf-16-le", errors="surrogatepass")
    return [raw[i] | (raw[i + 1] << 8) for i in range(0, len(raw), 2)]


def _base36(value: int) -> str:
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value == 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = digits[remainder] + result
    return result


def _sanitize_native_slug(text: str) -> tuple[str, list[int]]:
    units = _js_utf16_units(text)
    sanitized = "".join(
        chr(unit)
        if (
            0x30 <= unit <= 0x39
            or 0x41 <= unit <= 0x5A
            or 0x61 <= unit <= 0x7A
        )
        else "-"
        for unit in units
    )
    return sanitized, units


def _native_project_slug(path: Path | str) -> str:
    """Exact Claude 2.1.217 project-directory slug for one path."""

    text = str(path)
    sanitized, units = _sanitize_native_slug(text)
    if len(sanitized) <= 200:
        return sanitized
    hashed = 0
    for unit in units:
        hashed = (hashed * 31 + unit) & 0xFFFFFFFF
    if hashed & 0x80000000:
        hashed -= 0x100000000
    return f"{sanitized[:200]}-{_base36(abs(hashed))}"


def _native_slug_component(name: str) -> str:
    return _sanitize_native_slug(name)[0]


def _slugs_for_session(runtime: Runtime, session_id: str) -> tuple[str, ...]:
    """All projects-dir slugs containing a native UUID, names only."""

    home = Path(runtime.environ.get("HOME") or Path.home())
    projects = home / ".claude" / "projects"
    found: set[str] = set()
    try:
        for project_dir in projects.iterdir():
            if not project_dir.is_dir():
                continue
            if (project_dir / f"{session_id}.jsonl").exists():
                found.add(project_dir.name)
    except OSError:
        return ()
    return tuple(sorted(found))


def _decode_project_slug_candidates(slug: str) -> tuple[Path, ...]:
    """Return every existing directory represented by a non-injective slug."""

    if not slug.startswith("-"):
        return ()
    candidates: list[tuple[Path, str]] = [(Path("/"), slug[1:])]
    finals: set[Path] = set()
    for _depth in range(24):
        following: list[tuple[Path, str]] = []
        for base, remaining in candidates:
            if remaining == "":
                finals.add(base)
                continue
            try:
                children = [child for child in base.iterdir() if child.is_dir()]
            except OSError:
                continue
            for child in children:
                encoded = _native_slug_component(child.name)
                if remaining == encoded:
                    following.append((child, ""))
                elif remaining.startswith(encoded + "-"):
                    following.append((child, remaining[len(encoded) + 1 :]))
        if not following:
            break
        candidates = list(dict.fromkeys(following))[:64]
    finals.update(path for path, remaining in candidates if remaining == "")
    return tuple(sorted(finals, key=str))


def _decode_project_slug(slug: str) -> Path | None:
    """Compatibility helper: decode only when exactly one directory matches."""

    candidates = _decode_project_slug_candidates(slug)
    return candidates[0] if len(candidates) == 1 else None


def _original_cwd_for_adopt(
    runtime: Runtime, session_id: str, explicit_cwd: str | None = None
) -> str:
    """Resolve exactly one original CWD or fail closed with repair guidance."""

    slugs = _slugs_for_session(runtime, session_id)
    if not slugs:
        raise CLIError(
            f"cannot locate native session {session_id} in local Claude project "
            "metadata; open it natively on this machine before adoption"
        )
    if explicit_cwd is not None:
        chosen = Path(explicit_cwd).resolve()
        if not chosen.is_dir():
            raise CLIError(
                f"adoption CWD {str(chosen)!r} is not an accessible directory"
            )
        encoded = _native_project_slug(chosen)
        if encoded not in slugs:
            raise CLIError(
                f"adoption CWD {str(chosen)!r} maps to project slug {encoded!r}, "
                f"which does not contain native session {session_id}; observed "
                f"slugs: {slugs!r}"
            )
        return str(chosen)
    current = Path(runtime.cwd)
    if _native_project_slug(current) in slugs:
        return str(current)
    candidates = {
        path for slug in slugs for path in _decode_project_slug_candidates(slug)
    }
    if current in candidates:
        return str(current)
    if len(candidates) == 1:
        return str(next(iter(candidates)))
    if not candidates:
        raise CLIError(
            f"cannot decode the project directory for session slugs {slugs!r}; "
            "cd into the session's original project and adopt again"
        )
    choices = ", ".join(str(path) for path in sorted(candidates, key=str))
    raise CLIError(
        f"session {session_id} has ambiguous project directories: {choices}; "
        "cd into the exact original project and adopt again"
    )


if __name__ == "__main__":
    raise SystemExit(main())
