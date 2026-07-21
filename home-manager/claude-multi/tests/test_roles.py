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
    ),
    "cm-analyst": ("read-mostly", "report artifact", "evidence"),
    "cm-reviewer": ("severity", "verdict", "independent"),
    "cm-implementer": ("bounded", "worktree", "commit", "validate"),
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

    def test_report_artifact_contracts(self) -> None:
        analyst = self.bundle.roles["cm-analyst"]["mutation_contract"]
        reviewer = self.bundle.roles["cm-reviewer"]["mutation_contract"]
        self.assertIn("report artifacts", analyst)
        self.assertIn("review artifacts", reviewer)
        self.assertIn("No implementation", analyst)
        self.assertIn("No implementation", reviewer)

    def test_lead_contracts(self) -> None:
        lead = self.bundle.roles["cm-lead"]
        self.assertIn("trivial", lead["mutation_contract"])
        self.assertIn("exact ID", lead["delegation_contract"])
        self.assertIn("without per-invocation model overrides", lead["delegation_contract"])


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
