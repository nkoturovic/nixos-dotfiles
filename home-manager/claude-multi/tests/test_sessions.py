"""Tests for launcher-owned session state."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_multi import catalog, composition, sessions, state, strict_json
from claude_multi.sessions import SessionError


CATALOG_ROOT = Path(__file__).resolve().parents[1]
FIXED_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


def _schema() -> dict:
    return strict_json.load(CATALOG_ROOT / "schemas" / "session.schema.json")


def _snapshot() -> dict:
    bundle = catalog.load_catalog(CATALOG_ROOT)
    resolved = composition.resolve(bundle.docs, bundle.default_composition)
    return composition.snapshot(resolved)


def _record(snapshot, session_id=FIXED_ID, forked_from=None) -> dict:
    return sessions.make_record(
        session_id=session_id,
        cwd="/project/path",
        composition_name="default",
        snapshot=snapshot,
        catalog_version=1,
        catalog_hash="sha256:" + "0" * 64,
        launcher_version="2.0.0",
        forked_from=forked_from,
        now="2026-07-21T00:00:00Z",
    )


class SessionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-sessions-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(self._cleanup)
        self.store = sessions.SessionStore(self.root, _schema())
        self.snapshot = _snapshot()

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)


class RecordTests(SessionTestCase):
    def test_save_load_roundtrip(self) -> None:
        record = _record(self.snapshot)
        path = self.store.save(record)
        self.assertEqual(
            stat.S_IMODE(os.lstat(path).st_mode), 0o600
        )
        loaded = self.store.load(FIXED_ID)
        self.assertEqual(loaded["session_id"], FIXED_ID)
        self.assertEqual(loaded["snapshot"], self.snapshot)
        self.assertEqual(loaded["forked_from"], None)

    def test_record_schema_valid(self) -> None:
        from claude_multi import validate

        record = _record(self.snapshot, forked_from=OTHER_ID)
        self.assertEqual(validate.validate(record, _schema()), [])

    def test_corrupt_record_reported(self) -> None:
        path = self.store._record_path(FIXED_ID)
        state.atomic_write(path, b"{not json")
        with self.assertRaisesRegex(SessionError, "corrupt session record"):
            self.store.load(FIXED_ID)

    def test_stale_record_missing(self) -> None:
        with self.assertRaises(SessionError):
            self.store.load(FIXED_ID)

    def test_schema_violating_record_rejected_on_save(self) -> None:
        record = _record(self.snapshot)
        record["snapshot"]["scalar_context_tokens"] = "not-a-number"
        with self.assertRaises(SessionError):
            self.store.save(record)

    def test_fork_relationship_recorded(self) -> None:
        record = _record(self.snapshot, session_id=OTHER_ID, forked_from=FIXED_ID)
        self.store.save(record)
        self.assertEqual(self.store.load(OTHER_ID)["forked_from"], FIXED_ID)

    def test_no_secret_fields_in_record(self) -> None:
        record = _record(self.snapshot)
        blob = strict_json.canonical_bytes(record).decode("utf-8")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", blob)
        self.assertNotIn("api-key", blob)


class MintTests(SessionTestCase):
    def test_new_id_collision_retry(self) -> None:
        self.store.save(_record(self.snapshot))
        sequence = iter([FIXED_ID, OTHER_ID])
        with mock.patch.object(sessions.uuid, "uuid4", side_effect=lambda: sessions.uuid.UUID(next(sequence))):
            minted = self.store.new_id()
        self.assertEqual(minted, OTHER_ID)

    def test_new_id_is_uuid4(self) -> None:
        minted = self.store.new_id()
        self.assertRegex(minted, sessions.UUID4)


class LinkTests(SessionTestCase):
    def test_link_adopts_unmanaged_session(self) -> None:
        record = _record(self.snapshot, session_id=OTHER_ID)
        self.store.link(record)
        self.assertEqual(self.store.load(OTHER_ID)["composition_name"], "default")

    def test_link_rejects_existing(self) -> None:
        self.store.save(_record(self.snapshot))
        with self.assertRaisesRegex(SessionError, "already managed"):
            self.store.link(_record(self.snapshot))

    def test_link_rejects_non_uuid(self) -> None:
        record = _record(self.snapshot, session_id="not-a-uuid")
        with self.assertRaisesRegex(SessionError, "not a UUID"):
            self.store.link(record)


class PointerTests(SessionTestCase):
    def test_update_and_last_roundtrip(self) -> None:
        self.assertTrue(self.store.update_last("/project/path", FIXED_ID))
        self.assertEqual(self.store.last("/project/path"), FIXED_ID)

    def test_last_missing(self) -> None:
        self.assertIsNone(self.store.last("/nowhere"))

    def test_lock_failure_skips_update_without_blocking(self) -> None:
        pointer = self.store._pointer_path("/project/path")
        lock = state.FileLock(pointer)
        self.assertTrue(lock.acquire(blocking=False))
        try:
            self.assertFalse(self.store.update_last("/project/path", FIXED_ID))
        finally:
            lock.release()
        self.assertIsNone(self.store.last("/project/path"))
        self.assertTrue(self.store.update_last("/project/path", OTHER_ID))
        self.assertEqual(self.store.last("/project/path"), OTHER_ID)

    def test_corrupt_pointer_treated_as_missing(self) -> None:
        pointer = self.store._pointer_path("/project/path")
        state.atomic_write(pointer, b"junk{")
        self.assertIsNone(self.store.last("/project/path"))

    def test_clear_last_only_clears_matching_pointer(self) -> None:
        self.store.update_last("/project/path", FIXED_ID)
        self.assertFalse(self.store.clear_last("/project/path", OTHER_ID))
        self.assertEqual(self.store.last("/project/path"), FIXED_ID)
        self.assertTrue(self.store.clear_last("/project/path", FIXED_ID))
        self.assertIsNone(self.store.last("/project/path"))

    def test_clear_last_missing_pointer(self) -> None:
        self.assertFalse(self.store.clear_last("/nowhere", FIXED_ID))


class DriftTests(SessionTestCase):
    def test_no_drift(self) -> None:
        record = _record(self.snapshot)
        report = sessions.drift_report(
            record,
            catalog_hash=record["catalog_hash"],
            composition_hash=record["composition_hash"],
        )
        self.assertEqual(report, [])

    def test_catalog_and_composition_drift(self) -> None:
        record = _record(self.snapshot)
        report = sessions.drift_report(
            record,
            catalog_hash="sha256:" + "f" * 64,
            composition_hash="sha256:" + "e" * 64,
        )
        self.assertEqual(len(report), 2)
        self.assertIn("trusted catalog changed", report[0])
        self.assertIn("composition 'default' changed", report[1])


class RootTests(unittest.TestCase):
    def test_xdg_roots(self) -> None:
        env = {
            "XDG_STATE_HOME": "/tmp/state-x",
            "XDG_CONFIG_HOME": "/tmp/config-x",
            "HOME": "/tmp/home-x",
        }
        self.assertEqual(sessions.state_root(env), Path("/tmp/state-x/claude-multi"))
        self.assertEqual(sessions.config_root(env), Path("/tmp/config-x/claude-multi"))
        fallback = {"HOME": "/tmp/home-y"}
        self.assertEqual(
            sessions.state_root(fallback), Path("/tmp/home-y/.local/state/claude-multi")
        )
        self.assertEqual(
            sessions.config_root(fallback), Path("/tmp/home-y/.config/claude-multi")
        )


if __name__ == "__main__":
    unittest.main()
