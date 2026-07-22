"""Developer onboarding for claude-multi v2: drafts, check, review, promote.

Draft → Check → Review exact diff/hash → Promote source. Promotion writes
trusted repository JSON only: it never builds, activates, stages, commits,
restarts services, resolves real secrets, or contacts a provider. Dummy
secrets are used for every check/review render. The pre/post-image hash
contract fails closed on any source drift.

This entrypoint also hosts the explicitly gated Phase-1B disposable probe
harness: `probe init --allow-local-claude --fixture-root PATH` and
`probe run --allow-local-claude --fixture-root PATH --native-contract FILE
[--allow-real-execution] [-- args]`; both consent flags are presence-based
(their values are ignored and never relax any other check). It never
touches live Claude config, provider credentials, real providers, user
transcripts, or the live shared daemon.
"""

from __future__ import annotations

import difflib
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import catalog as catalog_mod
from . import render as render_mod
from . import state, strict_json
from . import validate as schema_validate


class DevError(RuntimeError):
    """Raised on any onboarding failure (fail closed)."""


REPO_MARKERS = ("flake.nix", "home-manager/kotur.home.nix")
V2_ROOT = Path("home-manager/claude-multi")

_DUMMY_SECRET = "dummy-onboarding-secret"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dummy_resolver(_name: str) -> str:
    return _DUMMY_SECRET


def _none_resolver(_name: str) -> None:
    return None


# ---------------------------------------------------------------- drafts


class DraftStore:
    """Versioned strict-JSON drafts under XDG state, mode 0600."""

    def __init__(self, root: Path | str):
        self.root = state.ensure_private_dir(Path(root))

    def _path(self, name: str) -> Path:
        return self.root / f"{state.check_name(name)}.json"

    def save(self, name: str, draft: dict[str, Any]) -> Path:
        path = self._path(name)
        state.atomic_write(path, strict_json.canonical_file_bytes(draft))
        return path

    def load(self, name: str) -> dict[str, Any]:
        path = self._path(name)
        try:
            return strict_json.loads(state.read_private(path))
        except state.StateError as exc:
            raise DevError(f"cannot read draft {name!r}: {exc}") from exc
        except strict_json.StrictJSONError as exc:
            raise DevError(f"draft {name!r} is corrupt: {exc}") from exc


def _load_draft_schema(catalog_root: Path) -> dict[str, Any]:
    return strict_json.load(catalog_root / "schemas" / "draft.schema.json")


def _load_review_schema(catalog_root: Path) -> dict[str, Any]:
    return strict_json.load(catalog_root / "schemas" / "review.schema.json")


def _validate_draft(draft: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    problems = schema_validate.validate(draft, schema, "$")
    if problems:
        raise DevError("invalid draft: " + "; ".join(problems))
    return draft


def make_model_draft(
    *,
    name: str,
    provider: str,
    entry: dict[str, Any],
    fixtures: list[str] | None = None,
    notes: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Draft for a model on an existing provider (transport/auth inherited)."""

    draft: dict[str, Any] = {
        "version": 1,
        "kind": "model",
        "provider": provider,
        "entry": entry,
        "fixtures": fixtures or [],
        "created_at": now or _now(),
    }
    if notes:
        draft["notes"] = notes
    return draft


def make_provider_draft(
    *,
    name: str,
    provider_profile: dict[str, Any],
    model_entry: dict[str, Any],
    contract_claims: list[str],
    fixtures: list[str] | None = None,
    notes: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Draft for a new provider; requires a complete model and typed profile."""

    if not model_entry:
        raise DevError("provider draft requires a complete model entry")
    if not contract_claims:
        raise DevError("provider draft requires contract claims")
    draft: dict[str, Any] = {
        "version": 1,
        "kind": "provider",
        "entry": {
            "provider": provider_profile,
            "model": model_entry,
            "contract_claims": contract_claims,
        },
        "fixtures": fixtures or [],
        "created_at": now or _now(),
    }
    if notes:
        draft["notes"] = notes
    return draft


def draft_hash(draft: dict[str, Any]) -> str:
    return strict_json.bundle_digest(draft)


# ------------------------------------------------------- candidate images


def _entry_id(entry: dict[str, Any], what: str) -> str:
    ident = entry.get("id")
    if not ident:
        raise DevError(f"{what} entry requires an 'id' field")
    return str(ident)


def build_post_images(
    docs: dict[str, Any], draft: dict[str, Any]
) -> dict[str, bytes]:
    """Exact canonical post-image bytes for every affected trusted file."""

    images: dict[str, bytes] = {}
    if draft["kind"] == "model":
        models_doc = strict_json.loads(
            strict_json.canonical_bytes(docs["models"])
        )
        key = _entry_id(draft["entry"], "model")
        entry = {k: v for k, v in draft["entry"].items() if k != "id"}
        if key in models_doc["models"]:
            raise DevError(f"model {key!r} already exists in the catalog")
        models_doc["models"][key] = entry
        images[str(V2_ROOT / "catalog" / "models.json")] = (
            strict_json.canonical_file_bytes(models_doc)
        )
    else:
        provider_entry = draft["entry"]["provider"]
        model_entry = draft["entry"]["model"]
        provider_id = _entry_id(provider_entry, "provider")
        model_id = _entry_id(model_entry, "model")
        provider_clean = {k: v for k, v in provider_entry.items() if k != "id"}
        model_clean = {k: v for k, v in model_entry.items() if k != "id"}
        providers_doc = strict_json.loads(
            strict_json.canonical_bytes(docs["providers"])
        )
        models_doc = strict_json.loads(strict_json.canonical_bytes(docs["models"]))
        if provider_id in providers_doc["providers"]:
            raise DevError(f"provider {provider_id!r} already exists")
        if model_id in models_doc["models"]:
            raise DevError(f"model {model_id!r} already exists in the catalog")
        providers_doc["providers"][provider_id] = provider_clean
        models_doc["models"][model_id] = model_clean
        images[str(V2_ROOT / "catalog" / "providers.json")] = (
            strict_json.canonical_file_bytes(providers_doc)
        )
        images[str(V2_ROOT / "catalog"/ "models.json")] = (
            strict_json.canonical_file_bytes(models_doc)
        )
    return images


def _apply_images_to_docs(
    docs: dict[str, Any], images: dict[str, bytes]
) -> dict[str, Any]:
    applied = {key: strict_json.loads(strict_json.canonical_bytes(value)) for key, value in docs.items()}
    for relative, data in images.items():
        document = strict_json.loads(data)
        if relative.endswith("providers.json"):
            applied["providers"] = document
        elif relative.endswith("models.json"):
            applied["models"] = document
        else:
            raise DevError(f"unsupported post-image target {relative!r}")
    return applied


def _validate_post_image_schemas(repo: Path, applied: dict[str, Any]) -> None:
    """Run each candidate document through its actual trusted schema first."""

    for relative, document_key in (
        ("catalog/providers.json", "providers"),
        ("catalog/models.json", "models"),
    ):
        schema = strict_json.load(repo / V2_ROOT / "schemas" / f"{document_key}.schema.json")
        problems = schema_validate.validate(applied[document_key], schema, "$")
        if problems:
            raise DevError(
                f"candidate {relative} fails its trusted schema: "
                + "; ".join(problems)
            )


def _validate_candidate(repo: Path, docs: dict[str, Any], prompt_bodies: dict[str, bytes]) -> None:
    problems = catalog_mod.validate_catalog(
        {"docs": docs, "prompt_bodies": prompt_bodies}
    )
    if problems:
        raise DevError("candidate bundle invalid: " + "; ".join(problems))


def _check_new_entry_policy(docs: dict[str, Any], draft: dict[str, Any]) -> None:
    """New entries are New · Off: never in compositions, never preferred."""

    composition = docs["compositions/default"]
    if draft["kind"] == "model":
        key = _entry_id(draft["entry"], "model")
        availability = composition["availability"]["models"]
        if key in availability:
            raise DevError(
                f"new model {key!r} must not appear in any composition; it is New · Off"
            )
        for slot in composition["slots"]:
            if slot["model"] == key:
                raise DevError(f"new model {key!r} must not be slotted or preferred")


# ------------------------------------------------------------ repo verify


def _repo_atomic_write(target: Path, data: bytes) -> None:
    """Atomic write inside a source checkout.

    Same-directory temp file, fsync file and directory, atomic replace. The
    checkout's own permissions apply (source files are group-readable); the
    stricter 0700/0600 `state` primitives guard private state instead.
    """

    parent = target.parent
    info = os.lstat(parent)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise DevError(f"unsafe repo directory {parent}")
    if target.is_symlink():
        raise DevError(f"repo target {target} must not be a symlink")
    mode = 0o644
    if target.exists():
        existing = stat.S_IMODE(os.lstat(target).st_mode)
        mode = existing if existing else 0o644
    descriptor, temporary = tempfile.mkstemp(
        dir=parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    dirfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(dirfd)
    finally:
        os.close(dirfd)


def verify_repo(repo: Path | str) -> Path:
    """Require an explicit, writable, real source checkout (never the store)."""

    candidate = Path(repo)
    if candidate.is_symlink():
        raise DevError(f"repo path {candidate} must not be a symlink")
    resolved = candidate.resolve()
    if str(resolved).startswith("/nix/store"):
        raise DevError("the Nix store is never a writable source checkout")
    if not resolved.is_dir():
        raise DevError(f"repo path {candidate} is not a directory")
    for marker in REPO_MARKERS:
        if not (resolved / marker).is_file():
            raise DevError(f"{resolved} is not a source checkout: missing {marker}")
    probe = resolved / V2_ROOT / "catalog"
    if not probe.is_dir():
        raise DevError(f"{resolved} lacks the v2 catalog tree")
    if not os.access(resolved, os.W_OK):
        raise DevError(f"repo path {resolved} is not writable")
    return resolved


def repo_revision(repo: Path) -> str:
    """Source identity: git HEAD when available, 'nogit' otherwise."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "nogit"
    if completed.returncode != 0:
        return "nogit"
    return completed.stdout.strip() or "nogit"


# ------------------------------------------------------ scratch candidate


def materialize_candidate(
    repo: Path, images: dict[str, bytes], parent: Path | None = None
) -> Path:
    """Copy the verified checkout minus unsafe exclusions, apply post-images."""

    if parent is None:
        root = Path(state_root_default())
        state.ensure_private_dir(root)
        parent = Path(tempfile.mkdtemp(prefix="claude-multi-candidate-", dir=root))
    if parent.exists():
        os.chmod(parent, 0o700)
    state.ensure_private_dir(parent)
    try:
        candidate = parent / "tree"

        def _ignore(directory: str, names: list[str]) -> set[str]:
            skipped: set[str] = set()
            for name in names:
                if name in (".git", ".slim", "result") or name.startswith("result-"):
                    skipped.add(name)
                    continue
                if (Path(directory) / name).is_symlink():
                    skipped.add(name)
            return skipped

        shutil.copytree(repo, candidate, ignore=_ignore, symlinks=False)
        # The candidate tree is private: no group/other access anywhere.
        for root_dir, dirs, _files in os.walk(candidate):
            os.chmod(root_dir, 0o700)
            for name in dirs:
                os.chmod(Path(root_dir) / name, 0o700)
        for root_dir, _dirs, files in os.walk(candidate):
            for name in files:
                if (Path(root_dir) / name).is_symlink():
                    raise DevError("candidate copy retained a symlink")
        for relative, data in images.items():
            target = candidate / relative
            if not target.parent.is_dir():
                raise DevError(f"post-image target directory missing: {target.parent}")
            state.atomic_write(target, data)
        return candidate
    except BaseException:
        # A partially materialized candidate is removed before re-raising;
        # cleanup is constrained to the exact candidate root.
        _cleanup_candidate(parent)
        raise


def state_root_default() -> str:
    root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return str(Path(root) / "claude-multi")


# --------------------------------------------------------------- lifecycle


@dataclass(frozen=True)
class CheckResult:
    draft_hash: str
    images: dict[str, bytes]
    render: render_mod.RenderResult
    bundle_valid: bool
    candidate: Path
    builds: tuple[dict[str, Any], ...]


def _default_runner(command: list[str], cwd: Path) -> dict[str, Any]:
    if shutil.which(command[0]) is None:
        return {"cmd": command, "skipped": f"{command[0]} not available"}
    completed = subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=1800
    )
    return {
        "cmd": command,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def _sanitize_output(text: str) -> str:
    """Strip every nonempty value tied to a sensitive env name from output.

    Values of any length are redacted, longest first so overlapping values
    cannot survive partial replacement. Nonsecret diagnostics are retained.
    """

    sensitive: list[str] = []
    for name, value in os.environ.items():
        if not value:
            continue
        if any(marker in name.upper() for marker in ("KEY", "TOKEN", "SECRET", "PASS")):
            sensitive.append(value)
    sanitized = text
    for value in sorted(set(sensitive), key=len, reverse=True):
        sanitized = sanitized.replace(value, "***")
    return sanitized


def _cleanup_candidate(parent: Path) -> None:
    """Remove a scratch candidate tree deterministically and only there.

    The parent is the exact directory this module created; nothing outside it
    is ever touched, and no background reaper exists.
    """

    resolved = parent.resolve()
    if not resolved.is_dir() or resolved.is_symlink():
        return
    shutil.rmtree(resolved)


def _gate_builds(builds: tuple[dict[str, Any], ...]) -> None:
    """Candidate builds gate Check: genuine failures are deterministic errors.

    Binary-unavailable skips are a distinct, honestly reported nonfatal
    status; they are never confused with command failure.
    """

    for build in builds:
        target = build["cmd"][-1] if build.get("cmd") else "unknown"
        if "skipped" in build:
            continue
        code = build.get("returncode")
        if code != 0:
            detail = _sanitize_output(
                (build.get("stderr") or build.get("stdout") or "")[-800:]
            )
            raise DevError(
                f"candidate build failed for {target} (exit {code}): {detail}"
            )


def check_draft(
    draft: dict[str, Any],
    *,
    repo: Path,
    runner: Callable[[list[str], Path], dict[str, Any]] | None = None,
    candidate_parent: Path | None = None,
) -> CheckResult:
    """Check: validate candidate bundle, dummy-secret render, scratch builds."""

    repo = verify_repo(repo)
    raw = catalog_mod.load_raw(repo / V2_ROOT)
    images = build_post_images(raw["docs"], draft)
    candidate_docs = _apply_images_to_docs(raw["docs"], images)
    _validate_post_image_schemas(repo, candidate_docs)
    _validate_candidate(repo, candidate_docs, raw["prompt_bodies"])
    _check_new_entry_policy(candidate_docs, draft)

    render_result = render_mod.render_config(
        candidate_docs["gateway"],
        candidate_docs["providers"]["providers"],
        candidate_docs["models"]["models"],
        home=Path("/home/onboarding"),
        gateway_token=_DUMMY_SECRET,
        resolve_secret=_dummy_resolver,
    )
    # Deterministic render proof: two renders are byte-identical.
    again = render_mod.render_config(
        candidate_docs["gateway"],
        candidate_docs["providers"]["providers"],
        candidate_docs["models"]["models"],
        home=Path("/home/onboarding"),
        gateway_token=_DUMMY_SECRET,
        resolve_secret=_dummy_resolver,
    )
    if again.yaml != render_result.yaml:
        raise DevError("renderer output is not deterministic")

    candidate = materialize_candidate(repo, images, candidate_parent)
    run = runner or _default_runner
    try:
        builds = (
            run(
                [
                    "nix-build",
                    "--no-out-link",
                    str(candidate / V2_ROOT / "tests" / "default.nix"),
                ],
                candidate,
            ),
            run(
                ["nix-build", "--no-out-link", str(candidate / V2_ROOT / "package.nix")],
                candidate,
            ),
        )
        _gate_builds(builds)
        return CheckResult(
            draft_hash=draft_hash(draft),
            images=images,
            render=render_result,
            bundle_valid=True,
            candidate=candidate,
            builds=builds,
        )
    finally:
        # The scratch tree is transient; all evidence lives in the result.
        _cleanup_candidate(candidate.parent)


def _unified_diff(relative: str, before: bytes | None, after: bytes) -> str:
    old = [] if before is None else before.decode("utf-8").splitlines(keepends=True)
    new = after.decode("utf-8").splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            old, new, fromfile=f"a/{relative}", tofile=f"b/{relative}"
        )
    )


def review_draft(
    draft: dict[str, Any],
    *,
    draft_name: str,
    repo: Path,
    revision: str | None = None,
    now: str | None = None,
    check_result: "CheckResult | None" = None,
    runner: Callable[[list[str], Path], dict[str, Any]] | None = None,
    candidate_parent: Path | None = None,
) -> dict[str, Any]:
    """Review: exact file set, pre/post hashes, results, unified diff evidence.

    Review is never creatable from a failed check: it accepts a passed
    CheckResult or runs the same offline candidate gates itself first.
    """

    repo = verify_repo(repo)
    if check_result is None:
        check_result = check_draft(
            draft, repo=repo, runner=runner, candidate_parent=candidate_parent
        )
    raw = catalog_mod.load_raw(repo / V2_ROOT)
    images = build_post_images(raw["docs"], draft)
    candidate_docs = _apply_images_to_docs(raw["docs"], images)
    _validate_post_image_schemas(repo, candidate_docs)
    _validate_candidate(repo, candidate_docs, raw["prompt_bodies"])
    _check_new_entry_policy(candidate_docs, draft)

    render_result = render_mod.render_config(
        candidate_docs["gateway"],
        candidate_docs["providers"]["providers"],
        candidate_docs["models"]["models"],
        home=Path("/home/onboarding"),
        gateway_token=_DUMMY_SECRET,
        resolve_secret=_dummy_resolver,
    )

    files: list[dict[str, Any]] = []
    diffs: list[str] = []
    for relative in sorted(images):
        target = repo / relative
        before: bytes | None = None
        if target.exists():
            if target.is_symlink() or not target.is_file():
                raise DevError(f"affected path {relative} is not a regular file")
            before = target.read_bytes()
        after = images[relative]
        files.append(
            {
                "path": relative,
                "pre_image_hash": (
                    None if before is None else "sha256:" + strict_json.sha256_hex(before)
                ),
                "post_image_hash": "sha256:" + strict_json.sha256_hex(after),
            }
        )
        diffs.append(_unified_diff(relative, before, after))

    record = {
        "version": 1,
        "draft": draft_name,
        "draft_hash": draft_hash(draft),
        "repo": {
            "path": str(repo),
            "revision": revision if revision is not None else repo_revision(repo),
        },
        "files": files,
        "results": {
            "bundle_valid": True,
            "render_sha256": "sha256:" + strict_json.sha256_hex(
                render_result.yaml.encode("utf-8")
            ),
            "unavailable_providers": list(render_result.unavailable),
            "diff": "\n".join(diffs),
        },
        "created_at": now or _now(),
    }
    problems = schema_validate.validate(
        record, _load_review_schema(repo / V2_ROOT), "$"
    )
    if problems:
        raise DevError("review record invalid: " + "; ".join(problems))
    return record


def _validate_review_record(repo: Path, record: Any) -> dict[str, Any]:
    """Schema-validate a review record before any field access (fail closed)."""

    if not isinstance(record, dict):
        raise DevError("review record is not a JSON object")
    problems = schema_validate.validate(
        record, _load_review_schema(repo / V2_ROOT), "$"
    )
    if problems:
        raise DevError("review record invalid: " + "; ".join(problems))
    return record


def promote_draft(
    draft: dict[str, Any],
    *,
    draft_name: str,
    repo: Path,
    review_record: dict[str, Any],
    drafts_root: Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Promote: re-verify hashes, apply reviewed post-images atomically.

    Applies only the reviewed JSON post-images with an in-memory journal and
    rollback on failure; never builds/activates/stages/commits/restarts,
    resolves no real secrets, and makes no provider call.
    """

    repo = verify_repo(repo)
    record = _validate_review_record(repo, review_record)
    if record["draft"] != draft_name or record["draft_hash"] != draft_hash(draft):
        raise DevError("review record does not match the draft")
    if record["repo"]["path"] != str(repo):
        raise DevError("review record repo path mismatch")

    raw = catalog_mod.load_raw(repo / V2_ROOT)
    images = build_post_images(raw["docs"], draft)
    candidate_docs = _apply_images_to_docs(raw["docs"], images)
    _validate_post_image_schemas(repo, candidate_docs)
    _validate_candidate(repo, candidate_docs, raw["prompt_bodies"])
    _check_new_entry_policy(candidate_docs, draft)

    # Hash contract: reviewed files must match recomputed post-images exactly.
    reviewed = {entry["path"]: entry for entry in record["files"]}
    if set(reviewed) != set(images):
        raise DevError("reviewed file set differs from the candidate file set")
    journal: list[dict[str, Any]] = []
    for relative in sorted(images):
        after = images[relative]
        expected_post = reviewed[relative]["post_image_hash"]
        actual_post = "sha256:" + strict_json.sha256_hex(after)
        if actual_post != expected_post:
            raise DevError(f"post-image hash mismatch for {relative}: source drift")
        target = repo / relative
        before: bytes | None = None
        if target.exists():
            if target.is_symlink() or not target.is_file():
                raise DevError(f"affected path {relative} is not a regular file")
            before = target.read_bytes()
        expected_pre = reviewed[relative]["pre_image_hash"]
        actual_pre = None if before is None else "sha256:" + strict_json.sha256_hex(before)
        if actual_pre != expected_pre:
            raise DevError(f"pre-image hash mismatch for {relative}: source changed after review")
        journal.append({"path": relative, "before": before})

    applied: list[str] = []
    try:
        for entry in journal:
            _repo_atomic_write(repo / entry["path"], images[entry["path"]])
            applied.append(entry["path"])
    except BaseException:
        for entry in journal:
            target = repo / entry["path"]
            if entry["path"] not in applied:
                continue
            if entry["before"] is None:
                target.unlink(missing_ok=True)
            else:
                _repo_atomic_write(target, entry["before"])
        raise DevError("promotion failed and was rolled back") from None

    if drafts_root is not None:
        journal_record = {
            "version": 1,
            "draft": draft_name,
            "applied": applied,
            "post_image_hashes": {
                path: "sha256:" + strict_json.sha256_hex(images[path])
                for path in sorted(applied)
            },
            "created_at": now or _now(),
        }
        store = DraftStore(drafts_root)
        state.atomic_write(
            store.root / f"{draft_name}.journal.json",
            strict_json.canonical_file_bytes(journal_record),
        )
    return {"applied": applied, "draft_hash": record["draft_hash"]}


def resolve_promote_mode(repo: Any, patch_output: Any) -> str:
    """Promote forms are mutually exclusive: apply to source XOR emit a patch."""

    if repo is not None and patch_output is not None:
        raise DevError(
            "promote accepts either --repo PATH (apply) or --patch-output FILE "
            "(emit-only), never both"
        )
    if patch_output is not None:
        return "patch"
    if repo is not None:
        return "apply"
    raise DevError("promote requires --repo PATH or --patch-output FILE")


def promote_patch_output(
    draft: dict[str, Any], *, repo: Path, output: Path
) -> Path:
    """Emit-only candidate patch: no draft/source mutation, mutually exclusive."""

    repo = verify_repo(repo)
    raw = catalog_mod.load_raw(repo / V2_ROOT)
    images = build_post_images(raw["docs"], draft)
    applied_docs = _apply_images_to_docs(raw["docs"], images)
    _validate_post_image_schemas(repo, applied_docs)
    _validate_candidate(repo, applied_docs, raw["prompt_bodies"])
    _check_new_entry_policy(applied_docs, draft)
    diffs = []
    for relative in sorted(images):
        target = repo / relative
        before = target.read_bytes() if target.is_file() and not target.is_symlink() else None
        diffs.append(_unified_diff(relative, before, images[relative]))
    parent = output.parent
    if parent.is_symlink() or not parent.is_dir():
        raise DevError(f"patch output directory {parent} is unsafe")
    if output.is_symlink():
        raise DevError(f"patch output {output} must not be a symlink")
    # Private-state primitive: same-directory mode-0600 atomic write.
    state.atomic_write(output, "\n".join(diffs).encode("utf-8"))
    return output


# -------------------------------------------------------------- smoke test

def smoke_test(model: str, *, allow_provider_call: bool) -> dict[str, Any]:
    """Consent-gated live smoke. Without consent: zero requests, guidance only.

    No provider transport is wired in this workflow, so consenting calls
    fail closed instead of touching a network.
    """

    if not allow_provider_call:
        return {
            "status": "refused",
            "requests": 0,
            "guidance": (
                "smoke-test requires explicit --allow-provider-call consent; "
                "no external request was made"
            ),
        }
    raise DevError("no provider transport is wired in this workflow")


# -------------------------------------------------------------------- CLI


def _parse_flags(args: list[str]) -> tuple[list[str], dict[str, Any]]:
    positionals: list[str] = []
    flags: dict[str, Any] = {}
    index = 0
    while index < len(args):
        token = args[index]
        if token.startswith("--"):
            key = token[2:]
            if "=" in key:
                key, value = key.split("=", 1)
                flags[key] = value
            elif index + 1 < len(args) and not args[index + 1].startswith("--"):
                flags[key] = args[index + 1]
                index += 1
            else:
                flags[key] = True
        else:
            positionals.append(token)
        index += 1
    return positionals, flags


def _draft_store() -> DraftStore:
    return DraftStore(
        Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local" / "state")))
        / "claude-multi"
        / "drafts"
    )


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    command, rest = argv[0], argv[1:]
    if command == "probe":
        # Lazy: a tracked module must not hard-import the dev-only probe
        # harness at top level (G0 REWRITE verdict).
        from . import probe as probe_mod

        head = rest[: rest.index("--")] if "--" in rest else rest
        tail = rest[rest.index("--") + 1:] if "--" in rest else []
        positionals, flags = _parse_flags(head)
        return probe_mod.probe_cli(positionals, flags, tail)
    positionals, flags = _parse_flags(rest)
    repo = Path(flags.get("repo", ".")) if "repo" in flags else None
    try:
        if command in ("model", "provider") and positionals[:1] == ["add"]:
            drafts = _draft_store()
            kind = command
            if "from-json" not in flags:
                raise DevError(f"{kind} add requires --from-json FILE")
            spec = strict_json.load(Path(flags["from-json"]))
            if not isinstance(spec, dict):
                raise DevError("spec file must contain a JSON object")
            required = ("provider", "entry") if kind == "model" else (
                "provider",
                "model",
                "contract_claims",
            )
            for field in required:
                if field not in spec:
                    raise DevError(f"spec file is missing required field {field!r}")
            name = state.check_name(str(flags.get("name", "draft")))
            if kind == "model":
                draft = make_model_draft(
                    name=name,
                    provider=spec["provider"],
                    entry=spec["entry"],
                    fixtures=spec.get("fixtures"),
                    notes=spec.get("notes"),
                )
            else:
                draft = make_provider_draft(
                    name=name,
                    provider_profile=spec["provider"],
                    model_entry=spec["model"],
                    contract_claims=spec["contract_claims"],
                    fixtures=spec.get("fixtures"),
                    notes=spec.get("notes"),
                )
            path = drafts.save(name, _validate_draft(draft, _load_draft_schema(
                (repo or Path(".")).resolve() / V2_ROOT
            )))
            print(f"draft saved: {path}")
            return 0
        if command == "check":
            drafts = _draft_store()
            if not positionals:
                raise DevError("check requires a DRAFT name")
            draft = _validate_draft(drafts.load(positionals[0]), _load_draft_schema(
                verify_repo(repo or Path(".")) / V2_ROOT
            ))
            result = check_draft(draft, repo=verify_repo(repo or Path(".")))
            print(f"check ok: draft {result.draft_hash}")
            for build in result.builds:
                print(f"  build: {build.get('cmd', ['?'])[-1]} -> {build.get('returncode', build.get('skipped'))}")
            return 0
        if command == "review":
            drafts = _draft_store()
            if not positionals:
                raise DevError("review requires a DRAFT name")
            verified = verify_repo(repo or Path("."))
            draft = _validate_draft(
                drafts.load(positionals[0]), _load_draft_schema(verified / V2_ROOT)
            )
            record = review_draft(draft, draft_name=positionals[0], repo=verified)
            out = drafts.root / f"{positionals[0]}.review.json"
            state.atomic_write(out, strict_json.canonical_file_bytes(record))
            print(f"review recorded: {out}")
            print(record["results"]["diff"])
            return 0
        if command == "promote":
            drafts = _draft_store()
            if not positionals:
                raise DevError("promote requires a DRAFT name")
            mode = resolve_promote_mode(
                repo if "repo" in flags else None, flags.get("patch-output")
            )
            if mode == "patch":
                verified = verify_repo(repo or Path("."))
                draft = _validate_draft(
                    drafts.load(positionals[0]), _load_draft_schema(verified / V2_ROOT)
                )
                path = promote_patch_output(draft, repo=verified, output=Path(flags["patch-output"]))
                print(f"patch written: {path}")
                return 0
            verified = verify_repo(repo)
            draft = _validate_draft(
                drafts.load(positionals[0]), _load_draft_schema(verified / V2_ROOT)
            )
            record = strict_json.loads(
                state.read_private(drafts.root / f"{positionals[0]}.review.json")
            )
            result = promote_draft(
                draft,
                draft_name=positionals[0],
                repo=verified,
                review_record=record,
                drafts_root=drafts.root,
            )
            print(f"promoted: {result['applied']}")
            return 0
        if command == "smoke-test":
            if not positionals:
                raise DevError("smoke-test requires a MODEL name")
            outcome = smoke_test(
                positionals[0], allow_provider_call=bool(flags.get("allow-provider-call"))
            )
            print(outcome.get("guidance", outcome["status"]))
            return 2 if outcome["status"] == "refused" else 0
        raise DevError(f"unknown command {command!r}")
    except (
        DevError,
        strict_json.StrictJSONError,
        state.StateError,
        catalog_mod.CatalogError,
    ) as exc:
        print(f"claude-multi-dev: {exc}", file=os.sys.stderr)
        return 2
