"""Pure editor-state and numbered fallback tests for Phase 3."""

from __future__ import annotations

import copy
import io
import unittest
from pathlib import Path

from claude_multi import catalog
from claude_multi.editor import EditorError, EditorState, run_line_editor


CATALOG_ROOT = Path(__file__).resolve().parents[1]


def make_state(document=None, docs=None) -> EditorState:
    bundle = catalog.load_catalog(CATALOG_ROOT)
    return EditorState(
        docs or bundle.docs,
        document or bundle.default_composition,
        bundle.default_composition,
    )


class NavigationTests(unittest.TestCase):
    def test_sections_revisit_without_losing_document(self) -> None:
        state = make_state()
        state.set_native("general_purpose", "on")
        state.select_section(3)
        state.move_row(1, 3)
        state.select_section(1)
        state.move_row(2, 8)
        state.select_section(3)
        self.assertEqual(state.row(3), 1)
        self.assertEqual(state.document["native_agents"]["general_purpose"], "on")

    def test_section_wrap_and_dirty(self) -> None:
        state = make_state()
        self.assertFalse(state.dirty)
        state.next_section(-1)
        self.assertEqual(state.section, "Save or launch")
        state.set_native("plan", "off")
        self.assertTrue(state.dirty)

    def test_resize_independent_state(self) -> None:
        state = make_state()
        before = copy.deepcopy(state.document)
        state.select_section(2)
        state.row_by_section["Roles"] = 2
        self.assertEqual(state.document, before)
        self.assertEqual(state.row(3), 2)


class AvailabilityTests(unittest.TestCase):
    def test_provider_preview_lists_every_invalidated_slot(self) -> None:
        state = make_state()
        change = state.preview_availability("provider", "kimi", "off")
        self.assertEqual(
            [item.label.split(" · ")[0] for item in change.invalidated],
            ["Analyst", "Implementer"],
        )
        self.assertTrue(any("exceeds provider" in error for error in change.resulting_errors))

    def test_model_preview_lists_lead(self) -> None:
        state = make_state()
        change = state.preview_availability("model", "fable", "off")
        self.assertEqual(len(change.invalidated), 1)
        self.assertEqual(change.invalidated[0].kind, "lead")
        self.assertIn("Lead", change.invalidated[0].label)

    def test_invalidation_requires_explicit_confirmation(self) -> None:
        state = make_state()
        change = state.preview_availability("provider", "openai", "off")
        with self.assertRaisesRegex(EditorError, "explicit confirmation"):
            state.apply_availability(change)
        self.assertEqual(state.provider_scope("openai"), "lead+agents")

    def test_confirm_preserves_invalidated_selections(self) -> None:
        state = make_state()
        before = copy.deepcopy(state.document["slots"])
        change = state.preview_availability("provider", "openai", "off")
        state.apply_availability(change, confirm=True)
        self.assertEqual(state.document["slots"], before)
        self.assertTrue(state.validation_errors())

    def test_provider_upper_bound_visible_in_effective_use(self) -> None:
        state = make_state()
        change = state.preview_availability("provider", "openai", "agents")
        state.apply_availability(change)
        self.assertNotIn("lead", state.effective_uses("sol"))
        self.assertIn("agents", state.effective_uses("sol"))

    def test_omitted_catalog_model_is_new_off(self) -> None:
        state = make_state()
        del state.document["availability"]["models"]["opus"]
        self.assertTrue(state.model_is_new("opus"))
        self.assertEqual(state.model_scope("opus"), "off")

    def test_unknown_scope_rejected(self) -> None:
        state = make_state()
        with self.assertRaises(EditorError):
            state.preview_availability("model", "sol", "everything")


class VariantTests(unittest.TestCase):
    def test_first_variant_is_preferred(self) -> None:
        state = make_state()
        state.document["slots"] = [state.lead_slot()]
        state.toggle_variant("cm-reviewer", "gpt55", "high")
        variants = state.variants_for_role("cm-reviewer")
        self.assertEqual(len(variants), 1)
        self.assertTrue(variants[0]["preferred"])

    def test_additional_variant_does_not_replace_preferred(self) -> None:
        state = make_state()
        state.toggle_variant("cm-analyst", "fable", "max")
        variants = state.variants_for_role("cm-analyst")
        self.assertEqual(sum(bool(item["preferred"]) for item in variants), 1)
        self.assertEqual(next(item["model"] for item in variants if item["preferred"]), "sol")

    def test_set_preferred_is_explicit_and_unique(self) -> None:
        state = make_state()
        state.set_preferred("cm-analyst", "kimi-k3", "max")
        preferred = [item for item in state.variants_for_role("cm-analyst") if item["preferred"]]
        self.assertEqual([item["model"] for item in preferred], ["kimi-k3"])

    def test_cannot_remove_preferred_while_alternatives_remain(self) -> None:
        state = make_state()
        with self.assertRaisesRegex(EditorError, "preferred"):
            state.toggle_variant("cm-analyst", "sol", "high")
        self.assertTrue(state.has_variant("cm-analyst", "sol", "high"))

    def test_remove_preferred_with_explicit_replacement(self) -> None:
        state = make_state()
        state.toggle_variant(
            "cm-analyst",
            "sol",
            "high",
            replacement_preferred=("kimi-k3", "max"),
        )
        variants = state.variants_for_role("cm-analyst")
        self.assertEqual([item["model"] for item in variants], ["kimi-k3"])
        self.assertTrue(variants[0]["preferred"])

    def test_role_may_have_zero_variants(self) -> None:
        state = make_state()
        state.toggle_variant("cm-reviewer", "gpt55", "high", replacement_preferred=("opus", "xhigh"))
        state.toggle_variant("cm-reviewer", "opus", "xhigh")
        self.assertEqual(state.variants_for_role("cm-reviewer"), [])

    def test_preferred_must_be_selected(self) -> None:
        state = make_state()
        with self.assertRaisesRegex(EditorError, "already be selected"):
            state.set_preferred("cm-reviewer", "fable", "max")

    def test_cannot_add_unavailable_variant(self) -> None:
        state = make_state()
        change = state.preview_availability("model", "fable", "lead")
        state.apply_availability(change)
        with self.assertRaisesRegex(EditorError, "Availability"):
            state.toggle_variant("cm-analyst", "fable", "max")


class LeadNativeAndActionTests(unittest.TestCase):
    def test_lead_requires_availability(self) -> None:
        state = make_state()
        with self.assertRaisesRegex(EditorError, "Availability"):
            state.set_lead("opus")
        change = state.preview_availability("model", "opus", "lead+agents")
        state.apply_availability(change)
        state.set_lead("opus")
        self.assertEqual(state.lead_slot()["model"], "opus")

    def test_missing_lead_is_blocked_and_repair_inserts_first(self) -> None:
        document = copy.deepcopy(make_state().document)
        original_non_lead = [
            copy.deepcopy(slot)
            for slot in document["slots"]
            if slot["role"] != "cm-lead"
        ]
        document["slots"] = original_non_lead
        state = make_state(document)
        self.assertIsNone(state.lead_slot())
        self.assertEqual(state.lead_summary(), "none selected")
        self.assertTrue(state.validation_errors())
        state.set_lead("fable")
        self.assertEqual(
            state.document["slots"][0],
            {"role": "cm-lead", "model": "fable"},
        )
        self.assertEqual(state.document["slots"][1:], original_non_lead)
        self.assertEqual(state.validation_errors(), [])

    def test_missing_lead_repair_keeps_availability_guard(self) -> None:
        document = copy.deepcopy(make_state().document)
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        document["availability"]["models"]["fable"] = "agents"
        state = make_state(document)
        with self.assertRaisesRegex(EditorError, "Availability"):
            state.set_lead("fable")
        self.assertIsNone(state.lead_slot())

    def test_native_policy_transitions(self) -> None:
        state = make_state()
        state.set_native("explore", "native")
        state.set_native("plan", "off")
        state.set_native("general_purpose", "on")
        self.assertEqual(
            state.document["native_agents"],
            {"explore": "native", "plan": "off", "general_purpose": "on"},
        )

    def test_restore_default_is_in_memory_only(self) -> None:
        state = make_state()
        state.set_native("plan", "off")
        state.restore_default()
        self.assertEqual(state.document, state.seed_document)
        self.assertIn("save to persist", state.message)

    def test_save_actions_are_distinct(self) -> None:
        state = make_state()
        actions = {
            state.outcome("update").action,
            state.outcome("save-as", "other").action,
            state.outcome("launch-once").action,
        }
        self.assertEqual(actions, {"update", "save-as", "launch-once"})

    def test_management_actions_keep_source_document(self) -> None:
        state = make_state()
        source = copy.deepcopy(state.document)
        for action in ("duplicate", "rename", "use-as-template"):
            outcome = state.outcome(action, "copy")
            self.assertEqual(outcome.document, source)
        self.assertEqual(state.document, source)

    def test_unsafe_target_rejected(self) -> None:
        state = make_state()
        with self.assertRaises(OSError):
            state.outcome("save-as", "../escape")


class LineFallbackTests(unittest.TestCase):
    def test_cancel_returns_no_outcome(self) -> None:
        state = make_state()
        output = io.StringIO()
        self.assertIsNone(run_line_editor(state, io.StringIO("0\n"), output))
        self.assertIn("Status: Ready", output.getvalue())

    def test_navigation_preserves_native_edit(self) -> None:
        state = make_state()
        # Native agents → Plan → off, then cancel.
        input_stream = io.StringIO("4\n2\n2\n0\n")
        run_line_editor(state, input_stream, io.StringIO())
        self.assertEqual(state.document["native_agents"]["plan"], "off")

    def test_launch_once_outcome(self) -> None:
        state = make_state()
        output = io.StringIO()
        outcome = run_line_editor(state, io.StringIO("5\n3\n"), output)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.action, "launch-once")

    def test_eof_is_cancellation(self) -> None:
        state = make_state()
        self.assertIsNone(run_line_editor(state, io.StringIO(""), io.StringIO()))

    def test_existing_variant_can_be_made_preferred(self) -> None:
        state = make_state()
        # Roles → Analyst → Kimi → Make preferred → cancel.
        outcome = run_line_editor(
            state,
            io.StringIO("3\n1\n2\n1\n0\n"),
            io.StringIO(),
        )
        self.assertIsNone(outcome)
        preferred = [
            item["model"]
            for item in state.variants_for_role("cm-analyst")
            if item["preferred"]
        ]
        self.assertEqual(preferred, ["kimi-k3"])

    def test_delete_requires_explicit_confirmation(self) -> None:
        state = make_state()
        # Save/actions → Delete → reject confirmation → cancel.
        outcome = run_line_editor(
            state,
            io.StringIO("5\n6\nno\n0\n"),
            io.StringIO(),
        )
        self.assertIsNone(outcome)
        self.assertEqual(state.message, "Destructive action cancelled.")

    def test_invalid_availability_row_reports_guidance_and_preserves_state(self) -> None:
        state = make_state()
        before = copy.deepcopy(state.document)
        output = io.StringIO()
        outcome = run_line_editor(state, io.StringIO("2\n99\n0\n"), output)
        self.assertIsNone(outcome)
        self.assertEqual(state.document, before)
        self.assertIn("Choose a listed number.", output.getvalue())

    def test_invalid_availability_scope_reports_guidance_and_preserves_state(self) -> None:
        state = make_state()
        before = copy.deepcopy(state.document)
        output = io.StringIO()
        outcome = run_line_editor(state, io.StringIO("2\n1\nnot-a-number\n0\n"), output)
        self.assertIsNone(outcome)
        self.assertEqual(state.document, before)
        self.assertIn("Choose a listed number.", output.getvalue())

    def test_missing_lead_summary_is_blocked_and_cancel_is_safe(self) -> None:
        document = copy.deepcopy(make_state().document)
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        state = make_state(document)
        output = io.StringIO()
        self.assertIsNone(run_line_editor(state, io.StringIO("0\n"), output))
        self.assertIn("Lead · none selected", output.getvalue())
        self.assertIn("Status: BLOCKED", output.getvalue())
        self.assertIsNone(state.lead_slot())

    def test_missing_lead_can_be_repaired_then_return_launch_plan(self) -> None:
        document = copy.deepcopy(make_state().document)
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        state = make_state(document)
        # Lead → Fable; Save or launch → Launch once.
        outcome = run_line_editor(
            state,
            io.StringIO("1\n1\n5\n3\n"),
            io.StringIO(),
        )
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.action, "launch-once")
        self.assertEqual(outcome.document["slots"][0]["role"], "cm-lead")
        self.assertEqual(state.validation_errors(), [])


if __name__ == "__main__":
    unittest.main()
