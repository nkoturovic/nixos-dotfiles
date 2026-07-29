"""Tests for role contracts and canonical model-neutral prompts."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from claude_multi import catalog, strict_json


CATALOG_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_ROLES = {"cm-lead", "cm-analyst", "cm-reviewer", "cm-implementer"}

# Model/provider identity must never appear in canonical role prompts.
FORBIDDEN_TOKENS = re.compile(
    r"\b(fable|opus|kimi|sol|gpt|gpt55|moonshot|anthropic|openai)\b", re.IGNORECASE
)

PROMPT_KEYWORDS = {
    "cm-lead": (
        "integration",
        "trivial",
        "exact generated IDs",
        "model override",
        "One writer",
        "independence",
        "Nested delegation",
        "Worktree handoff",
        "must not EnterWorktree",
        "never need EnterWorktree",
        "Uncommitted work lives only",
        "untracked",
        "git apply -",
        "Worktree-isolated dispatch requires",
        "fail at creation",
        "git -C <repo> worktree add",
        "read-mostly variants by absolute path",
        "run the implementation from a repo-rooted",
        "bounded implementation yourself",
        "continue the same agent",
        "accumulated context survives",
        "narrower scope",
        "Never summarize the death and move on",
        "not done either",
        "5 consecutive deaths",
        "counter resets",
        "stop it with TaskStop",
    ),
    "cm-analyst": (
        "read-mostly",
        "report artifact",
        "evidence",
        "may delegate",
        "git -C",
        "Never call EnterWorktree",
    ),
    "cm-reviewer": (
        "severity",
        "verdict",
        "independent",
        "Finisher",
        "report every edit",
        "git -C",
        "Never call EnterWorktree",
    ),
    "cm-implementer": (
        "bounded",
        "worktree",
        "commit",
        "validate",
        "Descendants share these same boundaries",
        "base ref",
        "uncommitted, or mixed",
        "Leave the worktree in place",
        "git -C <path>",
        "EnterWorktree is not",
    ),
}


class RoleContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = catalog.load_catalog(CATALOG_ROOT)

    def test_role_set_exact(self) -> None:
        self.assertEqual(set(self.bundle.roles), EXPECTED_ROLES)

    def test_no_blanket_write_denial(self) -> None:
        for role_id in ("cm-analyst", "cm-reviewer"):
            self.assertEqual(
                self.bundle.roles[role_id]["disallowed_tools"],
                [],
                f"{role_id} must not receive blanket tool denial",
            )

    def test_implementer_isolation_field(self) -> None:
        self.assertEqual(self.bundle.roles["cm-implementer"]["isolation"], "worktree")
        for role_id in ("cm-lead", "cm-analyst", "cm-reviewer"):
            self.assertIsNone(self.bundle.roles[role_id]["isolation"])

    def test_contract_strings_dropped_prompts_canonical(self) -> None:
        # D13: roles.json carries summaries only; the canonical contracts
        # live in the prompt bodies, not in duplicated catalog strings.
        for role_id, role in self.bundle.roles.items():
            with self.subTest(role=role_id):
                self.assertNotIn("mutation_contract", role)
                self.assertNotIn("delegation_contract", role)


class CanonicalPromptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = catalog.load_catalog(CATALOG_ROOT)
        cls.texts = {
            role_id: body.decode("utf-8")
            for role_id, body in cls.bundle.prompt_bodies.items()
        }

    def test_prompt_files_exist_nonempty_newline_terminated(self) -> None:
        for role_id in EXPECTED_ROLES:
            body = self.bundle.prompt_bodies[role_id]
            self.assertTrue(body.strip(), f"{role_id} prompt is empty")
            self.assertTrue(body.endswith(b"\n"), f"{role_id} prompt lacks trailing newline")

    def test_prompts_are_model_neutral(self) -> None:
        for role_id, text in self.texts.items():
            match = FORBIDDEN_TOKENS.search(text)
            self.assertIsNone(
                match, f"{role_id} prompt contains model/provider identity: {match!r}"
            )

    def test_prompt_keywords_preserve_contracts(self) -> None:
        for role_id, keywords in PROMPT_KEYWORDS.items():
            text = self.texts[role_id]
            for keyword in keywords:
                self.assertIn(keyword, text, f"{role_id} prompt missing {keyword!r}")

    def test_prompt_identity_hashes_stable(self) -> None:
        for role_id, body in self.bundle.prompt_bodies.items():
            first = strict_json.sha256_hex(body)
            second = strict_json.sha256_hex(body)
            self.assertEqual(first, second)
            self.assertEqual(
                self.bundle.bundle["prompts"][role_id], "sha256:" + first
            )

    def test_prompt_hashes_distinct_across_roles(self) -> None:
        digests = [
            strict_json.sha256_hex(body) for body in self.bundle.prompt_bodies.values()
        ]
        self.assertEqual(len(digests), len(set(digests)))


if __name__ == "__main__":
    unittest.main()
