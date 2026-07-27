"""Tests for the evidence-gated re-pin flow (claude-multi update)."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
import tempfile
import unittest
from pathlib import Path

from claude_multi import state, upgrade
from claude_multi.upgrade import UpgradeError


FAKE_CLAUDE = """#!/bin/sh
if [ "$1" = "--version" ]; then
    echo "VERSION_NAME (Claude Code)"
    exit 0
fi
if [ "$1" = "--help" ]; then
    echo "Usage: claude [options] - Claude Code"
    exit 0
fi
exit 0
"""

OLD_CONTRACT = {
    "claude": {
        "validated_version": "2.1.217",
        "executable": {
            "configured_path": "",  # set in setUp
            "resolved_path": "",    # set in setUp
            "sha256": "a" * 64,
            "inspection": "test",
            "inspected_at": "2026-01-01",
        },
    },
    "lifecycle_evidence": {
        "inspected_version": "2.1.217",
        "evidence_id": "deepwork-thing-2.1.217",
    },
}


class UpgradeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-upgrade-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.versions = self.root / "versions"
        self.versions.mkdir()
        self.old = self._write_version("2.1.217")
        self.new = self._write_version("2.1.218")
        self.link = self.root / "bin" / "claude"
        self.link.parent.mkdir()
        self.link.symlink_to(self.old)
        self.contract = json.loads(json.dumps(OLD_CONTRACT))
        self.contract["claude"]["executable"]["configured_path"] = str(self.link)
        self.contract["claude"]["executable"]["resolved_path"] = str(self.old)

    def _write_version(self, name: str) -> Path:
        path = self.versions / name
        path.write_text(FAKE_CLAUDE.replace("VERSION_NAME", name), encoding="utf-8")
        path.chmod(0o755)
        return path

    def _product_tree(self) -> Path:
        product = self.root / "repo" / "home-manager" / "claude-multi"
        (product / "catalog").mkdir(parents=True)
        (product / "tests").mkdir()
        (product / "catalog" / "native-contract.json").write_text(
            json.dumps(self.contract, indent=2) + "\n", encoding="utf-8"
        )
        (product / "version.json").write_text(
            json.dumps({"version": 1, "launcher_version": "2.3.1", "catalog_version": 3}) + "\n",
            encoding="utf-8",
        )
        return product

    def _runner(self, returncode: int, out: str = "Ran 42 tests"):
        def run(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], returncode, out, "")
        return run


class InspectCandidateTests(UpgradeTestCase):
    def test_inspects_a_valid_artifact(self) -> None:
        result = upgrade.inspect_candidate(self.new)
        self.assertEqual(result.version, "2.1.218")
        self.assertEqual(len(result.sha256), 64)

    def test_rejects_symlink_missing_shape_and_mode(self) -> None:
        with self.assertRaisesRegex(UpgradeError, "symlink"):
            upgrade.inspect_candidate(self.link)
        with self.assertRaisesRegex(UpgradeError, "missing"):
            upgrade.inspect_candidate(self.versions / "2.1.999")
        odd = self.versions / "claude-dev"
        odd.write_text("#!/bin/sh\n", encoding="utf-8")
        odd.chmod(0o755)
        with self.assertRaisesRegex(UpgradeError, "X.Y.Z"):
            upgrade.inspect_candidate(odd)
        noexec = self._write_version("2.1.300")
        noexec.chmod(0o644)
        with self.assertRaisesRegex(UpgradeError, "not executable"):
            upgrade.inspect_candidate(noexec)


class FindCandidateTests(UpgradeTestCase):
    def test_newer_symlink_target_wins(self) -> None:
        self.link.unlink()
        self.link.symlink_to(self.new)
        self.assertEqual(upgrade.find_candidate(self.contract), self.new)

    def test_newest_retained_when_symlink_current(self) -> None:
        self.assertEqual(upgrade.find_candidate(self.contract), self.new)

    def test_none_when_nothing_newer(self) -> None:
        self.new.unlink()
        self.assertIsNone(upgrade.find_candidate(self.contract))

    def test_rejects_malformed_pin(self) -> None:
        self.contract["claude"]["validated_version"] = "dev"
        with self.assertRaisesRegex(UpgradeError, "not X.Y.Z"):
            upgrade.find_candidate(self.contract)


class RenderContractTests(UpgradeTestCase):
    def test_promotes_executable_facts_and_provenance(self) -> None:
        inspection = upgrade.inspect_candidate(self.new)
        promoted = upgrade.render_contract(self.contract, inspection, today="2026-07-24")
        self.assertEqual(promoted["claude"]["validated_version"], "2.1.218")
        self.assertEqual(
            promoted["claude"]["executable"]["resolved_path"], str(self.new)
        )
        self.assertEqual(promoted["claude"]["executable"]["sha256"], inspection.sha256)
        self.assertEqual(promoted["claude"]["executable"]["inspected_at"], "2026-07-24")
        self.assertEqual(
            promoted["lifecycle_evidence"]["inspected_version"], "2.1.218"
        )
        self.assertEqual(
            promoted["lifecycle_evidence"]["evidence_id"], "deepwork-thing-2.1.218"
        )
        # The prior contract is not mutated.
        self.assertEqual(self.contract["claude"]["validated_version"], "2.1.217")


class RunUpgradeTests(UpgradeTestCase):

    def test_override_written_and_repo_promoted(self) -> None:
        product = self._product_tree()
        override_path = self.root / "config" / "native-contract.json"
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=self.contract,
            override_path=override_path,
            today="2026-07-24",
            runner=self._runner(0),
        )
        self.assertEqual(outcome.kind, "prepared")
        self.assertTrue(any("active now" in line for line in outcome.messages))
        promoted = json.loads(override_path.read_bytes())
        self.assertEqual(promoted["claude"]["validated_version"], "2.1.218")
        self.assertEqual(stat.S_IMODE(os.lstat(override_path).st_mode), 0o600)
        repo_contract = json.loads(
            (product / "catalog" / "native-contract.json").read_bytes()
        )
        self.assertEqual(repo_contract["claude"]["validated_version"], "2.1.218")
        version_doc = json.loads((product / "version.json").read_bytes())
        self.assertEqual(version_doc["catalog_version"], 4)

    def test_promotion_syncs_pinned_test_literals(self) -> None:
        product = self._product_tree()
        old_sha = self.contract["claude"]["executable"]["sha256"]
        pinned = (
            'validated = "2.1.217"\n'
            'resolved = "/home/user/.local/share/claude/versions/2.1.217"\n'
            f'sha = "{old_sha}"\n'
            'floors = {"opus5": "2.1.217"}  # decoupled from the artifact version\n'
            'self.assertEqual(bundle.docs["version"]["catalog_version"], 3)\n'
        )
        (product / "tests" / "test_catalog.py").write_text(pinned, encoding="utf-8")
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=self.contract,
            override_path=self.root / "config" / "native-contract.json",
            today="2026-07-24",
            runner=self._runner(0),
        )
        text = (product / "tests" / "test_catalog.py").read_text(encoding="utf-8")
        inspection = upgrade.inspect_candidate(self.new)
        self.assertIn('validated = "2.1.218"', text)
        self.assertIn("versions/2.1.218", text)
        self.assertIn(f'sha = "{inspection.sha256}"', text)
        self.assertIn('"catalog_version"], 4)', text)
        # The decoupled per-model floor line is never touched.
        self.assertIn('floors = {"opus5": "2.1.217"}', text)
        self.assertTrue(
            any("synced version-pinned test literals" in line for line in outcome.messages)
        )

    def test_evidence_failure_restores_synced_test_files(self) -> None:
        product = self._product_tree()
        pinned = 'validated = "2.1.217"\n'
        target = product / "tests" / "test_native_contract.py"
        target.write_text(pinned, encoding="utf-8")
        with self.assertRaisesRegex(UpgradeError, "evidence suite failed"):
            upgrade.run_upgrade(
                checkout_root=product,
                native_contract=self.contract,
                override_path=self.root / "config" / "native-contract.json",
                today="2026-07-24",
                runner=self._runner(1, "FAILED"),
            )
        self.assertEqual(target.read_text(encoding="utf-8"), pinned)

    def test_evidence_failure_restores_repo_and_writes_no_override(self) -> None:
        product = self._product_tree()
        before_contract = (product / "catalog" / "native-contract.json").read_bytes()
        before_version = (product / "version.json").read_bytes()
        override_path = self.root / "config" / "native-contract.json"
        with self.assertRaisesRegex(UpgradeError, "evidence suite failed"):
            upgrade.run_upgrade(
                checkout_root=product,
                native_contract=self.contract,
                override_path=override_path,
                today="2026-07-24",
                runner=self._runner(1, "FAILED"),
            )
        self.assertEqual((product / "catalog" / "native-contract.json").read_bytes(), before_contract)
        self.assertEqual((product / "version.json").read_bytes(), before_version)
        self.assertFalse(override_path.exists())

    def test_missing_checkout_fails_closed(self) -> None:
        product = self._product_tree()
        override_path = self.root / "config" / "native-contract.json"
        with self.assertRaisesRegex(UpgradeError, "source checkout"):
            upgrade.run_upgrade(
                checkout_root=self.root / "elsewhere" / "home-manager" / "claude-multi",
                native_contract=self.contract,
                override_path=override_path,
                today="2026-07-24",
                runner=self._runner(0),
            )
        self.assertFalse(override_path.exists())

    def test_already_current(self) -> None:
        self.new.unlink()
        product = self._product_tree()
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=self.contract,
            override_path=self.root / "config" / "native-contract.json",
            today="2026-07-24",
            runner=self._runner(0),
        )
        self.assertEqual(outcome.kind, "current")
        self.assertIn("nothing to re-pin", outcome.messages[0])


if __name__ == "__main__":
    unittest.main()

class CurrentCleanupTests(UpgradeTestCase):
    def test_current_run_removes_a_redundant_override(self) -> None:
        self.new.unlink()  # nothing newer than the pin
        product = self._product_tree()
        override_path = self.root / "config" / "native-contract.json"
        state.ensure_private_dir(override_path.parent)
        state.atomic_write(override_path, b"{}\n")
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=self.contract,
            override_path=override_path,
            today="2026-07-24",
            runner=self._runner(0),
        )
        self.assertEqual(outcome.kind, "current")
        self.assertFalse(override_path.exists())
        # An empty override is unversioned: removed as unreadable, not "redundant".
        self.assertTrue(any("removed unreadable" in m for m in outcome.messages))

    def test_activate_uses_the_repo_root_as_flake_target(self) -> None:
        product = self._product_tree()
        calls: list[list[str]] = []

        def recording_runner(args, **kwargs):
            calls.append(list(args))
            return subprocess.CompletedProcess(args, 0, "Ran 42 tests", "")

        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=self.contract,
            override_path=self.root / "config" / "native-contract.json",
            today="2026-07-24",
            runner=recording_runner,
            activate=True,
        )
        self.assertEqual(outcome.kind, "activated")
        hm = calls[-1]
        self.assertEqual(hm[:3], ["home-manager", "switch", "--flake"])
        self.assertEqual(hm[3], str(self.root / "repo") + "#kotur")


class UpdateLockWaitNoteTests(unittest.TestCase):
    """A contended update says so instead of sitting silent (review nit)."""

    def test_waiting_note_emitted_when_lock_held(self) -> None:
        import tempfile
        from pathlib import Path

        from claude_multi import state, upgrade as upgrade_mod

        root = Path(tempfile.mkdtemp(prefix="claude-multi-lock-"))
        self.addCleanup(shutil.rmtree, root, True)
        override = root / "cfg" / "native-contract.json"
        state.ensure_private_dir(override.parent)
        blocker = state.FileLock(override)
        blocker.acquire(blocking=True)
        notes: list[str] = []
        outcome_holder: list[object] = []

        import threading

        def _run() -> None:
            outcome_holder.append(
                upgrade_mod.run_upgrade(
                    checkout_root=root / "checkout",
                    native_contract=_MINIMAL_CONTRACT,
                    override_path=override,
                    today="2026-07-27",
                    runner=_unused_runner,
                    progress=notes.append,
                )
            )

        worker = threading.Thread(target=_run)
        worker.start()
        try:
            deadline = time.time() + 5
            while not notes and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(any("waiting" in note for note in notes))
        finally:
            blocker.release()
            worker.join(timeout=10)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome_holder[0].kind, "current")


_MINIMAL_CONTRACT = {
    "claude": {
        "validated_version": "9.9.9",
        "executable": {
            "configured_path": "/nonexistent/claude",
            "resolved_path": "/nonexistent/versions/9.9.9",
        },
    }
}


def _unused_runner(*args, **kwargs):  # pragma: no cover - never reached
    raise AssertionError("runner must not run for the current path")


class HeartbeatTests(unittest.TestCase):
    def test_beats_and_stops_cleanly(self) -> None:
        notes: list[str] = []
        with upgrade._Heartbeat(notes.append, "  [3/4] evidence suite still running", interval=0.02):
            time.sleep(0.07)
        self.assertTrue(notes)
        self.assertIn("evidence suite still running", notes[0])
        self.assertIn("elapsed", notes[0])
        count_at_stop = len(notes)
        time.sleep(0.06)
        # The thread is stopped on exit: no further beats.
        self.assertEqual(len(notes), count_at_stop)

    def test_noop_without_progress(self) -> None:
        with upgrade._Heartbeat(None, "phase", interval=0.01):
            time.sleep(0.03)


class OverrideRedundancyGateTests(UpgradeTestCase):
    """H2: the 'current' path deletes only genuinely-redundant overrides."""

    def _current_setup(self, override_version: str):
        self.new.unlink()  # nothing newer than the pin
        product = self._product_tree()
        override_path = self.root / "config" / "native-contract.json"
        state.ensure_private_dir(override_path.parent)
        doc = json.loads(json.dumps(self.contract))
        doc["claude"]["validated_version"] = override_version
        state.atomic_write(
            override_path, (json.dumps(doc, indent=2) + "\n").encode("utf-8")
        )
        effective = json.loads(json.dumps(self.contract))
        effective["claude"]["validated_version"] = override_version
        return product, override_path, effective

    def test_newer_override_is_kept_not_deleted(self) -> None:
        product, override_path, effective = self._current_setup("2.1.221")
        packaged = json.loads(json.dumps(self.contract))  # baseline stays 2.1.217
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=effective,
            override_path=override_path,
            today="2026-07-27",
            runner=self._runner(0),
            packaged_contract=packaged,
        )
        self.assertEqual(outcome.kind, "current")
        self.assertTrue(override_path.exists())
        self.assertTrue(any("not redundant" in line for line in outcome.messages))

    def test_equal_override_is_removed_as_redundant(self) -> None:
        product, override_path, effective = self._current_setup("2.1.217")
        packaged = json.loads(json.dumps(self.contract))
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=effective,
            override_path=override_path,
            today="2026-07-27",
            runner=self._runner(0),
            packaged_contract=packaged,
        )
        self.assertEqual(outcome.kind, "current")
        self.assertFalse(override_path.exists())

    def test_newer_override_with_activate_runs_the_switch(self) -> None:
        product, override_path, effective = self._current_setup("2.1.221")
        packaged = json.loads(json.dumps(self.contract))
        calls = []

        def recording_runner(*args, **kwargs):
            calls.append(args[0])
            return subprocess.CompletedProcess(args[0], 0, "ok", "")

        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=effective,
            override_path=override_path,
            today="2026-07-27",
            runner=recording_runner,
            activate=True,
            packaged_contract=packaged,
        )
        self.assertEqual(outcome.kind, "activated")
        self.assertEqual(len(calls), 1)
        self.assertIn("home-manager", calls[0][0])
        self.assertTrue(override_path.exists())

    def test_missing_packaged_contract_keeps_the_override(self) -> None:
        product, override_path, effective = self._current_setup("2.1.217")
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=effective,
            override_path=override_path,
            today="2026-07-27",
            runner=self._runner(0),
        )
        self.assertTrue(override_path.exists())


class SyncAnchoringTests(UpgradeTestCase):
    """H6: prefix-colliding pins must never mangle decoupled literals."""

    def test_prefix_collision_does_not_mangle_other_versions(self) -> None:
        product = self._product_tree()
        pinned = (
            'validated = "2.1.2"\n'
            'default_floor = floors.get(model_id, "2.1.216")\n'
            '# 2.1.216 remains the truthful minimum floor\n'
            'fixture = "2.1.300"\n'
        )
        target = product / "tests" / "test_catalog.py"
        target.write_text(pinned, encoding="utf-8")
        backups: dict = {}
        synced = upgrade._sync_pinned_test_literals(
            product,
            old_version="2.1.2",
            new_version="2.1.20",
            old_sha256=None,
            new_sha256="b" * 64,
            old_inspected_at=None,
            new_inspected_at="2026-07-27",
            old_catalog_version=3,
            backups=backups,
        )
        text = target.read_text(encoding="utf-8")
        self.assertEqual(synced, ["tests/test_catalog.py"])
        self.assertIn('validated = "2.1.20"', text)
        self.assertIn('"2.1.216"', text)          # never mangled to 2.1.2016
        self.assertIn('"2.1.300"', text)          # never mangled to 2.1.2000
        self.assertIn("2.1.216 remains", text)    # comments anchored too
        self.assertNotIn("2.1.2016", text)
        self.assertNotIn("2.1.200", text.replace("2.1.300", ""))

    def test_floors_get_lines_are_never_synced(self) -> None:
        product = self._product_tree()
        pinned = (
            'validated = "2.1.217"\n'
            'floors = {"opus5": "2.1.217"}\n'
            'x = floors.get(model_id, "2.1.217")\n'
        )
        target = product / "tests" / "test_catalog.py"
        target.write_text(pinned, encoding="utf-8")
        upgrade._sync_pinned_test_literals(
            product,
            old_version="2.1.217",
            new_version="2.1.218",
            old_sha256=None,
            new_sha256="b" * 64,
            old_inspected_at=None,
            new_inspected_at="2026-07-27",
            old_catalog_version=3,
            backups={},
        )
        text = target.read_text(encoding="utf-8")
        self.assertIn('validated = "2.1.218"', text)
        self.assertIn('floors = {"opus5": "2.1.217"}', text)
        self.assertIn('floors.get(model_id, "2.1.217")', text)


class CandidateFallbackTests(UpgradeTestCase):
    """H10: an invalid highest-named artifact is skipped, not fatal."""

    def test_invalid_highest_entry_falls_back_with_a_note(self) -> None:
        bogus = self._write_version("2.1.300")
        bogus.chmod(0o644)  # not executable -> fails inspection
        product = self._product_tree()
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=self.contract,
            override_path=self.root / "config" / "native-contract.json",
            today="2026-07-27",
            runner=self._runner(0),
        )
        self.assertEqual(outcome.kind, "prepared")
        self.assertEqual(outcome.inspection.version, "2.1.218")
        self.assertTrue(any("skipped unusable" in line for line in outcome.messages))
        self.assertTrue(any("2.1.300" in line for line in outcome.messages))

    def test_all_candidates_invalid_reports_skips(self) -> None:
        self.new.chmod(0o644)
        product = self._product_tree()
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=self.contract,
            override_path=self.root / "config" / "native-contract.json",
            today="2026-07-27",
            runner=self._runner(0),
        )
        self.assertEqual(outcome.kind, "current")
        self.assertTrue(any("skipped unusable" in line for line in outcome.messages))


class RepoFileWriteTests(unittest.TestCase):
    """H15: promotion writes are crash-atomic and preserve repo modes."""

    def test_write_repo_file_preserves_mode_and_content(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="claude-multi-repofile-"))
        self.addCleanup(shutil.rmtree, root, True)
        target = root / "file.json"
        target.write_text("old\n", encoding="utf-8")
        target.chmod(0o644)
        upgrade._write_repo_file(target, b"new\n")
        self.assertEqual(target.read_bytes(), b"new\n")
        self.assertEqual(stat.S_IMODE(os.lstat(target).st_mode), 0o644)
        self.assertEqual(list(root.glob("*.tmp-*")), [])

    def test_write_repo_file_new_file_gets_644(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="claude-multi-repofile-"))
        self.addCleanup(shutil.rmtree, root, True)
        target = root / "created.json"
        upgrade._write_repo_file(target, b"x\n")
        self.assertEqual(stat.S_IMODE(os.lstat(target).st_mode), 0o644)


class CandidateOrderingTests(UpgradeTestCase):
    """U8: retained versions order by version key, never lexically."""

    def test_narrow_digit_versions_order_numerically(self) -> None:
        self.contract["claude"]["validated_version"] = "2.1.50"
        # Neutralize the symlink: it must resolve to the pin (not a candidate).
        pinned = self._write_version("2.1.50")
        self.link.unlink()
        self.link.symlink_to(pinned)
        old = self._write_version("2.1.99")
        newer = self._write_version("2.1.218")
        ordered = upgrade.find_candidates(self.contract)
        # Retained versions are newest-first BY VERSION KEY: 2.1.218 > 2.1.217
        # > 2.1.99 — lexically, 2.1.99 would incorrectly sort first (U8).
        self.assertEqual(ordered, [newer, self.old, old])

    def test_restore_writes_are_mode_preserving_atomic(self) -> None:
        product = self._product_tree()
        target = product / "catalog" / "native-contract.json"
        target.chmod(0o644)
        before_mode = stat.S_IMODE(os.lstat(target).st_mode)
        with self.assertRaisesRegex(UpgradeError, "evidence suite failed"):
            upgrade.run_upgrade(
                checkout_root=product,
                native_contract=self.contract,
                override_path=self.root / "config" / "native-contract.json",
                today="2026-07-27",
                runner=self._runner(1, "FAILED"),
            )
        self.assertEqual(stat.S_IMODE(os.lstat(target).st_mode), before_mode)
        self.assertEqual(
            target.read_bytes(), json.dumps(self.contract, indent=2).encode() + b"\n"
        )
        self.assertEqual(list(product.glob("**/*.tmp-*")), [])


class FinalGateResolutionTests(UpgradeTestCase):
    """SF1/SF2/N2: the final gate's resolution set."""

    def test_schema_invalid_override_is_removed_not_kept(self) -> None:
        # Valid JSON + version, but a version shape the strict schema
        # rejects -> broken, removed (previously kept -> doctor loop).
        self.new.unlink()
        product = self._product_tree()
        override_path = self.root / "config" / "native-contract.json"
        state.ensure_private_dir(override_path.parent)
        doc = json.loads(json.dumps(self.contract))
        doc["claude"]["validated_version"] = "not.a.version"
        state.atomic_write(
            override_path, (json.dumps(doc, indent=2) + "\n").encode("utf-8")
        )
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=self.contract,
            override_path=override_path,
            today="2026-07-27",
            runner=self._runner(0),
            packaged_contract=self.contract,
        )
        self.assertFalse(override_path.exists())
        self.assertTrue(any("removed unreadable" in m for m in outcome.messages))

    def test_override_broken_flag_removes_loader_rejected_override(self) -> None:
        # Valid version shape but the loader already rejected the file
        # (schema/secret scan): the caller's flag removes it directly.
        self.new.unlink()
        product = self._product_tree()
        override_path = self.root / "config" / "native-contract.json"
        state.ensure_private_dir(override_path.parent)
        doc = json.loads(json.dumps(self.contract))
        doc["claude"]["validated_version"] = "9.9.9"
        state.atomic_write(
            override_path, (json.dumps(doc, indent=2) + "\n").encode("utf-8")
        )
        outcome = upgrade.run_upgrade(
            checkout_root=product,
            native_contract=self.contract,
            override_path=override_path,
            today="2026-07-27",
            runner=self._runner(0),
            packaged_contract=self.contract,
            override_broken=True,
        )
        self.assertFalse(override_path.exists())

    def test_write_repo_file_refuses_symlinked_target(self) -> None:
        root = Path(tempfile.mkdtemp(prefix="claude-multi-symlink-"))
        self.addCleanup(shutil.rmtree, root, True)
        real = root / "real"
        real.write_text("x\n", encoding="utf-8")
        link = root / "link"
        link.symlink_to(real)
        with self.assertRaisesRegex(UpgradeError, "symlinked"):
            upgrade._write_repo_file(link, b"y\n")
        self.assertEqual(real.read_text(encoding="utf-8"), "x\n")
