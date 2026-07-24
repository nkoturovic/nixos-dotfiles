"""CLI, quick-confirm, persistence, and resume UX tests for Phase 3."""

from __future__ import annotations

import copy
import curses
import io
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from claude_multi import cli, composition, scope as scope_mod, sessions, state, strict_json, tui
from claude_multi.tui import EditorError, EditorOutcome, EditorState, workflow_guarantee_panel


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
            doctor_binary_callback=lambda _contract: (
                [],
                ["Managed Claude 2.1.217 verified (fixture)."],
            ),
            doctor_daemon_callback=lambda: cli.launch.DaemonStatus(
                state="absent", summary="fixture daemon absent"
            ),
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
        mode="legacy",
        scope_generation=0,
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
            mode=mode,
            scope_generation=scope_generation,
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
        self.assertNotIn("Variants", first_screen)
        self.assertNotIn("cm-reviewer", first_screen)

    def test_blocked_enter_prints_edit_guidance(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "blocked"
        document["availability"]["providers"]["openai"] = "off"
        self.runtime.compositions.save(document)
        code, output = self.run_cli(["--composition", "blocked", "--line"], "\nq\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])
        self.assertIn("Status         BLOCKED", output)
        self.assertIn("$EDITOR", output)
        self.assertIn("blocked.json", output)
        self.assertNotIn("Traceback", output)

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
        # R1 P1: no recorded/current switching and no editor on managed.
        self.assertEqual(
            cli.quick_footer(plan),
            (
                "Enter launch · D details · S sessions · ? workflows · Q cancel",
            ),
        )

    def test_managed_blocked_footer_does_not_claim_launch(self) -> None:
        record = self.save_session()
        record["snapshot"]["lead"]["model"] = "no-such-model"
        self.runtime.session_store.save(record)
        plan = cli.managed_plan(self.runtime, record)
        self.assertFalse(plan.ready)
        self.assertEqual(
            cli.quick_footer(plan),
            (
                "Enter transition hint · D details · S sessions · ? workflows · Q cancel",
            ),
        )
        self.assertNotIn("Enter launch", "\n".join(cli.quick_footer(plan)))

    def test_missing_lead_enter_prints_edit_guidance_and_never_launches(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "no-lead"
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        self.runtime.compositions.save(document)
        code, output = self.run_cli(
            ["--composition", "no-lead", "--line"],
            "\nq\n",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])
        self.assertIn("Lead           none selected", output)
        self.assertIn("$EDITOR", output)
        self.assertIn("no-lead.json", output)
        self.assertNotIn("Traceback", output)

    def test_missing_lead_e_prints_edit_guidance_without_traceback(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "no-lead-e"
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        self.runtime.compositions.save(document)
        code, output = self.run_cli(
            ["--composition", "no-lead-e", "--line"],
            "e\nq\n",
        )
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])
        self.assertIn("$EDITOR", output)
        self.assertIn("no-lead-e.json", output)
        self.assertNotIn("Traceback", output)

    def test_missing_lead_repair_update_and_launch_plan(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "repair-lead"
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        self.runtime.compositions.save(document)

        def editor_side_effect(state, *_args, **_kwargs):
            state.set_lead("fable")
            return EditorOutcome("update", copy.deepcopy(state.document))

        # Enter (blocked) opens the editor; the repaired composition is saved;
        # Enter launches.
        with mock.patch(
            "claude_multi.cli.run_editor", side_effect=editor_side_effect
        ):
            code, output = self.run_cli(
                ["--composition", "repair-lead", "--line"],
                "\n\n",
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
            ["sessions", "transition", FIXED_ID, "--composition", "default"],
            ["sessions", "transition", FIXED_ID, "--composition", "default", "--its-exited"],
            ["models"], ["show"], ["show", "default"], ["doctor"],
            ["doctor", "--prune"], ["doctor", "--repair", FIXED_ID],
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
        # R1 P1: the recorded/current switcher is gone from the footer.
        self.assertNotIn("C current", output)
        self.assertNotIn("R recorded", output)
        self.assertIn("catalog_hash", output)
        self.assertIn("composition_hash", output)
        self.assertIn("Existing workers", output)

    def test_resume_enter_uses_recorded_snapshot(self) -> None:
        self.save_session()
        code, _ = self.run_cli(["-r", FIXED_ID], "\n")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].result.session_action.kind, "resume")

    def test_resume_override_refused_with_transition_pointer(self) -> None:
        self.save_session()
        self.runtime.compositions.duplicate("default", "other")
        code, output = self.run_cli(
            ["--composition", "other", "-r", FIXED_ID], interactive=False
        )
        self.assertEqual(code, 2)
        self.assertIn("resume always uses the recorded composition", output)
        self.assertIn(
            f"claude-multi sessions transition {FIXED_ID} --composition other",
            output,
        )
        self.assertEqual(self.launches, [])

    def test_continue_override_refused_with_transition_pointer(self) -> None:
        self.save_session()
        self.runtime.compositions.duplicate("default", "other")
        code, output = self.run_cli(
            ["--composition", "other", "-c"], interactive=False
        )
        self.assertEqual(code, 2)
        self.assertIn("resume always uses the recorded composition", output)
        self.assertIn(
            f"claude-multi sessions transition {FIXED_ID} --composition other",
            output,
        )
        self.assertEqual(self.launches, [])

    def test_resume_override_refusal_is_pinned_verbatim(self) -> None:
        self.assertEqual(
            cli.RESUME_OVERRIDE_REFUSAL,
            "resume always uses the recorded composition; --composition {name!r} does "
            "not apply to session {uuid}. To change composition: `claude-multi "
            "sessions transition {uuid} --composition {name}`",
        )

    def test_provider_drift_warning_is_pinned_verbatim(self) -> None:
        self.assertEqual(
            cli.RESUME_PROVIDER_DRIFT_WARNING,
            "The saved composition now leads with {new_provider}; resume keeps the "
            "recorded {old_provider} lead, so hidden reasoning continuity is "
            "preserved. To switch providers: `claude-multi sessions transition "
            "{uuid} --composition <name>` after the process exits. For strict "
            "separation fork natively (`claude --resume OLD --fork-session`) and "
            "adopt with `claude-multi sessions link UUID`.",
        )

    def test_resume_same_name_composition_is_not_an_override(self) -> None:
        # Naming the recorded composition changes nothing: the recorded
        # intent is re-resolved and the resume proceeds.
        self.save_session()
        code, output = self.run_cli(
            ["--composition", "default", "-r", FIXED_ID], interactive=False
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].result.session_action.kind, "resume")

    def test_noninteractive_resume_needs_no_composition(self) -> None:
        # Regression: scripted `-r <uuid>` must resume from the record alone;
        # the --composition requirement applies only to fresh launches.
        self.save_session()
        code, output = self.run_cli(["-r", FIXED_ID], interactive=False)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].result.session_action.kind, "resume")

    def test_noninteractive_continue_needs_no_composition(self) -> None:
        self.save_session()
        self.runtime.session_store.update_last(self.runtime.cwd, FIXED_ID)
        code, output = self.run_cli(["-c"], interactive=False)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].result.session_action.kind, "resume")

    def test_noninteractive_fresh_still_requires_composition(self) -> None:
        code, output = self.run_cli([], interactive=False)
        self.assertEqual(code, 2)
        self.assertIn("noninteractive launch requires --composition NAME", output)
        self.assertEqual(self.launches, [])

    def test_saved_drift_shows_transition_note_and_resumes_recorded(self) -> None:
        # The saved composition changed after the session was recorded: drift
        # is informational, the transition path is named, and resume still
        # launches the RECORDED composition (R1 P1/P2).
        record = self.save_session()
        current = self.runtime.compositions.load("default")
        current["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(current)
        code, output = self.run_cli(["-r", FIXED_ID], "\n")
        self.assertEqual(code, 0, output)
        self.assertIn("composition 'default' changed since session creation", output)
        self.assertIn(
            f"claude-multi sessions transition {FIXED_ID} --composition <name>",
            re.sub(r"\s+", " ", output),
        )
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].resolved.lead.model, "fable")
        self.assertEqual(self.launches[0].result.session_action.kind, "resume")
        # The record itself was not rewritten by the saved drift.
        self.assertEqual(record["composition_name"], "default")

    def test_saved_cross_provider_drift_warns_informationally(self) -> None:
        record = self.save_session()
        current = self.runtime.compositions.load("default")
        current["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(current)
        plan = cli.managed_plan(self.runtime, record)
        self.assertEqual(plan.action, "resume")
        self.assertEqual(plan.source, "Recorded snapshot")
        self.assertIn("hidden reasoning", plan.cross_provider_warning)
        self.assertIn("fork natively", plan.cross_provider_warning)
        self.assertIn("sessions transition", plan.cross_provider_warning)
        self.assertTrue(plan.ready)
        self.assertEqual(plan.resolved.lead.model, "fable")

    def test_c_and_r_keys_redirect_to_transition_without_switching(self) -> None:
        self.save_session()
        current = self.runtime.compositions.load("default")
        current["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(current)
        code, output = self.run_cli(["-r", FIXED_ID], "c\nr\n\n")
        self.assertEqual(code, 0, output)
        self.assertIn(
            f"claude-multi sessions transition {FIXED_ID} --composition <name>",
            re.sub(r"\s+", " ", output),
        )
        # Every render stayed on the recorded snapshot; Enter resumed it.
        self.assertNotIn("Current saved composition", output)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].resolved.lead.model, "fable")

    def test_e_key_on_managed_plan_redirects_and_never_opens_editor(self) -> None:
        # R1 P1: the editor on a managed resume was the same override
        # vector (edit -> launch-once/save -> plain resume at the same
        # generation). It now shows the transition path instead.
        self.save_session()
        with mock.patch(
            "claude_multi.cli.run_editor",
            side_effect=AssertionError("editor must not open on managed plans"),
        ):
            code, output = self.run_cli(["-r", FIXED_ID, "--line"], "e\nq\n")
        self.assertEqual(code, 0, output)
        self.assertIn(
            f"claude-multi sessions transition {FIXED_ID} --composition <name>",
            output,
        )
        self.assertNotIn("$EDITOR", output)
        self.assertEqual(self.launches, [])

    def test_blocked_managed_enter_redirects_and_never_opens_editor(self) -> None:
        record = self.save_session()
        record["snapshot"]["lead"]["model"] = "no-such-model"
        self.runtime.session_store.save(record)
        with mock.patch(
            "claude_multi.cli.run_editor",
            side_effect=AssertionError("editor must not open on managed plans"),
        ):
            code, output = self.run_cli(["-r", FIXED_ID, "--line"], "\nq\n")
        self.assertEqual(code, 0, output)
        self.assertIn("Status         BLOCKED", output)
        self.assertIn("Enter transition hint", output)
        self.assertIn(
            f"claude-multi sessions transition {FIXED_ID} --composition <name>",
            output,
        )
        self.assertNotIn("$EDITOR", output)
        self.assertEqual(self.launches, [])

    def test_resume_recorded_note_is_pinned_verbatim(self) -> None:
        self.assertEqual(
            cli.RESUME_RECORDED_NOTE,
            "resume uses the recorded composition; to change it: `claude-multi "
            "sessions transition {uuid} --composition <name>`",
        )

    def test_catalog_drifted_snapshot_recompiles_and_resumes(self) -> None:
        # R1 P2: the recorded selectors drifted from the installed catalog
        # (e.g. a catalog update changed a lane's client_selector). Resume
        # re-resolves the recorded intent against the installed catalog,
        # shows drift informationally, and proceeds with the durable
        # recompile instead of blocking.
        record = self.save_session(mode="durable", scope_generation=2)
        drifted = copy.deepcopy(record["snapshot"])
        drifted["variants"][0]["client_selector"] = "old-selector[1m]"
        drifted["lead"]["client_selector"] = "old-lead-selector"
        record["snapshot"] = drifted
        record["catalog_hash"] = "sha256:" + "0" * 64
        self.runtime.session_store.save(record)
        plan = cli.managed_plan(self.runtime, record)
        self.assertTrue(plan.ready, plan.errors)
        self.assertIn("trusted catalog changed since session creation", plan.drift)
        code, output = self.run_cli(["-r", FIXED_ID], "q\n")
        self.assertEqual(code, 0, output)
        self.assertIn("trusted catalog changed since session creation", output)
        self.assertIn("Status         Ready", output)
        code, _ = self.run_cli(["-r", FIXED_ID], "\n")
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        prepared = self.launches[0]
        self.assertTrue(prepared.result.durable)
        self.assertEqual(prepared.record["scope_generation"], 2)
        self.assertEqual(prepared.expected_source_scope_generation, 2)
        self.assertEqual(
            prepared.expected_source_composition_hash, record["composition_hash"]
        )
        # The recompile heals the drifted selectors from the catalog.
        healed = prepared.record["snapshot"]
        self.assertNotEqual(healed["variants"][0]["client_selector"], "old-selector[1m]")
        self.assertNotEqual(healed["lead"]["client_selector"], "old-lead-selector")

    def test_missing_saved_composition_is_informational_and_resumes(self) -> None:
        # R1 P2: the saved composition vanished after recording; the recorded
        # snapshot is the authority and the resume proceeds.
        ghost = self.runtime.compositions.load("default")
        ghost["name"] = "ghost"
        self.runtime.compositions.save(ghost)
        self.save_session(ghost)
        self.runtime.compositions.delete("ghost")
        plan = cli.managed_plan(
            self.runtime, self.runtime.session_store.load(FIXED_ID)
        )
        self.assertTrue(plan.ready, plan.errors)
        self.assertIn("composition 'ghost' is missing", plan.drift)
        code, output = self.run_cli(["-r", FIXED_ID], "\n")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].resolved.lead.model, "fable")

    def test_unresolvable_recorded_intent_fails_closed_with_drift_named(self) -> None:
        # R1 P2: a role/model genuinely deleted from the catalog is the only
        # fail-closed case; the resolve error names the missing model and the
        # drift lines stay visible in the render.
        record = self.save_session()
        record["snapshot"]["lead"]["model"] = "no-such-model"
        record["catalog_hash"] = "sha256:" + "0" * 64
        self.runtime.session_store.save(record)
        plan = cli.managed_plan(self.runtime, record)
        self.assertFalse(plan.ready)
        self.assertIn("no-such-model", "; ".join(plan.errors))
        self.assertIn("trusted catalog changed since session creation", plan.drift)
        code, output = self.run_cli(["-r", FIXED_ID], "q\n")
        self.assertEqual(code, 0)
        self.assertIn("Status         BLOCKED", output)
        self.assertIn("no-such-model", output)
        self.assertIn("trusted catalog changed since session creation", output)
        code, output = self.run_cli(
            ["--composition", "default", "-r", FIXED_ID], interactive=False
        )
        self.assertEqual(code, 2)
        self.assertIn("composition is blocked", output)
        self.assertIn("no-such-model", output)
        self.assertEqual(self.launches, [])

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
        self.assertEqual(prepared.record["managed_id"], FIXED_ID)
        self.assertEqual(prepared.record["forked_from"], OTHER_ID)

    def test_fork_prepare_fails_with_adoption_guidance(self) -> None:
        self.save_session()
        with self.assertRaisesRegex(cli.CLIError, "sessions link") as raised:
            self.runtime.prepare(
                self.runtime.compositions.load("default"),
                action="fork",
                passthrough=[],
                session_id=FIXED_ID,
            )
        self.assertIn("fork natively", str(raised.exception))
        # The failure happens before any launch is prepared or performed.
        self.assertEqual(self.launches, [])


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
        import builtins

        project_dir = (
            Path(self.runtime.environ["HOME"])
            / ".claude"
            / "projects"
            / cli._native_project_slug(self.runtime.cwd)
        )
        project_dir.mkdir(parents=True)
        transcript = project_dir / f"{FIXED_ID}.jsonl"
        transcript.write_bytes(b'{"poison":"must-not-be-read"}\n')
        real_builtin_open = builtins.open
        real_io_open = io.open

        def guarded_open(original):
            def wrapper(file, mode="r", *args, **kwargs):
                try:
                    target = Path(file)
                except TypeError:
                    target = None
                if target == transcript and any(flag in mode for flag in ("r", "+")):
                    raise AssertionError("transcript body was opened")
                return original(file, mode, *args, **kwargs)

            return wrapper

        with mock.patch("builtins.open", side_effect=guarded_open(real_builtin_open)), mock.patch(
            "io.open", side_effect=guarded_open(real_io_open)
        ):
            code, output = self.run_cli(
                ["sessions", "link", FIXED_ID, "--composition", "default"]
            )
            self.assertEqual(code, 0)
            self.assertIn("Linked", output)
            record = self.runtime.session_store.resolve(FIXED_ID)
            stable_id = record["managed_id"]
            self.assertEqual(self.run_cli(["sessions", "list"])[0], 0)
            code, output = self.run_cli(["sessions", "show", FIXED_ID])
            self.assertEqual(code, 0)
            self.assertIn('"runtime_session_id":"' + FIXED_ID, output)
            self.assertIn('"managed_id":', output)
            payload = strict_json.canonical_bytes(
                {
                    "hook_event_name": "SessionStart",
                    "session_id": FIXED_ID,
                    "source": "resume",
                    "cwd": self.runtime.cwd,
                    "model": "claude-fable-5[1m]",
                    "transcript_path": str(transcript),
                }
            ).decode("utf-8")
            self.assertEqual(
                self.run_cli(
                    ["session-event", "start", "--managed-id", stable_id], payload
                )[0],
                0,
            )
            code, output = self.run_cli(["sessions", "forget", FIXED_ID])
            self.assertEqual(code, 0)
            self.assertIn("Forgot", output)

    def test_link_updates_pointer_for_adopted_original_cwd(self) -> None:
        other = self.root / "original-project"
        other.mkdir()
        with mock.patch.object(
            cli, "_original_cwd_for_adopt", return_value=str(other)
        ):
            code, output = self.run_cli(
                ["sessions", "link", FIXED_ID, "--composition", "default"]
            )
        self.assertEqual(code, 0, output)
        record = self.runtime.session_store.resolve(FIXED_ID)
        self.assertEqual(record["cwd"], str(other))
        self.assertEqual(
            self.runtime.session_store.last(str(other)), record["managed_id"]
        )
        self.assertIsNone(self.runtime.session_store.last(self.runtime.cwd))

    def test_link_can_adopt_native_session_as_ordinary_gateway(self) -> None:
        with mock.patch.object(
            cli, "_original_cwd_for_adopt", return_value=self.runtime.cwd
        ):
            code, output = self.run_cli(
                ["sessions", "link", FIXED_ID, "--model", "qwen38"]
            )
        self.assertEqual(code, 0, output)
        self.assertIn("as ordinary", output)
        record = self.runtime.session_store.resolve(FIXED_ID)
        self.assertEqual(record["session_type"], sessions.SESSION_TYPE_ORDINARY)
        self.assertEqual(record["ordinary_model"], "qwen38")
        self.assertEqual(record["context_profile"], "large")
        self.assertEqual(record["mode"], "legacy")
        self.assertEqual(record["scope_generation"], 0)
        self.assertEqual(
            self.runtime.session_store.last(
                self.runtime.cwd, session_type=sessions.SESSION_TYPE_ORDINARY
            ),
            record["managed_id"],
        )
        self.assertIsNone(self.runtime.session_store.last(self.runtime.cwd))
        prepared = self.runtime.prepare_direct(
            action="resume",
            model_id=None,
            passthrough=[],
            session_id=record["managed_id"],
        )
        self.assertEqual(prepared.record["mode"], "durable")
        self.assertEqual(prepared.record["scope_generation"], 1)
        self.assertEqual(prepared.expected_source_scope_generation, 0)
        self.assertNotIn("--model", prepared.result.argv)

    def test_relink_runtime_repairs_existing_record_without_moving_scope_id(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        runtime_id = "22222222-2222-4222-8222-222222222222"
        repaired_cwd = self.root / "repaired-project"
        repaired_cwd.mkdir()
        code, output = self.run_cli(
            [
                "sessions",
                "relink-runtime",
                FIXED_ID,
                runtime_id,
                "--cwd",
                str(repaired_cwd),
            ]
        )
        self.assertEqual(code, 0, output)
        self.assertIn(f"managed {FIXED_ID}", output)
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["managed_id"], FIXED_ID)
        self.assertEqual(record["runtime_session_id"], runtime_id)
        self.assertEqual(record["cwd"], str(repaired_cwd))
        self.assertEqual(record["identity_state"], sessions.IDENTITY_AUTHORITATIVE)
        self.assertEqual(record["launch_epoch"], 1)
        self.assertRegex(record["mutation_token"], sessions.UUID4)
        self.assertIsNone(self.runtime.session_store.last(self.runtime.cwd))
        self.assertEqual(
            self.runtime.session_store.last(str(repaired_cwd)), FIXED_ID
        )

    def test_relink_runtime_ownership_failure_does_not_partially_commit_cwd(self) -> None:
        target = self.save_session(mode="durable", scope_generation=1)
        target["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        target["observed_cwd"] = "/wrong/project"
        self.runtime.session_store.save(target)
        owner_id = "33333333-3333-4333-8333-333333333333"
        owner = sessions.make_record(
            managed_id=owner_id,
            runtime_session_id=OTHER_ID,
            cwd=self.runtime.cwd,
            composition_name=target["composition_name"],
            snapshot=target["snapshot"],
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            mode="durable",
            scope_generation=1,
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )
        self.runtime.session_store.save(owner)
        before = self.runtime.session_store.read_record_bytes(FIXED_ID)
        repaired_cwd = self.root / "other-project"
        repaired_cwd.mkdir()
        code, output = self.run_cli(
            [
                "sessions",
                "relink-runtime",
                FIXED_ID,
                OTHER_ID,
                "--cwd",
                str(repaired_cwd),
            ]
        )
        self.assertEqual(code, 2, output)
        self.assertIn("already owned", output)
        self.assertEqual(self.runtime.session_store.read_record_bytes(FIXED_ID), before)

    def test_noninteractive_link_requires_composition(self) -> None:
        code, output = self.run_cli(["sessions", "link", FIXED_ID], interactive=False)
        self.assertEqual(code, 2)
        self.assertIn("requires --composition", output)

    def test_doctor_uses_injected_local_check(self) -> None:
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("Ready", output)
        self.assertEqual(self.launches, [])

    def test_doctor_reports_binary_and_daemon_info(self) -> None:
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0)
        self.assertIn("Managed Claude 2.1.217 verified (fixture).", output)
        self.assertIn("Shared daemon: fixture daemon absent.", output)

    def test_doctor_blocked_when_binary_verification_fails(self) -> None:
        self.runtime.doctor_binary_callback = lambda _contract: (
            ["managed Claude binary: inspected Claude artifact /x is missing; "
             "re-run the native-contract inspection against the installed version"],
            [],
        )
        code, output = self.run_cli(["doctor"], interactive=False)
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", output)
        self.assertNotIn("Ready", output)
        self.assertIn("managed Claude binary", output)
        self.assertIn("re-run the native-contract inspection", output)

    def test_doctor_binary_parity_uses_launch_resolver_by_default(self) -> None:
        runtime = cli.Runtime(
            asset_root=CATALOG_ROOT,
            environ=self.runtime.environ,
            cwd=self.root / "project",
        )
        self.assertIs(runtime.doctor_binary_callback, cli.launch.doctor_binary_report)
        self.assertIs(runtime.doctor_daemon_callback, cli.launch.inspect_shared_daemon)


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


class DoctorBinaryParityTests(CLITestCase):
    """Real launch resolver callback behind Doctor, temp fixture artifact."""

    def _fixture_runtime(self, sha256: str) -> cli.Runtime:
        install = self.root / "install" / "versions" / "2.1.217"
        install.parent.mkdir(parents=True, exist_ok=True)
        install.write_bytes(b"#!/bin/fake-claude\n")
        install.chmod(0o755)
        link = self.root / "bin" / "claude"
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(install)
        fixture = {
            "claude": {
                "validated_version": "2.1.217",
                "executable": {
                    "configured_path": str(link),
                    "resolved_path": str(install),
                    "sha256": sha256,
                    "inspection": "test fixture",
                    "inspected_at": "2026-07-22",
                },
            }
        }
        runtime = cli.Runtime(
            asset_root=CATALOG_ROOT,
            environ=self.runtime.environ,
            cwd=self.root / "project",
            doctor_callback=lambda _runtime: [],
            doctor_daemon_callback=lambda: cli.launch.DaemonStatus(
                state="absent", summary="fixture daemon absent"
            ),
        )
        # Real callback (the Runtime default) against the fixture contract.
        self.assertIs(runtime.doctor_binary_callback, cli.launch.doctor_binary_report)
        runtime.catalog.docs["native-contract"] = fixture
        return runtime

    def test_doctor_ready_with_intact_fixture_artifact(self) -> None:
        import hashlib

        sha256 = hashlib.sha256(b"#!/bin/fake-claude\n").hexdigest()
        runtime = self._fixture_runtime(sha256)
        output = io.StringIO()
        code = cli.main(
            ["doctor"],
            runtime=runtime,
            output_stream=output,
            interactive=False,
        )
        self.assertEqual(code, 0)
        self.assertIn("Ready", output.getvalue())
        self.assertIn("Managed Claude 2.1.217 verified", output.getvalue())
        self.assertIn("resolves to the inspected artifact", output.getvalue())

    def test_doctor_blocked_with_tampered_fixture_hash(self) -> None:
        runtime = self._fixture_runtime("0" * 64)
        output = io.StringIO()
        code = cli.main(
            ["doctor"],
            runtime=runtime,
            output_stream=output,
            interactive=False,
        )
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", output.getvalue())
        self.assertNotIn("Ready", output.getvalue())
        # Same failure text launch would raise: parity, not a parallel check.
        self.assertIn("content hash does not match", output.getvalue())
        self.assertIn("re-run the native-contract inspection", output.getvalue())


class BareLaunchStreamTests(unittest.TestCase):
    """_open_tty_streams resolution: prefer /dev/tty, fall back to real TTYs."""

    def test_prefers_dev_tty_with_read_write_handles(self) -> None:
        opened: list[tuple] = []

        def fake_open(path, mode, **kwargs):
            opened.append((path, mode))
            return object() if mode == "r" else object()

        with mock.patch("builtins.open", side_effect=fake_open):
            inp, out = cli._open_tty_streams()
        self.assertEqual(
            opened,
            [("/dev/tty", "r"), ("/dev/tty", "w")],
        )
        self.assertIsNot(inp, out)

    def test_output_handle_failure_closes_input_and_falls_back(self) -> None:
        closed = []

        class Handle:
            def close(self):
                closed.append(True)

        def fake_open(path, mode, **kwargs):
            if mode == "w":
                raise OSError("cannot open for writing")
            return Handle()

        with mock.patch("builtins.open", side_effect=fake_open):
            with mock.patch.object(cli, "_stdio_streams_are_ttys", return_value=True):
                inp, out = cli._open_tty_streams()
        self.assertEqual(closed, [True])
        self.assertIs(inp, cli.sys.stdin)

    def test_falls_back_to_stdio_when_both_are_ttys(self) -> None:
        with mock.patch("builtins.open", side_effect=OSError("ENXIO")):
            with mock.patch("sys.stdin") as fake_in, mock.patch("sys.stdout") as fake_out:
                fake_in.isatty.return_value = True
                fake_out.isatty.return_value = True
                inp, out = cli._open_tty_streams()
        self.assertIs(inp, fake_in)
        self.assertIs(out, fake_out)

    def test_fails_closed_when_stdout_not_tty(self) -> None:
        with mock.patch("builtins.open", side_effect=OSError("ENXIO")):
            with mock.patch("sys.stdin") as fake_in, mock.patch("sys.stdout") as fake_out:
                fake_in.isatty.return_value = True
                fake_out.isatty.return_value = False
                with self.assertRaisesRegex(cli.CLIError, "no interactive terminal"):
                    cli._open_tty_streams()

    def test_fails_closed_when_stdin_not_tty(self) -> None:
        with mock.patch("builtins.open", side_effect=OSError("ENXIO")):
            with mock.patch("sys.stdin") as fake_in, mock.patch("sys.stdout") as fake_out:
                fake_in.isatty.return_value = False
                fake_out.isatty.return_value = True
                with self.assertRaisesRegex(cli.CLIError, "no interactive terminal"):
                    cli._open_tty_streams()


class StdioProbeTests(unittest.TestCase):
    def test_none_streams_fail_closed(self) -> None:
        with mock.patch("builtins.open", side_effect=OSError("ENXIO")):
            with mock.patch.object(cli.sys, "stdin", None), mock.patch.object(
                cli.sys, "stdout", None
            ):
                with self.assertRaisesRegex(cli.CLIError, "no interactive terminal"):
                    cli._open_tty_streams()

    def test_closed_stream_probe_fails_closed(self) -> None:
        with mock.patch("builtins.open", side_effect=OSError("ENXIO")):
            with mock.patch("sys.stdin") as fake_in, mock.patch("sys.stdout") as fake_out:
                fake_in.isatty.side_effect = ValueError("I/O operation on closed file")
                with self.assertRaisesRegex(cli.CLIError, "no interactive terminal"):
                    cli._open_tty_streams()

    def test_probe_oserror_fails_closed(self) -> None:
        with mock.patch("builtins.open", side_effect=OSError("ENXIO")):
            with mock.patch("sys.stdin") as fake_in, mock.patch("sys.stdout") as fake_out:
                fake_in.isatty.side_effect = OSError("inappropriate ioctl")
                with self.assertRaisesRegex(cli.CLIError, "no interactive terminal"):
                    cli._open_tty_streams()

    def test_probe_attribute_error_fails_closed(self) -> None:
        with mock.patch("builtins.open", side_effect=OSError("ENXIO")):
            with mock.patch.object(cli.sys, "stdin", object()), mock.patch.object(
                cli.sys, "stdout", object()
            ):
                with self.assertRaisesRegex(cli.CLIError, "no interactive terminal"):
                    cli._open_tty_streams()


def _hide_transition_engine(test_case) -> None:
    """Simulate a build without Lane D's module (lazy import must fail closed).

    ``from . import transition`` resolves the parent package attribute first,
    so simulating absence requires removing both the attribute and the
    sys.modules entry.
    """

    import claude_multi

    if hasattr(claude_multi, "transition"):
        saved = claude_multi.transition
        del claude_multi.transition
        test_case.addCleanup(setattr, claude_multi, "transition", saved)
    patcher = mock.patch.dict(sys.modules, {"claude_multi.transition": None})
    test_case.addCleanup(patcher.stop)
    patcher.start()


class QuickConfirmVisibilityTests(CLITestCase):
    """UX §1 quick-confirm badges: durability, workflow, policy, project."""

    def test_fresh_plan_shows_all_badges(self) -> None:
        code, output = self.run_cli([], "q\n")
        self.assertEqual(code, 0)
        self.assertIn("durable scope (per-session files)", output)
        self.assertIn("workflows native", output)
        self.assertIn(
            "Policy         Explore→cm-analyst · Plan native · "
            "general-purpose off · generic denied",
            output,
        )
        self.assertIn("Project", output)
        self.assertIn("none colliding", output)

    def test_workflows_off_badge_and_panel(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "off-comp"
        document["workflows"] = "off"
        self.runtime.compositions.save(document)
        code, output = self.run_cli(["--composition", "off-comp"], "?\nq\n")
        self.assertEqual(code, 0)
        self.assertIn("workflows off", output)
        self.assertIn("Native workflows (ultracode): OFF", output)
        self.assertIn("disableWorkflows:true compiled", output)

    def test_question_mark_prints_guarantee_panel_without_launching(self) -> None:
        code, output = self.run_cli([], "?\nq\n")
        self.assertEqual(code, 0)
        self.assertIn("Native workflows (ultracode): ON", output)
        self.assertIn("never counts", output)
        self.assertIn("no cm-role contract", output)
        self.assertIn("? workflows", output)
        self.assertEqual(self.launches, [])

    def test_project_agents_counted_without_collision(self) -> None:
        agents = Path(self.runtime.cwd) / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "helper.md").write_bytes(
            b"---\nname: helper\ndescription: project-local\n---\nbody\n"
        )
        (agents / "notes.md").write_bytes(b"no frontmatter\n")
        code, output = self.run_cli([], "q\n")
        self.assertEqual(code, 0)
        self.assertIn("2 project agents discovered (native precedence; none colliding)", output)

    def test_cm_collision_blocks_with_exact_error_language(self) -> None:
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        variant_id = resolved.variants[0].id
        agents = Path(self.runtime.cwd) / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / f"{variant_id}.md").write_bytes(
            f"---\nname: {variant_id}\ndescription: shadow\n---\nbody\n".encode()
        )
        code, output = self.run_cli([], "\n0\nq\n")
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])
        self.assertIn("Status         BLOCKED", output)
        flat = re.sub(r"\s+", " ", output)
        self.assertIn(
            f"project agent {variant_id!r} at .claude/agents/{variant_id}.md "
            "collides with the managed cm-* namespace. Rename or remove it, "
            "or launch from a different directory.",
            flat,
        )

    def test_resume_collision_preflight_uses_recorded_cwd(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        recorded_cwd = self.root / "recorded-project"
        recorded_cwd.mkdir()
        record["cwd"] = str(recorded_cwd)
        self.runtime.session_store.save(record)
        variant_id = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        ).variants[0].id

        invocation_agents = Path(self.runtime.cwd) / ".claude" / "agents"
        invocation_agents.mkdir(parents=True)
        (invocation_agents / f"{variant_id}.md").write_text(
            f"---\nname: {variant_id}\ndescription: irrelevant\n---\nbody\n"
        )
        plan = cli.managed_plan(self.runtime, record)
        self.assertTrue(plan.ready, plan.errors)

        recorded_agents = recorded_cwd / ".claude" / "agents"
        recorded_agents.mkdir(parents=True)
        (recorded_agents / f"{variant_id}.md").write_text(
            f"---\nname: {variant_id}\ndescription: collision\n---\nbody\n"
        )
        plan = cli.managed_plan(self.runtime, record)
        self.assertFalse(plan.ready)
        self.assertTrue(any("collides" in error for error in plan.errors))

    def test_legacy_record_resume_shows_upgrade_note(self) -> None:
        self.save_session()
        code, output = self.run_cli(["-r", FIXED_ID], "q\n")
        self.assertEqual(code, 0)
        self.assertIn("legacy argv record · resume upgrades to durable scope", output)
        flat = re.sub(r"\s+", " ", output)
        self.assertIn("this session predates durable scopes; resuming will "
                      "attach a scope and keep the transcript.", flat)

    def test_durable_record_shows_generation(self) -> None:
        self.save_session(mode="durable", scope_generation=2)
        code, output = self.run_cli(["-r", FIXED_ID], "q\n")
        self.assertEqual(code, 0)
        self.assertIn("durable scope (per-session files) · generation 2", output)
        self.assertNotIn("predates durable scopes", output)


class SessionsScreenTests(CLITestCase):
    """UX §4 sessions screen: mode column, per-row actions, forget honesty."""

    def test_list_shows_mode_column_actions_and_help(self) -> None:
        self.save_session(session_id=FIXED_ID, mode="durable", scope_generation=2)
        self.save_session(session_id=OTHER_ID)
        code, output = self.run_cli(["sessions", "list"])
        self.assertEqual(code, 0)
        self.assertIn(f"{FIXED_ID}  cm:default  durable(g2)", output)
        self.assertIn(f"{OTHER_ID}  cm:default  legacy", output)
        durable_row = next(line for line in output.splitlines() if FIXED_ID in line)
        legacy_row = next(line for line in output.splitlines() if OTHER_ID in line)
        self.assertIn("[r]esume [t]ransition [f]orget", durable_row)
        self.assertIn("[r]esume (upgrades to durable) [f]orget", legacy_row)
        self.assertIn("actions: [r]esume", output)
        self.assertIn("[t]ransition `claude-multi sessions transition <uuid>", output)
        self.assertIn("transcripts are never touched", output)

    def test_empty_list_keeps_header_and_help(self) -> None:
        code, output = self.run_cli(["sessions", "list"])
        self.assertEqual(code, 0)
        self.assertIn("(no recorded sessions)", output)
        self.assertIn("[f]orget", output)

    def _write_scope(self, session_id: str) -> None:
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        plan = scope_mod.compile_scope(
            resolved,
            self.runtime.catalog.docs["roles"]["roles"],
            self.runtime.catalog.prompt_bodies,
            scope_mod.catalog_meta_from_docs(self.runtime.catalog.docs),
        )
        scope_mod.write_scope(self.runtime.session_store.root, session_id, plan)

    def test_forget_states_exact_deletion_and_removes_scope(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        live = scope_mod.scope_dir(self.runtime.session_store.root, FIXED_ID)
        self.assertTrue(live.is_dir())
        code, output = self.run_cli(["sessions", "forget", FIXED_ID])
        self.assertEqual(code, 0)
        self.assertIn("Forgot", output)
        self.assertIn("session record + generated scope", output)
        self.assertIn("Transcripts are never touched", output)
        self.assertFalse(live.exists())
        self.assertFalse(self.runtime.session_store.exists(FIXED_ID))

    def test_forget_without_scope_says_so(self) -> None:
        self.save_session()
        code, output = self.run_cli(["sessions", "forget", FIXED_ID])
        self.assertEqual(code, 0)
        self.assertIn("Forgot", output)
        self.assertIn("no generated scope existed", output)
        self.assertIn("Transcripts are never touched", output)

    def test_forget_not_found_unchanged(self) -> None:
        code, output = self.run_cli(["sessions", "forget", FIXED_ID])
        self.assertEqual(code, 0)
        self.assertIn("Not found", output)


class TransitionCommandTests(CLITestCase):
    """TRANSITIONS §3 CLI flow against Lane D's real transition module."""

    def _shifted_composition(self):
        document = self.runtime.compositions.load("default")
        document["name"] = "shifted"
        document["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(document)
        return document

    def _write_scope(self, session_id: str) -> None:
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        plan = scope_mod.compile_scope(
            resolved,
            self.runtime.catalog.docs["roles"]["roles"],
            self.runtime.catalog.prompt_bodies,
            scope_mod.catalog_meta_from_docs(self.runtime.catalog.docs),
        )
        scope_mod.write_scope(self.runtime.session_store.root, session_id, plan)

    def test_transition_rejects_invalid_uuid(self) -> None:
        code, output = self.run_cli(
            ["sessions", "transition", "not-a-uuid", "--composition", "default"]
        )
        self.assertEqual(code, 2)
        self.assertIn("not a UUIDv4", output)

    def test_transition_legacy_record_is_actionable(self) -> None:
        self.save_session()
        code, output = self.run_cli(
            ["sessions", "transition", FIXED_ID, "--composition", "default"],
            "y\n",
        )
        self.assertEqual(code, 2)
        self.assertIn("v1 transitions require a durable session", output)
        self.assertIn("resume it once to upgrade", output)
        self.assertEqual(self.launches, [])

    def test_declined_confirmation_prints_diff_and_exact_command(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self._shifted_composition()
        code, output = self.run_cli(
            ["sessions", "transition", FIXED_ID, "--composition", "shifted"],
            "n\n",
        )
        self.assertEqual(code, 0)
        self.assertIn("lead model: fable -> sol", output)
        self.assertIn("EXITED (not merely idle)", output)
        self.assertIn(
            f"claude-multi sessions transition {FIXED_ID} --composition shifted",
            output,
        )
        self.assertEqual(self.launches, [])
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["scope_generation"], 1)
        self.assertEqual(record["composition_name"], "default")

    def test_confirmed_transition_relaunches_and_bumps_generation(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self._shifted_composition()
        code, output = self.run_cli(
            ["sessions", "transition", FIXED_ID, "--composition", "shifted"],
            "y\n",
        )
        self.assertEqual(code, 0, output)
        self.assertIn("lead model: fable -> sol", output)
        self.assertEqual(len(self.launches), 1)
        launched = self.launches[0]
        self.assertTrue(launched.result.durable)
        self.assertEqual(launched.record["scope_generation"], 2)
        self.assertEqual(launched.record["composition_name"], "shifted")
        # The record execute() saved is what the relaunch persists.
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["scope_generation"], 2)
        self.assertEqual(record["composition_name"], "shifted")
        scopes_root = self.runtime.session_store.root / "scopes"
        self.assertTrue((scopes_root / FIXED_ID).is_dir())
        # The prior generation is retained for rollback.
        self.assertTrue((scopes_root / f".{FIXED_ID}.prev").is_dir())

    def test_transition_accepts_authoritative_runtime_uuid(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["runtime_session_id"] = OTHER_ID
        record["identity_state"] = sessions.IDENTITY_AUTHORITATIVE
        self.runtime.session_store.save(record)
        self._write_scope(FIXED_ID)
        code, output = self.run_cli(
            [
                "sessions",
                "transition",
                OTHER_ID,
                "--composition",
                "default",
                "--its-exited",
            ],
            interactive=False,
        )
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].record["managed_id"], FIXED_ID)

    def test_its_exited_flag_skips_prompt_and_relaunches(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        code, output = self.run_cli(
            [
                "sessions", "transition", FIXED_ID,
                "--composition", "default", "--its-exited",
            ],
            interactive=False,
        )
        self.assertEqual(code, 0, output)
        self.assertIn("no semantic composition changes", output)
        self.assertNotIn("[y/N]", output)
        self.assertEqual(len(self.launches), 1)

    def test_noninteractive_without_flag_never_mutates(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        code, output = self.run_cli(
            ["sessions", "transition", FIXED_ID, "--composition", "default"],
            interactive=False,
        )
        self.assertEqual(code, 0)
        self.assertIn(
            f"claude-multi sessions transition {FIXED_ID} --composition default",
            output,
        )
        self.assertEqual(self.launches, [])
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["scope_generation"], 1
        )

    def test_from_inside_target_session_is_always_print_only(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self.runtime.environ["CLAUDE_MULTI_SESSION_ID"] = FIXED_ID
        code, output = self.run_cli(
            [
                "sessions", "transition", FIXED_ID,
                "--composition", "default", "--its-exited",
            ],
            interactive=False,
        )
        self.assertEqual(code, 0)
        self.assertIn(
            f"claude-multi sessions transition {FIXED_ID} --composition default",
            output,
        )
        self.assertEqual(self.launches, [])
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["scope_generation"], 1
        )

    def test_exec_failure_restores_prior_generation_and_record(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self._shifted_composition()

        def failing_launch(_prepared):
            raise OSError("simulated execve failure")

        self.runtime.launch_callback = failing_launch
        code, output = self.run_cli(
            ["sessions", "transition", FIXED_ID, "--composition", "shifted"],
            "y\n",
        )
        self.assertEqual(code, 2)
        self.assertIn("relaunch exec failed", output)
        self.assertIn("prior scope generation and record were restored", output)
        self.assertIn("Retry with:", output)
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["scope_generation"], 1)
        self.assertEqual(record["composition_name"], "default")
        scopes_root = self.runtime.session_store.root / "scopes"
        self.assertTrue((scopes_root / FIXED_ID).is_dir())
        self.assertFalse((scopes_root / f".{FIXED_ID}.prev").exists())

    def test_non_oserror_launch_failure_restores_prior_transition(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self._shifted_composition()
        self.runtime.launch_callback = lambda _prepared: (_ for _ in ()).throw(
            cli.launch.LaunchError("gateway disappeared")
        )
        code, output = self.run_cli(
            ["sessions", "transition", FIXED_ID, "--composition", "shifted"],
            "y\n",
        )
        self.assertEqual(code, 2)
        self.assertIn("relaunch launch failed", output)
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["scope_generation"], 1)
        self.assertEqual(record["composition_name"], "default")

    def test_transition_unavailable_without_engine(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        _hide_transition_engine(self)
        code, output = self.run_cli(
            ["sessions", "transition", FIXED_ID, "--composition", "default"]
        )
        self.assertEqual(code, 2)
        self.assertIn("transitions are unavailable", output)
        self.assertIn("claude_multi.transition", output)


class DoctorVisibilityTests(CLITestCase):
    """SPEC §7 doctor additions: scope integrity, prune, collisions, evidence."""

    def _write_scope(self, session_id: str) -> None:
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        plan = scope_mod.compile_scope(
            resolved,
            self.runtime.catalog.docs["roles"]["roles"],
            self.runtime.catalog.prompt_bodies,
            scope_mod.catalog_meta_from_docs(self.runtime.catalog.docs),
            managed_id=session_id,
            hook_command=self.runtime.hook_command,
            launch_epoch=(
                self.runtime.session_store.load(session_id).get("launch_epoch", 0)
                if self.runtime.session_store.exists(session_id)
                else 0
            ),
        )
        scope_mod.write_scope(self.runtime.session_store.root, session_id, plan)
        return plan

    def test_sessions_scope_collisions_and_evidence_lines(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        plan = self._write_scope(FIXED_ID)
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0, output)
        self.assertIn("Sessions: 1 recorded · 1 durable · 0 legacy.", output)
        self.assertIn(
            f"Scope: {FIXED_ID[:8]}… OK ({len(plan.agent_files) + 1} files, gen 1).",
            output,
        )
        self.assertIn("Collisions: none (0 project agents).", output)
        self.assertIn(
            "Evidence: add-dir carry: backgrounding documented · "
            "takeover binary-consistent, acceptance-pending(U1)",
            output,
        )

    def test_missing_scope_blocks_with_repair_guidance(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", output)
        self.assertIn(f"scope files for {FIXED_ID[:8]}… are missing (0/", output)
        self.assertIn(f"doctor --repair {FIXED_ID}", output)

    def test_drifted_scope_reports_record_scope_mismatch(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        plan = self._write_scope(FIXED_ID)
        target = next(iter(sorted(plan.agent_files)))
        tampered = (
            scope_mod.scope_dir(self.runtime.session_store.root, FIXED_ID) / target
        )
        state.atomic_write(tampered, b"tampered body\n")
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 1)
        self.assertIn("record↔scope mismatch", output)
        self.assertIn(f"doctor --repair {FIXED_ID}", output)

    def test_legacy_sessions_have_no_scope_check(self) -> None:
        self.save_session()
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0, output)
        self.assertIn("Sessions: 1 recorded · 0 durable · 1 legacy.", output)
        self.assertNotIn("Scope:", output)

    def test_legacy_context_snapshot_blocks_compaction_diagnostics(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        for key in (
            "client_context_tokens",
            "provider_context_tokens",
            "auto_compact_tokens",
        ):
            record["snapshot"]["lead"].pop(key, None)
        record["snapshot"].pop("auto_compact_window_tokens", None)
        self.runtime.session_store.save(record)
        self._write_scope(FIXED_ID)
        code, output = self.run_cli(["doctor"])
        # By-design lazy state: attention tier, never a BLOCKED verdict; the
        # printed guidance names the in-place repair.
        self.assertEqual(code, 0, output)
        self.assertIn("legacy context snapshot missing", output)
        self.assertIn("auto_compact_window_tokens", output)
        self.assertIn("doctor --repair-all", output)
        self.assertNotIn("BLOCKED", output)

    def test_corrupt_session_record_blocks_doctor_and_is_counted(self) -> None:
        path = self.runtime.session_store.sessions_dir / f"{OTHER_ID}.json"
        state.atomic_write(path, b"{not-json\n")
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 1)
        self.assertIn(
            "Sessions: 1 recorded · 0 durable · 0 legacy · 1 unreadable.",
            output,
        )
        self.assertIn(f"session record {OTHER_ID} is unreadable", output)
        self.assertIn("corrupt session record", output)

    def test_prune_removes_staging_and_forgotten_scopes_only(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self._write_scope(OTHER_ID)  # no record: forgotten
        scopes_root = self.runtime.session_store.root / "scopes"
        staging_prev = scopes_root / f".{FIXED_ID}.prev"
        state.ensure_private_dir(staging_prev)
        state.atomic_write(staging_prev / "settings.json", b"{}\n")
        staging_new = scopes_root / f".{OTHER_ID}.new"
        state.ensure_private_dir(staging_new)
        staging_forgotten_prev = scopes_root / f".{OTHER_ID}.prev"
        state.ensure_private_dir(staging_forgotten_prev)
        code, output = self.run_cli(["doctor", "--prune"])
        self.assertEqual(code, 0, output)
        self.assertIn("Pruned:", output)
        self.assertNotIn(f"stale staging dir scopes/.{FIXED_ID}.prev", output)
        self.assertIn(f"stale staging dir scopes/.{OTHER_ID}.new", output)
        self.assertIn(f"stale staging dir scopes/.{OTHER_ID}.prev", output)
        self.assertIn(f"scope for forgotten session {OTHER_ID}", output)
        # A scope and transition rollback directory with a living record are
        # never touched; both may be needed between transition commit and exec.
        self.assertTrue(
            scope_mod.scope_dir(self.runtime.session_store.root, FIXED_ID).is_dir()
        )
        self.assertTrue(staging_prev.is_dir())
        self.assertTrue(self.runtime.session_store.exists(FIXED_ID))

    def test_prune_without_stale_scopes_is_a_noop(self) -> None:
        code, output = self.run_cli(["doctor", "--prune"])
        self.assertEqual(code, 0)
        self.assertIn("nothing stale", output)

    def test_prune_rejects_symlinked_scopes_root_without_following(self) -> None:
        scopes_root = self.runtime.session_store.root / "scopes"
        if scopes_root.exists():
            shutil.rmtree(scopes_root)
        outside = self.root / "outside-scopes"
        outside.mkdir()
        sentinel = outside / "keep.txt"
        sentinel.write_text("keep", encoding="utf-8")
        scopes_root.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(state.StateError, "is a symlink"):
            cli._doctor_prune(self.runtime, io.StringIO())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_collision_scan_blocks_with_fail_closed_list(self) -> None:
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        variant_id = resolved.variants[0].id
        agents = Path(self.runtime.cwd) / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / f"{variant_id}.md").write_bytes(
            f"---\nname: {variant_id}\n---\nbody\n".encode()
        )
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 1)
        self.assertIn("Collisions: 1 blocking (1 project agent).", output)
        self.assertIn("collides with the managed cm-* namespace", output)

    def test_repair_recompiles_missing_scope_from_record(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        live = scope_mod.scope_dir(self.runtime.session_store.root, FIXED_ID)
        self.assertFalse(live.exists())
        code, output = self.run_cli(["doctor", "--repair", FIXED_ID])
        self.assertEqual(code, 0, output)
        self.assertIn("record generation 1 is authoritative", output)
        self.assertIn("live scope was missing; recompiled", output)
        self.assertTrue(live.is_dir())
        # After repair the integrity check passes.
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0, output)
        self.assertIn("OK (", output)

    def test_ordinary_repair_preserves_launch_epoch_in_hooks(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            launch_epoch=7,
        )
        self.runtime.session_store.save(record)
        code, output = self.run_cli(["doctor", "--repair", FIXED_ID])
        self.assertEqual(code, 0, output)
        live = scope_mod.scope_dir(self.runtime.session_store.root, FIXED_ID)
        settings = strict_json.load(live / "settings.json")
        self.assertEqual(settings["env"]["CLAUDE_MULTI_LAUNCH_EPOCH"], "7")
        start = settings["hooks"]["SessionStart"][0]["hooks"][0]["command"]
        end = settings["hooks"]["SessionEnd"][0]["hooks"][0]["command"]
        self.assertIn("--launch-epoch 7", start)
        self.assertIn("--launch-epoch 7", end)

    def test_repair_converges_drifted_scope(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        plan = self._write_scope(FIXED_ID)
        target = next(iter(sorted(plan.agent_files)))
        tampered = (
            scope_mod.scope_dir(self.runtime.session_store.root, FIXED_ID) / target
        )
        state.atomic_write(tampered, b"tampered body\n")
        code, output = self.run_cli(["doctor", "--repair", FIXED_ID])
        self.assertEqual(code, 0, output)
        self.assertIn("live scope drifted from record authority", output)
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0, output)
        self.assertIn("OK (", output)

    def test_repair_legacy_record_is_actionable(self) -> None:
        self.save_session()
        code, output = self.run_cli(["doctor", "--repair", FIXED_ID])
        self.assertEqual(code, 2)
        self.assertIn("resume a legacy session to upgrade it first", output)

    def test_repair_unavailable_without_engine(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        _hide_transition_engine(self)
        code, output = self.run_cli(["doctor", "--repair", FIXED_ID])
        self.assertEqual(code, 2)
        self.assertIn("transitions are unavailable", output)

    def test_repair_all_converges_scopes_and_refreshes_records(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        # Simulate a pre-context-fields record: legacy snapshot + stale scope.
        for key in (
            "client_context_tokens",
            "provider_context_tokens",
            "auto_compact_tokens",
        ):
            record["snapshot"]["lead"].pop(key, None)
        record["snapshot"].pop("auto_compact_window_tokens", None)
        self.runtime.session_store.save(record)
        self._write_scope(FIXED_ID)
        stale = (
            scope_mod.scope_dir(self.runtime.session_store.root, FIXED_ID)
            / "settings.json"
        )
        state.atomic_write(stale, b'{"stale": true}\n')
        code, output = self.run_cli(["doctor", "--repair-all"])
        self.assertEqual(code, 0, output)
        self.assertIn("1 durable session(s) converged", output)
        self.assertIn("record snapshot refreshed", output)
        refreshed = self.runtime.session_store.load(FIXED_ID)
        self.assertIn("client_context_tokens", refreshed["snapshot"]["lead"])
        self.assertIn("auto_compact_window_tokens", refreshed["snapshot"])
        self.assertEqual(refreshed["scope_generation"], 1)
        # The same composition: lead model and variants are unchanged.
        self.assertEqual(
            refreshed["snapshot"]["lead"]["model"], record["snapshot"]["lead"]["model"]
        )
        # Doctor is now clean for this session: no attention, no problem.
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0, output)
        self.assertNotIn("legacy context snapshot", output)
        self.assertNotIn("BLOCKED", output)

    def test_repair_all_skips_legacy_and_reports_unreadable(self) -> None:
        self.save_session()  # legacy mode
        path = self.runtime.session_store.sessions_dir / f"{OTHER_ID}.json"
        state.atomic_write(path, b"{not-json\n")
        code, output = self.run_cli(["doctor", "--repair-all"])
        self.assertEqual(code, 1)
        self.assertIn("skipped", output)
        self.assertIn("resume it once to upgrade", output)
        self.assertIn("FAILED", output)
        self.assertIn("unreadable", output)

    def test_repair_all_handles_ordinary_records(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            launch_epoch=2,
        )
        self.runtime.session_store.save(record)
        live = scope_mod.scope_dir(self.runtime.session_store.root, FIXED_ID)
        self.assertFalse(live.exists())
        code, output = self.run_cli(["doctor", "--repair-all"])
        self.assertEqual(code, 0, output)
        settings = strict_json.load(live / "settings.json")
        self.assertEqual(settings["env"]["CLAUDE_MULTI_LAUNCH_EPOCH"], "2")
        self.assertEqual(settings["model"], "claude-multi-qwen38-max[1m]")
        self.assertNotIn("permissions", settings)
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0, output)

    def test_refresh_record_snapshot_refuses_composition_change(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        other = copy.deepcopy(record["snapshot"])
        other["lead"] = {**other["lead"], "model": "qwen38"}
        with self.assertRaisesRegex(sessions.SessionError, "composition change"):
            sessions.refresh_record_snapshot(
                record,
                snapshot=other,
                catalog_version=self.runtime.catalog_version,
                catalog_hash=self.runtime.catalog.bundle_sha256,
                launcher_version=self.runtime.launcher_version,
            )


class EditorWorkflowsTests(unittest.TestCase):
    """UX §2: workflows row in the composition editor with the `?` panel."""

    def _state(self, document=None) -> EditorState:
        from claude_multi import catalog as catalog_mod

        bundle = catalog_mod.load_catalog(
            Path(__file__).resolve().parents[1]
        )
        return EditorState(
            bundle.docs,
            document or bundle.default_composition,
            bundle.default_composition,
        )

    def test_set_workflows_roundtrip_keeps_native_document_minimal(self) -> None:
        state = self._state()
        self.assertEqual(state.workflows, "native")
        self.assertNotIn("workflows", state.document)
        state.set_workflows("off")
        self.assertEqual(state.document["workflows"], "off")
        self.assertTrue(state.dirty)
        state.set_workflows("native")
        self.assertNotIn("workflows", state.document)

    def test_set_workflows_rejects_unknown_mode(self) -> None:
        state = self._state()
        with self.assertRaisesRegex(EditorError, "invalid workflows mode"):
            state.set_workflows("managed")

    def test_guarantee_panel_modes(self) -> None:
        native = workflow_guarantee_panel("native")
        self.assertIn("Native workflows (ultracode): ON", native)
        self.assertNotIn("disableWorkflows:true", native)
        off = workflow_guarantee_panel("off")
        self.assertIn("Native workflows (ultracode): OFF", off)
        self.assertIn("disableWorkflows:true compiled", off)
        self.assertIn('presents off as "safer subagents"', off)


class SnapshotWorkflowsTests(CLITestCase):
    def test_snapshot_to_document_preserves_workflows_off(self) -> None:
        document = self.runtime.compositions.load("default")
        document["workflows"] = "off"
        self.save_session(document)
        record = self.runtime.session_store.load(FIXED_ID)
        rebuilt = cli.snapshot_to_document(record)
        self.assertEqual(rebuilt["workflows"], "off")
        resolved = self.runtime.resolve_document(rebuilt)
        self.assertEqual(resolved.workflows, "off")

    def test_snapshot_to_document_omits_native_workflows(self) -> None:
        self.save_session()
        record = self.runtime.session_store.load(FIXED_ID)
        rebuilt = cli.snapshot_to_document(record)
        self.assertNotIn("workflows", rebuilt)


class DurableDefaultWiringTests(CLITestCase):
    """Lead-integrator tests: durable-by-default launch selection + --legacy."""

    def _prepared(self, argv, text="\n", interactive=True):
        code, _out = self.run_cli(argv, text, interactive=interactive)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        return self.launches[0]

    def test_fresh_launch_is_durable(self) -> None:
        prepared = self._prepared(["--composition", "default"])
        self.assertTrue(prepared.result.durable)
        argv = prepared.result.argv
        self.assertIn("--add-dir", argv)
        self.assertNotIn("--agents", argv)
        self.assertNotIn("--disallowedTools", argv)
        settings_path = argv[argv.index("--settings") + 1]
        self.assertIn("settings.json", settings_path)
        self.assertIn("scopes", settings_path)
        self.assertEqual(prepared.record["mode"], "durable")
        self.assertEqual(prepared.record["scope_generation"], 1)
        self.assertEqual(prepared.record["workflows"], "native")

    def test_fresh_launch_legacy_flag_keeps_argv_mode(self) -> None:
        prepared = self._prepared(["--composition", "default", "--legacy"])
        self.assertFalse(prepared.result.durable)
        self.assertIn("--agents", prepared.result.argv)
        self.assertEqual(prepared.record["mode"], "legacy")
        self.assertEqual(prepared.record["scope_generation"], 0)

    def test_resume_legacy_record_upgrades_to_durable(self) -> None:
        self.save_session(mode="legacy", scope_generation=0)
        prepared = self._prepared(["-r", FIXED_ID])
        self.assertTrue(prepared.result.durable)
        self.assertEqual(prepared.record["mode"], "durable")
        self.assertEqual(prepared.record["scope_generation"], 1)

    def test_resume_durable_record_preserves_generation(self) -> None:
        self.save_session(mode="durable", scope_generation=3)
        prepared = self._prepared(["-r", FIXED_ID])
        self.assertTrue(prepared.result.durable)
        self.assertEqual(prepared.record["scope_generation"], 3)

    def test_legacy_resume_of_durable_record_keeps_lineage(self) -> None:
        self.save_session(mode="durable", scope_generation=2)
        prepared = self._prepared(["-r", FIXED_ID, "--legacy"])
        self.assertFalse(prepared.result.durable)
        self.assertIn("--agents", prepared.result.argv)
        # Only this launch is argv-mode; the durable lineage is preserved.
        self.assertEqual(prepared.record["mode"], "durable")
        self.assertEqual(prepared.record["scope_generation"], 2)

    def test_badge_legacy_fresh(self) -> None:
        document = self.runtime.compositions.load("default")
        plan = cli.build_quick_plan(
            self.runtime, document, action="fresh", source="test"
        )
        plan.legacy_requested = True
        self.assertEqual(
            cli._durability_badge(plan), "legacy argv (--legacy compatibility hatch)"
        )

    def test_badge_legacy_resume_durable(self) -> None:
        self.save_session(mode="durable", scope_generation=4)
        record = self.runtime.session_store.load(FIXED_ID)
        plan = cli.managed_plan(self.runtime, record)
        plan.legacy_requested = True
        self.assertEqual(
            cli._durability_badge(plan),
            "legacy argv (--legacy) · durable scope retained on disk",
        )


class SessionStoreUuidGuardTests(CLITestCase):
    def test_load_rejects_non_uuid(self) -> None:
        with self.assertRaises(sessions.SessionError):
            self.runtime.session_store.load("../escape")

    def test_forget_rejects_non_uuid(self) -> None:
        with self.assertRaises(sessions.SessionError):
            self.runtime.session_store.forget("..%2fbad")


class QuickConfirmTuiScreenTests(CLITestCase):
    """The curses quick-confirm card, driven via the FakeWindow double."""

    def _plan(self, document=None, **kwargs):
        document = document or self.runtime.compositions.load("default")
        return cli.build_quick_plan(
            self.runtime, document, action="fresh", source="Trusted default", **kwargs
        )

    def _run(self, plan, keys, passthrough=None):
        from test_tui import FakeWindow

        screen = cli._QuickConfirmScreen(
            self.runtime, plan, passthrough=passthrough or [], palette=tui.MONO_PALETTE
        )
        win = FakeWindow(keys)
        result = screen.run(win)
        return result, win

    def test_card_shows_badges_table_policy_and_project(self) -> None:
        result, win = self._run(self._plan(), ["q"])
        self.assertIsNone(result)
        text = win.text()
        self.assertIn("composition: default", text)
        self.assertIn("workflows: native", text)
        self.assertIn("durable scope (per-session files)", text)
        self.assertIn("Explore→cm-analyst · Plan native", text)
        self.assertIn("project", text)
        self.assertIn("none colliding", text)
        self.assertIn("★ preferred", text)
        self.assertIn("Status  Ready", text)
        self.assertIn("Q cancel", text)

    def test_enter_returns_perform_intent(self) -> None:
        result, _win = self._run(self._plan(), ["\n"])
        self.assertIsNotNone(result)
        action, prepared = result
        self.assertEqual(action, "perform")
        self.assertEqual(prepared.result.session_action.kind, "fresh")
        self.assertEqual(self.launches, [])  # perform happens after teardown

    def test_details_toggle_shows_availability(self) -> None:
        _result, win = self._run(self._plan(), ["d", "q"])
        text = win.text()
        self.assertIn("availability", text)
        self.assertIn("scalar", text)
        first_frame = win.frames[0]
        self.assertNotIn("availability", first_frame)

    def test_question_mark_opens_guarantee_modal(self) -> None:
        result, win = self._run(self._plan(), ["?", "\n", "q"])
        self.assertIsNone(result)
        self.assertTrue(
            any("Native workflows (ultracode): ON" in frame for frame in win.frames)
        )

    def test_managed_plan_has_no_recorded_current_switcher(self) -> None:
        # R1 P1: the curses card no longer offers R/C; both keys open the
        # recorded-only redirect Modal and the source never switches.
        record = self.save_session()
        plan = cli.managed_plan(self.runtime, record)
        result, win = self._run(plan, ["c", "\n", "r", "\n", "q"])
        self.assertIsNone(result)
        self.assertIn("Recorded snapshot", win.text())
        self.assertFalse(
            any("Current saved composition" in frame for frame in win.frames)
        )
        self.assertNotIn("R recorded", win.text())
        self.assertNotIn("C current", win.text())
        redirects = [
            frame for frame in win.frames if "Recorded-only resume" in frame
        ]
        self.assertEqual(len(redirects), 2)
        self.assertTrue(
            any("sessions transition" in frame for frame in redirects)
        )

    def test_managed_plan_e_and_blocked_enter_redirect_not_edit(self) -> None:
        # R1 P1: editing from a managed resume was the same override vector;
        # E and Enter-on-blocked show the transition path instead.
        record = self.save_session()
        plan = cli.managed_plan(self.runtime, record)
        with mock.patch.object(
            cli._QuickConfirmScreen,
            "_edit",
            side_effect=AssertionError("editor must not open on managed plans"),
        ):
            result, win = self._run(plan, ["e", "\n", "q"])
        self.assertIsNone(result)
        self.assertNotIn("E edit", win.text())
        redirects = [
            frame for frame in win.frames if "Recorded-only resume" in frame
        ]
        self.assertEqual(len(redirects), 1)
        # Blocked managed plan: Enter takes the same redirect.
        record["snapshot"]["lead"]["model"] = "no-such-model"
        self.runtime.session_store.save(record)
        blocked = cli.managed_plan(self.runtime, record)
        self.assertFalse(blocked.ready)
        with mock.patch.object(
            cli._QuickConfirmScreen,
            "_edit",
            side_effect=AssertionError("editor must not open on managed plans"),
        ):
            result, win = self._run(blocked, ["\n", "\n", "q"])
        self.assertIsNone(result)
        self.assertIn("Enter transition hint", win.text())
        redirects = [
            frame for frame in win.frames if "Recorded-only resume" in frame
        ]
        self.assertEqual(len(redirects), 1)

    def test_blocked_enter_opens_form_editor_and_esc_returns(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "blocked-tui"
        document["availability"]["providers"]["openai"] = "off"
        self.runtime.compositions.save(document)
        plan = self._plan(self.runtime.compositions.load("blocked-tui"))
        self.assertFalse(plan.ready)
        result, win = self._run(plan, ["\n", "\x1b", "q"])
        self.assertIsNone(result)
        self.assertTrue(any("Edit blocked-tui" in frame for frame in win.frames))
        self.assertEqual(self.launches, [])

    def test_esc_cancels(self) -> None:
        result, _win = self._run(self._plan(), ["\x1b"])
        self.assertIsNone(result)


class SessionsTuiScreenTests(CLITestCase):
    """UX §4 interactive sessions Table, driven via the FakeWindow double."""

    def _run(self, keys):
        from test_tui import FakeWindow

        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(keys)
        result = screen.run(win)
        return result, win, screen

    def test_table_renders_mode_column_and_actions(self) -> None:
        self.save_session(session_id=FIXED_ID, mode="durable", scope_generation=2)
        self.save_session(session_id=OTHER_ID)
        result, win, _ = self._run(["q"])
        self.assertIsNone(result)
        text = win.text()
        self.assertIn("durable(g2)", text)
        self.assertIn("legacy", text)
        self.assertIn("cm:default", text)
        self.assertIn("R resume", text)
        self.assertIn("[r]esume [t]ransition [f]orget", text)

    def test_empty_table(self) -> None:
        result, win, _ = self._run(["q"])
        self.assertIsNone(result)
        self.assertIn("(no recorded sessions)", win.text())

    def test_forget_modal_states_deletion_and_forgets(self) -> None:
        self.save_session(session_id=FIXED_ID, mode="durable", scope_generation=1)
        result, win, _screen = self._run(["f", "\n", "q"])
        self.assertIsNone(result)
        self.assertTrue(
            any("session record + generated scope" in frame for frame in win.frames)
        )
        self.assertTrue(
            any("Transcripts are never touched" in frame for frame in win.frames)
        )
        self.assertFalse(self.runtime.session_store.exists(FIXED_ID))
        self.assertIn("Forgot", win.text())

    def test_forget_cancel_keeps_record(self) -> None:
        self.save_session(session_id=FIXED_ID, mode="durable", scope_generation=1)
        self._run(["f", curses.KEY_RIGHT, "\n", "q"])
        self.assertTrue(self.runtime.session_store.exists(FIXED_ID))

    def test_resume_returns_intent(self) -> None:
        self.save_session(session_id=FIXED_ID, mode="durable", scope_generation=1)
        result, _win, _screen = self._run(["r", "\n"])
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "resume")
        self.assertEqual(result[1]["managed_id"], FIXED_ID)

    def test_resume_legacy_modal_states_upgrade(self) -> None:
        self.save_session(session_id=FIXED_ID)
        result, win, _screen = self._run(["r", curses.KEY_RIGHT, "\n", "q"])
        self.assertIsNone(result)
        self.assertTrue(
            any("predates durable scopes" in frame for frame in win.frames)
        )

    def test_transition_intent_with_composition_choice(self) -> None:
        self.save_session(session_id=FIXED_ID, mode="durable", scope_generation=1)
        self.runtime.compositions.duplicate("default", "other")
        result, _win, _screen = self._run(["t", curses.KEY_DOWN, "\n"])
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "transition")
        self.assertEqual(result[1]["managed_id"], FIXED_ID)
        self.assertEqual(result[2], "other")

    def test_transition_choice_cancelled(self) -> None:
        self.save_session(session_id=FIXED_ID, mode="durable", scope_generation=1)
        result, _win, _screen = self._run(["t", "\x1b", "q"])
        self.assertIsNone(result)


class TransitionTuiScreenTests(unittest.TestCase):
    """TRANSITIONS §3 diff view + exited-confirmation Modal."""

    DIFF = ["lead model: fable -> sol", "workflows: native -> native"]

    def _run(self, keys):
        from test_tui import FakeWindow

        screen = cli._TransitionScreen(list(self.DIFF), palette=tui.MONO_PALETTE)
        win = FakeWindow(keys)
        return screen.run(win), win

    def test_diff_rendered_and_confirm_via_modal(self) -> None:
        confirmed, win = self._run(["\n", "\n"])
        self.assertTrue(confirmed)
        text = win.text()
        self.assertIn("lead model: fable -> sol", text)
        self.assertIn("EXITED (not merely idle)", text)
        self.assertIn("Has the target process exited?", text)

    def test_esc_cancels_without_confirmation(self) -> None:
        confirmed, _win = self._run(["\x1b"])
        self.assertFalse(confirmed)

    def test_modal_cancel_declines(self) -> None:
        confirmed, _win = self._run(["\n", curses.KEY_RIGHT, "\n"])
        self.assertFalse(confirmed)

    def test_modal_esc_declines(self) -> None:
        confirmed, _win = self._run(["\n", "\x1b"])
        self.assertFalse(confirmed)


class RunEditorChooserTests(CLITestCase):
    """The single editor entry: printed guidance when curses is unavailable."""

    def _editor_state_and_plan(self):
        document = self.runtime.compositions.load("default")
        plan = cli.build_quick_plan(
            self.runtime, document, action="fresh", source="Trusted default"
        )
        editor_state = EditorState(
            self.runtime.catalog.docs,
            plan.document,
            self.runtime.catalog.default_composition,
        )
        return editor_state, plan

    def test_dumb_streams_print_plan_and_editor_command(self) -> None:
        editor_state, plan = self._editor_state_and_plan()
        output = io.StringIO()
        outcome = cli.run_editor(
            editor_state,
            io.StringIO(""),
            output,
            plan=plan,
            runtime=self.runtime,
        )
        self.assertIsNone(outcome)
        text = output.getvalue()
        self.assertIn("Status         Ready", text)
        self.assertIn("Variants", text)  # printed --print style with details
        self.assertIn("$EDITOR", text)
        self.assertIn("default.json", text)
        self.assertIn("trusted seed", text)

    def test_force_line_skips_curses_attempt(self) -> None:
        editor_state, plan = self._editor_state_and_plan()
        output = io.StringIO()
        outcome = cli.run_editor(
            editor_state,
            io.StringIO(""),
            output,
            force_line=True,
            plan=plan,
            runtime=self.runtime,
        )
        self.assertIsNone(outcome)
        self.assertIn("$EDITOR", output.getvalue())


class DoctorBadgePaletteTests(CLITestCase):
    """Doctor keeps its line contract; badges style only when color is active."""

    def test_output_palette_is_mono_for_non_tty(self) -> None:
        palette = cli._output_palette(io.StringIO(), io.StringIO(), False)
        self.assertIs(palette, tui.MONO_PALETTE)

    def test_output_palette_detects_dark_for_tty(self) -> None:
        class Tty(io.StringIO):
            def isatty(self):
                return True

        with mock.patch.dict(os.environ, {"COLORFGBG": "15;0"}, clear=False):
            os.environ.pop("NO_COLOR", None)
            palette = cli._output_palette(Tty(), Tty(), False)
        self.assertIs(palette, tui.DARK_PALETTE)

    def test_output_palette_no_color_flag_wins(self) -> None:
        class Tty(io.StringIO):
            def isatty(self):
                return True

        palette = cli._output_palette(Tty(), Tty(), True)
        self.assertIs(palette, tui.MONO_PALETTE)

    def test_doctor_ready_badge_colored_only_on_tty(self) -> None:
        class Tty(io.StringIO):
            def isatty(self):
                return True

        output = Tty()
        with mock.patch.dict(os.environ, {"COLORFGBG": "15;0"}, clear=False):
            os.environ.pop("NO_COLOR", None)
            code = cli.main(
                ["doctor"],
                runtime=self.runtime,
                output_stream=output,
                interactive=False,
            )
        self.assertEqual(code, 0)
        self.assertIn("\x1b[32mReady\x1b[0m", output.getvalue())

    def test_doctor_ready_badge_plain_with_no_color(self) -> None:
        class Tty(io.StringIO):
            def isatty(self):
                return True

        output = Tty()
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False):
            code = cli.main(
                ["doctor"],
                runtime=self.runtime,
                output_stream=output,
                interactive=False,
            )
        self.assertEqual(code, 0)
        self.assertIn("Ready\n", output.getvalue())
        self.assertNotIn("\x1b", output.getvalue())


class SessionsListTuiDriverTests(CLITestCase):
    """_sessions_list_tui wiring: screen intents reuse the command flows."""

    def _args(self):
        return cli.build_parser().parse_args(["sessions", "list"])

    def test_quit_returns_zero_without_action(self) -> None:
        with mock.patch(
            "claude_multi.cli.tui.run_curses_on_streams", return_value=None
        ):
            code = cli._sessions_list_tui(
                self.runtime,
                self._args(),
                input_stream=io.StringIO(),
                output_stream=io.StringIO(),
                no_color=False,
            )
        self.assertEqual(code, 0)
        self.assertEqual(self.launches, [])

    def test_resume_intent_prepares_and_performs(self) -> None:
        record = self.save_session(mode="durable", scope_generation=2)
        with mock.patch(
            "claude_multi.cli.tui.run_curses_on_streams",
            return_value=("resume", record),
        ):
            code = cli._sessions_list_tui(
                self.runtime,
                self._args(),
                input_stream=io.StringIO(),
                output_stream=io.StringIO(),
                no_color=False,
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].result.session_action.kind, "resume")
        self.assertEqual(self.launches[0].record["scope_generation"], 2)

    def test_resume_blocked_intent_prints_plan_and_fails(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        tampered = self.runtime.compositions.load("default")
        tampered["slots"][0]["model"] = "no-such-model"
        record["snapshot"]["lead"]["model"] = "no-such-model"
        self.runtime.session_store.save(record)
        with mock.patch(
            "claude_multi.cli.tui.run_curses_on_streams",
            return_value=("resume", record),
        ):
            output = io.StringIO()
            code = cli._sessions_list_tui(
                self.runtime,
                self._args(),
                input_stream=io.StringIO(),
                output_stream=output,
                no_color=False,
            )
        self.assertEqual(code, 2)
        self.assertIn("composition is blocked", output.getvalue())
        self.assertEqual(self.launches, [])

    def test_transition_intent_runs_transition_flow(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        plan = scope_mod.compile_scope(
            resolved,
            self.runtime.catalog.docs["roles"]["roles"],
            self.runtime.catalog.prompt_bodies,
            scope_mod.catalog_meta_from_docs(self.runtime.catalog.docs),
        )
        scope_mod.write_scope(self.runtime.session_store.root, FIXED_ID, plan)
        shifted = self.runtime.compositions.load("default")
        shifted["name"] = "shifted"
        shifted["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(shifted)
        with mock.patch(
            "claude_multi.cli.tui.run_curses_on_streams",
            return_value=("transition", record, "shifted"),
        ):
            output = io.StringIO()
            code = cli._sessions_list_tui(
                self.runtime,
                self._args(),
                input_stream=io.StringIO("y\n"),
                output_stream=output,
                no_color=False,
            )
        self.assertEqual(code, 0, output.getvalue())
        self.assertIn("lead model: fable -> sol", output.getvalue())
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["composition_name"], "shifted"
        )


class QuickConfirmCursesPlumbingTests(CLITestCase):
    """quick_confirm chooses the curses card on capable streams, line else."""

    def _plan(self):
        document = self.runtime.compositions.load("default")
        return cli.build_quick_plan(
            self.runtime, document, action="fresh", source="Trusted default"
        )

    def test_capable_streams_route_to_curses_screen(self) -> None:
        called = []

        def fake_screen(runtime, plan, **kwargs):
            called.append(plan)
            return 0

        with mock.patch.object(
            cli.tui, "streams_curses_capable", return_value=True
        ), mock.patch.object(cli, "_curses_quick_confirm", side_effect=fake_screen):
            result = cli.quick_confirm(
                self.runtime,
                self._plan(),
                input_stream=io.StringIO("\n"),
                output_stream=io.StringIO(),
                passthrough=[],
                force_line=False,
            )
        self.assertEqual(result, 0)
        self.assertEqual(len(called), 1)

    def test_curses_failure_falls_back_to_line_loop(self) -> None:
        output = io.StringIO()
        with mock.patch.object(
            cli.tui, "streams_curses_capable", return_value=True
        ), mock.patch.object(
            cli, "_curses_quick_confirm", side_effect=cli.curses.error("boom")
        ):
            result = cli.quick_confirm(
                self.runtime,
                self._plan(),
                input_stream=io.StringIO("q\n"),
                output_stream=output,
                passthrough=[],
                force_line=False,
            )
        self.assertEqual(result, 0)
        self.assertIn("Status         Ready", output.getvalue())
        self.assertEqual(self.launches, [])

    def test_force_line_never_enters_curses(self) -> None:
        output = io.StringIO()
        with mock.patch.object(
            cli, "_curses_quick_confirm", side_effect=AssertionError("must not run")
        ):
            result = cli.quick_confirm(
                self.runtime,
                self._plan(),
                input_stream=io.StringIO("q\n"),
                output_stream=output,
                passthrough=[],
                force_line=True,
            )
        self.assertEqual(result, 0)
        self.assertIn("Status         Ready", output.getvalue())


class QuickConfirmAccessibilityTests(CLITestCase):
    """UX §8: every colorized element carries a text form (no color-only meaning)."""

    def test_card_text_identical_across_palettes_attrs_differ(self) -> None:
        from test_tui import FakeWindow

        document = self.runtime.compositions.load("default")
        texts: list[str] = []
        status_attrs: list[int] = []
        for palette in (tui.MONO_PALETTE, tui.DARK_PALETTE, tui.LIGHT_PALETTE):
            plan = cli.build_quick_plan(
                self.runtime, document, action="fresh", source="Trusted default"
            )
            screen = cli._QuickConfirmScreen(
                self.runtime, plan, passthrough=[], palette=palette
            )
            win = FakeWindow(["q"])
            screen.run(win)
            texts.append(win.text())
            row, col = win.find("Status  Ready")[0]
            status_attrs.append(win.attr_at(row, col))
        self.assertEqual(texts[0], texts[1])
        self.assertEqual(texts[1], texts[2])
        self.assertEqual(status_attrs[0], 0)  # mono: no color attribute at all
        self.assertNotEqual(status_attrs[1], 0)
        self.assertNotEqual(status_attrs[2], 0)


class LegacyWorkflowsOffRefusalTests(CLITestCase):
    """R1 P4: --legacy cannot honor workflows:off (no durable settings
    channel); prepare fails closed with the pinned refusal."""

    def _off_composition(self):
        document = self.runtime.compositions.load("default")
        document["name"] = "off-comp"
        document["workflows"] = "off"
        self.runtime.compositions.save(document)
        return document

    def test_refusal_message_is_pinned_verbatim(self) -> None:
        self.assertEqual(
            cli.LEGACY_WORKFLOWS_OFF_REFUSAL,
            "workflows:off requires a durable session; drop --legacy or set "
            "workflows: native",
        )

    def test_prepare_refuses_legacy_off_fresh(self) -> None:
        document = self._off_composition()
        with self.assertRaises(cli.CLIError) as raised:
            self.runtime.prepare(
                document,
                action="fresh",
                passthrough=[],
                legacy_requested=True,
            )
        self.assertEqual(str(raised.exception), cli.LEGACY_WORKFLOWS_OFF_REFUSAL)
        self.assertEqual(self.launches, [])

    def test_noninteractive_legacy_off_exits_two_without_launch(self) -> None:
        self._off_composition()
        code, output = self.run_cli(
            ["--composition", "off-comp", "--legacy"], interactive=False
        )
        self.assertEqual(code, 2)
        self.assertIn(cli.LEGACY_WORKFLOWS_OFF_REFUSAL, output)
        self.assertEqual(self.launches, [])

    def test_interactive_enter_shows_refusal_and_never_launches(self) -> None:
        self._off_composition()
        code, output = self.run_cli(
            ["--composition", "off-comp", "--legacy", "--line"], "\nq\n"
        )
        self.assertEqual(code, 0)
        self.assertIn(cli.LEGACY_WORKFLOWS_OFF_REFUSAL, output)
        self.assertEqual(self.launches, [])

    def test_resume_legacy_off_record_is_refused(self) -> None:
        document = self._off_composition()
        self.save_session(document, mode="durable", scope_generation=1)
        code, output = self.run_cli(["-r", FIXED_ID, "--legacy", "--line"], "\nq\n")
        self.assertEqual(code, 0)
        self.assertIn(cli.LEGACY_WORKFLOWS_OFF_REFUSAL, output)
        self.assertEqual(self.launches, [])

    def test_off_without_legacy_prepares_durable(self) -> None:
        document = self._off_composition()
        prepared = self.runtime.prepare(
            document, action="fresh", passthrough=[], legacy_requested=False
        )
        self.assertTrue(prepared.result.durable)
        self.assertEqual(prepared.record["workflows"], "off")

    def test_legacy_native_workflows_still_launches_argv_mode(self) -> None:
        code, _ = self.run_cli(
            ["--composition", "default", "--legacy"], interactive=False
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        self.assertFalse(self.launches[0].result.durable)


class TransitionPreflightTests(CLITestCase):
    """R1 L3: binary verification + gateway readiness run BEFORE
    transition.execute; a failure aborts with nothing touched."""

    def _write_scope(self, session_id: str) -> None:
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        plan = scope_mod.compile_scope(
            resolved,
            self.runtime.catalog.docs["roles"]["roles"],
            self.runtime.catalog.prompt_bodies,
            scope_mod.catalog_meta_from_docs(self.runtime.catalog.docs),
        )
        scope_mod.write_scope(self.runtime.session_store.root, session_id, plan)

    def _shifted_composition(self):
        document = self.runtime.compositions.load("default")
        document["name"] = "shifted"
        document["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(document)
        return document

    def test_preflight_refusal_is_pinned_verbatim(self) -> None:
        self.assertEqual(
            cli.TRANSITION_PREFLIGHT_REFUSAL,
            "transition preflight failed; the session record and scope are "
            "untouched: ",
        )

    def test_binary_failure_aborts_before_execute(self) -> None:
        from claude_multi import transition as transition_mod

        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self._shifted_composition()
        self.runtime.doctor_binary_callback = lambda _contract: (
            ["managed Claude binary: inspected Claude artifact /x is missing; "
             "re-run the native-contract inspection against the installed version"],
            [],
        )
        with mock.patch.object(transition_mod, "execute") as execute_mock:
            code, output = self.run_cli(
                ["sessions", "transition", FIXED_ID, "--composition", "shifted"],
                "y\n",
            )
        self.assertEqual(code, 2)
        execute_mock.assert_not_called()
        self.assertIn("transition preflight failed", output)
        self.assertIn("the session record and scope are untouched", output)
        self.assertIn("managed Claude binary", output)
        self.assertIn("re-run the native-contract inspection", output)
        self.assertEqual(self.launches, [])
        # Nothing touched: record and scope generation unchanged.
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["scope_generation"], 1)
        self.assertEqual(record["composition_name"], "default")
        scopes_root = self.runtime.session_store.root / "scopes"
        self.assertFalse((scopes_root / f".{FIXED_ID}.prev").exists())
        self.assertFalse((scopes_root / f".{FIXED_ID}.new").exists())

    def test_readiness_failure_aborts_before_execute(self) -> None:
        from claude_multi import transition as transition_mod

        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self._shifted_composition()
        self.runtime.doctor_callback = lambda _runtime: [
            "local gateway: fixture gateway down"
        ]
        with mock.patch.object(transition_mod, "execute") as execute_mock:
            code, output = self.run_cli(
                ["sessions", "transition", FIXED_ID, "--composition", "shifted"],
                "y\n",
            )
        self.assertEqual(code, 2)
        execute_mock.assert_not_called()
        self.assertIn("transition preflight failed", output)
        self.assertIn("local gateway: fixture gateway down", output)
        self.assertEqual(self.launches, [])
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["scope_generation"], 1)

    def test_its_exited_flag_also_preflights(self) -> None:
        from claude_multi import transition as transition_mod

        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self.runtime.doctor_binary_callback = lambda _contract: (
            ["managed Claude binary: content hash does not match"], []
        )
        with mock.patch.object(transition_mod, "execute") as execute_mock:
            code, output = self.run_cli(
                [
                    "sessions", "transition", FIXED_ID,
                    "--composition", "default", "--its-exited",
                ],
                interactive=False,
            )
        self.assertEqual(code, 2)
        execute_mock.assert_not_called()
        self.assertIn("transition preflight failed", output)
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["scope_generation"], 1
        )

    def test_print_only_dry_run_skips_preflight(self) -> None:
        # Without confirmation nothing can mutate, so the diff + exact
        # command stay available even with a broken binary/gateway.
        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self.runtime.doctor_binary_callback = lambda _contract: (
            ["managed Claude binary: content hash does not match"], []
        )
        code, output = self.run_cli(
            ["sessions", "transition", FIXED_ID, "--composition", "default"],
            interactive=False,
        )
        self.assertEqual(code, 0, output)
        self.assertIn(
            f"claude-multi sessions transition {FIXED_ID} --composition default",
            output,
        )
        self.assertEqual(self.launches, [])
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["scope_generation"], 1
        )

    def test_confirmed_transition_runs_preflight_then_execute(self) -> None:
        from claude_multi import transition as transition_mod

        self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        self._shifted_composition()
        real_execute = transition_mod.execute
        with mock.patch.object(
            transition_mod, "execute", side_effect=real_execute
        ) as execute_mock:
            code, output = self.run_cli(
                ["sessions", "transition", FIXED_ID, "--composition", "shifted"],
                "y\n",
            )
        self.assertEqual(code, 0, output)
        execute_mock.assert_called_once()
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["scope_generation"], 2
        )


class TerminalInjectionTests(CLITestCase):
    """R1 P3: external text (cwd, project paths, names) never reaches the
    terminal raw; every render point escapes control bytes visibly."""

    OSC_CWD = "/tmp/evil\x1b]8;;https://bad.example\x07link\x1b]8;;\x07"

    def test_hostile_cwd_sanitized_in_sessions_list(self) -> None:
        record = self.save_session()
        record["cwd"] = self.OSC_CWD
        self.runtime.session_store.save(record)
        code, output = self.run_cli(["sessions", "list"], interactive=False)
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", output)
        self.assertNotIn("\x07", output)
        self.assertIn("^[]8;;https://bad.example^Glink^[]8;;^G", output)

    def test_hostile_cwd_sanitized_in_sessions_tui_table(self) -> None:
        from test_tui import FakeWindow

        record = self.save_session()
        record["cwd"] = self.OSC_CWD
        self.runtime.session_store.save(record)
        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(["q"])
        screen.run(win)
        text = win.text()
        self.assertNotIn("\x1b", text)
        self.assertNotIn("\x07", text)
        self.assertIn("^[", text)

    def test_hostile_project_agent_path_sanitized_in_quick_confirm(self) -> None:
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        variant_id = resolved.variants[0].id
        agents = Path(self.runtime.cwd) / ".claude" / "agents"
        agents.mkdir(parents=True)
        hostile = agents / "cm-shadow\x1b[2J\x07.md"
        hostile.write_bytes(
            f"---\nname: {variant_id}\ndescription: shadow\n---\nbody\n".encode()
        )
        code, output = self.run_cli([], "q\n")
        self.assertEqual(code, 0)
        self.assertIn("Status         BLOCKED", output)
        self.assertNotIn("\x1b", output)
        self.assertNotIn("\x07", output)
        self.assertIn("cm-shadow^[", output)

    def test_hostile_project_agent_path_sanitized_in_doctor(self) -> None:
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        variant_id = resolved.variants[0].id
        agents = Path(self.runtime.cwd) / ".claude" / "agents"
        agents.mkdir(parents=True)
        hostile = agents / "cm-shadow\x1b[2J.md"
        hostile.write_bytes(
            f"---\nname: {variant_id}\ndescription: shadow\n---\nbody\n".encode()
        )
        code, output = self.run_cli(["doctor"], interactive=False)
        self.assertEqual(code, 1)
        self.assertNotIn("\x1b", output)
        self.assertIn("cm-shadow^[", output)

    def test_hostile_composition_name_sanitized_in_quick_confirm(self) -> None:
        # Composition names are schema/check_name constrained on every real
        # path; the render still defends in depth (in-memory document).
        document = self.runtime.compositions.load("default")
        document["name"] = "evil\x1b[2J\nname"
        plan = cli.build_quick_plan(
            self.runtime, document, action="fresh", source="test"
        )
        rendered = cli.render_quick_confirm(self.runtime, plan)
        self.assertNotIn("\x1b", rendered)
        self.assertIn("evil^[", rendered)
        self.assertIn("^Jname", rendered)

    def test_hostile_record_model_sanitized_in_quick_confirm(self) -> None:
        record = self.save_session()
        record["snapshot"]["lead"]["model"] = "fable\x1b[2J"
        self.runtime.session_store.save(record)
        plan = cli.managed_plan(self.runtime, record)
        self.assertFalse(plan.ready)
        rendered = cli.render_quick_confirm(self.runtime, plan)
        self.assertNotIn("\x1b", rendered)

    def test_hostile_override_name_sanitized_in_main_error(self) -> None:
        self.save_session()
        code, output = self.run_cli(
            ["--composition", "evil\x1b]8;;https://bad\x07", "-r", FIXED_ID],
            interactive=False,
        )
        self.assertEqual(code, 2)
        self.assertNotIn("\x1b", output)
        self.assertNotIn("\x07", output)
        self.assertIn("resume always uses the recorded composition", output)

    def test_hostile_transition_diff_line_sanitized(self) -> None:
        # A hand-crafted record can carry hostile snapshot selectors; the
        # line-mode diff print escapes them.
        record = self.save_session(mode="durable", scope_generation=1)
        record["snapshot"]["lead"]["effort"] = "high\x1b[2J"
        self.runtime.session_store.save(record)
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        plan = scope_mod.compile_scope(
            resolved,
            self.runtime.catalog.docs["roles"]["roles"],
            self.runtime.catalog.prompt_bodies,
            scope_mod.catalog_meta_from_docs(self.runtime.catalog.docs),
        )
        scope_mod.write_scope(self.runtime.session_store.root, FIXED_ID, plan)
        code, output = self.run_cli(
            ["sessions", "transition", FIXED_ID, "--composition", "default"],
            interactive=False,
        )
        self.assertEqual(code, 0, output)
        self.assertNotIn("\x1b", output)
        self.assertIn("high^[", output)


class ResumeNameResolutionTests(CLITestCase):
    """-r accepts exact UUIDs and composition names (native exit-hint form)."""

    OTHER_ID = "97a6194a-1111-4222-8333-444455556666"

    def test_uuid_still_resumes_exact(self) -> None:
        self.save_session()
        self.assertEqual(
            cli._resolve_resume_target(self.runtime, FIXED_ID), FIXED_ID
        )

    def test_unique_composition_name_resolves(self) -> None:
        self.save_session()
        self.assertEqual(
            cli._resolve_resume_target(self.runtime, "default"), FIXED_ID
        )

    def test_cm_prefixed_name_resolves(self) -> None:
        self.save_session()
        self.assertEqual(
            cli._resolve_resume_target(self.runtime, "cm:default"), FIXED_ID
        )

    def test_ambiguous_name_lists_candidates(self) -> None:
        self.save_session(session_id=FIXED_ID)
        self.save_session(session_id=self.OTHER_ID)
        with self.assertRaises(cli.CLIError) as ctx:
            cli._resolve_resume_target(self.runtime, "default")
        text = str(ctx.exception)
        self.assertIn("matches 2 managed sessions", text)
        self.assertIn(FIXED_ID, text)
        self.assertIn(self.OTHER_ID, text)

    def test_unknown_name_points_at_sessions_list(self) -> None:
        with self.assertRaises(cli.CLIError) as ctx:
            cli._resolve_resume_target(self.runtime, "cm:no-such-thing")
        self.assertIn("claude-multi sessions list", str(ctx.exception))

    def test_resume_by_name_end_to_end(self) -> None:
        self.save_session()
        code, _out = self.run_cli(["-r", "cm:default"], "\n", interactive=True)
        self.assertEqual(code, 0)
        self.assertEqual(
            self.launches[0].record["managed_id"], FIXED_ID
        )


class VisibleMessageTests(CLITestCase):
    def test_multi_line_error_keeps_structure(self) -> None:
        self.save_session(session_id=FIXED_ID)
        self.save_session(session_id="97a6194a-1111-4222-8333-444455556666")
        code, out = self.run_cli(["-r", "default"], interactive=True)
        self.assertEqual(code, 2)
        self.assertIn("matches 2 managed sessions", out)
        self.assertNotIn("^J", out)
        self.assertIn(FIXED_ID, out)
        # Each candidate renders on its own real line.
        self.assertTrue(
            any(line.strip().startswith(FIXED_ID) for line in out.splitlines())
        )

    def test_visible_message_sanitizes_per_line(self) -> None:
        hostile = "line one\x1b]0;pwned\x07\nline two"
        rendered = cli.tui.visible_message(hostile)
        self.assertEqual(rendered, "line one^[]0;pwned^G\nline two")


class QuickConfirmSessionsKeyTests(CLITestCase):
    def test_line_mode_s_prints_sessions_and_hint(self) -> None:
        self.save_session()
        code, out = self.run_cli(
            ["-r", FIXED_ID], "s\nq\n", interactive=True
        )
        self.assertEqual(code, 0)
        self.assertIn(FIXED_ID, out)
        self.assertIn("claude-multi -r <uuid>", out)

    def test_curses_open_sessions_cancel_stays(self) -> None:
        self.save_session()
        palette = cli.tui.Palette("mono", False)
        plan = cli.managed_plan(
            self.runtime, self.runtime.session_store.load(FIXED_ID)
        )
        screen = cli._QuickConfirmScreen(self.runtime, plan, passthrough=[], palette=palette)

        class FakeSessions:
            def __init__(self, runtime, *, palette):
                pass

            def run(self, win):
                return None

        original = cli._SessionsScreen
        cli._SessionsScreen = FakeSessions
        try:
            self.assertIsNone(screen._open_sessions(None))
        finally:
            cli._SessionsScreen = original

    def test_curses_open_sessions_resume_returns_perform(self) -> None:
        self.save_session()
        palette = cli.tui.Palette("mono", False)
        record = self.runtime.session_store.load(FIXED_ID)
        plan = cli.managed_plan(self.runtime, record)
        screen = cli._QuickConfirmScreen(self.runtime, plan, passthrough=[], palette=palette)

        class FakeSessions:
            def __init__(self, runtime, *, palette):
                pass

            def run(self, win):
                return ("resume", record)

        original = cli._SessionsScreen
        cli._SessionsScreen = FakeSessions
        try:
            outcome = screen._open_sessions(None)
        finally:
            cli._SessionsScreen = original
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome[0], "perform")
        self.assertEqual(outcome[1].record["managed_id"], FIXED_ID)


class PresetCycleTests(CLITestCase):
    def _two_presets(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "second"
        self.runtime.compositions.save(document)

    def test_cycle_forward_and_back(self) -> None:
        self._two_presets()
        document = self.runtime.compositions.load("default")
        plan = cli.build_quick_plan(self.runtime, document, action="fresh", source="t")
        cycled = cli._cycle_preset(self.runtime, plan, 1)
        self.assertEqual(cycled.document["name"], "second")
        self.assertIn("2/2", cycled.source)
        back = cli._cycle_preset(self.runtime, cycled, -1)
        self.assertEqual(back.document["name"], "default")

    def test_cycle_wraps(self) -> None:
        self._two_presets()
        document = self.runtime.compositions.load("default")
        plan = cli.build_quick_plan(self.runtime, document, action="fresh", source="t")
        cycled = cli._cycle_preset(self.runtime, plan, -1)
        self.assertEqual(cycled.document["name"], "second")

    def test_managed_plan_does_not_cycle(self) -> None:
        self._two_presets()
        self.save_session()
        record = self.runtime.session_store.load(FIXED_ID)
        plan = cli.managed_plan(self.runtime, record)
        self.assertIs(cli._cycle_preset(self.runtime, plan, 1), plan)

    def test_single_preset_is_noop(self) -> None:
        document = self.runtime.compositions.load("default")
        plan = cli.build_quick_plan(self.runtime, document, action="fresh", source="t")
        self.assertIs(cli._cycle_preset(self.runtime, plan, 1), plan)

    def test_line_mode_p_cycles(self) -> None:
        self._two_presets()
        code, out = self.run_cli([], "p\nq\n", interactive=True)
        self.assertEqual(code, 0)
        self.assertIn("Composition    second · Selected preset 2/2", out)


class PolishBatchTests(CLITestCase):
    """M5.1 polish batch: w toggle, bare -r, --print-launch, candidate modes,
    relative age, prune coverage."""

    # A: workflow toggle --------------------------------------------------
    def test_w_toggles_workflows_off_and_back(self) -> None:
        document = self.runtime.compositions.load("default")
        plan = cli.build_quick_plan(self.runtime, document, action="fresh", source="t")
        off = cli._toggle_workflows(self.runtime, plan)
        self.assertEqual(off.document.get("workflows"), "off")
        self.assertEqual(off.resolved.workflows, "off")
        back = cli._toggle_workflows(self.runtime, off)
        self.assertNotIn("workflows", back.document)

    def test_w_managed_plan_is_noop(self) -> None:
        self.save_session()
        record = self.runtime.session_store.load(FIXED_ID)
        plan = cli.managed_plan(self.runtime, record)
        self.assertIs(cli._toggle_workflows(self.runtime, plan), plan)

    # B: bare -r ----------------------------------------------------------
    def test_bare_resume_prints_listing_when_piped(self) -> None:
        self.save_session()
        code, out = self.run_cli(["-r"], interactive=False)
        self.assertEqual(code, 0)
        self.assertIn(FIXED_ID, out)
        self.assertIn("sessions", out)

    # C: --print-launch ----------------------------------------------------
    def test_print_launch_shows_argv_without_token(self) -> None:
        code, out = self.run_cli(
            ["--composition", "default", "--print-launch"], interactive=False
        )
        self.assertEqual(code, 0)
        self.assertIn("--add-dir", out)
        self.assertIn("--settings", out)
        self.assertIn("ANTHROPIC_AUTH_TOKEN", out)
        self.assertIn("value never shown", out)
        self.assertEqual(len(self.launches), 0)

    # D: ambiguous candidates carry the mode column ------------------------
    def test_ambiguous_candidates_show_mode(self) -> None:
        self.save_session(session_id=FIXED_ID, mode="durable", scope_generation=2)
        self.save_session(
            session_id="97a6194a-1111-4222-8333-444455556666", mode="legacy"
        )
        with self.assertRaises(cli.CLIError) as ctx:
            cli._resolve_resume_target(self.runtime, "default")
        text = str(ctx.exception)
        self.assertIn("durable(g2)", text)
        self.assertIn("legacy", text)

    # E: relative age -------------------------------------------------------
    def test_record_age_buckets(self) -> None:
        from datetime import datetime, timezone, timedelta

        now = datetime(2026, 7, 22, 22, 0, 0, tzinfo=timezone.utc)
        def record_age_at(hours, minutes=0):
            stamp = (now - timedelta(hours=hours, minutes=minutes)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            return cli._record_age({"created_at": stamp}, now=now)

        self.assertEqual(record_age_at(0, 0), "just now")
        self.assertEqual(record_age_at(0, 45), "45m ago")
        self.assertEqual(record_age_at(5), "5h ago")
        self.assertEqual(record_age_at(96), "4d ago")
        self.assertEqual(
            cli._record_age({"created_at": "bogus"}, now=now), "bogus"
        )

    # F: prune covers lead prompts and locks --------------------------------
    def test_prune_removes_generated_files_of_forgotten_sessions(self) -> None:
        store = self.runtime.session_store
        gone = "9bc5fd42-d428-4545-97af-3eefcb05b9f2"
        alive = FIXED_ID
        self.save_session(session_id=alive)
        prompt_gone = store.root / f"lead-prompt-abcdef1234567890-{gone}.md"
        prompt_alive = store.root / f"lead-prompt-abcdef1234567890-{alive}.md"
        state.atomic_write(prompt_gone, b"x")
        state.atomic_write(prompt_alive, b"x")
        lock_gone = store.lifecycle_lock(gone)
        lock_gone.acquire(blocking=False)
        lock_gone.release()
        lock_alive = store.lifecycle_lock(alive)
        lock_alive.acquire(blocking=False)
        lock_alive.release()
        import io as _io

        code = cli._doctor_prune(self.runtime, _io.StringIO())
        self.assertEqual(code, 0)
        self.assertFalse(prompt_gone.exists())
        self.assertTrue(prompt_alive.exists())
        self.assertTrue(lock_gone.lock_path.exists())
        self.assertTrue(lock_alive.lock_path.exists())

    def test_prune_waits_for_session_lifecycle_lock(self) -> None:
        store = self.runtime.session_store
        gone = "9bc5fd42-d428-4545-97af-3eefcb05b9f2"
        orphan = scope_mod.scope_dir(store.root, gone)
        state.ensure_private_dir(orphan)
        lock = store.lifecycle_lock(gone)
        self.assertTrue(lock.acquire(blocking=False))
        done: list[int] = []
        thread = threading.Thread(
            target=lambda: done.append(cli._doctor_prune(self.runtime, io.StringIO()))
        )
        thread.start()
        thread.join(timeout=0.2)
        self.assertTrue(thread.is_alive())
        self.assertTrue(orphan.exists())
        lock.release()
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(done, [0])
        self.assertFalse(orphan.exists())
        self.assertTrue(lock.lock_path.exists())


class PickerIntentThreadingTests(CLITestCase):
    def test_print_launch_reports_launch_mode_separately(self) -> None:
        self.save_session(mode="durable", scope_generation=2)
        code, out = self.run_cli(
            ["-r", FIXED_ID, "--legacy", "--print-launch"], "\n", interactive=True
        )
        self.assertEqual(code, 0)
        self.assertIn("launch mode: legacy argv", out)
        self.assertIn("record mode: durable", out)

    def test_picker_resume_threads_legacy_and_passthrough(self) -> None:
        import io as _io

        self.save_session(mode="durable", scope_generation=1)
        performed = []

        class FakeScreen:
            def __init__(self, runtime, *, palette):
                pass

            def run(self, win):
                return ("resume", self_record)

        self_record = self.runtime.session_store.load(FIXED_ID)
        original_screen = cli._SessionsScreen
        original_runner = cli.tui.run_curses_on_streams
        cli._SessionsScreen = FakeScreen
        cli.tui.run_curses_on_streams = lambda fn, i, o, palette: fn(None)
        try:
            code = cli._sessions_list_tui(
                self.runtime,
                None,
                input_stream=_io.StringIO(),
                output_stream=_io.StringIO(),
                no_color=True,
                passthrough=["--verbose"],
                legacy_requested=True,
            )
        finally:
            cli._SessionsScreen = original_screen
            cli.tui.run_curses_on_streams = original_runner
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        prepared = self.launches[0]
        self.assertIn("--verbose", prepared.result.argv)
        self.assertFalse(prepared.result.durable)


class NativeDiscoveryTests(CLITestCase):
    def _fake_projects(self, session_ids):
        home = self.runtime.environ["HOME"]
        import os, pathlib

        projects = pathlib.Path(home) / ".claude" / "projects"
        projects.mkdir(parents=True, exist_ok=True)
        for slug, sid in session_ids:
            target = projects / slug
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{sid}.jsonl").write_bytes(b"{}\n")

    def test_discovers_unmanaged_only(self) -> None:
        self.save_session()
        native_id = "aaaaaaaa-1111-4111-8111-111111111111"
        self._fake_projects([("-proj", FIXED_ID), ("-proj", native_id), ("-proj", "not-a-uuid")])
        found = cli._discover_native_sessions(self.runtime)
        self.assertEqual([item["session_id"] for item in found], [native_id])
        self.assertEqual(found[0]["slug"], "-proj")

    def test_duplicate_uuid_across_project_dirs_is_one_row(self) -> None:
        native_id = "aaaaaaaa-1111-4111-8111-111111111111"
        self._fake_projects([("-one", native_id), ("-two", native_id)])
        found = cli._discover_native_sessions(self.runtime)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["session_id"], native_id)
        self.assertEqual(found[0]["slugs"], ("-one", "-two"))
        self.assertEqual(found[0]["slug"], "(ambiguous)")

    def test_cwd_filter_applies_before_native_row_limit(self) -> None:
        native_id = "aaaaaaaa-1111-4111-8111-111111111111"
        current_slug = cli._native_project_slug(self.runtime.cwd)
        entries = [(current_slug, native_id)]
        for index in range(25):
            other = self.root / f"unrelated-{index}"
            other.mkdir()
            session_id = f"{index + 1:08x}-1111-4111-8111-{index + 1:012x}"
            entries.append((cli._native_project_slug(other), session_id))
        self._fake_projects(entries)
        projects = Path(self.runtime.environ["HOME"]) / ".claude" / "projects"
        os.utime(projects / current_slug / f"{native_id}.jsonl", (1, 1))
        for index, (slug, session_id) in enumerate(entries[1:], start=100):
            os.utime(projects / slug / f"{session_id}.jsonl", (index, index))
        found = cli._discover_native_sessions(
            self.runtime, limit=20, cwd_filter=self.runtime.cwd
        )
        self.assertEqual([item["session_id"] for item in found], [native_id])
        self.assertEqual(found[0]["cwd"], self.runtime.cwd)

    def test_managed_runtime_id_is_not_rediscovered_as_native(self) -> None:
        native_id = "aaaaaaaa-1111-4111-8111-111111111111"
        record = self.save_session()
        updated = self.runtime.session_store.reconcile_runtime(
            record["managed_id"],
            observed_runtime_id=native_id,
            source="resume",
            cwd=self.runtime.cwd,
        )
        self._fake_projects([("-proj", native_id)])
        self.assertEqual(updated["runtime_session_id"], native_id)
        self.assertEqual(cli._discover_native_sessions(self.runtime), [])

    def test_missing_projects_dir_is_empty(self) -> None:
        self.assertEqual(cli._discover_native_sessions(self.runtime), [])

    def test_adopt_moves_native_to_managed(self) -> None:
        native_id = "aaaaaaaa-1111-4111-8111-111111111111"
        self._fake_projects([("-proj", native_id)])
        screen = cli._SessionsScreen(
            self.runtime, palette=cli.tui.MONO_PALETTE
        )
        self.assertEqual(len(screen.native), 1)
        screen.section = "native"
        item = screen.native[0]
        document = self.runtime.compositions.load("default")
        resolved = self.runtime.resolve_document(document)
        record = sessions.make_record(
            session_id=item["session_id"],
            cwd=self.runtime.cwd,
            composition_name=document["name"],
            snapshot=composition.snapshot(resolved),
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.link(record)
        screen._reload()
        self.assertEqual(screen.native, [])
        self.assertEqual(
            [r["runtime_session_id"] for r in screen.records], [item["session_id"]]
        )


class VersionConsistencyTests(unittest.TestCase):
    def test_package_version_matches_version_json(self) -> None:
        import json
        from claude_multi import __version__

        document = json.loads(
            (cli.default_asset_root() / "version.json").read_text(encoding="utf-8")
        )
        self.assertEqual(__version__, document["launcher_version"])

    def test_cli_version_flag_uses_package_version(self) -> None:
        parser = cli.build_parser()
        with self.assertRaises(SystemExit) as ctx:
            parser.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)


class ProxyVersionConsistencyTests(unittest.TestCase):
    def test_proxy_version_matches_package(self) -> None:
        import io
        from claude_multi import __version__, proxy

        out = io.StringIO()
        with __import__("contextlib").redirect_stdout(out):
            proxy.main(["--version"])
        self.assertIn(__version__, out.getvalue())


class AdoptOriginalCwdTests(CLITestCase):
    def test_decode_project_slug_with_dashed_names(self) -> None:
        home = Path(self.runtime.environ["HOME"])
        project = home / "projects" / "occams-agent-flow"
        project.mkdir(parents=True)
        slug = cli._native_project_slug(project)
        self.assertEqual(cli._decode_project_slug(slug), project)
        self.assertIsNone(cli._decode_project_slug("-no-such-place-anywhere"))

    def test_native_project_slug_matches_pinned_client_vectors(self) -> None:
        self.assertEqual(cli._native_project_slug("/tmp/a-b_c.d"), "-tmp-a-b-c-d")
        self.assertEqual(cli._native_project_slug("/tmp/😀"), "-tmp---")
        self.assertEqual(cli._native_project_slug("a" * 200), "a" * 200)
        self.assertEqual(
            cli._native_project_slug("a" * 201), "a" * 200 + "-rkvsv5"
        )
        self.assertEqual(
            cli._native_project_slug("/" + "a" * 200),
            "-" + "a" * 199 + "-b6ymvl",
        )

    def test_decode_hidden_project_slug_and_explicit_cwd(self) -> None:
        home = Path(self.runtime.environ["HOME"])
        hidden = home / ".claude"
        hidden.mkdir(parents=True, exist_ok=True)
        slug = cli._native_project_slug(hidden)
        self.assertTrue(slug.endswith("--claude"), slug)
        self.assertEqual(cli._decode_project_slug(slug), hidden)

        native_id = "aaaaaaaa-1111-4111-8111-111111111111"
        project_dir = home / ".claude" / "projects" / slug
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / f"{native_id}.jsonl").write_bytes(b"not-read")
        self.assertEqual(
            cli._original_cwd_for_adopt(
                self.runtime, native_id, explicit_cwd=str(hidden)
            ),
            str(hidden),
        )
        wrong = home / "wrong"
        wrong.mkdir()
        with self.assertRaisesRegex(cli.CLIError, "does not contain native session"):
            cli._original_cwd_for_adopt(
                self.runtime, native_id, explicit_cwd=str(wrong)
            )

    def test_explicit_cwd_accepts_long_hashed_native_slug(self) -> None:
        home = Path(self.runtime.environ["HOME"])
        project = home / ("long-" + "a" * 190)
        project.mkdir(parents=True)
        slug = cli._native_project_slug(project)
        self.assertGreater(len(str(project)), 200)
        self.assertLessEqual(len(slug), 207)
        native_id = "cccccccc-3333-4333-8333-333333333333"
        project_dir = home / ".claude" / "projects" / slug
        project_dir.mkdir(parents=True)
        (project_dir / f"{native_id}.jsonl").write_bytes(b"not-read")
        self.assertEqual(
            cli._original_cwd_for_adopt(
                self.runtime, native_id, explicit_cwd=str(project)
            ),
            str(project),
        )

    def test_adopt_records_decoded_original_cwd(self) -> None:
        home = Path(self.runtime.environ["HOME"])
        project = home / "projects" / "real-project"
        project.mkdir(parents=True)
        slug = cli._native_project_slug(project)
        native_id = "aaaaaaaa-1111-4111-8111-111111111111"
        projects = home / ".claude" / "projects" / slug
        projects.mkdir(parents=True)
        (projects / f"{native_id}.jsonl").write_bytes(b"{}\n")
        resolved_cwd = cli._original_cwd_for_adopt(self.runtime, native_id)
        self.assertEqual(resolved_cwd, str(project))

    def test_adopt_without_local_metadata_fails_closed(self) -> None:
        with self.assertRaisesRegex(cli.CLIError, "cannot locate native session"):
            cli._original_cwd_for_adopt(
                self.runtime, "bbbbbbbb-2222-4222-8222-222222222222"
            )


class ResumeChdirTests(CLITestCase):
    def test_resume_enters_record_cwd(self) -> None:
        import os as _os

        Path(self.runtime.cwd).mkdir(parents=True, exist_ok=True)
        store = self.runtime.session_store
        record = self.save_session()
        captured = {}

        def fake_execve(path, argv, env):
            captured["cwd"] = _os.getcwd()
            return 0

        other = Path(self.runtime.environ["HOME"]) / "projects" / "other"
        other.mkdir(parents=True)
        fake_binary = self.root / "fake-claude"
        fake_binary.write_bytes(b"#!/bin/sh\n")
        fake_binary.chmod(0o755)
        binary_status = cli.launch.BinaryStatus(
            inspected_path=fake_binary,
            validated_version="fixture",
            sha256="0" * 64,
            configured_path=fake_binary,
            configured_target=fake_binary,
            configured_matches=True,
            advisory=None,
        )
        with mock.patch.object(
            cli.launch, "resolve_claude", return_value=binary_status
        ):
            cli.launch.perform_launch(
                self.launches[0].result
                if self.launches
                else self._fresh_compiled(record),
                record=record,
                store=store,
                native_contract=self.runtime.catalog.docs["native-contract"],
                gateway=self.runtime.catalog.docs["gateway"],
                execve=fake_execve,
                readiness=lambda *a, **k: "t" * 64,
            )
        self.assertEqual(captured["cwd"], record["cwd"])

    def _fresh_compiled(self, record):
        document = self.runtime.compositions.load("default")
        prepared = self.runtime.prepare(
            document, action="resume", passthrough=[], session_id=record["managed_id"]
        )
        return prepared.result


class CwdFilterToggleTests(CLITestCase):
    def test_filter_shows_only_current_directory(self) -> None:
        here = self.save_session()
        other = self.save_session(
            session_id="97a6194a-1111-4222-8333-444455556666"
        )
        # Move the second record to another cwd.
        record = self.runtime.session_store.load(other["managed_id"])
        record["cwd"] = "/somewhere/else"
        self.runtime.session_store.save(record)
        screen = cli._SessionsScreen(
            self.runtime, palette=cli.tui.MONO_PALETTE
        )
        self.assertEqual(len(screen.records), 2)
        screen.cwd_filter = True
        screen._reload()
        self.assertEqual(
            [r["managed_id"] for r in screen.records], [here["managed_id"]]
        )

    def test_empty_filtered_view_can_toggle_back_to_all(self) -> None:
        other = self.save_session()
        record = self.runtime.session_store.load(other["managed_id"])
        record["cwd"] = "/somewhere/else"
        self.runtime.session_store.save(record)
        screen = cli._SessionsScreen(
            self.runtime, palette=cli.tui.MONO_PALETTE
        )
        with mock.patch.object(screen, "_draw"), mock.patch.object(
            cli.tui, "hide_cursor"
        ), mock.patch.object(
            cli.tui,
            "read_key",
            side_effect=(
                cli.tui.Key("char", "C"),
                cli.tui.Key("char", "C"),
                cli.tui.Key("char", "Q"),
            ),
        ):
            self.assertIsNone(screen.run(object()))
        self.assertFalse(screen.cwd_filter)
        self.assertEqual(len(screen.records), 1)


class SessionEventAndDirectModeTests(CLITestCase):
    def test_session_start_reconciles_runtime_id_without_persisting_transcript(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        runtime_id = "22222222-2222-4222-8222-222222222222"
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": runtime_id,
                "source": "compact",
                "cwd": self.runtime.cwd,
                "model": "claude-fable-5[1m]",
                "transcript_path": "/must/not/be/read.jsonl",
            }
        ).decode("utf-8")
        code, output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0, output)
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["runtime_session_id"], runtime_id)
        self.assertEqual(record["runtime_aliases"][0]["session_id"], FIXED_ID)
        self.assertNotIn("transcript", strict_json.canonical_bytes(record).decode())

    def test_session_event_reads_stdin_without_opening_tty(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": FIXED_ID,
                "source": "startup",
                "cwd": self.runtime.cwd,
            }
        ).decode("utf-8")
        output = io.StringIO()
        with mock.patch.object(
            cli, "_open_tty_streams", side_effect=AssertionError("must not open tty")
        ), mock.patch.object(cli.sys, "stdin", io.StringIO(payload)):
            code = cli.main(
                ["session-event", "start", "--managed-id", FIXED_ID],
                runtime=self.runtime,
                output_stream=output,
                interactive=None,
            )
        self.assertEqual(code, 0, output.getvalue())

    def test_managed_fork_event_warns_and_preserves_parent_runtime(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        fork_id = "22222222-2222-4222-8222-222222222222"
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": fork_id,
                "source": "fork",
                "cwd": self.runtime.cwd,
            }
        ).decode("utf-8")
        code, output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0, output)
        self.assertIn("does not yet have an independent durable", output)
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["runtime_session_id"], FIXED_ID)
        self.assertEqual(record["pending_forks"][0]["session_id"], fork_id)

    def test_managed_model_mismatch_is_recorded_as_repair_needed(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": FIXED_ID,
                "source": "resume",
                "cwd": self.runtime.cwd,
                "model": "gpt-multi-sol-high",
            }
        ).decode("utf-8")
        code, output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0, output)
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["identity_state"], sessions.IDENTITY_REPAIR_NEEDED)
        self.assertEqual(record["observed_model"], "gpt-multi-sol-high")
        response = strict_json.loads(output)
        context = response["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"claude-multi -r {FIXED_ID}", context)
        self.assertIn("sessions transition", context)

    def test_managed_different_lane_is_not_treated_as_equivalent(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["snapshot"]["lead"]["model"] = "sol"
        record["snapshot"]["lead"]["client_selector"] = "gpt-multi-sol-high"
        record["composition_hash"] = strict_json.bundle_digest(record["snapshot"])
        self.runtime.session_store.save(record)
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": FIXED_ID,
                "source": "resume",
                "cwd": self.runtime.cwd,
                "model": "gpt-multi-sol-xhigh",
            }
        ).decode("utf-8")
        code, output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0, output)
        updated = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_REPAIR_NEEDED)
        self.assertEqual(updated["observed_model"], "gpt-multi-sol-xhigh")
        self.assertIn("sessions transition", output)

    def test_ordinary_hook_maps_selector_back_to_catalog_model(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.save(record)
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": FIXED_ID,
                "source": "compact",
                "cwd": self.runtime.cwd,
                "model": "claude-fable-5[1m]",
            }
        ).decode("utf-8")
        code, output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0, output)
        updated = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(updated["ordinary_model"], "fable")
        self.assertEqual(updated["context_profile"], "large")
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_AUTHORITATIVE)
        self.assertNotIn("observed_model", updated)
        self.assertEqual(output, "")

    def test_ordinary_hook_accepts_wire_model_in_same_profile(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.save(record)
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": FIXED_ID,
                "source": "resume",
                "cwd": self.runtime.cwd,
                "model": "claude-fable-5",
            }
        ).decode("utf-8")
        code, output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0, output)
        updated = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(updated["ordinary_model"], "fable")
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_AUTHORITATIVE)
        self.assertEqual(output, "")

    def test_ordinary_hook_accepts_canonical_opus_1m_selector(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.save(record)
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": FIXED_ID,
                "source": "compact",
                "cwd": self.runtime.cwd,
                "model": "claude-opus-4-8[1m]",
            }
        ).decode("utf-8")
        code, output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0, output)
        updated = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(updated["ordinary_model"], "opus")
        self.assertEqual(updated["context_profile"], "large")
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_AUTHORITATIVE)
        self.assertNotIn("observed_model", updated)
        self.assertEqual(output, "")

    def test_ordinary_cross_profile_hook_fails_closed_with_warning(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.save(record)
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": FIXED_ID,
                "source": "resume",
                "cwd": self.runtime.cwd,
                "model": "gpt-multi-sol-high",
            }
        ).decode("utf-8")
        code, output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0, output)
        updated = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(updated["ordinary_model"], "qwen38")
        self.assertEqual(updated["context_profile"], "large")
        self.assertEqual(updated["observed_model"], "gpt-multi-sol-high")
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_REPAIR_NEEDED)
        context = strict_json.loads(output)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("context/compaction profile", context)
        self.assertIn(f"claude-gateway -r {FIXED_ID} --model qwen38", context)

    def test_ordinary_unknown_hook_model_fails_closed(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.save(record)
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": FIXED_ID,
                "source": "compact",
                "cwd": self.runtime.cwd,
                "model": "unknown-provider-model",
            }
        ).decode("utf-8")
        code, output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0, output)
        updated = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(updated["ordinary_model"], "qwen38")
        self.assertEqual(updated["observed_model"], "unknown-provider-model")
        self.assertEqual(updated["identity_state"], sessions.IDENTITY_REPAIR_NEEDED)
        strict_json.loads(output)

    def test_direct_print_launch_has_no_composition_or_agents(self) -> None:
        code, output = self.run_cli(
            ["direct", "--model", "qwen38", "--print-launch"]
        )
        self.assertEqual(code, 0, output)
        self.assertIn("cg:qwen38", output)
        self.assertIn("target gateway:qwen38", output)
        self.assertNotIn("--agents", output)
        self.assertNotIn("--append-system-prompt-file", output)
        self.assertEqual(self.launches, [])

    def test_direct_resume_targets_recorded_runtime_uuid(self) -> None:
        runtime_id = "22222222-2222-4222-8222-222222222222"
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=runtime_id,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.save(record)
        prepared = self.runtime.prepare_direct(
            action="resume", model_id=None, passthrough=[], session_id=FIXED_ID
        )
        self.assertEqual(prepared.result.argv[:2], ["--resume", runtime_id])
        self.assertNotIn("--model", prepared.result.argv)
        self.assertEqual(prepared.record["managed_id"], FIXED_ID)
        self.assertEqual(prepared.record["session_type"], sessions.SESSION_TYPE_ORDINARY)
        self.assertEqual(prepared.record["launch_epoch"], 1)
        command = prepared.result.scope_plan.settings["hooks"]["SessionStart"][0][
            "hooks"
        ][0]["command"]
        self.assertIn("--launch-epoch 1", command)

    def test_direct_continue_uses_ordinary_pointer_not_managed_pointer(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        ordinary = sessions.make_ordinary_record(
            managed_id=OTHER_ID,
            runtime_session_id=OTHER_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )
        self.runtime.session_store.save(ordinary)
        self.runtime.session_store.update_last(self.runtime.cwd, FIXED_ID)
        self.runtime.session_store.update_last(
            self.runtime.cwd,
            OTHER_ID,
            session_type=sessions.SESSION_TYPE_ORDINARY,
        )
        code, output = self.run_cli(["direct", "-c", "--print-launch"])
        self.assertEqual(code, 0, output)
        self.assertIn("--resume\n", output)
        self.assertIn(OTHER_ID, output)

    def test_direct_implicit_resume_refuses_model_repair_state(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            identity_state=sessions.IDENTITY_REPAIR_NEEDED,
        )
        record["observed_model"] = "gpt-multi-sol-high"
        self.runtime.session_store.save(record)
        with self.assertRaisesRegex(cli.CLIError, "explicitly relaunch"):
            self.runtime.prepare_direct(
                action="resume", model_id=None, passthrough=[], session_id=FIXED_ID
            )

    def test_direct_explicit_model_repairs_with_pinned_relaunch(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            identity_state=sessions.IDENTITY_REPAIR_NEEDED,
        )
        record["observed_model"] = "gpt-multi-sol-high"
        self.runtime.session_store.save(record)
        prepared = self.runtime.prepare_direct(
            action="resume", model_id="sol", passthrough=[], session_id=FIXED_ID
        )
        self.assertTrue(prepared.model_relaunch)
        self.assertEqual(prepared.record["ordinary_model"], "sol")
        self.assertEqual(prepared.record["context_profile"], "sol")
        self.assertEqual(
            prepared.result.argv[prepared.result.argv.index("--model") + 1],
            "gpt-multi-sol-high",
        )
        self.assertEqual(
            prepared.result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "372000"
        )
        self.assertEqual(
            prepared.result.env_set["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "90"
        )

    def test_direct_pending_fork_blocks_even_with_explicit_model_repair(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            identity_state=sessions.IDENTITY_REPAIR_NEEDED,
        )
        record["observed_model"] = "gpt-multi-sol-high"
        record["pending_forks"] = [
            {
                "session_id": OTHER_ID,
                "observed_at": "2026-07-22T00:00:00Z",
            }
        ]
        self.runtime.session_store.save(record)
        with self.assertRaisesRegex(cli.CLIError, "unresolved native fork"):
            self.runtime.prepare_direct(
                action="resume",
                model_id="sol",
                passthrough=[],
                session_id=FIXED_ID,
            )

    def test_managed_resume_refuses_cwd_repair_state(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project"
        self.runtime.session_store.save(record)
        document = self.runtime.compositions.load(record["composition_name"])
        with self.assertRaisesRegex(cli.CLIError, "relink-runtime"):
            self.runtime.prepare(
                document,
                action="resume",
                passthrough=[],
                session_id=FIXED_ID,
            )

    def test_managed_model_only_repair_prepares_pinned_relaunch(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_model"] = "gpt-multi-sol-high"
        self.runtime.session_store.save(record)
        document = self.runtime.compositions.load(record["composition_name"])
        prepared = self.runtime.prepare(
            document,
            action="resume",
            passthrough=[],
            session_id=FIXED_ID,
        )
        self.assertTrue(prepared.model_relaunch)
        self.assertIn("--model", prepared.result.argv)
