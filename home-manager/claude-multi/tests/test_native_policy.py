"""Tests for native built-in agent policy compilation."""

from __future__ import annotations

import itertools
import unittest
from pathlib import Path

from claude_multi import catalog, compiler, composition, strict_json


CATALOG_ROOT = Path(__file__).resolve().parents[1]


class PolicyMatrixTests(unittest.TestCase):
    def test_full_matrix(self) -> None:
        explores = ("replace", "native", "off")
        plans = ("native", "off")
        general = ("on", "off")
        for explore, plan, gp in itertools.product(explores, plans, general):
            with self.subTest(explore=explore, plan=plan, general_purpose=gp):
                env, denies = compiler.compile_native_policy(
                    {"explore": explore, "plan": plan, "general_purpose": gp}
                )
                both_non_native = explore != "native" and plan != "native"
                if both_non_native:
                    self.assertEqual(
                        env, {"CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS": "1"}
                    )
                    self.assertNotIn("Agent(Explore)", denies)
                    self.assertNotIn("Agent(Plan)", denies)
                else:
                    self.assertEqual(env, {})
                    if explore != "native":
                        self.assertIn("Agent(Explore)", denies)
                    else:
                        self.assertNotIn("Agent(Explore)", denies)
                    if plan != "native":
                        self.assertIn("Agent(Plan)", denies)
                    else:
                        self.assertNotIn("Agent(Plan)", denies)
                if gp == "off":
                    self.assertIn("Agent(general-purpose)", denies)
                else:
                    self.assertNotIn("Agent(general-purpose)", denies)
                # stable deny order: Explore, Plan, general-purpose
                expected: list[str] = []
                if not both_non_native and explore != "native":
                    expected.append("Agent(Explore)")
                if not both_non_native and plan != "native":
                    expected.append("Agent(Plan)")
                if gp == "off":
                    expected.append("Agent(general-purpose)")
                self.assertEqual(denies, expected)

    def test_default_seed_policy(self) -> None:
        env, denies = compiler.compile_native_policy(
            {"explore": "replace", "plan": "native", "general_purpose": "off"}
        )
        self.assertEqual(env, {})
        self.assertEqual(denies, ["Agent(Explore)", "Agent(general-purpose)"])

    def test_both_disabled_uses_env_var(self) -> None:
        env, denies = compiler.compile_native_policy(
            {"explore": "off", "plan": "off", "general_purpose": "on"}
        )
        self.assertEqual(env, {"CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS": "1"})
        self.assertEqual(denies, [])

    def test_no_same_name_shadowing(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        generated_ids = {variant.id for variant in resolved.variants} | {"cm-lead"}
        for builtin in ("Explore", "Plan", "general-purpose"):
            self.assertNotIn(builtin, generated_ids)


class DenyConsolidationTests(unittest.TestCase):
    def test_single_disallowed_tools_occurrence(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        snap = composition.snapshot(resolved)
        result = compiler.compile_launch(
            docs=bundle.docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh("11111111-1111-4111-8111-111111111111"),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), strict_json.bundle_digest(snap)
            ),
        )
        occurrences = [
            index for index, token in enumerate(result.argv) if token == "--disallowedTools"
        ]
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(
            result.argv[occurrences[0] + 1],
            "Agent(Explore) Agent(general-purpose)",
        )

    def test_policy_env_merged_into_env_set(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = dict(bundle.default_composition)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        snap = composition.snapshot(resolved)
        result = compiler.compile_launch(
            docs=bundle.docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh("11111111-1111-4111-8111-111111111111"),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), strict_json.bundle_digest(snap)
            ),
        )
        self.assertNotIn("CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS", result.env_set)


if __name__ == "__main__":
    unittest.main()
