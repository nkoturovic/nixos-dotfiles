"""Owner-controlled, symlink-safe state primitives for claude-multi v2.

All launcher writes go through this module: parent directories must be owned
and group/other-inaccessible, targets must be regular files (never symlinks),
writes use a same-directory temporary file with mode 0600, fsync of file and
directory, and an atomic replace. Advisory sibling locks coordinate concurrent
launchers.

Crash-window contract: an OS or process hard crash after the temporary file
is fsynced but before ``os.replace`` may leave a same-directory
``.<name>.*.tmp`` stale file (mode 0600, owner-only). The previous target
content remains intact and no partial target is ever visible; the stale temp
file is harmless and may be removed by the owner. No automatic reaper is
provided. Ordinary Python exceptions always clean up the temporary file and
are covered by tests.
"""

from __future__ import annotations

import errno
import fcntl
import os
import re
import stat
import tempfile
from pathlib import Path


class StateError(OSError):
    """Raised when a state path or write violates safety requirements."""


SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def check_name(name: str) -> str:
    """Validate a state file name (composition, session, draft); no traversal."""

    if not SAFE_NAME.fullmatch(name):
        raise StateError(
            errno.EINVAL,
            f"unsafe state name {name!r}: must match {SAFE_NAME.pattern}",
        )
    return name


def _check_directory(path: Path) -> None:
    try:
        info = os.lstat(path)
    except FileNotFoundError as exc:
        raise StateError(errno.ENOENT, f"missing state directory {path}") from exc
    if stat.S_ISLNK(info.st_mode):
        raise StateError(errno.ELOOP, f"state directory {path} is a symlink")
    if not stat.S_ISDIR(info.st_mode):
        raise StateError(errno.ENOTDIR, f"state directory {path} is not a directory")
    if info.st_uid != os.geteuid():
        raise StateError(errno.EPERM, f"state directory {path} is not owner-controlled")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise StateError(
            errno.EPERM,
            f"state directory {path} must not be accessible by group/other",
        )


def _check_regular_file(path: Path) -> None:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode):
        raise StateError(errno.ELOOP, f"state file {path} is a symlink")
    if not stat.S_ISREG(info.st_mode):
        raise StateError(errno.EINVAL, f"state file {path} is not a regular file")
    if info.st_uid != os.geteuid():
        raise StateError(errno.EPERM, f"state file {path} is not owner-controlled")


def ensure_private_dir(path: Path | str) -> Path:
    """Create (if missing) and validate a private 0700 state directory.

    Every directory newly created by this call — the leaf and any missing
    parents — is hardened to mode 0700, so a child-first creation can never
    leave a group/other-accessible parent behind.
    """

    candidate = Path(path)
    if candidate.exists() or candidate.is_symlink():
        _check_directory(candidate)
        return candidate
    missing: list[Path] = []
    probe = candidate
    while not probe.exists():
        missing.append(probe)
        probe = probe.parent
    candidate.mkdir(mode=0o700, parents=True, exist_ok=True)
    for created in missing:
        os.chmod(created, 0o700)
    _check_directory(candidate)
    return candidate


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(path: Path | str, data: bytes) -> None:
    """Write data atomically: same-dir temp, mode 0600, fsync, replace.

    On ordinary exceptions the temporary file is removed and the previous
    target (if any) is preserved. See the module docstring for the documented
    hard-crash window that may leave a harmless stale temp file.
    """

    target = Path(path)
    if target.name in ("", ".", ".."):
        raise StateError(errno.EINVAL, f"unsafe state path {target}")
    parent = target.parent
    _check_directory(parent)
    if os.path.lexists(target):
        _check_regular_file(target)

    descriptor, temporary = tempfile.mkstemp(
        dir=parent, prefix=f".{target.name}.", suffix=".tmp"
    )
    try:
        os.fchmod(descriptor, 0o600)
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
    _fsync_directory(parent)


def read_private(path: Path | str) -> bytes:
    """Read a state file after regular-file, ownership, and mode checks."""

    target = Path(path)
    try:
        _check_regular_file(target)
    except FileNotFoundError as exc:
        raise StateError(errno.ENOENT, f"missing state file {target}") from exc
    info = os.lstat(target)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise StateError(
            errno.EPERM,
            f"state file {target} must not be accessible by group/other",
        )
    return target.read_bytes()


def remove_private(path: Path | str) -> bool:
    """Remove a private regular state file and durably fsync its directory."""

    target = Path(path)
    _check_directory(target.parent)
    if not os.path.lexists(target):
        return False
    _check_regular_file(target)
    os.unlink(target)
    _fsync_directory(target.parent)
    return True


class FileLock:
    """Advisory exclusive lock on a sibling ``<target>.lock`` file."""

    def __init__(self, target: Path | str):
        target = Path(target)
        self._lock_path = target.with_name(target.name + ".lock")
        self._descriptor: int | None = None

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def acquire(self, blocking: bool = True) -> bool:
        if self._descriptor is not None:
            raise StateError(errno.EINVAL, "lock already held")
        _check_directory(self._lock_path.parent)
        if os.path.lexists(self._lock_path):
            _check_regular_file(self._lock_path)
        descriptor = os.open(
            self._lock_path, os.O_RDWR | os.O_CREAT, 0o600
        )
        try:
            os.fchmod(descriptor, 0o600)
            flags = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(descriptor, flags)
        except BlockingIOError:
            os.close(descriptor)
            return False
        except BaseException:
            os.close(descriptor)
            raise
        self._descriptor = descriptor
        return True

    def release(self) -> None:
        if self._descriptor is None:
            return
        try:
            fcntl.flock(self._descriptor, fcntl.LOCK_UN)
        finally:
            os.close(self._descriptor)
            self._descriptor = None

    def __enter__(self) -> "FileLock":
        self.acquire(blocking=True)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
