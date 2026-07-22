"""Editor-state and form-editor tests.

:class:`~claude_multi.tui.EditorState` is pure document semantics (no
terminal); :class:`~claude_multi.tui.FormEditorScreen` is driven here through
the same :class:`~test_tui.FakeWindow` curses double as the widget tests, so
no test depends on numbered-menu indexes or a real terminal.
"""

from __future__ import annotations

import contextlib
import copy
import unittest
from pathlib import Path

from claude_multi import catalog, strict_json, tui
from claude_multi.tui import EditorError, EditorState

from test_tui import (
    CTRL_G,
    DOWN,
    ENTER,
    ESC,
    RIGHT,
    UP,
    FakeWindow,
)


CATALOG_ROOT = Path(__file__).resolve().parents[1]


def make_state(document=None, docs=None) -> EditorState:
    bundle = catalog.load_catalog(CATALOG_ROOT)
    return EditorState(
        docs or bundle.docs,
        document or bundle.default_composition,
        bundle.default_composition,
    )


def run_form(state, keys, *, height=30, width=90, **kwargs):
    kwargs.setdefault("palette", tui.MONO_PALETTE)
    screen = tui.FormEditorScreen(state, **kwargs)
    win = FakeWindow(keys, height=height, width=width)
    outcome = screen.run(win)
    return outcome, win, screen


def _focusable_rows(screen):
    rows = screen._build_rows()
    return rows, [i for i, row in enumerate(rows) if row.kind != "section"]


def nav_keys(screen, *predicates):
    """Cumulative DOWN keys from the first field through each predicate row."""

    keys: list = []
    current = 0
    for predicate in predicates:
        rows, focusable = _focusable_rows(screen)
        target = next(i for i in focusable if predicate(rows[i]))
        steps = (focusable.index(target) - current) % len(focusable)
        keys.extend([DOWN] * steps)
        current = focusable.index(target)
    return keys


def row_is_actions(row):
    return row.kind == "actions"


def row_is_workflows(row):
    return row.kind == "check" and row.payload == "workflows"


def row_is_general_purpose(row):
    return row.kind == "check" and row.payload == "general_purpose"


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

    def test_workflows_roundtrip_keeps_native_document_minimal(self) -> None:
        state = make_state()
        self.assertEqual(state.workflows, "native")
        self.assertNotIn("workflows", state.document)
        state.set_workflows("off")
        self.assertEqual(state.document["workflows"], "off")
        self.assertTrue(state.dirty)
        state.set_workflows("native")
        self.assertNotIn("workflows", state.document)

    def test_workflows_rejects_unknown_mode(self) -> None:
        state = make_state()
        with self.assertRaisesRegex(EditorError, "invalid workflows mode"):
            state.set_workflows("managed")


class FormEditorRenderTests(unittest.TestCase):
    def test_card_renders_sections_status_and_keybar(self) -> None:
        state = make_state()
        _, win, _ = run_form(state, [ESC])
        text = win.text()
        self.assertIn("claude-multi / Edit default", text)
        for section in ("General", "Lead", "Availability", "Roles", "Native agents", "Actions"):
            self.assertIn(section, text)
        self.assertIn("Status: Ready", text)
        self.assertIn("JSON in $EDITOR", text)
        self.assertIn("[x] native workflows (ultracode)", text)

    def test_modified_marker_and_blocked_status(self) -> None:
        document = copy.deepcopy(make_state().document)
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        state = make_state(document)
        # Focus moves to Description; typing marks the form modified.
        _, win, _ = run_form(state, [DOWN, "x", ESC, ENTER])
        text = win.text()
        self.assertIn("Status: BLOCKED", text)
        self.assertIn("· modified", text)

    def test_cancel_clean_document_returns_none(self) -> None:
        state = make_state()
        before = copy.deepcopy(state.document)
        outcome, _win, _ = run_form(state, [ESC])
        self.assertIsNone(outcome)
        self.assertEqual(state.document, before)

    def test_too_small_guard(self) -> None:
        state = make_state()
        outcome, win, _ = run_form(state, [ESC], height=12, width=40)
        self.assertIsNone(outcome)
        self.assertIn("Terminal too small", win.text())


class FormEditorFieldTests(unittest.TestCase):
    def test_name_text_input_edits_document(self) -> None:
        state = make_state()
        # Name is the first focused field; typing appends at the cursor.
        outcome, _win, _ = run_form(state, list("-v2") + [ESC, ENTER])
        self.assertIsNone(outcome)
        self.assertEqual(state.document["name"], "default-v2")

    def test_lead_select_list_changes_model(self) -> None:
        state = make_state()
        keys = nav_keys(state_screen := _screen(state), lambda r: r.kind == "lead")
        outcome, _win, _ = run_form(state, keys + [ENTER, DOWN, ENTER, ESC, ENTER])
        self.assertIsNone(outcome)
        self.assertEqual(state.lead_slot()["model"], "kimi-k3")

    def test_workflows_checkbox_toggles_off_and_panel_shows(self) -> None:
        state = make_state()
        keys = nav_keys(_screen(state), row_is_workflows)
        outcome, win, _ = run_form(state, keys + [" ", "?", ENTER, ESC, ENTER])
        self.assertIsNone(outcome)
        self.assertEqual(state.workflows, "off")
        self.assertTrue(
            any("Native workflows (ultracode): OFF" in frame for frame in win.frames)
        )

    def test_general_purpose_checkbox_toggles_on(self) -> None:
        state = make_state()
        keys = nav_keys(_screen(state), row_is_general_purpose)
        run_form(state, keys + [" ", ESC, ENTER])
        self.assertEqual(state.document["native_agents"]["general_purpose"], "on")

    def test_native_radio_row_selects_option(self) -> None:
        state = make_state()
        keys = nav_keys(
            _screen(state),
            lambda r: r.kind == "radio" and r.payload[0] == "explore",
        )
        run_form(state, keys + [RIGHT, " ", ESC, ENTER])
        self.assertEqual(state.document["native_agents"]["explore"], "native")

    def test_availability_change_with_invalidation_modal(self) -> None:
        state = make_state()
        keys = nav_keys(
            _screen(state),
            lambda r: r.kind == "avail" and r.payload == ("provider", "kimi"),
        )
        # Scope list: lead+agents -> off (3 down), invalidation Modal: Apply.
        outcome, win, _ = run_form(
            state, keys + [ENTER, DOWN, DOWN, DOWN, ENTER, ENTER, ESC, ENTER]
        )
        self.assertIsNone(outcome)
        self.assertEqual(state.provider_scope("kimi"), "off")
        self.assertTrue(state.validation_errors())
        self.assertTrue(any("Invalidated selections:" in frame for frame in win.frames))

    def test_availability_change_cancelled_in_modal(self) -> None:
        state = make_state()
        keys = nav_keys(
            _screen(state),
            lambda r: r.kind == "avail" and r.payload == ("provider", "kimi"),
        )
        run_form(state, keys + [ENTER, DOWN, DOWN, DOWN, ENTER, RIGHT, ENTER, ESC, ENTER])
        self.assertEqual(state.provider_scope("kimi"), "lead+agents")
        self.assertEqual(state.message, "Availability change cancelled.")

    def test_role_variants_multi_select_and_prefer(self) -> None:
        state = make_state()
        variants = state.compatible_variants("cm-analyst")
        fable_index = variants.index(("fable", "max"))
        kimi_index = variants.index(("kimi-k3", "max"))
        keys = nav_keys(
            _screen(state), lambda r: r.kind == "role" and r.payload == "cm-analyst"
        )
        script = keys + [ENTER]
        script += [DOWN] * fable_index + [" "]
        delta = kimi_index - fable_index
        script += ([DOWN] * delta) if delta > 0 else ([UP] * (-delta))
        script += ["p", ENTER, ESC, ENTER]
        outcome, _win, _ = run_form(state, script)
        self.assertIsNone(outcome)
        self.assertTrue(state.has_variant("cm-analyst", "fable", "max"))
        preferred = [
            item["model"]
            for item in state.variants_for_role("cm-analyst")
            if item["preferred"]
        ]
        self.assertEqual(preferred, ["kimi-k3"])

    def test_removing_preferred_variant_reports_error(self) -> None:
        state = make_state()
        variants = state.compatible_variants("cm-analyst")
        sol_index = variants.index(("sol", "high"))
        keys = nav_keys(
            _screen(state), lambda r: r.kind == "role" and r.payload == "cm-analyst"
        )
        _, win, _ = run_form(
            state, keys + [ENTER] + [DOWN] * sol_index + [" ", ENTER, ESC, ENTER]
        )
        self.assertTrue(state.has_variant("cm-analyst", "sol", "high"))
        self.assertTrue(any("preferred" in frame for frame in win.frames))


def _screen(state):
    return tui.FormEditorScreen(state, palette=tui.MONO_PALETTE)


class FormEditorActionTests(unittest.TestCase):
    def _actions(self, state):
        return nav_keys(_screen(state), row_is_actions)

    def test_update_outcome(self) -> None:
        state = make_state()
        outcome, _win, _ = run_form(state, self._actions(state) + [ENTER, ENTER])
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.action, "update")
        self.assertEqual(outcome.document["name"], "default")

    def test_update_blocked_after_rename(self) -> None:
        state = make_state()
        script = list("x") + self._actions(state) + [ENTER, ENTER]
        outcome, win, _ = run_form(state, script + [ESC, ENTER])
        self.assertIsNone(outcome)
        self.assertIn(tui.FORM_UPDATE_RENAMED, state.message)

    def test_save_as_with_target_modal(self) -> None:
        state = make_state()
        script = self._actions(state) + [ENTER, DOWN, ENTER]
        script += list("copy") + [ENTER, ENTER]
        outcome, _win, _ = run_form(state, script)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.action, "save-as")
        self.assertEqual(outcome.target, "copy")

    def test_save_as_cancelled_without_target(self) -> None:
        state = make_state()
        script = self._actions(state) + [ENTER, DOWN, ENTER, ESC, ESC]
        outcome, _win, _ = run_form(state, script)
        self.assertIsNone(outcome)

    def test_launch_once_outcome(self) -> None:
        state = make_state()
        outcome, _win, _ = run_form(
            state, self._actions(state) + [ENTER, DOWN, DOWN, ENTER]
        )
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.action, "launch-once")

    def test_delete_requires_modal_confirmation(self) -> None:
        state = make_state()
        script = self._actions(state) + [ENTER] + [DOWN] * 5 + [ENTER, ENTER]
        outcome, win, _ = run_form(state, script)
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome.action, "delete")
        self.assertTrue(any("Delete 'default'?" in frame for frame in win.frames))

    def test_delete_cancelled_in_modal(self) -> None:
        state = make_state()
        script = self._actions(state) + [ENTER] + [DOWN] * 5 + [ENTER, RIGHT, ENTER, ESC]
        outcome, _win, _ = run_form(state, script)
        self.assertIsNone(outcome)
        self.assertEqual(state.message, "Destructive action cancelled.")

    def test_discard_modal_keep_editing_then_discard(self) -> None:
        state = make_state()
        script = list("x") + [ESC, RIGHT, ENTER, ESC, ENTER]
        outcome, win, _ = run_form(state, script)
        self.assertIsNone(outcome)
        self.assertTrue(any(tui.FORM_DISCARD_TITLE in frame for frame in win.frames))


class FormEditorJsonTests(unittest.TestCase):
    def _opener(self, mutate):
        def open(path: str) -> int:
            document = strict_json.load(path)
            mutate(document)
            Path(path).write_bytes(strict_json.canonical_file_bytes(document))
            return 0

        return open

    def _kwargs(self, opener):
        return {
            "open_in_editor": opener,
            "suspender": lambda _win: contextlib.nullcontext(),
        }

    def test_ctrl_g_applies_valid_json(self) -> None:
        state = make_state()

        def mutate(document):
            document["description"] = "edited in $EDITOR"

        _, win, _ = run_form(
            state, [CTRL_G, ESC, ENTER], **self._kwargs(self._opener(mutate))
        )
        self.assertEqual(state.document["description"], "edited in $EDITOR")
        self.assertEqual(state.message, "JSON applied from $EDITOR.")
        self.assertTrue(state.dirty)

    def test_ctrl_g_invalid_json_keeps_document(self) -> None:
        state = make_state()
        before = copy.deepcopy(state.document)

        def opener(path: str) -> int:
            Path(path).write_bytes(b"{not json\n")
            return 0

        run_form(state, [CTRL_G, ESC], **self._kwargs(opener))
        self.assertEqual(state.document, before)
        self.assertIn("JSON not applied", state.message)

    def test_ctrl_g_editor_failure_status(self) -> None:
        state = make_state()

        def opener(path: str) -> int:
            return 3

        run_form(state, [CTRL_G, ESC], **self._kwargs(opener))
        self.assertIn("$EDITOR exited with status 3", state.message)

    def test_ctrl_g_unchanged_json(self) -> None:
        state = make_state()

        def opener(path: str) -> int:
            return 0

        run_form(state, [CTRL_G, ESC], **self._kwargs(opener))
        self.assertEqual(state.message, "JSON unchanged.")

    def test_ctrl_g_semantically_invalid_json_applies_as_blocked(self) -> None:
        state = make_state()

        def mutate(document):
            document["slots"] = [
                slot for slot in document["slots"] if slot["role"] != "cm-lead"
            ]

        run_form(state, [CTRL_G, ESC, ENTER], **self._kwargs(self._opener(mutate)))
        self.assertTrue(state.validation_errors())
        self.assertIn("validation problems", state.message)

    def test_ctrl_g_name_collision_rejected(self) -> None:
        state = make_state()

        def mutate(document):
            document["name"] = "taken"

        _, _win, _ = run_form(
            state,
            [CTRL_G, ESC],
            name_taken=lambda name: name == "taken",
            **self._kwargs(self._opener(mutate)),
        )
        self.assertEqual(state.document["name"], "default")
        self.assertIn("already exists", state.message)


if __name__ == "__main__":
    unittest.main()
