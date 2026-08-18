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
import shlex
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
    custom,
    launch,
    proxy as proxy_mod,
    render,
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
        # Durable delete: same safety checks as any state write, plus the
        # directory fsync so a crash cannot resurrect the composition.
        return state.remove_private(path)

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
            # Crash window (accepted, compose-lane): both names may exist
            # after an interrupted rename; deleting one by hand converges.
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
        doctor_served_callback: Callable[
            ["Runtime", str], tuple[list[str], list[str]]
        ] | None = None,
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
        # None = built-in served-selector cross-check (015); tests inject a
        # stub here (or a doctor_callback, which skips the whole branch).
        self.doctor_served_callback = doctor_served_callback

    @property
    def launcher_version(self) -> str:
        return self.catalog.docs["version"]["launcher_version"]

    def custom_conflicts(self) -> list[str]:
        """Custom-registry ids shadowing the catalog (dropped by the merge)."""

        return custom.merge_conflicts(
            self.catalog.docs, custom.load_registry(self.environ)
        )

    @property
    def ordinary_docs(self) -> dict[str, Any]:
        """Catalog docs + the custom registry merged (020).

        The ONLY docs view the ordinary-gateway paths may use (picker,
        prepare_direct, listings, profile math, ordinary scope recompile).
        Managed composition resolution keeps using ``catalog.docs`` —
        customs are never composition-eligible. Computed per access: the
        registry is a small 0600 file and never cached stale.
        """

        return custom.merge_docs(self.catalog.docs, custom.load_registry(self.environ))

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
            session_cwd=(
                prior_record["cwd"] if prior_record is not None else self.cwd
            ),
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
                    # Single message source (002 review): the ordinary-aware
                    # helper names the same explicit relaunch.
                    raise CLIError(sessions.relink_message(prior))
                model_relaunch = True
            runtime_id = sessions.runtime_session_id(prior)
            selected_model = model_id or prior["ordinary_model"]
            session_action = compiler.build_resume(stable_id, runtime_id)
        else:
            raise CLIError(f"unknown direct launch action {action!r}")

        launch_epoch = 1 if prior is None else prior.get("launch_epoch", 0) + 1
        profile = compiler.direct_context_profile(self.ordinary_docs, selected_model)
        scope_dir = scope_mod.scope_dir(self.session_store.root, stable_id)
        result = compiler.compile_direct_launch(
            docs=self.ordinary_docs,
            session_action=session_action,
            model_id=selected_model,
            passthrough=passthrough,
            scope_dir=scope_dir,
            hook_command=self.hook_command,
            state_root=self.session_store.root,
            pin_model=pin_model,
            launch_epoch=launch_epoch,
            token_helper_command=self.token_helper_command,
            session_cwd=prior["cwd"] if prior is not None else self.cwd,
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
        self._enforce_resume_gate(prepared, resume_decision)
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
        skip ONLY the daemon-owned branch too (their flow already warns
        on live sessions); identity and transcript damage still refuse.
        """

        action = prepared.result.session_action
        if action.kind != "resume" or not prepared.record:
            return
        gate = _evaluate_resume_gate(
            self, prepared.record, live_prefixes=self._live_prefixes()
        )
        if gate.kind == "ok":
            return
        # Precommitted transition relaunches skip ONLY the liveness branch
        # (the transition flow already warns on live sessions — verifier
        # nuance); repair and transcript damage are always enforced
        # (review must-fix 2).
        if gate.kind == "daemon-owned" and (
            resume_decision == "force" or prepared.precommitted
        ):
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
    composition_group = parser.add_mutually_exclusive_group()
    composition_group.add_argument("--composition", metavar="NAME", help="use a named composition")
    composition_group.add_argument("--composition-file", metavar="PATH|-", help="launch one unsaved composition JSON; '-' reads stdin and forces noninteractive; resume/continue reject file overrides; nothing is saved")
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
    direct_parser.add_argument("--model", dest="direct_model", help="catalog or custom-registry model id (see `claude-multi models` and `claude-multi custom list`)")
    direct_parser.add_argument("--force", action="store_true", default=argparse.SUPPRESS, help="resume despite a background-liveness marker (only bypasses the heuristic ● check)")
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
    compose_commands.add_parser("list", help="list compositions, most-recently-used first, with last-used age")

    custom_parser = commands.add_parser(
        "custom", help="manage custom providers/models (the ordinary-session registry)"
    )
    custom_commands = custom_parser.add_subparsers(dest="custom_command", required=True)
    custom_commands.add_parser("list", help="list custom providers and models")
    add_provider = custom_commands.add_parser(
        "add-provider", help="register an Anthropic-compatible endpoint"
    )
    add_provider.add_argument("name")
    add_provider.add_argument("--base-url", required=True)
    add_provider.add_argument("--auth", choices=("bearer", "header"), required=True)
    add_provider.add_argument("--header", default=None, help="header name when --auth header")
    add_provider.add_argument("--secret-env", required=True, help="env var in the secret file")
    add_provider.add_argument("--display", default=None)
    for action in ("remove-provider", "remove-model"):
        item = custom_commands.add_parser(action, help=f"{action} by name")
        item.add_argument("name")
    add_model = custom_commands.add_parser(
        "add-model", help="mark a model into the ordinary registry"
    )
    add_model.add_argument("name")
    add_model.add_argument("--provider", required=True)
    add_model.add_argument("--wire", required=True, help="the exact id the API expects")
    add_model.add_argument("--context", required=True, type=int, help="context window in tokens")
    add_model.add_argument("--display", default=None)
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

    commands.add_parser("models", help="list catalog models with wire ids and exact typed /model selectors")
    discover_parser = commands.add_parser(
        "discover",
        help="list the models a provider advertises (a provider call — runs only on explicit invocation)",
    )
    discover_parser.add_argument("provider", help="provider id (see `claude-multi models`)")
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
    latest = max(here, key=_record_sort_key_last_used)
    noun = "session" if len(here) == 1 else "sessions"
    return f"{len(here)} {noun} here · latest activity {_record_last_used_age(latest)} · S to pick"


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
    names = composition_pick_order(runtime)
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
        return (f"{primary} · D details · S sessions · G gateway models · ? help · H health{update} · Q cancel",)
    primary = "Enter launch" if plan.ready else "Enter edit"
    return (f"{primary} · E edit · D details · S sessions · G gateway models · ? help · P preset · W wf on/off · H health{update} · Q cancel",)


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
    except state.CommittedStateError as exc:
        # The write COMMITTED but directory durability is unconfirmed
        # (compose-lane P2): reporting "unsaved" would be a lie in the
        # dangerous direction. The source labels the indeterminate state.
        failed = _editor_failure_plan(runtime, plan, outcome.document, exc)
        failed.source = "Committed, durability unconfirmed"
        return failed
    except (CLIError, ValueError, OSError) as exc:
        return _editor_failure_plan(runtime, plan, outcome.document, exc)


QUICK_HELP = (
    "Enter — launch this composition (durable scope).\n"
    "E — edit the composition (form editor; ^G opens the JSON editor there).\n"
    "Tab / Shift-Tab — cycle composition presets (most-recently-used first).\n"
    "W — toggle native workflows on/off for this launch.\n"
    "D — details (scalar, providers, catalog hashes, workers).\n"
    "H — doctor in place (health: Ready / Attention / BLOCKED).\n"
    "U — re-pin Claude when the update badge shows.\n"
    "S — sessions: managed + native picker (resume, switch comp/model, adopt).\n"
    "G — gateway models: pick a model (no composition), browse the full catalog (M), or manage providers (P).\n"
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
        resume_decision: str | None = None,
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
        self.resume_decision = resume_decision
        self.gate_notice: str | None = None
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
        if plan.project_agent_count or plan.project_collisions:
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
                f"gateway unreachable — launches will fail: {self.gateway_problem} "
                "— start: `systemctl --user start cli-proxy-api`",
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
            *([self.gate_notice] if self.gate_notice else []),
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
        # A secret problem names a file path; point at the in-TUI remedy
        # (G picker → P providers → masked key entry) so the BLOCKED card
        # is never a dead end.
        if any(
            "secret" in error and "missing" in error for error in errors
        ):
            hint = (
                "fix without leaving the TUI: G (gateway models) → P "
                "(providers) → Enter — masked key entry into the secret env file"
            )
            for line in textwrap.wrap(hint, width=max(32, width - 6)):
                if row >= bottom:
                    break
                tui.safe_add(win, row, 4, line, palette.attr("accent"))
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
                bindings.append(("Tab", "recent preset"))
            bindings.append(("W", "wf on/off"))
        if self.update_hint is not None:
            bindings.append(("U", "update"))
        # ? sits right before Esc: on narrow terminals the KeyBar compacts
        # middle bindings first, and help is the discovery mechanism (D30).
        bindings.extend((("D", "details"), ("S", "sessions"), ("G", "gateway models"), ("H", "health"), ("?", "help"), ("Esc", "cancel")))
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

        # The only heavyweight one-keystroke action: confirm first (a stray
        # U costs minutes of evidence suite).
        confirmed = tui.Modal(
            "Run the Claude update?",
            [
                f"re-pin {self.update_hint[0]} → {self.update_hint[1]} — the "
                "full evidence-gated suite runs first (minutes); the pin "
                "changes only on green.",
            ],
            buttons=(("Run update", True), ("Cancel", False)),
        ).run(win, self.palette, background=self._draw)
        if not confirmed:
            return
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
            if not problems:
                # The same footer the CLI prints: the line that keeps an
                # operator from chasing a by-design attention item.
                footer = "Catalog, compositions, and local gateway are valid"
                footer += (
                    "; attention items are by-design lazy state, not damage.\n"
                    if attention
                    else ".\n"
                )
                self.tty_out.write(footer)
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

    def _open_sessions(self, win: Any) -> tuple | None:
        """Open the sessions picker in place; None means stay on the card.

        Resume maps to ("perform", PreparedLaunch, decision) so the outer
        launcher tears down curses before exec, exactly like Enter. A
        transition request returns ("transition", record, composition_name)
        for the caller to run after teardown. The picker's
        ``legacy_requested`` flag
        follows the originating plan.
        """

        result = _SessionsScreen(
            self.runtime,
            palette=self.palette,
            resume_decision=self.resume_decision,
        ).run(win)
        if result is None:
            return None
        if result[0] == "resume":
            record = result[1]
            decision = result[2] if len(result) > 2 else None
            if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
                # Ordinary records have no composition plan; mirror the
                # standalone picker's branch (review must-fix: managed_plan
                # on an ordinary record raised KeyError). A 4th element is
                # the picked model from a T switch (D48).
                prepared = self.runtime.prepare_direct(
                    action="resume",
                    model_id=(
                        result[3]
                        if len(result) > 3
                        else (
                            record.get("ordinary_model")
                            if "observed_model" in record
                            else None
                        )
                    ),
                    passthrough=self.passthrough,
                    session_id=sessions.managed_id(record),
                )
                return ("perform", prepared, decision)
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

    def _open_ordinary(self, win: Any) -> tuple | None:
        """Open the ordinary gateway picker in place; None means stay.

        A pick maps to ("perform", PreparedLaunch, None) so the outer
        launcher tears down curses before exec, exactly like Enter (D46).
        """

        model_id = _OrdinaryScreen(self.runtime, palette=self.palette).run(win)
        if model_id is None:
            return None
        if isinstance(model_id, tuple):
            # Models-browser E jump: edit this model's Availability scope in
            # the card's composition (fresh plans only — R1 P1 on managed).
            if self.plan.record is not None:
                self._recorded_only_modal(win)
                return None
            self._edit(win, focus_model=model_id[1])
            return None
        try:
            prepared = self.runtime.prepare_direct(
                action="fresh",
                model_id=model_id,
                passthrough=self.passthrough,
            )
        except (CLIError, compiler.CompilerError) as exc:
            # Transient notice, never plan.errors (same contract as the
            # gate modal): the card stays ready and G can be retried.
            self.gate_notice = str(exc)
            return None
        return ("perform", prepared, None)

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

    def _edit(self, win: Any, *, focus_model: str | None = None) -> str | None:
        editor_state = _editor_state_for_plan(self.runtime, self.plan)
        if focus_model is not None:
            editor_state.message = (
                f"set the {focus_model} scope (lead+agents / lead / agents "
                "/ off), then ^O to save or launch"
            )
        screen = tui.FormEditorScreen(
            editor_state,
            palette=self.palette,
            name_taken=self.runtime.compositions.contains,
            initial_row=("avail", focus_model) if focus_model else None,
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

    def run(self, win: Any) -> tuple[str, PreparedLaunch, str | None] | None:
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
            if key.kind == "char" and key.ch.lower() == "g":
                outcome = self._open_ordinary(win)
                if outcome is None:
                    continue
                return outcome
            preset_delta = _cycle_key_delta(key.kind)
            if preset_delta is not None:
                if self.plan.record is None and self.plan.source in (
                    "Unsaved launch",
                    "Unsaved editor changes",
                ):
                    confirmed = tui.Modal(
                        "Discard the unsaved composition edits?",
                        [
                            "Cycling away replaces the unsaved document with a "
                            "saved preset (Save it first via E → ^O to keep it).",
                        ],
                        buttons=(("Cycle away", True), ("Stay", False)),
                    ).run(win, self.palette, background=self._draw)
                    if not confirmed:
                        continue
                cycled = _cycle_preset(self.runtime, self.plan, preset_delta)
                if cycled is self.plan and self.plan.record is not None:
                    # Dead-key feedback: on a recorded session only the
                    # transition engine changes compositions (R1 P1).
                    self.gate_notice = (
                        "preset cycling applies to fresh launches; a recorded "
                        "session changes composition via sessions transition"
                    )
                else:
                    self.plan = cycled
                continue
            if key.kind == "char" and key.ch.lower() == "w":
                toggled = _toggle_workflows(self.runtime, self.plan)
                if toggled is self.plan and self.plan.record is not None:
                    self.gate_notice = (
                        "workflow toggle applies to fresh launches; a recorded "
                        "session keeps the recorded composition"
                    )
                else:
                    self.plan = toggled
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
                and key.ch.lower() in ("r", "c")
            ):
                self._recorded_only_modal(win)
                continue
            if (key.kind == "char" and key.ch.lower() == "e") or (
                key.kind == "enter" and not self.plan.ready
            ):
                if self.plan.record is not None:
                    # A repair-needed record blocks the plan; offer the same
                    # one-keypress repair the picker does instead of only
                    # naming the command (TUI-first, issue 002).
                    gate = _evaluate_resume_gate(self.runtime, self.plan.record)
                    if gate.kind == "repair-needed":
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
                            self.gate_notice = str(exc)
                            continue
                        if resolved_gate is None:
                            continue
                        _, record, _decision = resolved_gate
                        self.plan = managed_plan(self.runtime, record)
                        self.gate_notice = None
                        continue
                    self._recorded_only_modal(win)
                    continue
                if self._edit(win) == "exit":
                    return None
                continue
            if key.kind == "enter" and self.plan.ready:
                decision: str | None = self.resume_decision
                self.gate_notice = None
                if self.plan.record is not None and self.plan.action == "resume":
                    gate = _evaluate_resume_gate(self.runtime, self.plan.record)
                    if gate.kind != "ok" and not (
                        gate.kind == "daemon-owned" and decision == "force"
                    ):
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
                            # Transient notice, never plan.errors: the plan
                            # stays ready and the action can be retried
                            # (review should-fix 5).
                            self.gate_notice = str(exc)
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
    resume_decision: str | None = None,
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
        resume_decision=resume_decision,
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
                resume_decision=resume_decision,
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
        if key == "h":
            problems, info_lines, attention = _collect_doctor_reports(runtime)
            verdict = "BLOCKED" if problems else ("Attention" if attention else "Ready")
            output_stream.write(f"claude-multi doctor: {verdict}\n")
            for line in problems:
                output_stream.write(f"  - {tui.visible_text(line)}\n")
            for line in attention:
                output_stream.write(f"  ! {tui.visible_text(line)}\n")
            for line in info_lines:
                output_stream.write(f"{tui.visible_text(line)}\n")
            if not problems:
                footer = "Catalog, compositions, and local gateway are valid"
                footer += (
                    "; attention items are by-design lazy state, not damage.\n"
                    if attention
                    else ".\n"
                )
                output_stream.write(footer)
            else:
                output_stream.write(
                    "run `claude-multi doctor --repair-all` now? [y/N] "
                )
                output_stream.flush()
                if input_stream.readline().strip().lower() in ("y", "yes"):
                    _doctor_repair_all(runtime, output_stream)
            continue
        if key == "s":
            _print_sessions_listing(runtime, output_stream)
            if tui.streams_curses_capable(input_stream, output_stream):
                output_stream.write(
                    "resume managed sessions with `claude-multi -r <uuid>` (or a "
                    "name), ordinary sessions with `claude-gateway --resume "
                    "<uuid>`, or press S in the curses UI to pick interactively.\n"
                )
            else:
                output_stream.write(
                    "resume managed sessions with `claude-multi -r <uuid>` (or a "
                    "name), ordinary sessions with `claude-gateway --resume <uuid>`.\n"
                )
            continue
        if key == "g":
            _print_ordinary_listing(runtime, output_stream)
            output_stream.write(
                "launch with `claude-gateway --model <model>` (or "
                "`claude-multi direct --model <model>`)"
                + (
                    ", or press G in the curses UI to pick interactively.\n"
                    if tui.streams_curses_capable(input_stream, output_stream)
                    else ".\n"
                )
            )
            continue
        if key in ("p", "P"):
            if plan.record is None and plan.source in (
                "Unsaved launch",
                "Unsaved editor changes",
            ):
                output_stream.write(
                    "discard the unsaved composition edits by cycling? [y/N] "
                )
                output_stream.flush()
                if input_stream.readline().strip().lower() not in ("y", "yes"):
                    continue
            # _read_key lowercases, so cycling here is forward-only (Shift-Tab
            # backward exists only in curses).
            cycled = _cycle_preset(runtime, plan, 1)
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


def _composition_recency(runtime: Runtime) -> tuple[dict[str, str], dict[str, str]]:
    """Per-composition effective last-used, split this-cwd vs other cwds.

    Derived from managed session records (015 D-a): no new state — D3 keeps
    records the intent authority, and hooks keep ``last_seen_at`` current.
    Ordinary records carry no composition and are ignored.
    """

    here: dict[str, str] = {}
    elsewhere: dict[str, str] = {}
    for record in _session_records(runtime):
        if record["session_type"] != sessions.SESSION_TYPE_MANAGED:
            continue
        name = record.get("composition_name")
        if not name:
            continue
        target = here if record.get("cwd") == runtime.cwd else elsewhere
        stamp = _record_last_seen(record)
        if stamp > target.get(name, ""):
            target[name] = stamp
    return here, elsewhere


def _merged_recency(
    here: dict[str, str], elsewhere: dict[str, str]
) -> dict[str, str]:
    """this-cwd and other-cwd recency tables merged by max stamp."""

    merged = dict(elsewhere)
    for name, stamp in here.items():
        if stamp > merged.get(name, ""):
            merged[name] = stamp
    return merged


def _pick_order_from(
    names: list[str], here: dict[str, str], elsewhere: dict[str, str]
) -> list[str]:
    """Most-recently-used pick order: this directory's compositions first
    (recency desc, name asc on ties), then globally-recent ones, then
    never-launched names alphabetically. Store names are the namespace —
    records of deleted compositions never resurrect them.
    """

    namespace = set(names)
    recent_here = {k: v for k, v in here.items() if k in namespace}
    # "Elsewhere" is strictly compositions used ONLY in other directories —
    # a name used here AND elsewhere appears once, in the here tier.
    recent_else = {
        k: v
        for k, v in elsewhere.items()
        if k in namespace and k not in recent_here
    }

    def _rank(table: dict[str, str]) -> list[str]:
        # stable sort: names ascending, then stamps descending over that
        return sorted(sorted(table), key=lambda n: table[n], reverse=True)

    ranked = _rank(recent_here) + _rank(recent_else)
    remainder = sorted(namespace - set(recent_here) - set(recent_else))
    return ranked + remainder


def composition_pick_order(runtime: Runtime) -> list[str]:
    """MRU ordering for every composition pick surface (cycle, choosers)."""

    here, elsewhere = _composition_recency(runtime)
    return _pick_order_from(runtime.compositions.names(), here, elsewhere)


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
    ("T", "switch/transition"),
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
    "  T switch — managed: transition, same transcript, different composition\n"
    "    (semantic diff first; the session must be exited; relaunches exactly).\n"
    "    ordinary gateway rows: switch model — pick from the profile groups,\n"
    "    same transcript, the resume gate applies as with R.\n"
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
    "  adopt/discard decision (X) · ! repair needed.\n"
    "\n"
    "rows are sorted by last used (hooks keep it current); the last used age\n"
    "  is always shown, created appears when width permits (both relative).\n"
    "  C cwd filter — ON by default: only this directory's sessions; toggle\n"
    "  to see all.\n"
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
SESSIONS_EMPTY_FILTERED = "(no sessions in this directory — press C to see all)"
FORGET_MODAL_TITLE = "Forget session {short}?"
FORGET_MODAL_BODY = (
    "Deletes: session record + generated scope{scope_note}.\n"
    "Transcripts are never touched."
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
TRANSITION_SELECT_TITLE = "Transition {short} to composition · recent first"
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


def _record_age_of(record: dict[str, Any], field: str, *, now: datetime | None = None) -> str:
    """Relative age of one record timestamp field ("2h ago"); raw value on parse failure."""

    try:
        stamp = datetime.strptime(record[field], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (KeyError, ValueError):
        return str(record.get(field, "?"))
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int((current - stamp).total_seconds()))
    return _age_from_seconds(seconds)


def _record_age(record: dict[str, Any], *, now: datetime | None = None) -> str:
    """Relative session age for display ("2h ago"); ISO string on parse failure."""

    return _record_age_of(record, "created_at", now=now)


def _record_last_seen(record: dict[str, Any]) -> str:
    """Effective last-activity timestamp for sort and display.

    Creation is itself a use: a missing, malformed, or pre-creation
    `last_seen_at` (clock skew, hand-edits) falls back to `created_at`.
    """

    created = record["created_at"]
    last_seen = record.get("last_seen_at")
    if isinstance(last_seen, str) and last_seen >= created:
        try:
            datetime.strptime(last_seen, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return created
        return last_seen
    return created


def _last_used_age(effective: str, *, now: datetime | None = None) -> str:
    """Relative age of an effective last-used timestamp (ISO string on failure)."""

    try:
        stamp = datetime.strptime(effective, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return effective
    current = now or datetime.now(timezone.utc)
    seconds = max(0, int((current - stamp).total_seconds()))
    return _age_from_seconds(seconds)


def _record_last_used_age(record: dict[str, Any], *, now: datetime | None = None) -> str:
    """Relative age of the effective last-used timestamp (same value as sort)."""

    return _last_used_age(_record_last_seen(record), now=now)


def _record_sort_key_last_used(record: dict[str, Any]) -> str:
    """Descending-sort key: the effective last-used timestamp."""

    return _record_last_seen(record)


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
    Returns ("resume", record[, decision[, model]]) — the 4th element is an
    ordinary T-switch's picked model (D48) — or ("transition", record,
    composition) for the CLI to execute after curses teardown; forget/adopt
    run inline.
    """

    def __init__(
        self,
        runtime: Runtime,
        *,
        palette: tui.Palette,
        resume_decision: str | None = None,
    ):
        self.runtime = runtime
        self.palette = palette
        self.resume_decision = resume_decision
        self.section = "managed"
        self.selected = 0
        self.message = ""
        # Harness-style default (issue 012): the picker opens on this
        # directory's sessions; C widens to all.
        self.cwd_filter = True
        self._reload()

    def _reload(self) -> None:
        self.records = sorted(
            _session_records(self.runtime),
            key=_record_sort_key_last_used,
            reverse=True,
        )
        # One traversal for both the rows and the elsewhere flag (review):
        # filter-before-limit is preserved by slicing after the in-memory
        # filter, exactly as the function's own order does.
        native_all = _discover_native_sessions(self.runtime, limit=None)
        self.live_prefixes = _live_background_prefixes()
        if self.cwd_filter:
            all_records = self.records
            self.records = [
                record for record in all_records if record["cwd"] == self.runtime.cwd
            ]
            native_filtered = [
                item for item in native_all if item["cwd"] == self.runtime.cwd
            ]
            # For the empty-state copy: something exists OUTSIDE this
            # directory.
            self.empty_elsewhere = (
                not self.records
                and not native_filtered
                and (bool(all_records) or bool(native_all))
            )
        else:
            native_filtered = native_all
            self.empty_elsewhere = False
        self.native = native_filtered[:20]
        # Overflow beyond the newest-20 cap, surfaced in the section header.
        self.native_overflow = len(native_filtered) - len(self.native)
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
                _record_last_used_age(record),
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
            empty_note = (
                SESSIONS_EMPTY_FILTERED if self.empty_elsewhere else SESSIONS_EMPTY
            )
            tui.safe_add(win, row, 2, empty_note, palette.attr("dim"))
            row += 2
        else:
            tui.safe_add(win, row - 1, 2, "managed (claude-multi)", palette.attr("dim"))
            managed_max = min(len(managed_rows), actions_row - row - 3)
            managed_max = max(managed_max, 1)
            shown_rows, managed_selected = _windowed(
                managed_rows, self.selected if self.section == "managed" else -1, managed_max
            )
            # Width-tiered honesty: every tier fits its range (content +
            # separators = width-4); created appears only when it fits.
            if width >= 82:
                columns = ["session", "composition", "mode", "cwd", "last used", "created"]
                min_widths = [20, 10, 10, 7, 8, 8]
                pick = (0, 1, 2, 3, 4, 5)
            elif width >= 71:
                columns = ["session", "composition", "mode", "cwd", "last used"]
                min_widths = [20, 10, 10, 7, 8]
                pick = (0, 1, 2, 3, 4)
            elif width >= 57:
                columns = ["session", "composition", "mode", "last used"]
                min_widths = [16, 10, 10, 8]
                pick = (0, 1, 2, 4)
            else:
                columns = ["session", "composition", "last used"]
                min_widths = [16, 10, 8]
                pick = (0, 1, 4)
            tier_rows = [[row[index] for index in pick] for row in shown_rows]
            table = tui.Table(
                columns,
                tier_rows,
                selected=managed_selected,
                min_widths=min_widths,
            )
            table.draw(win, row, 2, width - 2, palette, max_rows=managed_max)
            row += managed_max + 2
        if self.native:
            native_header = (
                "native (unmanaged, discovered names+times only) · press L to adopt"
            )
            if self.native_overflow:
                native_header += f" · +{self.native_overflow} more (newest 20 shown)"
            tui.safe_add(
                win,
                row,
                2,
                native_header,
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
        # Live rows never reach the modal (review): forgetting under a
        # running process orphans it — E stops it in place first. The
        # under-lock pre-delete check closes the residual race below.
        if _record_is_live(record, self.live_prefixes):
            self.message = (
                f"session {session_id[:12]}… is live (●) — stop it first (E), "
                "then forget"
            )
            return
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
        # Same serialized effects as `sessions forget`, including the
        # under-lock liveness re-check.
        try:
            self.runtime.session_store.forget_session(
                session_id,
                pre_delete_check=_forget_liveness_guard(self.runtime, session_id),
            )
        except sessions.SessionError as exc:
            self._reload()
            self.message = str(exc)
            return
        self._reload()
        self.selected = min(self.selected, max(0, len(self._active()) - 1))
        self.message = FORGET_DONE.format(session_id=session_id)

    def _choose_composition(self, win: Any, record: dict[str, Any]) -> str | None:
        short = f"{sessions.managed_id(record)[:8]}…"
        names = composition_pick_order(self.runtime)
        chooser = tui.SelectList(
            TRANSITION_SELECT_TITLE.format(short=short),
            [
                tui.SelectItem(
                    name,
                    note="current" if name == record["composition_name"] else "",
                )
                for name in names
            ],
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

        names = composition_pick_order(self.runtime)
        chooser = tui.SelectList(
            f"Adopt {item['session_id'][:8]}… into composition · recent first",
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

    def run(self, win: Any) -> tuple | None:
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
                if gate.kind == "daemon-owned" and self.resume_decision == "force":
                    return ("resume", record, "force")
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
                    outcome = self._switch_ordinary_model(win, record)
                    if outcome is not None:
                        return outcome
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

    def _switch_ordinary_model(self, win: Any, record: dict[str, Any]) -> tuple | None:
        """Ordinary model switch (D48): gate, pick, confirm.

        Returns a resume intent whose 4th element carries the picked model
        as the explicit relaunch model; the caller prepares exactly as for
        a plain ordinary resume. None means stay in the picker.
        """

        if record.get("pending_forks"):
            self.message = (
                "model switch is fork-blocked — press X to resolve the fork "
                "(exact commands: sessions show)"
            )
            return None
        picked = _OrdinaryScreen(
            self.runtime,
            palette=self.palette,
            initial_model=record["ordinary_model"],
            purpose="switch",
        ).run(win)
        if picked is None:
            return None
        current = record["ordinary_model"]
        if picked == current:
            self.message = f"already on {picked}"
            return None
        old_profile = record["context_profile"]
        new_profile = compiler.direct_context_profile(
            self.runtime.ordinary_docs, picked
        )
        profile_line = (
            f"same context profile ({old_profile})"
            if new_profile == old_profile
            else (
                f"context profile {old_profile} → {new_profile} — the scope "
                "fence and compaction policy are rebuilt"
            )
        )
        confirmed = tui.Modal(
            "Switch model?",
            [
                f"{current} → {picked}",
                profile_line,
                "Same transcript. Make sure the session's process has exited.",
            ],
            buttons=(("Switch", True), ("Cancel", False)),
        ).run(win, self.palette, background=self._draw)
        if not confirmed:
            self.message = "Switch cancelled."
            return None
        # The gate runs LAST (review): its repair/stop actions mutate
        # immediately, so they must come after every cancellable step — the
        # switch is already confirmed here, and R's full gate modal gives
        # repair/stop/force/transcript guidance parity.
        decision: str | None = self.resume_decision
        gate = _evaluate_resume_gate(self.runtime, record)
        if gate.kind != "ok" and not (
            gate.kind == "daemon-owned" and decision == "force"
        ):
            try:
                resolved_gate = _run_resume_gate_modal(
                    self.runtime,
                    record,
                    gate,
                    win,
                    self.palette,
                    background=self._draw,
                )
            except (CLIError, sessions.SessionError) as exc:
                self.message = str(exc)
                return None
            if resolved_gate is None:
                self.message = "Switch cancelled."
                return None
            _, record, decision = resolved_gate
        return ("resume", record, decision, picked)


ORDINARY_TITLE = "gateway session — no composition"
ORDINARY_TITLE_SWITCH = "switch model — gateway session"
ORDINARY_SUBTITLE = (
    "plain Claude through the local gateway · native /model within a group · "
    "no roster, no workflow pins"
)
ORDINARY_PROFILE_NOTES = {
    "large": "1M context · /model switches freely within this group",
    "grok": "500K context · /model switches freely within this group",
}
ORDINARY_PROFILE_NOTE_DEFAULT = "/model switches freely within this group"
ORDINARY_KEYBAR = (
    ("Enter", "launch"),
    ("M", "all models"),
    ("P", "providers"),
    ("?", "help"),
    ("Esc", "back"),
)
ORDINARY_KEYBAR_SWITCH = (
    ("Enter", "select"),
    ("M", "all models"),
    ("P", "providers"),
    ("?", "help"),
    ("Esc", "back"),
)
ORDINARY_HELP_INTRO = {
    "launch": (
        "ordinary gateway sessions run plain Claude through the local gateway: no\n"
        "composition, no agent roster, no workflow pins — you pick the model.\n"
        "\n"
        "  Enter — launch a fresh session with the selected model.\n"
        "  Esc — back to the card.\n"
    ),
    "switch": (
        "you are switching this ordinary session's model: same plain Claude,\n"
        "same transcript, no composition — the /model group rules still apply.\n"
        "\n"
        "  Enter — select the new model.\n"
        "  Esc — back to the sessions screen.\n"
    ),
}
ORDINARY_HELP_SHARED = (
    "\n"
    "Groups are context profiles: the launched session's /model allow-list is\n"
    "its group; switching across groups is an explicit relaunch\n"
    "(claude-gateway --resume ID --model MODEL) so a 1M transcript can never\n"
    "strand into a smaller context. The session starts on the model's default\n"
    "selector; lanes (e.g. sol high/xhigh) switch via /model in-session. The\n"
    "native /model picker shows built-in Anthropic rows plus the current\n"
    "model — typing a selector switches to anything the group allows; the\n"
    "exact string for the selected row is shown under the list.\n"
    "\n"
    "Rows marked (no secret) belong to a provider whose secret is missing: the\n"
    "gateway omits providers rendered without their secret, so the model will\n"
    "fail unless the running gateway still serves an older config. Enter asks\n"
    "for explicit confirmation first. The marking speaks for\n"
    "the initial model only — in-session /model allows the whole group\n"
    "(its picker shows built-in Anthropic rows plus the current model).\n"
    "\n"
    "The session is tracked as an ordinary record: resume it from the sessions\n"
    "screen (S) or with claude-gateway --continue, with the usual resume gate.\n"
    "\n"
    "P opens the providers pane: per-provider local status (credential source,\n"
    "rendered/served selectors), exact connect instructions, masked key\n"
    "entry, and the custom provider/model flows (N new provider, A add\n"
    "models). M browses the full catalog+custom model list; D removes a\n"
    "custom model row.\n"
    "\n"
    "Arrow keys or j/k move; Esc closes this panel."
)
ORDINARY_UNAVAILABLE_TITLE = "Provider secret missing"
ORDINARY_UNAVAILABLE_BODY = (
    "The gateway omits providers rendered without their secret; the model\n"
    "may fail unless the running gateway still serves an older config."
)
ORDINARY_UNSERVED_TITLE = "Not served by the running gateway"
ORDINARY_UNSERVED_BODY = (
    "The rendered config carries this selector but the running daemon does\n"
    "not — apply with `claude-multi-proxy init` + `systemctl --user restart\n"
    "cli-proxy-api` (between turns). Launching now fails at request time."
)
ORDINARY_SIGNIN_TITLE = "Provider sign-in needed"
ORDINARY_SIGNIN_BODY = (
    "The {pool} OAuth pool has no credential record; the gateway serves\n"
    "nothing for it. The model will fail at request time unless the pool\n"
    "is signed in first."
)
ORDINARY_SECRET_FILE_ERROR = "secret env file unavailable or invalid"


def _ordinary_unavailable(runtime: Runtime) -> dict[str, str]:
    """Provider -> unavailable reason for ordinary launch marking (D46).

    Same rule the renderer applies when omitting providers from the gateway
    config (render.unavailable_providers), resolved against the live secret
    env file — the picker's dimmed rows and the CLI warning can never drift
    from what the gateway actually serves. A malformed, unreadable, or
    unsafe secret env file resolves nothing: every direct provider is then
    marked with one static file-level reason (never the parser's message)
    rather than crashing an advisory preflight.
    """

    providers = runtime.ordinary_docs["providers"]["providers"]
    try:
        entries = render.unavailable_providers(
            providers,
            resolve_secret=lambda name: proxy_mod.resolve_secret(
                name, environ=runtime.environ
            ),
        )
    except (proxy_mod.ProxyError, UnicodeDecodeError):
        return {
            provider_id: ORDINARY_SECRET_FILE_ERROR
            for provider_id in sorted(providers)
            if providers[provider_id]["transport"]["kind"] == "direct"
        }
    return {entry["provider"]: entry["reason"] for entry in entries}


def _model_client_selectors(model: dict[str, Any]) -> list[str]:
    """Sorted client selectors (base + lanes) for one catalog model."""

    return sorted(
        {model["client_selector"]}
        | {lane["client_selector"] for lane in model["lanes"].values()}
    )


def _ordinary_typed_selectors(model: dict[str, Any]) -> str:
    """Typed /model selector strings for one ordinary model (015 D-c).

    The native /model picker display-filters custom aliases (issue 009), but
    a typed selector hits the allow-list — showing the exact strings keeps
    in-session switching discoverable without fighting the native filter.
    """

    return " · ".join(f"/model {s}" for s in _model_client_selectors(model))


def _print_ordinary_listing(runtime: Runtime, output_stream: Any) -> None:
    """Text-mode ordinary launch listing (mirrors the G picker, D46)."""

    docs = runtime.ordinary_docs
    models = docs["models"]["models"]
    unavailable = _ordinary_unavailable(runtime)
    output_stream.write(
        "ordinary gateway sessions (no composition; /model within a group):\n"
    )
    for profile, model_ids in compiler.ordinary_launch_models(runtime.ordinary_docs).items():
        note = ORDINARY_PROFILE_NOTES.get(profile, ORDINARY_PROFILE_NOTE_DEFAULT)
        output_stream.write(f"  {profile} · {note}:\n")
        for model_id in model_ids:
            model = models[model_id]
            reason = unavailable.get(model["provider"])
            suffix = f"  (unavailable: {reason})" if reason is not None else ""
            output_stream.write(
                f"    {model_id}\t{tui.visible_text(model['display'])}\t"
                f"{_ordinary_typed_selectors(model)}{suffix}\n"
            )
    output_stream.write("  providers (local status; names only):\n")
    for fact in _provider_facts(runtime).facts:
        kind_label = "OAuth pool" if fact["kind"] == "oauth-pool" else "direct key"
        output_stream.write(
            f"    {fact['id']}\t{kind_label} · {fact['credential']} · "
            f"{fact['expected']} rendered · {fact['served_note']}\n"
        )
        output_stream.write(
            f"      connect: {_connect_hint(runtime, fact['id'])}\n"
        )


MODELS_TITLE = "models — catalog + custom"
MODELS_SUBTITLE = (
    "the `claude-multi models` list as a screen · enable/disable is a "
    "composition act (E → Availability)"
)
MODELS_HELP = (
    "Navigate with arrows or j/k; Home/End jumps to the first/last row.\n"
    "\n"
    "Every catalog model plus operator-added customs, with provider,\ncapabilities,\n"
    "context profile, and the exact typed /model selectors (the native\n"
    "picker display-filters custom aliases — typed selectors hit the\n"
    "allow-list). Enter opens the full detail (wire id, context bounds,\n"
    "routing note).\n"
    "\n"
    "There is no global enable/disable by design: availability is\n"
    "composition intent — the editor owns it (E on the card → Availability,\n"
    "per model: lead+agents / lead / agents / off). 'not in default' marks\n"
    "models the default composition does not enable. When E is offered in\n"
    "the keybar, it jumps straight to this model's Availability row in the\n"
    "composition on the card behind this picker.\n"
    "\n"
    "Provider-advertised models that are NOT here are onboarding candidates\n"
    "(`claude-multi discover <provider>`); the catalog owns admission."
)


class _ModelsScreen:
    """Read-only catalog model browser (2.14.0): the TUI form of `models`.

    Deliberately read-only — a global enable/disable would be a third
    intent authority beside compositions and records (D3); the row detail
    points at the editor's Availability section instead.
    """

    def __init__(
        self,
        runtime: Runtime,
        *,
        palette: tui.Palette,
        allow_edit_jump: bool = False,
    ):
        self.runtime = runtime
        self.palette = palette
        # The edit jump is offered only from the launch-purpose G picker:
        # it returns up to the card, which opens the editor on this model's
        # Availability row (fresh plans only — R1 P1 guards the rest).
        self.allow_edit_jump = allow_edit_jump
        self.rows = sorted(runtime.ordinary_docs["models"]["models"])
        default_models = runtime.compositions.load("default")["availability"]["models"]
        self.in_default = {m for m in self.rows if m in default_models}
        self.selected = 0

    def _keybar(self) -> tui.KeyBar:
        bindings: list[tuple[str, str]] = [("Enter", "details")]
        # The enable jump targets the editor's Availability section, which
        # exists only for catalog models — customs are ordinary-only and
        # get the scaffold pointer in their detail modal instead.
        if (
            self.allow_edit_jump
            and self.rows
            and self.rows[self.selected] in self.runtime.catalog.models
        ):
            bindings.append(("E", "enable in composition"))
        bindings.extend((("?", "help"), ("Esc", "back")))
        return tui.KeyBar(bindings)

    def _draw(self, win: Any) -> None:
        win.erase()
        palette = self.palette
        height, width = win.getmaxyx()
        keybar = self._keybar()
        bar_rows = keybar.rows(width)
        wrap_width = max(20, width - 4)
        selector_lines = (
            textwrap.wrap(
                f"in-session: {_ordinary_typed_selectors(self.runtime.ordinary_docs['models']['models'][self.rows[self.selected]])}",
                wrap_width,
            )
            if self.rows
            else []
        )
        needed = 5 + len(self.rows) + len(selector_lines) + 1
        if height < needed or width < 44:
            tui.safe_add(win, 1, 2, MODELS_TITLE, palette.attr("accent") | curses.A_BOLD)
            tui.safe_add(
                win, 3, 2, "terminal too small; `claude-multi models` is the text form.",
                palette.attr("warn"),
            )
            keybar.draw(win, height - 1, palette)
            win.refresh()
            return
        tui.safe_add(win, 1, 2, MODELS_TITLE, palette.attr("accent") | curses.A_BOLD)
        tui.safe_add(win, 2, 2, "─" * min(width - 1, 62), palette.attr("dim"))
        tui.safe_add(win, 3, 2, MODELS_SUBTITLE, palette.attr("dim"))
        row = 5
        models = self.runtime.ordinary_docs["models"]["models"]
        providers = self.runtime.ordinary_docs["providers"]["providers"]
        for index, model_id in enumerate(self.rows):
            model = models[model_id]
            family = providers[model["provider"]]["independence_family"]
            capabilities = ",".join(model["capabilities"])
            profile = model["context"]["ordinary_profile"] or "agents-only"
            label = f"{model_id} — {model['display']} · {family} · {capabilities} · {profile}"
            if model_id not in self.runtime.catalog.models:
                label += "  (custom · ordinary only)"
            elif model_id not in self.in_default:
                label += "  (not in default)"
            attr = palette.attr("normal")
            if index == self.selected:
                attr |= curses.A_REVERSE
            prefix = "> " if index == self.selected else "  "
            tui.safe_add(win, row, 2, prefix + label, attr)
            row += 1
        message_row = height - bar_rows - 1
        start = message_row - len(selector_lines) + 1
        for offset, line in enumerate(selector_lines):
            tui.safe_add(win, start + offset, 2, line, palette.attr("dim"))
        keybar.draw(win, height - 1, palette)
        win.refresh()

    def _details(self, win: Any) -> None:
        model_id = self.rows[self.selected]
        model = self.runtime.ordinary_docs["models"]["models"][model_id]
        context = model["context"]
        scalar = context["scalar_tokens"] or "none"
        wrap_width = max(20, min(64, win.getmaxyx()[1] - 8))
        lines = [
            f"provider: {model['provider']} · capabilities: {','.join(model['capabilities'])}",
            f"wire: {model['wire_model']}",
            f"context: client={context['client_tokens']} provider={context['provider_tokens']} scalar={scalar}",
            f"ordinary profile: {context['ordinary_profile'] or 'agents-only'} · default lane: {model['default_lane']}",
            f"in-session: {_ordinary_typed_selectors(model)}",
        ]
        for line in textwrap.wrap(f"routing: {model['routing_note']}", wrap_width):
            lines.append(line)
        if model_id not in self.runtime.catalog.models:
            lines.extend(
                textwrap.wrap(
                    "custom (ordinary sessions only) — for composition use, "
                    "onboard it into the catalog: `claude-multi-dev model add "
                    "--like <sibling>`",
                    wrap_width,
                )
            )
        tui.Modal(
            f"{model_id} — {model['display']}",
            lines,
            buttons=(("Close", True),),
        ).run(win, self.palette, background=self._draw)

    def run(self, win: Any) -> str | None:
        """None on Esc; the selected model id on an enabled E (edit jump)."""

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
            if key.kind == "char" and key.ch == "?":
                tui.Modal(
                    MODELS_TITLE + " — help",
                    MODELS_HELP.splitlines(),
                    buttons=(("Close", True),),
                ).run(win, self.palette, background=self._draw)
                continue
            if key.kind == "up" or (key.kind == "char" and key.ch == "k"):
                if self.selected > 0:
                    self.selected -= 1
                continue
            if key.kind == "down" or (key.kind == "char" and key.ch == "j"):
                if self.selected < len(self.rows) - 1:
                    self.selected += 1
                continue
            if key.kind == "home":
                self.selected = 0
                continue
            if key.kind == "end":
                self.selected = max(0, len(self.rows) - 1)
                continue
            if not self.rows:
                continue
            if (
                key.kind == "char"
                and key.ch.lower() == "e"
                and self.allow_edit_jump
                and self.rows[self.selected] in self.runtime.catalog.models
            ):
                return self.rows[self.selected]
            if key.kind == "enter":
                self._details(win)
                continue


class _OrdinaryScreen:
    """Ordinary gateway model picker (D46/D48), launch or switch purpose.

    Sections are ordinary context profiles — the native /model fence of the
    session. Rows are catalog + custom models (lanes stay in-session via /model).
    Rows whose provider secret is missing render dimmed with a (no secret)
    marker: the marking is render-time availability (the gateway omits
    providers rendered without their secret), so Enter on one rechecks the
    file and asks for explicit confirmation before launching/switching
    anyway — except a same-model pick during a switch, which the caller
    no-ops, so no confirm is shown. Returns the picked catalog model id;
    None cancels.
    """

    def __init__(
        self,
        runtime: Runtime,
        *,
        palette: tui.Palette,
        initial_model: str | None = None,
        purpose: str = "launch",
    ) -> None:
        self.runtime = runtime
        self.palette = palette
        self.purpose = purpose
        self.groups = compiler.ordinary_launch_models(runtime.ordinary_docs)
        self.rows = [
            model_id for model_ids in self.groups.values() for model_id in model_ids
        ]
        self.unavailable = _ordinary_unavailable(runtime)
        # OAuth pools always render, so a missing CREDENTIAL RECORD is
        # invisible to the secret marking — but the gateway serves nothing
        # for that pool. Mark those rows too (names/counts only).
        self.oauth_records = _oauth_credential_records(runtime)
        # Best-effort served set: rendered-but-unserved rows are the
        # marked-but-not-applied funnel state (init/restart pending). None
        # means unknown (gateway down) — nothing is marked on that alone.
        try:
            token = launch.read_gateway_token(runtime.catalog.docs["gateway"])
            self.served, _status = launch.served_models(
                runtime.catalog.docs["gateway"], token
            )
        except launch.LaunchError:
            self.served = None
        self._initial_model = initial_model
        # Preselect the given model (ordinary model switch, D48); otherwise
        # match the CLI default (prepare_direct: model_id or "sol").
        if initial_model in self.rows:
            self.selected = self.rows.index(initial_model)
        else:
            self.selected = self.rows.index("sol") if "sol" in self.rows else 0

    @property
    def _action_verb(self) -> str:
        return "launching" if self.purpose == "launch" else "switching"

    def _title_text(self) -> str:
        return ORDINARY_TITLE if self.purpose == "launch" else ORDINARY_TITLE_SWITCH

    def _row_reason(self, model_id: str) -> str | None:
        model = self.runtime.ordinary_docs["models"]["models"][model_id]
        return self.unavailable.get(model["provider"])

    def _row_is_custom(self, model_id: str) -> bool:
        return model_id in custom.load_registry(self.runtime.environ)["models"]

    def _row_signin_pool(self, model_id: str) -> str | None:
        """OAuth pool name when the row's provider has no credential record."""

        model = self.runtime.ordinary_docs["models"]["models"][model_id]
        transport = self.runtime.ordinary_docs["providers"]["providers"][model["provider"]]["transport"]
        if transport["kind"] != "oauth-pool":
            return None
        pool = transport["pool"]
        return None if self.oauth_records.get(pool, 0) > 0 else pool

    def _row_unserved(self, model_id: str) -> bool:
        """Fully wired but the running gateway lacks the route (020 funnel).

        Only fires when the row is otherwise ready (secret present, pool
        signed in) — secret/sign-in markings own those cases. Unknown
        served state marks nothing.
        """

        if self.served is None:
            return False
        if self._row_reason(model_id) is not None or self._row_signin_pool(model_id):
            return False
        model = self.runtime.ordinary_docs["models"]["models"][model_id]
        selectors = {
            lane["client_selector"].removesuffix("[1m]")
            for lane in model["lanes"].values()
        }
        return bool(selectors) and not selectors <= self.served

    def _detail_reserve(self, width: int) -> int:
        """Wrapped-line worst case for the selected-row detail (floor math).

        Computed over every reason any direct provider could produce at
        this width plus every row's typed-selector line, so the size floor
        never flaps while browsing.
        """

        providers = self.runtime.ordinary_docs["providers"]["providers"]
        candidates = [
            f"missing required secret {p['transport']['auth']['secret_ref']}"
            for p in providers.values()
            if p["transport"]["kind"] == "direct"
        ]
        candidates.append(ORDINARY_SECRET_FILE_ERROR)
        candidates.extend(
            f"no {p['transport']['pool']} credential record — sign in: "
            f"`claude-multi-proxy {_OAUTH_LOGIN_COMMANDS.get(p['transport']['pool'], p['transport']['pool'] + '-login')}`"
            for p in providers.values()
            if p["transport"]["kind"] == "oauth-pool"
        )
        candidates.append(
            "not served by the running gateway — apply first: "
            "`claude-multi-proxy init` + `systemctl --user restart cli-proxy-api`"
        )
        wrap_width = max(20, width - 4)
        reason_lines = max(
            len(
                textwrap.wrap(
                    f"{reason} — Enter asks before {self._action_verb} anyway",
                    wrap_width,
                )
            )
            for reason in candidates
        )
        selector_lines = max(
            (
                len(
                    textwrap.wrap(
                        f"in-session: {_ordinary_typed_selectors(self.runtime.ordinary_docs['models']['models'][m])}",
                        wrap_width,
                    )
                )
                for m in self.rows
            ),
            default=0,
        )
        return reason_lines + selector_lines

    def _draw(self, win: Any) -> None:
        win.erase()
        palette = self.palette
        height, width = win.getmaxyx()
        bindings = list(
            ORDINARY_KEYBAR if self.purpose == "launch" else ORDINARY_KEYBAR_SWITCH
        )
        if self.rows and self._row_is_custom(self.rows[self.selected]):
            bindings.insert(-2, ("D", "remove"))  # custom rows only, before ?/Esc
        keybar = tui.KeyBar(tuple(bindings))
        bar_rows = keybar.rows(width)
        # Layout: title, separator, subtitle, blank, per group header + rows
        # (+ a blank after each), trailing blank, wrapped detail lines above
        # the bar (worst case reserved so the floor is stable per width).
        needed = (
            4 + len(self.rows) + 2 * len(self.groups) + 1 + self._detail_reserve(width)
        )
        if height < needed or width < 44:
            # Minimum-size floor (same H6 contract as the sessions screen):
            # below this the list cannot render honestly.
            tui.safe_add(win, 1, 2, self._title_text(), palette.attr("accent") | curses.A_BOLD)
            tui.safe_add(
                win, 3, 2, "terminal too small for the gateway picker;",
                palette.attr("warn"),
            )
            tui.safe_add(
                win, 4, 2, "resize, or use `claude-gateway --model MODEL` (text).",
                palette.attr("dim"),
            )
            keybar.draw(win, height - 1, palette)
            win.refresh()
            return
        message_row = height - bar_rows - 1
        tui.safe_add(win, 1, 2, self._title_text(), palette.attr("accent") | curses.A_BOLD)
        tui.safe_add(win, 2, 2, "─" * min(width - 1, 62), palette.attr("dim"))
        tui.safe_add(win, 3, 2, ORDINARY_SUBTITLE, palette.attr("dim"))
        row = 5
        index = 0
        models = self.runtime.ordinary_docs["models"]["models"]
        providers = self.runtime.ordinary_docs["providers"]["providers"]
        for profile, model_ids in self.groups.items():
            header = (
                custom.profile_label(profile)
                if profile.startswith("custom-")
                else f"{profile} · {ORDINARY_PROFILE_NOTES.get(profile, ORDINARY_PROFILE_NOTE_DEFAULT)}"
            )
            tui.safe_add(win, row, 2, header, palette.attr("dim"))
            row += 1
            for model_id in model_ids:
                reason = self._row_reason(model_id)
                model = models[model_id]
                family = providers[model["provider"]]["independence_family"]
                # Rows stay narrow on purpose (H11): the full reason for the
                # selected row is spelled out on the detail lines instead.
                label = f"{model_id} — {model['display']} · {family}"
                signin_pool = self._row_signin_pool(model_id)
                unserved = self._row_unserved(model_id)
                if reason is not None:
                    label += "  (no secret)"
                elif signin_pool is not None:
                    label += "  (sign in needed)"
                elif unserved:
                    label += "  (not served)"
                attr = (
                    palette.attr("dim")
                    if reason is not None or signin_pool is not None or unserved
                    else palette.attr("normal")
                )
                if index == self.selected:
                    attr |= curses.A_REVERSE
                prefix = "> " if index == self.selected else "  "
                tui.safe_add(win, row, 2, prefix + label, attr)
                row += 1
                index += 1
            row += 1
        if not self.rows:
            tui.safe_add(
                win, row, 2, "(no ordinary-capable models in this catalog)",
                palette.attr("dim"),
            )
        if self.rows:
            selected = self.rows[self.selected]
            selected_reason = self._row_reason(selected)
            selected_signin = self._row_signin_pool(selected)
            wrap_width = max(20, width - 4)
            detail: list[tuple[str, str]] = []
            if selected_reason is not None:
                detail.extend(
                    (line, "warn")
                    for line in textwrap.wrap(
                        f"{selected_reason} — Enter asks before {self._action_verb} anyway",
                        wrap_width,
                    )
                )
            if selected_signin is not None:
                command = _OAUTH_LOGIN_COMMANDS.get(
                    selected_signin, f"{selected_signin}-login"
                )
                detail.extend(
                    (line, "warn")
                    for line in textwrap.wrap(
                        f"no {selected_signin} credential record — sign in: "
                        f"`claude-multi-proxy {command}`",
                        wrap_width,
                    )
                )
            if self._row_unserved(selected):
                detail.extend(
                    (line, "warn")
                    for line in textwrap.wrap(
                        "not served by the running gateway — apply first: "
                        "`claude-multi-proxy init` + `systemctl --user "
                        "restart cli-proxy-api`",
                        wrap_width,
                    )
                )
            detail.extend(
                (line, "dim")
                for line in textwrap.wrap(
                    f"in-session: {_ordinary_typed_selectors(models[selected])}",
                    wrap_width,
                )
            )
            start = message_row - len(detail) + 1
            for offset, (line, role) in enumerate(detail):
                tui.safe_add(win, start + offset, 2, line, palette.attr(role))
        keybar.draw(win, height - 1, palette)
        win.refresh()

    def run(self, win: Any) -> str | None:
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
            if key.kind == "char" and key.ch == "?":
                tui.Modal(
                    self._title_text() + " — help",
                    (ORDINARY_HELP_INTRO[self.purpose] + ORDINARY_HELP_SHARED).splitlines(),
                    buttons=(("Close", True),),
                ).run(win, self.palette, background=self._draw)
                continue
            if key.kind == "char" and key.ch.lower() == "p":
                _ProvidersScreen(self.runtime, palette=self.palette).run(win)
                continue
            if key.kind == "char" and key.ch.lower() == "m":
                jump = _ModelsScreen(
                    self.runtime,
                    palette=self.palette,
                    allow_edit_jump=self.purpose == "launch",
                ).run(win)
                if jump is not None:
                    return ("edit-availability", jump)
                continue
            if key.kind == "up" or (key.kind == "char" and key.ch == "k"):
                if self.selected > 0:
                    self.selected -= 1
                continue
            if key.kind == "down" or (key.kind == "char" and key.ch == "j"):
                if self.selected < len(self.rows) - 1:
                    self.selected += 1
                continue
            if key.kind == "home":
                self.selected = 0
                continue
            if key.kind == "end":
                self.selected = max(0, len(self.rows) - 1)
                continue
            if not self.rows:
                # Same empty-list guard as the sessions screen: action keys
                # never index an empty catalog.
                continue
            if key.kind == "char" and key.ch.lower() == "d":
                picked_id = self.rows[self.selected]
                if not self._row_is_custom(picked_id):
                    continue  # catalog models are code; D is a no-op on them
                confirmed = tui.Modal(
                    f"Remove custom model {picked_id}?",
                    [
                        "Removes it from the custom registry (sessions keep",
                        "their transcript; the alias disappears after apply).",
                        "Apply: `claude-multi-proxy init` + restart (between turns).",
                    ],
                    buttons=(("Remove", True), ("Cancel", False)),
                ).run(win, self.palette, background=self._draw)
                if confirmed:
                    custom.remove_model(self.runtime.environ, picked_id)
                    self.groups = compiler.ordinary_launch_models(self.runtime.ordinary_docs)
                    self.rows = [
                        m for ids in self.groups.values() for m in ids
                    ]
                    self.selected = min(self.selected, max(0, len(self.rows) - 1))
                continue
            if key.kind == "enter":
                model_id = self.rows[self.selected]
                # Recheck at Enter (not just at open): the secret file can
                # change while the picker is up, and the marking is advisory
                # renderability — never a stale verdict. A same-model pick
                # during a switch skips the confirm: the caller no-ops it,
                # so no risky-sounding ask for a relaunch that never happens.
                self.unavailable = _ordinary_unavailable(self.runtime)
                self.oauth_records = _oauth_credential_records(self.runtime)
                reason = self._row_reason(model_id)
                signin_pool = self._row_signin_pool(model_id)
                unserved = self._row_unserved(model_id)
                if (reason is not None or signin_pool is not None or unserved) and not (
                    self.purpose == "switch" and model_id == self._initial_model
                ):
                    model = self.runtime.ordinary_docs["models"]["models"][model_id]
                    _height, modal_width = win.getmaxyx()
                    wrap_width = max(20, min(60, modal_width - 8))
                    lines = [
                        f"{model_id} — {model['display']} "
                        f"(provider {model['provider']})"
                    ]
                    if reason is not None:
                        title = ORDINARY_UNAVAILABLE_TITLE
                        body = [reason + ".", *ORDINARY_UNAVAILABLE_BODY.splitlines()]
                    elif signin_pool is not None:
                        title = ORDINARY_SIGNIN_TITLE
                        body = [
                            *ORDINARY_SIGNIN_BODY.format(pool=signin_pool).splitlines(),
                        ]
                    else:
                        title = ORDINARY_UNSERVED_TITLE
                        body = ORDINARY_UNSERVED_BODY.splitlines()
                    for raw in body:
                        lines.extend(textwrap.wrap(raw, wrap_width))
                    lines.extend(
                        textwrap.wrap(
                            "connect: " + _connect_hint(self.runtime, model["provider"]),
                            wrap_width,
                        )
                    )
                    confirmed = tui.Modal(
                        title,
                        lines,
                        buttons=(
                            ("Cancel", False),
                            (
                                "Launch anyway"
                                if self.purpose == "launch"
                                else "Switch anyway",
                                True,
                            ),
                        ),
                    ).run(win, self.palette, background=self._draw)
                    if not confirmed:
                        continue
                return model_id


PROVIDERS_TITLE = "providers — local status"
PROVIDERS_LEGEND = (
    "served = registered by the running local gateway; upstream auth, quota, "
    "and reachability stay unknown"
)
PROVIDERS_KEYBAR = (
    ("Enter", "setup"),
    ("N", "new provider"),
    ("A", "add models"),
    ("R", "refresh"),
    ("?", "help"),
    ("Esc", "back"),
)
PROVIDERS_HELP = (
    "Each row is one catalog provider with its LOCAL facts: the credential\n"
    "source (env-var name present/missing, or OAuth credential-record count),\n"
    "how many selectors the catalog renders for it, and how many the running\n"
    "gateway serves. 'served' means registered by the local gateway — upstream\n"
    "auth, quota, and reachability stay unknown.\n"
    "\n"
    "Enter on a direct-key row opens masked key entry: the value is written\n"
    "to the standard secret env file (0600, atomic, never echoed). Saving a\n"
    "key does not move served counts until you apply it: `claude-multi-proxy\n"
    "init`, then `systemctl --user restart cli-proxy-api` (restart between\n"
    "turns — an in-flight request may need a retry).\n"
    "\n"
    "Enter on an OAuth-pool row shows the sign-in command (OAuth runs outside\n"
    "the TUI). R re-reads every fact. Esc goes back.\n"
    "\n"
    "N registers a new Anthropic-compatible provider (endpoint + key env var).\n"
    "A on a row adds models to the custom registry — a confirmed one-shot\n"
    "fetch for listing-capable providers (Kimi verified), manual type-in\n"
    "anywhere. Enter on a custom provider offers key-replace / edit / remove.\n"
    "Customs are ordinary-session only; compositions onboard via\n"
    "claude-multi-dev model add --like. Apply changes with `claude-multi-proxy\n"
    "init` + `systemctl --user restart cli-proxy-api` (between turns)."
)


def _registry_id_for_wire(wire: str, taken: set[str]) -> str | None:
    """Slash-free registry key for a provider-listed wire id (022 review).

    OpenRouter wires carry `author/model` slashes, which are not valid state
    names; the model basename (dots are valid) is the natural key, with the
    dash-joined full wire as the collision fallback. ``None`` when both are
    taken (the caller reports the entry as failed).
    """

    basename = wire.rsplit("/", 1)[-1]
    for candidate in (basename, wire.replace("/", "-")):
        if candidate in taken:
            continue
        try:
            state.check_name(candidate)
        except state.StateError:
            continue
        return candidate
    return None


def _connect_hint(runtime: Runtime, provider_id: str) -> str:
    """Exact per-provider connect instruction (018) — names/paths, no values."""

    provider = runtime.ordinary_docs["providers"]["providers"][provider_id]
    transport = provider["transport"]
    if transport["kind"] == "oauth-pool":
        pool = transport["pool"]
        command = _OAUTH_LOGIN_COMMANDS.get(pool, f"{pool}-login")
        return f"run `claude-multi-proxy {command}` (OAuth sign-in)"
    name = transport["auth"]["secret_ref"].removeprefix("env:")
    path = proxy_mod.secret_env_path(runtime.environ)
    return (
        f"set {name} in {path} (P providers here offers masked entry), then "
        "`claude-multi-proxy init` and `systemctl --user restart cli-proxy-api`"
    )


@dataclass(frozen=True)
class ProviderFacts:
    """Provider rows plus the two pane-level gateway facts (018/2.13.1)."""

    facts: list[dict[str, Any]]
    config_drift: bool | None
    gateway_down: bool


def _provider_facts(runtime: Runtime) -> ProviderFacts:
    """Per-provider local status facts (018): names and counts only.

    Shared by the providers screen and the line-mode listing so both tell
    the same story. No provider calls; secret values never read into facts.
    ``config_drift``/``gateway_down`` ride along so the pane can explain
    counts that the running daemon can't.
    """

    merged = runtime.ordinary_docs
    providers = merged["providers"]["providers"]
    models = merged["models"]["models"]
    custom_registry = custom.load_registry(runtime.environ)
    unavailable = _ordinary_unavailable(runtime)
    try:
        token = launch.read_gateway_token(runtime.catalog.docs["gateway"])
    except launch.LaunchError:
        token = None
    if token is not None:
        snap = _gateway_snapshot(runtime, token)
    else:
        snap = GatewaySnapshot(
            served=None,
            gateway_down=True,
            models_status=None,
            expected=frozenset(),
            oauth_alias_pools={},
            render_error=None,
            config_drift=None,
            config_note=None,
            oauth_records=_oauth_credential_records(runtime),
        )
    facts: list[dict[str, Any]] = []
    for provider_id in sorted(providers):
        provider = providers[provider_id]
        transport = provider["transport"]
        kind = transport["kind"]
        if kind == "oauth-pool":
            pool = transport["pool"]
            records = snap.oauth_records.get(pool, 0)
            credential = (
                f"{records} credential record" + ("s" if records != 1 else "")
            )
            command = _OAUTH_LOGIN_COMMANDS.get(pool, f"{pool}-login")
            guidance = f"sign in: `claude-multi-proxy {command}`"
            expected = render.provider_selectors(provider_id, provider, models)
        else:
            secret_ref = transport["auth"]["secret_ref"]
            name = secret_ref.removeprefix("env:")
            reason = unavailable.get(provider_id)
            if reason is None:
                credential = f"{name} present"
            elif reason == f"missing required secret {secret_ref}":
                credential = f"{name} missing"
            else:
                credential = reason
            guidance = f"set {name} (masked) with Enter"
            expected = render.provider_selectors(
                provider_id, provider, models, available=reason is None
            )
        if snap.served is None:
            served_note = "unknown" if snap.gateway_down else "unknown (non-200)"
        else:
            served_note = f"{len(expected & snap.served)}/{len(expected)} served"
        facts.append(
            {
                "id": provider_id,
                "display": provider["display"],
                "kind": kind,
                "custom": provider_id in custom_registry["providers"],
                "credential": credential,
                "expected": len(expected),
                "served_note": served_note,
                "guidance": guidance,
            }
        )
    return ProviderFacts(
        facts,
        config_drift=snap.config_drift,
        gateway_down=snap.gateway_down,
    )


class _ProvidersScreen:
    """Read-only provider status + setup actions (018), opened from G with P.

    Facts are names/counts only; the one write action is masked direct-key
    entry into the standard secret env file. OAuth sign-in is guidance (the
    exact claude-multi-proxy login command), never an in-TUI flow.
    """

    def __init__(self, runtime: Runtime, *, palette: tui.Palette):
        self.runtime = runtime
        self.palette = palette
        self.selected = 0
        self.message: str | None = None
        self.message_role = "accent"
        self.config_drift: bool | None = None
        self.gateway_down = False
        self.facts: list[dict[str, Any]] = []
        self._load_facts()

    def _load_facts(self) -> None:
        loaded = _provider_facts(self.runtime)
        self.facts = loaded.facts
        self.config_drift = loaded.config_drift
        self.gateway_down = loaded.gateway_down
        self.selected = min(self.selected, max(0, len(self.facts) - 1))

    def _draw(self, win: Any) -> None:
        win.erase()
        palette = self.palette
        height, width = win.getmaxyx()
        keybar = tui.KeyBar(PROVIDERS_KEYBAR)
        bar_rows = keybar.rows(width)
        wrap_width = max(20, width - 4)
        legend_lines = textwrap.wrap(PROVIDERS_LEGEND, wrap_width)
        banner_lines: list[str] = []
        if self.gateway_down:
            banner_lines.extend(
                textwrap.wrap(
                    "gateway is DOWN — served counts unknown; start it with "
                    "`systemctl --user start cli-proxy-api`",
                    wrap_width,
                )
            )
        elif self.config_drift:
            banner_lines.extend(
                textwrap.wrap(
                    "config drift — counts reflect the running daemon, not the "
                    "catalog: run `claude-multi-proxy init`, then `systemctl "
                    "--user restart cli-proxy-api`",
                    wrap_width,
                )
            )
        message_lines = (
            textwrap.wrap(self.message, wrap_width) if self.message else []
        )
        needed = (
            4
            + len(legend_lines)
            + len(banner_lines)
            + 2 * len(self.facts)
            + len(message_lines)
            + 2
        )
        if height < needed or width < 44:
            tui.safe_add(win, 1, 2, PROVIDERS_TITLE, palette.attr("accent") | curses.A_BOLD)
            tui.safe_add(
                win, 3, 2, "terminal too small; resize or press Esc.",
                palette.attr("warn"),
            )
            keybar.draw(win, height - 1, palette)
            win.refresh()
            return
        tui.safe_add(win, 1, 2, PROVIDERS_TITLE, palette.attr("accent") | curses.A_BOLD)
        tui.safe_add(win, 2, 2, "─" * min(width - 1, 62), palette.attr("dim"))
        row = 3
        for line in legend_lines:
            tui.safe_add(win, row, 2, line, palette.attr("dim"))
            row += 1
        for line in banner_lines:
            tui.safe_add(win, row, 2, line, palette.attr("warn"))
            row += 1
        row += 1
        for index, fact in enumerate(self.facts):
            kind_label = "OAuth pool" if fact["kind"] == "oauth-pool" else "direct key"
            if fact.get("custom"):
                kind_label += " · custom"
            head = f"{fact['display']} · {kind_label} · {fact['credential']}"
            attr = palette.attr("normal")
            if index == self.selected:
                attr |= curses.A_REVERSE
            prefix = "> " if index == self.selected else "  "
            tui.safe_add(win, row, 2, prefix + head, attr)
            row += 1
            detail = (
                f"selectors {fact['expected']} rendered · {fact['served_note']} · "
                f"{fact['guidance']}"
            )
            tui.safe_add(win, row, 4, detail, palette.attr("dim"))
            row += 1
        if message_lines:
            row += 1
            for line in message_lines:
                tui.safe_add(win, row, 2, line, palette.attr(self.message_role))
                row += 1
        keybar.draw(win, height - 1, palette)
        win.refresh()

    def _setup(self, win: Any) -> None:
        fact = self.facts[self.selected]
        provider = self.runtime.ordinary_docs["providers"]["providers"][fact["id"]]
        transport = provider["transport"]
        if transport["kind"] == "oauth-pool":
            pool = transport["pool"]
            command = _OAUTH_LOGIN_COMMANDS.get(pool, f"{pool}-login")
            tui.Modal(
                f"{fact['display']} — OAuth sign-in",
                [
                    "OAuth sign-in runs outside the TUI (browser/device flow):",
                    f"    claude-multi-proxy {command}",
                    "New credential records load via the gateway auth-dir "
                    "watcher; if routes stay absent, `systemctl --user "
                    "restart cli-proxy-api`.",
                ],
                buttons=(("Close", True),),
            ).run(win, self.palette, background=self._draw)
            return
        if fact.get("custom"):
            # Custom providers are operator state: offer modify/remove
            # alongside the key entry (catalog providers stay view+key only).
            chooser = tui.SelectList(
                f"{fact['display']} — custom provider",
                [
                    tui.SelectItem("set / replace the API key (masked)"),
                    tui.SelectItem("edit endpoint / auth fields"),
                    tui.SelectItem("remove this provider"),
                ],
                footer=(("Enter", "choose"), ("Esc", "back")),
            )
            choice = chooser.run(win, self.palette)
            if choice == 1:
                spec = custom.load_registry(self.runtime.environ)["providers"][fact["id"]]
                base_url = self._prompt(
                    win, "API endpoint", ["base URL"], initial=spec["base_url"]
                )
                if not base_url:
                    return self._cancelled("Edit provider")
                auth_index = tui.SelectList(
                    "auth kind",
                    [
                        tui.SelectItem("bearer (Authorization: Bearer …)"),
                        tui.SelectItem("header (x-api-key style)"),
                    ],
                    footer=(("Enter", "choose"), ("Esc", "cancel")),
                ).run(win, self.palette)
                if auth_index is None:
                    return self._cancelled("Edit provider")
                auth_kind = "bearer" if auth_index == 0 else "header"
                header = spec.get("header")
                if auth_kind == "header":
                    header = self._prompt(
                        win, "auth header name", [], initial=header or "x-api-key"
                    )
                    if not header:
                        return self._cancelled("Edit provider")
                secret_env = self._prompt(
                    win, "key env variable name", [], initial=spec["secret_env"]
                )
                if not secret_env:
                    return self._cancelled("Edit provider")
                try:
                    custom.add_provider(
                        self.runtime.environ,
                        fact["id"],
                        base_url=base_url,
                        auth_kind=auth_kind,
                        secret_env=secret_env,
                        header=header,
                        display=spec.get("display"),
                        catalog_providers=self.runtime.catalog.providers,
                    )
                except (custom.CustomModelsError, OSError, ValueError) as exc:
                    self.message = str(exc)
                    self.message_role = "warn"
                    return
                self._load_facts()
                self.message = (
                    f"provider {fact['id']} updated — apply: `claude-multi-proxy "
                    "init` + restart (between turns)"
                )
                self.message_role = "accent"
                return
            if choice == 2:
                confirmed = tui.Modal(
                    f"Remove {fact['display']}?",
                    [
                        "Removes the provider from the custom registry. Its",
                        "custom models must be removed first (the picker D key).",
                        "Apply afterwards: `claude-multi-proxy init` + restart.",
                    ],
                    buttons=(("Remove", True), ("Cancel", False)),
                ).run(win, self.palette, background=self._draw)
                if confirmed:
                    try:
                        custom.remove_provider(self.runtime.environ, fact["id"])
                        self._load_facts()
                        self.message = (
                            f"provider {fact['id']} removed — apply: "
                            "`claude-multi-proxy init` + restart (between turns)"
                        )
                        self.message_role = "accent"
                    except (custom.CustomModelsError, OSError) as exc:
                        self.message = str(exc)
                        self.message_role = "warn"
                return
            if choice is None:
                return
            # choice 0 falls through to the masked key entry below
        name = transport["auth"]["secret_ref"].removeprefix("env:")
        path = proxy_mod.secret_env_path(self.runtime.environ)
        modal = tui.Modal(
            f"{fact['display']} — set {name}",
            [
                f"The key is typed masked and written to {path}",
                "(0600, atomic; the value is never shown or logged).",
                "Apply afterwards: `claude-multi-proxy init`, then",
                "`systemctl --user restart cli-proxy-api` (between turns —",
                "an in-flight request may need a retry).",
            ],
            # Save first like every other input modal: type → Enter → Enter
            # confirms (Cancel remains one Left away; Esc cancels always).
            buttons=(("Save", True), ("Cancel", False)),
            input=tui.TextInput(mask="•"),
        )
        confirmed = modal.run(win, self.palette, background=self._draw)
        value = modal.input.value
        modal.input.value = ""  # never let a typed secret linger in widgets
        if not confirmed:
            self.message = "Set key cancelled."
            self.message_role = "warn"
            return
        try:
            length = proxy_mod.set_secret_value(path, name, value)
        except proxy_mod.ProxyError as exc:
            self.message = str(exc)
            self.message_role = "warn"
            return
        self._load_facts()
        self.message_role = "accent"
        self.message = (
            f"saved {name} ({length} chars) to {path} — apply: "
            "`claude-multi-proxy init`, then `systemctl --user restart "
            "cli-proxy-api` (between turns)"
        )

    def _prompt(
        self,
        win: Any,
        title: str,
        lines: list[str],
        *,
        masked: bool = False,
        initial: str = "",
    ) -> str | None:
        """One labeled input modal; None on cancel. Values never logged."""

        modal = tui.Modal(
            title,
            lines,
            buttons=(("OK", True), ("Cancel", False)),
            input=tui.TextInput(initial, mask="•" if masked else None),
        )
        confirmed = modal.run(win, self.palette, background=self._draw)
        value = modal.input.value.strip()
        modal.input.value = ""  # never let a typed secret linger in widgets
        if not confirmed:
            return None
        return value

    def _add_provider(self, win: Any) -> None:
        """N — register a custom Anthropic-compatible provider (020)."""

        provider_id = self._prompt(
            win,
            "New provider — id",
            ["letters/digits/dashes (e.g. my-lab); Anthropic-compatible API"],
        )
        if not provider_id:
            return self._cancelled("New provider")
        base_url = self._prompt(
            win,
            f"New provider {provider_id} — API endpoint",
            ["base URL, e.g. https://host.example.com/apps/anthropic"],
        )
        if not base_url:
            return self._cancelled("New provider")
        chooser = tui.SelectList(
            "auth kind",
            [
                tui.SelectItem("bearer (Authorization: Bearer …)"),
                tui.SelectItem("header (x-api-key style)"),
            ],
            footer=(("Enter", "choose"), ("Esc", "cancel")),
        )
        auth_index = chooser.run(win, self.palette)
        if auth_index is None:
            return self._cancelled("New provider")
        auth_kind = "bearer" if auth_index == 0 else "header"
        header = None
        if auth_kind == "header":
            header = self._prompt(
                win, "auth header name", ["e.g. x-api-key"], initial="x-api-key"
            )
            if not header:
                return self._cancelled("New provider")
        suggested = f"{provider_id.upper().replace('-', '_')}_API_KEY"
        secret_env = self._prompt(
            win,
            "key env variable name",
            [f"a variable in ~/.config/secrets/claude.env, e.g. {suggested};",
             "naming an EXISTING variable reuses it (no key prompt)"],
            initial=suggested,
        )
        if not secret_env:
            return self._cancelled("New provider")
        try:
            custom.add_provider(
                self.runtime.environ,
                provider_id,
                base_url=base_url,
                auth_kind=auth_kind,
                secret_env=secret_env,
                header=header,
                catalog_providers=self.runtime.catalog.providers,
            )
        except (custom.CustomModelsError, OSError, ValueError) as exc:
            self.message = str(exc)
            self.message_role = "warn"
            return
        # The key prompt is skipped when the variable already has a value.
        if proxy_mod.resolve_secret(secret_env, environ=self.runtime.environ) is None:
            key = self._prompt(
                win,
                f"set {secret_env} (masked)",
                ["the API key — written 0600, never shown or logged"],
                masked=True,
            )
            if key:
                try:
                    proxy_mod.set_secret_value(
                        proxy_mod.secret_env_path(self.runtime.environ),
                        secret_env,
                        key,
                    )
                except proxy_mod.ProxyError as exc:
                    self.message = str(exc)
                    self.message_role = "warn"
                    return
        key_set = proxy_mod.resolve_secret(secret_env, environ=self.runtime.environ) is not None
        self._load_facts()
        self.message_role = "accent"
        self.message = (
            f"provider {provider_id} saved"
            + ("" if key_set else f" (key {secret_env} not set — Enter on its row later)")
            + " — next: A add models on its row, "
            "then `claude-multi-proxy init` + `systemctl --user restart "
            "cli-proxy-api` (between turns)"
        )

    def _cancelled(self, what: str) -> None:
        self.message = f"{what} cancelled."
        self.message_role = "warn"

    def _add_models(self, win: Any) -> None:
        """A — mark provider models into the custom registry (020)."""

        fact = self.facts[self.selected]
        provider_id = fact["id"]
        provider = self.runtime.ordinary_docs["providers"]["providers"][provider_id]
        if provider["transport"]["kind"] == "oauth-pool":
            self._setup(win)
            return
        listed: list[dict[str, Any]] | None = None
        if proxy_mod.listing_supported(provider_id):
            public_listing = proxy_mod.listing_is_public(provider_id)
            listing_path = proxy_mod.listing_endpoint(
                provider_id, self.runtime.ordinary_docs["providers"]["providers"]
            )
            confirmed = tui.Modal(
                f"Query {fact['display']} for its model list?",
                [
                    "one read-only GET to the provider's model listing —"
                    " the endpoint is public; no key is sent."
                ]
                if public_listing
                else [
                    f"one read-only GET {listing_path} — nothing else is",
                    "sent; the key travels in the auth header only.",
                ],
                buttons=(("Query", True), ("Type in manually", False)),
            ).run(win, self.palette, background=self._draw)
            if confirmed:
                try:
                    listed = proxy_mod.list_provider_models(
                        provider_id,
                        self.runtime.ordinary_docs["providers"]["providers"],
                        environ=self.runtime.environ,
                    )
                except proxy_mod.ProxyError as exc:
                    self.message = f"{exc} — falling back to manual entry"
                    self.message_role = "warn"
        if listed is not None:
            registry = custom.load_registry(self.runtime.environ)
            catalog_wires = {
                model["wire_model"] for model in self.runtime.catalog.models.values()
            }
            custom_wires = {spec["wire_model"] for spec in registry["models"].values()}
            fresh = [
                entry
                for entry in listed
                if entry["id"] not in catalog_wires and entry["id"] not in custom_wires
            ]
            if not fresh:
                self.message = (
                    "nothing new: every listed model is already cataloged or marked"
                )
                self.message_role = "accent"
                return
            chooser = tui.SelectList(
                f"mark models from {fact['display']}",
                [
                    tui.SelectItem(
                        f"{entry['id']}"
                        + (
                            f" · {entry['context_length']} ctx"
                            if isinstance(entry.get("context_length"), int)
                            else ""
                        )
                    )
                    for entry in fresh
                ],
                footer=(
                    ("Space", "toggle"),
                    ("Enter", "mark selected"),
                    ("Esc", "cancel"),
                ),
                multi=True,
            )
            picked = chooser.run(win, self.palette)
            if picked is None:
                return self._cancelled("Add models")
            if not picked:
                self.message = "Add models: nothing selected (Space toggles, Enter marks)."
                self.message_role = "warn"
                return
            errors: list[str] = []
            marked = 0
            taken_ids = set(registry["models"]) | set(self.runtime.catalog.models)
            for index in picked:
                entry = fresh[index]
                context = entry.get("context_length")
                if not isinstance(context, int) or context < 8192:
                    # Never guess the bound: ask when the provider omits it.
                    answer = self._prompt(
                        win,
                        f"context tokens for {entry['id']}",
                        ["the listing gave no context_length — the bound",
                         "drives the session's compaction policy"],
                    )
                    if not answer:
                        errors.append(f"{entry['id']}: skipped (no context bound)")
                        continue
                    try:
                        context = int(answer)
                    except ValueError:
                        errors.append(f"{entry['id']}: invalid context {answer!r}")
                        continue
                # The wire id is not always a valid registry key (OpenRouter
                # wires carry `author/model` slashes) — derive a slash-free
                # key: the model basename, then the dash-joined full wire on
                # collisions.
                registry_id = _registry_id_for_wire(entry["id"], taken_ids)
                if registry_id is None:
                    errors.append(
                        f"{entry['id']}: no free registry key (basename and "
                        "dash-joined form both taken)"
                    )
                    continue
                try:
                    custom.add_model(
                        self.runtime.environ,
                        registry_id,
                        wire_model=entry["id"],
                        provider=provider_id,
                        context_tokens=context,
                        display=entry.get("display_name") or None,
                        created_via="discover",
                        catalog_providers=self.runtime.catalog.providers,
                        catalog_models=tuple(self.runtime.catalog.models.keys()),
                    )
                    taken_ids.add(registry_id)
                    marked += 1
                except (custom.CustomModelsError, OSError, ValueError) as exc:
                    errors.append(f"{entry['id']}: {exc}")
            self._load_facts()
            self.message_role = "accent" if not errors else "warn"
            self.message = (
                f"marked {marked} model(s) — apply with `claude-multi-proxy init` "
                "+ `systemctl --user restart cli-proxy-api` (between turns)"
                + ("; failed: " + "; ".join(errors) if errors else "")
            )
            return
        # Manual entry (the fallback and the qwen path).
        model_id = self._prompt(win, "model id", ["registry key, letters/digits/dashes"])
        if not model_id:
            return self._cancelled("Add models")
        wire = self._prompt(
            win,
            "wire model id",
            [f"the exact id the API expects (e.g. {model_id})"],
            initial=model_id,
        )
        if not wire:
            return self._cancelled("Add models")
        context_raw = self._prompt(
            win, "context tokens", ["the model's context window, e.g. 262144"]
        )
        if not context_raw:
            return self._cancelled("Add models")
        try:
            context = int(context_raw)
        except ValueError:
            self.message = f"context tokens must be a number, got {context_raw!r}"
            self.message_role = "warn"
            return
        display = self._prompt(
            win,
            "display name (optional)",
            ["shown in pickers; empty uses the id"],
        )
        if display is None:
            return self._cancelled("Add models")
        try:
            custom.add_model(
                self.runtime.environ,
                model_id,
                wire_model=wire,
                provider=provider_id,
                context_tokens=context,
                display=display or None,
                created_via="manual",
                catalog_providers=self.runtime.catalog.providers,
                catalog_models=tuple(self.runtime.catalog.models.keys()),
            )
        except (custom.CustomModelsError, OSError, ValueError) as exc:
            self.message = str(exc)
            self.message_role = "warn"
            return
        self._load_facts()
        self.message_role = "accent"
        self.message = (
            f"marked {model_id} — apply with `claude-multi-proxy init` + "
            "`systemctl --user restart cli-proxy-api` (between turns)"
        )

    def run(self, win: Any) -> None:
        tui.hide_cursor()
        while True:
            self._draw(win)
            key = tui.read_key(win)
            if key.kind == "resize":
                continue
            if key.kind == "ctrl" and key.ch == "c":
                raise KeyboardInterrupt
            if key.kind == "esc":
                return
            if key.kind == "up" or (key.kind == "char" and key.ch == "k"):
                if self.selected > 0:
                    self.selected -= 1
                continue
            if key.kind == "down" or (key.kind == "char" and key.ch == "j"):
                if self.selected < len(self.facts) - 1:
                    self.selected += 1
                continue
            if key.kind == "home":
                self.selected = 0
                continue
            if key.kind == "end":
                self.selected = max(0, len(self.facts) - 1)
                continue
            if key.kind == "char" and key.ch == "?":
                tui.Modal(
                    PROVIDERS_TITLE + " — help",
                    PROVIDERS_HELP.splitlines(),
                    buttons=(("Close", True),),
                ).run(win, self.palette, background=self._draw)
                continue
            if key.kind == "char" and key.ch.lower() == "n":
                self._add_provider(win)
                continue
            if key.kind == "char" and key.ch.lower() == "a":
                self._add_models(win)
                continue
            if key.kind == "char" and key.ch.lower() == "r":
                self._load_facts()
                self.message = "refreshed."
                self.message_role = "accent"
                continue
            if key.kind == "enter":
                self._setup(win)
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
    resume_decision: str | None = None,
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
    screen = _SessionsScreen(
        runtime, palette=palette, resume_decision=resume_decision
    )
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
                # A 4th element is the picked model from a T switch (D48).
                # Otherwise: a leftover observed_model after a gate repair
                # needs the explicit-model relaunch; the recorded model is
                # implied (review should-fix 7).
                model_id=(
                    result[3]
                    if len(result) > 3
                    else (
                        record.get("ordinary_model")
                        if "observed_model" in record
                        else None
                    )
                ),
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


def _forget_liveness_guard(
    runtime: Runtime, stable_id: str
) -> Callable[[dict[str, Any] | None], str | None]:
    """The shared forget refusal, run under the lifecycle lock (review).

    A liveness verdict taken before blocking on the lock is stale by the
    time deletion happens, so both the CLI handler and the picker pass this
    to ``forget_session`` as its under-lock pre-delete check. A corrupt
    record has no runtime id; the stable-id prefix + self checks still
    apply.
    """

    def check(current: dict[str, Any] | None) -> str | None:
        if runtime.environ.get("CLAUDE_MULTI_MANAGED_ID") == stable_id:
            return (
                "refusing to forget the session you are running "
                "inside — exit it first"
            )
        prefixes = _live_background_prefixes()
        if current is not None:
            live = _record_is_live(current, prefixes)
        else:
            live = any(stable_id.startswith(prefix) for prefix in prefixes)
        if live:
            return (
                f"session {stable_id} is live in the background — "
                "stop it first (`claude-multi sessions stop "
                f"{stable_id}`)"
            )
        return None

    return check


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
    """Shared stop guards; a refusal message, or None when stop is possible.

    Liveness is checked for the exact resume target that `claude stop`
    will be invoked for (the current runtime id). A live historical alias
    is a different native session — out of this command's scope (review:
    precheck and stop must target the same identity).
    """

    stable_id = sessions.managed_id(record)
    if runtime.environ.get("CLAUDE_MULTI_MANAGED_ID") == stable_id:
        return "refusing to stop the session you are running inside"
    runtime_id = sessions.runtime_session_id(record)
    if not any(
        runtime_id.startswith(prefix) for prefix in _live_background_prefixes()
    ):
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
) -> tuple[str, str, bool]:
    """Metadata-only: is the runtime transcript where the record points?

    Returns (status, detail, decoded): ("present", path, True) |
    ("elsewhere", project-path-or-slugs, decoded?) | ("missing", path, True).
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
    if expected.is_file():
        return ("present", str(expected), True)
    found = _slugs_for_session(runtime, runtime_id)
    decoded: set[str] = set()
    for slug in found:
        # Best-effort decode back to real project paths. Slugs are NOT
        # injective ("a-b/c" and "a/b-c" collide), so only a single
        # surviving candidate counts as exact — anything else falls back
        # to slug-level guidance (review must-fix: ambiguous decode).
        for candidate in _decode_project_slug_candidates(slug):
            decoded.add(str(candidate))
    if len(decoded) == 1:
        return ("elsewhere", decoded.pop(), True)
    if found:
        return ("elsewhere", ", ".join(sorted(found)), False)
    return ("missing", str(expected), True)


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
            lines=(sessions.relink_message(record),),
            actions=(("repair-resume", "Repair & resume"),),
        )
    # Transcript blockers outrank liveness: a missing transcript makes the
    # daemon question moot, and a force decision must never bypass them
    # (review must-fix 1).
    status, detail, decoded = _resume_transcript_status(runtime, record)
    if status == "elsewhere":
        runtime_id = sessions.runtime_session_id(record)
        home = Path(runtime.environ.get("HOME") or Path.home())
        expected = (
            home
            / ".claude"
            / "projects"
            / _native_project_slug(record["cwd"])
            / f"{runtime_id}.jsonl"
        )
        if decoded:
            remedy = (
                f"if the session was intentionally re-homed there, repair "
                f"the record with `claude-multi sessions relink-runtime "
                f"{stable_id} {runtime_id} --cwd {shlex.quote(detail)}`; "
                "otherwise move the transcript to the expected location "
                f"{expected} and resume from the recorded dir"
            )
        else:
            remedy = (
                "inspect those directories and relink with "
                "`claude-multi sessions relink-runtime "
                f"{stable_id} {runtime_id} --cwd <that project directory>` "
                "only if the session was intentionally re-homed; the "
                f"expected transcript location is {expected}"
            )
        text = (
            f"the transcript for runtime {runtime_id} was not found under "
            f"the recorded project dir ({record['cwd']}), but a file with "
            f"the same name exists under: {detail}. {remedy}."
        )
        return ResumeGate(
            kind="transcript-elsewhere",
            title="Transcript found in a different project",
            lines=(text,),
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
            lines=(text,),
            actions=(),
        )
    if status == "present" and not Path(record["cwd"]).is_dir():
        # The classic rename (deep analysis core P1): the transcript sits
        # exactly where the record points, but the recorded project
        # directory itself is gone — resume would fail entering the CWD at
        # launch. Name the two real exits up front: rename back, or move
        # the transcript into the new project dir and relink the record.
        runtime_id = sessions.runtime_session_id(record)
        text = (
            f"the recorded project dir ({record['cwd']}) is gone, though the "
            "transcript is still filed under it. If the directory was "
            "renamed or moved, either rename it back (resume then just "
            "works), or repair the record to the new location — `claude-multi "
            f"sessions relink-runtime {stable_id} {runtime_id} --cwd "
            "'<new project directory>'` — and resume again: the follow-up "
            "check then names the exact spot to move the transcript to."
        )
        return ResumeGate(
            kind="cwd-missing",
            title="Recorded project directory is gone",
            lines=(text,),
            actions=(),
        )
    prefixes = (
        live_prefixes if live_prefixes is not None else _live_background_prefixes()
    )
    # The resume conflict is only the RESUME TARGET being live (native
    # resume of the same UUID forks/refuses). A live historical alias is
    # an older branch — its hooks are stale-epoch-rejected, and resuming
    # the current runtime does not conflict with it (review: alias-aware
    # liveness must not gate). The ● row marker intentionally stays
    # lineage-broad for display.
    runtime_id = sessions.runtime_session_id(record)
    if any(runtime_id.startswith(prefix) for prefix in prefixes):
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
            lines=(text,),
            actions=(
                ("stop-resume", "Stop & resume"),
                ("force", "Resume anyway"),
            ),
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
    # Raw lines are stored unwrapped (text mode joins them verbatim);
    # wrap only for modal presentation (review must-fix 4).
    lines = [
        wrapped for line in gate.lines for wrapped in textwrap.wrap(line, width=60)
    ]
    choice = tui.Modal(
        gate.title, lines, buttons=tuple(buttons)
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
        # Rescan before granting the bypass: if the target still shows
        # live, ask for a retry instead of forcing into a fork (review).
        if any(
            runtime_id.startswith(prefix)
            for prefix in _live_background_prefixes()
        ):
            raise CLIError(
                "stop issued but the session still shows live — give it a "
                "moment and retry"
            )
        # No force exemption: the mandatory gate re-evaluates fresh at the
        # launch boundary — a stop that raced a relaunch is caught there
        # instead of being bypassed by a stale decision (review must-fix).
        return ("resume", record, None)
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
        return "[r]esume [t] switch model [f]orget"
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
        key=_record_sort_key_last_used,
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
                f"last used {_record_last_used_age(record)} · "
                f"created {_record_age(record)}  {_record_actions_label(record)}"
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
    return compiler.direct_model_for_selector(runtime.ordinary_docs, selector)


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

    # Bound original bytes before parsing (security lane): a hostile or
    # broken hook pipe must not allocate unbounded memory before the
    # strict-JSON limit ever runs.
    limit = strict_json.DEFAULT_LIMITS.max_bytes
    raw_stream = getattr(input_stream, "buffer", None)
    if raw_stream is not None:
        raw = raw_stream.read(limit + 1)
    else:  # injected text streams (tests) have no .buffer
        raw = input_stream.read(limit + 1).encode("utf-8")
    if len(raw) > limit:
        raise CLIError(f"session hook payload exceeds the {limit}-byte limit")
    try:
        payload = strict_json.loads(raw)
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
        # D44: model/cwd evidence is admissible only from the main session.
        # The 2.1.220 compact payload carries no agent-context marker
        # (probe-verified shape: cwd/hook_event_name/session_id/source/
        # transcript_path), so for MANAGED sessions — the production bleed
        # case — compact events are inadmissible unconditionally; a managed
        # model change is a transition (its start event reports the model)
        # or is re-observed at the next start/resume. Ordinary sessions
        # keep compact-model reconciliation (their in-session /model
        # tracking path); an unmarked compact bleed there is the accepted
        # residual documented in D44. agent_id/agent_transcript_path/
        # /subagents/ markers are honored as defense-in-depth for any
        # payload shape that carries them.
        transcript_path = payload.get("transcript_path")
        agent_context = bool(
            payload.get("agent_id")
            or payload.get("agent_transcript_path")
        ) or (isinstance(transcript_path, str) and "/subagents/" in transcript_path)
        if agent_context or (
            source == "compact"
            and current["session_type"] == sessions.SESSION_TYPE_MANAGED
        ):
            cwd = None
            model = None
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
            # Catalog drift (recorded lead removed/renamed) must stay
            # informational (R1 P2): skip model reconciliation, never crash
            # the hook — the runtime-id reconciliation below still runs.
            lead_model = runtime.catalog.models.get(lead_id)
            if lead_model is not None:
                equivalent = {
                    lead_model["wire_model"],
                    current["snapshot"]["lead"]["client_selector"],
                }
                if lead_model["context"]["client_tokens"] >= 1_000_000:
                    # Mirror compiler.direct_model_for_selector: Claude Code
                    # may report the canonical full selector even when the
                    # process entered through a gateway alias.
                    equivalent.add(lead_model["wire_model"] + "[1m]")
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


@dataclass(frozen=True)
class GatewaySnapshot:
    """One shared read of local gateway state (015 D-d, 018 providers modal).

    Loopback probe + names-only filesystem facts; no provider call, and no
    secret or auth-file contents. ``served`` is None when the daemon is down
    or answered non-200; ``config_drift`` is None when the on-disk config is
    unreadable; ``oauth_records`` maps pool -> credential-record count.
    """

    served: frozenset[str] | None
    gateway_down: bool
    models_status: int | None
    expected: frozenset[str]
    oauth_alias_pools: dict[str, str]
    render_error: str | None
    config_drift: bool | None
    config_note: str | None
    oauth_records: dict[str, int]


_OAUTH_LOGIN_COMMANDS = {"claude": "claude-login", "codex": "codex-device-login"}


def _oauth_credential_records(runtime: Runtime) -> dict[str, int]:
    """Pool -> credential-record count (names-only, symlink-refusing)."""

    gateway_info = runtime.catalog.docs["gateway"]["gateway"]
    providers = runtime.catalog.docs["providers"]["providers"]
    pools = sorted(
        {
            provider["transport"]["pool"]
            for provider in providers.values()
            if provider["transport"]["kind"] == "oauth-pool"
        }
    )
    counts = {pool: 0 for pool in pools}
    home = Path(runtime.environ.get("HOME") or Path.home())
    try:
        entries = list((home / gateway_info["auth_dir"]).iterdir())
    except OSError:
        return counts
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            continue
        for pool in pools:
            if entry.name.startswith(f"{pool}-") and entry.name.endswith(".json"):
                counts[pool] += 1
    return counts


def _gateway_snapshot(runtime: Runtime, token: str) -> GatewaySnapshot:
    """Collect the shared snapshot; each fact degrades independently."""

    try:
        served, models_status = launch.served_models(
            runtime.catalog.docs["gateway"], token
        )
        down = False
    except launch.LaunchError:
        served, down, models_status = None, True, None
    home = Path(runtime.environ.get("HOME") or Path.home())
    expected: frozenset[str] = frozenset()
    alias_pools: dict[str, str] = {}
    render_error: str | None = None
    drift: bool | None = None
    config_note: str | None = None
    try:
        document, _available, _unavailable = render.build_config_document(
            runtime.ordinary_docs["gateway"],
            runtime.ordinary_docs["providers"]["providers"],
            runtime.ordinary_docs["models"]["models"],
            home=home,
            gateway_token=token,
            resolve_secret=lambda name: proxy_mod.resolve_secret(
                name, environ=runtime.environ
            ),
        )
        expected = render.rendered_selectors(document)
        alias_pools = {
            entry["alias"]: pool
            for pool, entries in document.get("oauth-model-alias", {}).items()
            for entry in entries
        }
        try:
            on_disk = state.read_private(proxy_mod.config_dir(home) / "config.yaml")
            # Byte equality only — the deterministic render makes drift
            # detection exact; contents (which carry secrets) are compared,
            # never displayed.
            drift = on_disk != render.emit_yaml(document).encode("utf-8")
        except (OSError, ValueError) as exc:
            drift = None
            config_path = proxy_mod.config_dir(home) / "config.yaml"
            # Only an existing-but-unreadable file is noteworthy (a wrong
            # mode silently disables the radar); a missing one is the
            # never-initialized state, which readiness already covers.
            if config_path.exists():
                config_note = f"on-disk gateway config unreadable for the drift check: {exc}"
    except (
        render.RenderError,
        proxy_mod.ProxyError,
        UnicodeDecodeError,
        OSError,
        ValueError,
    ) as exc:
        render_error = str(exc)
    return GatewaySnapshot(
        served=None if down else served,
        models_status=models_status,
        gateway_down=down,
        expected=expected,
        oauth_alias_pools=alias_pools,
        render_error=render_error,
        config_drift=drift,
        oauth_records=_oauth_credential_records(runtime),
        config_note=config_note,
    )


def _doctor_served_report(runtime: Runtime, token: str) -> tuple[list[str], list[str]]:
    """Served-vs-rendered selector cross-check (015 D-d). Loopback only.

    The running gateway's /v1/models is its in-process registry — with
    --local-model it mirrors the config the daemon started with. Three
    distinct diagnoses, in order: on-disk config drift (init not run),
    unserved rendered selectors (daemon not restarted — or, for OAuth pools
    without a credential record, login missing), and stale claude-multi-shaped
    served selectors (removed aliases). Embedded-registry extras (unrelated
    Anthropic/Codex models) are ignored by design. Wire-name remappings are
    invisible to /v1/models — the config-drift byte check covers those.
    """

    snap = _gateway_snapshot(runtime, token)
    if snap.gateway_down:
        # Readiness passed moments ago but the models probe failed — a
        # restart is likely in flight. Say the radar never ran (silence
        # would read as a clean bill).
        return [], [
            "the gateway changed state after the readiness check (a restart "
            "in flight?); the served-selector cross-check was skipped — "
            "rerun doctor when it settles"
        ]
    problems: list[str] = []
    info: list[str] = []
    if snap.config_note is not None:
        info.append(snap.config_note)
    if snap.render_error is not None:
        info.append(f"served-selector cross-check skipped: {snap.render_error}")
        return problems, info
    if snap.config_drift:
        problems.append(
            "the on-disk gateway config differs from a fresh render of the "
            "installed catalog (a catalog edit or secret rotation is not "
            "applied): run `claude-multi-proxy init`, then `systemctl --user "
            "restart cli-proxy-api`"
        )
        # Selector classification stops here (not independently actionable
        # under drift) — but a missing OAuth credential record is its own
        # action, so it still surfaces.
        for pool, count in sorted(snap.oauth_records.items()):
            if count == 0:
                command = _OAUTH_LOGIN_COMMANDS.get(pool, f"{pool}-login")
                info.append(
                    f"the {pool} OAuth pool has no credential record: sign in "
                    f"with `claude-multi-proxy {command}`"
                )
        return problems, info
    if snap.served is None:
        if snap.models_status == 401:
            # The running daemon holds a different token than the rendered
            # config (rotated token + init without restart): every session
            # would 401 while healthz stays green. This is a problem, not
            # an advisory skip (gateway-lane finding).
            problems.append(
                "the running gateway rejected the local token (401 on "
                "/v1/models): the daemon serves an older config — restart "
                "with `systemctl --user restart cli-proxy-api`"
            )
            return problems, info
        info.append(
            "local gateway: /v1/models returned a non-200 status; the "
            "served-selector cross-check was skipped"
        )
        return problems, info
    missing = sorted(snap.expected - snap.served)
    oauth_missing: dict[str, list[str]] = {}
    direct_missing: list[str] = []
    for selector in missing:
        pool = snap.oauth_alias_pools.get(selector)
        if pool is None:
            direct_missing.append(selector)
        else:
            oauth_missing.setdefault(pool, []).append(selector)
    restart_note = (
        "`systemctl --user restart cli-proxy-api` (the daemon does not "
        "hot-reload a re-rendered config)"
    )
    for selector in direct_missing[:3]:
        problems.append(
            f"the running gateway does not serve rendered selector "
            f"{selector!r}: {restart_note}"
        )
    if len(direct_missing) > 3:
        problems.append(
            f"…and {len(direct_missing) - 3} more unserved rendered selectors: "
            "`systemctl --user restart cli-proxy-api`"
        )
    for pool, selectors in sorted(oauth_missing.items()):
        shown = ", ".join(selectors[:3]) + ("…" if len(selectors) > 3 else "")
        if snap.oauth_records.get(pool, 0) == 0:
            command = _OAUTH_LOGIN_COMMANDS.get(pool, f"{pool}-login")
            info.append(
                f"the {pool} OAuth pool has no credential record: rendered "
                f"selector(s) {shown} stay unserved until "
                f"`claude-multi-proxy {command}`"
            )
        else:
            problems.append(
                f"the running gateway does not serve rendered selector(s) "
                f"{shown} although a {pool} credential record exists: {restart_note}"
            )
    stale = [
        selector
        for selector in sorted(snap.served - snap.expected)
        if selector.startswith(("claude-multi-", "gpt-multi-"))
    ]
    if stale:
        info.append(
            "gateway serves selector(s) absent from the current render: "
            + ", ".join(stale[:3])
            + ("…" if len(stale) > 3 else "")
            + " — `systemctl --user restart cli-proxy-api`; if they persist "
            "afterwards, run `claude-multi-proxy init` and restart again"
        )
    return problems, info


def _collect_doctor_reports(
    runtime: Runtime,
) -> tuple[list[str], list[str], list[str]]:
    """(problems, info, attention) for doctor — shared by CLI and the TUI.

    Same checks, same lines: composition validation and secrets, binary
    verification, daemon, contract source, gateway readiness, scope/session
    integrity, collisions, the re-pin early-warning.
    """

    problems: list[str] = []
    custom_conflicts = runtime.custom_conflicts()
    if custom_conflicts:
        # Ignored by the merge (catalog wins) but a real misconfiguration:
        # attention with the exact fix, never silent.
        scope_attention_extra = [
            "custom registry entries shadow catalog ids and were IGNORED: "
            + ", ".join(custom_conflicts)
            + " — remove or rename them in ~/.config/claude-multi/custom.json"
        ]
    else:
        scope_attention_extra = []
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
            gateway_token = launch.check_readiness(runtime.catalog.docs["gateway"])
        except launch.LaunchError as exc:
            problems.append(
                f"local gateway: {exc} — start it with `systemctl --user "
                "start cli-proxy-api`"
            )
        else:
            served_callback = runtime.doctor_served_callback or _doctor_served_report
            served_problems, served_info = served_callback(runtime, gateway_token)
            problems.extend(served_problems)
            info_lines.extend(served_info)
    scope_info, scope_problems, scope_attention = _doctor_scope_report(runtime)
    info_lines.extend(scope_info)
    problems.extend(scope_problems)
    scope_attention.extend(scope_attention_extra)
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
                runtime.ordinary_docs, args.direct_model
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
        launched_model = runtime.ordinary_docs["models"]["models"][
            prepared.record["ordinary_model"]
        ]
        launched_provider = runtime.ordinary_docs["providers"]["providers"][
            launched_model["provider"]
        ]
        if launched_provider["transport"]["kind"] == "direct":
            unavailable_reason = _ordinary_unavailable(runtime).get(
                launched_model["provider"]
            )
            if unavailable_reason is not None:
                # Non-blocking (D46): the TUI picker asks for confirmation,
                # the CLI stays permissive — but the operator is told why
                # the session's requests may fail before the process starts.
                # Never on --print-launch: a dry-run report stays clean.
                output_stream.write(
                    f"warning: {unavailable_reason} — requests may fail "
                    "unless the running gateway still serves an earlier "
                    "rendered config.\n"
                    f"fix: {_connect_hint(runtime, launched_model['provider'])}\n"
                )
        # execve never flushes Python buffers: force the note/warning above
        # out before control reaches the launch boundary.
        output_stream.flush()
        return runtime.perform(
            prepared,
            resume_decision="force" if getattr(args, "force", False) else None,
        )

    if args.command == "custom":
        command = args.custom_command
        if command == "list":
            registry = custom.load_registry(runtime.environ)
            if not registry["providers"] and not registry["models"]:
                output_stream.write("(no custom providers or models registered)\n")
                return 0
            for provider_id, spec in sorted(registry["providers"].items()):
                output_stream.write(
                    f"provider {tui.visible_text(provider_id)}\t{spec['base_url']} · "
                    f"{spec['auth_kind']} · env:{spec['secret_env']}\n"
                )
            for model_id, spec in sorted(registry["models"].items()):
                output_stream.write(
                    f"model {tui.visible_text(model_id)}\twire={spec['wire_model']} · "
                    f"provider={spec['provider']} · context={spec['context_tokens']} · "
                    f"{spec['created_via']}\n"
                )
            return 0
        if command == "add-provider":
            custom.add_provider(
                runtime.environ,
                args.name,
                base_url=args.base_url,
                auth_kind=args.auth,
                secret_env=args.secret_env,
                header=args.header,
                display=args.display,
                catalog_providers=runtime.catalog.providers,
            )
            output_stream.write(
                f"provider {args.name} registered — apply with "
                "`claude-multi-proxy init` + `systemctl --user restart cli-proxy-api`\n"
            )
            return 0
        if command == "remove-provider":
            removed = custom.remove_provider(runtime.environ, args.name)
            output_stream.write(f"{'Removed' if removed else 'Not found'}: {args.name}\n")
            return 0 if removed else 2
        if command == "add-model":
            custom.add_model(
                runtime.environ,
                args.name,
                wire_model=args.wire,
                provider=args.provider,
                context_tokens=args.context,
                display=args.display,
                created_via="manual",
                catalog_providers=runtime.catalog.providers,
                catalog_models=tuple(runtime.catalog.models.keys()),
            )
            output_stream.write(
                f"model {args.name} marked — apply with `claude-multi-proxy init` "
                "+ `systemctl --user restart cli-proxy-api`\n"
            )
            return 0
        if command == "remove-model":
            removed = custom.remove_model(runtime.environ, args.name)
            output_stream.write(f"{'Removed' if removed else 'Not found'}: {args.name}\n")
            return 0 if removed else 2

    if args.command == "compose":
        command = args.compose_command
        if command == "list":
            here, elsewhere = _composition_recency(runtime)
            merged = _merged_recency(here, elsewhere)
            order = _pick_order_from(runtime.compositions.names(), here, elsewhere)
            for name in order:
                origin = "user" if runtime.compositions.has_user(name) else "trusted seed"
                stamp = merged.get(name)
                last_used = _last_used_age(stamp) if stamp else "-"
                output_stream.write(f"{tui.visible_text(name)}\t{origin}\t{last_used}\n")
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
                        resume_decision=(
                            "force" if getattr(args, "force", False) else None
                        ),
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
                output_stream.write(
                    tui.visible_text(sessions.relink_message(record)) + "\n"
                )
            output_stream.write(strict_json.canonical_file_bytes(record).decode("utf-8"))
            return 0
        if command == "forget":
            record: dict[str, Any] | None = None
            try:
                record = runtime.session_store.resolve(args.uuid)
                stable_id = sessions.managed_id(record)
            except sessions.SessionError as exc:
                if "no managed session matches" in str(exc):
                    output_stream.write(f"Not found: {args.uuid}\n")
                    return 0
                # A corrupt record is exactly what forget exists for (deep
                # analysis core P1): an exact UUID with a present record file
                # forgets load-free — scope removal + pointer sweep by id.
                if not (
                    sessions.UUID4.fullmatch(args.uuid)
                    and runtime.session_store.exists(args.uuid)
                ):
                    raise
                stable_id = args.uuid
                output_stream.write(
                    f"note: record {stable_id} is unreadable ({exc}); "
                    "forgetting it load-free.\n"
                )
            # Live guard (deep analysis core P2 + review must-fix): the
            # check runs UNDER the lifecycle lock with a fresh prefix scan —
            # a liveness verdict taken before blocking on the lock would be
            # stale by the time deletion happens.
            removed, scope_removed = runtime.session_store.forget_session(
                stable_id,
                pre_delete_check=_forget_liveness_guard(runtime, stable_id),
            )
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
                    runtime.ordinary_docs, args.link_model
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

    if args.command == "discover":
        providers = runtime.ordinary_docs["providers"]["providers"]
        if args.provider not in providers:
            raise CLIError(
                f"unknown provider {args.provider!r} (have: {', '.join(sorted(providers))})"
            )
        # An explicit operator invocation is the per-call approval; this is
        # the only place claude-multi ever calls a provider listing API.
        try:
            entries = proxy_mod.list_provider_models(
                args.provider, providers, environ=runtime.environ
            )
        except proxy_mod.ProxyError as exc:
            raise CLIError(str(exc)) from exc
        by_wire: dict[str, str] = {}
        for catalog_id, model in runtime.catalog.models.items():
            # Provider-scoped: the same wire on a DIFFERENT provider is a
            # different route, not this listing's catalog entry (022 — the
            # multi-route convention: one entry per (model, provider)).
            if model["provider"] == args.provider:
                by_wire.setdefault(model["wire_model"], catalog_id)
        custom_wires: dict[str, str] = {}
        for custom_id, spec in custom.load_registry(runtime.environ)["models"].items():
            if spec["provider"] == args.provider:
                custom_wires.setdefault(spec["wire_model"], custom_id)
        if not entries:
            output_stream.write(f"{args.provider}: the provider advertised no models\n")
            return 0
        for entry in entries:
            # Provider-supplied strings pass the terminal sanitizer (they are
            # external input, same rule as filesystem-derived text).
            wire = tui.visible_text(entry["id"])
            catalog_id = by_wire.get(entry["id"])
            custom_id = custom_wires.get(entry["id"])
            status = (
                f"cataloged as {catalog_id}"
                if catalog_id is not None
                else (
                    f"marked custom as {custom_id}"
                    if custom_id is not None
                    else "not registered — mark it in the TUI (G → P → A) or "
                    "onboard it for compositions via `claude-multi-dev model add --like`"
                )
            )
            context = (
                f" ctx={entry['context_length']}"
                if isinstance(entry.get("context_length"), int)
                else ""
            )
            efforts = (
                f" efforts={','.join(tui.visible_text(e) for e in entry['think_efforts'])}"
                if entry.get("think_efforts")
                else ""
            )
            output_stream.write(f"{wire}\t{status}{context}{efforts}\n")
        return 0

    if args.command == "models":
        default_doc = runtime.compositions.load("default")
        default_models = default_doc["availability"]["models"]
        for model_id, model in sorted(runtime.catalog.models.items()):
            provider = runtime.catalog.providers[model["provider"]]["display"]
            capabilities = ",".join(model["capabilities"])
            context = model["context"]
            scalar = context["scalar_tokens"] or "none"
            profile = context["ordinary_profile"] or "agents-only"
            typed = _ordinary_typed_selectors(model)
            new = "" if model_id in default_models else " · not in default"
            output_stream.write(
                f"{model_id}\t{model['display']}\t{provider}\t{capabilities}\t"
                f"client={context['client_tokens']} provider={context['provider_tokens']} "
                f"scalar={scalar} profile={profile} wire={model['wire_model']}{new}\n"
                f"  in-session: {typed}\n"
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
    if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
        # A retired ordinary profile (e.g. 'sol' after D57) can never
        # recompile — --repair would just error. Point at the re-pinning
        # resume instead (review sweep, 023).
        try:
            compiler.direct_context_profile(
                runtime.ordinary_docs, record["ordinary_model"]
            )
            compiler.direct_profile_selectors(
                runtime.ordinary_docs, record["context_profile"]
            )
        except compiler.CompilerError as exc:
            reason = (
                f"its recorded model {record['ordinary_model']!r} is no longer "
                "an ordinary lead"
                if "lead" in str(exc)
                else f"its recorded profile {record['context_profile']!r} is retired"
            )
            repair = (
                f"{reason} — resume with an explicit supported --model to "
                f"re-pin the record: claude-gateway -r {session_id} "
                f"--model {record['ordinary_model']}"
            )
    try:
        if record["session_type"] == sessions.SESSION_TYPE_ORDINARY:
            expected = scope_mod.compile_ordinary_scope(
                managed_id=session_id,
                hook_command=runtime.hook_command,
                available_models=compiler.direct_profile_selectors(
                    runtime.ordinary_docs, record["context_profile"]
                ),
                default_model=runtime.ordinary_docs["models"]["models"][
                    record["ordinary_model"]
                ]["client_selector"],
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
                    runtime.ordinary_docs, record["context_profile"]
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
            ordinary_docs=runtime.ordinary_docs,
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
                ordinary_docs=runtime.ordinary_docs,
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


def _resolve_resume_target(runtime: Runtime, value: str) -> str:
    """Resolve a resume argument to a managed session UUID.

    Native Claude's exit hint prints ``claude --resume "cm:<composition>"``
    (the display name, not the UUID). Accept that form here: an exact UUIDv4
    resumes as before; otherwise the value (with an optional ``cm:`` prefix)
    matches managed sessions by composition name OR by the generated display
    name (``cm:<composition>@<project>``, issue 013 — the exact form the
    exit hint prints for project-qualified sessions). Exactly one match
    resumes, several list the candidates with their UUIDs, none fails with
    a pointer to ``claude-multi sessions list``.
    """

    if sessions.UUID4.fullmatch(value):
        return value
    name = value.removeprefix("cm:")
    matches = [
        record
        for record in _session_records(runtime)
        if record["session_type"] == sessions.SESSION_TYPE_MANAGED
        and (
            record["composition_name"] == name
            or value
            == compiler.session_display_name(
                f"cm:{record['composition_name']}", record["cwd"]
            )
        )
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


RESUME_FILE_OVERRIDE_REFUSAL = (
    "resume always uses the recorded composition; --composition-file {path!r} "
    "does not apply to session {uuid} — a file's content is never verifiably "
    "the recorded intent. To change composition: `claude-multi sessions "
    "transition {uuid} --composition NAME`"
)


def _refuse_resume_override(
    composition_name: str | None,
    record: dict[str, Any],
    *,
    composition_file: str | None = None,
) -> None:
    """R1 P1: ordinary resume never accepts a changed composition.

    A name equal to the recorded one is not an override (the recorded intent
    is re-resolved anyway); anything else is refused with the transition
    command named as the one path. A composition FILE is refused
    unconditionally: its content is never verifiably the recorded intent,
    even when its name matches.
    """

    if composition_file is not None:
        raise CLIError(
            RESUME_FILE_OVERRIDE_REFUSAL.format(
                path=composition_file, uuid=sessions.managed_id(record)
            )
        )
    if composition_name is not None and composition_name != record["composition_name"]:
        raise CLIError(
            RESUME_OVERRIDE_REFUSAL.format(
                name=composition_name, uuid=sessions.managed_id(record)
            )
        )


def _load_composition_argument(
    runtime: Runtime, value: str, inp: TextIO
) -> dict[str, Any]:
    """Load an unsaved composition document from a file path or stdin ('-').

    The stdin read is bounded at the strict-JSON byte limit plus one, so an
    oversized pipe is rejected before an unbounded allocation.
    """

    try:
        if value == "-":
            limit = strict_json.DEFAULT_LIMITS.max_bytes
            # Bound ORIGINAL BYTES, not decoded characters: a text stream's
            # read(n) counts characters (and may normalize CRLF), which is
            # not the strict-JSON byte limit.
            raw_stream = getattr(inp, "buffer", None)
            if raw_stream is not None:
                raw = raw_stream.read(limit + 1)
            else:  # injected text streams (tests) have no .buffer
                raw = inp.read(limit + 1).encode("utf-8")
            if len(raw) > limit:
                raise composition.CompositionError(
                    f"$: input exceeds the {limit}-byte limit"
                )
            return composition.validate_document(
                strict_json.loads(raw), runtime.compositions.schema
            )
        return composition.load_composition_file(value, runtime.compositions.schema)
    except (OSError, ValueError) as exc:
        raise CLIError(f"cannot load composition file {value!r}: {exc}") from exc


# Commands whose output is a report: honor stdout redirection/pipes instead
# of writing to the controlling terminal. Interactive-first commands (compose
# editor, sessions transition, direct, bare launch) keep the tty stream.
_STDOUT_REPORT_COMMANDS = frozenset(
    {
        ("doctor", None),
        ("discover", None),
        ("models", None),
        ("show", None),
        ("update", None),
        ("session-event", None),
        ("compose", "list"),
        ("custom", "list"),
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
        if getattr(args, "composition_file", None) == "-" and interactive is not False:
            # stdin carries the document, so it cannot also feed key input:
            # '-' forces noninteractive (the --help promise), unconditionally.
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
                        resume_decision="force" if args.force else None,
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
            _refuse_resume_override(
                args.composition, record, composition_file=args.composition_file
            )
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
            _refuse_resume_override(
                args.composition, record, composition_file=args.composition_file
            )
            plan = managed_plan(runtime, record)
            plan.legacy_requested = args.legacy
        else:
            # Only a FRESH launch can need an explicit composition: resume and
            # continue take the recorded intent, so they must never demand one.
            if not interactive and args.composition is None and args.composition_file is None:
                raise CLIError(
                    "noninteractive launch requires --composition NAME or --composition-file PATH; no default was selected"
                )
            if args.composition_file is not None:
                document = _load_composition_argument(
                    runtime, args.composition_file, inp
                )
                source = (
                    "Composition from stdin"
                    if args.composition_file == "-"
                    else f"Composition file {args.composition_file}"
                )
            elif args.composition is not None:
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
    except (CLIError, sessions.SessionError, state.StateError, catalog.CatalogError, compiler.CompilerError, composition.CompositionError, launch.LaunchError, custom.CustomModelsError) as exc:
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
            if (project_dir / f"{session_id}.jsonl").is_file():
                found.add(project_dir.name)
    except OSError:
        # An unreadable projects root is indistinguishable from "no
        # transcripts" at metadata level — treated as missing (the
        # conservative guidance either way).
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
