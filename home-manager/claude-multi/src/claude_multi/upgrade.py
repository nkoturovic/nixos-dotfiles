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
   backup/restore on any failure;
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
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class UpgradeError(RuntimeError):
    """Raised on any failed upgrade step (fail closed, repo untouched)."""


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


def find_candidate(native_contract: dict[str, Any]) -> Path | None:
    """Newest acceptable artifact: newer symlink target, else newest retained."""

    record = native_contract["claude"]
    pinned = _version_key(record["validated_version"])
    if pinned is None:
        raise UpgradeError(
            f"pinned version {record['validated_version']!r} is not X.Y.Z"
        )
    configured = Path(record["executable"]["configured_path"])
    if os.path.lexists(configured):
        target = Path(os.path.realpath(configured))
        key = _version_key(target.name)
        if key is not None and key > pinned:
            return target
    versions_dir = Path(record["executable"]["resolved_path"]).parent
    best: tuple[tuple[int, ...], Path] | None = None
    try:
        entries = sorted(versions_dir.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        key = _version_key(entry.name)
        if key is None or key <= pinned:
            continue
        if best is None or key > best[0]:
            best = (key, entry)
    return best[1] if best is not None else None


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
) -> UpgradeOutcome:
    """Inspect → promote → evidence → override → (optionally) activate.

    The operator override takes effect the moment it is written — no
    rebuild, no service restart. Promoting into the source checkout keeps
    the packaged baseline honest; it lands at the next natural Home Manager
    activation (or immediately with ``--activate``). Any failure before the
    override write leaves both the repo and the override byte-identical.
    """

    from . import state  # local import: hardened writes for the config root

    candidate = find_candidate(native_contract)
    if candidate is None:
        return UpgradeOutcome(
            kind="current",
            inspection=None,
            messages=(
                f"pinned Claude {native_contract['claude']['validated_version']} "
                "is the newest installed artifact; nothing to re-pin.",
            ),
        )
    inspection = inspect_candidate(candidate)
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

    promoted = render_contract(native_contract, inspection, today=today)
    version_doc = json.loads(version_backup.decode("utf-8"))
    version_doc["catalog_version"] = int(version_doc["catalog_version"]) + 1
    messages = [
        f"candidate: {inspection.path}",
        f"inspected offline: --version ok, --help ok, sha256 {inspection.sha256[:12]}...",
    ]
    try:
        contract_path.write_text(
            json.dumps(promoted, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        version_path.write_text(
            json.dumps(version_doc, indent=2) + "\n", encoding="utf-8"
        )
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
        contract_path.write_bytes(contract_backup)
        version_path.write_bytes(version_backup)
        raise

    state.ensure_private_dir(override_path.parent)
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
    switch = runner(
        ["home-manager", "switch", "--flake", flake_target or str(checkout_root.parents[2]) + "#kotur"],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if switch.returncode != 0:
        tail = (switch.stdout or "") + (switch.stderr or "")
        raise UpgradeError(
            "home-manager switch failed; the override is already active and "
            "the repo baseline is promoted — activate manually. Tail:\n"
            + "\n".join(tail.strip().splitlines()[-15:])
        )
    messages.append("activated: home-manager switch completed")
    return UpgradeOutcome(
        kind="activated", inspection=inspection, messages=tuple(messages)
    )
