"""Evidence-gated Claude re-pin (`claude-multi update`).

The pinned native contract is the launch trust anchor, so it must never
follow Claude's auto-updater blindly — but re-pinning must not be a manual
scavenger hunt either. This module is the whole flow as one command:

1. **detect** the candidate artifact (the configured symlink target when it
   is newer than the pin, else the newest versioned artifact under
   ``versions/``);
2. **inspect** it offline (basename==version, regular non-symlink executable,
   SHA-256, ``--version``, ``--help`` — no prompt, no provider, no daemon);
3. **evidence**: run the offline test suite of the source checkout, which
   includes the real-binary probe tests (compaction hooks, delegation) now
   pointed at the candidate contract;
4. **promote**: write the new ``catalog/native-contract.json`` and bump
   ``catalog_version`` — only after the evidence passes, with byte-exact
   backup/restore on any failure. The suite's deliberate version pins
   (validated-version/SHA/path literals in the contract tests) are synced
   in the same transaction, so the evidence suite validates the *new*
   contract instead of failing against the old one;
5. print the exact commit + Home Manager activation commands, or run the
   activation when explicitly confirmed (``--activate`` or interactive
   approval).

Every step fails closed: a failed inspection or red suite leaves the repo
byte-identical to before. No real provider, transcript, or live daemon is
ever touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class UpgradeError(RuntimeError):
    """Raised on any failed upgrade step (fail closed, repo untouched)."""

    def __init__(self, message: str, *, post_override: bool = False):
        super().__init__(message)
        self.post_override = post_override


def _write_repo_file(path: Path, data: bytes) -> None:
    """Crash-atomic write preserving the repo file's existing mode.

    ``state.atomic_write`` is mode-0600-by-design for private state; checkout
    files are normal repo files (0644), so promotion uses the same
    temp+fsync+replace discipline with the mode carried over (review H15).
    """

    if path.is_symlink():
        # Never follow a symlink into a mode/target the caller didn't mean
        # (final-gate N2): repo files are git-managed regulars.
        raise UpgradeError(f"refusing to replace symlinked repo file {path}")
    try:
        mode = stat.S_IMODE(os.lstat(path).st_mode)
    except OSError:
        mode = 0o644
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


class _Heartbeat:
    """Periodic proof-of-life line while a long subprocess phase runs."""

    def __init__(
        self, progress: Callable[[str], None] | None, label: str, interval: float = 15.0
    ):
        self._progress = progress
        self._label = label
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_Heartbeat":
        if self._progress is None:
            return self
        started = time.monotonic()

        def _beat() -> None:
            while not self._stop.wait(self._interval):
                self._progress(
                    f"{self._label} ({int(time.monotonic() - started)}s elapsed)"
                )

        self._thread = threading.Thread(target=_beat, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        return False


_VERSION_DIR_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


@dataclass(frozen=True)
class CandidateInspection:
    """Offline evidence for one candidate Claude artifact."""

    path: Path
    version: str
    sha256: str


def _version_key(name: str) -> tuple[int, ...] | None:
    parts = name.split(".")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def inspect_candidate(path: Path) -> CandidateInspection:
    """Offline artifact inspection: shape, hash, --version, --help."""

    if not path.is_absolute():
        raise UpgradeError(f"candidate path {path} is not absolute")
    if not os.path.lexists(path):
        raise UpgradeError(f"candidate artifact {path} is missing")
    if path.is_symlink():
        raise UpgradeError(
            f"candidate artifact {path} is a symlink; inspect the resolved file"
        )
    if not path.is_file():
        raise UpgradeError(f"candidate artifact {path} is not a regular file")
    if not _VERSION_DIR_RE.fullmatch(path.name):
        raise UpgradeError(
            f"candidate basename {path.name!r} is not an X.Y.Z version"
        )
    if not os.access(path, os.X_OK):
        raise UpgradeError(f"candidate artifact {path} is not executable")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    try:
        version_out = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/")},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpgradeError(f"candidate --version failed: {exc}") from exc
    if version_out.returncode != 0 or path.name not in version_out.stdout:
        raise UpgradeError(
            f"candidate --version did not report {path.name}: "
            f"{version_out.stdout.strip()!r}"
        )
    try:
        help_out = subprocess.run(
            [str(path), "--help"],
            capture_output=True,
            text=True,
            timeout=30,
            env={"PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/")},
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpgradeError(f"candidate --help failed: {exc}") from exc
    if help_out.returncode != 0 or "Claude Code" not in help_out.stdout:
        raise UpgradeError("candidate --help does not look like Claude Code")
    return CandidateInspection(path=path, version=path.name, sha256=digest)


def find_candidates(native_contract: dict[str, Any]) -> list[Path]:
    """Ordered acceptable artifacts: newer symlink target first, then the
    retained versions newest-first BY VERSION KEY (never lexically — name
    order puts 2.1.99 ahead of 2.1.218; review U8). Callers inspect in
    order and skip invalid entries (review H10: an invalid highest name
    must not abort the whole update)."""

    record = native_contract["claude"]
    pinned = _version_key(record["validated_version"])
    if pinned is None:
        raise UpgradeError(
            f"pinned version {record['validated_version']!r} is not X.Y.Z"
        )
    ordered: list[Path] = []
    configured = Path(record["executable"]["configured_path"])
    if os.path.lexists(configured):
        target = Path(os.path.realpath(configured))
        key = _version_key(target.name)
        if key is not None and key > pinned:
            ordered.append(target)
    versions_dir = Path(record["executable"]["resolved_path"]).parent
    keyed: list[tuple[tuple[int, ...], Path]] = []
    try:
        entries = list(versions_dir.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        key = _version_key(entry.name)
        if key is None or key <= pinned:
            continue
        keyed.append((key, entry))
    keyed.sort(key=lambda item: item[0], reverse=True)
    for _key, entry in keyed:
        if any(existing == entry for existing in ordered):
            continue
        ordered.append(entry)
    return ordered


def find_candidate(native_contract: dict[str, Any]) -> Path | None:
    """Newest acceptable artifact: newer symlink target, else newest retained."""

    ordered = find_candidates(native_contract)
    return ordered[0] if ordered else None


def render_contract(
    prior: dict[str, Any], inspection: CandidateInspection, *, today: str
) -> dict[str, Any]:
    """The promoted native contract: new executable facts, provenance carried."""

    contract = json.loads(json.dumps(prior))
    executable = contract["claude"]["executable"]
    executable["resolved_path"] = str(inspection.path)
    executable["sha256"] = inspection.sha256
    executable["inspection"] = (
        "Offline symlink/path resolution and SHA-256 file hashing; local "
        f"execution of the pinned {inspection.version} binary limited to "
        "--version and --help (no prompt, no provider request, no daemon "
        "contact)."
    )
    executable["inspected_at"] = today
    contract["claude"]["validated_version"] = inspection.version
    lifecycle = contract.setdefault("lifecycle_evidence", {})
    lifecycle["inspected_version"] = inspection.version
    evidence_id = lifecycle.get("evidence_id")
    if isinstance(evidence_id, str) and evidence_id:
        lifecycle["evidence_id"] = re.sub(
            r"[0-9]+\.[0-9]+\.[0-9]+$", inspection.version, evidence_id
        )
    return contract


@dataclass(frozen=True)
class UpgradeOutcome:
    """What happened; ``activated`` is only ever set by the caller's flow."""

    kind: str  # "current" | "prepared" | "activated"
    inspection: CandidateInspection | None
    messages: tuple[str, ...]


_SYNC_TEST_FILES = ("tests/test_catalog.py", "tests/test_native_contract.py")


def _sync_pinned_test_literals(
    product_root: Path,
    *,
    old_version: str,
    new_version: str,
    old_sha256: str | None,
    new_sha256: str,
    old_inspected_at: str | None,
    new_inspected_at: str,
    old_catalog_version: int,
    backups: dict[Path, bytes],
) -> list[str]:
    """Move the suite's deliberate version pins to the promoted contract.

    Several tests pin the reviewed baseline on purpose (the re-pin commit's
    diff IS the review trail): validated-version literals, the resolved-path
    and SHA-256 facts, and the ``catalog_version`` literal. If promotion did
    not move them, the evidence suite would fail against the very contract
    it is meant to validate (the 2.1.220 re-pin failure). Lines that are
    intentionally decoupled from the artifact version (the per-model
    ``floors = {...}`` minimums) are left untouched. Original bytes are
    recorded in ``backups`` for the fail-closed restore.
    """

    synced: list[str] = []
    for relative in _SYNC_TEST_FILES:
        path = product_root / relative
        if not path.is_file():
            continue
        original = path.read_bytes()
        lines = original.decode("utf-8").splitlines(keepends=True)
        changed: list[str] = []
        for line in lines:
            if "floors" in line:
                changed.append(line)  # model minimums are decoupled by design
                continue
            # Boundary-anchored: the old pin must never rewrite inside a
            # longer literal (e.g. old 2.1.2 mangling 2.1.216 into 2.1.2016
            # — hardening review H6).
            updated = re.sub(
                r"(?<![0-9.])" + re.escape(old_version) + r"(?![0-9])",
                new_version,
                line,
            )
            if old_sha256 and old_sha256 != new_sha256:
                updated = updated.replace(old_sha256, new_sha256)
            if old_inspected_at and old_inspected_at != new_inspected_at:
                updated = updated.replace(
                    f'"{old_inspected_at}"', f'"{new_inspected_at}"'
                )
            updated = updated.replace(
                f'"catalog_version"], {old_catalog_version})',
                f'"catalog_version"], {old_catalog_version + 1})',
            )
            changed.append(updated)
        new_text = "".join(changed)
        if new_text.encode("utf-8") != original:
            backups.setdefault(path, original)
            _write_repo_file(path, new_text.encode("utf-8"))
            synced.append(relative)
    return synced


def run_upgrade(
    *,
    checkout_root: Path,
    native_contract: dict[str, Any],
    override_path: Path,
    today: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    env: dict[str, str] | None = None,
    activate: bool = False,
    flake_target: str | None = None,
    progress: Callable[[str], None] | None = None,
    packaged_contract: dict[str, Any] | None = None,
    override_broken: bool = False,
) -> UpgradeOutcome:
    """Inspect → promote → evidence → override → (optionally) activate.

    The operator override takes effect the moment it is written — no
    rebuild, no service restart. Promoting into the source checkout keeps
    the packaged baseline honest; it lands at the next natural Home Manager
    activation (or immediately with ``--activate``). Any failure before the
    override write leaves both the repo and the override byte-identical.

    ``progress`` receives one line per long phase (inspect, evidence suite,
    override write, activation) so interactive callers can show life while
    the minutes-long evidence suite runs.

    The whole flow is serialized through an advisory lock next to the
    override file: two concurrent updates (two terminals, card U + CLI)
    queue instead of interleaving the checkout promotion.

    ``packaged_contract`` is the installed baseline (asset root), used to
    decide whether an existing override is genuinely redundant. Callers
    must pass it: the effective contract (``native_contract``) already
    includes the override, and deleting an override that is strictly newer
    than the packaged baseline would silently downgrade the launch trust
    anchor (hardening review H2).
    """

    from . import state  # local import: hardened writes for the config root

    def _note(line: str) -> None:
        if progress is not None:
            progress(line)

    state.ensure_private_dir(override_path.parent)
    update_lock = state.FileLock(override_path)
    if not update_lock.acquire(blocking=False):
        _note("another claude-multi update is running; waiting for it…")
        update_lock.acquire(blocking=True)
    try:
        return _run_upgrade_locked(
            checkout_root=checkout_root,
            native_contract=native_contract,
            override_path=override_path,
            today=today,
            runner=runner,
            env=env,
            activate=activate,
            flake_target=flake_target,
            note=_note,
            progress=progress,
            packaged_contract=packaged_contract,
            override_broken=override_broken,
        )
    finally:
        update_lock.release()


def _run_upgrade_locked(
    *,
    checkout_root: Path,
    native_contract: dict[str, Any],
    override_path: Path,
    today: str,
    runner: Callable[..., subprocess.CompletedProcess],
    env: dict[str, str] | None,
    activate: bool,
    flake_target: str | None,
    note: Callable[[str], None],
    progress: Callable[[str], None] | None,
    packaged_contract: dict[str, Any] | None,
    override_broken: bool,
) -> UpgradeOutcome:
    from . import state

    _note = note

    def _activate(messages: list[str], total: int) -> None:
        _note(f"[{total}/{total}] running home-manager switch (a few minutes)…")
        with _Heartbeat(
            progress, f"  [{total}/{total}] home-manager switch still running"
        ):
            switch = runner(
                ["home-manager", "switch", "--flake", flake_target or str(checkout_root.parents[1]) + "#kotur"],
                capture_output=True,
                text=True,
                timeout=1800,
            )
        if switch.returncode != 0:
            tail = (switch.stdout or "") + (switch.stderr or "")
            raise UpgradeError(
                "home-manager switch failed; the override is already active and "
                "the repo baseline is promoted — activate manually. Tail:\n"
                + "\n".join(tail.strip().splitlines()[-15:]),
                post_override=True,
            )
        messages.append("activated: home-manager switch completed")

    candidate = None
    inspection: CandidateInspection | None = None
    skipped: list[str] = []
    for entry in find_candidates(native_contract):
        try:
            inspection = inspect_candidate(entry)
            candidate = entry
            break
        except UpgradeError as exc:
            skipped.append(f"{entry.name}: {exc}")
    skip_note = (
        "skipped unusable candidate artifact(s): " + "; ".join(skipped)
        if skipped
        else None
    )
    if candidate is None or inspection is None:
        pin = native_contract["claude"]["validated_version"]
        messages = [
            f"pinned Claude {pin} is the newest installed artifact; nothing to re-pin."
        ]
        if skip_note is not None:
            messages.append(skip_note)
        if os.path.lexists(override_path):
            # Remove the override only when it is genuinely redundant: the
            # packaged baseline must have caught up. Deleting an override that
            # is strictly newer silently downgrades the trust anchor (H2).
            # An unreadable/unversioned/schema-invalid override is never
            # applied by the loader anyway — removing it heals (and when the
            # caller already knows the loader rejected it, ``override_broken``
            # tells us directly; final-gate review SF1).
            redundant = False
            broken = override_broken
            override_version: str | None = None
            try:
                override_doc = json.loads(override_path.read_bytes().decode("utf-8"))
                override_version = override_doc["claude"]["validated_version"]
                if not isinstance(override_version, str):
                    override_version = None
            except (OSError, ValueError, KeyError):
                override_version = None
            packaged_version = (
                packaged_contract["claude"]["validated_version"]
                if packaged_contract is not None
                else None
            )
            if override_version is None or _version_key(override_version) is None:
                # Unreadable, unversioned, or a version the loader's strict
                # schema would reject: never applicable, always safe to heal.
                broken = True
            elif packaged_version is not None:
                ov_key = _version_key(override_version)
                pk_key = _version_key(packaged_version)
                redundant = (
                    ov_key is not None and pk_key is not None and ov_key <= pk_key
                )
            if broken:
                state.remove_private(override_path)
                messages.append(
                    f"removed unreadable contract override {override_path} "
                    "(the strict loader would fail closed on it)"
                )
            elif redundant:
                state.remove_private(override_path)
                messages.append(
                    f"removed redundant contract override {override_path} "
                    "(the packaged contract is current)"
                )
            else:
                messages.append(
                    f"operator override pins {override_version} while "
                    f"the packaged baseline is {packaged_version or 'unknown'}; "
                    "the override stays (it is not redundant)"
                )
                if activate:
                    _activate(messages, 2)
                    return UpgradeOutcome(
                        kind="activated", inspection=None, messages=tuple(messages)
                    )
        return UpgradeOutcome(
            kind="current", inspection=None, messages=tuple(messages)
        )
    total = 5 if activate else 4
    _note(f"[1/{total}] candidate {inspection.version} inspected offline "
          "(--version, --help, sha256)")
    product_root = Path(checkout_root)
    contract_path = product_root / "catalog" / "native-contract.json"
    version_path = product_root / "version.json"
    if not (
        contract_path.is_file()
        and version_path.is_file()
        and (product_root / "tests").is_dir()
    ):
        raise UpgradeError(
            f"{product_root} is not a claude-multi source checkout with tests; "
            "the evidence suite needs the checkout — set "
            "CLAUDE_MULTI_SOURCE_REPO to the nixos-dotfiles checkout"
        )
    contract_backup = contract_path.read_bytes()
    version_backup = version_path.read_bytes()
    sync_backups: dict[Path, bytes] = {}

    promoted = render_contract(native_contract, inspection, today=today)
    version_doc = json.loads(version_backup.decode("utf-8"))
    version_doc["catalog_version"] = int(version_doc["catalog_version"]) + 1
    messages = [
        f"candidate: {inspection.path}",
        f"inspected offline: --version ok, --help ok, sha256 {inspection.sha256[:12]}...",
    ]
    if skip_note is not None:
        messages.append(skip_note)
    try:
        _write_repo_file(
            contract_path,
            (json.dumps(promoted, indent=2, sort_keys=True) + "\n").encode("utf-8"),
        )
        _write_repo_file(
            version_path,
            (json.dumps(version_doc, indent=2) + "\n").encode("utf-8"),
        )
        synced = _sync_pinned_test_literals(
            product_root,
            old_version=native_contract["claude"]["validated_version"],
            new_version=inspection.version,
            old_sha256=native_contract["claude"]["executable"].get("sha256"),
            new_sha256=inspection.sha256,
            old_inspected_at=native_contract["claude"]["executable"].get(
                "inspected_at"
            ),
            new_inspected_at=today,
            old_catalog_version=int(version_doc["catalog_version"]) - 1,
            backups=sync_backups,
        )
        if synced:
            messages.append(
                "synced version-pinned test literals (" + ", ".join(synced) + ")"
            )
        _note(
            f"[2/{total}] promoted into the checkout; version-pinned test "
            "literals synced"
        )
        _note(
            f"[3/{total}] running the offline evidence suite against the "
            "candidate (about 2 minutes; the suite is silent between updates)…"
        )
        with _Heartbeat(progress, f"  [3/{total}] evidence suite still running"):
            evidence = runner(
                [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                cwd=product_root,
                env={
                    **(os.environ if env is None else env),
                    "PYTHONPATH": "src:tests",
                },
                capture_output=True,
                text=True,
                timeout=1800,
            )
        tail = (evidence.stdout or "") + (evidence.stderr or "")
        if evidence.returncode != 0:
            raise UpgradeError(
                "the offline evidence suite failed against the candidate "
                "contract; the repo was restored unchanged and no override "
                "was written. Failing tail:\n"
                + "\n".join(tail.strip().splitlines()[-15:])
            )
        ran = re.search(r"Ran (\d+) tests", tail)
        messages.append(
            f"evidence: offline suite green ({ran.group(1) if ran else '?'} tests, "
            "includes the real-binary compaction/delegation probes)"
        )
    except BaseException:
        # The restore runs precisely when things already failed — its
        # writes must be just as crash-atomic as the promotion's (U1).
        _write_repo_file(contract_path, contract_backup)
        _write_repo_file(version_path, version_backup)
        for synced_path, synced_bytes in sync_backups.items():
            _write_repo_file(synced_path, synced_bytes)
        raise

    state.ensure_private_dir(override_path.parent)
    _note(f"[4/{total}] evidence green; writing the operator contract override…")
    state.atomic_write(
        override_path,
        (json.dumps(promoted, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    messages.append(
        f"active now: operator contract override {override_path} pins "
        f"{inspection.version} (no rebuild or restart needed)"
    )
    messages.append(
        f"promoted into the source checkout (catalog_version "
        f"{version_doc['catalog_version']}); the packaged baseline lands at "
        "the next natural Home Manager activation"
    )
    if not activate:
        return UpgradeOutcome(
            kind="prepared", inspection=inspection, messages=tuple(messages)
        )
    _activate(messages, total)
    return UpgradeOutcome(
        kind="activated", inspection=inspection, messages=tuple(messages)
    )
