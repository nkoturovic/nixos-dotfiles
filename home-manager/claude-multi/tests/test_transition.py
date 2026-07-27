"""Tests for composition transitions: diff, prepare, execute, restore, converge."""

from __future__ import annotations

import copy
import dataclasses
import errno
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_multi import catalog, compiler, composition, scope, sessions, state, strict_json, transition
from claude_multi.transition import TransitionError


CATALOG_ROOT = Path(__file__).resolve().parents[1]
FIXED_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


def _lead_to_kimi(document: dict) -> None:
    for slot in document["slots"]:
        if slot["role"] == "cm-lead":
            slot["model"] = "kimi-k3"


def _workflows_off(document: dict) -> None:
    document["workflows"] = "off"


def _explore_native(document: dict) -> None:
    document["native_agents"]["explore"] = "native"


def _add_analyst(document: dict) -> None:
    document["slots"].append(
        {"role": "cm-analyst", "model": "sol", "lane": "xhigh", "preferred": False}
    )


def _remove_kimi_analyst(document: dict) -> None:
    document["slots"] = [
        slot
        for slot in document["slots"]
        if not (slot["role"] == "cm-analyst" and slot["model"] == "kimi-k3")
    ]


class TransitionTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-transition-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.bundle = catalog.load_catalog(CATALOG_ROOT)
        self.store = sessions.SessionStore(
            self.root / "state",
            strict_json.load(CATALOG_ROOT / "schemas" / "session.schema.json"),
        )
        self.project = self.root / "project"
        self.project.mkdir()
        os.chmod(self.project, 0o700)
        self.default_resolved = composition.resolve(
            self.bundle.docs, self.bundle.default_composition
        )
        self.meta = transition.catalog_meta_from_catalog(self.bundle)

    # Fixture helpers -----------------------------------------------------

    def _document(self, *mutations) -> dict:
        document = copy.deepcopy(self.bundle.default_composition)
        for mutate in mutations:
            mutate(document)
        return document

    def _compile_scope(self, resolved, launch_epoch: int = 0) -> scope.ScopePlan:
        return scope.compile_scope(
            resolved,
            self.bundle.docs["roles"]["roles"],
            self.bundle.prompt_bodies,
            scope.catalog_meta_from_docs(self.bundle.docs),
            managed_id=FIXED_ID,
            hook_command=str(scope.hook_shim_path(self.root / "state")),
            launch_epoch=launch_epoch,
            token_helper_command=str(
                scope.gateway_token_shim_path(self.root / "state")
            ),
        )

    def _make_session(
        self,
        *mutations,
        session_id: str = FIXED_ID,
        generation: int = 1,
        catalog_hash: str | None = None,
        write_live_scope: bool = True,
    ):
        """Persist a durable record at `generation` plus its live scope."""

        document = self._document(*mutations)
        resolved = composition.resolve(self.bundle.docs, document)
        snapshot = composition.snapshot(resolved)
        record = sessions.make_record(
            session_id=session_id,
            cwd=str(self.project),
            composition_name=document["name"],
            snapshot=snapshot,
            catalog_version=self.bundle.docs["version"]["catalog_version"],
            catalog_hash=catalog_hash or self.bundle.bundle_sha256,
            launcher_version=self.bundle.docs["version"]["launcher_version"],
            mode="durable",
            scope_generation=generation,
            workflows=resolved.workflows,
            now="2026-07-21T00:00:00Z",
        )
        self.store.save(record)
        plan = self._compile_scope(resolved, record["launch_epoch"])
        if write_live_scope:
            scope.write_scope(self.store.root, session_id, plan)
        return record, resolved, plan

    def _scopes_root(self) -> Path:
        return self.store.root / "scopes"

    def _live(self, session_id: str = FIXED_ID) -> Path:
        return self._scopes_root() / session_id

    def _prev(self, session_id: str = FIXED_ID) -> Path:
        return self._scopes_root() / f".{session_id}.prev"

    def _staging(self, session_id: str = FIXED_ID) -> Path:
        return self._scopes_root() / f".{session_id}.new"

    def _live_file_bytes(self, relpath: str, session_id: str = FIXED_ID) -> bytes:
        return self._live(session_id).joinpath(*relpath.split("/")).read_bytes()

    def _prepare(self, *mutations, session_id: str = FIXED_ID) -> transition.Plan:
        return transition.prepare(
            self.store, session_id, self._document(*mutations), self.bundle
        )


class BuildDiffTests(TransitionTestCase):
    def test_noop_transition_reports_no_semantic_changes(self) -> None:
        record, resolved, _ = self._make_session()
        diff = transition.build_diff(record, resolved, self.meta)
        self.assertEqual(diff, ["no semantic composition changes"])

    def test_variant_added(self) -> None:
        record, _, _ = self._make_session()
        target = composition.resolve(self.bundle.docs, self._document(_add_analyst))
        diff = transition.build_diff(record, target, self.meta)
        self.assertEqual(
            diff,
            [
                "variant added: cm-analyst-sol-xhigh (role cm-analyst, "
                "model sol, lane xhigh, selector gpt-multi-sol-xhigh)"
            ],
        )

    def test_variant_removed(self) -> None:
        record, _, _ = self._make_session()
        target = composition.resolve(
            self.bundle.docs, self._document(_remove_kimi_analyst)
        )
        diff = transition.build_diff(record, target, self.meta)
        self.assertEqual(
            diff,
            [
                "variant removed: cm-analyst-kimi-k3-max (role cm-analyst, "
                "model kimi-k3, lane max, selector claude-multi-kimi-k3[1m])"
            ],
        )

    def test_variant_changed_with_per_field_detail(self) -> None:
        record, resolved, _ = self._make_session()
        mutated = copy.deepcopy(record)
        for variant in mutated["snapshot"]["variants"]:
            if variant["id"] == "cm-analyst-sol-high":
                variant["client_selector"] = "gpt-multi-sol-high-v2"
                variant["preferred"] = False
        diff = transition.build_diff(mutated, resolved, self.meta)
        self.assertEqual(
            diff,
            [
                "variant changed: cm-analyst-sol-high: client_selector "
                "'gpt-multi-sol-high-v2' -> 'gpt-multi-sol-high'; "
                "preferred False -> True"
            ],
        )

    def test_lead_model_change(self) -> None:
        record, _, _ = self._make_session()
        target = composition.resolve(self.bundle.docs, self._document(_lead_to_kimi))
        diff = transition.build_diff(record, target, self.meta)
        self.assertEqual(
            diff,
            [
                "lead model: opus5 -> kimi-k3",
                "lead client_selector: claude-multi-opus-5[1m] -> claude-multi-kimi-k3[1m]",
            ],
        )

    def test_workflows_native_to_off(self) -> None:
        record, _, _ = self._make_session()
        target = composition.resolve(self.bundle.docs, self._document(_workflows_off))
        diff = transition.build_diff(record, target, self.meta)
        self.assertIn("workflows: native -> off", diff)
        # D8: ultracode maps to xhigh with native workflows off.
        self.assertIn("lead effort: ultracode -> xhigh", diff)

    def test_workflows_off_to_native(self) -> None:
        record, _, _ = self._make_session(_workflows_off)
        diff = transition.build_diff(record, self.default_resolved, self.meta)
        self.assertIn("workflows: off -> native", diff)
        self.assertIn("lead effort: xhigh -> ultracode", diff)

    def test_deny_removed(self) -> None:
        record, _, _ = self._make_session()
        target = composition.resolve(self.bundle.docs, self._document(_explore_native))
        diff = transition.build_diff(record, target, self.meta)
        self.assertEqual(diff, ["permission deny removed: Agent(Explore)"])

    def test_deny_added(self) -> None:
        record, _, _ = self._make_session(_explore_native)
        diff = transition.build_diff(record, self.default_resolved, self.meta)
        self.assertEqual(diff, ["permission deny added: Agent(Explore)"])

    def test_catalog_drift_line_only_on_hash_mismatch(self) -> None:
        drifted = "sha256:" + "0" * 64
        record, resolved, _ = self._make_session(catalog_hash=drifted)
        diff = transition.build_diff(record, resolved, self.meta)
        self.assertEqual(len(diff), 2)
        self.assertEqual(diff[0], "no semantic composition changes")
        self.assertIn("catalog drift", diff[1])
        self.assertIn(drifted, diff[1])
        self.assertIn(self.bundle.bundle_sha256, diff[1])
        record, resolved, _ = self._make_session()
        diff = transition.build_diff(record, resolved, self.meta)
        self.assertNotIn("catalog drift", "\n".join(diff))

    def test_diff_is_pure(self) -> None:
        record, _, _ = self._make_session()
        before = self.store.read_record_bytes(FIXED_ID)
        target = composition.resolve(self.bundle.docs, self._document(_lead_to_kimi))
        transition.build_diff(record, target, self.meta)
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)


class PrepareTests(TransitionTestCase):
    def test_prepare_success(self) -> None:
        record, _, _ = self._make_session()
        plan = self._prepare(_lead_to_kimi)
        self.assertEqual(plan.session_id, FIXED_ID)
        self.assertEqual(plan.prior_record, record)
        self.assertEqual(plan.new_record["scope_generation"], 2)
        self.assertEqual(plan.new_record["managed_id"], FIXED_ID)
        self.assertEqual(plan.new_record["created_at"], record["created_at"])
        self.assertEqual(
            plan.new_record["composition_hash"],
            strict_json.bundle_digest(plan.new_record["snapshot"]),
        )
        self.assertEqual(plan.new_record["catalog_hash"], self.bundle.bundle_sha256)
        self.assertEqual(plan.new_record["workflows"], "native")
        self.assertIn("lead model: opus5 -> kimi-k3", plan.diff)
        self.assertEqual(
            plan.command_text,
            f"claude-multi sessions transition {FIXED_ID} --composition default",
        )
        # Nothing mutated.
        self.assertFalse(self._staging().exists())
        self.assertFalse(self._prev().exists())

    def test_prepare_noop_transition_is_allowed(self) -> None:
        self._make_session()
        plan = self._prepare()
        self.assertEqual(list(plan.diff), ["no semantic composition changes"])
        self.assertEqual(plan.new_record["scope_generation"], 2)
        self.assertEqual(
            plan.new_record["composition_hash"], plan.prior_record["composition_hash"]
        )

    def test_prepare_rejects_non_uuid(self) -> None:
        uppercase = "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"
        for bad in ("not-a-uuid", "../escape", "", uppercase):
            with self.subTest(bad=bad):
                with self.assertRaises(TransitionError):
                    transition.prepare(
                        self.store, bad, self._document(), self.bundle
                    )

    def test_prepare_missing_record_fails_closed(self) -> None:
        with self.assertRaises(sessions.SessionError):
            transition.prepare(self.store, FIXED_ID, self._document(), self.bundle)

    def test_prepare_legacy_record_refused(self) -> None:
        snapshot = composition.snapshot(self.default_resolved)
        record = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=snapshot,
            catalog_version=1,
            catalog_hash=self.bundle.bundle_sha256,
            launcher_version="2.1.0",
            now="2026-07-21T00:00:00Z",
        )
        self.store.save(record)
        before = self.store.read_record_bytes(FIXED_ID)
        with self.assertRaisesRegex(TransitionError, "legacy"):
            self._prepare(_lead_to_kimi)
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)

    def test_prepare_unresolvable_target_refused_and_mutates_nothing(self) -> None:
        self._make_session()
        before = self.store.read_record_bytes(FIXED_ID)

        def break_model(document: dict) -> None:
            document["slots"][0]["model"] = "no-such-model"

        with self.assertRaisesRegex(TransitionError, "does not resolve"):
            self._prepare(break_model)
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)
        self.assertFalse(self._staging().exists())
        self.assertFalse(self._prev().exists())


class ExecutePrintOnlyTests(TransitionTestCase):
    def test_confirm_required_print_only_never_mutates(self) -> None:
        _, _, plan_n = self._make_session()
        before = self.store.read_record_bytes(FIXED_ID)
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(plan, confirm_exited=False, environ={})
        self.assertEqual(outcome.kind, transition.PRINT_ONLY)
        self.assertEqual(
            outcome.command_text,
            f"claude-multi sessions transition {FIXED_ID} --composition default",
        )
        self.assertEqual(outcome.diff, list(plan.diff))
        self.assertIsNone(outcome.compile_result)
        self.assertIsNone(outcome.record)
        self.assertIsNone(outcome.prior_record_bytes)
        # Nothing mutated: record bytes, live content, no staging/prev dirs.
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(plan_n.settings),
        )
        self.assertFalse(self._staging().exists())
        self.assertFalse(self._prev().exists())

    def test_from_inside_target_session_always_print_only(self) -> None:
        self._make_session()
        before = self.store.read_record_bytes(FIXED_ID)
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(
            plan,
            confirm_exited=True,
            environ={transition.SESSION_ENV_VAR: FIXED_ID},
        )
        self.assertEqual(outcome.kind, transition.PRINT_ONLY)
        self.assertIsNotNone(outcome.command_text)
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)
        self.assertFalse(self._staging().exists())
        self.assertFalse(self._prev().exists())

    def test_from_inside_a_different_session_proceeds(self) -> None:
        self._make_session()
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(
            plan,
            confirm_exited=True,
            environ={transition.SESSION_ENV_VAR: OTHER_ID},
        )
        self.assertEqual(outcome.kind, transition.RELAUNCH)


class ExecuteRelaunchTests(TransitionTestCase):
    def test_relaunch_swaps_scope_and_saves_generation_n_plus_1(self) -> None:
        _, _, old_plan = self._make_session()
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(plan, confirm_exited=True, environ={})

        self.assertEqual(outcome.kind, transition.RELAUNCH)
        self.assertEqual(outcome.prior_record_bytes, prior_bytes)
        self.assertEqual(
            {key: value for key, value in outcome.record.items() if key != "mutation_token"},
            plan.new_record,
        )
        self.assertRegex(outcome.record["mutation_token"], sessions.UUID4)
        self.assertIsNotNone(outcome.compile_result)
        # The outcome carries the exact committed bytes as the restore guard's
        # ownership token (CAS-by-own-write).
        self.assertEqual(
            outcome.committed_record_bytes, self.store.read_record_bytes(FIXED_ID)
        )

        # Live holds generation N+1 content; .prev holds the exact generation N
        # bytes; the staging dir is gone.
        new_plan = outcome.compile_result.scope_plan
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(new_plan.settings),
        )
        for relpath, data in new_plan.agent_files.items():
            self.assertEqual(self._live_file_bytes(relpath), data)
        self.assertEqual(
            self._prev().joinpath("settings.json").read_bytes(),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertFalse(self._staging().exists())

        # The on-disk record is exactly the generation N+1 record.
        self.assertEqual(
            self.store.read_record_bytes(FIXED_ID),
            strict_json.canonical_file_bytes(outcome.record),
        )
        stored = self.store.load(FIXED_ID)
        self.assertEqual(stored["scope_generation"], 2)
        self.assertEqual(stored["created_at"], "2026-07-21T00:00:00Z")
        self.assertEqual(stored["forked_from"], None)

        # The launch-ready result resumes the same UUID with the new argv.
        argv = outcome.compile_result.argv
        self.assertEqual(argv[argv.index("--resume") + 1], FIXED_ID)
        self.assertEqual(
            argv[argv.index("--add-dir") + 1], str(self._live())
        )
        self.assertEqual(
            outcome.compile_result.env_set[transition.SESSION_ENV_VAR], FIXED_ID
        )

    def test_swap_rename_and_fsync_ordering(self) -> None:
        self._make_session()
        plan = self._prepare(_lead_to_kimi)
        calls: list[tuple] = []
        real_rename = os.rename

        def tracking_rename(src, dst):
            calls.append(("rename", Path(src).name, Path(dst).name))
            return real_rename(src, dst)

        with mock.patch.object(transition.os, "rename", tracking_rename):
            outcome = transition.execute(
                plan,
                confirm_exited=True,
                environ={},
                dir_fsync=lambda path: calls.append(("fsync", Path(path).name)),
            )
        self.assertEqual(outcome.kind, transition.RELAUNCH)
        self.assertEqual(
            calls,
            [
                ("rename", FIXED_ID, f".{FIXED_ID}.prev"),
                ("fsync", "scopes"),
                ("rename", f".{FIXED_ID}.new", FIXED_ID),
                ("fsync", "scopes"),
            ],
        )

    def test_scope_fsync_failure_restores_prior_scope_and_raises_transition_error(self) -> None:
        before_record, _, old_plan = self._make_session()
        plan = self._prepare(_lead_to_kimi)
        calls = 0

        def fail_first_fsync(_path: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError(errno.EIO, "injected scope fsync failure")

        with self.assertRaisesRegex(TransitionError, "prior scope was restored"):
            transition.execute(
                plan,
                confirm_exited=True,
                environ={},
                dir_fsync=fail_first_fsync,
            )
        self.assertEqual(self.store.load(FIXED_ID), before_record)
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertFalse(self._prev().exists())
        self.assertFalse(self._staging().exists())

    def test_previous_prev_is_replaced_by_the_new_prior_generation(self) -> None:
        _, _, old_plan = self._make_session()
        stale = self._prev()
        state.ensure_private_dir(stale)
        state.atomic_write(stale / "junk.txt", b"stale")
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(outcome.kind, transition.RELAUNCH)
        self.assertFalse((self._prev() / "junk.txt").exists())
        self.assertEqual(
            self._prev().joinpath("settings.json").read_bytes(),
            strict_json.canonical_file_bytes(old_plan.settings),
        )

    def test_noop_transition_bumps_generation_with_identical_content(self) -> None:
        _, _, old_plan = self._make_session()
        plan = self._prepare()
        outcome = transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(outcome.kind, transition.RELAUNCH)
        stored = self.store.load(FIXED_ID)
        self.assertEqual(stored["scope_generation"], 2)
        self.assertEqual(
            stored["composition_hash"], plan.prior_record["composition_hash"]
        )
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(outcome.compile_result.scope_plan.settings),
        )
        self.assertNotEqual(
            outcome.compile_result.scope_plan.settings["env"][
                "CLAUDE_MULTI_LAUNCH_EPOCH"
            ],
            old_plan.settings["env"]["CLAUDE_MULTI_LAUNCH_EPOCH"],
        )

    def test_transition_with_catalog_drift_records_installed_hash(self) -> None:
        drifted = "sha256:" + "0" * 64
        self._make_session(catalog_hash=drifted)
        plan = self._prepare(_lead_to_kimi)
        self.assertTrue(any("catalog drift" in line for line in plan.diff))
        outcome = transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(outcome.record["catalog_hash"], self.bundle.bundle_sha256)

    def test_compile_error_touches_nothing(self) -> None:
        self._make_session()
        before = self.store.read_record_bytes(FIXED_ID)
        docs = copy.deepcopy(self.bundle.docs)
        docs["roles"]["roles"]["cm-analyst"]["tools"] = ["Read"]
        broken = dataclasses.replace(self.bundle, docs=docs)
        plan = transition.prepare(
            self.store, FIXED_ID, self._document(_lead_to_kimi), broken
        )
        with self.assertRaises(scope.ScopeError):
            transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)
        self.assertFalse(self._staging().exists())
        self.assertFalse(self._prev().exists())

    def test_collision_gate_fails_closed_before_any_mutation(self) -> None:
        _, _, old_plan = self._make_session()
        agents = self.project / ".claude" / "agents"
        agents.mkdir(parents=True)
        offender = agents / "shadow.md"
        offender.write_text("---\nname: cm-analyst-sol-xhigh\n---\n\nshadow\n")
        before = self.store.read_record_bytes(FIXED_ID)
        plan = self._prepare(_add_analyst)
        with self.assertRaisesRegex(TransitionError, "cm-analyst-sol-xhigh"):
            transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertFalse(self._staging().exists())
        self.assertFalse(self._prev().exists())

    def test_exec_failure_restore_brings_back_prev_and_exact_record_bytes(self) -> None:
        _, _, old_plan = self._make_session()
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(plan, confirm_exited=True, environ={})
        transition.restore_exec_failure(
            self.store, FIXED_ID, outcome.prior_record_bytes,
        expected_record_bytes=outcome.committed_record_bytes,
        )
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertFalse(self._prev().exists())
        self.assertFalse(self._staging().exists())

    def test_exec_failure_restore_without_prior_live_scope(self) -> None:
        # Record exists but no live scope at transition time (e.g. pruned):
        # the restore removes the staged N+1 scope and the record bytes.
        self._make_session(write_live_scope=False)
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(plan, confirm_exited=True, environ={})
        self.assertTrue(self._live().exists())
        self.assertFalse(self._prev().exists())
        transition.restore_exec_failure(
            self.store, FIXED_ID, outcome.prior_record_bytes,
        expected_record_bytes=outcome.committed_record_bytes,
        )
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)
        self.assertFalse(self._live().exists())
        self.assertFalse(self._prev().exists())

    def test_exec_failure_restore_clears_stale_staging(self) -> None:
        self._make_session()
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(plan, confirm_exited=True, environ={})
        staging = self._staging()
        state.ensure_private_dir(staging)
        transition.restore_exec_failure(
            self.store, FIXED_ID, outcome.prior_record_bytes,
        expected_record_bytes=outcome.committed_record_bytes,
        )
        self.assertFalse(staging.exists())

    def test_exec_failure_restores_record_before_scope_fsync(self) -> None:
        self._make_session()
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        outcome = transition.execute(
            self._prepare(_lead_to_kimi), confirm_exited=True, environ={}
        )

        def failing_fsync(_path: Path) -> None:
            raise OSError("fsync failed")

        with self.assertRaisesRegex(OSError, "fsync failed"):
            transition.restore_exec_failure(
                self.store,
                FIXED_ID,
                outcome.prior_record_bytes,
                dir_fsync=failing_fsync,
            expected_record_bytes=outcome.committed_record_bytes,
            )
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)

    def test_committed_record_write_failure_restores_prior_generation(self) -> None:
        _, _, old_plan = self._make_session()
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        plan = self._prepare(_lead_to_kimi)
        real_save = self.store.save
        injected = False

        def fail_after_replace(record):
            nonlocal injected
            result = real_save(record)
            if record.get("scope_generation") == 2 and not injected:
                injected = True
                raise state.CommittedStateError("injected directory fsync failure")
            return result

        with mock.patch.object(self.store, "save", side_effect=fail_after_replace):
            with self.assertRaisesRegex(
                state.CommittedStateError, "injected directory fsync failure"
            ):
                transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertFalse(self._prev().exists())
        self.assertFalse(self._staging().exists())

    def test_precommit_record_write_failure_restores_prior_generation(self) -> None:
        _, _, old_plan = self._make_session()
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        plan = self._prepare(_lead_to_kimi)
        real_save = self.store.save

        def fail_before_replace(record):
            if record.get("scope_generation") == 2:
                raise state.StateError(errno.ENOSPC, "injected precommit failure")
            return real_save(record)

        with mock.patch.object(self.store, "save", side_effect=fail_before_replace):
            with self.assertRaisesRegex(state.StateError, "injected precommit failure"):
                transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertFalse(self._prev().exists())
        self.assertFalse(self._staging().exists())

    def test_exec_failure_restore_rejects_non_uuid_before_path_access(self) -> None:
        with self.assertRaisesRegex(TransitionError, "managed-session UUID"):
            transition.restore_exec_failure(
                self.store, "../escape", b"not-used",
                expected_record_bytes=b"not-used",
            )

    def test_symlinked_live_scope_fails_closed_before_mutation(self) -> None:
        _, _, old_plan = self._make_session()
        outside = self.root / "outside"
        outside.mkdir()
        scope._remove_tree(self._live())
        self._live().symlink_to(outside)
        before = self.store.read_record_bytes(FIXED_ID)
        plan = self._prepare(_lead_to_kimi)
        with self.assertRaisesRegex(TransitionError, "symlink"):
            transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)
        self.assertTrue(self._live().is_symlink())
        self.assertFalse(self._prev().exists())
        self.assertFalse(self._staging().exists())

    def test_stale_plan_aborts_under_lock_without_mutating(self) -> None:
        # L1: a plan prepared against generation N aborts inside the lock when
        # a concurrent transition already advanced the record to N+1.
        self._make_session()
        plan_a = self._prepare(_lead_to_kimi)
        plan_b = self._prepare(_explore_native)
        outcome_b = transition.execute(plan_b, confirm_exited=True, environ={})
        self.assertEqual(outcome_b.kind, transition.RELAUNCH)
        after_b = self.store.read_record_bytes(FIXED_ID)
        live_b = self._live_file_bytes("settings.json")
        prev_b = self._prev().joinpath("settings.json").read_bytes()
        with self.assertRaisesRegex(TransitionError, "advanced concurrently"):
            transition.execute(plan_a, confirm_exited=True, environ={})
        # Nothing mutated: the record stays at plan_b's commit, the scopes
        # are untouched, and no staging dir leaked.
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), after_b)
        self.assertEqual(self._live_file_bytes("settings.json"), live_b)
        self.assertEqual(self._prev().joinpath("settings.json").read_bytes(), prev_b)
        self.assertFalse(self._staging().exists())
        stored = self.store.load(FIXED_ID)
        self.assertEqual(stored["scope_generation"], 2)
        self.assertEqual(
            stored["composition_hash"], plan_b.new_record["composition_hash"]
        )

    def test_intervening_launch_authority_invalidates_transition_plan(self) -> None:
        self._make_session()
        plan = self._prepare(_lead_to_kimi)
        current = self.store.load(FIXED_ID)
        current["launch_epoch"] += 1
        current["mutation_token"] = sessions.new_mutation_token()
        self.store.save(current)
        after_launch = self.store.read_record_bytes(FIXED_ID)
        live_before = self._live_file_bytes("settings.json")
        with self.assertRaisesRegex(
            TransitionError, "launch authority advanced concurrently"
        ):
            transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), after_launch)
        self.assertEqual(self._live_file_bytes("settings.json"), live_before)
        self.assertFalse(self._prev().exists())
        self.assertFalse(self._staging().exists())

    def test_runtime_change_after_prepare_aborts_before_scope_mutation(self) -> None:
        self._make_session()
        plan = self._prepare(_lead_to_kimi)
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="compact",
            cwd=str(self.project),
            now="2026-07-22T00:00:00Z",
        )
        after_hook = self.store.read_record_bytes(FIXED_ID)
        live_before = self._live_file_bytes("settings.json")
        with self.assertRaisesRegex(TransitionError, "runtime identity changed"):
            transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), after_hook)
        self.assertEqual(self._live_file_bytes("settings.json"), live_before)
        self.assertFalse(self._prev().exists())
        self.assertFalse(self._staging().exists())

    def test_lifecycle_metadata_after_prepare_is_carried_into_transition(self) -> None:
        self._make_session()
        plan = self._prepare(_lead_to_kimi)
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            source="compact",
            cwd=str(self.project),
            now="2026-07-22T00:00:00Z",
        )
        self.store.record_session_end(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            reason="other",
            now="2026-07-22T00:00:01Z",
        )
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        outcome = transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(outcome.prior_record_bytes, prior_bytes)
        self.assertEqual(outcome.record["last_event_source"], "end")
        self.assertEqual(outcome.record["last_end_reason"], "other")
        self.assertEqual(outcome.record["last_seen_at"], "2026-07-22T00:00:01Z")
        self.assertEqual(outcome.record, self.store.load(FIXED_ID))

    def test_restore_survives_lifecycle_hook_after_transition_commit(self) -> None:
        _, _, old_plan = self._make_session()
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(plan, confirm_exited=True, environ={})
        self.store.record_session_end(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            reason="other",
            launch_epoch=outcome.record["launch_epoch"],
            now="2026-07-22T00:00:01Z",
        )
        transition.restore_exec_failure(
            self.store,
            FIXED_ID,
            outcome.prior_record_bytes,
            expected_record_bytes=outcome.committed_record_bytes,
        )
        restored = self.store.load(FIXED_ID)
        self.assertEqual(restored["scope_generation"], 1)
        self.assertEqual(restored["last_end_reason"], "other")
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertFalse(self._prev().exists())

    def test_model_repair_transition_cannot_bypass_pending_fork(self) -> None:
        self._make_session()
        current = self.store.load(FIXED_ID)
        current["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        current["observed_model"] = "gpt-multi-sol-high"
        current["pending_forks"] = [
            {"session_id": OTHER_ID, "observed_at": "2026-07-22T00:00:00Z"}
        ]
        self.store.save(current)
        plan = self._prepare(_lead_to_kimi)
        before = self.store.read_record_bytes(FIXED_ID)
        live_before = self._live_file_bytes("settings.json")
        with self.assertRaisesRegex(TransitionError, "unresolved native fork"):
            transition.execute(plan, confirm_exited=True, environ={})
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)
        self.assertEqual(self._live_file_bytes("settings.json"), live_before)
        self.assertFalse(self._prev().exists())
        self.assertFalse(self._staging().exists())

    def test_restore_with_matching_expected_bytes_restores(self) -> None:
        _, _, old_plan = self._make_session()
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(plan, confirm_exited=True, environ={})
        transition.restore_exec_failure(
            self.store,
            FIXED_ID,
            outcome.prior_record_bytes,
            expected_record_bytes=outcome.committed_record_bytes,
        )
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertFalse(self._prev().exists())
        self.assertFalse(self._staging().exists())

    def test_restore_noops_after_authoritative_relink_changes_token(self) -> None:
        self._make_session()
        outcome = transition.execute(
            self._prepare(_lead_to_kimi), confirm_exited=True, environ={}
        )
        repaired_cwd = self.root / "repaired-project"
        repaired_cwd.mkdir()
        self.store.relink_runtime(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            cwd=str(repaired_cwd),
        )
        after_relink = self.store.read_record_bytes(FIXED_ID)
        transition.restore_exec_failure(
            self.store,
            FIXED_ID,
            outcome.prior_record_bytes,
            expected_record_bytes=outcome.committed_record_bytes,
        )
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), after_relink)
        self.assertEqual(self.store.load(FIXED_ID)["scope_generation"], 2)
        self.assertEqual(self.store.load(FIXED_ID)["cwd"], str(repaired_cwd))

    def test_restore_noops_when_a_newer_attempt_committed(self) -> None:
        # L2: the failing execute's cleanup must not clobber a newer commit.
        self._make_session()
        outcome_a = transition.execute(
            self._prepare(_lead_to_kimi), confirm_exited=True, environ={}
        )
        # A newer transition commits generation 3 before the failed exec's
        # cleanup runs.
        transition.execute(
            self._prepare(_explore_native), confirm_exited=True, environ={}
        )
        gen3_bytes = self.store.read_record_bytes(FIXED_ID)
        live_c = self._live_file_bytes("settings.json")
        prev_c = self._prev().joinpath("settings.json").read_bytes()
        transition.restore_exec_failure(
            self.store,
            FIXED_ID,
            outcome_a.prior_record_bytes,
            expected_record_bytes=outcome_a.committed_record_bytes,
        )
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), gen3_bytes)
        self.assertEqual(self.store.load(FIXED_ID)["scope_generation"], 3)
        self.assertEqual(self._live_file_bytes("settings.json"), live_c)
        self.assertEqual(self._prev().joinpath("settings.json").read_bytes(), prev_c)


class ConvergeTests(TransitionTestCase):
    def _n_plus_1_plan(self) -> scope.ScopePlan:
        # A variant add changes scope content (a lead-model change does not:
        # the scope holds variant files + settings, not the lead argv).
        resolved = composition.resolve(self.bundle.docs, self._document(_add_analyst))
        return self._compile_scope(resolved)

    def test_window_before_first_rename_prunes_staging(self) -> None:
        _, _, old_plan = self._make_session()
        transition._stage_scope(self.store.root, FIXED_ID, self._n_plus_1_plan())
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertFalse(self._staging().exists())
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertTrue(any("stale staged scope" in line for line in report))
        self.assertTrue(any("matches the record-authoritative" in line for line in report))

    def test_window_between_renames_restores_prev(self) -> None:
        _, _, old_plan = self._make_session()
        os.rename(self._live(), self._prev())
        transition._stage_scope(self.store.root, FIXED_ID, self._n_plus_1_plan())
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertTrue(self._live().exists())
        self.assertFalse(self._prev().exists())
        self.assertFalse(self._staging().exists())
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertTrue(any("restored .prev as the live scope" in line for line in report))

    def test_window_after_promotion_with_record_at_n_recompiles(self) -> None:
        _, _, old_plan = self._make_session()
        os.rename(self._live(), self._prev())
        scope.write_scope(self.store.root, FIXED_ID, self._n_plus_1_plan())
        # Record is still generation N: live (N+1) must converge back to N.
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertTrue(any("drifted from record authority" in line for line in report))
        # The prior generation is retained for prune, not deleted by repair.
        self.assertTrue(self._prev().exists())
        self.assertTrue(any("doctor --prune" in line for line in report))

    def test_window_after_record_save_is_already_converged(self) -> None:
        self._make_session()
        plan = self._prepare(_lead_to_kimi)
        outcome = transition.execute(plan, confirm_exited=True, environ={})
        live_before = self._live_file_bytes("settings.json")
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertTrue(any("generation 2" in line for line in report))
        self.assertTrue(any("matches the record-authoritative" in line for line in report))
        self.assertEqual(self._live_file_bytes("settings.json"), live_before)
        self.assertTrue(self._prev().exists())
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(
                outcome.compile_result.scope_plan.settings
            ),
        )

    def test_live_missing_without_prev_recompiles(self) -> None:
        _, _, old_plan = self._make_session()
        scope._remove_tree(self._live())
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertEqual(
            self._live_file_bytes("settings.json"),
            strict_json.canonical_file_bytes(old_plan.settings),
        )
        self.assertTrue(any("live scope was missing" in line for line in report))

    def test_drifted_content_is_recompiled(self) -> None:
        _, _, old_plan = self._make_session()
        agent = self._live() / ".claude" / "agents" / "cm-analyst-sol-high.md"
        agent.write_bytes(agent.read_bytes() + b"tampered\n")
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertEqual(
            agent.read_bytes(),
            old_plan.agent_files[".claude/agents/cm-analyst-sol-high.md"],
        )
        self.assertTrue(any("content differs" in line for line in report))

    def test_unexpected_extra_file_is_drift(self) -> None:
        self._make_session()
        junk = self._live() / "junk.txt"
        junk.write_bytes(b"junk")
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertFalse(junk.exists())
        self.assertTrue(any("unexpected file junk.txt" in line for line in report))

    def test_unexpected_directory_is_drift(self) -> None:
        self._make_session()
        junk = self._live() / ".claude" / "commands"
        junk.mkdir()
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertFalse(junk.exists())
        self.assertTrue(any("unexpected directory .claude/commands" in line for line in report))

    def test_symlinked_internal_directory_is_drift(self) -> None:
        self._make_session()
        outside = self.root / "outside-internal"
        outside.mkdir()
        linked = self._live() / ".claude" / "commands"
        linked.symlink_to(outside, target_is_directory=True)
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertFalse(os.path.lexists(linked))
        self.assertTrue(any("directory .claude/commands is a symlink" in line for line in report))

    def test_private_mode_drift_is_repaired(self) -> None:
        self._make_session()
        settings = self._live() / "settings.json"
        settings.chmod(0o644)
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertEqual(stat.S_IMODE(os.lstat(settings).st_mode), 0o600)
        self.assertTrue(any("settings.json mode is not 0600" in line for line in report))

    def test_converge_rejects_non_uuid(self) -> None:
        with self.assertRaises(TransitionError):
            transition.converge(self.store.root, self.store, "not-a-uuid", self.bundle)

    def test_converge_missing_record_fails_closed(self) -> None:
        with self.assertRaises(sessions.SessionError):
            transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)

    def test_converge_legacy_record_refused(self) -> None:
        snapshot = composition.snapshot(self.default_resolved)
        self.store.save(
            sessions.make_record(
                session_id=FIXED_ID,
                cwd=str(self.project),
                composition_name="default",
                snapshot=snapshot,
                catalog_version=1,
                catalog_hash=self.bundle.bundle_sha256,
                launcher_version="2.1.0",
                now="2026-07-21T00:00:00Z",
            )
        )
        with self.assertRaisesRegex(TransitionError, "legacy"):
            transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)

    def test_converge_state_root_mismatch_refused(self) -> None:
        self._make_session()
        with self.assertRaisesRegex(TransitionError, "state_root"):
            transition.converge(
                self.root / "elsewhere", self.store, FIXED_ID, self.bundle
            )

    def test_converge_symlinked_live_scope_fails_closed(self) -> None:
        self._make_session()
        outside = self.root / "outside"
        outside.mkdir()
        scope._remove_tree(self._live())
        self._live().symlink_to(outside)
        with self.assertRaisesRegex(TransitionError, "symlink"):
            transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)

    def test_converge_loads_the_record_under_the_lifecycle_lock(self) -> None:
        # L1: the authority sample must happen inside the serialized region.
        self._make_session()
        acquisitions: list[bool] = []
        real_load = self.store.load

        def spying_load(session_id: str) -> dict:
            probe = self.store.lifecycle_lock(session_id)
            acquired = probe.acquire(blocking=False)
            if acquired:
                probe.release()
            acquisitions.append(acquired)
            return real_load(session_id)

        with mock.patch.object(self.store, "load", spying_load):
            transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        # A competing non-blocking acquire on the same lock file had to fail,
        # proving load ran while converge held the lifecycle lock.
        self.assertEqual(acquisitions, [False])


class TransitionRecordTests(TransitionTestCase):
    def test_bumps_generation_and_preserves_identity(self) -> None:
        record, _, _ = self._make_session()
        new_snapshot = composition.snapshot(
            composition.resolve(self.bundle.docs, self._document(_lead_to_kimi))
        )
        bumped = sessions.transition_record(
            record,
            snapshot=new_snapshot,
            composition_name="default",
            workflows="native",
            catalog_version=1,
            catalog_hash=self.bundle.bundle_sha256,
            launcher_version="2.1.0",
        )
        self.assertEqual(bumped["scope_generation"], 2)
        self.assertEqual(bumped["managed_id"], record["managed_id"])
        self.assertEqual(bumped["cwd"], record["cwd"])
        self.assertEqual(bumped["created_at"], record["created_at"])
        self.assertEqual(bumped["forked_from"], record["forked_from"])
        self.assertEqual(bumped["snapshot"], new_snapshot)
        # Schema-valid through the real store path.
        self.store.save(bumped)

    def test_refuses_legacy_and_v1_records(self) -> None:
        snapshot = composition.snapshot(self.default_resolved)
        legacy = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=snapshot,
            catalog_version=1,
            catalog_hash=self.bundle.bundle_sha256,
            launcher_version="2.1.0",
            now="2026-07-21T00:00:00Z",
        )
        with self.assertRaises(sessions.SessionError):
            sessions.transition_record(
                legacy,
                snapshot=snapshot,
                composition_name="default",
                workflows="native",
                catalog_version=1,
                catalog_hash=self.bundle.bundle_sha256,
                launcher_version="2.1.0",
            )
        v1 = {**legacy, "version": 1, "mode": "durable", "scope_generation": 1}
        with self.assertRaises(sessions.SessionError):
            sessions.transition_record(
                v1,
                snapshot=snapshot,
                composition_name="default",
                workflows="native",
                catalog_version=1,
                catalog_hash=self.bundle.bundle_sha256,
                launcher_version="2.1.0",
            )


class SessionSentinelEnvTests(TransitionTestCase):
    def test_env_set_carries_the_session_sentinel(self) -> None:
        resolved = self.default_resolved
        snapshot = composition.snapshot(resolved)
        digest = strict_json.bundle_digest(snapshot)
        for action in (compiler.build_fresh(FIXED_ID), compiler.build_resume(FIXED_ID)):
            with self.subTest(action=action.kind):
                result = compiler.compile_launch(
                    docs=self.bundle.docs,
                    prompt_bodies=self.bundle.prompt_bodies,
                    resolved=resolved,
                    session_action=action,
                    passthrough=[],
                    settings_path=CATALOG_ROOT / "settings.json",
                    lead_prompt_path=compiler.lead_prompt_path(
                        Path("/state"), digest, FIXED_ID
                    ),
                )
                self.assertEqual(
                    result.env_set[transition.SESSION_ENV_VAR], FIXED_ID
                )


if __name__ == "__main__":
    unittest.main()


class OrdinaryTransitionRefusalTests(TransitionTestCase):
    """Ordinary gateway sessions have no composition to transition."""

    def _ordinary_record(self) -> dict:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=str(self.project),
            model="qwen38",
            context_profile="large",
            catalog_version=self.bundle.docs["version"]["catalog_version"],
            catalog_hash=self.bundle.bundle_sha256,
            launcher_version=self.bundle.docs["version"]["launcher_version"],
        )
        self.store.save(record)
        return record

    def test_prepare_refuses_ordinary_records(self) -> None:
        self._ordinary_record()
        with self.assertRaisesRegex(
            transition.TransitionError, "no composition to transition"
        ):
            transition.prepare(
                self.store, FIXED_ID, self.bundle.default_composition, self.bundle
            )

    def test_converge_repairs_ordinary_scope(self) -> None:
        self._ordinary_record()
        report = transition.converge(self.store.root, self.store, FIXED_ID, self.bundle)
        self.assertTrue(any("authoritative" in line for line in report))
        live = scope.scope_dir(self.store.root, FIXED_ID)
        settings = strict_json.load(live / "settings.json")
        self.assertIn("availableModels", settings)
        self.assertNotIn("permissions", settings)
        self.assertEqual(settings["env"]["CLAUDE_MULTI_MANAGED_ID"], FIXED_ID)
