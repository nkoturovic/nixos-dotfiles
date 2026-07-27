"""Tests for launcher-owned session state."""

from __future__ import annotations

import os
import stat
import tempfile
import threading
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
        self.assertEqual(loaded["managed_id"], FIXED_ID)
        self.assertEqual(loaded["runtime_session_id"], FIXED_ID)
        self.assertEqual(loaded["snapshot"], self.snapshot)
        self.assertEqual(loaded["forked_from"], None)

    def test_v3_record_fields_and_defaults(self) -> None:
        record = _record(self.snapshot)
        self.assertEqual(record["version"], 3)
        self.assertEqual(record["session_type"], sessions.SESSION_TYPE_MANAGED)
        self.assertEqual(record["identity_state"], sessions.IDENTITY_UNVERIFIED)
        self.assertEqual(record["mode"], "legacy")
        self.assertEqual(record["scope_generation"], 0)
        self.assertEqual(record["workflows"], "native")
        self.store.save(record)
        loaded = self.store.load(FIXED_ID)
        self.assertEqual(loaded["mode"], "legacy")
        self.assertEqual(loaded["scope_generation"], 0)
        self.assertEqual(loaded["workflows"], "native")

    def test_v2_durable_fields_roundtrip(self) -> None:
        record = sessions.make_record(
            session_id=FIXED_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            workflows="off",
            now="2026-07-21T00:00:00Z",
        )
        self.store.save(record)
        loaded = self.store.load(FIXED_ID)
        self.assertEqual(loaded["mode"], "durable")
        self.assertEqual(loaded["scope_generation"], 1)
        self.assertEqual(loaded["workflows"], "off")

    def test_v1_record_loads_with_defaults_untouched_on_disk(self) -> None:
        record = _record(self.snapshot)
        record["version"] = 1
        record["session_id"] = record.pop("managed_id")
        for key in (
            "runtime_session_id",
            "runtime_aliases",
            "session_type",
            "identity_state",
            "last_event_source",
            "last_seen_at",
            "pending_forks",
            "launch_epoch",
            "migrated_from_version",
            "mode",
            "scope_generation",
            "workflows",
        ):
            del record[key]
        self.store.save(record)
        on_disk = self.store.read_record_bytes(FIXED_ID)
        loaded = self.store.load(FIXED_ID)
        self.assertEqual(loaded["version"], 3)
        self.assertEqual(loaded["migrated_from_version"], 1)
        self.assertEqual(loaded["mode"], "legacy")
        self.assertEqual(loaded["scope_generation"], 0)
        self.assertEqual(loaded["workflows"], "native")
        # Load never rewrites the record.
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), on_disk)

    def test_v2_record_missing_new_fields_rejected(self) -> None:
        record = _record(self.snapshot)
        record["version"] = 2
        record["session_id"] = record.pop("managed_id")
        for key in (
            "runtime_session_id",
            "runtime_aliases",
            "session_type",
            "identity_state",
            "last_event_source",
            "last_seen_at",
            "pending_forks",
            "launch_epoch",
            "migrated_from_version",
        ):
            del record[key]
        del record["mode"]
        state.atomic_write(
            self.store._record_path(FIXED_ID),
            strict_json.canonical_file_bytes(record),
        )
        with self.assertRaisesRegex(SessionError, "oneOf variant"):
            self.store.load(FIXED_ID)

    def test_record_schema_valid(self) -> None:
        from claude_multi import validate

        record = _record(self.snapshot, forked_from=OTHER_ID)
        self.assertEqual(validate.validate(record, _schema()), [])

    def test_published_schema_rejects_incomplete_or_cross_type_v3_records(self) -> None:
        from claude_multi import validate

        incomplete = {
            "version": 3,
            "cwd": "/project/path",
            "catalog_version": 1,
            "catalog_hash": "sha256:" + "0" * 64,
            "launcher_version": "2.2.0",
            "created_at": "2026-07-21T00:00:00Z",
            "forked_from": None,
        }
        self.assertTrue(validate.validate(incomplete, _schema()))
        ordinary = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            model="qwen38",
            context_profile="large",
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
        )
        ordinary["composition_name"] = "default"
        self.assertTrue(validate.validate(ordinary, _schema()))

    def test_corrupt_record_reported(self) -> None:
        path = self.store._record_path(FIXED_ID)
        state.atomic_write(path, b"{not json")
        with self.assertRaisesRegex(SessionError, "corrupt session record"):
            self.store.load(FIXED_ID)

    def test_stale_record_missing(self) -> None:
        with self.assertRaises(SessionError):
            self.store.load(FIXED_ID)

    def test_embedded_session_id_must_match_record_path(self) -> None:
        path = self.store.sessions_dir / f"{FIXED_ID}.json"
        state.atomic_write(
            path,
            strict_json.canonical_file_bytes(
                _record(self.snapshot, session_id=OTHER_ID)
            ),
        )
        with self.assertRaisesRegex(SessionError, "does not match its record path"):
            self.store.load(FIXED_ID)

    def test_schema_violating_record_rejected_on_save(self) -> None:
        record = _record(self.snapshot)
        record["snapshot"]["scalar_context_tokens"] = "not-a-number"
        with self.assertRaises(SessionError):
            self.store.save(record)

    def test_invalid_mode_rejected_on_save(self) -> None:
        record = _record(self.snapshot)
        record["mode"] = "ephemeral"
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


class RecordBytesTests(SessionTestCase):
    def test_read_record_bytes_absent(self) -> None:
        self.assertIsNone(self.store.read_record_bytes(FIXED_ID))

    def test_read_restore_roundtrip_exact_bytes(self) -> None:
        self.store.save(_record(self.snapshot))
        original = self.store.read_record_bytes(FIXED_ID)
        self.assertIsNotNone(original)
        replacement = sessions.make_record(
            session_id=FIXED_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=2,
            now="2026-07-22T00:00:00Z",
        )
        self.store.save(replacement)
        self.assertNotEqual(self.store.read_record_bytes(FIXED_ID), original)
        self.store.restore_record_bytes(FIXED_ID, original)
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), original)
        restored = self.store.load(FIXED_ID)
        self.assertEqual(restored["mode"], "legacy")

    def test_record_path_helpers_reject_non_uuid(self) -> None:
        with self.assertRaises(SessionError):
            self.store.exists("../escape")
        with self.assertRaises(SessionError):
            self.store.read_record_bytes("../escape")
        with self.assertRaises(SessionError):
            self.store.restore_record_bytes("../escape", b"{}\n")


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

    def test_link_rechecks_existence_under_the_lifecycle_lock(self) -> None:
        # L1: the exists-recheck (and therefore the check-then-save pair) runs
        # after the session's lifecycle lock is acquired.
        acquisitions: list[bool] = []
        real_exists = self.store.exists

        def spying_exists(session_id: str) -> bool:
            probe = self.store.lifecycle_lock(session_id)
            acquired = probe.acquire(blocking=False)
            if acquired:
                probe.release()
            acquisitions.append(acquired)
            return real_exists(session_id)

        with mock.patch.object(self.store, "exists", spying_exists):
            self.store.link(_record(self.snapshot, session_id=OTHER_ID))
        # A competing non-blocking acquire on the same lock file had to fail,
        # proving exists ran while link held the lifecycle lock.
        self.assertTrue(acquisitions)
        self.assertTrue(all(acquired is False for acquired in acquisitions))
        self.assertTrue(self.store.exists(OTHER_ID))

    def test_link_waits_for_a_held_lifecycle_lock(self) -> None:
        # L1: a concurrent holder of the lifecycle lock serializes link.
        lock = self.store.lifecycle_lock(OTHER_ID)
        self.assertTrue(lock.acquire(blocking=False))
        done: list[bool] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                self.store.link(_record(self.snapshot, session_id=OTHER_ID))
                done.append(True)
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=0.5)
        try:
            self.assertTrue(thread.is_alive())
            self.assertEqual(done, [])
            self.assertFalse(self.store.exists(OTHER_ID))
        finally:
            lock.release()
            thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]
        self.assertEqual(done, [True])
        self.assertTrue(self.store.exists(OTHER_ID))

    def test_link_waits_for_pointer_and_commits_record_with_pointer(self) -> None:
        record = _record(self.snapshot, session_id=OTHER_ID)
        pointer = self.store._pointer_path(record["cwd"], record["session_type"])
        pointer_lock = state.FileLock(pointer)
        self.assertTrue(pointer_lock.acquire(blocking=False))
        done: list[Path] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                done.append(self.store.link(record))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=0.2)
        try:
            self.assertTrue(thread.is_alive())
            self.assertFalse(self.store.exists(OTHER_ID))
        finally:
            pointer_lock.release()
            thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]
        self.assertEqual(len(done), 1)
        self.assertTrue(self.store.exists(OTHER_ID))
        self.assertEqual(self.store.last(record["cwd"]), OTHER_ID)


class IdentityReconciliationTests(SessionTestCase):
    def test_runtime_drift_becomes_authoritative_and_old_id_is_alias(self) -> None:
        self.store.save(_record(self.snapshot))
        updated = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="compact",
            cwd="/project/path",
            now="2026-07-22T00:00:00Z",
        )
        self.assertEqual(updated["managed_id"], FIXED_ID)
        self.assertEqual(updated["runtime_session_id"], OTHER_ID)
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_AUTHORITATIVE)
        self.assertEqual(
            [item["session_id"] for item in updated["runtime_aliases"]],
            [FIXED_ID],
        )
        self.assertEqual(self.store.resolve(OTHER_ID)["managed_id"], FIXED_ID)
        self.assertEqual(self.store.resolve(FIXED_ID)["managed_id"], FIXED_ID)

    def test_repeated_start_is_idempotent(self) -> None:
        self.store.save(_record(self.snapshot))
        first = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="resume",
            cwd="/project/path",
            now="2026-07-22T00:00:00Z",
        )
        second = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="resume",
            cwd="/project/path",
            now="2026-07-22T00:00:01Z",
        )
        self.assertEqual(first["runtime_aliases"], second["runtime_aliases"])
        self.assertEqual(second["runtime_session_id"], OTHER_ID)

    def test_managed_fork_does_not_replace_parent_runtime(self) -> None:
        self.store.save(_record(self.snapshot))
        updated = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd="/project/path",
            now="2026-07-22T00:00:00Z",
        )
        self.assertEqual(updated["runtime_session_id"], FIXED_ID)
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_PENDING_FORK)
        self.assertEqual(updated["pending_forks"][0]["session_id"], OTHER_ID)

    def test_cwd_mismatch_marks_repair_needed_without_overwriting_origin(self) -> None:
        self.store.save(_record(self.snapshot))
        updated = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            source="startup",
            cwd="/wrong/project",
            now="2026-07-22T00:00:00Z",
        )
        self.assertEqual(updated["cwd"], "/project/path")
        self.assertEqual(updated["observed_cwd"], "/wrong/project")
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_REPAIR_NEEDED)

    def test_session_end_is_advisory_and_never_retargets_resume(self) -> None:
        self.store.save(_record(self.snapshot))
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="resume",
            cwd="/project/path",
            now="2026-07-22T00:00:00Z",
        )
        ended = self.store.record_session_end(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            reason="other",
            now="2026-07-22T00:00:01Z",
        )
        self.assertEqual(ended["runtime_session_id"], OTHER_ID)
        self.assertEqual(ended["last_end_reason"], "other")
        self.assertEqual(ended["identity_state"], sessions.IDENTITY_AUTHORITATIVE)

    def test_delayed_unseen_start_with_older_epoch_cannot_retarget_authority(self) -> None:
        delayed_id = "33333333-3333-4333-8333-333333333333"
        record = _record(self.snapshot)
        record["launch_epoch"] = 1
        self.store.save(record)
        newest = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="resume",
            cwd="/project/path",
            launch_epoch=3,
            now="2026-07-22T00:00:00Z",
        )
        delayed = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=delayed_id,
            source="resume",
            cwd="/project/path",
            launch_epoch=2,
            now="2026-07-22T00:00:01Z",
        )
        self.assertEqual(delayed, newest)
        self.assertEqual(delayed["runtime_session_id"], OTHER_ID)
        self.assertEqual(delayed["launch_epoch"], 3)
        later = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=delayed_id,
            source="resume",
            cwd="/project/path",
            launch_epoch=4,
            now="2026-07-22T00:00:02Z",
        )
        self.assertEqual(later["runtime_session_id"], delayed_id)
        self.assertEqual(later["launch_epoch"], 4)

    def test_delayed_start_from_runtime_alias_cannot_retarget_authority(self) -> None:
        self.store.save(_record(self.snapshot))
        current = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="resume",
            cwd="/project/path",
            now="2026-07-22T00:00:00Z",
        )
        delayed = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            source="compact",
            cwd="/wrong/project",
            model="gpt-multi-sol-high",
            observed_model="gpt-multi-sol-high",
            now="2026-07-22T00:00:01Z",
        )
        self.assertEqual(delayed, current)
        self.assertEqual(delayed["runtime_session_id"], OTHER_ID)
        self.assertNotIn("observed_cwd", delayed)
        self.assertNotIn("observed_model", delayed)

    def test_carry_lifecycle_state_overlays_current_without_mutation(self) -> None:
        target = _record(self.snapshot)
        target["scope_generation"] = 2
        target["runtime_session_id"] = FIXED_ID
        target["runtime_aliases"] = [{"session_id": OTHER_ID}]
        target["observed_model"] = "stale"
        current = _record(self.snapshot)
        current.update(
            {
                "runtime_session_id": OTHER_ID,
                "runtime_aliases": [
                    {
                        "session_id": FIXED_ID,
                        "source": "compact",
                        "observed_at": "2026-07-22T00:00:00Z",
                    }
                ],
                "identity_state": sessions.IDENTITY_REPAIR_NEEDED,
                "last_event_source": "compact",
                "last_seen_at": "2026-07-22T00:00:00Z",
                "pending_forks": [],
                "observed_cwd": "/wrong/project",
                "last_end_reason": "other",
            }
        )
        target_before = strict_json.canonical_bytes(target)
        current_before = strict_json.canonical_bytes(current)
        merged = sessions.carry_lifecycle_state(target, current)
        self.assertEqual(merged["scope_generation"], 2)
        for key in sessions._LIFECYCLE_FIELDS:
            self.assertEqual(merged.get(key), current.get(key))
        self.assertNotIn("observed_model", merged)
        merged["runtime_aliases"][0]["source"] = "changed"
        self.assertEqual(current["runtime_aliases"][0]["source"], "compact")
        self.assertEqual(strict_json.canonical_bytes(target), target_before)
        self.assertEqual(strict_json.canonical_bytes(current), current_before)

    def test_pending_fork_survives_later_parent_start(self) -> None:
        self.store.save(_record(self.snapshot))
        first = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd="/project/path",
            now="2026-07-22T00:00:00Z",
        )
        second = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            source="resume",
            cwd="/project/path",
            now="2026-07-22T00:00:01Z",
        )
        self.assertEqual(first["pending_forks"], second["pending_forks"])
        self.assertEqual(second["identity_state"], sessions.IDENTITY_PENDING_FORK)

    def test_fork_does_not_downgrade_existing_repair_state(self) -> None:
        record = _record(self.snapshot)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project"
        self.store.save(record)
        updated = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd="/wrong/project",
            now="2026-07-22T00:00:00Z",
        )
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_REPAIR_NEEDED)
        self.assertEqual(updated["pending_forks"][0]["session_id"], OTHER_ID)

    def test_linking_fork_clears_parent_pending_state(self) -> None:
        adopted_id = "33333333-3333-4333-8333-333333333333"
        self.store.save(_record(self.snapshot))
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd="/project/path",
            now="2026-07-22T00:00:00Z",
        )
        adopted = sessions.make_record(
            managed_id=adopted_id,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
            now="2026-07-22T00:00:01Z",
        )
        self.store.link(adopted)
        parent = self.store.load(FIXED_ID)
        self.assertEqual(parent["pending_forks"], [])
        self.assertEqual(parent["identity_state"], sessions.IDENTITY_AUTHORITATIVE)

    def test_link_rolls_back_parent_if_adopted_record_save_fails(self) -> None:
        adopted_id = "33333333-3333-4333-8333-333333333333"
        self.store.save(_record(self.snapshot))
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd="/project/path",
        )
        parent_before = self.store.read_record_bytes(FIXED_ID)
        adopted = sessions.make_record(
            managed_id=adopted_id,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )
        real_save = self.store.save

        def fail_adopted(record):
            if sessions.managed_id(record) == adopted_id:
                raise OSError("injected adopted-record failure")
            return real_save(record)

        with mock.patch.object(self.store, "save", side_effect=fail_adopted):
            with self.assertRaisesRegex(OSError, "injected"):
                self.store.link(adopted)
        self.assertFalse(self.store.exists(adopted_id))
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), parent_before)

    def test_stale_fork_event_after_adoption_is_ignored(self) -> None:
        adopted_id = "33333333-3333-4333-8333-333333333333"
        parent = _record(self.snapshot)
        parent["identity_state"] = sessions.IDENTITY_AUTHORITATIVE
        self.store.save(parent)
        adopted = sessions.make_record(
            managed_id=adopted_id,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )
        self.store.link(adopted)
        replayed = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd="/project/path",
        )
        self.assertEqual(replayed["pending_forks"], [])
        self.assertEqual(replayed["identity_state"], sessions.IDENTITY_AUTHORITATIVE)
        self.assertEqual(self.store.resolve(OTHER_ID)["managed_id"], adopted_id)

    def test_link_fails_closed_on_unparseable_record_without_partial_adoption(self) -> None:
        adopted_id = "33333333-3333-4333-8333-333333333333"
        corrupt_id = "44444444-4444-4444-8444-444444444444"
        parent = _record(self.snapshot)
        parent["identity_state"] = sessions.IDENTITY_AUTHORITATIVE
        self.store.save(parent)
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd="/project/path",
        )
        state.atomic_write(self.store._record_path(corrupt_id), b"{broken")
        adopted = sessions.make_record(
            managed_id=adopted_id,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )
        with self.assertRaisesRegex(SessionError, "cannot determine runtime ownership"):
            self.store.link(adopted)
        self.assertFalse(self.store.exists(adopted_id))
        self.assertEqual(
            self.store.load(FIXED_ID)["pending_forks"][0]["session_id"], OTHER_ID
        )

    def test_link_refuses_runtime_owned_by_schema_invalid_record(self) -> None:
        adopted_id = "33333333-3333-4333-8333-333333333333"
        owner = sessions.make_record(
            managed_id=FIXED_ID,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )
        self.store.save(owner)
        invalid_owner = {**owner, "identity_state": "invalid-state"}
        state.atomic_write(
            self.store._record_path(FIXED_ID),
            strict_json.canonical_file_bytes(invalid_owner),
        )
        adopted = sessions.make_record(
            managed_id=adopted_id,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )

        with self.assertRaisesRegex(
            SessionError, "unreadable session record .* may claim runtime session"
        ):
            self.store.link(adopted)
        self.assertFalse(self.store.exists(adopted_id))

        state.atomic_write(
            self.store._record_path(FIXED_ID),
            strict_json.canonical_file_bytes(owner),
        )
        self.assertEqual(self.store.resolve(OTHER_ID)["managed_id"], FIXED_ID)

    def test_link_refuses_unicode_escaped_owner_in_duplicate_key_record(self) -> None:
        adopted_id = "33333333-3333-4333-8333-333333333333"
        corrupt_id = "44444444-4444-4444-8444-444444444444"
        owner = sessions.make_record(
            managed_id=corrupt_id,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )
        raw = strict_json.canonical_file_bytes(owner).decode("utf-8")
        needle = f'"runtime_session_id":"{OTHER_ID}"'
        escaped = OTHER_ID.replace("2", "\\" + "u0032")
        malformed = raw.replace(
            needle,
            f'"runtime_session_id":"{FIXED_ID}",'
            f'"runtime_session_id":"{escaped}"',
            1,
        ).encode("utf-8")
        self.assertNotIn(OTHER_ID.encode("ascii"), malformed)
        state.atomic_write(self.store._record_path(corrupt_id), malformed)
        adopted = sessions.make_record(
            managed_id=adopted_id,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )

        with self.assertRaisesRegex(
            SessionError, "unreadable session record .* may claim runtime session"
        ):
            self.store.link(adopted)
        self.assertFalse(self.store.exists(adopted_id))

    def test_pending_fork_end_remains_advisory_and_adoption_heals_parent(self) -> None:
        adopted_id = "33333333-3333-4333-8333-333333333333"
        parent = _record(self.snapshot)
        parent["identity_state"] = sessions.IDENTITY_AUTHORITATIVE
        self.store.save(parent)
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd="/project/path",
        )
        ended = self.store.record_session_end(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            reason="other",
        )
        self.assertEqual(ended["identity_state"], sessions.IDENTITY_PENDING_FORK)
        adopted = sessions.make_record(
            managed_id=adopted_id,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )
        self.store.link(adopted)
        healed = self.store.load(FIXED_ID)
        self.assertEqual(healed["pending_forks"], [])
        self.assertEqual(healed["identity_state"], sessions.IDENTITY_AUTHORITATIVE)
        self.assertEqual(healed["runtime_session_id"], FIXED_ID)

    def test_forget_session_waits_for_lifecycle_lock(self) -> None:
        self.store.save(_record(self.snapshot))
        lock = self.store.lifecycle_lock(FIXED_ID)
        self.assertTrue(lock.acquire(blocking=False))
        done: list[tuple[bool, bool]] = []
        thread = threading.Thread(
            target=lambda: done.append(self.store.forget_session(FIXED_ID))
        )
        thread.start()
        thread.join(timeout=0.2)
        self.assertTrue(thread.is_alive())
        self.assertTrue(self.store.exists(FIXED_ID))
        lock.release()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(done[0][0], True)
        self.assertFalse(self.store.exists(FIXED_ID))

    def test_forget_scope_failure_preserves_record_authority(self) -> None:
        from claude_multi import scope

        self.store.save(_record(self.snapshot))
        live = scope.scope_dir(self.store.root, FIXED_ID)
        outside = self.root / "outside"
        outside.mkdir()
        live.parent.mkdir(parents=True, exist_ok=True)
        live.symlink_to(outside)
        with self.assertRaises(state.StateError):
            self.store.forget_session(FIXED_ID)
        self.assertTrue(self.store.exists(FIXED_ID))
        self.assertTrue(live.is_symlink())

    def test_forget_waits_for_contended_pointer_cleanup(self) -> None:
        record = _record(self.snapshot)
        self.store.save(record)
        self.store.update_last(record["cwd"], FIXED_ID)
        pointer = self.store._pointer_path(record["cwd"])
        pointer_lock = state.FileLock(pointer)
        self.assertTrue(pointer_lock.acquire(blocking=False))
        done: list[tuple[bool, bool]] = []
        thread = threading.Thread(
            target=lambda: done.append(self.store.forget_session(FIXED_ID))
        )
        thread.start()
        thread.join(timeout=0.2)
        self.assertTrue(thread.is_alive())
        self.assertTrue(self.store.exists(FIXED_ID))
        pointer_lock.release()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(done[0][0], True)
        self.assertIsNone(self.store.last(record["cwd"]))

    def test_forget_removes_transition_previous_scope(self) -> None:
        record = _record(self.snapshot)
        self.store.save(record)
        scopes_root = state.ensure_private_dir(self.store.root / "scopes")
        previous = scopes_root / f".{FIXED_ID}.prev"
        state.ensure_private_dir(previous)
        state.atomic_write(previous / "settings.json", b"{}\n")
        removed, scope_removed = self.store.forget_session(FIXED_ID)
        self.assertTrue(removed)
        self.assertTrue(scope_removed)
        self.assertFalse(previous.exists())
        self.assertFalse(self.store.exists(FIXED_ID))

    def test_failed_link_releases_runtime_index_lock(self) -> None:
        self.store.save(_record(self.snapshot))
        with self.assertRaises(SessionError):
            self.store.link(_record(self.snapshot))
        lock = self.store.runtime_index_lock()
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    def test_ordinary_record_has_no_composition_payload(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            model="qwen38",
            context_profile="large",
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            now="2026-07-22T00:00:00Z",
        )
        self.store.save(record)
        loaded = self.store.load(FIXED_ID)
        self.assertEqual(loaded["session_type"], sessions.SESSION_TYPE_ORDINARY)
        self.assertEqual(loaded["runtime_session_id"], OTHER_ID)
        self.assertNotIn("composition_name", loaded)
        self.assertNotIn("snapshot", loaded)


class PendingForkResolutionTests(SessionTestCase):
    """The 2026-07-27 incident lifecycle: a native fork is observed, authority
    later lands ON the fork (supervisor relaunch) — the stale pending marker
    must self-clear; genuinely pending forks keep blocking until resolve-fork.
    """

    THIRD_ID = "33333333-3333-4333-8333-333333333333"

    def _forked_record(self):
        self.store.save(_record(self.snapshot))
        forked = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd="/project/path",
            now="2026-07-22T00:00:00Z",
        )
        self.assertEqual(forked["identity_state"], sessions.IDENTITY_PENDING_FORK)
        self.assertEqual(forked["runtime_session_id"], FIXED_ID)
        return forked

    def _stuck_record(self):
        """The exact pre-fix state: authority on the fork, marker still pending."""

        record = {
            **_record(self.snapshot),
            "runtime_session_id": OTHER_ID,
            "runtime_aliases": [
                {
                    "session_id": FIXED_ID,
                    "source": "fork",
                    "observed_at": "2026-07-22T00:01:00Z",
                }
            ],
            "pending_forks": [
                {"session_id": OTHER_ID, "observed_at": "2026-07-22T00:00:00Z"}
            ],
            "identity_state": sessions.IDENTITY_PENDING_FORK,
        }
        self.store.save(record)
        return record

    def test_authority_landing_on_pending_fork_auto_resolves(self) -> None:
        self._forked_record()
        updated = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="resume",
            cwd="/project/path",
            now="2026-07-22T00:05:00Z",
        )
        self.assertEqual(updated["runtime_session_id"], OTHER_ID)
        self.assertEqual(updated["pending_forks"], [])
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_AUTHORITATIVE)
        self.assertEqual(
            [item["session_id"] for item in updated["runtime_aliases"]],
            [FIXED_ID],
        )

    def test_authority_elsewhere_keeps_pending_fork_blocking(self) -> None:
        self._forked_record()
        updated = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=self.THIRD_ID,
            source="resume",
            cwd="/project/path",
            now="2026-07-22T00:05:00Z",
        )
        self.assertEqual(updated["runtime_session_id"], self.THIRD_ID)
        self.assertEqual(
            [item["session_id"] for item in updated["pending_forks"]],
            [OTHER_ID],
        )
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_PENDING_FORK)

    def test_converge_pending_forks_clears_authority_holder(self) -> None:
        self._stuck_record()
        self.assertTrue(self.store.converge_pending_forks(FIXED_ID))
        updated = self.store.load(FIXED_ID)
        self.assertEqual(updated["pending_forks"], [])
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_AUTHORITATIVE)
        self.assertEqual(updated["runtime_session_id"], OTHER_ID)
        self.assertFalse(self.store.converge_pending_forks(FIXED_ID))

    def test_converge_pending_forks_keeps_genuine_pending(self) -> None:
        self._forked_record()
        self.assertFalse(self.store.converge_pending_forks(FIXED_ID))
        self.assertEqual(
            self.store.load(FIXED_ID)["identity_state"],
            sessions.IDENTITY_PENDING_FORK,
        )

    def test_converge_preserves_repair_needed(self) -> None:
        stuck = self._stuck_record()
        stuck = {**stuck, "identity_state": sessions.IDENTITY_REPAIR_NEEDED,
                 "observed_model": "some-model"}
        self.store.save(stuck)
        self.assertTrue(self.store.converge_pending_forks(FIXED_ID))
        updated = self.store.load(FIXED_ID)
        self.assertEqual(updated["pending_forks"], [])
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_REPAIR_NEEDED)

    def test_resolve_fork_discards_marker_and_keeps_parent(self) -> None:
        self._forked_record()
        updated = self.store.resolve_fork(FIXED_ID, OTHER_ID)
        self.assertEqual(updated["pending_forks"], [])
        self.assertEqual(updated["runtime_session_id"], FIXED_ID)
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_AUTHORITATIVE)

    def test_resolve_fork_refuses_authority_holder(self) -> None:
        self._stuck_record()
        with self.assertRaisesRegex(sessions.SessionError, "resume authority"):
            self.store.resolve_fork(FIXED_ID, OTHER_ID)

    def test_resolve_fork_refuses_unknown_and_bad_uuid(self) -> None:
        self._forked_record()
        with self.assertRaisesRegex(sessions.SessionError, "no pending fork"):
            self.store.resolve_fork(FIXED_ID, self.THIRD_ID)
        with self.assertRaisesRegex(sessions.SessionError, "UUIDv4"):
            self.store.resolve_fork(FIXED_ID, "not-a-uuid")

    def test_pending_fork_message_names_fork_and_remedies(self) -> None:
        record = self._forked_record()
        message = sessions.pending_fork_message(record)
        self.assertIn(FIXED_ID, message)
        self.assertIn(OTHER_ID, message)
        self.assertIn("sessions link", message)
        self.assertIn("resolve-fork", message)
        self.assertIn("default", message)  # recorded composition suggested
        self.assertIn("transcript is kept", message)

    def test_pending_fork_message_ordinary_uses_model_flag(self) -> None:
        ordinary = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd="/project/path",
            model="sol",
            context_profile="sol",
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.5.0",
            now="2026-07-21T00:00:00Z",
        )
        ordinary = {
            **ordinary,
            "pending_forks": [
                {"session_id": OTHER_ID, "observed_at": "2026-07-22T00:00:00Z"}
            ],
        }
        message = sessions.pending_fork_message(ordinary)
        self.assertIn("--model", message)
        self.assertIn(OTHER_ID, message)


class PointerTests(SessionTestCase):
    def test_update_and_last_roundtrip(self) -> None:
        self.assertTrue(self.store.update_last("/project/path", FIXED_ID))
        self.assertEqual(self.store.last("/project/path"), FIXED_ID)

    def test_managed_and_ordinary_pointers_are_independent(self) -> None:
        self.store.update_last("/project/path", FIXED_ID)
        self.store.update_last(
            "/project/path",
            OTHER_ID,
            session_type=sessions.SESSION_TYPE_ORDINARY,
        )
        self.assertEqual(self.store.last("/project/path"), FIXED_ID)
        self.assertEqual(
            self.store.last(
                "/project/path", session_type=sessions.SESSION_TYPE_ORDINARY
            ),
            OTHER_ID,
        )

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

    def test_pointer_with_non_uuid_session_is_treated_as_missing(self) -> None:
        pointer = self.store._pointer_path("/project/path")
        state.atomic_write(
            pointer,
            strict_json.canonical_file_bytes(
                {"cwd": "/project/path", "session_id": "../escape"}
            ),
        )
        self.assertIsNone(self.store.last("/project/path"))

    def test_pointer_updates_reject_non_uuid(self) -> None:
        with self.assertRaises(SessionError):
            self.store.update_last("/project/path", "../escape")
        with self.assertRaises(SessionError):
            self.store.clear_last("/project/path", "../escape")

    def test_compare_and_restore_pointer_bytes(self) -> None:
        self.store.update_last("/project/path", OTHER_ID)
        prior = self.store.read_pointer_bytes("/project/path")
        self.store.update_last("/project/path", FIXED_ID)
        self.assertTrue(
            self.store.restore_pointer_bytes("/project/path", FIXED_ID, prior)
        )
        self.assertEqual(self.store.last("/project/path"), OTHER_ID)
        self.assertEqual(self.store.read_pointer_bytes("/project/path"), prior)

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


class ForkCredentialRevocationTests(SessionTestCase):
    """H9: adopt/discard bumps the parent's epoch, staling the fork's hooks."""

    def _forked(self, epoch: int = 3):
        record = {
            **_record(self.snapshot),
            "launch_epoch": epoch,
        }
        self.store.save(record)
        return self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd="/project/path",
            launch_epoch=epoch,
            now="2026-07-22T00:00:00Z",
        )

    def test_resolve_fork_bumps_epoch_and_stales_the_fork_hooks(self) -> None:
        self._forked(epoch=3)
        updated = self.store.resolve_fork(FIXED_ID, OTHER_ID)
        self.assertEqual(updated["launch_epoch"], 4)
        # The fork's next id-changing hook (its baked epoch is 3) is ignored.
        after = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=self.store.new_id(),
            source="compact",
            cwd="/project/path",
            launch_epoch=3,
            now="2026-07-22T00:10:00Z",
        )
        self.assertEqual(after["runtime_session_id"], FIXED_ID)
        self.assertEqual(after["launch_epoch"], 4)

    def test_link_adoption_bumps_parent_epoch(self) -> None:
        forked = self._forked(epoch=2)
        linked = sessions.make_record(
            managed_id=self.store.new_id(),
            runtime_session_id=OTHER_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.6.1",
            now="2026-07-22T00:05:00Z",
        )
        self.store.link(linked)
        parent = self.store.load(FIXED_ID)
        self.assertEqual(parent["pending_forks"], [])
        self.assertEqual(parent["launch_epoch"], 3)


class PendingForkCapTests(SessionTestCase):
    """H19: the 17th distinct fork fails the hook visibly; nothing evicted."""

    def test_cap_refuses_new_fork_without_evicting(self) -> None:
        record = _record(self.snapshot)
        record["pending_forks"] = [
            {
                "session_id": f"00000000-0000-4000-8000-{index:012d}",
                "observed_at": "2026-07-22T00:00:00Z",
            }
            for index in range(16)
        ]
        record["identity_state"] = sessions.IDENTITY_PENDING_FORK
        self.store.save(record)
        with self.assertRaisesRegex(sessions.SessionError, "16 unresolved"):
            self.store.reconcile_runtime(
                FIXED_ID,
                observed_runtime_id=OTHER_ID,
                source="fork",
                cwd="/project/path",
                now="2026-07-22T00:05:00Z",
            )
        after = self.store.load(FIXED_ID)
        self.assertEqual(len(after["pending_forks"]), 16)
        # Re-observing an already-tracked fork is still fine (idempotent).
        again = self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id="00000000-0000-4000-8000-000000000005",
            source="fork",
            cwd="/project/path",
            now="2026-07-22T00:06:00Z",
        )
        self.assertEqual(len(again["pending_forks"]), 16)

    def test_multi_fork_message_ordinary_uses_model_flag(self) -> None:
        ordinary = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd="/project/path",
            model="sol",
            context_profile="sol",
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.6.1",
            now="2026-07-21T00:00:00Z",
        )
        ordinary["pending_forks"] = [
            {"session_id": OTHER_ID, "observed_at": "2026-07-22T00:00:00Z"},
            {"session_id": "33333333-3333-4333-8333-333333333333",
             "observed_at": "2026-07-22T00:01:00Z"},
        ]
        message = sessions.pending_fork_message(ordinary)
        self.assertIn("--model MODEL", message)
        self.assertNotIn("--composition", message)
