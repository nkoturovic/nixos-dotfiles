"""Tests for owner-controlled symlink-safe state primitives."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_multi import state
from claude_multi.state import StateError


class StateTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-state-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for path in sorted(self.root.rglob("*"), reverse=True):
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                os.chmod(path, 0o700)
                path.rmdir()
        self.root.rmdir()

    def _dir(self, name: str = "state") -> Path:
        return state.ensure_private_dir(self.root / name)


class NameValidationTests(StateTestCase):
    def test_safe_names_accepted(self) -> None:
        for name in ("default", "my-composition", "a.b_c", "x" * 64):
            self.assertEqual(state.check_name(name), name)

    def test_unsafe_names_rejected(self) -> None:
        for name in ("", "../x", "a/b", "Absolute", "-lead", ".hidden", "x" * 65):
            with self.assertRaises(StateError, msg=f"name {name!r} must be rejected"):
                state.check_name(name)


class AtomicWriteTests(StateTestCase):
    def test_roundtrip_mode_0600(self) -> None:
        directory = self._dir()
        target = directory / "session.json"
        state.atomic_write(target, b'{"ok": true}\n')
        self.assertEqual(state.read_private(target), b'{"ok": true}\n')
        mode = stat.S_IMODE(os.lstat(target).st_mode)
        self.assertEqual(mode, 0o600)

    def test_overwrite_replaces_content(self) -> None:
        directory = self._dir()
        target = directory / "session.json"
        state.atomic_write(target, b"first")
        state.atomic_write(target, b"second")
        self.assertEqual(state.read_private(target), b"second")

    def test_symlink_target_rejected(self) -> None:
        directory = self._dir()
        real = directory / "real.json"
        state.atomic_write(real, b"data")
        link = directory / "link.json"
        link.symlink_to(real)
        with self.assertRaises(StateError):
            state.atomic_write(link, b"nope")
        with self.assertRaises(StateError):
            state.read_private(link)
        self.assertEqual(state.read_private(real), b"data")

    def test_symlink_parent_rejected(self) -> None:
        directory = self._dir("real-state")
        link = self.root / "link-state"
        link.symlink_to(directory)
        with self.assertRaises(StateError):
            state.atomic_write(link / "session.json", b"nope")

    def test_non_regular_target_rejected(self) -> None:
        directory = self._dir()
        (directory / "subdir").mkdir()
        with self.assertRaises(StateError):
            state.atomic_write(directory / "subdir", b"nope")

    def test_group_accessible_parent_rejected(self) -> None:
        directory = self._dir()
        os.chmod(directory, 0o750)
        with self.assertRaises(StateError):
            state.atomic_write(directory / "session.json", b"nope")

    def test_group_accessible_file_rejected_on_read(self) -> None:
        directory = self._dir()
        target = directory / "session.json"
        state.atomic_write(target, b"data")
        os.chmod(target, 0o640)
        with self.assertRaises(StateError):
            state.read_private(target)

    def test_missing_parent_rejected(self) -> None:
        with self.assertRaises(StateError):
            state.atomic_write(self.root / "missing" / "session.json", b"nope")

    def test_failed_write_leaves_no_temp_files(self) -> None:
        directory = self._dir()
        os.chmod(directory, 0o500)
        with self.assertRaises(OSError):
            state.atomic_write(directory / "session.json", b"nope")
        os.chmod(directory, 0o700)
        leftovers = [p for p in directory.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])

    def test_injected_post_temp_failure_cleans_up_and_preserves_target(self) -> None:
        directory = self._dir()
        target = directory / "session.json"
        state.atomic_write(target, b"original")
        with mock.patch.object(state.os, "replace", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                state.atomic_write(target, b"new")
        self.assertEqual(state.read_private(target), b"original")
        leftovers = [p for p in directory.iterdir() if p.name.startswith(".")]
        self.assertEqual(leftovers, [])


class DirectoryTests(StateTestCase):
    def test_ensure_private_dir_creates_nested_0700(self) -> None:
        created = state.ensure_private_dir(self.root / "a" / "b")
        self.assertTrue(created.is_dir())
        self.assertEqual(stat.S_IMODE(os.lstat(created).st_mode) & 0o077, 0)
        self.assertEqual(
            stat.S_IMODE(os.lstat(self.root / "a").st_mode) & 0o077, 0
        )

    def test_child_first_creation_hardens_intermediate_parents(self) -> None:
        state.ensure_private_dir(self.root / "x" / "y" / "z")
        for part in ("x", "x/y", "x/y/z"):
            mode = stat.S_IMODE(os.lstat(self.root / part).st_mode)
            self.assertEqual(mode & 0o077, 0, f"{part} not hardened")

    def test_ensure_private_dir_rejects_symlink(self) -> None:
        directory = self._dir("real")
        link = self.root / "link"
        link.symlink_to(directory)
        with self.assertRaises(StateError):
            state.ensure_private_dir(link)


class FileLockTests(StateTestCase):
    def test_lock_exclusion_and_release(self) -> None:
        directory = self._dir()
        target = directory / "last-session.json"
        first = state.FileLock(target)
        second = state.FileLock(target)
        self.assertTrue(first.acquire(blocking=False))
        self.assertFalse(second.acquire(blocking=False))
        first.release()
        self.assertTrue(second.acquire(blocking=False))
        second.release()

    def test_lock_context_manager(self) -> None:
        directory = self._dir()
        target = directory / "last-session.json"
        with state.FileLock(target):
            rival = state.FileLock(target)
            self.assertFalse(rival.acquire(blocking=False))
        self.assertTrue(state.FileLock(target).acquire(blocking=False))

    def test_lock_file_is_mode_0600_regular(self) -> None:
        directory = self._dir()
        target = directory / "last-session.json"
        lock = state.FileLock(target)
        lock.acquire(blocking=False)
        info = os.lstat(lock.lock_path)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        lock.release()

    def test_lock_rejects_symlinked_lock_file(self) -> None:
        directory = self._dir()
        target = directory / "last-session.json"
        real = directory / "elsewhere"
        state.atomic_write(real, b"x")
        (directory / "last-session.json.lock").symlink_to(real)
        with self.assertRaises(StateError):
            state.FileLock(target).acquire(blocking=False)


if __name__ == "__main__":
    unittest.main()
