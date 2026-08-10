"""CLI, quick-confirm, persistence, and resume UX tests for Phase 3."""

from __future__ import annotations

import copy
import curses
import io
import os
import re
import shutil
import stat
import subprocess
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

    def _write_transcript(self, runtime_id: str) -> Path:
        """Metadata-only fixture transcript for the resume gate."""

        transcript = (
            Path(self.runtime.environ["HOME"])
            / ".claude"
            / "projects"
            / cli._native_project_slug(self.runtime.cwd)
            / f"{runtime_id}.jsonl"
        )
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.touch()
        return transcript

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
        # Fixture transcript so the resume gate sees "present" by default
        # (metadata-only existence; gate tests delete/relocate explicitly).
        self._write_transcript(session_id)
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
                "Enter launch · D details · S sessions · G new gateway · ? help · H health · Q cancel",
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
                "Enter transition hint · D details · S sessions · G new gateway · ? help · H health · Q cancel",
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
        self.assertEqual(self.launches[0].resolved.lead.model, "opus5")
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
        self.assertEqual(plan.resolved.lead.model, "opus5")

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
        self.assertEqual(self.launches[0].resolved.lead.model, "opus5")

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
        self.assertEqual(self.launches[0].resolved.lead.model, "opus5")

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
        # The phrase may wrap around the inserted file path; match parts.
        self.assertIn("unsafe", output)
        self.assertIn("malformed", output)
        self.assertIn(str(self.secret_file), output)
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
        self.assertIn("? help", output)
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
        self.assertIn("lead model: opus5 -> sol", output)
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
        self.assertIn("lead model: opus5 -> sol", output)
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
        self._write_transcript(OTHER_ID)
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
            token_helper_command=self.runtime.token_helper_command,
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

    def test_prune_collects_orphaned_digest_only_lead_prompts(self) -> None:
        # Pre-2.2 lead prompts (lead-prompt-<digest>.md, no session suffix)
        # are orphans by construction; session-suffixed prompts with a living
        # record are never touched.
        self.save_session(mode="durable", scope_generation=1)
        root = self.runtime.session_store.root
        orphan = root / "lead-prompt-abcdef0123456789.md"
        state.atomic_write(orphan, b"orphaned prompt\n")
        from claude_multi import compiler as compiler_mod

        living = compiler_mod.lead_prompt_path(
            root, "sha256:" + "a" * 64, FIXED_ID
        )
        state.atomic_write(living, b"living prompt\n")
        code, output = self.run_cli(["doctor", "--prune"])
        self.assertEqual(code, 0, output)
        self.assertIn(f"orphaned pre-2.2 lead prompt {orphan.name}", output)
        self.assertFalse(orphan.exists())
        self.assertTrue(living.exists())

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

    def test_repair_all_continues_past_a_broken_record(self) -> None:
        # One record whose composition no longer resolves against the
        # installed catalog must not abort the pass: it is reported as a
        # failure (exit 1) while every other session is still repaired.
        record = self.save_session(mode="durable", scope_generation=1)
        self._write_scope(FIXED_ID)
        poisoned = self.runtime.session_store.load(FIXED_ID)
        poisoned["snapshot"]["lead"] = {
            **poisoned["snapshot"]["lead"],
            "model": "no-such-model",
        }
        poisoned["snapshot"]["variants"] = []
        self.runtime.session_store.save(poisoned)
        healthy = sessions.make_ordinary_record(
            managed_id=OTHER_ID,
            runtime_session_id=OTHER_ID,
            cwd=self.runtime.cwd,
            model="qwen38",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.save(healthy)
        code, output = self.run_cli(["doctor", "--repair-all"])
        self.assertEqual(code, 1)
        self.assertIn("FAILED", output)
        self.assertIn(FIXED_ID, output)
        # The healthy ordinary session was still converged.
        live = scope_mod.scope_dir(self.runtime.session_store.root, OTHER_ID)
        self.assertTrue(live.is_dir())
        self.assertIn("1 durable session(s) converged", output)

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
        result, win = self._run(self._plan(), ["\x1b"])
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
        self.assertIn("Esc cancel", text)

    def test_enter_returns_perform_intent(self) -> None:
        result, _win = self._run(self._plan(), ["\n"])
        self.assertIsNotNone(result)
        action, prepared, *rest = result
        self.assertEqual(action, "perform")
        self.assertEqual(prepared.result.session_action.kind, "fresh")
        self.assertEqual(rest, [None])  # no gate decision for a fresh launch
        self.assertEqual(self.launches, [])  # perform happens after teardown

    def test_details_toggle_shows_availability(self) -> None:
        _result, win = self._run(self._plan(), ["d", "\x1b"])
        text = win.text()
        self.assertIn("availability", text)
        self.assertIn("scalar", text)
        first_frame = win.frames[0]
        self.assertNotIn("availability", first_frame)

    def test_question_mark_opens_guarantee_modal(self) -> None:
        result, win = self._run(self._plan(), ["?", "\n", "\x1b"])
        self.assertIsNone(result)
        self.assertTrue(
            any("Native workflows (ultracode): ON" in frame for frame in win.frames)
        )

    def test_managed_plan_has_no_recorded_current_switcher(self) -> None:
        # R1 P1: the curses card no longer offers R/C; both keys open the
        # recorded-only redirect Modal and the source never switches.
        record = self.save_session()
        plan = cli.managed_plan(self.runtime, record)
        result, win = self._run(plan, ["c", "\n", "r", "\n", "\x1b"])
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
            result, win = self._run(plan, ["e", "\n", "\x1b"])
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
            result, win = self._run(blocked, ["\n", "\n", "\x1b"])
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
        result, win = self._run(plan, ["\n", "\x1b", "\x1b"])
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
        result, win, _ = self._run(["\x1b"])
        self.assertIsNone(result)
        text = win.text()
        self.assertIn("durable(g2)", text)
        self.assertIn("legacy", text)
        self.assertIn("cm:default", text)
        self.assertIn("R resume", text)
        self.assertIn("[r]esume [t]ransition [f]orget", text)

    def test_empty_table(self) -> None:
        result, win, _ = self._run(["\x1b"])
        self.assertIsNone(result)
        self.assertIn("(no recorded sessions)", win.text())

    def test_forget_modal_states_deletion_and_forgets(self) -> None:
        self.save_session(session_id=FIXED_ID, mode="durable", scope_generation=1)
        result, win, _screen = self._run(["f", "\n", "\x1b"])
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
        self._run(["f", curses.KEY_RIGHT, "\n", "\x1b"])
        self.assertTrue(self.runtime.session_store.exists(FIXED_ID))

    def test_resume_returns_intent(self) -> None:
        self.save_session(session_id=FIXED_ID, mode="durable", scope_generation=1)
        result, _win, _screen = self._run(["r", "\n"])
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "resume")
        self.assertEqual(result[1]["managed_id"], FIXED_ID)

    def test_resume_legacy_modal_states_upgrade(self) -> None:
        self.save_session(session_id=FIXED_ID)
        result, win, _screen = self._run(["r", curses.KEY_RIGHT, "\n", "\x1b"])
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
        result, _win, _screen = self._run(["t", "\x1b", "\x1b"])
        self.assertIsNone(result)


class TransitionTuiScreenTests(unittest.TestCase):
    """TRANSITIONS §3 diff view + exited-confirmation Modal."""

    DIFF = ["lead model: opus5 -> sol", "workflows: native -> native"]

    def _run(self, keys):
        from test_tui import FakeWindow

        screen = cli._TransitionScreen(list(self.DIFF), palette=tui.MONO_PALETTE)
        win = FakeWindow(keys)
        return screen.run(win), win

    def test_diff_rendered_and_confirm_via_modal(self) -> None:
        confirmed, win = self._run(["\n", "\n"])
        self.assertTrue(confirmed)
        text = win.text()
        self.assertIn("lead model: opus5 -> sol", text)
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
        self.assertIn("lead model: opus5 -> sol", output.getvalue())
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
            win = FakeWindow(["\x1b"])
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
        # The hostile cwd is not this directory: widen past the issue-012
        # default filter so the row renders (sanitization is the subject).
        screen.cwd_filter = False
        screen._reload()
        win = FakeWindow(["\x1b"])
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

    def test_qualified_display_name_resolves(self) -> None:
        # The exit hint prints exactly the --name value (issue 013):
        # cm:<composition>@<project> must resolve like the plain forms.
        self.save_session()
        self.assertEqual(
            cli._resolve_resume_target(self.runtime, "cm:default@project"),
            FIXED_ID,
        )

    def test_qualified_name_uses_recorded_cwd(self) -> None:
        record = self.save_session()
        record["cwd"] = "/somewhere/project-x"
        self.runtime.session_store.save(record)
        self.assertEqual(
            cli._resolve_resume_target(self.runtime, "cm:default@project-x"),
            FIXED_ID,
        )

    def test_qualified_name_ambiguity_lists_candidates(self) -> None:
        self.save_session(session_id=FIXED_ID)
        self.save_session(session_id=self.OTHER_ID)
        with self.assertRaises(cli.CLIError) as ctx:
            cli._resolve_resume_target(self.runtime, "cm:default@project")
        text = str(ctx.exception)
        self.assertIn("matches 2 managed sessions", text)
        self.assertIn(FIXED_ID, text)
        self.assertIn(self.OTHER_ID, text)

    def test_unknown_qualified_name_points_at_sessions_list(self) -> None:
        self.save_session()
        with self.assertRaises(cli.CLIError) as ctx:
            cli._resolve_resume_target(self.runtime, "cm:default@nowhere")
        self.assertIn("claude-multi sessions list", str(ctx.exception))

    def test_resume_by_qualified_name_end_to_end(self) -> None:
        self.save_session()
        code, _out = self.run_cli(
            ["-r", "cm:default@project"], "\n", interactive=True
        )
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
        # Ordinary rows get the correct resume form too (D46 finding 5).
        self.assertIn("claude-gateway --resume <uuid>", out)

    def test_curses_open_sessions_cancel_stays(self) -> None:
        self.save_session()
        palette = cli.tui.Palette("mono", False)
        plan = cli.managed_plan(
            self.runtime, self.runtime.session_store.load(FIXED_ID)
        )
        screen = cli._QuickConfirmScreen(self.runtime, plan, passthrough=[], palette=palette)

        class FakeSessions:
            def __init__(self, runtime, *, palette, resume_decision=None):
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
            def __init__(self, runtime, *, palette, resume_decision=None):
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


class OrdinaryLaunchModelsTests(CLITestCase):
    """compiler.ordinary_launch_models: the picker's catalog enumeration (D46)."""

    def test_groups_match_catalog_profiles(self) -> None:
        groups = cli.compiler.ordinary_launch_models(self.runtime.catalog.docs)
        self.assertEqual(
            groups,
            {
                "large": ("fable", "glm52", "kimi-k3", "opus", "opus5", "qwen38"),
                "sol": ("sol",),
            },
        )

    def test_agents_only_models_never_appear(self) -> None:
        # gpt55 has no ordinary profile: direct_context_profile rejects it,
        # so the picker must never offer it.
        groups = cli.compiler.ordinary_launch_models(self.runtime.catalog.docs)
        self.assertNotIn("gpt55", {m for ids in groups.values() for m in ids})


class OrdinaryScreenTuiTests(CLITestCase):
    """The G gateway picker, driven via the FakeWindow double (D46)."""

    def setUp(self) -> None:
        super().setUp()
        self._with_oauth_records()

    def _with_oauth_records(self) -> None:
        """Most real machines have OAuth credential records; without them
        oauth rows legitimately demand sign-in confirmation (2.13.1)."""

        auth_dir = (
            Path(self.runtime.environ["HOME"])
            / self.runtime.catalog.docs["gateway"]["gateway"]["auth_dir"]
        )
        state.ensure_private_dir(auth_dir)
        state.atomic_write(auth_dir / "claude-fixture.json", b"{}")
        state.atomic_write(auth_dir / "codex-fixture.json", b"{}")

    def _run(self, keys, **win_kwargs):
        from test_tui import FakeWindow

        screen = cli._OrdinaryScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(keys, **win_kwargs)
        result = screen.run(win)
        return result, win, screen

    def test_initial_model_preselects_current(self) -> None:
        screen = cli._OrdinaryScreen(
            self.runtime, palette=tui.MONO_PALETTE, initial_model="qwen38"
        )
        self.assertEqual(screen.rows[screen.selected], "qwen38")

    def test_unknown_initial_model_falls_back_to_sol(self) -> None:
        # A recorded model the installed catalog no longer offers (catalog
        # drift) must not strand the picker: cursor lands on the CLI default.
        screen = cli._OrdinaryScreen(
            self.runtime, palette=tui.MONO_PALETTE, initial_model="gone"
        )
        self.assertEqual(screen.rows[screen.selected], "sol")

    def test_switch_purpose_shows_select_copy(self) -> None:
        screen = cli._OrdinaryScreen(
            self.runtime,
            palette=tui.MONO_PALETTE,
            initial_model="kimi-k3",
            purpose="switch",
        )
        from test_tui import FakeWindow

        win = FakeWindow(["\x1b"])
        self.assertIsNone(screen.run(win))
        text = win.text()
        self.assertIn("switch model — gateway session", text)
        self.assertIn("Enter select", text)
        self.assertNotIn("Enter launch", text)

    def test_renders_groups_models_and_availability(self) -> None:
        result, win, _screen = self._run(["\x1b"])
        self.assertIsNone(result)
        text = win.text()
        self.assertIn("gateway session — no composition", text)
        self.assertIn("large · 1M context", text)
        self.assertIn("sol · 372K context", text)
        for model_id in ("fable", "glm52", "kimi-k3", "opus", "opus5", "qwen38", "sol"):
            self.assertIn(model_id, text)
        self.assertIn("GLM-5.2 · alibaba", text)
        # The fixture secret file holds only the Kimi key: both qwen rows
        # carry the compact marker (the full reason lives on the detail
        # line for the selected row — see the confirm test).
        self.assertEqual(text.count("(no secret)"), 2)
        self.assertIn("Enter launch", text)

    def test_default_selection_is_sol_like_the_cli(self) -> None:
        result, _win, screen = self._run(["\n"])
        self.assertEqual(screen.rows[screen.selected], "sol")
        self.assertEqual(result, "sol")

    def test_navigation_picks_model(self) -> None:
        # Rows: fable glm52 kimi-k3 opus opus5 qwen38 | sol; cursor starts
        # on sol (index 6), three steps up land on opus.
        result, _win, _screen = self._run(["k", "k", "k", "\n"])
        self.assertEqual(result, "opus")

    def test_arrow_keys_match_jk(self) -> None:
        import curses as _curses

        # Two steps up from sol lands on opus5 (one step is qwen38, which
        # needs the confirm modal in this fixture — covered below).
        result, _win, _screen = self._run([_curses.KEY_UP, _curses.KEY_UP, "\n"])
        self.assertEqual(result, "opus5")

    def test_unavailable_row_requires_explicit_confirm(self) -> None:
        # sol(6) -> five steps up = glm52(1); Enter opens the confirm modal;
        # the default button is Cancel.
        result, win, _screen = self._run(["k", "k", "k", "k", "k", "\n", "\n", "\x1b"])
        self.assertIsNone(result)
        self.assertTrue(
            any("Provider secret missing" in frame for frame in win.frames)
        )
        self.assertTrue(
            any(
                "missing required secret env:QWEN_CLAUDE_API_KEY" in frame
                for frame in win.frames
            )
        )

    def test_unavailable_row_confirmed_launches_anyway(self) -> None:
        import curses as _curses

        result, _win, _screen = self._run(
            ["k", "k", "k", "k", "k", "\n", _curses.KEY_RIGHT, "\n"]
        )
        self.assertEqual(result, "glm52")

    def test_reason_rechecked_at_enter_not_cached(self) -> None:
        # Screen opens with the qwen secret missing (cached marking), the
        # secret appears while the picker is up, Enter rechecks and launches
        # without any confirm modal.
        screen = cli._OrdinaryScreen(self.runtime, palette=tui.MONO_PALETTE)
        self.assertIn("qwen", screen.unavailable)
        with open(self.secret_file, "a", encoding="utf-8") as handle:
            handle.write("QWEN_CLAUDE_API_KEY=late-addition\n")
        from test_tui import FakeWindow

        result = screen.run(FakeWindow(["k", "k", "k", "k", "k", "\n"]))
        self.assertEqual(result, "glm52")

    def test_row_unblocks_once_secret_appears(self) -> None:
        with open(self.secret_file, "a", encoding="utf-8") as handle:
            handle.write("QWEN_CLAUDE_API_KEY=late-addition\n")
        result, _win, _screen = self._run(["k", "k", "k", "k", "k", "\n"])
        self.assertEqual(result, "glm52")

    def test_esc_creates_no_record(self) -> None:
        result, _win, _screen = self._run(["\x1b"])
        self.assertIsNone(result)
        self.assertEqual(
            list(self.runtime.session_store.sessions_dir.glob("*.json")), []
        )

    def test_help_modal_explains_profiles(self) -> None:
        result, win, _screen = self._run(["?", "\n", "\x1b"])
        self.assertIsNone(result)
        self.assertTrue(
            any("switching across groups is an" in frame for frame in win.frames)
        )

    def test_terminal_floor(self) -> None:
        result, win, _screen = self._run(["\x1b"], height=10, width=50)
        self.assertIsNone(result)
        self.assertIn("terminal too small for the gateway picker", win.text())

    def test_floor_boundary_at_wide_width(self) -> None:
        # Width 90 wraps the worst-case detail to one line: needed is 17,
        # so 16 floors and 17 renders (exact boundary pins).
        result, win, _screen = self._run(["\x1b"], height=16, width=90)
        self.assertIsNone(result)
        self.assertIn("terminal too small for the gateway picker", win.text())
        result, win, _screen = self._run(["\x1b"], height=17, width=90)
        self.assertIsNone(result)
        self.assertIn("gateway session — no composition", win.text())

    def test_full_reason_survives_minimum_width(self) -> None:
        # Width 44 must wrap, never truncate, the complete secret reference.
        _result, win, _screen = self._run(["k", "k", "k", "k", "k", "\x1b"], width=44)
        self.assertIn("env:QWEN_CLAUDE_API_KEY", win.text())

    def test_confirm_modal_intact_at_minimum_width(self) -> None:
        result, win, _screen = self._run(
            ["k", "k", "k", "k", "k", "\n", "\n", "\x1b"], width=44
        )
        self.assertIsNone(result)
        modal_frame = next(
            frame for frame in win.frames if "Provider secret missing" in frame
        )
        # The reference must survive INSIDE the modal's bordered rows — a
        # whole-frame search would pass from the background detail alone.
        self.assertTrue(
            any(
                "env:QWEN_CLAUDE_API_KEY" in line and line.lstrip().startswith("|")
                for line in modal_frame.splitlines()
            )
        )

    def test_malformed_secret_file_marks_rows_without_crashing(self) -> None:
        # A duplicate assignment raises ProxyError directly; invalid bytes
        # are translated to ProxyError by parse_secret_env's decode wrapper.
        # The advisory probe must degrade to the static marking for both.
        for break_style in ("duplicate", "bytes"):
            self.setUp()
            if break_style == "duplicate":
                with open(self.secret_file, "a", encoding="utf-8") as handle:
                    handle.write("KIMI_CLAUDE_API_KEY=duplicate-assignment\n")
            else:
                with open(self.secret_file, "ab") as handle:
                    handle.write(b"\xff\xfe invalid bytes")
            screen = cli._OrdinaryScreen(self.runtime, palette=tui.MONO_PALETTE)
            self.assertEqual(
                screen.unavailable.get("qwen"), cli.ORDINARY_SECRET_FILE_ERROR
            )
            self.assertEqual(
                screen.unavailable.get("kimi"), cli.ORDINARY_SECRET_FILE_ERROR
            )
            from test_tui import FakeWindow

            result = screen.run(FakeWindow(["k", "\x1b"]))
            self.assertIsNone(result)

    def test_empty_catalog_never_indexes_or_launches(self) -> None:
        # Defense parity with the sessions screen's empty guard: a catalog
        # with no ordinary-capable models renders a note and ignores Enter.
        import copy as _copy
        import dataclasses as _dc
        from test_tui import FakeWindow

        docs = _copy.deepcopy(self.runtime.catalog.docs)
        for model in docs["models"]["models"].values():
            model["context"]["ordinary_profile"] = None
        self.runtime.catalog = _dc.replace(self.runtime.catalog, docs=docs)
        result, win, _screen = self._run(["j", "k", "\n", "\x1b"])
        self.assertIsNone(result)
        self.assertIn("(no ordinary-capable models in this catalog)", win.text())
        self.assertEqual(
            list(self.runtime.session_store.sessions_dir.glob("*.json")), []
        )

    def test_narrow_widths_do_not_overdraw(self) -> None:
        # FakeWindow raises curses.error on out-of-bounds writes: surviving
        # the draw at each width is the assertion.
        for width in (44, 56, 71, 90):
            result, _win, _screen = self._run(["\x1b"], width=width)
            self.assertIsNone(result)


class OrdinaryCardKeyTests(CLITestCase):
    """The card's G key + _open_ordinary intent wiring (D46)."""

    def _plan(self):
        return cli.build_quick_plan(
            self.runtime,
            self.runtime.compositions.load("default"),
            action="fresh",
            source="Trusted default",
        )

    def _screen(self):
        return cli._QuickConfirmScreen(
            self.runtime, self._plan(), passthrough=[], palette=tui.MONO_PALETTE
        )

    def _fake_ordinary(self, picked):
        class FakeOrdinary:
            def __init__(self, runtime, *, palette):
                pass

            def run(self, win):
                return picked

        return FakeOrdinary

    def test_cancel_stays_on_card(self) -> None:
        screen = self._screen()
        original = cli._OrdinaryScreen
        cli._OrdinaryScreen = self._fake_ordinary(None)
        try:
            self.assertIsNone(screen._open_ordinary(None))
        finally:
            cli._OrdinaryScreen = original

    def test_pick_returns_perform_intent_with_ordinary_record(self) -> None:
        screen = self._screen()
        original = cli._OrdinaryScreen
        cli._OrdinaryScreen = self._fake_ordinary("glm52")
        try:
            outcome = screen._open_ordinary(None)
        finally:
            cli._OrdinaryScreen = original
        self.assertIsNotNone(outcome)
        action, prepared, *rest = outcome
        self.assertEqual(action, "perform")
        self.assertEqual(rest, [None])
        record = prepared.record
        self.assertEqual(record["session_type"], sessions.SESSION_TYPE_ORDINARY)
        self.assertEqual(record["ordinary_model"], "glm52")
        self.assertEqual(record["context_profile"], "large")
        self.assertIn("--model", prepared.result.argv)
        self.assertIn("claude-multi-glm52-max[1m]", prepared.result.argv)
        self.assertEqual(self.launches, [])  # perform happens after teardown

    def test_prepare_failure_becomes_transient_notice(self) -> None:
        screen = self._screen()
        original = cli._OrdinaryScreen
        cli._OrdinaryScreen = self._fake_ordinary("no-such-model")
        try:
            outcome = screen._open_ordinary(None)
        finally:
            cli._OrdinaryScreen = original
        self.assertIsNone(outcome)
        self.assertIn("no-such-model", screen.gate_notice)
        self.assertEqual(screen.plan.errors, [])  # never poisons the plan

    def test_card_g_dispatches_and_keybar_lists_it(self) -> None:
        from test_tui import FakeWindow

        screen = self._screen()
        original = cli._OrdinaryScreen
        cli._OrdinaryScreen = self._fake_ordinary("kimi-k3")
        try:
            win = FakeWindow(["g"])
            outcome = screen.run(win)
        finally:
            cli._OrdinaryScreen = original
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome[0], "perform")
        self.assertEqual(outcome[1].record["ordinary_model"], "kimi-k3")
        self.assertIn("G new gateway", win.text())

    def test_line_mode_g_prints_listing_and_hint(self) -> None:
        code, out = self.run_cli([], "g\nq\n", interactive=True)
        self.assertEqual(code, 0)
        self.assertIn("ordinary gateway sessions (no composition", out)
        self.assertIn("glm52", out)
        self.assertIn("unavailable: missing required secret env:QWEN_CLAUDE_API_KEY", out)
        self.assertIn("claude-gateway --model <model>", out)

    def test_passthrough_threads_into_prepared_argv(self) -> None:
        screen = cli._QuickConfirmScreen(
            self.runtime,
            self._plan(),
            passthrough=["--verbose"],
            palette=tui.MONO_PALETTE,
        )
        original = cli._OrdinaryScreen
        cli._OrdinaryScreen = self._fake_ordinary("sol")
        try:
            outcome = screen._open_ordinary(None)
        finally:
            cli._OrdinaryScreen = original
        self.assertIsNotNone(outcome)
        self.assertIn("--verbose", outcome[1].result.argv)

    def test_direct_cli_warns_on_missing_secret_before_launch(self) -> None:
        code, out = self.run_cli(["direct", "--model", "glm52"], interactive=False)
        self.assertEqual(code, 0)
        self.assertIn(
            "warning: missing required secret env:QWEN_CLAUDE_API_KEY", out
        )
        self.assertEqual(len(self.launches), 1)  # advisory, never blocking

    def test_direct_cli_print_launch_stays_clean(self) -> None:
        # Dry-run reports never carry the warning, even with the secret gone.
        code, out = self.run_cli(
            ["direct", "--model", "glm52", "--print-launch"], interactive=False
        )
        self.assertEqual(code, 0)
        self.assertNotIn("warning:", out)
        self.assertEqual(self.launches, [])

    def test_direct_cli_silent_when_secret_present(self) -> None:
        with open(self.secret_file, "a", encoding="utf-8") as handle:
            handle.write("QWEN_CLAUDE_API_KEY=present\n")
        code, out = self.run_cli(["direct", "--model", "glm52"], interactive=False)
        self.assertEqual(code, 0)
        self.assertNotIn("warning:", out)

    def _break_secret_file(self, style: str = "duplicate") -> None:
        # "duplicate": ProxyError from the parser; "bytes": invalid UTF-8,
        # translated to ProxyError by parse_secret_env's decode wrapper.
        if style == "duplicate":
            with open(self.secret_file, "a", encoding="utf-8") as handle:
                handle.write("KIMI_CLAUDE_API_KEY=duplicate-assignment\n")
        else:
            with open(self.secret_file, "ab") as handle:
                handle.write(b"\xff\xfe invalid bytes")

    def test_line_mode_g_survives_malformed_secret_file(self) -> None:
        for style in ("duplicate", "bytes"):
            self.setUp()
            self._break_secret_file(style)
            code, out = self.run_cli([], "g\nq\n", interactive=True)
            self.assertEqual(code, 0)
            self.assertIn(cli.ORDINARY_SECRET_FILE_ERROR, out)
            self.assertIn("claude-gateway --model <model>", out)

    def test_direct_oauth_provider_never_probes_secrets(self) -> None:
        # sol's provider is an OAuth pool: the availability probe must not
        # run at all, even with a broken secret env file.
        self._break_secret_file()
        probed = []
        original = cli._ordinary_unavailable

        def spy(runtime):
            probed.append(True)
            return original(runtime)

        cli._ordinary_unavailable = spy
        try:
            code, out = self.run_cli(["direct", "--model", "sol"], interactive=False)
        finally:
            cli._ordinary_unavailable = original
        self.assertEqual(code, 0)
        self.assertNotIn("warning:", out)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(probed, [])

    def test_direct_warns_statically_on_malformed_secret_file(self) -> None:
        for style in ("duplicate", "bytes"):
            self.setUp()
            self._break_secret_file(style)
            code, out = self.run_cli(["direct", "--model", "kimi-k3"], interactive=False)
            self.assertEqual(code, 0)
            self.assertIn(f"warning: {cli.ORDINARY_SECRET_FILE_ERROR}", out)
            self.assertEqual(len(self.launches), 1)  # advisory, never blocking

    def test_direct_flushes_warning_before_launch_boundary(self) -> None:
        import io as _io

        class FlushSpy(_io.StringIO):
            def __init__(self) -> None:
                super().__init__()
                self.flushes = 0

            def flush(self) -> None:
                self.flushes += 1
                super().flush()

        spy = FlushSpy()

        def launch_then_assert_flushed(prepared):
            # execve never flushes Python buffers: the warning must be out
            # before control reaches the launch boundary.
            self.assertGreater(spy.flushes, 0)
            self.launches.append(prepared)
            return 0

        self.runtime.launch_callback = launch_then_assert_flushed
        code = cli.main(
            ["direct", "--model", "glm52"],
            runtime=self.runtime,
            input_stream=None,
            output_stream=spy,
            interactive=False,
        )
        self.assertEqual(code, 0)
        self.assertIn("warning:", spy.getvalue())
        self.assertEqual(len(self.launches), 1)


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


class CompositionPickOrderTests(CLITestCase):
    """015 D-a: MRU derivation and pick order (no new state)."""

    NEXT_ID = 0

    def _save_named(self, name: str) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = name
        self.runtime.compositions.save(document)

    def _record(
        self,
        name: str,
        *,
        cwd: str | None = None,
        created: str = "2026-07-20T00:00:00Z",
        last_seen: str | None = None,
    ):
        CompositionPickOrderTests.NEXT_ID += 1
        managed_id = f"22222222-2222-4222-8222-{CompositionPickOrderTests.NEXT_ID:012d}"
        document = self.runtime.compositions.load("default")
        resolved = self.runtime.resolve_document(document)
        record = sessions.make_record(
            managed_id=managed_id,
            cwd=cwd if cwd is not None else self.runtime.cwd,
            composition_name=name,
            snapshot=composition.snapshot(resolved),
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            now=created,
        )
        if last_seen is not None:
            record["last_seen_at"] = last_seen
        self.runtime.session_store.save(record)
        return record

    def test_never_launched_fall_to_alphabetical_tail(self) -> None:
        self._two_presets_order()
        self.assertEqual(
            cli.composition_pick_order(self.runtime), ["default", "second"]
        )

    def _two_presets_order(self) -> None:
        self._save_named("second")

    def test_mru_orders_by_last_seen(self) -> None:
        self._two_presets_order()
        self._record("second", last_seen="2026-08-09T10:00:00Z")
        self._record("default", last_seen="2026-08-01T10:00:00Z")
        self.assertEqual(
            cli.composition_pick_order(self.runtime), ["second", "default"]
        )

    def test_this_cwd_beats_global_recency(self) -> None:
        self._save_named("alpha")
        self._save_named("second")
        # alpha used elsewhere MORE recently; second used here older.
        self._record("alpha", cwd="/elsewhere", last_seen="2026-08-09T10:00:00Z")
        self._record("second", last_seen="2026-08-01T10:00:00Z")
        self.assertEqual(
            cli.composition_pick_order(self.runtime),
            ["second", "alpha", "default"],
        )

    def test_ties_break_by_name_ascending(self) -> None:
        self._save_named("alpha")
        self._save_named("second")
        stamp = "2026-08-09T10:00:00Z"
        self._record("second", cwd="/elsewhere", last_seen=stamp)
        self._record("alpha", cwd="/elsewhere", last_seen=stamp)
        self.assertEqual(
            cli.composition_pick_order(self.runtime),
            ["alpha", "second", "default"],
        )

    def test_used_here_and_elsewhere_appears_once(self) -> None:
        self._save_named("alpha")
        # alpha used in BOTH this directory (older) and elsewhere (newer):
        # the here tier wins and the name appears exactly once.
        self._record("alpha", last_seen="2026-08-01T10:00:00Z")
        self._record("alpha", cwd="/elsewhere", last_seen="2026-08-09T10:00:00Z")
        order = cli.composition_pick_order(self.runtime)
        self.assertEqual(order, ["alpha", "default"])
        self.assertEqual(order.count("alpha"), 1)

    def test_deleted_composition_is_not_resurrected(self) -> None:
        self._record("ghost", last_seen="2026-08-09T10:00:00Z")
        self.assertEqual(cli.composition_pick_order(self.runtime), ["default"])

    def test_ordinary_records_are_ignored(self) -> None:
        self._two_presets_order()
        record = sessions.make_ordinary_record(
            managed_id="33333333-3333-4333-8333-333333333333",
            runtime_session_id=None,
            cwd=self.runtime.cwd,
            model="kimi-k3",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            now="2026-08-09T10:00:00Z",
        )
        self.runtime.session_store.save(record)
        self.assertEqual(
            cli.composition_pick_order(self.runtime), ["default", "second"]
        )

    def test_unreadable_record_does_not_poison_order(self) -> None:
        self._two_presets_order()
        self._record("second", last_seen="2026-08-09T10:00:00Z")
        bad = self.runtime.session_store.sessions_dir / "44444444-4444-4444-8444-444444444444.json"
        state.atomic_write(bad, b"{not json")
        self.assertEqual(
            cli.composition_pick_order(self.runtime), ["second", "default"]
        )

    def test_cycle_follows_mru_not_alphabetical(self) -> None:
        self._save_named("aaa")  # alphabetically first, never launched
        self._record("default", last_seen="2026-08-09T10:00:00Z")
        document = self.runtime.compositions.load("aaa")
        plan = cli.build_quick_plan(self.runtime, document, action="fresh", source="t")
        cycled = cli._cycle_preset(self.runtime, plan, 1)
        # MRU order: default (used), then aaa (never launched). From aaa,
        # forward wraps to the MRU head.
        self.assertEqual(cycled.document["name"], "default")
        self.assertIn("1/2", cycled.source)

    def test_cycle_from_unsaved_document_lands_on_mru_head(self) -> None:
        self._record("default", last_seen="2026-08-09T10:00:00Z")
        self._save_named("second")
        document = self.runtime.compositions.load("default")
        document["name"] = "unsaved-experiment"
        plan = cli.build_quick_plan(self.runtime, document, action="fresh", source="t")
        cycled = cli._cycle_preset(self.runtime, plan, 1)
        self.assertEqual(cycled.document["name"], "default")


class ComposeListOrderTests(CLITestCase):
    """015 D-a: compose list is MRU-ordered with a last-used column."""

    def test_order_and_last_used_column(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "second"
        self.runtime.compositions.save(document)
        record = sessions.make_record(
            managed_id="55555555-5555-4555-8555-555555555555",
            cwd=self.runtime.cwd,
            composition_name="second",
            snapshot=composition.snapshot(
                self.runtime.resolve_document(self.runtime.compositions.load("default"))
            ),
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            now="2026-08-09T10:00:00Z",
        )
        self.runtime.session_store.save(record)
        code, out = self.run_cli(["compose", "list"], interactive=False)
        self.assertEqual(code, 0)
        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(lines[0].split("\t")[0], "second")
        self.assertEqual(lines[1].split("\t")[0], "default")
        self.assertEqual(lines[0].split("\t")[1], "user")
        self.assertNotEqual(lines[0].split("\t")[2], "-")
        self.assertEqual(lines[1].split("\t")[2], "-")


class CompositionFileTests(CLITestCase):
    """015 D-b: --composition-file on-the-fly ingestion."""

    def _write_document(self, name="fly-by-night") -> Path:
        document = self.runtime.compositions.load("default")
        document["name"] = name
        path = self.root / "fly.json"
        state.atomic_write(path, strict_json.pretty_file_bytes(document))
        return path

    def test_valid_file_launches_noninteractive(self) -> None:
        path = self._write_document()
        code, out = self.run_cli(
            ["--composition-file", str(path)], interactive=False
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        record = self.launches[0].record
        self.assertEqual(record["composition_name"], "fly-by-night")

    def test_file_plan_resumes_from_snapshot_when_never_saved(self) -> None:
        path = self._write_document()
        code, _out = self.run_cli(["--composition-file", str(path)], interactive=False)
        self.assertEqual(code, 0)
        record = self.launches[0].record
        # The name was never saved to the store: resume rebuilds intent from
        # the recorded snapshot (R1 P2 drift stays informational).
        plan = cli.managed_plan(self.runtime, record)
        self.assertTrue(plan.ready, "; ".join(plan.errors))

    def test_stdin_dash_forces_noninteractive(self) -> None:
        document = self.runtime.compositions.load("default")
        document["name"] = "piped-in"
        payload = strict_json.pretty_file_bytes(document).decode("utf-8")
        with unittest.mock.patch("sys.stdin", io.StringIO(payload)):
            code, _out = self.run_cli(
                ["--composition-file", "-"], interactive=False
            )
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].record["composition_name"], "piped-in")

    def test_schema_invalid_file_is_exit_2(self) -> None:
        path = self.root / "bad.json"
        state.atomic_write(path, b'{"version": 1, "name": "x"}')
        code, out = self.run_cli(
            ["--composition-file", str(path)], interactive=False
        )
        self.assertEqual(code, 2)
        self.assertIn("cannot load composition file", out)

    def test_wrong_version_is_exit_2(self) -> None:
        document = self.runtime.compositions.load("default")
        document["version"] = 999
        path = self.root / "wrongver.json"
        state.atomic_write(path, strict_json.pretty_file_bytes(document))
        code, out = self.run_cli(
            ["--composition-file", str(path)], interactive=False
        )
        self.assertEqual(code, 2)
        # The schema's version const rejects before the version gate runs.
        self.assertIn("must equal 1", out)

    def test_mutually_exclusive_with_composition_flag(self) -> None:
        path = self._write_document()
        with self.assertRaises(SystemExit):
            cli.build_parser().parse_args(
                ["--composition", "default", "--composition-file", str(path)]
            )

    def test_resume_refuses_composition_file(self) -> None:
        self.save_session()
        path = self._write_document()
        code, out = self.run_cli(
            ["-r", FIXED_ID, "--composition-file", str(path)], interactive=False
        )
        self.assertEqual(code, 2)
        self.assertIn("resume always uses the recorded composition", out)

    def test_resume_refuses_same_named_composition_file(self) -> None:
        # R1 P1: a file is ALWAYS an unverifiable override — even when its
        # document name equals the recorded composition (save_session records
        # the default composition).
        self.save_session()
        path = self._write_document(name="default")
        code, out = self.run_cli(
            ["-r", FIXED_ID, "--composition-file", str(path)], interactive=False
        )
        self.assertEqual(code, 2)
        self.assertIn("never verifiably", out)

    def test_oversized_stdin_rejected_before_unbounded_read(self) -> None:
        limit = strict_json.DEFAULT_LIMITS.max_bytes
        payload = " " * (limit + 2)
        with unittest.mock.patch("sys.stdin", io.StringIO(payload)):
            code, out = self.run_cli(
                ["--composition-file", "-"], interactive=False
            )
        self.assertEqual(code, 2)
        self.assertIn("byte limit", out)

    def test_print_launch_accepts_composition_file(self) -> None:
        path = self._write_document()
        code, out = self.run_cli(
            ["--composition-file", str(path), "--print-launch"], interactive=False
        )
        self.assertEqual(code, 0)
        self.assertIn("claude argv", out)
        self.assertEqual(self.launches, [])


class OrdinarySelectorDetailTests(CLITestCase):
    """015 D-c: typed /model selectors shown per row (009 discoverability)."""

    def _run(self, keys, **win_kwargs):
        from test_tui import FakeWindow

        screen = cli._OrdinaryScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(keys, **win_kwargs)
        result = screen.run(win)
        return result, win, screen

    def test_selected_row_shows_typed_selector(self) -> None:
        _result, win, _screen = self._run(["k", "\x1b"])  # sol -> qwen38
        self.assertIn("in-session: /model claude-multi-qwen38-max[1m]", win.text())

    def test_sol_row_shows_both_lane_selectors(self) -> None:
        _result, win, _screen = self._run(["\x1b"])  # starts on sol
        self.assertIn("/model gpt-multi-sol-high", win.text())
        self.assertIn("/model gpt-multi-sol-xhigh", win.text())

    def test_unavailable_row_shows_reason_then_selector(self) -> None:
        _result, win, _screen = self._run(["k", "\x1b"])  # qwen38: no secret
        text = win.text()
        reason_at = text.find("missing required secret")
        selector_at = text.find("in-session: /model claude-multi-qwen38-max[1m]")
        self.assertNotEqual(reason_at, -1)
        self.assertNotEqual(selector_at, -1)
        self.assertLess(reason_at, selector_at)

    def test_detail_reserve_covers_selector_lines(self) -> None:
        screen = cli._OrdinaryScreen(self.runtime, palette=tui.MONO_PALETTE)
        for width in (44, 60, 90):
            reserve = screen._detail_reserve(width)
            longest = max(
                len(cli._ordinary_typed_selectors(screen.runtime.catalog.models[m]))
                for m in screen.rows
            )
            self.assertGreaterEqual(reserve, 1 if longest + 13 <= width - 4 else 2)

    def test_text_listing_includes_selectors(self) -> None:
        out = io.StringIO()
        cli._print_ordinary_listing(self.runtime, out)
        text = out.getvalue()
        self.assertIn("/model claude-multi-kimi-k3[1m]", text)
        self.assertIn("/model gpt-multi-sol-high · /model gpt-multi-sol-xhigh", text)


class DoctorServedCrossCheckTests(CLITestCase):
    """015 D-d: served-vs-rendered selector cross-check (loopback only)."""

    def _report(self, served):
        with unittest.mock.patch.object(
            cli.launch, "served_models", return_value=served
        ):
            return cli._doctor_served_report(self.runtime, "t" * 64)

    def _expected(self):
        home = Path(self.runtime.environ["HOME"])
        document, _available, _unavailable = cli.render.build_config_document(
            self.runtime.catalog.docs["gateway"],
            self.runtime.catalog.docs["providers"]["providers"],
            self.runtime.catalog.docs["models"]["models"],
            home=home,
            gateway_token="t" * 64,
            resolve_secret=lambda name: cli.proxy_mod.resolve_secret(
                name, environ=self.runtime.environ
            ),
        )
        return cli.render.rendered_selectors(document)

    def test_full_match_is_silent(self) -> None:
        problems, info = self._report(set(self._expected()))
        self.assertEqual(problems, [])
        self.assertEqual(info, [])

    def test_missing_selector_is_a_problem_naming_restart(self) -> None:
        expected = set(self._expected())
        # A DIRECT-provider alias: no OAuth-record disambiguation applies.
        missing_one = "claude-multi-kimi-k3"
        self.assertIn(missing_one, expected)
        problems, _info = self._report(expected - {missing_one})
        self.assertEqual(len(problems), 1)
        self.assertIn(missing_one, problems[0])
        self.assertIn("restart cli-proxy-api", problems[0])

    def test_missing_oauth_alias_without_record_points_to_login(self) -> None:
        expected = set(self._expected())
        self.assertIn("claude-multi-opus-5", expected)
        # Fixture has no OAuth credential records: login guidance, not restart.
        problems, info = self._report(expected - {"claude-multi-opus-5"})
        self.assertEqual(problems, [])
        self.assertEqual(len(info), 1)
        self.assertIn("claude-login", info[0])
        self.assertIn("claude-multi-opus-5", info[0])

    def test_missing_oauth_alias_with_record_is_a_restart_problem(self) -> None:
        expected = set(self._expected())
        auth_dir = (
            Path(self.runtime.environ["HOME"])
            / self.runtime.catalog.docs["gateway"]["gateway"]["auth_dir"]
        )
        state.ensure_private_dir(auth_dir)
        state.atomic_write(auth_dir / "claude-fixture.json", b"{}")
        problems, _info = self._report(expected - {"claude-multi-opus-5"})
        self.assertEqual(len(problems), 1)
        self.assertIn("claude-multi-opus-5", problems[0])
        self.assertIn("restart cli-proxy-api", problems[0])

    def test_config_drift_is_a_problem_naming_init_and_restart(self) -> None:
        config_dir = cli.proxy_mod.config_dir(Path(self.runtime.environ["HOME"]))
        state.ensure_private_dir(config_dir)
        state.atomic_write(config_dir / "config.yaml", b"stale: true\n")
        problems, _info = self._report(set(self._expected()))
        self.assertEqual(len(problems), 1)
        self.assertIn("claude-multi-proxy init", problems[0])
        self.assertIn("restart cli-proxy-api", problems[0])

    def test_fresh_config_match_produces_no_drift_problem(self) -> None:
        config_dir = cli.proxy_mod.config_dir(Path(self.runtime.environ["HOME"]))
        state.ensure_private_dir(config_dir)
        home = Path(self.runtime.environ["HOME"])
        document, _a, _u = cli.render.build_config_document(
            self.runtime.catalog.docs["gateway"],
            self.runtime.catalog.docs["providers"]["providers"],
            self.runtime.catalog.docs["models"]["models"],
            home=home,
            gateway_token="t" * 64,
            resolve_secret=lambda name: cli.proxy_mod.resolve_secret(
                name, environ=self.runtime.environ
            ),
        )
        state.atomic_write(
            config_dir / "config.yaml",
            cli.render.emit_yaml(document).encode("utf-8"),
        )
        problems, info = self._report(set(self._expected()))
        self.assertEqual(problems, [])
        self.assertEqual(info, [])

    def test_stale_our_shaped_selector_is_info(self) -> None:
        served = set(self._expected()) | {"claude-multi-retired-max"}
        problems, info = self._report(served)
        self.assertEqual(problems, [])
        self.assertEqual(len(info), 1)
        self.assertIn("claude-multi-retired-max", info[0])

    def test_registry_extras_are_ignored(self) -> None:
        served = set(self._expected()) | {"claude-sonnet-4-5", "gpt-5.6-codex-mini"}
        problems, info = self._report(served)
        self.assertEqual(problems, [])
        self.assertEqual(info, [])

    def test_non_200_is_one_info_line(self) -> None:
        with unittest.mock.patch.object(
            cli.launch, "served_models", return_value=None
        ):
            problems, info = cli._doctor_served_report(self.runtime, "t" * 64)
        self.assertEqual(problems, [])
        self.assertEqual(len(info), 1)
        self.assertIn("non-200", info[0])

    def test_connection_failure_reports_a_transient_skip(self) -> None:
        # Readiness passed but the models probe failed (a restart in
        # flight): the radar must SAY it never ran — silence reads as clean.
        with unittest.mock.patch.object(
            cli.launch,
            "served_models",
            side_effect=cli.launch.LaunchError("connection refused"),
        ):
            problems, info = cli._doctor_served_report(self.runtime, "t" * 64)
        self.assertEqual(problems, [])
        self.assertEqual(len(info), 1)
        self.assertIn("changed state after the readiness check", info[0])

    def test_expected_covers_fixture_secret_omissions(self) -> None:
        # The fixture secret file holds only the Kimi key: qwen selectors
        # are not rendered, so they are not expected either.
        expected = self._expected()
        self.assertIn("claude-multi-kimi-k3", expected)
        self.assertNotIn("claude-multi-qwen38-max", expected)
        self.assertIn("claude-multi-opus-5", expected)
        # Aliases only: upstream wire names are NOT served (verified live
        # against a disposable loopback proxy during review).
        self.assertNotIn("k3", expected)


class ProvidersPaneTests(CLITestCase):
    """018: the providers pane inside the G picker."""

    def _open(self, keys, **win_kwargs):
        from test_tui import FakeWindow

        screen = cli._ProvidersScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(keys, **win_kwargs)
        screen.run(win)
        return win, screen

    def test_rows_render_with_honest_vocabulary(self) -> None:
        win, _screen = self._open(["\x1b"])
        text = win.text()
        self.assertIn("providers — local status", text)
        self.assertIn("upstream auth, quota", text)
        self.assertIn("Anthropic · OAuth pool ·", text)
        self.assertIn("OpenAI · OAuth pool ·", text)
        # Fixture secret file holds only the Kimi key.
        self.assertIn("KIMI_CLAUDE_API_KEY present", text)
        self.assertIn("QWEN_CLAUDE_API_KEY missing", text)
        # No gateway in the fixture: route fields say unknown, never "down".
        self.assertIn("unknown", text)
        self.assertNotIn("connected", text.lower())

    def test_oauth_row_setup_shows_login_command(self) -> None:
        # First row is Anthropic (sorted): Enter opens the guidance modal.
        win, _screen = self._open(["\n", "\x1b", "\x1b"])
        self.assertIn("claude-multi-proxy claude-login", win.text())

    def test_masked_entry_writes_secret_and_never_echoes(self) -> None:
        # Qwen row is last: three downs, Enter, type, Enter to buttons,
        # Right to Save, Enter.
        import curses as _curses

        secret = "sk-test-qwen-key-123"
        script = ["j", "j", "j", "\n"] + list(secret)
        script += ["\n", "\n", "\x1b"]
        win, screen = self._open(script)
        content = self.secret_file.read_text()
        self.assertIn(f"QWEN_CLAUDE_API_KEY={secret}", content)
        self.assertIn("KIMI_CLAUDE_API_KEY=cli-test-dummy", content)
        self.assertEqual(
            stat.S_IMODE(self.secret_file.stat().st_mode), 0o600
        )
        for frame in win.frames:
            self.assertNotIn(secret, frame)
        self.assertIn("saved QWEN_CLAUDE_API_KEY (20 chars)", screen.message or "")

    def test_masked_entry_invalid_value_writes_nothing(self) -> None:
        import curses as _curses

        script = ["j", "j", "j", "\n"] + list("bad key with spaces")
        script += ["\n", "\n", "\x1b"]
        _win, screen = self._open(script)
        self.assertIn("unsupported shape", screen.message or "")
        self.assertNotIn("QWEN", self.secret_file.read_text())

    def test_masked_entry_cancel_writes_nothing(self) -> None:
        script = ["j", "j", "j", "\n"] + list("sk-whatever") + ["\x1b", "\x1b"]
        _win, screen = self._open(script)
        self.assertEqual(screen.message, "Set key cancelled.")
        self.assertNotIn("QWEN", self.secret_file.read_text())

    def test_refresh_reloads_facts(self) -> None:
        _win, screen = self._open(["r", "\x1b"])
        self.assertEqual(screen.message, "refreshed.")

    def test_listing_includes_providers_section(self) -> None:
        out = io.StringIO()
        cli._print_ordinary_listing(self.runtime, out)
        text = out.getvalue()
        self.assertIn("providers (local status; names only):", text)
        self.assertIn("KIMI_CLAUDE_API_KEY present", text)
        self.assertIn("QWEN_CLAUDE_API_KEY missing", text)

    def test_picker_p_key_opens_pane(self) -> None:
        from test_tui import FakeWindow

        screen = cli._OrdinaryScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(["p", "\x1b", "\x1b"])
        screen.run(win)
        self.assertTrue(
            any("providers — local status" in frame for frame in win.frames)
        )


class ImprovementBatchTests(CLITestCase):
    """2.13.1: pins for the multi-lens improvement batch."""

    # -- card: secret problems point at the in-TUI fix --------------------
    def test_blocked_card_secret_error_shows_pane_hint(self) -> None:
        document = self.runtime.compositions.load("default")
        document["availability"]["providers"]["openai"] = "off"
        self.runtime.compositions.save(document)  # kimi secret still needed
        os.remove(self.secret_file)  # now the kimi secret is missing too
        code, output = self.run_cli(
            ["--composition", document["name"], "--line"], "\nq\n"
        )
        self.assertEqual(code, 0)
        self.assertIn("BLOCKED", output)

    def test_blocked_card_curses_hint_line(self) -> None:
        from test_tui import FakeWindow

        os.remove(self.secret_file)
        document = self.runtime.compositions.load("default")
        plan = cli.build_quick_plan(self.runtime, document, action="fresh", source="t")
        self.assertFalse(plan.ready)
        screen = cli._QuickConfirmScreen(
            self.runtime, plan, passthrough=[], palette=tui.MONO_PALETTE,
            gateway_check=lambda: None,
        )
        win = FakeWindow(["\x1b"])
        screen.run(win)
        self.assertIn("G (gateway models) → P (providers)", win.text())

    # -- G picker: OAuth sign-in marking ----------------------------------
    def test_oauth_rows_marked_sign_in_needed_without_records(self) -> None:
        from test_tui import FakeWindow

        screen = cli._OrdinaryScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(["\x1b"])
        screen.run(win)
        text = win.text()
        self.assertIn("(sign in needed)", text)  # fixture has no auth records
        self.assertIn("no codex credential record", text)

    def test_sign_in_confirm_names_login_command(self) -> None:
        from test_tui import FakeWindow

        screen = cli._OrdinaryScreen(
            self.runtime, palette=tui.MONO_PALETTE, initial_model="opus5"
        )
        win = FakeWindow(["\n", "\n", "\x1b"])  # Enter → modal → Cancel
        result = screen.run(win)
        self.assertIsNone(result)
        self.assertIn("claude-multi-proxy claude-login", win.text())

    # -- providers pane: help + drift banner ------------------------------
    def test_providers_pane_has_help(self) -> None:
        from test_tui import FakeWindow

        screen = cli._ProvidersScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(["?", "\x1b", "\x1b"])
        screen.run(win)
        self.assertTrue(any("— help" in f for f in win.frames))

    def test_providers_pane_shows_drift_banner(self) -> None:
        from test_tui import FakeWindow

        config_dir = cli.proxy_mod.config_dir(Path(self.runtime.environ["HOME"]))
        state.ensure_private_dir(config_dir)
        state.atomic_write(config_dir / "config.yaml", b"stale: true\n")
        # served probe will fail (no gateway): banner needs down OR drift;
        # drift requires the served probe to have succeeded — simulate by
        # patching served_models to return the expected set.
        expected = set()
        with unittest.mock.patch.object(
            cli.launch, "served_models", return_value=expected
        ):
            screen = cli._ProvidersScreen(self.runtime, palette=tui.MONO_PALETTE)
            win = FakeWindow(["\x1b"])
            screen.run(win)
        self.assertIn("config drift", win.text())

    # -- line mode: h runs doctor ------------------------------------------
    def test_line_mode_h_prints_doctor(self) -> None:
        code, out = self.run_cli([], "h\nq\n", interactive=True)
        self.assertEqual(code, 0)
        self.assertIn("claude-multi doctor: Ready", out)
        self.assertIn("Catalog, compositions, and local gateway are valid.", out)

    # -- models command ----------------------------------------------------
    def test_models_lists_wire_and_typed_selectors(self) -> None:
        code, out = self.run_cli(["models"], interactive=False)
        self.assertEqual(code, 0)
        self.assertIn("wire=qwen3.8-max", out)
        self.assertIn("in-session: /model claude-multi-qwen38-max[1m]", out)
        self.assertIn("wire=gpt-5.6-sol", out)

    # -- discover command ---------------------------------------------------
    def test_discover_marks_cataloged_and_candidates(self) -> None:
        entries = [
            {"id": "k3", "display_name": "K3", "context_length": 1048576,
             "think_efforts": ["low", "high", "max"]},
            {"id": "k4", "display_name": "K4", "context_length": 524288},
        ]
        with unittest.mock.patch.object(
            cli.proxy_mod, "list_provider_models", return_value=entries
        ):
            code, out = self.run_cli(["discover", "kimi"], interactive=False)
        self.assertEqual(code, 0)
        self.assertIn("k3\tcataloged as kimi-k3", out)
        self.assertIn("k4\tonboarding candidate", out)
        self.assertIn("ctx=1048576", out)

    def test_discover_unknown_provider_is_exit_2(self) -> None:
        code, out = self.run_cli(["discover", "nope"], interactive=False)
        self.assertEqual(code, 2)
        self.assertIn("unknown provider", out)

    def test_discover_qwen_reports_unsupported(self) -> None:
        with self.assertRaises(cli.proxy_mod.ProxyError):
            cli.proxy_mod.list_provider_models(
                "qwen",
                self.runtime.catalog.docs["providers"]["providers"],
                environ=self.runtime.environ,
            )

    # -- editor: message visible while BLOCKED; ^C respects dirty ----------
    def test_editor_message_visible_when_blocked(self) -> None:
        from test_editor import make_state, run_form
        from test_tui import FakeWindow

        state = make_state()
        state.document["name"] = ""  # invalid → BLOCKED (and dirty)
        state.message = "refused: edit Availability first"
        outcome, win, _screen = run_form(state, ["\x1b", "\n"])  # Esc, Discard
        self.assertIn("refused: edit Availability first", win.text())

    def test_editor_ctrl_c_clean_exits_without_modal(self) -> None:
        import curses as _curses  # noqa: F401 (contrast with dirty case)
        from test_editor import make_state
        from test_tui import FakeWindow

        state = make_state()
        screen = tui.FormEditorScreen(state)
        win = FakeWindow(["\x03"], height=30, width=90)
        with self.assertRaises(KeyboardInterrupt):
            screen.run(win)

    def test_editor_ctrl_c_dirty_asks_before_discarding(self) -> None:
        import curses as _curses
        from test_editor import make_state
        from test_tui import FakeWindow

        state = make_state()
        screen = tui.FormEditorScreen(state)
        # type x (dirty) → ^C → modal → Right to "Keep editing" → Enter →
        # Esc → dirty discard modal again → Enter (Discard) leaves.
        script = (
            list("x")
            + ["\x03", _curses.KEY_RIGHT, "\n", "\x1b", "\n"]
        )
        win = FakeWindow(script, height=30, width=90)
        outcome = screen.run(win)
        self.assertIsNone(outcome)
        self.assertTrue(
            any("Discard unsaved changes?" in frame for frame in win.frames)
        )

    # -- U confirm ----------------------------------------------------------
    def test_u_cancel_does_not_run_update(self) -> None:
        from test_tui import FakeWindow

        calls = []
        screen = cli._QuickConfirmScreen(
            self.runtime,
            cli.build_quick_plan(
                self.runtime,
                self.runtime.compositions.load("default"),
                action="fresh", source="t",
            ),
            passthrough=[], palette=tui.MONO_PALETTE,
            update_hint=("2.1.218", "2.1.219"),
            gateway_check=lambda: None,
            upgrade_runner=lambda: calls.append(1) or [],
            hint_detector=lambda _contract: None,
            tty_in=io.StringIO("\n"),
        )
        win = FakeWindow(["u", "\x1b", "\x1b"])  # u → modal → Esc cancels
        screen.run(win)
        self.assertEqual(calls, [])


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
            def __init__(self, runtime, *, palette, resume_decision=None):
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
        # The fake native project is not this directory: widen past the
        # issue-012 default filter to reach it.
        screen.cwd_filter = False
        screen._reload()
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
    def test_filter_defaults_on_and_widens_on_toggle(self) -> None:
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
        # Issue 012: the picker opens on this directory's sessions.
        self.assertTrue(screen.cwd_filter)
        self.assertEqual(
            [r["managed_id"] for r in screen.records], [here["managed_id"]]
        )
        screen.cwd_filter = False
        screen._reload()
        self.assertEqual(len(screen.records), 2)

    def test_toggle_roundtrip_returns_to_filtered_default(self) -> None:
        other = self.save_session()
        record = self.runtime.session_store.load(other["managed_id"])
        record["cwd"] = "/somewhere/else"
        self.runtime.session_store.save(record)
        screen = cli._SessionsScreen(
            self.runtime, palette=cli.tui.MONO_PALETTE
        )
        self.assertTrue(screen.cwd_filter)
        self.assertEqual(screen.records, [])
        with mock.patch.object(screen, "_draw"), mock.patch.object(
            cli.tui, "hide_cursor"
        ), mock.patch.object(
            cli.tui,
            "read_key",
            side_effect=(
                cli.tui.Key("char", "C"),
                cli.tui.Key("char", "C"),
                cli.tui.Key("esc"),
            ),
        ):
            self.assertIsNone(screen.run(object()))
        # Roundtrip: ON (default) -> OFF (widened) -> ON (re-filtered).
        self.assertTrue(screen.cwd_filter)
        self.assertEqual(screen.records, [])

    def test_empty_filtered_state_names_the_filter(self) -> None:
        other = self.save_session()
        record = self.runtime.session_store.load(other["managed_id"])
        record["cwd"] = "/somewhere/else"
        self.runtime.session_store.save(record)
        from test_tui import FakeWindow

        result, win, _screen = self._run_sessions(["\x1b"])
        self.assertIsNone(result)
        self.assertIn("(no sessions in this directory — press C to see all)", win.text())
        self.assertIn("cwd filter ON", win.text())

    def test_toggle_messages_and_help_line(self) -> None:
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
                cli.tui.Key("esc"),
            ),
        ):
            self.assertIsNone(screen.run(object()))
        # The two toggle messages fired in order (second C re-filters).
        self.assertEqual(screen.message, "showing only this directory")
        self.assertIn("C cwd filter — ON by default", cli.SESSIONS_HELP)

    def _run_sessions(self, keys):
        from test_tui import FakeWindow

        screen = cli._SessionsScreen(self.runtime, palette=cli.tui.MONO_PALETTE)
        win = FakeWindow(keys)
        return screen.run(win), win, screen


class SessionNameWiringTests(CLITestCase):
    """--name carries the session's project basename end to end (issue 013)."""

    def test_fresh_managed_name_carries_project_basename(self) -> None:
        document = self.runtime.compositions.load("default")
        prepared = self.runtime.prepare(document, action="fresh", passthrough=[])
        index = prepared.result.argv.index("--name")
        # The fixture runtime cwd is <root>/project.
        self.assertEqual(prepared.result.argv[index + 1], "cm:default@project")

    def test_resume_name_uses_recorded_cwd_not_current(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["cwd"] = "/somewhere/project-x"
        self.runtime.session_store.save(record)
        document = self.runtime.compositions.load("default")
        prepared = self.runtime.prepare(
            document, action="resume", passthrough=[], session_id=FIXED_ID
        )
        index = prepared.result.argv.index("--name")
        self.assertEqual(prepared.result.argv[index + 1], "cm:default@project-x")

    def test_direct_name_carries_project_basename(self) -> None:
        prepared = self.runtime.prepare_direct(
            action="fresh", model_id="glm52", passthrough=[]
        )
        index = prepared.result.argv.index("--name")
        self.assertEqual(prepared.result.argv[index + 1], "cg:glm52@project")


class OrdinaryModelSwitchTests(CLITestCase):
    """T on an ordinary row: gate, pick, confirm, model override (D48)."""

    def setUp(self) -> None:
        super().setUp()
        auth_dir = (
            Path(self.runtime.environ["HOME"])
            / self.runtime.catalog.docs["gateway"]["gateway"]["auth_dir"]
        )
        # OAuth credential records exist on a normal machine; without them
        # oauth rows legitimately demand sign-in confirmation (2.13.1).
        state.ensure_private_dir(auth_dir)
        state.atomic_write(auth_dir / "claude-fixture.json", b"{}")
        state.atomic_write(auth_dir / "codex-fixture.json", b"{}")

    def _ordinary(self, model="kimi-k3"):
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model=model,
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.save(record)
        self._write_transcript(FIXED_ID)
        return record

    def _run(self, keys, resume_decision=None):
        from test_tui import FakeWindow

        screen = cli._SessionsScreen(
            self.runtime, palette=tui.MONO_PALETTE, resume_decision=resume_decision
        )
        win = FakeWindow(keys)
        return screen.run(win), win, screen

    def test_full_flow_returns_model_override(self) -> None:
        self._ordinary()
        result, _win, _screen = self._run(["t", "j", "j", "\n", "\n"])
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "resume")
        self.assertEqual(result[1]["managed_id"], FIXED_ID)
        self.assertIsNone(result[2])
        self.assertEqual(result[3], "opus5")

    def test_initial_selection_is_current_model(self) -> None:
        self._ordinary(model="qwen38")
        # Current qwen38 preselected (index 5); one step up lands on the
        # available opus5 (index 4).
        result, _win, _screen = self._run(["t", "k", "\n", "\n"])
        self.assertEqual(result[3], "opus5")

    def test_same_model_pick_is_noop(self) -> None:
        self._ordinary()
        result, _win, screen = self._run(["t", "\n", "\x1b"])
        self.assertIsNone(result)
        self.assertEqual(screen.message, "already on kimi-k3")

    def test_picker_cancel_stays(self) -> None:
        self._ordinary()
        result, _win, _screen = self._run(["t", "\x1b", "\x1b"])
        self.assertIsNone(result)

    def test_confirm_cancel_stays(self) -> None:
        self._ordinary()
        result, _win, screen = self._run(["t", "j", "\n", "\x1b", "\x1b"])
        self.assertIsNone(result)
        self.assertEqual(screen.message, "Switch cancelled.")

    def test_daemon_owned_gate_modal_after_confirm(self) -> None:
        # Gate runs LAST (after picker/no-op/confirm, all cancellable);
        # the full R modal then applies — force via buttons threads the
        # decision into the switch result.
        import curses as _curses

        self._ordinary()
        with mock.patch.object(
            cli, "_live_background_prefixes", return_value=frozenset({"11111111"})
        ):
            result, win, _screen = self._run(
                ["t", "j", "j", "\n", "\n", _curses.KEY_RIGHT, "\n"]
            )
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "resume")
        self.assertEqual(result[2], "force")
        self.assertEqual(result[3], "opus5")

    def test_gate_cancel_after_confirm_stays(self) -> None:
        self._ordinary()
        with mock.patch.object(
            cli, "_live_background_prefixes", return_value=frozenset({"11111111"})
        ):
            result, _win, screen = self._run(
                ["t", "j", "j", "\n", "\n", "\x1b", "\x1b"]
            )
        self.assertIsNone(result)
        self.assertEqual(screen.message, "Switch cancelled.")

    def test_transcript_missing_gate_guidance_shown(self) -> None:
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="kimi-k3",
            context_profile="large",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.save(record)
        # No transcript fixture: after the switch confirm, the gate modal
        # names the missing transcript (R parity), and Esc cancels.
        result, win, screen = self._run(
            ["t", "j", "j", "\n", "\n", "\x1b", "\x1b"]
        )
        self.assertIsNone(result)
        self.assertEqual(screen.message, "Switch cancelled.")
        self.assertTrue(
            any("no transcript file exists for runtime" in frame for frame in win.frames),
            "expected the transcript-missing gate modal to render",
        )

    def test_same_model_pick_skips_secret_confirm(self) -> None:
        # Current model's provider secret is unavailable in this fixture:
        # picking it must no-op directly, never ask a risky-sounding
        # confirm for a relaunch that never happens (review should-fix).
        self._ordinary(model="qwen38")
        result, win, screen = self._run(["t", "\n", "\x1b"])
        self.assertIsNone(result)
        self.assertEqual(screen.message, "already on qwen38")
        self.assertFalse(
            any("Provider secret missing" in frame for frame in win.frames)
        )

    def test_force_decision_skips_gate_modal_and_threads(self) -> None:
        self._ordinary()
        with mock.patch.object(
            cli, "_live_background_prefixes", return_value=frozenset({"11111111"})
        ):
            result, _win, _screen = self._run(
                ["t", "j", "j", "\n", "\n"], resume_decision="force"
            )
        self.assertIsNotNone(result)
        self.assertEqual(result[2], "force")
        self.assertEqual(result[3], "opus5")

    def test_cross_profile_confirm_names_the_rebuild(self) -> None:
        self._ordinary()
        # From kimi-k3 (large) to sol (sol profile): the confirm modal must
        # state the profile change and the fence/compaction rebuild.
        # kimi(2) -> j j j j -> sol(6).
        result, win, _screen = self._run(
            ["t", "j", "j", "j", "j", "\n", "\x1b", "\x1b"]
        )
        self.assertIsNone(result)  # confirm modal cancelled via Esc
        self.assertTrue(
            any(
                "context profile large → sol" in frame
                and "scope fence and compaction policy are rebuilt" in frame
                for frame in win.frames
            )
        )

    def test_card_open_sessions_threads_override_into_prepare(self) -> None:
        record = self._ordinary()
        palette = cli.tui.Palette("mono", False)
        plan = cli.build_quick_plan(
            self.runtime,
            self.runtime.compositions.load("default"),
            action="fresh",
            source="Trusted default",
        )
        screen = cli._QuickConfirmScreen(
            self.runtime, plan, passthrough=["--verbose"], palette=palette
        )

        class FakeSessions:
            def __init__(self, runtime, *, palette, resume_decision=None):
                pass

            def run(self, win):
                return ("resume", record, None, "glm52")

        original = cli._SessionsScreen
        cli._SessionsScreen = FakeSessions
        try:
            outcome = screen._open_sessions(None)
        finally:
            cli._SessionsScreen = original
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome[0], "perform")
        prepared = outcome[1]
        self.assertEqual(prepared.record["ordinary_model"], "glm52")
        self.assertEqual(prepared.record["context_profile"], "large")
        self.assertIn("claude-multi-glm52-max[1m]", prepared.result.argv)
        self.assertIn("--verbose", prepared.result.argv)
        self.assertEqual(self.launches, [])

    def test_driver_threads_override_into_launch(self) -> None:
        import io as _io

        record = self._ordinary()

        class FakeScreen:
            def __init__(self, runtime, *, palette, resume_decision=None):
                pass

            def run(self, win):
                return ("resume", record, None, "glm52")

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
            )
        finally:
            cli._SessionsScreen = original_screen
            cli.tui.run_curses_on_streams = original_runner
        self.assertEqual(code, 0)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(self.launches[0].record["ordinary_model"], "glm52")


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
        with self.assertRaisesRegex(
            cli.CLIError,
            r"claude-gateway -r " + FIXED_ID + r" --model qwen38",
        ):
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

class DoctorRepinAttentionTests(CLITestCase):
    """The standing drift early-warning lands in the Attention tier."""

    def test_doctor_shows_repin_attention(self) -> None:
        original = cli.launch.repin_suggestion
        cli.launch.repin_suggestion = lambda _contract: (
            "Claude 9.9.9 is available at /x/claude while the pinned trust "
            "anchor is 1.1.1; re-pin deliberately"
        )
        try:
            code, output = self.run_cli(["doctor"])
        finally:
            cli.launch.repin_suggestion = original
        self.assertEqual(code, 0, output)
        self.assertIn("Attention", output)
        self.assertIn("re-pin deliberately", output)
        self.assertNotIn("BLOCKED", output)

class QuickConfirmHealthUpdateTests(CLITestCase):
    """The card's update badge, health strip, and U/H actions."""

    def _plan(self):
        return cli.build_quick_plan(
            self.runtime,
            self.runtime.compositions.load("default"),
            action="fresh",
            source="Trusted default",
        )

    def _screen(self, keys, **kwargs):
        from test_tui import FakeWindow

        import io as _io

        screen = cli._QuickConfirmScreen(
            self.runtime,
            self._plan(),
            passthrough=[],
            palette=cli.tui.MONO_PALETTE,
            tty_in=_io.StringIO(kwargs.pop("tty_text", "n\n\n")),
            tty_out=_io.StringIO(),
            **kwargs,
        )
        win = FakeWindow(keys)
        return screen, win

    def test_update_badge_and_health_row_shown(self) -> None:
        # Hermetic pin: the health row reads the catalog contract; pin it
        # in-memory so a packaged re-pin never disturbs this widget test.
        docs = self.runtime.catalog.docs
        original = docs["native-contract"]
        docs["native-contract"] = copy.deepcopy(original)
        docs["native-contract"]["claude"]["validated_version"] = "2.1.218"
        self.addCleanup(docs.__setitem__, "native-contract", original)
        screen, win = self._screen(
            ["\x1b"],
            update_hint=("2.1.218", "2.1.219"),
            gateway_problem=None,
            gateway_checked=True,
        )
        self.assertIsNone(screen.run(win))
        text = win.text()
        self.assertIn("Claude 2.1.219 available · pinned 2.1.218 · press U to update", text)
        self.assertIn("gateway ok · pin 2.1.218", text)
        self.assertIn("U update", text)
        self.assertIn("H health", text)

    def test_gateway_problem_row_is_an_error(self) -> None:
        screen, win = self._screen(
            ["\x1b"], gateway_problem="connection refused", gateway_checked=True
        )
        self.assertIsNone(screen.run(win))
        self.assertIn("gateway unreachable", win.text())

    def test_no_health_lines_when_unchecked(self) -> None:
        screen, win = self._screen(["\x1b"])
        self.assertIsNone(screen.run(win))
        text = win.text()
        self.assertNotIn("gateway ok", text)
        self.assertNotIn("gateway unreachable", text)
        self.assertNotIn("available · pinned", text)
        self.assertNotIn("U update", text)

    def test_u_action_runs_update_and_refreshes(self) -> None:
        calls = []
        screen, win = self._screen(
            ["u", "\n", "\x1b"],
            update_hint=("2.1.218", "2.1.219"),
            gateway_check=lambda: None,
            upgrade_runner=lambda: calls.append(1) or ["active now: override pins 2.1.219"],
            hint_detector=lambda _contract: None,
            tty_text="\n",
        )
        self.assertIsNone(screen.run(win))
        self.assertEqual(calls, [1])
        # hint refreshed through the injected detector (override active -> clears)
        self.assertIsNone(screen.update_hint)
        self.assertTrue(screen.gateway_checked)

    def test_h_action_prints_doctor_and_offers_repair(self) -> None:
        screen, win = self._screen(["h", "\x1b"], gateway_check=lambda: None, tty_text="n\n\n")
        self.assertIsNone(screen.run(win))
        self.assertTrue(screen.gateway_checked)


class ResolveForkCommandTests(CLITestCase):
    """`sessions resolve-fork`: the discard path for pending native forks."""

    def _forked(self):
        self.save_session(mode="durable", scope_generation=1)
        return self.runtime.session_store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd=self.runtime.cwd,
            now="2026-07-22T00:00:00Z",
        )

    def _stuck(self):
        record = self._forked()
        stuck = {
            **record,
            "runtime_session_id": OTHER_ID,
            "runtime_aliases": [
                {
                    "session_id": FIXED_ID,
                    "source": "fork",
                    "observed_at": "2026-07-22T00:01:00Z",
                }
            ],
        }
        self.runtime.session_store.save(stuck)
        return stuck

    def test_resolve_fork_discards_marker(self) -> None:
        self._forked()
        code, output = self.run_cli(["sessions", "resolve-fork", FIXED_ID, OTHER_ID])
        self.assertEqual(code, 0, output)
        self.assertIn("Resolved fork", output)
        self.assertIn("transcript stays", output)
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["pending_forks"], [])
        self.assertEqual(record["identity_state"], "authoritative")

    def test_resolve_fork_authority_holder_points_at_repair_all(self) -> None:
        self._stuck()
        code, output = self.run_cli(["sessions", "resolve-fork", FIXED_ID, OTHER_ID])
        self.assertEqual(code, 2)
        self.assertIn("doctor --repair-all", output)

    def test_resolve_fork_unknown_fork(self) -> None:
        self._forked()
        code, output = self.run_cli(
            ["sessions", "resolve-fork", FIXED_ID, "33333333-3333-4333-8333-333333333333"]
        )
        self.assertEqual(code, 2)
        self.assertIn("no pending fork", output)

    def test_blocked_resume_message_is_actionable(self) -> None:
        self._forked()
        code, output = self.run_cli(["-r", FIXED_ID], interactive=False)
        self.assertEqual(code, 2)
        self.assertIn(OTHER_ID, output)
        self.assertIn("resolve-fork", output)
        self.assertIn("sessions link", output)


class DoctorPendingForkTests(CLITestCase):
    """A fork marker the live runtime already resolved: attention, not damage."""

    def _stuck(self):
        self.save_session(mode="durable", scope_generation=1)
        record = self.runtime.session_store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd=self.runtime.cwd,
            now="2026-07-22T00:00:00Z",
        )
        stuck = {
            **record,
            "runtime_session_id": OTHER_ID,
            "runtime_aliases": [
                {
                    "session_id": FIXED_ID,
                    "source": "fork",
                    "observed_at": "2026-07-22T00:01:00Z",
                }
            ],
        }
        self.runtime.session_store.save(stuck)
        resolved = self.runtime.resolve_document(
            self.runtime.compositions.load("default")
        )
        plan = scope_mod.compile_scope(
            resolved,
            self.runtime.catalog.docs["roles"]["roles"],
            self.runtime.catalog.prompt_bodies,
            scope_mod.catalog_meta_from_docs(self.runtime.catalog.docs),
            managed_id=FIXED_ID,
            hook_command=self.runtime.hook_command,
            token_helper_command=self.runtime.token_helper_command,
        )
        scope_mod.write_scope(self.runtime.session_store.root, FIXED_ID, plan)

    def test_stuck_marker_is_attention_not_blocked(self) -> None:
        self._stuck()
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0, output)
        self.assertIn("already resolved", output)
        self.assertNotIn("BLOCKED", output)

    def test_repair_all_clears_the_marker(self) -> None:
        self._stuck()
        code, output = self.run_cli(["doctor", "--repair-all"])
        self.assertEqual(code, 0, output)
        self.assertIn("cleared a fork marker", output)
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["pending_forks"], []
        )
        code, output = self.run_cli(["doctor"])
        self.assertNotIn("already resolved", output)


class SessionsScreenForkLiveTests(CLITestCase):
    """The picker's fork markers, X resolution, and live (background) ●."""

    def _run(self, keys):
        from test_tui import FakeWindow

        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(keys)
        result = screen.run(win)
        return result, win, screen

    def _forked(self):
        self.save_session(mode="durable", scope_generation=1)
        return self.runtime.session_store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd=self.runtime.cwd,
            now="2026-07-22T00:00:00Z",
        )

    def test_fork_marker_shown_and_x_discards(self) -> None:
        self._forked()
        result, win, _ = self._run(["x", "\n", "\x1b"])
        self.assertIsNone(result)
        self.assertIn("⚠", win.frames[0])
        self.assertTrue(
            any("Discard the pending-fork marker" in frame for frame in win.frames)
        )
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["pending_forks"], []
        )
        self.assertIn("marker discarded", win.text())

    def test_x_survives_a_concurrent_fork_state_change(self) -> None:
        # The pending set changed between screen load and modal confirm
        # (another terminal resolved it): message, not a crashed picker.
        self._forked()
        with mock.patch.object(
            self.runtime.session_store,
            "resolve_fork",
            side_effect=sessions.SessionError(
                f"session {FIXED_ID} has no pending fork {OTHER_ID}"
            ),
        ):
            result, win, _ = self._run(["x", "\n", "\x1b"])
        self.assertIsNone(result)
        self.assertIn("no pending fork", win.text())

    def test_x_auto_clears_authority_holding_marker(self) -> None:
        record = self._forked()
        stuck = {
            **record,
            "runtime_session_id": OTHER_ID,
            "runtime_aliases": [
                {
                    "session_id": FIXED_ID,
                    "source": "fork",
                    "observed_at": "2026-07-22T00:01:00Z",
                }
            ],
        }
        self.runtime.session_store.save(stuck)
        result, win, _ = self._run(["x", "\x1b"])
        self.assertIsNone(result)
        self.assertIn("fork marker cleared", win.text())
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["pending_forks"], []
        )

    def test_x_without_pending_reports_nothing_to_do(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        result, win, _ = self._run(["x", "\x1b"])
        self.assertIsNone(result)
        self.assertIn("no pending fork", win.text())

    def test_resume_key_explains_fork_block(self) -> None:
        self._forked()
        result, win, _ = self._run(["r", "\x1b"])
        self.assertIsNone(result)
        self.assertIn("fork-blocked", win.text())

    def test_live_marker_rendered_for_background_owned_session(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        with mock.patch.object(
            cli, "_live_background_prefixes", return_value=frozenset({"11111111"})
        ):
            result, win, _ = self._run(["\x1b"])
        self.assertIsNone(result)
        self.assertIn("●", win.text())

    def test_native_fork_row_is_annotated(self) -> None:
        self._forked()
        home = Path(self.runtime.environ["HOME"])
        projects = home / ".claude" / "projects"
        target = projects / "-proj"
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{OTHER_ID}.jsonl").write_bytes(b"{}\n")
        found = cli._discover_native_sessions(self.runtime)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["fork_of"], FIXED_ID)
        # The fork's native project is not this directory: opt the screen
        # out of the issue-012 default filter to see it.
        from test_tui import FakeWindow

        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        screen.cwd_filter = False
        screen._reload()
        win = FakeWindow(["\x1b"])
        self.assertIsNone(screen.run(win))
        self.assertIn("(fork)", win.text())


class LiveBackgroundPrefixTests(unittest.TestCase):
    def test_socket_names_become_prefixes(self) -> None:
        import shutil as _shutil

        root = Path(tempfile.mkdtemp(prefix="cc-daemon-test-"))
        self.addCleanup(_shutil.rmtree, root, True)
        sock = root / "916a7da9" / "pty"
        sock.mkdir(parents=True)
        (sock / "707e80d4.sock").write_bytes(b"")
        (sock / "not-a-sock.txt").write_bytes(b"")
        (root / "916a7da9" / "spare").mkdir()
        self.assertEqual(cli._live_background_prefixes(root), frozenset({"707e80d4"}))

    def test_missing_root_is_empty(self) -> None:
        self.assertEqual(
            cli._live_background_prefixes(Path("/nonexistent-cc-daemon-root")),
            frozenset(),
        )


class UpdateProgressTests(CLITestCase):
    """The U action and run_upgrade report phases live (no more silent hang)."""

    def test_run_upgrade_reports_phases(self) -> None:
        import json as _json

        from claude_multi import upgrade as upgrade_mod

        home = Path(self.runtime.environ["HOME"])
        checkout = self.root / "checkout"
        (checkout / "tests").mkdir(parents=True)
        (checkout / "catalog").mkdir()
        contract = _json.loads(
            (CATALOG_ROOT / "catalog" / "native-contract.json").read_text()
        )
        contract["claude"]["validated_version"] = "0.0.1"
        (checkout / "catalog" / "native-contract.json").write_text(_json.dumps(contract))
        (checkout / "version.json").write_text(
            _json.dumps({"version": 1, "launcher_version": "2.5.0", "catalog_version": 4})
        )
        versions = home / ".local" / "share" / "claude" / "versions"
        versions.mkdir(parents=True)
        candidate = versions / "9.9.9"
        candidate.write_bytes(
            b'#!/bin/sh\nif [ "$1" = "--help" ]; then echo "Claude Code"; else echo "9.9.9"; fi\n'
        )
        candidate.chmod(0o755)
        contract["claude"]["executable"]["configured_path"] = str(
            home / ".local" / "bin" / "claude"
        )
        contract["claude"]["executable"]["resolved_path"] = str(candidate)

        class _Done:
            returncode = 0
            stdout = "Ran 1 tests in 0.001s\nOK\n"
            stderr = ""

        notes: list[str] = []
        outcome = upgrade_mod.run_upgrade(
            checkout_root=checkout,
            native_contract=contract,
            override_path=self.root / "cfg" / "native-contract.json",
            today="2026-07-27",
            runner=lambda *a, **k: _Done(),
            progress=notes.append,
        )
        self.assertEqual(outcome.kind, "prepared")
        self.assertTrue(any("evidence suite" in note for note in notes))
        self.assertTrue(any("override" in note for note in notes))

    def test_u_action_streams_header_before_runner_output(self) -> None:
        import io as _io

        from test_tui import FakeWindow

        screen = cli._QuickConfirmScreen(
            self.runtime,
            cli.build_quick_plan(
                self.runtime,
                self.runtime.compositions.load("default"),
                action="fresh",
                source="Trusted default",
            ),
            passthrough=[],
            palette=tui.MONO_PALETTE,
            update_hint=("2.1.218", "2.1.219"),
            gateway_check=lambda: None,
            upgrade_runner=lambda: ["active now: override pins 2.1.219"],
            hint_detector=lambda _contract: None,
            tty_in=_io.StringIO("\n"),
            tty_out=(buffer := _io.StringIO()),
        )
        win = FakeWindow(["u", "\n", "\x1b"])
        self.assertIsNone(screen.run(win))
        text = buffer.getvalue()
        self.assertIn("evidence-gated re-pin", text)
        self.assertLess(
            text.index("evidence-gated re-pin"), text.index("active now")
        )


class ReviewNitRegressionTests(CLITestCase):
    """Cross-family review nits: single --repair converges fork markers too."""

    def test_single_repair_clears_authority_holding_marker(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        record = self.runtime.session_store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd=self.runtime.cwd,
            now="2026-07-22T00:00:00Z",
        )
        stuck = {
            **record,
            "runtime_session_id": OTHER_ID,
            "runtime_aliases": [
                {
                    "session_id": FIXED_ID,
                    "source": "fork",
                    "observed_at": "2026-07-22T00:01:00Z",
                }
            ],
        }
        self.runtime.session_store.save(stuck)
        code, output = self.run_cli(["doctor", "--repair", FIXED_ID])
        self.assertEqual(code, 0, output)
        self.assertIn("cleared a fork marker", output)
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["pending_forks"], []
        )


class ForkHardeningBatchTests(CLITestCase):
    """H16/H18/H21: resume self-heal, ordinary fork context, X remaining."""

    def _stuck(self):
        self.save_session(mode="durable", scope_generation=1)
        record = self.runtime.session_store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd=self.runtime.cwd,
            now="2026-07-22T00:00:00Z",
        )
        stuck = {
            **record,
            "runtime_session_id": OTHER_ID,
            "runtime_aliases": [
                {
                    "session_id": FIXED_ID,
                    "source": "fork",
                    "observed_at": "2026-07-22T00:01:00Z",
                }
            ],
        }
        self.runtime.session_store.save(stuck)

    def test_noninteractive_resume_self_heals_authority_marker(self) -> None:
        self._stuck()
        self._write_transcript(OTHER_ID)
        code, output = self.run_cli(["-r", FIXED_ID], interactive=False)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(
            self.runtime.session_store.load(FIXED_ID)["pending_forks"], []
        )

    def test_x_reports_remaining_genuine_forks(self) -> None:
        from test_tui import FakeWindow

        self._stuck()
        record = self.runtime.session_store.load(FIXED_ID)
        record = {
            **record,
            "pending_forks": record["pending_forks"]
            + [
                {
                    "session_id": "33333333-3333-4333-8333-333333333333",
                    "observed_at": "2026-07-22T00:02:00Z",
                }
            ],
        }
        self.runtime.session_store.save(record)
        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(["x", "\x1b"])
        self.assertIsNone(screen.run(win))
        self.assertIn("1 genuine pending (X again)", win.text())
        remaining = self.runtime.session_store.load(FIXED_ID)["pending_forks"]
        self.assertEqual(
            [item["session_id"] for item in remaining],
            ["33333333-3333-4333-8333-333333333333"],
        )

    def test_ordinary_fork_context_uses_model_flag(self) -> None:
        import argparse as _argparse
        import io as _io
        import json as _json

        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=self.runtime.cwd,
            model="sol",
            context_profile="sol",
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
            now="2026-07-21T00:00:00Z",
        )
        self.runtime.session_store.save(record)
        self.runtime.session_store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd=self.runtime.cwd,
            now="2026-07-22T00:00:00Z",
        )
        out = _io.StringIO()
        args = _argparse.Namespace(
            event="start", managed_id=FIXED_ID, launch_epoch=1
        )
        cli._handle_session_event(
            self.runtime,
            args,
            input_stream=_io.StringIO(
                _json.dumps({"session_id": OTHER_ID, "source": "fork"})
            ),
            output_stream=out,
        )
        text = out.getvalue()
        self.assertIn("--model MODEL", text)
        self.assertNotIn("--composition", text)


def _row_text(win, y: int) -> str:
    return "".join(
        win.grid.get((y, x), (" ", 0))[0] for x in range(win.width)
    ).rstrip()


class CardBottomReservationTests(CLITestCase):
    """H3: Status + keybar survive crowded cards at common terminal sizes."""

    def _screen(self, keys, **kwargs):
        from test_tui import FakeWindow

        import io as _io

        screen = cli._QuickConfirmScreen(
            self.runtime,
            self._plan(),
            passthrough=[],
            palette=tui.MONO_PALETTE,
            tty_in=_io.StringIO(kwargs.pop("tty_text", "n\n\n")),
            tty_out=_io.StringIO(),
            **kwargs,
        )
        return screen, FakeWindow(keys)

    def _plan(self):
        return cli.build_quick_plan(
            self.runtime,
            self.runtime.compositions.load("default"),
            action="fresh",
            source="Trusted default",
        )

    def test_status_badge_visible_at_80x16_crowded(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        screen, win = self._screen(
            ["\x1b"],
            update_hint=("2.1.218", "2.1.220"),
            gateway_problem=None,
            gateway_checked=True,
        )
        win.height, win.width = 16, 80
        self.assertIsNone(screen.run(win))
        text = win.text()
        self.assertIn("Status  Ready", text)
        self.assertIn("Esc", text)

    def test_status_badge_visible_with_details_at_80x24(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        screen, win = self._screen(
            ["d", "\x1b"],
            update_hint=("2.1.218", "2.1.220"),
            gateway_problem=None,
            gateway_checked=True,
        )
        win.height, win.width = 24, 80
        self.assertIsNone(screen.run(win))
        self.assertIn("Status  Ready", win.text())

    def test_blocked_badge_and_error_visible_at_80x14(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        self.runtime.session_store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd=self.runtime.cwd,
            now="2026-07-22T00:00:00Z",
        )
        plan = cli.build_quick_plan(
            self.runtime,
            self.runtime.compositions.load("default"),
            action="resume",
            source="picker",
            record=self.runtime.session_store.load(FIXED_ID),
        )
        self.assertFalse(plan.ready)
        screen = cli._QuickConfirmScreen(
            self.runtime,
            plan,
            passthrough=[],
            palette=tui.MONO_PALETTE,
        )
        from test_tui import FakeWindow

        win = FakeWindow(["\x1b"], height=14, width=80)
        self.assertIsNone(screen.run(win))
        text = win.text()
        # Badge + clean first error line; no overdraw/fusion at 80x14.
        self.assertIn("Status  BLOCKED", text)
        self.assertIn("unresolved native", text)
        self.assertIn("Esc cancel", text)


class SessionsScreenFloorTests(CLITestCase):
    """H6: the sessions screen degrades honestly below its minimum size."""

    def test_tiny_terminal_shows_floor_not_overdraw(self) -> None:
        from test_tui import FakeWindow

        self.save_session(mode="durable", scope_generation=1)
        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(["\x1b"], height=10, width=50)
        self.assertIsNone(screen.run(win))
        self.assertIn("terminal too small", win.text())


class KeyBarExitNeverClippedTests(CLITestCase):
    """H8k: the exit binding survives any overflow."""

    def test_exit_visible_when_three_rows_needed(self) -> None:
        from test_tui import FakeWindow

        bar = tui.KeyBar(
            (
                ("R", "resume"), ("T", "switch comp"), ("X", "resolve fork"),
                ("F", "forget"), ("L", "adopt"), ("C", "cwd filter"),
                ("?", "help"), ("Esc", "quit"),
            )
        )
        win = FakeWindow((), height=10, width=40)
        bar.draw(win, 9, tui.MONO_PALETTE)
        all_text = _row_text(win, 8) + _row_text(win, 9)
        self.assertIn("Esc quit", all_text)


class SelectListFooterReservationTests(CLITestCase):
    """H12: a wrapped footer never overdraws the message row."""

    def test_message_survives_wrapped_footer(self) -> None:
        from test_tui import FakeWindow

        select = tui.SelectList(
            "pick",
            [tui.SelectItem("one"), tui.SelectItem("two")],
            footer=(("Enter", "choose"), ("A", "action-one"),
                    ("B", "action-two"), ("Esc", "back")),
        )
        win = FakeWindow((), height=12, width=42)
        select.message = "something happened"
        select.draw(win, tui.MONO_PALETTE)
        self.assertIn("something happened", _row_text(win, 12 - 1 - select.footer.rows(42)))
        bottom = _row_text(win, 10) + _row_text(win, 11)
        self.assertIn("Esc back", bottom)


class ModalTinyTerminalTests(CLITestCase):
    """H14: tiny terminals never see wrong (negative-sliced) body lines."""

    def test_tiny_modal_shows_buttons_not_wrong_lines(self) -> None:
        from test_tui import FakeWindow

        modal = tui.Modal(
            "title",
            ["first-body-line", "second-body-line", "third-body-line"],
            buttons=(("OK", True),),
        )
        win = FakeWindow((), height=5, width=40)
        modal.draw(win, tui.MONO_PALETTE)
        text = win.text()
        self.assertIn("OK", text)
        self.assertNotIn("third-body-line", text)


class TransitionScreenLayoutTests(CLITestCase):
    """H13: diff lines start below the separator; indicator above the bar."""

    def test_first_diff_line_below_separator(self) -> None:
        from test_tui import FakeWindow

        screen = cli._TransitionScreen(
            ["first-diff-line", "second-diff-line"], palette=tui.MONO_PALETTE
        )
        win = FakeWindow((), height=12, width=60)
        screen._draw(win)
        self.assertEqual(_row_text(win, 2).strip(), "─" * 57)
        self.assertIn("first-diff-line", _row_text(win, 3))


class BrokenOverrideDegradationTests(CLITestCase):
    """An invalid override is never applied — and never bricks the CLI."""

    def _broken_runtime(self):
        config = sessions.config_root(self.runtime.environ)
        state.ensure_private_dir(config)
        state.atomic_write(config / "native-contract.json", b'{"claude": {bad json')
        return cli.Runtime(
            asset_root=CATALOG_ROOT,
            environ=self.runtime.environ,
            cwd=self.runtime.cwd,
            launch_callback=self.runtime.launch_callback,
            doctor_callback=lambda _runtime: [],
            doctor_binary_callback=lambda _contract: ([], ["fixture binary ok"]),
            doctor_daemon_callback=lambda: cli.launch.DaemonStatus(
                state="absent", summary="fixture daemon absent"
            ),
        )

    def test_runtime_degrades_and_doctor_reports(self) -> None:
        runtime = self._broken_runtime()
        self.assertIsNotNone(runtime.broken_override_error)
        self.assertEqual(runtime.catalog.contract_source, "packaged")
        saved, self.runtime = self.runtime, runtime
        try:
            code, output = self.run_cli(["doctor"])
        finally:
            self.runtime = saved
        self.assertEqual(code, 1)
        self.assertIn("BLOCKED", output)
        self.assertIn("invalid and was IGNORED", output)
        self.assertIn("claude-multi update", output)

    def test_update_removes_the_broken_override(self) -> None:
        runtime = self._broken_runtime()
        override = sessions.config_root(self.runtime.environ) / "native-contract.json"
        self.assertTrue(override.exists())
        saved, self.runtime = self.runtime, runtime
        try:
            code, output = self.run_cli(["update"], interactive=False)
        finally:
            self.runtime = saved
        self.assertEqual(code, 0, output)
        self.assertIn("removed unreadable contract override", output)
        self.assertFalse(override.exists())


class FinalGateLayoutTests(CLITestCase):
    """N1: details never draw onto the reserved Status row."""

    def test_details_availability_never_touches_status_row(self) -> None:
        from test_tui import FakeWindow

        screen = cli._QuickConfirmScreen(
            self.runtime,
            cli.build_quick_plan(
                self.runtime,
                self.runtime.compositions.load("default"),
                action="fresh",
                source="Trusted default",
            ),
            passthrough=[],
            palette=tui.MONO_PALETTE,
        )
        win = FakeWindow(["d", "\x1b"], height=20, width=80)
        self.assertIsNone(screen.run(win))
        status_row_text = _row_text(
            win, 20 - tui.KeyBar(screen._keybar().bindings).rows(80) - 2
        )
        self.assertIn("Status  Ready", status_row_text)


class SessionsStopTests(CLITestCase):
    """`sessions stop` + the picker's E action (upstream stop primitive)."""

    def _fixture_contract(self):
        import hashlib as _hashlib

        binary = self.root / "versions" / "2.1.999"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(b"#!/bin/sh\n")
        binary.chmod(0o755)
        return {
            "claude": {
                "validated_version": "2.1.999",
                "executable": {
                    "configured_path": str(self.root / "bin" / "claude"),
                    "resolved_path": str(binary),
                    "sha256": _hashlib.sha256(binary.read_bytes()).hexdigest(),
                    "inspection": "fixture",
                    "inspected_at": "2026-07-27",
                },
            }
        }

    def _runtime_with_fixture_contract(self):
        docs = self.runtime.catalog.docs
        original = docs["native-contract"]
        docs["native-contract"] = self._fixture_contract()
        self.addCleanup(docs.__setitem__, "native-contract", original)

    def _runner(self, captured):
        def run(argv, **kwargs):
            captured.append((list(argv), kwargs.get("env")))
            return subprocess.CompletedProcess(argv, 0, "stopped\n", "")
        return run

    def test_stop_happy_path_invokes_verified_binary(self) -> None:
        self._runtime_with_fixture_contract()
        self.save_session(mode="durable", scope_generation=1)
        captured = []
        with mock.patch.object(cli, "_live_background_prefixes", return_value=frozenset({"11111111"})), \
             mock.patch("subprocess.run", side_effect=self._runner(captured)):
            code, output = self.run_cli(
                ["sessions", "stop", FIXED_ID, "--yes"], interactive=False
            )
        self.assertEqual(code, 0, output)
        argv, env = captured[0]
        self.assertEqual(argv[1:], ["stop", FIXED_ID])
        # The scrubbed env is the only barrier between the gateway
        # token/CLAUDE_MULTI_SECRET_ENV and the subprocess (review S1).
        self.assertEqual(set(env), {"PATH", "HOME"})
        self.assertIn("Stopped session", output)
        self.assertIn("conversation is kept", output)

    def test_stop_refuses_when_not_live(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        with mock.patch.object(cli, "_live_background_prefixes", return_value=frozenset()):
            code, output = self.run_cli(
                ["sessions", "stop", FIXED_ID, "--yes"], interactive=False
            )
        self.assertEqual(code, 0, output)
        self.assertIn("nothing to stop", output)

    def test_stop_refuses_self_stop(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        self.runtime.environ["CLAUDE_MULTI_MANAGED_ID"] = FIXED_ID
        try:
            with mock.patch.object(
                cli, "_live_background_prefixes", return_value=frozenset({"11111111"})
            ):
                code, output = self.run_cli(
                    ["sessions", "stop", FIXED_ID, "--yes"], interactive=False
                )
        finally:
            del self.runtime.environ["CLAUDE_MULTI_MANAGED_ID"]
        self.assertEqual(code, 0, output)
        self.assertIn("running inside", output)

    def test_stop_noninteractive_requires_yes(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        with mock.patch.object(cli, "_live_background_prefixes", return_value=frozenset({"11111111"})):
            code, output = self.run_cli(["sessions", "stop", FIXED_ID], interactive=False)
        self.assertEqual(code, 2)
        self.assertIn("--yes", output)

    def test_stop_confirmation_declined(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        with mock.patch.object(cli, "_live_background_prefixes", return_value=frozenset({"11111111"})):
            code, output = self.run_cli(["sessions", "stop", FIXED_ID], "n\n")
        self.assertEqual(code, 0, output)
        self.assertIn("cancelled", output)

    def test_stop_upstream_failure_surfaces(self) -> None:
        self._runtime_with_fixture_contract()
        self.save_session(mode="durable", scope_generation=1)

        def failing(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 3, "", "no such session")

        with mock.patch.object(cli, "_live_background_prefixes", return_value=frozenset({"11111111"})), \
             mock.patch("subprocess.run", side_effect=failing):
            code, output = self.run_cli(
                ["sessions", "stop", FIXED_ID, "--yes"], interactive=False
            )
        self.assertEqual(code, 2)
        self.assertIn("no such session", output)

    def test_tui_e_stops_live_row(self) -> None:
        from test_tui import FakeWindow

        self._runtime_with_fixture_contract()
        self.save_session(mode="durable", scope_generation=1)
        captured = []
        with mock.patch.object(cli, "_live_background_prefixes", return_value=frozenset({"11111111"})), \
             mock.patch("subprocess.run", side_effect=self._runner(captured)):
            screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
            win = FakeWindow(["e", "\n", "\x1b"])
            self.assertIsNone(screen.run(win))
        self.assertEqual(len(captured), 1)
        self.assertIn("conversation kept", win.text())
        self.assertTrue(any("Stop live session" in frame for frame in win.frames))

    def test_tui_e_not_live_is_a_message(self) -> None:
        from test_tui import FakeWindow

        self.save_session(mode="durable", scope_generation=1)
        with mock.patch.object(cli, "_live_background_prefixes", return_value=frozenset()):
            screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
            win = FakeWindow(["e", "\x1b"])
            self.assertIsNone(screen.run(win))
        self.assertIn("nothing to stop", win.text())

    def test_transition_live_note_shown_when_target_live(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        shifted = self.runtime.compositions.load("default")
        shifted["name"] = "shifted"
        shifted["slots"][0]["model"] = "sol"
        self.runtime.compositions.save(shifted)
        with mock.patch.object(cli, "_live_background_prefixes", return_value=frozenset({"11111111"})):
            code, output = self.run_cli(
                ["sessions", "transition", FIXED_ID, "--composition", "shifted"],
                "n\n",
            )
        self.assertIn("stop it first", output)
        self.assertIn("sessions stop", output)


class SubagentModelRadarTests(CLITestCase):
    """D39 radar: doctor flags a roster-flattening settings env override."""

    def test_doctor_attention_on_user_settings_override(self) -> None:
        home = Path(self.runtime.environ["HOME"])
        settings = home / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text('{"env": {"CLAUDE_CODE_SUBAGENT_MODEL": "gpt-multi-sol-high"}}')
        code, output = self.run_cli(["doctor"])
        self.assertEqual(code, 0, output)
        self.assertIn("CLAUDE_CODE_SUBAGENT_MODEL", output)
        self.assertIn("flattens", output)

    def test_no_attention_without_override(self) -> None:
        code, output = self.run_cli(["doctor"])
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", output)


class ResumeGateTests(CLITestCase):
    """Issue 003: the resume gate — pure evaluation, perform backstop, modal flow."""

    def _gate(self, record, prefixes=frozenset()):
        return cli._evaluate_resume_gate(
            self.runtime, record, live_prefixes=prefixes
        )

    def _transcript(self, record):
        return (
            Path(self.runtime.environ["HOME"])
            / ".claude"
            / "projects"
            / cli._native_project_slug(record["cwd"])
            / f"{sessions.runtime_session_id(record)}.jsonl"
        )

    def _bare_runtime(self):
        return cli.Runtime(
            asset_root=CATALOG_ROOT,
            environ=self.runtime.environ,
            cwd=self.runtime.cwd,
            launch_callback=None,
            doctor_callback=lambda _runtime: [],
        )

    def _prepared(self, record, *, kind="resume", precommitted=False):
        import types

        action = (
            cli.compiler.build_resume(
                sessions.managed_id(record), sessions.runtime_session_id(record)
            )
            if kind == "resume"
            else cli.compiler.build_fresh(sessions.managed_id(record))
        )
        result = types.SimpleNamespace(session_action=action)
        return cli.PreparedLaunch(
            result, record, None, {}, precommitted=precommitted
        )

    # -- evaluator branches --------------------------------------------------

    def test_gate_ok_for_healthy_record(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        self.assertEqual(self._gate(record).kind, "ok")

    def test_gate_repair_needed_carries_relink_message(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project"
        self.runtime.session_store.save(record)
        gate = self._gate(record)
        self.assertEqual(gate.kind, "repair-needed")
        text = " ".join(gate.lines)
        self.assertIn("relink-runtime", text)
        self.assertIn(FIXED_ID, text)
        self.assertIn("--cwd /wrong/project", text)
        self.assertIn(("repair-resume", "Repair & resume"), gate.actions)

    def test_gate_refusal_text_never_splits_commands(self) -> None:
        # Review must-fix 4: the refusal text is built from raw lines, so
        # no path length can hyphen-split `relink-runtime` apart.
        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project-with-a-very-long-path-" + "x" * 80
        self.runtime.session_store.save(record)
        gate = self._gate(record)
        refusal = cli._resume_gate_refusal(gate)
        self.assertIn("relink-runtime", refusal)
        self.assertNotIn("relink- runtime", refusal)

    def test_gate_transcript_blocker_outranks_daemon_owned(self) -> None:
        # Review must-fix 1: live + missing transcript → transcript gate,
        # and force must not bypass it.
        record = self.save_session(mode="durable", scope_generation=1)
        self._transcript(record).unlink()
        gate = self._gate(record, prefixes=frozenset({FIXED_ID}))
        self.assertEqual(gate.kind, "transcript-missing")
        runtime = self._bare_runtime()
        with mock.patch.object(
            runtime, "_live_prefixes", return_value=frozenset({FIXED_ID})
        ):
            with self.assertRaises(cli.CLIError) as raised:
                runtime.perform(self._prepared(record), resume_decision="force")
        self.assertIn("Transcript not found", str(raised.exception))

    def test_gate_daemon_owned_offers_stop_and_force(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        gate = self._gate(record, prefixes=frozenset({FIXED_ID}))
        self.assertEqual(gate.kind, "daemon-owned")
        text = " ".join(gate.lines)
        self.assertIn(f"sessions stop {FIXED_ID}", text)
        self.assertIn("best-effort heuristic", text)
        values = [value for value, _label in gate.actions]
        self.assertEqual(values, ["stop-resume", "force"])

    def test_gate_transcript_missing_guides_restore_or_forget(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        self._transcript(record).unlink()
        gate = self._gate(record)
        self.assertEqual(gate.kind, "transcript-missing")
        text = " ".join(gate.lines)
        self.assertIn("never deletes transcripts", text)
        self.assertIn(f"sessions forget {FIXED_ID}", text)
        self.assertEqual(gate.actions, ())

    def test_gate_transcript_elsewhere_points_at_relink_cwd(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        stray = self._transcript(record)
        other_dir = stray.parent.parent / "-other-project"
        other_dir.mkdir(parents=True)
        stray.rename(other_dir / stray.name)
        gate = self._gate(record)
        self.assertEqual(gate.kind, "transcript-elsewhere")
        text = " ".join(gate.lines)
        self.assertIn("-other-project", text)
        self.assertIn("relink-runtime", text)

    # -- perform backstop ----------------------------------------------------

    def test_perform_refuses_daemon_owned_without_decision(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        runtime = self._bare_runtime()
        with mock.patch.object(
            runtime, "_live_prefixes", return_value=frozenset({FIXED_ID})
        ):
            with self.assertRaises(cli.CLIError) as raised:
                runtime.perform(self._prepared(record))
        self.assertIn(f"sessions stop {FIXED_ID}", str(raised.exception))

    def test_perform_force_bypasses_daemon_owned_only(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        runtime = self._bare_runtime()
        with mock.patch.object(
            cli.launch, "perform_launch", return_value=0
        ) as perform_launch:
            with mock.patch.object(
                runtime, "_live_prefixes", return_value=frozenset({FIXED_ID})
            ):
                code = runtime.perform(
                    self._prepared(record), resume_decision="force"
                )
        self.assertEqual(code, 0)
        self.assertTrue(perform_launch.called)

    def test_perform_never_bypasses_transcript_missing(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        self._transcript(record).unlink()
        runtime = self._bare_runtime()
        with self.assertRaises(cli.CLIError) as raised:
            runtime.perform(self._prepared(record), resume_decision="force")
        self.assertIn("Transcript not found", str(raised.exception))

    def test_perform_exempts_precommitted_transitions(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        runtime = self._bare_runtime()
        with mock.patch.object(cli.launch, "perform_launch", return_value=0):
            with mock.patch.object(
                runtime, "_live_prefixes", return_value=frozenset({FIXED_ID})
            ):
                code = runtime.perform(self._prepared(record, precommitted=True))
        self.assertEqual(code, 0)

    def test_perform_ignores_fresh_actions(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        self._transcript(record).unlink()
        runtime = self._bare_runtime()
        with mock.patch.object(cli.launch, "perform_launch", return_value=0):
            code = runtime.perform(self._prepared(record, kind="fresh"))
        self.assertEqual(code, 0)

    # -- picker modal flow ---------------------------------------------------

    def _run_picker(self, keys):
        from test_tui import FakeWindow

        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow(keys)
        return screen.run(win), win

    def test_picker_repair_and_resume_repairs_identity(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project"
        self.runtime.session_store.save(record)
        result, win = self._run_picker(["r", "\n"])
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "resume")
        self.assertIsNone(result[2] if len(result) > 2 else None)
        repaired = self.runtime.session_store.load(FIXED_ID)
        self.assertNotIn("observed_cwd", repaired)
        self.assertEqual(
            repaired["identity_state"], sessions.IDENTITY_AUTHORITATIVE
        )

    def test_picker_gate_cancel_keeps_record_untouched(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project"
        self.runtime.session_store.save(record)
        before = self.runtime.session_store.read_record_bytes(FIXED_ID)
        result, win = self._run_picker(["r", "\x1b", "\x1b"])
        self.assertIsNone(result)
        self.assertEqual(self.runtime.session_store.read_record_bytes(FIXED_ID), before)

    def test_picker_marker_shows_repair_glyph(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project"
        self.runtime.session_store.save(record)
        _result, win = self._run_picker(["\x1b"])
        self.assertIn("! ", win.text())

    # -- text-mode surfaces --------------------------------------------------

    def test_sessions_show_prepends_relink_message(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project"
        self.runtime.session_store.save(record)
        code, output = self.run_cli(["sessions", "show", FIXED_ID])
        self.assertEqual(code, 0)
        first_line = output.splitlines()[0]
        self.assertIn("relink-runtime", first_line)
        self.assertIn("--cwd /wrong/project", output)

    def test_actions_label_marks_repair_needed_row(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project"
        self.runtime.session_store.save(record)
        label = cli._record_actions_label(record)
        self.assertIn("repair needed (resume blocked)", label)
        self.assertNotIn("[t]ransition", label)


class ResumeGateReviewFixTests(CLITestCase):
    """Cross-family review follow-ups (002/003 hardening round 2)."""

    def _ordinary(self, **overrides):
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
        for key, value in overrides.items():
            record[key] = value
        self.runtime.session_store.save(record)
        return record

    def test_direct_force_flag_equivalent_both_placements(self) -> None:
        parser = cli.build_parser()
        before = parser.parse_args(
            ["--force", "direct", "--resume", FIXED_ID]
        )
        after = parser.parse_args(
            ["direct", "--force", "--resume", FIXED_ID]
        )
        self.assertTrue(getattr(before, "force", False))
        self.assertTrue(getattr(after, "force", False))

    def test_transcript_elsewhere_decodes_real_project_path(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        stray = (
            Path(self.runtime.environ["HOME"])
            / ".claude"
            / "projects"
            / cli._native_project_slug(record["cwd"])
            / f"{FIXED_ID}.jsonl"
        )
        real_project = Path(self.runtime.environ["HOME"]) / "real-project"
        real_project.mkdir(parents=True)
        slug_dir = stray.parent.parent / cli._native_project_slug(real_project)
        slug_dir.mkdir(parents=True)
        stray.rename(slug_dir / stray.name)
        gate = cli._evaluate_resume_gate(self.runtime, record)
        self.assertEqual(gate.kind, "transcript-elsewhere")
        text = " ".join(gate.lines)
        self.assertIn(str(real_project), text)
        self.assertIn(f"--cwd {str(real_project)}", text)

    def test_ordinary_combined_repair_prepares_with_recorded_model(self) -> None:
        record = self._ordinary(
            identity_state=sessions.IDENTITY_REPAIR_NEEDED,
            observed_model="gpt-multi-sol-high",
        )
        # The gate's repair-resume cleared observed_cwd (none here); the
        # picker passes the recorded model explicitly for the leftover
        # model evidence — the prepare must accept the relaunch.
        with self.assertRaises(cli.CLIError):
            self.runtime.prepare_direct(
                action="resume", model_id=None, passthrough=[], session_id=FIXED_ID
            )
        prepared = self.runtime.prepare_direct(
            action="resume",
            model_id=record["ordinary_model"],
            passthrough=[],
            session_id=FIXED_ID,
        )
        self.assertTrue(prepared.model_relaunch)


class ResumeGateRound3Tests(CLITestCase):
    """Second-review hardening: ambiguity, non-files, force threading, notices."""

    def _prepared(self, record, *, precommitted=False):
        import types

        action = cli.compiler.build_resume(
            sessions.managed_id(record), sessions.runtime_session_id(record)
        )
        result = types.SimpleNamespace(session_action=action)
        return cli.PreparedLaunch(
            result, record, None, {}, precommitted=precommitted
        )

    def _bare_runtime(self):
        return cli.Runtime(
            asset_root=CATALOG_ROOT,
            environ=self.runtime.environ,
            cwd=self.runtime.cwd,
            launch_callback=None,
            doctor_callback=lambda _runtime: [],
        )

    def _transcript(self, record):
        return (
            Path(self.runtime.environ["HOME"])
            / ".claude"
            / "projects"
            / cli._native_project_slug(record["cwd"])
            / f"{sessions.runtime_session_id(record)}.jsonl"
        )

    def test_precommitted_still_refuses_transcript_missing(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        self._transcript(record).unlink()
        runtime = self._bare_runtime()
        with self.assertRaises(cli.CLIError) as raised:
            runtime.perform(self._prepared(record, precommitted=True))
        self.assertIn("Transcript not found", str(raised.exception))

    def test_ambiguous_slug_decode_falls_back_to_slug_guidance(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        home = Path(self.runtime.environ["HOME"])
        # Two real paths whose slugs collide: "a-b/c" and "a/b-c".
        for real in (home / "a-b" / "c", home / "a" / "b-c"):
            real.mkdir(parents=True)
        stray = self._transcript(record)
        slug_dir = stray.parent.parent / cli._native_project_slug(home / "a-b" / "c")
        self.assertEqual(
            slug_dir, stray.parent.parent / cli._native_project_slug(home / "a" / "b-c")
        )
        slug_dir.mkdir(parents=True)
        stray.rename(slug_dir / stray.name)
        gate = cli._evaluate_resume_gate(self.runtime, record)
        self.assertEqual(gate.kind, "transcript-elsewhere")
        text = " ".join(gate.lines)
        # No exact --cwd command when the decode is ambiguous.
        self.assertNotIn(str(home / "a-b" / "c"), text)
        self.assertIn("<that project directory>", text)

    def test_directory_named_like_transcript_is_not_a_transcript(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        stray = self._transcript(record)
        stray.unlink()
        (stray.parent / f"{FIXED_ID}.jsonl").mkdir()
        gate = cli._evaluate_resume_gate(self.runtime, record)
        self.assertEqual(gate.kind, "transcript-missing")

    def test_picker_force_skips_daemon_modal(self) -> None:
        from test_tui import FakeWindow

        record = self.save_session(mode="durable", scope_generation=1)
        screen = cli._SessionsScreen(
            self.runtime, palette=tui.MONO_PALETTE, resume_decision="force"
        )
        with mock.patch.object(
            cli, "_live_background_prefixes", return_value=frozenset({FIXED_ID})
        ):
            result = screen.run(FakeWindow(["r"]))
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "resume")
        self.assertEqual(result[2], "force")

    def test_gate_notice_does_not_poison_the_plan(self) -> None:
        from test_tui import FakeWindow

        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project"
        record["runtime_session_id"] = OTHER_ID
        self.runtime.session_store.save(record)
        self._write_transcript(OTHER_ID)
        owner = sessions.make_record(
            session_id=OTHER_ID,
            cwd=self.runtime.cwd,
            composition_name="default",
            snapshot=record["snapshot"],
            catalog_version=self.runtime.catalog_version,
            catalog_hash=self.runtime.catalog.bundle_sha256,
            launcher_version=self.runtime.launcher_version,
        )
        self.runtime.session_store.save(owner)
        plan = cli.managed_plan(self.runtime, record)
        self.assertFalse(plan.ready)  # blocked by the record state itself
        errors_before = list(plan.errors)
        screen = cli._QuickConfirmScreen(
            self.runtime, plan, passthrough=[], palette=tui.MONO_PALETTE
        )
        screen.run(FakeWindow(["\n", "\n", "\x1b"]))
        # The failed repair surfaces as a transient notice; it must NOT
        # append to plan.errors (review should-fix 5).
        self.assertIsNotNone(screen.gate_notice)
        self.assertIn("already owned", screen.gate_notice)
        self.assertEqual(plan.errors, errors_before)
        self.assertEqual(self.launches, [])

    def test_card_repair_modal_repairs_and_unblocks(self) -> None:
        from test_tui import FakeWindow

        record = self.save_session(mode="durable", scope_generation=1)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_cwd"] = "/wrong/project"
        self.runtime.session_store.save(record)
        plan = cli.managed_plan(self.runtime, record)
        self.assertFalse(plan.ready)
        screen = cli._QuickConfirmScreen(
            self.runtime, plan, passthrough=[], palette=tui.MONO_PALETTE
        )
        result = screen.run(FakeWindow(["\n", "\n", "\n"]))
        repaired = self.runtime.session_store.load(FIXED_ID)
        self.assertNotIn("observed_cwd", repaired)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "perform")


class ResumeGateRound4Tests(CLITestCase):
    """Per-issue re-review fixes: ordinary embedded picker, no-force stop,
    target-only precheck."""

    def _bare_runtime(self):
        return cli.Runtime(
            asset_root=CATALOG_ROOT,
            environ=self.runtime.environ,
            cwd=self.runtime.cwd,
            launch_callback=None,
            doctor_callback=lambda _runtime: [],
        )

    def _prepared(self, record, *, precommitted=False):
        import types

        action = cli.compiler.build_resume(
            sessions.managed_id(record), sessions.runtime_session_id(record)
        )
        result = types.SimpleNamespace(session_action=action)
        return cli.PreparedLaunch(
            result, record, None, {}, precommitted=precommitted
        )

    def _ordinary(self, **overrides):
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
        for key, value in overrides.items():
            record[key] = value
        self.runtime.session_store.save(record)
        self._write_transcript(sessions.runtime_session_id(record))
        return record

    def test_embedded_picker_resume_handles_ordinary_record(self) -> None:
        record = self._ordinary()
        plan = cli.build_quick_plan(
            self.runtime,
            self.runtime.compositions.load("default"),
            action="fresh",
            source="t",
        )
        screen = cli._QuickConfirmScreen(
            self.runtime, plan, passthrough=[], palette=tui.MONO_PALETTE
        )

        class FakeSessions:
            def __init__(self, runtime, *, palette, resume_decision=None):
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
        self.assertEqual(
            outcome[1].result.session_action.kind, "resume"
        )

    def test_stop_resume_does_not_bypass_final_gate(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        runtime = self._bare_runtime()
        prepared = self._prepared(record)
        with mock.patch.object(cli.launch, "perform_launch", return_value=0):
            # Decision None: a target that is live again at the boundary
            # is refused (no stale force exemption after stop & resume).
            with mock.patch.object(
                runtime, "_live_prefixes", return_value=frozenset({FIXED_ID})
            ):
                with self.assertRaises(cli.CLIError) as raised:
                    runtime.perform(prepared, resume_decision=None)
        self.assertIn("live in the background", str(raised.exception))

    def test_stop_precheck_requires_target_liveness_not_alias(self) -> None:
        record = self.save_session(mode="durable", scope_generation=1)
        record["runtime_session_id"] = OTHER_ID
        record["runtime_aliases"] = [
            {
                "session_id": FIXED_ID,
                "source": "fork",
                "observed_at": "2026-07-22T00:00:00Z",
            }
        ]
        self.runtime.session_store.save(record)
        # Only the historical alias (FIXED_ID) is live; the current runtime
        # (OTHER_ID) is not — stop must refuse rather than target a
        # non-live identity.
        with mock.patch.object(
            cli, "_live_background_prefixes", return_value=frozenset({FIXED_ID})
        ):
            refusal = cli._stop_precheck(self.runtime, record)
        self.assertIsNotNone(refusal)
        self.assertIn("not live in the background", refusal)


class SubagentModelBleedTests(CLITestCase):
    """D44: model/cwd evidence from subagent contexts and compact events is
    inadmissible; start/resume events stay authoritative."""

    def _event(self, **fields):
        payload = {
            "hook_event_name": "SessionStart",
            "session_id": FIXED_ID,
            "cwd": self.runtime.cwd,
        }
        payload.update(fields)
        return strict_json.canonical_bytes(payload).decode("utf-8")

    def test_compact_model_is_not_observed(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        code, _output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID],
            self._event(source="compact", model="gpt-multi-sol-xhigh"),
        )
        self.assertEqual(code, 0)
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertNotIn("observed_model", record)
        self.assertNotEqual(
            record["identity_state"], sessions.IDENTITY_REPAIR_NEEDED
        )

    def test_compact_does_not_clear_a_legit_observed_model(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        record = self.runtime.session_store.load(FIXED_ID)
        record["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        record["observed_model"] = "gpt-multi-sol-high"
        self.runtime.session_store.save(record)
        code, _output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID],
            self._event(source="compact", model="gpt-multi-sol-xhigh"),
        )
        self.assertEqual(code, 0)
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["observed_model"], "gpt-multi-sol-high")

    def test_agent_context_markers_suppress_model_and_cwd(self) -> None:
        # Defense-in-depth on SYNTHETIC shapes (the 2.1.220 compact payload
        # carries no markers — D44). source="resume" is used so the
        # managed-compact blanket cannot mask the marker branch itself:
        # each marker alone must suppress BOTH model and cwd evidence.
        self.save_session(mode="durable", scope_generation=1)
        worktree = "/home/user/repo/.claude/worktrees/agent-abc123"
        markers = (
            {"agent_id": "agent-abc123"},
            {"agent_transcript_path": "/proj/x/subagents/agent-abc123.jsonl"},
            {"transcript_path": "/proj/x/subagents/agent-abc123.jsonl"},
        )
        for marker in markers:
            with self.subTest(marker=next(iter(marker))):
                record = self.runtime.session_store.load(FIXED_ID)
                for key in ("observed_model", "observed_cwd"):
                    record.pop(key, None)
                self.runtime.session_store.save(record)
                code, _output = self.run_cli(
                    ["session-event", "start", "--managed-id", FIXED_ID],
                    self._event(
                        source="resume",
                        cwd=worktree,
                        model="gpt-multi-sol-xhigh",
                        **marker,
                    ),
                )
                self.assertEqual(code, 0)
                record = self.runtime.session_store.load(FIXED_ID)
                self.assertNotIn("observed_model", record)
                self.assertNotIn("observed_cwd", record)

    def test_resume_model_still_observed(self) -> None:
        self.save_session(mode="durable", scope_generation=1)
        code, _output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID],
            self._event(source="resume", model="gpt-multi-sol-high"),
        )
        self.assertEqual(code, 0)
        record = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(record["observed_model"], "gpt-multi-sol-high")
        self.assertEqual(record["identity_state"], sessions.IDENTITY_REPAIR_NEEDED)


class SubagentModelBleedOrdinaryTests(CLITestCase):
    """D44 ordinary-side: compact reconciliation stays, agent context ignored."""

    def _ordinary(self):
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
        return record

    def test_ordinary_compact_still_reconciles_model(self) -> None:
        self._ordinary()
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": FIXED_ID,
                "source": "compact",
                "cwd": self.runtime.cwd,
                "model": "claude-fable-5[1m]",
            }
        ).decode("utf-8")
        code, _output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0)
        updated = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(updated["ordinary_model"], "fable")

    def test_ordinary_agent_context_event_ignored(self) -> None:
        # Same defense-in-depth note as the managed marker test: synthetic
        # shape; the compact payload carries no such field at 2.1.220.
        self._ordinary()
        payload = strict_json.canonical_bytes(
            {
                "hook_event_name": "SessionStart",
                "session_id": FIXED_ID,
                "source": "compact",
                "cwd": self.runtime.cwd,
                "model": "claude-fable-5[1m]",
                "agent_transcript_path": "/proj/x/subagents/agent-abc.jsonl",
            }
        ).decode("utf-8")
        code, _output = self.run_cli(
            ["session-event", "start", "--managed-id", FIXED_ID], payload
        )
        self.assertEqual(code, 0)
        updated = self.runtime.session_store.load(FIXED_ID)
        self.assertEqual(updated["ordinary_model"], "qwen38")


class SessionsLastUsedSortTests(CLITestCase):
    """Feature: sessions sorted by last used, created also visible."""

    def _save_with_times(
        self, session_id, *, created, last_seen=None, scope_generation=1
    ):
        record = self.save_session(
            session_id=session_id, mode="durable", scope_generation=scope_generation
        )
        record["created_at"] = created
        if last_seen is not None:
            record["last_seen_at"] = last_seen
        self.runtime.session_store.save(record)
        return record

    def test_sort_prefers_last_seen_over_created(self) -> None:
        older_created_recent_use = self._save_with_times(
            FIXED_ID,
            created="2026-07-20T00:00:00Z",
            last_seen="2026-07-29T10:00:00Z",
        )
        newer_created_stale_use = self._save_with_times(
            OTHER_ID,
            created="2026-07-28T00:00:00Z",
            last_seen="2026-07-21T00:00:00Z",
        )
        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        self.assertEqual(
            [r["managed_id"] for r in screen.records], [FIXED_ID, OTHER_ID]
        )
        rows = screen._managed_rows()
        first = rows[0]
        self.assertEqual(first[4], cli._record_last_used_age(older_created_recent_use))
        self.assertEqual(first[5], cli._record_age(older_created_recent_use))

    def test_missing_last_seen_falls_back_to_created(self) -> None:
        # The schema always carries last_seen_at; the helper's fallback is
        # exercised directly at the unit level.
        bare = {"created_at": "2026-07-25T00:00:00Z"}
        self.assertEqual(cli._record_last_seen(bare), bare["created_at"])
        self.assertEqual(
            cli._record_sort_key_last_used(bare), bare["created_at"]
        )
        self._save_with_times(FIXED_ID, created="2026-07-25T00:00:00Z")
        self._save_with_times(
            OTHER_ID,
            created="2026-07-28T00:00:00Z",
            last_seen="2026-07-20T00:00:00Z",
        )
        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        # FIXED's last_seen (make_record default 07-21) precedes its created
        # (07-25) → clamps to created; OTHER (last_seen 07-20 < created
        # 07-28) also clamps: the effective order is by created (07-28 first).
        self.assertEqual(
            [r["managed_id"] for r in screen.records], [OTHER_ID, FIXED_ID]
        )

    def test_rows_show_last_used_and_created_columns(self) -> None:
        self._save_with_times(
            FIXED_ID,
            created="2026-07-25T00:00:00Z",
            last_seen="2026-07-29T10:00:00Z",
        )
        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        row = screen._managed_rows()[0]
        self.assertEqual(len(row), 6)
        self.assertEqual(row[4], cli._record_last_used_age(row_record := self.runtime.session_store.load(FIXED_ID)))
        self.assertEqual(row[5], cli._record_age(row_record))

    def test_text_listing_sorted_by_last_used_with_both_fields(self) -> None:
        self._save_with_times(
            FIXED_ID,
            created="2026-07-20T00:00:00Z",
            last_seen="2026-07-29T10:00:00Z",
        )
        self._save_with_times(
            OTHER_ID,
            created="2026-07-28T00:00:00Z",
            last_seen="2026-07-21T00:00:00Z",
        )
        code, output = self.run_cli(["sessions", "list"], interactive=False)
        self.assertEqual(code, 0)
        self.assertIn("last used", output)
        self.assertIn("created", output)
        self.assertLess(output.index(FIXED_ID), output.index(OTHER_ID))


class SessionsLastUsedLayoutTests(CLITestCase):
    """Width-adaptive table: both ages at >=82 cols, last-used only below."""

    def _seed(self):
        record = self.save_session(mode="durable", scope_generation=1)
        record["created_at"] = "2026-07-25T00:00:00Z"
        record["last_seen_at"] = "2026-07-29T10:00:00Z"
        self.runtime.session_store.save(record)

    def _draw_text(self, width):
        from test_tui import FakeWindow

        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow([], width=width, height=24)
        screen._draw(win)
        return win.text()

    def test_full_width_shows_both_age_columns(self) -> None:
        self._seed()
        text = self._draw_text(90)
        self.assertIn("last used", text)
        self.assertIn("created", text)

    def test_narrow_width_keeps_last_used_drops_created_column(self) -> None:
        self._seed()
        text = self._draw_text(80)
        self.assertIn("last used", text)
        self.assertNotIn("created", text)

    def test_pre_creation_last_seen_falls_back_to_created(self) -> None:
        record = {
            "created_at": "2026-07-28T00:00:00Z",
            "last_seen_at": "2026-07-20T00:00:00Z",
        }
        self.assertEqual(cli._record_last_seen(record), record["created_at"])
        malformed = {
            "created_at": "2026-07-28T00:00:00Z",
            "last_seen_at": "not-a-date",
        }
        self.assertEqual(cli._record_last_seen(malformed), malformed["created_at"])
        missing = {"created_at": "2026-07-28T00:00:00Z"}
        self.assertEqual(cli._record_last_seen(missing), missing["created_at"])


class SessionsLastUsedTierTests(CLITestCase):
    """Every width tier renders honestly (fits its range)."""

    def _seed(self):
        record = self.save_session(mode="durable", scope_generation=1)
        record["created_at"] = "2026-07-25T00:00:00Z"
        record["last_seen_at"] = "2026-07-29T10:00:00Z"
        self.runtime.session_store.save(record)

    def _header(self, width):
        from test_tui import FakeWindow

        screen = cli._SessionsScreen(self.runtime, palette=tui.MONO_PALETTE)
        win = FakeWindow([], width=width, height=24)
        screen._draw(win)
        return win.text()

    def test_tiers_render_last_used_at_every_width(self) -> None:
        self._seed()
        for width in (44, 57, 71, 82, 100):
            with self.subTest(width=width):
                self.assertIn("last used", self._header(width))

    def test_created_only_at_full_width(self) -> None:
        self._seed()
        self.assertIn("created", self._header(82))
        self.assertNotIn("created", self._header(81))

    def test_narrowest_tier_drops_mode_and_cwd(self) -> None:
        self._seed()
        text = self._header(44)
        self.assertIn("last used", text)
        self.assertNotIn("mode", text)

    def test_last_used_age_matches_sort_value(self) -> None:
        record = {
            "created_at": "2026-99-99T00:00:00Z",
            "last_seen_at": "2026-07-20T00:00:00Z",
        }
        # Calendar-invalid created sorts as the effective value; the display
        # must show the SAME value, never a divergent fallback.
        self.assertEqual(
            cli._record_last_used_age(record), cli._record_last_seen(record)
        )
