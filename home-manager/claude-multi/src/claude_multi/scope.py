"""Durable per-session scopes for claude-multi.

A scope is the per-session directory ``<state_root>/scopes/<uuid>/`` holding
every generated, policy-relevant file for one managed session::

    scopes/<uuid>/
    ├── .claude/agents/<variant-id>.md   # one per selected variant
    └── settings.json                    # compiled session settings

``compile_scope`` is pure: it maps (resolved composition, trusted catalog
roles and prompt bodies, catalog metadata) to a plan of file bytes plus the
compiled settings document. ``write_scope`` is the only effect: it stages the
plan into the sibling ``scopes/.<uuid>.new/`` with per-file
``state.atomic_write``, then renames the staging directory into
``scopes/<uuid>/`` and fsyncs the ``scopes/`` parent, so a crash never
leaves a half-written live scope. Scope content is a pure function of
(record composition, installed catalog); a lost or drifted scope is always
re-derivable from those two authorities.

The exact-``cm-*`` collision gate (:func:`find_cm_collisions`) scans project
agent directories, passthrough ``--add-dir`` agent directories, and the
managed settings agents directory for agent files whose frontmatter ``name``
exactly equals one of the session's generated variant IDs. A collision would
silently shadow a guaranteed definition (closest-to-cwd precedence), so the
launcher fails closed naming the offending file. The scanner is line-wise:
no YAML dependency, tolerant of unreadable files (a file the session user
cannot read cannot shadow anything either).
"""

from __future__ import annotations

import errno
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from . import compiler, state, strict_json
from .composition import ResolvedComposition


class ScopeError(ValueError):
    """Raised when a scope cannot be compiled or written (fail closed)."""


# SPEC 2.1 durable floor, verbatim. Every generated description carries the
# no-substitution sentinel so the rule survives lead-appendix loss (U2).
SENTINEL_SUFFIX = (
    "Managed cm session: if a selected cm-* type is unavailable, stop; "
    "never substitute a generic agent."
)

# SPEC 2.1 cross-family independence rule, verbatim template; carried by
# cm-reviewer variant descriptions only.
REVIEW_INDEPENDENCE = (
    "Review independence: a change authored by a {family}-family variant "
    "must not receive its sole verdict from another {family}-family variant "
    "while a cross-family reviewer is enabled."
)

# Closed compiled-settings key allowlist (SPEC 2.2). Nothing else is emitted.
COMPILED_SETTINGS_KEYS = frozenset(
    {
        "disableWorkflows",
        "workflowSizeGuideline",
        "workflowKeywordTriggerEnabled",
        "permissions",
        "availableModels",
        "worktree",
    }
)

# The three workflow keys the package base settings.json must supply.
_BASE_WORKFLOW_KEYS = ("disableWorkflows", "workflowSizeGuideline", "workflowKeywordTriggerEnabled")

DEFAULT_MANAGED_AGENTS_DIR = Path("/etc/claude-code/.claude/agents")

_AGENT_DIR_RELPATH = PurePosixPath(".claude/agents")


def _check_session_id(session_id: str) -> str:
    """Require the canonical lowercase UUIDv4 used by scope path names."""

    try:
        parsed = uuid.UUID(session_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ScopeError(f"session id {session_id!r} is not a UUIDv4") from exc
    if parsed.version != 4 or str(parsed) != session_id:
        raise ScopeError(f"session id {session_id!r} is not a UUIDv4")
    return session_id


@dataclass(frozen=True)
class CatalogMeta:
    """Catalog-derived metadata the scope compiler needs beyond the resolution.

    ``base_settings`` is the package base ``settings.json`` document (merge
    base); ``client_selectors`` is every client selector the trusted catalog
    declares (model-level and per-lane); ``generic_agent_aliases`` is the
    native-contract's recorded generic agent aliases.
    """

    base_settings: dict[str, Any]
    client_selectors: tuple[str, ...]
    generic_agent_aliases: tuple[str, ...]


@dataclass(frozen=True)
class ScopePlan:
    """Pure scope content: file bytes by scope-relative path, plus settings."""

    agent_files: dict[str, bytes]
    settings: dict[str, Any]

    @property
    def agent_names(self) -> frozenset[str]:
        """Generated variant IDs (the ``cm-*`` names the collision gate owns)."""

        return frozenset(PurePosixPath(relpath).stem for relpath in self.agent_files)


def catalog_meta_from_docs(docs: dict[str, Any]) -> CatalogMeta:
    """Build :class:`CatalogMeta` from the loaded trusted catalog documents."""

    selectors: set[str] = set()
    for model in docs["models"]["models"].values():
        selectors.add(model["client_selector"])
        for lane in model["lanes"].values():
            selectors.add(lane["client_selector"])
    return CatalogMeta(
        base_settings=dict(docs["settings"]),
        client_selectors=tuple(sorted(selectors)),
        generic_agent_aliases=tuple(
            docs["native-contract"]["generic_agent_aliases"]["values"]
        ),
    )


def _yaml_double_quoted(value: str) -> str:
    """YAML double-quoted scalar: safe for descriptions containing ': '."""

    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _agent_file_bytes(
    resolved: ResolvedComposition,
    roles: dict[str, Any],
    prompt_bodies: dict[str, bytes],
    variant: Any,
) -> bytes:
    role = roles[variant.role]
    if role.get("tools") or role.get("disallowed_tools"):
        raise ScopeError(
            f"role {variant.role!r} declares frontmatter tools/disallowed_tools; "
            "emitting them is not implemented (fail closed)"
        )
    if variant.agent_effort == "ultracode":
        raise ScopeError(
            f"variant {variant.id!r}: ultracode is a lead-only effort and must "
            "never appear as an agent effort value"
        )
    description = compiler.variant_description(
        role, {"display": variant.display}, variant
    )
    description = f"{description} {SENTINEL_SUFFIX}"
    if variant.role == "cm-reviewer":
        description = (
            f"{description} {REVIEW_INDEPENDENCE.format(family=variant.family)}"
        )
    lines = [
        "---",
        f"name: {variant.id}",
        f"description: {_yaml_double_quoted(description)}",
        f"model: {variant.client_selector}",
        f"effort: {variant.agent_effort}",
    ]
    if variant.isolation is not None:
        lines.append(f"isolation: {variant.isolation}")
    lines.append("---")
    lines.append("")
    header = "\n".join(lines) + "\n"
    return header.encode("utf-8") + prompt_bodies[variant.role]


def compile_scope(
    resolved: ResolvedComposition,
    roles: dict[str, Any],
    prompt_bodies: dict[str, bytes],
    catalog_meta: CatalogMeta,
) -> ScopePlan:
    """Pure per-session scope plan. No effects; fail closed on policy gaps."""

    agent_files: dict[str, bytes] = {}
    for variant in resolved.variants:
        relpath = str(_AGENT_DIR_RELPATH / f"{variant.id}.md")
        agent_files[relpath] = _agent_file_bytes(
            resolved, roles, prompt_bodies, variant
        )

    _, denies = compiler.compile_native_policy(
        resolved.native_agents, catalog_meta.generic_agent_aliases
    )
    settings = dict(catalog_meta.base_settings)
    settings["disableWorkflows"] = resolved.workflows == "off"
    settings["permissions"] = {"deny": denies}
    settings["availableModels"] = sorted(
        set(catalog_meta.client_selectors) | {resolved.lead.client_selector}
    )
    if any(variant.isolation == "worktree" for variant in resolved.variants):
        settings["worktree"] = {"baseRef": "head"}
    unknown = sorted(set(settings) - COMPILED_SETTINGS_KEYS)
    if unknown:
        raise ScopeError(
            f"compiled settings carry keys outside the closed allowlist: {unknown}"
        )
    missing = [key for key in _BASE_WORKFLOW_KEYS if key not in settings]
    if missing:
        raise ScopeError(
            f"package base settings do not supply required workflow keys {missing}"
        )
    return ScopePlan(agent_files=agent_files, settings=settings)


def plan_hash(plan: ScopePlan) -> str:
    """Content hash of a plan: canonical settings plus per-file digests."""

    manifest = {
        "agent_files": {
            relpath: "sha256:" + strict_json.sha256_hex(data)
            for relpath, data in sorted(plan.agent_files.items())
        },
        "settings": plan.settings,
    }
    return strict_json.bundle_digest(manifest)


def scope_dir(state_root: Path | str, session_id: str) -> Path:
    """The live scope directory for a session."""

    return Path(state_root) / "scopes" / _check_session_id(session_id)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_tree(path: Path) -> None:
    """Remove a scope tree; refuse symlinks and non-directories."""

    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise state.StateError(errno.ELOOP, f"scope path {path} is a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise state.StateError(
            errno.ENOTDIR, f"scope path {path} is not a directory"
        )
    shutil.rmtree(path)


def _check_relpath(relpath: str) -> PurePosixPath:
    candidate = PurePosixPath(relpath)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise ScopeError(f"unsafe scope-relative path {relpath!r}")
    return candidate


def write_scope(state_root: Path | str, session_id: str, plan: ScopePlan) -> Path:
    """Stage the plan into ``scopes/.<uuid>.new/`` and rename it into place.

    Per-file writes use ``state.atomic_write`` (mode 0600); directories are
    mode 0700 via ``state.ensure_private_dir``. A stale staging directory from
    an interrupted write is removed first. If a live scope already exists it
    is removed just before the rename; the record remains the authority and
    the scope is re-derivable from record+catalog across that window. The
    ``scopes/`` parent is fsynced after the rename.
    """

    _check_session_id(session_id)
    scopes_root = state.ensure_private_dir(Path(state_root) / "scopes")
    staging = scopes_root / f".{session_id}.new"
    live = scopes_root / session_id
    if os.path.lexists(staging):
        _remove_tree(staging)
    state.ensure_private_dir(staging)
    for relpath, data in sorted(plan.agent_files.items()):
        relative = _check_relpath(relpath)
        target = staging.joinpath(*relative.parts)
        state.ensure_private_dir(target.parent)
        state.atomic_write(target, data)
    state.atomic_write(
        staging / "settings.json", strict_json.canonical_file_bytes(plan.settings)
    )
    if os.path.lexists(live):
        _remove_tree(live)
    os.rename(staging, live)
    _fsync_directory(scopes_root)
    return live


def remove_scope(state_root: Path | str, session_id: str) -> bool:
    """Remove a session's live scope and any stale staging dir. Idempotent."""

    _check_session_id(session_id)
    scopes_root = Path(state_root) / "scopes"
    removed = False
    for candidate in (
        scopes_root / session_id,
        scopes_root / f".{session_id}.new",
    ):
        if os.path.lexists(candidate):
            _remove_tree(candidate)
            removed = True
    if removed:
        _fsync_directory(scopes_root)
    return removed


def _frontmatter_name(path: Path) -> str | None:
    """Line-wise frontmatter ``name:`` extraction (no YAML dependency).

    Returns None for files without a frontmatter block or without a name;
    unreadable files are skipped by the caller (they cannot shadow a loaded
    definition for the same user). YAML inline comments are ignored unless
    they occur inside a quoted scalar.
    """

    try:
        with open(path, "rb") as handle:
            raw = handle.read(65536)
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    name: str | None = None
    for line in lines[1:]:
        stripped = line.strip()
        if stripped == "---":
            return name
        if stripped.startswith("name:") or stripped.startswith("name :"):
            value = stripped.split(":", 1)[1].strip()
            quote: str | None = None
            escaped = False
            for index, character in enumerate(value):
                if quote == '"':
                    if escaped:
                        escaped = False
                    elif character == "\\":
                        escaped = True
                    elif character == quote:
                        quote = None
                elif quote == "'":
                    if character == quote:
                        quote = None
                elif character in "\"'":
                    quote = character
                elif character == "#" and (
                    index == 0 or value[index - 1].isspace()
                ):
                    value = value[:index].rstrip()
                    break
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            name = value
    return None


def _scan_agents_dir(
    directory: Path, generated_names: frozenset[str]
) -> list[tuple[Path, str]]:
    """Recursively scan one native agent directory, skipping unreadable paths."""

    collisions: list[tuple[Path, str]] = []
    for root, directories, filenames in os.walk(
        directory, topdown=True, onerror=lambda _error: None, followlinks=False
    ):
        directories.sort()
        for filename in sorted(filenames):
            entry = Path(root) / filename
            try:
                if not entry.is_file() or entry.suffix != ".md":
                    continue
            except OSError:
                continue
            name = _frontmatter_name(entry)
            if name is not None and name in generated_names:
                collisions.append((entry, name))
    return collisions


def find_cm_collisions(
    cwd: Path | str,
    add_dirs: Iterable[Path | str],
    generated_names: frozenset[str] | set[str],
    *,
    managed_agents_dir: Path | None = DEFAULT_MANAGED_AGENTS_DIR,
) -> list[tuple[Path, str]]:
    """Exact ``cm-*`` name collisions outside the managed scope (fail closed).

    Scans (a) every ``.claude/agents/`` directory from ``cwd`` up to and
    including the git root (first ancestor containing ``.git``) or the
    filesystem root, (b) ``.claude/agents/`` under each passthrough
    ``--add-dir``, and (c) the managed settings agents directory when it
    exists and is readable. ``add_dirs`` must be the user passthrough dirs
    only — never the session's own scope dir. Returns a deterministic sorted
    list of ``(offending file, colliding name)``; empty means clean. Exposed
    for Doctor's collision check as well as the durable launch gate.
    """

    names = frozenset(generated_names)
    collisions: list[tuple[Path, str]] = []
    seen: set[Path] = set()

    def scan(directory: Path) -> None:
        try:
            key = directory.resolve()
        except OSError:
            key = directory
        if key in seen:
            return
        seen.add(key)
        collisions.extend(_scan_agents_dir(directory, names))

    start = Path(cwd).resolve()
    for ancestor in (start, *start.parents):
        candidate = ancestor / ".claude" / "agents"
        if candidate.is_dir():
            scan(candidate)
        if (ancestor / ".git").exists():
            break
    for add_dir in add_dirs:
        candidate = Path(add_dir) / ".claude" / "agents"
        if candidate.is_dir():
            scan(candidate)
    if managed_agents_dir is not None and managed_agents_dir.is_dir():
        scan(managed_agents_dir)
    return sorted(collisions, key=lambda item: (str(item[0]), item[1]))
