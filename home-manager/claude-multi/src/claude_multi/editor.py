"""Stateful terminal editor for claude-multi compositions.

The :class:`EditorState` object owns all mutable editor semantics and has no
terminal or persistence dependencies.  Both the curses interface and the
numbered line fallback operate on that same state.  Persistence and launching
remain CLI concerns so cancelling this module has no side effects.
"""

from __future__ import annotations

import copy
import curses
import os
from dataclasses import dataclass, field
from typing import Any, TextIO

from . import catalog as catalog_mod


SECTIONS = ("Lead", "Availability", "Roles", "Native agents", "Save or launch")
SCOPES = catalog_mod.SCOPES
_SCOPE_USES = {
    "lead+agents": frozenset(("lead", "agents")),
    "lead": frozenset(("lead",)),
    "agents": frozenset(("agents",)),
    "off": frozenset(),
}


class EditorError(ValueError):
    """A requested edit would violate an explicit editor invariant."""


@dataclass(frozen=True)
class Invalidation:
    kind: str
    label: str
    role: str
    model: str


@dataclass(frozen=True)
class AvailabilityChange:
    level: str  # provider | model
    key: str
    old_scope: str
    new_scope: str
    invalidated: tuple[Invalidation, ...]
    resulting_errors: tuple[str, ...]


@dataclass(frozen=True)
class EditorOutcome:
    """Requested side effect; interpreted by the CLI after the editor exits."""

    action: str
    document: dict[str, Any]
    target: str | None = None


def _role_label(role_id: str) -> str:
    return role_id.removeprefix("cm-").replace("-", " ").title()


@dataclass
class EditorState:
    """Pure, revisitable composition editor state."""

    docs: dict[str, Any]
    document: dict[str, Any]
    seed_document: dict[str, Any]
    original_document: dict[str, Any] | None = None
    section_index: int = 0
    row_by_section: dict[str, int] = field(default_factory=dict)
    message: str = ""

    def __post_init__(self) -> None:
        self.document = copy.deepcopy(self.document)
        self.seed_document = copy.deepcopy(self.seed_document)
        if self.original_document is None:
            self.original_document = copy.deepcopy(self.document)
        else:
            self.original_document = copy.deepcopy(self.original_document)

    @property
    def section(self) -> str:
        return SECTIONS[self.section_index]

    @property
    def dirty(self) -> bool:
        return self.document != self.original_document

    @property
    def name(self) -> str:
        return self.document["name"]

    def next_section(self, delta: int = 1) -> None:
        self.section_index = (self.section_index + delta) % len(SECTIONS)

    def select_section(self, index: int) -> None:
        if not 0 <= index < len(SECTIONS):
            raise EditorError(f"section index {index} is out of range")
        self.section_index = index

    def move_row(self, delta: int, count: int) -> int:
        if count <= 0:
            self.row_by_section[self.section] = 0
            return 0
        current = self.row_by_section.get(self.section, 0)
        current = (current + delta) % count
        self.row_by_section[self.section] = current
        return current

    def row(self, count: int) -> int:
        if count <= 0:
            return 0
        current = min(self.row_by_section.get(self.section, 0), count - 1)
        self.row_by_section[self.section] = current
        return current

    @property
    def models(self) -> dict[str, Any]:
        return self.docs["models"]["models"]

    @property
    def providers(self) -> dict[str, Any]:
        return self.docs["providers"]["providers"]

    @property
    def roles(self) -> dict[str, Any]:
        return self.docs["roles"]["roles"]

    def validation_errors(self) -> list[str]:
        return catalog_mod.validate_composition(
            self.document, self.models, self.roles, self.providers
        )

    def lead_slot(self) -> dict[str, Any] | None:
        for slot in self.document["slots"]:
            if slot["role"] == catalog_mod.LEAD_ROLE:
                return slot
        return None

    def lead_summary(self) -> str:
        slot = self.lead_slot()
        if slot is None:
            return "none selected"
        model = self.models.get(slot.get("model"))
        return model["display"] if model is not None else "unknown model"

    def set_lead(self, model_id: str) -> None:
        model = self.models.get(model_id)
        if model is None:
            raise EditorError(f"unknown model {model_id!r}")
        if "lead" not in model["capabilities"] or model["lead"] is None:
            raise EditorError(f"{model['display']} cannot be used as the lead")
        if "lead" not in self.effective_uses(model_id):
            raise EditorError(
                f"{model['display']} is not available for lead use; edit Availability first"
            )
        lead = self.lead_slot()
        if lead is None:
            # Lead is always first; all existing non-lead slot ordering remains
            # byte-for-byte stable relative to itself.
            self.document["slots"].insert(
                0, {"role": catalog_mod.LEAD_ROLE, "model": model_id}
            )
        else:
            lead["model"] = model_id

    def provider_scope(self, provider_id: str) -> str:
        return self.document["availability"]["providers"].get(provider_id, "off")

    def model_scope(self, model_id: str) -> str:
        return self.document["availability"]["models"].get(model_id, "off")

    def model_is_new(self, model_id: str) -> bool:
        return model_id not in self.document["availability"]["models"]

    def effective_uses(self, model_id: str) -> frozenset[str]:
        model = self.models[model_id]
        provider_uses = _SCOPE_USES[self.provider_scope(model["provider"])]
        model_uses = _SCOPE_USES[self.model_scope(model_id)]
        return provider_uses & model_uses & frozenset(model["capabilities"])

    def _preview_document(self, level: str, key: str, scope: str) -> dict[str, Any]:
        if scope not in SCOPES:
            raise EditorError(f"unknown availability scope {scope!r}")
        if level == "provider" and key not in self.providers:
            raise EditorError(f"unknown provider {key!r}")
        if level == "model" and key not in self.models:
            raise EditorError(f"unknown model {key!r}")
        candidate = copy.deepcopy(self.document)
        plural = "providers" if level == "provider" else "models"
        candidate["availability"][plural][key] = scope
        return candidate

    def preview_availability(
        self, level: str, key: str, new_scope: str
    ) -> AvailabilityChange:
        candidate = self._preview_document(level, key, new_scope)
        plural = "providers" if level == "provider" else "models"
        old_scope = self.document["availability"][plural].get(key, "off")
        provider_scopes = candidate["availability"]["providers"]
        model_scopes = candidate["availability"]["models"]
        invalidated: list[Invalidation] = []
        for slot in candidate["slots"]:
            model_id = slot["model"]
            model = self.models.get(model_id)
            if model is None:
                continue
            allowed = (
                _SCOPE_USES[provider_scopes.get(model["provider"], "off")]
                & _SCOPE_USES[model_scopes.get(model_id, "off")]
                & frozenset(model["capabilities"])
            )
            use = "lead" if slot["role"] == catalog_mod.LEAD_ROLE else "agents"
            if use in allowed:
                continue
            lane = slot.get("lane", model["default_lane"])
            if use == "lead":
                label = f"Lead · {model['display']}"
            else:
                label = f"{_role_label(slot['role'])} · {model['display']} · {lane}"
            invalidated.append(
                Invalidation(use, label, slot["role"], model_id)
            )
        errors = catalog_mod.validate_composition(
            candidate, self.models, self.roles, self.providers
        )
        return AvailabilityChange(
            level=level,
            key=key,
            old_scope=old_scope,
            new_scope=new_scope,
            invalidated=tuple(invalidated),
            resulting_errors=tuple(errors),
        )

    def apply_availability(
        self, change: AvailabilityChange, *, confirm: bool = False
    ) -> None:
        if change.invalidated and not confirm:
            labels = ", ".join(item.label for item in change.invalidated)
            raise EditorError(
                f"availability change invalidates {labels}; explicit confirmation required"
            )
        candidate = self._preview_document(change.level, change.key, change.new_scope)
        self.document = candidate

    def variants_for_role(self, role_id: str) -> list[dict[str, Any]]:
        return [
            slot
            for slot in self.document["slots"]
            if slot["role"] == role_id
        ]

    def compatible_variants(self, role_id: str) -> list[tuple[str, str]]:
        variants: list[tuple[str, str]] = []
        for model_id, model in sorted(self.models.items()):
            if role_id not in model["compatible_roles"] or role_id == catalog_mod.LEAD_ROLE:
                continue
            for lane in sorted(model["lanes"]):
                variants.append((model_id, lane))
        return variants

    def has_variant(self, role_id: str, model_id: str, lane: str) -> bool:
        return any(
            slot["role"] == role_id
            and slot["model"] == model_id
            and slot.get("lane", self.models[model_id]["default_lane"]) == lane
            for slot in self.document["slots"]
        )

    def toggle_variant(
        self,
        role_id: str,
        model_id: str,
        lane: str,
        *,
        replacement_preferred: tuple[str, str] | None = None,
    ) -> None:
        if role_id == catalog_mod.LEAD_ROLE or role_id not in self.roles:
            raise EditorError(f"invalid variant role {role_id!r}")
        model = self.models.get(model_id)
        if model is None or role_id not in model["compatible_roles"]:
            raise EditorError(f"model {model_id!r} is not compatible with {role_id!r}")
        if lane not in model["lanes"]:
            raise EditorError(f"model {model_id!r} has no lane {lane!r}")

        slots = self.document["slots"]
        found_index: int | None = None
        for index, slot in enumerate(slots):
            if (
                slot["role"] == role_id
                and slot["model"] == model_id
                and slot.get("lane", model["default_lane"]) == lane
            ):
                found_index = index
                break
        if found_index is None:
            if "agents" not in self.effective_uses(model_id):
                raise EditorError(
                    f"{model['display']} is not available for agent use; edit Availability first"
                )
            existing = self.variants_for_role(role_id)
            slots.append(
                {
                    "role": role_id,
                    "model": model_id,
                    "lane": lane,
                    "preferred": not existing,
                }
            )
            return

        removed = slots[found_index]
        remaining = [
            slot for index, slot in enumerate(slots)
            if index != found_index and slot["role"] == role_id
        ]
        if removed.get("preferred") and remaining:
            if replacement_preferred is None:
                raise EditorError(
                    "the preferred variant cannot be removed until another variant is preferred"
                )
            if not any(
                slot["model"] == replacement_preferred[0]
                and slot.get("lane", self.models[slot["model"]]["default_lane"])
                == replacement_preferred[1]
                for slot in remaining
            ):
                raise EditorError("replacement preferred variant is not selected")
            for slot in remaining:
                slot["preferred"] = (
                    slot["model"],
                    slot.get("lane", self.models[slot["model"]]["default_lane"]),
                ) == replacement_preferred
        del slots[found_index]

    def set_preferred(self, role_id: str, model_id: str, lane: str) -> None:
        variants = self.variants_for_role(role_id)
        target_found = False
        for slot in variants:
            slot_lane = slot.get("lane", self.models[slot["model"]]["default_lane"])
            selected = slot["model"] == model_id and slot_lane == lane
            slot["preferred"] = selected
            target_found |= selected
        if not target_found:
            raise EditorError("preferred variant must already be selected")

    def set_native(self, key: str, value: str) -> None:
        allowed = {
            "explore": ("replace", "native", "off"),
            "plan": ("native", "off"),
            "general_purpose": ("on", "off"),
        }
        if key not in allowed or value not in allowed[key]:
            raise EditorError(f"invalid native-agent setting {key}={value!r}")
        self.document["native_agents"][key] = value

    def restore_default(self) -> None:
        self.document = copy.deepcopy(self.seed_document)
        self.message = "Trusted default restored in the editor; save to persist it."

    def outcome(self, action: str, target: str | None = None) -> EditorOutcome:
        if action not in {
            "save", "update", "save-as", "launch-once", "duplicate", "rename",
            "delete", "restore-default", "use-as-template",
        }:
            raise EditorError(f"unknown editor action {action!r}")
        if target is not None:
            from .state import check_name

            check_name(target)
        return EditorOutcome(action, copy.deepcopy(self.document), target)


def _write_line(stream: TextIO, text: str = "") -> None:
    stream.write(text + "\n")
    stream.flush()


def _read_line(input_stream: TextIO, output_stream: TextIO, prompt: str) -> str | None:
    output_stream.write(prompt)
    output_stream.flush()
    try:
        value = input_stream.readline()
    except KeyboardInterrupt:
        return None
    if value == "":
        return None
    return value.rstrip("\r\n")


def _numbered_choice(
    input_stream: TextIO,
    output_stream: TextIO,
    title: str,
    choices: list[str],
    *,
    allow_back: bool = True,
) -> int | None:
    _write_line(output_stream, "")
    _write_line(output_stream, title)
    for index, choice in enumerate(choices, 1):
        _write_line(output_stream, f"  {index}. {choice}")
    if allow_back:
        _write_line(output_stream, "  0. Back")
    value = _read_line(input_stream, output_stream, "> ")
    if value is None or (allow_back and value == "0"):
        return None
    try:
        index = int(value) - 1
    except ValueError:
        return -1
    return index if 0 <= index < len(choices) else -1


def _line_lead(state: EditorState, inp: TextIO, out: TextIO) -> None:
    model_ids = [
        model_id for model_id, model in sorted(state.models.items())
        if "lead" in model["capabilities"] and model["lead"] is not None
    ]
    choices = []
    for model_id in model_ids:
        status = "available" if "lead" in state.effective_uses(model_id) else "unavailable"
        choices.append(f"{state.models[model_id]['display']} · {status}")
    index = _numbered_choice(inp, out, "Lead", choices)
    if index is None:
        return
    if index < 0:
        state.message = "Choose a listed number."
        return
    try:
        state.set_lead(model_ids[index])
        state.message = "Lead updated."
    except EditorError as exc:
        state.message = str(exc)


def _line_availability(state: EditorState, inp: TextIO, out: TextIO) -> None:
    rows: list[tuple[str, str, str]] = []
    for provider_id, provider in sorted(state.providers.items()):
        rows.append(("provider", provider_id, provider["display"]))
        for model_id, model in sorted(state.models.items()):
            if model["provider"] == provider_id:
                badge = " · New · Off" if state.model_is_new(model_id) else ""
                rows.append(("model", model_id, f"  {model['display']}{badge}"))
    choices = []
    for level, key, label in rows:
        scope = state.provider_scope(key) if level == "provider" else state.model_scope(key)
        choices.append(f"{label} · {scope}")
    index = _numbered_choice(inp, out, "Availability", choices)
    if index is None:
        return
    if index < 0:
        state.message = "Choose a listed number."
        return
    level, key, label = rows[index]
    scope_index = _numbered_choice(inp, out, f"Scope for {label.strip()}", list(SCOPES))
    if scope_index is None:
        return
    if scope_index < 0:
        state.message = "Choose a listed number."
        return
    change = state.preview_availability(level, key, SCOPES[scope_index])
    if change.invalidated:
        _write_line(out, "This change invalidates:")
        for item in change.invalidated:
            _write_line(out, f"  - {item.label}")
        answer = _read_line(inp, out, "Apply and keep these selections? [y/N] ")
        if answer is None or answer.lower() != "y":
            state.message = "Availability change cancelled."
            return
    state.apply_availability(change, confirm=bool(change.invalidated))
    state.message = "Availability updated; selections were preserved."


def _line_roles(state: EditorState, inp: TextIO, out: TextIO) -> None:
    role_ids = [role for role in sorted(state.roles) if role != catalog_mod.LEAD_ROLE]
    labels = []
    for role in role_ids:
        selected = state.variants_for_role(role)
        preferred = next((slot for slot in selected if slot.get("preferred")), None)
        preferred_text = "none"
        if preferred:
            preferred_text = state.models[preferred["model"]]["display"]
        labels.append(f"{_role_label(role)} · {len(selected)} variants · preferred {preferred_text}")
    role_index = _numbered_choice(inp, out, "Roles", labels)
    if role_index is None or role_index < 0:
        return
    role_id = role_ids[role_index]
    variants = state.compatible_variants(role_id)
    variant_labels = []
    for model_id, lane in variants:
        selected = state.has_variant(role_id, model_id, lane)
        preferred = any(
            slot["model"] == model_id
            and slot.get("lane", state.models[model_id]["default_lane"]) == lane
            and slot.get("preferred")
            for slot in state.variants_for_role(role_id)
        )
        marker = "[x]" if selected else "[ ]"
        pref = " · preferred" if preferred else ""
        available = "agents" in state.effective_uses(model_id)
        unavailable = " · unavailable" if not available else ""
        variant_labels.append(
            f"{marker} {state.models[model_id]['display']} · {lane}{pref}{unavailable}"
        )
    variant_index = _numbered_choice(inp, out, _role_label(role_id), variant_labels)
    if variant_index is None or variant_index < 0:
        return
    model_id, lane = variants[variant_index]
    selected = state.has_variant(role_id, model_id, lane)
    if selected:
        selected_slot = next(
            slot for slot in state.variants_for_role(role_id)
            if slot["model"] == model_id
            and slot.get("lane", state.models[model_id]["default_lane"]) == lane
        )
        action = _numbered_choice(
            inp,
            out,
            f"{state.models[model_id]['display']} · {lane}",
            ["Make preferred", "Remove variant"],
        )
        if action is None or action < 0:
            return
        if action == 0:
            state.set_preferred(role_id, model_id, lane)
            state.message = "Preferred variant updated."
            return
        if selected_slot.get("preferred") and len(state.variants_for_role(role_id)) > 1:
            state.message = "Choose another preferred variant before removing this one."
            return
        state.toggle_variant(role_id, model_id, lane)
        state.message = "Variant removed."
    else:
        if "agents" not in state.effective_uses(model_id):
            state.message = "Variant is unavailable; edit Availability first."
            return
        state.toggle_variant(role_id, model_id, lane)
        make_preferred = _read_line(inp, out, "Make this variant preferred? [y/N] ")
        if make_preferred and make_preferred.lower() == "y":
            state.set_preferred(role_id, model_id, lane)
        state.message = "Variant added."


def _line_native(state: EditorState, inp: TextIO, out: TextIO) -> None:
    keys = ("explore", "plan", "general_purpose")
    labels = [f"{key.replace('_', ' ')} · {state.document['native_agents'][key]}" for key in keys]
    index = _numbered_choice(inp, out, "Native agents", labels)
    if index is None or index < 0:
        return
    key = keys[index]
    allowed = {
        "explore": ["replace", "native", "off"],
        "plan": ["native", "off"],
        "general_purpose": ["on", "off"],
    }[key]
    value_index = _numbered_choice(inp, out, key.replace("_", " ").title(), allowed)
    if value_index is None or value_index < 0:
        return
    state.set_native(key, allowed[value_index])
    state.message = "Native-agent policy updated."


def _line_actions(state: EditorState, inp: TextIO, out: TextIO) -> EditorOutcome | None:
    actions = [
        ("update", "Update current composition"),
        ("save-as", "Save as new composition"),
        ("launch-once", "Launch once without saving"),
        ("duplicate", "Duplicate"),
        ("rename", "Rename"),
        ("delete", "Delete"),
        ("restore-default", "Restore trusted default"),
        ("use-as-template", "Use as template"),
    ]
    index = _numbered_choice(inp, out, "Save or launch", [label for _, label in actions])
    if index is None or index < 0:
        return None
    action = actions[index][0]
    target = None
    if action in {"save-as", "duplicate", "rename", "use-as-template"}:
        target = _read_line(inp, out, "Target name: ")
        if not target:
            state.message = "A target name is required."
            return None
    if action in {"delete", "restore-default"}:
        answer = _read_line(
            inp,
            out,
            "Type YES to delete this composition: "
            if action == "delete"
            else "Type YES to replace it with the trusted default: ",
        )
        if answer != "YES":
            state.message = "Destructive action cancelled."
            return None
    return state.outcome(action, target)


def run_line_editor(
    state: EditorState, input_stream: TextIO, output_stream: TextIO
) -> EditorOutcome | None:
    """Numbered fallback exposing the same editor state and side-effect intents."""

    while True:
        _write_line(output_stream, "")
        marker = " · modified" if state.dirty else ""
        _write_line(output_stream, f"claude-multi / Edit {state.name}{marker}")
        _write_line(output_stream, f"  1. Lead · {state.lead_summary()}")
        _write_line(output_stream, "  2. Availability")
        _write_line(output_stream, "  3. Roles and variants")
        _write_line(output_stream, "  4. Native agents")
        _write_line(output_stream, "  5. Save or launch")
        _write_line(output_stream, "  0. Cancel")
        errors = state.validation_errors()
        _write_line(output_stream, f"Status: {'BLOCKED' if errors else 'Ready'}")
        if state.message:
            _write_line(output_stream, state.message)
        value = _read_line(input_stream, output_stream, "> ")
        if value is None or value == "0":
            return None
        if value == "1":
            _line_lead(state, input_stream, output_stream)
        elif value == "2":
            _line_availability(state, input_stream, output_stream)
        elif value == "3":
            _line_roles(state, input_stream, output_stream)
        elif value == "4":
            _line_native(state, input_stream, output_stream)
        elif value == "5":
            outcome = _line_actions(state, input_stream, output_stream)
            if outcome is not None:
                return outcome
        else:
            state.message = "Choose 0–5."


def _safe_add(window: Any, row: int, col: int, text: str, width: int, attr: int = 0) -> None:
    if row < 0 or col < 0 or width <= col:
        return
    clipped = text[: max(0, width - col - 1)]
    try:
        window.addstr(row, col, clipped, attr)
    except curses.error:
        pass


def _read_text_curses(stdscr: Any, prompt: str, initial: str = "") -> str | None:
    height, width = stdscr.getmaxyx()
    curses.echo()
    try:
        stdscr.move(height - 2, 0)
        stdscr.clrtoeol()
        _safe_add(stdscr, height - 2, 0, prompt, width)
        stdscr.addstr(height - 2, min(len(prompt), width - 2), initial)
        raw = stdscr.getstr(height - 2, min(len(prompt) + len(initial), width - 2), max(1, width - len(prompt) - 2))
    except (curses.error, KeyboardInterrupt):
        return None
    finally:
        curses.noecho()
    return initial + raw.decode("utf-8", "replace")


def _confirm_curses(stdscr: Any, title: str, lines: list[str]) -> bool:
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        _safe_add(stdscr, 0, 0, title, width, curses.A_BOLD)
        for index, line in enumerate(lines, 2):
            _safe_add(stdscr, index, 2, line, width)
        _safe_add(stdscr, height - 2, 0, "Y apply · N cancel", width)
        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("y"), ord("Y")):
            return True
        if key in (3, ord("n"), ord("N"), 27, ord("q")):
            return False


def _curses_list(
    stdscr: Any,
    title: str,
    rows: list[str],
    *,
    footer: str,
    selected: int = 0,
) -> tuple[int | None, int]:
    selected = min(max(selected, 0), max(0, len(rows) - 1))
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 9 or width < 38:
            _safe_add(stdscr, 0, 0, "Terminal too small.", width, curses.A_BOLD)
            _safe_add(stdscr, 2, 0, "Resize, or press Esc to return.", width)
            stdscr.refresh()
            key = stdscr.getch()
            if key in (3, 27, ord("q")):
                return None, selected
            continue
        _safe_add(stdscr, 0, 0, title, width, curses.A_BOLD)
        visible = max(1, height - 4)
        start = max(0, min(selected - visible + 1, max(0, len(rows) - visible)))
        for screen_row, item_index in enumerate(range(start, min(len(rows), start + visible)), 2):
            marker = ">" if item_index == selected else " "
            attr = curses.A_REVERSE if item_index == selected else 0
            _safe_add(stdscr, screen_row, 0, f"{marker} {rows[item_index]}", width, attr)
        _safe_add(stdscr, height - 2, 0, footer, width)
        stdscr.refresh()
        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            continue
        if key in (curses.KEY_UP, ord("k"), curses.KEY_BTAB):
            selected = (selected - 1) % max(1, len(rows))
        elif key in (curses.KEY_DOWN, ord("j"), 9):
            selected = (selected + 1) % max(1, len(rows))
        elif key in (10, 13, curses.KEY_ENTER):
            return selected, selected
        elif key in (3, 27, ord("q")):
            return None, selected
        elif key == ord(" "):
            return selected, selected
        elif key in (ord("p"), ord("P")):
            return selected, -selected - 1  # signal preferred action


def _curses_lead(stdscr: Any, state: EditorState) -> None:
    ids = [
        model_id for model_id, model in sorted(state.models.items())
        if "lead" in model["capabilities"] and model["lead"] is not None
    ]
    rows = [
        f"{state.models[mid]['display']} · "
        f"{'available' if 'lead' in state.effective_uses(mid) else 'unavailable'}"
        for mid in ids
    ]
    index, _ = _curses_list(stdscr, "Lead", rows, footer="Enter choose · Esc back")
    if index is not None:
        try:
            state.set_lead(ids[index])
            state.message = "Lead updated."
        except EditorError as exc:
            state.message = str(exc)


def _curses_availability(stdscr: Any, state: EditorState) -> None:
    entries: list[tuple[str, str, str]] = []
    for provider_id, provider in sorted(state.providers.items()):
        entries.append(("provider", provider_id, provider["display"]))
        for model_id, model in sorted(state.models.items()):
            if model["provider"] == provider_id:
                entries.append(("model", model_id, "  " + model["display"]))
    selected = 0
    while True:
        rows = []
        for level, key, label in entries:
            scope = state.provider_scope(key) if level == "provider" else state.model_scope(key)
            new = " · New · Off" if level == "model" and state.model_is_new(key) else ""
            rows.append(f"{label:<34} {scope}{new}")
        index, selected = _curses_list(
            stdscr, "Availability", rows,
            footer="Enter cycle scope · Esc back · selections are never silently removed",
            selected=selected,
        )
        if index is None:
            return
        level, key, label = entries[index]
        current = state.provider_scope(key) if level == "provider" else state.model_scope(key)
        new_scope = SCOPES[(SCOPES.index(current) + 1) % len(SCOPES)]
        change = state.preview_availability(level, key, new_scope)
        if change.invalidated:
            lines = [f"{label.strip()}: {current} → {new_scope}", "", "Invalidated selections:"]
            lines += [f"- {item.label}" for item in change.invalidated]
            lines += ["", "Selections remain present and the composition becomes BLOCKED."]
            if not _confirm_curses(stdscr, "Confirm availability change", lines):
                state.message = "Availability change cancelled."
                continue
        state.apply_availability(change, confirm=bool(change.invalidated))
        state.message = "Availability updated; selections were preserved."


def _curses_role_variants(stdscr: Any, state: EditorState, role_id: str) -> None:
    variants = state.compatible_variants(role_id)
    selected = 0
    while True:
        rows = []
        for model_id, lane in variants:
            active = state.has_variant(role_id, model_id, lane)
            slot = next(
                (
                    item for item in state.variants_for_role(role_id)
                    if item["model"] == model_id
                    and item.get("lane", state.models[model_id]["default_lane"]) == lane
                ),
                None,
            )
            preferred = bool(slot and slot.get("preferred"))
            available = "agents" in state.effective_uses(model_id)
            hint = state.models[model_id]["role_hints"].get(role_id, state.models[model_id]["routing_note"])
            rows.append(
                f"{'[x]' if active else '[ ]'} {state.models[model_id]['display']} · {lane}"
                f"{' · preferred' if preferred else ''}{' · unavailable' if not available else ''}"
                f" — {hint}"
            )
        index, signal = _curses_list(
            stdscr, _role_label(role_id), rows,
            footer="Space toggle · P prefer · Esc back",
            selected=selected,
        )
        if index is None:
            return
        selected = index
        model_id, lane = variants[index]
        if signal < 0:
            try:
                state.set_preferred(role_id, model_id, lane)
                state.message = "Preferred variant updated."
            except EditorError as exc:
                state.message = str(exc)
            continue
        if not state.has_variant(role_id, model_id, lane) and "agents" not in state.effective_uses(model_id):
            state.message = "Variant is unavailable; edit Availability first."
            continue
        try:
            state.toggle_variant(role_id, model_id, lane)
            state.message = "Variant selection updated."
        except EditorError as exc:
            state.message = str(exc)


def _curses_roles(stdscr: Any, state: EditorState) -> None:
    ids = [role for role in sorted(state.roles) if role != catalog_mod.LEAD_ROLE]
    selected = 0
    while True:
        rows = []
        for role in ids:
            variants = state.variants_for_role(role)
            preferred = next((slot for slot in variants if slot.get("preferred")), None)
            preferred_name = state.models[preferred["model"]]["display"] if preferred else "none"
            rows.append(f"{_role_label(role):<18} {len(variants)} variants · {preferred_name} preferred")
        index, selected = _curses_list(
            stdscr, "Roles and variants", rows,
            footer="Enter variants · Esc back", selected=selected,
        )
        if index is None:
            return
        _curses_role_variants(stdscr, state, ids[index])


def _curses_native(stdscr: Any, state: EditorState) -> None:
    keys = ("explore", "plan", "general_purpose")
    allowed = {
        "explore": ("replace", "native", "off"),
        "plan": ("native", "off"),
        "general_purpose": ("on", "off"),
    }
    selected = 0
    while True:
        rows = [
            f"{key.replace('_', ' ').title():<22} {state.document['native_agents'][key]}"
            for key in keys
        ]
        index, selected = _curses_list(
            stdscr, "Native agents", rows,
            footer="Enter cycle · Esc back", selected=selected,
        )
        if index is None:
            return
        key = keys[index]
        values = allowed[key]
        current = state.document["native_agents"][key]
        state.set_native(key, values[(values.index(current) + 1) % len(values)])
        state.message = "Native-agent policy updated."


def _curses_actions(stdscr: Any, state: EditorState) -> EditorOutcome | None:
    actions = [
        ("update", "Update current composition"),
        ("save-as", "Save as new composition"),
        ("launch-once", "Launch once without saving"),
        ("duplicate", "Duplicate"),
        ("rename", "Rename"),
        ("delete", "Delete"),
        ("restore-default", "Restore trusted default"),
        ("use-as-template", "Use as template"),
    ]
    index, _ = _curses_list(
        stdscr, "Save or launch", [label for _, label in actions],
        footer="Enter choose · Esc back",
    )
    if index is None:
        return None
    action = actions[index][0]
    target = None
    if action in {"save-as", "duplicate", "rename", "use-as-template"}:
        target = _read_text_curses(stdscr, "Target name: ")
        if not target:
            state.message = "A target name is required."
            return None
    if action in {"delete", "restore-default"}:
        label = (
            f"Delete {state.name!r}?"
            if action == "delete"
            else "Replace the editor with the trusted default?"
        )
        if not _confirm_curses(
            stdscr,
            label,
            ["This action is explicit and will not launch Claude."],
        ):
            state.message = "Destructive action cancelled."
            return None
    try:
        return state.outcome(action, target)
    except (EditorError, OSError) as exc:
        state.message = str(exc)
        return None


def _curses_main(stdscr: Any, state: EditorState) -> EditorOutcome | None | str:
    curses.curs_set(0)
    stdscr.keypad(True)
    while True:
        stdscr.erase()
        height, width = stdscr.getmaxyx()
        if height < 12 or width < 48:
            _safe_add(stdscr, 0, 0, "Terminal too small for the full editor.", width, curses.A_BOLD)
            _safe_add(stdscr, 2, 0, "Resize to at least 48×12.", width)
            _safe_add(stdscr, 3, 0, "L line mode · Q cancel", width)
            stdscr.refresh()
            key = stdscr.getch()
            if key in (ord("l"), ord("L")):
                return "line"
            if key in (3, ord("q"), ord("Q"), 27):
                return None
            continue
        errors = state.validation_errors()
        marker = " · modified" if state.dirty else ""
        _safe_add(stdscr, 0, 0, f"claude-multi / Edit {state.name}{marker}", width, curses.A_BOLD)
        role_count = len({slot["role"] for slot in state.document["slots"] if slot["role"] != catalog_mod.LEAD_ROLE})
        variant_count = sum(
            slot["role"] != catalog_mod.LEAD_ROLE
            for slot in state.document["slots"]
        )
        summaries = [
            state.lead_summary(),
            " · ".join(f"{state.providers[p]['display']} {state.provider_scope(p)}" for p in sorted(state.providers)),
            f"{role_count} roles · {variant_count} variants",
            " · ".join(f"{k.replace('_', ' ')} {v}" for k, v in state.document["native_agents"].items()),
            "Update, save as, launch once, or manage this composition",
        ]
        for index, section in enumerate(SECTIONS):
            row = 2 + index * 2
            selected = index == state.section_index
            attr = curses.A_REVERSE if selected else 0
            _safe_add(stdscr, row, 0, f"{'>' if selected else ' '} {section}", width, attr)
            _safe_add(stdscr, row + 1, 4, summaries[index], width)
        status_row = min(height - 5, 2 + len(SECTIONS) * 2)
        _safe_add(stdscr, status_row, 0, f"Status: {'BLOCKED' if errors else 'Ready'}", width, curses.A_BOLD)
        if errors:
            _safe_add(stdscr, status_row + 1, 2, errors[0], width)
        elif state.message:
            _safe_add(stdscr, status_row + 1, 2, state.message, width)
        _safe_add(stdscr, height - 2, 0, "↑↓/Tab move · Enter open · Esc cancel", width)
        stdscr.refresh()
        key = stdscr.getch()
        if key == curses.KEY_RESIZE:
            continue
        if key in (curses.KEY_UP, ord("k"), curses.KEY_BTAB):
            state.next_section(-1)
        elif key in (curses.KEY_DOWN, ord("j"), 9):
            state.next_section(1)
        elif key in (3, 27, ord("q"), ord("Q")):
            return None
        elif key in (10, 13, curses.KEY_ENTER):
            if state.section_index == 0:
                _curses_lead(stdscr, state)
            elif state.section_index == 1:
                _curses_availability(stdscr, state)
            elif state.section_index == 2:
                _curses_roles(stdscr, state)
            elif state.section_index == 3:
                _curses_native(stdscr, state)
            else:
                outcome = _curses_actions(stdscr, state)
                if outcome is not None:
                    return outcome


def run_curses_editor(state: EditorState) -> EditorOutcome | None | str:
    """Run the full-screen editor with terminal restoration via curses.wrapper."""

    return curses.wrapper(_curses_main, state)


def run_editor(
    state: EditorState,
    input_stream: TextIO,
    output_stream: TextIO,
    *,
    force_line: bool = False,
) -> EditorOutcome | None:
    """Choose curses or numbered fallback without changing editor semantics."""

    if force_line or os.environ.get("TERM") in (None, "", "dumb"):
        return run_line_editor(state, input_stream, output_stream)
    try:
        outcome = run_curses_editor(state)
    except KeyboardInterrupt:
        return None
    except (curses.error, OSError):
        return run_line_editor(state, input_stream, output_stream)
    if outcome == "line":
        return run_line_editor(state, input_stream, output_stream)
    return outcome
