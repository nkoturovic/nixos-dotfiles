"""CLI, quick-confirm, persistence, and resume UX tests for Phase 3."""

from __future__ import annotations

import copy
import io
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_multi import cli, composition, sessions, state, strict_json
from claude_multi.editor import EditorError, EditorOutcome


CATALOG_ROOT = Path(__file__).resolve().parents[1]
FIXED_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


class CLITestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-cli-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.launches = []
        secret_dir = state.ensure_private_dir(self.root / "secrets")
        self.secret_file = secret_dir / "claude.env"
        state.atomic_write(self.secret_file, b"KIMI_CLAUDE_API_KEY=cli-test-dummy\n")
        env = {
            "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_STATE_HOME": str(self.root / "state"),
            "TERM": "dumb",
            "CLAUDE_MULTI_SECRET_ENV": str(self.secret_file),
        }
        self.runtime = cli.Runtime(
            asset_root=CATALOG_ROOT,
            environ=env,
            cwd=self.root / "project",
            launch_callback=lambda prepared: self.launches.append(prepared) or 0,
            doctor_callback=lambda _runtime: [],
        )

    def run_cli(self, argv, text="", *, interactive=True):
        output = io.StringIO()
        code = cli.main(
            argv,
            runtime=self.runtime,
            input_stream=io.StringIO(text) if interactive else None,
            output_stream=output,
            interactive=interactive,
        )
        return code, output.getvalue()

    def save_session(
        self,
        document=None,
        *,
        session_id=FIXED_ID,
        forked_from=None,
    ):
        document = document or self.runtime.compositions.load("default")
        resolved = self.runtime.resolve_document(document)
        record = sessions.make_record(
            session_id=session_id,
            cwd=self.runtime.cwd,
            composition_name=document["name"],
            snapshot=composition.snapshot(resolved),
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            forked_from=forked_from,
            now="2026-07-21T00:00:00Z",
        )
        self.runtime.session_store.save(record)
        self.runtime.session_store.update_last(self.runtime.cwd, session_id)
        return record


class QuickConfirmTests(CLITestCase):
    def test_ready_one_enter_launches_once(self) -> None:
        code, output = self.run_cli([], "\n")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        self.assertIn("Status         Ready", output)
        self.assertIn("Scalar bound", output)
        self.assertIn("Native agents", output)

    def test_cancel_does_not_launch_or_write_session(self) -> None:
        before = list(self.runtime.session_store.sessions_dir.iterdir())
        code, output = self.run_cli([], "q\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])
        self.assertEqual(list(self.runtime.session_store.sessions_dir.iterdir()), before)
        self.assertIn("Q cancel", output)

    def test_eof_does_not_launch(self) -> None:
        code, _ = self.run_cli([], "")
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])

    def test_interrupt_does_not_launch(self) -> None:
        class Interrupting(io.StringIO):
            def readline(self, *args, **kwargs):
                raise KeyboardInterrupt

        output = io.StringIO()
        code = cli.main(
            [], runtime=self.runtime, input_stream=Interrupting(), output_stream=output, interactive=True
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])

    def test_details_are_bounded_and_optional(self) -> None:
        code, output = self.run_cli([], "d\nq\n")
        self.assertEqual(code, 0)
        self.assertIn("Variants", output)
        self.assertIn("Analyst", output)
        first_screen = output.split("D details", 1)[0]
        self.assertNotIn("cm-analyst", first_screen)

    def test_blocked_enter_routes_to_line_editor(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "blocked"
        document["availability"]["providers"]["openai"] = "off"
        self.runtime.compositions.save(document)
        code, output = self.run_cli(["--composition", "blocked", "--line"], "\n0\nq\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])
        self.assertIn("BLOCKED", output)
        self.assertIn("claude-multi / Edit blocked", output)

    def test_quick_summary_has_required_hierarchy(self) -> None:
        _, output = self.run_cli([], "q\n")
        for label in (
            "Action", "Composition", "Lead", "Roles", "Providers",
            "Native agents", "Scalar bound", "Drift", "Status",
        ):
            self.assertIn(label, output)

    def test_quick_summary_wraps_for_narrow_terminals(self) -> None:
        document = self.runtime.compositions.load("default")
        plan = cli.build_quick_plan(
            self.runtime, document, action="fresh", source="Trusted default"
        )
        rendered = cli.render_quick_confirm(self.runtime, plan, width=50)
        self.assertTrue(all(len(line) <= 50 for line in rendered.splitlines()))
        self.assertIn("Providers", rendered)

    def test_managed_ready_footer_names_enter_launch_exactly(self) -> None:
        record = self.save_session()
        plan = cli.managed_plan(self.runtime, record)
        self.assertEqual(
            cli.quick_footer(plan),
            (
                "Enter launch · R recorded · C current · F fork · E edit · D details · Q cancel",
            ),
        )

    def test_managed_blocked_footer_does_not_claim_launch(self) -> None:
        record = self.save_session()
        current = self.runtime.compositions.load("default")
        current["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(current)
        plan = cli.managed_plan(self.runtime, record, choice="current")
        self.assertFalse(plan.ready)
        self.assertEqual(
            cli.quick_footer(plan),
            (
                "Enter edit · R recorded · C current · F fork · E edit · D details · Q cancel",
                "O resume current without fork",
            ),
        )
        self.assertNotIn("Enter launch", "\n".join(cli.quick_footer(plan)))

    def test_missing_lead_enter_opens_editor_and_cancel_never_launches(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "no-lead"
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        self.runtime.compositions.save(document)
        code, output = self.run_cli(
            ["--composition", "no-lead", "--line"],
            "\n0\nq\n",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])
        self.assertIn("Lead           none selected", output)
        self.assertIn("Lead · none selected", output)
        self.assertNotIn("Traceback", output)

    def test_missing_lead_e_opens_editor_without_traceback(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "no-lead-e"
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        self.runtime.compositions.save(document)
        code, output = self.run_cli(
            ["--composition", "no-lead-e", "--line"],
            "e\n0\nq\n",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])
        self.assertIn("claude-multi / Edit no-lead-e", output)
        self.assertNotIn("Traceback", output)

    def test_missing_lead_repair_update_and_launch_plan(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "repair-lead"
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        self.runtime.compositions.save(document)
        # Enter editor; Lead → Fable; Save → Update; Enter launch.
        code, output = self.run_cli(
            ["--composition", "repair-lead", "--line"],
            "\n1\n1\n5\n1\n\n",
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.launches), 1)
        saved = self.runtime.compositions.load("repair-lead")
        self.assertEqual(saved["slots"][0], {"role": "cm-lead", "model": "fable"})
        self.assertEqual(self.launches[0].resolved.lead.model, "fable")
        self.assertNotIn("Traceback", output)

    def test_editor_error_is_rendered_as_blocked_without_traceback(self) -> None:
        document = self.runtime.compositions.load("default")
        plan = cli.build_quick_plan(
            self.runtime, document, action="fresh", source="Trusted default"
        )
        output = io.StringIO()
        with mock.patch(
            "claude_multi.cli.run_editor",
            side_effect=EditorError("lead editor could not continue"),
        ):
            result = cli.quick_confirm(
                self.runtime,
                plan,
                input_stream=io.StringIO("e\nq\n"),
                output_stream=output,
                passthrough=[],
                force_line=True,
            )
        self.assertEqual(result, 0)
        self.assertIn("Status         BLOCKED", output.getvalue())
        self.assertIn("lead editor could not continue", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())
        self.assertEqual(self.launches, [])


class InteractivePassthroughTests(CLITestCase):
    def test_structural_and_contingency_tails_block_before_enter(self) -> None:
        cases = (
            (["--", "--model", "raw"], "--model"),
            (["--", "--model=raw"], "--model=raw"),
            (["--", "-r" + FIXED_ID], "-r" + FIXED_ID),
            (
                ["--", "--append-system-prompt-file", "/tmp/prompt"],
                "--append-system-prompt-file",
            ),
        )
        for argv, token in cases:
            with self.subTest(argv=argv):
                code, output = self.run_cli(argv, "q\n")
                self.assertEqual(code, 0)
                self.assertIn("Status         BLOCKED", output)
                self.assertIn(token, output)
                self.assertIn("launcher-owned", output)
                self.assertIn("Enter edit", output)
                self.assertNotIn("Enter launch", output)
                self.assertEqual(self.launches, [])

    def test_safe_interactive_tail_stays_ready_and_byte_order_preserved(self) -> None:
        tail = ["--verbose", "hello world", "--debug"]
        code, output = self.run_cli(["--", *tail], "\n")
        self.assertEqual(code, 0, output)
        self.assertIn("Status         Ready", output)
        self.assertIn("Enter launch", output)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].result.argv[-len(tail):], tail)


class NoninteractiveAndParserTests(CLITestCase):
    def test_noninteractive_requires_explicit_composition(self) -> None:
        code, output = self.run_cli([], interactive=False)
        self.assertEqual(code, 2)
        self.assertIn("requires --composition NAME", output)
        self.assertEqual(self.launches, [])

    def test_noninteractive_explicit_composition_skips_editor(self) -> None:
        code, output = self.run_cli(["--composition", "default"], interactive=False)
        self.assertEqual(code, 0)
        self.assertEqual(output, "")
        self.assertEqual(len(self.launches), 1)

    def test_safe_passthrough_preserves_order_and_bytes(self) -> None:
        code, _ = self.run_cli(
            ["--composition", "default", "--", "--verbose", "hello world"],
            interactive=False,
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.launches[0].result.argv[-2:], ["--verbose", "hello world"])

    def test_structural_passthrough_rejected_by_phase2(self) -> None:
        code, output = self.run_cli(
            ["--composition", "default", "--", "--model=raw"],
            interactive=False,
        )
        self.assertEqual(code, 2)
        self.assertIn("launcher-owned", output)
        self.assertEqual(self.launches, [])

    def test_continue_after_separator_is_not_launcher_control(self) -> None:
        code, output = self.run_cli(
            ["--composition", "default", "--", "-c"], interactive=False
        )
        self.assertEqual(code, 2)
        self.assertIn("launcher-owned", output)

    def test_passthrough_rejected_for_management_command(self) -> None:
        code, output = self.run_cli(["models", "--", "hello"])
        self.assertEqual(code, 2)
        self.assertIn("only for launch", output)

    def test_complete_parser_surface(self) -> None:
        parser = cli.build_parser()
        samples = [
            ["compose", "list"], ["compose", "show", "default"],
            ["compose", "new", "x"], ["compose", "edit", "default"],
            ["compose", "duplicate", "default", "x"],
            ["compose", "rename", "x", "y"], ["compose", "delete", "x"],
            ["compose", "restore-default"],
            ["compose", "use-as-template", "default", "x"],
            ["sessions", "list"], ["sessions", "show", FIXED_ID],
            ["sessions", "forget", FIXED_ID], ["sessions", "link", FIXED_ID],
            ["models"], ["show"], ["show", "default"], ["doctor"],
            ["-c"], ["-r", FIXED_ID], ["--composition", "default"],
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertIsNotNone(parser.parse_args(sample))


class RememberedAndResumeTests(CLITestCase):
    def test_per_cwd_last_composition_precedes_default(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "project"
        self.runtime.compositions.save(document)
        self.save_session(document)
        code, output = self.run_cli([], "q\n")
        self.assertEqual(code, 0)
        self.assertIn("project · Last used in this directory", output)

    def test_continue_without_pointer_is_actionable(self) -> None:
        code, output = self.run_cli(["-c"], "")
        self.assertEqual(code, 2)
        self.assertIn("no managed session", output)

    def test_exact_resume_shows_recorded_choice_and_hashes(self) -> None:
        self.save_session()
        code, output = self.run_cli(["-r", FIXED_ID], "q\n")
        self.assertEqual(code, 0)
        self.assertIn("Recorded snapshot", output)
        self.assertIn("Catalog drift", output)
        self.assertIn("Composition", output)
        self.assertIn("R recorded · C current · F fork", output)
        self.assertIn("catalog_hash", output)
        self.assertIn("composition_hash", output)
        self.assertIn("Existing workers", output)

    def test_resume_enter_uses_recorded_snapshot(self) -> None:
        self.save_session()
        code, _ = self.run_cli(["-r", FIXED_ID], "\n")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].result.session_action.kind, "resume")

    def test_resume_current_cross_provider_defaults_fork_and_warns(self) -> None:
        record = self.save_session()
        current = self.runtime.compositions.load("default")
        current["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(current)
        plan = cli.managed_plan(self.runtime, record, choice="current")
        self.assertEqual(plan.action, "fork")
        self.assertIn("hidden reasoning", plan.cross_provider_warning)
        self.assertFalse(plan.ready)  # pinned fork contract remains fail-closed
        self.assertTrue(any("fork natively" in error for error in plan.errors))

    def test_user_may_return_to_recorded_after_cross_provider_choice(self) -> None:
        self.save_session()
        current = self.runtime.compositions.load("default")
        current["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(current)
        code, output = self.run_cli(["-r", FIXED_ID], "c\nr\nq\n")
        self.assertEqual(code, 0)
        self.assertIn("Fork is the default", re.sub(r"\s+", " ", output))
        self.assertEqual(self.launches, [])

    def test_user_may_deliberately_override_fork_default(self) -> None:
        self.save_session()
        current = self.runtime.compositions.load("default")
        current["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(current)
        code, output = self.run_cli(["-r", FIXED_ID], "c\no\n\n")
        self.assertEqual(code, 0)
        self.assertIn("O resume current without fork", output)
        self.assertIn("Deliberate resume override selected", output)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].resolved.lead.model, "sol")
        self.assertEqual(self.launches[0].result.session_action.kind, "resume")

    def test_noninteractive_resume_requires_and_uses_named_composition(self) -> None:
        self.save_session()
        code, output = self.run_cli(
            ["--composition", "default", "-r", FIXED_ID], interactive=False
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].result.session_action.kind, "resume")

    def test_invalid_resume_uuid_rejected(self) -> None:
        code, output = self.run_cli(["-r", "not-a-uuid"], "")
        self.assertEqual(code, 2)
        self.assertIn("not a UUIDv4", output)

    def test_resume_prepare_preserves_existing_fork_lineage(self) -> None:
        self.save_session(forked_from=OTHER_ID)
        prepared = self.runtime.prepare(
            self.runtime.compositions.load("default"),
            action="resume",
            passthrough=[],
            session_id=FIXED_ID,
        )
        self.assertEqual(prepared.record["session_id"], FIXED_ID)
        self.assertEqual(prepared.record["forked_from"], OTHER_ID)


class CompositionCommandTests(CLITestCase):
    def test_duplicate_rename_delete_restore_and_template(self) -> None:
        self.assertEqual(self.run_cli(["compose", "duplicate", "default", "copy"])[0], 0)
        self.assertTrue(self.runtime.compositions.has_user("copy"))
        self.assertEqual(self.run_cli(["compose", "rename", "copy", "renamed"])[0], 0)
        self.assertFalse(self.runtime.compositions.has_user("copy"))
        self.assertTrue(self.runtime.compositions.has_user("renamed"))
        self.assertEqual(
            self.run_cli(["compose", "use-as-template", "renamed", "template"])[0], 0
        )
        self.assertEqual(self.runtime.compositions.load("template")["name"], "template")
        self.assertEqual(self.run_cli(["compose", "delete", "renamed"])[0], 0)
        self.assertFalse(self.runtime.compositions.has_user("renamed"))
        self.assertEqual(self.run_cli(["compose", "restore-default"])[0], 0)
        self.assertTrue(self.runtime.compositions.has_user("default"))

    def test_names_and_paths_are_validated(self) -> None:
        code, output = self.run_cli(["compose", "duplicate", "default", "../bad"])
        self.assertEqual(code, 2)
        self.assertIn("unsafe state name", output)

    def test_show_and_models_are_concise(self) -> None:
        code, output = self.run_cli(["models"])
        self.assertEqual(code, 0)
        self.assertIn("fable\tFable", output)
        code, output = self.run_cli(["show", "default"])
        self.assertEqual(code, 0)
        self.assertIn("Status         Ready", output)

    def test_compose_list_marks_seed_and_user(self) -> None:
        self.runtime.compositions.duplicate("default", "copy")
        code, output = self.run_cli(["compose", "list"])
        self.assertEqual(code, 0)
        self.assertIn("default\ttrusted seed", output)
        self.assertIn("copy\tuser", output)

    def test_duplicate_and_rename_reject_every_visible_target(self) -> None:
        self.runtime.compositions.duplicate("default", "source")
        self.runtime.compositions.duplicate("default", "taken")
        taken_path = self.runtime.compositions._path("taken")
        taken_before = taken_path.read_bytes()
        for command in (
            ["compose", "duplicate", "source", "default"],
            ["compose", "duplicate", "source", "taken"],
            ["compose", "rename", "source", "default"],
            ["compose", "rename", "source", "taken"],
        ):
            with self.subTest(command=command):
                code, output = self.run_cli(command)
                self.assertEqual(code, 2)
                self.assertIn("already exists", output)
                self.assertIn("Update", output)
                self.assertTrue(self.runtime.compositions.has_user("source"))
                self.assertEqual(taken_path.read_bytes(), taken_before)
        self.assertFalse(self.runtime.compositions.has_user("default"))


class CompositionTargetGuardTests(CLITestCase):
    ACTIONS = ("save-as", "duplicate", "use-as-template")

    def plan(self):
        document = self.runtime.compositions.load("default")
        return cli.build_quick_plan(
            self.runtime, document, action="fresh", source="Trusted default"
        )

    def test_editor_actions_reject_existing_user_target_without_mutation(self) -> None:
        taken = self.runtime.compositions.load("default")
        taken["name"] = "taken"
        taken["description"] = "must remain byte-identical"
        self.runtime.compositions.save(taken)
        path = self.runtime.compositions._path("taken")
        before = path.read_bytes()
        for action in self.ACTIONS:
            with self.subTest(action=action):
                outcome = EditorOutcome(
                    action,
                    copy.deepcopy(self.plan().document),
                    "taken",
                )
                with self.assertRaisesRegex(cli.CLIError, "already exists.*Update"):
                    cli._apply_editor_outcome(self.runtime, self.plan(), outcome)
                self.assertEqual(path.read_bytes(), before)

    def test_editor_actions_reject_seed_target_without_creating_override(self) -> None:
        seed_before = strict_json.canonical_bytes(self.runtime.catalog.default_composition)
        self.assertFalse(self.runtime.compositions.has_user("default"))
        for action in self.ACTIONS:
            with self.subTest(action=action):
                outcome = EditorOutcome(
                    action,
                    copy.deepcopy(self.plan().document),
                    "default",
                )
                with self.assertRaisesRegex(cli.CLIError, "already exists.*Update"):
                    cli._apply_editor_outcome(self.runtime, self.plan(), outcome)
                self.assertFalse(self.runtime.compositions.has_user("default"))
                self.assertEqual(
                    strict_json.canonical_bytes(self.runtime.compositions.load("default")),
                    seed_before,
                )

    def test_editor_actions_accept_fresh_targets(self) -> None:
        source_before = strict_json.canonical_bytes(self.runtime.compositions.load("default"))
        for action in self.ACTIONS:
            target = action.replace("-", "_")
            with self.subTest(action=action):
                outcome = EditorOutcome(
                    action,
                    copy.deepcopy(self.plan().document),
                    target,
                )
                updated = cli._apply_editor_outcome(self.runtime, self.plan(), outcome)
                self.assertIsNotNone(updated)
                self.assertTrue(self.runtime.compositions.has_user(target))
                self.assertEqual(self.runtime.compositions.load(target)["name"], target)
        self.assertEqual(
            strict_json.canonical_bytes(self.runtime.compositions.load("default")),
            source_before,
        )


class EditorFailurePreservationTests(CLITestCase):
    def initial_plan(self):
        document = self.runtime.compositions.load("default")
        return cli.build_quick_plan(
            self.runtime, document, action="fresh", source="Trusted default"
        )

    def test_target_collision_preserves_edits_for_successful_retry(self) -> None:
        occupied = self.runtime.compositions.load("default")
        occupied["name"] = "occupied"
        occupied["description"] = "existing target"
        self.runtime.compositions.save(occupied)
        occupied_path = self.runtime.compositions._path("occupied")
        occupied_before = occupied_path.read_bytes()

        edited = self.runtime.compositions.load("default")
        edited["description"] = "preserve this edit"
        seen = []

        def editor_side_effect(state, *_args, **_kwargs):
            seen.append(copy.deepcopy(state.document))
            if len(seen) == 1:
                return EditorOutcome("save-as", copy.deepcopy(edited), "occupied")
            self.assertEqual(state.document["description"], "preserve this edit")
            self.assertTrue(state.dirty)
            return EditorOutcome(
                "save-as", copy.deepcopy(state.document), "retry-target"
            )

        output = io.StringIO()
        with mock.patch(
            "claude_multi.cli.run_editor", side_effect=editor_side_effect
        ):
            result = cli.quick_confirm(
                self.runtime,
                self.initial_plan(),
                input_stream=io.StringIO("e\ne\nq\n"),
                output_stream=output,
                passthrough=[],
                force_line=True,
            )
        self.assertEqual(result, 0)
        self.assertEqual(len(seen), 2)
        self.assertIn("Status         BLOCKED", output.getvalue())
        self.assertIn("already exists", output.getvalue())
        self.assertEqual(occupied_path.read_bytes(), occupied_before)
        self.assertEqual(
            self.runtime.compositions.load("retry-target")["description"],
            "preserve this edit",
        )
        self.assertEqual(self.launches, [])

    def test_invalid_update_preserves_edits_then_repair_succeeds(self) -> None:
        invalid = self.runtime.compositions.load("default")
        invalid["description"] = "unfinished but retained"
        invalid["slots"] = [
            slot for slot in invalid["slots"] if slot["role"] != "cm-lead"
        ]
        seen = []

        def editor_side_effect(state, *_args, **_kwargs):
            seen.append(copy.deepcopy(state.document))
            if len(seen) == 1:
                return EditorOutcome("update", copy.deepcopy(invalid))
            self.assertEqual(
                state.document["description"], "unfinished but retained"
            )
            self.assertTrue(state.dirty)
            self.assertIsNone(state.lead_slot())
            state.set_lead("fable")
            return EditorOutcome("update", copy.deepcopy(state.document))

        output = io.StringIO()
        with mock.patch(
            "claude_multi.cli.run_editor", side_effect=editor_side_effect
        ):
            result = cli.quick_confirm(
                self.runtime,
                self.initial_plan(),
                input_stream=io.StringIO("e\ne\nq\n"),
                output_stream=output,
                passthrough=[],
                force_line=True,
            )
        self.assertEqual(result, 0)
        self.assertEqual(len(seen), 2)
        self.assertIn("Status         BLOCKED", output.getvalue())
        saved = self.runtime.compositions.load("default")
        self.assertEqual(saved["description"], "unfinished but retained")
        self.assertEqual(saved["slots"][0]["role"], "cm-lead")
        self.assertEqual(self.launches, [])

    def test_update_io_failure_keeps_document_and_original_bytes_until_retry(self) -> None:
        self.runtime.compositions.restore_default()
        path = self.runtime.compositions._path("default")
        before = path.read_bytes()
        edited = self.runtime.compositions.load("default")
        edited["description"] = "retry after storage failure"
        original_save = self.runtime.compositions.save
        save_calls = 0
        editor_calls = 0

        def flaky_save(document, *, target=None):
            nonlocal save_calls
            save_calls += 1
            if save_calls == 1:
                raise OSError("simulated storage failure")
            return original_save(document, target=target)

        def editor_side_effect(state, *_args, **_kwargs):
            nonlocal editor_calls
            editor_calls += 1
            if editor_calls == 1:
                return EditorOutcome("update", copy.deepcopy(edited))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(
                state.document["description"], "retry after storage failure"
            )
            self.assertTrue(state.dirty)
            return EditorOutcome("update", copy.deepcopy(state.document))

        output = io.StringIO()
        with mock.patch.object(
            self.runtime.compositions, "save", side_effect=flaky_save
        ), mock.patch(
            "claude_multi.cli.run_editor", side_effect=editor_side_effect
        ):
            result = cli.quick_confirm(
                self.runtime,
                self.initial_plan(),
                input_stream=io.StringIO("e\ne\nq\n"),
                output_stream=output,
                passthrough=[],
                force_line=True,
            )
        self.assertEqual(result, 0)
        self.assertIn("simulated storage failure", output.getvalue())
        self.assertEqual(
            self.runtime.compositions.load("default")["description"],
            "retry after storage failure",
        )
        self.assertEqual(self.launches, [])


class CompositionReadSafetyTests(CLITestCase):
    def test_ordinary_private_file_loads(self) -> None:
        self.runtime.compositions.duplicate("default", "ordinary")
        self.assertEqual(self.runtime.compositions.load("ordinary")["name"], "ordinary")

    def test_external_symlink_is_rejected_without_following(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "external"
        external = self.root / "external.json"
        external.write_bytes(strict_json.canonical_file_bytes(document))
        os.chmod(external, 0o600)
        self.runtime.compositions._path("external").symlink_to(external)
        with self.assertRaisesRegex(
            cli.CLIError, "cannot load composition 'external'.*symlink"
        ):
            self.runtime.compositions.load("external")
        self.assertEqual(
            external.read_bytes(), strict_json.canonical_file_bytes(document)
        )

    def test_in_tree_symlink_is_rejected_without_following(self) -> None:
        self.runtime.compositions.duplicate("default", "source")
        source = self.runtime.compositions._path("source")
        before = source.read_bytes()
        self.runtime.compositions._path("alias").symlink_to(source.name)
        with self.assertRaisesRegex(
            cli.CLIError, "cannot load composition 'alias'.*symlink"
        ):
            self.runtime.compositions.load("alias")
        self.assertEqual(source.read_bytes(), before)


class SessionCommandTests(CLITestCase):
    def test_link_list_show_forget_without_private_scrape(self) -> None:
        code, output = self.run_cli(
            ["sessions", "link", FIXED_ID, "--composition", "default"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Linked", output)
        self.assertEqual(self.run_cli(["sessions", "list"])[0], 0)
        code, output = self.run_cli(["sessions", "show", FIXED_ID])
        self.assertEqual(code, 0)
        self.assertIn('"session_id":"' + FIXED_ID, output)
        code, output = self.run_cli(["sessions", "forget", FIXED_ID])
        self.assertEqual(code, 0)
        self.assertIn("Forgot", output)

    def test_noninteractive_link_requires_composition(self) -> None:
        code, output = self.run_cli(["sessions", "link", FIXED_ID], interactive=False)
        self.assertEqual(code, 2)
        self.assertIn("requires --composition", output)

    def test_doctor_uses_injected_local_check(self) -> None:
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("Ready", output)
        self.assertEqual(self.launches, [])


if __name__ == "__main__":
    unittest.main()


class SecretReadinessSurfaceTests(CLITestCase):
    def _without_secret(self):
        self.secret_file.unlink()

    def test_quick_confirm_blocked_when_selected_secret_missing(self) -> None:
        self._without_secret()
        # Cancel at the quick-confirm; Enter is not pressed, so no editor opens.
        code, output = self.run_cli([], text="q\n")
        self.assertEqual(code, 0)
        self.assertIn("BLOCKED", output)
        self.assertIn("Kimi (kimi)", output)
        self.assertIn("env:KIMI_CLAUDE_API_KEY", output)
        self.assertEqual(self.launches, [])

    def test_quick_confirm_ready_when_secret_present(self) -> None:
        code, output = self.run_cli([], text="\n")
        self.assertNotIn("env:KIMI_CLAUDE_API_KEY unavailable", output)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)

    def test_doctor_reports_affected_compositions(self) -> None:
        self._without_secret()
        code, output = self.run_cli(["doctor"], interactive=False)
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", output)
        self.assertIn("composition default: provider Kimi (kimi)", output)

    def test_doctor_ready_when_secret_present(self) -> None:
        code, output = self.run_cli(["doctor"], interactive=False)
        self.assertEqual(code, 0)
        self.assertIn("Ready", output)

    def test_error_line_redacted(self) -> None:
        self.secret_file.write_bytes(b"not-an-assignment\n")
        code, output = self.run_cli([], text="q\n")
        self.assertEqual(code, 0)
        self.assertIn("unsafe or malformed", output)
        self.assertNotIn("not-an-assignment", output)
