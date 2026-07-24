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
                    {"explore": explore, "plan": plan, "general_purpose": gp},
                    ["claude"],
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
                # stable deny order: Explore, Plan, general-purpose, aliases
                expected: list[str] = []
                if not both_non_native and explore != "native":
                    expected.append("Agent(Explore)")
                if not both_non_native and plan != "native":
                    expected.append("Agent(Plan)")
                if gp == "off":
                    expected.append("Agent(general-purpose)")
                expected.append("Agent(claude)")
                self.assertEqual(denies, expected)

    def test_generic_aliases_sorted_and_deduplicated(self) -> None:
        _, denies = compiler.compile_native_policy(
            {"explore": "native", "plan": "native", "general_purpose": "on"},
            ["claude", "beta", "claude"],
        )
        self.assertEqual(denies, ["Agent(beta)", "Agent(claude)"])

    def test_generic_alias_never_duplicates_builtin_deny(self) -> None:
        _, denies = compiler.compile_native_policy(
            {"explore": "replace", "plan": "native", "general_purpose": "off"},
            ["general-purpose", "Explore"],
        )
        self.assertEqual(denies, ["Agent(Explore)", "Agent(general-purpose)"])

    def test_no_aliases_preserves_builtin_only_order(self) -> None:
        env, denies = compiler.compile_native_policy(
            {"explore": "replace", "plan": "native", "general_purpose": "off"}
        )
        self.assertEqual(env, {})
        self.assertEqual(denies, ["Agent(Explore)", "Agent(general-purpose)"])

    def test_default_seed_policy(self) -> None:
        env, denies = compiler.compile_native_policy(
            {"explore": "replace", "plan": "native", "general_purpose": "off"},
            ["claude"],
        )
        self.assertEqual(env, {})
        self.assertEqual(
            denies, ["Agent(Explore)", "Agent(general-purpose)", "Agent(claude)"]
        )

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
                Path("/state"), strict_json.bundle_digest(snap),
                "11111111-1111-4111-8111-111111111111",
            ),
        )
        occurrences = [
            index for index, token in enumerate(result.argv) if token == "--disallowedTools"
        ]
        self.assertEqual(len(occurrences), 1)
        self.assertEqual(
            result.argv[occurrences[0] + 1],
            "Agent(Explore) Agent(general-purpose) Agent(claude)",
        )

    def test_deny_list_consumes_contract_generic_aliases(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        snap = composition.snapshot(resolved)
        contract = {
            **bundle.docs["native-contract"],
            "generic_agent_aliases": {
                **bundle.docs["native-contract"]["generic_agent_aliases"],
                "values": ["claude", "beta"],
            },
        }
        docs = {**bundle.docs, "native-contract": contract}
        result = compiler.compile_launch(
            docs=docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh("11111111-1111-4111-8111-111111111111"),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), strict_json.bundle_digest(snap),
                "11111111-1111-4111-8111-111111111111",
            ),
        )
        index = result.argv.index("--disallowedTools")
        self.assertEqual(
            result.argv[index + 1],
            "Agent(Explore) Agent(general-purpose) Agent(beta) Agent(claude)",
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
                Path("/state"), strict_json.bundle_digest(snap),
                "11111111-1111-4111-8111-111111111111",
            ),
        )
        self.assertNotIn("CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS", result.env_set)

    def test_policy_env_var_unset_inherited_but_compiled_value_wins(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        document["native_agents"] = {
            "explore": "off",
            "plan": "off",
            "general_purpose": "on",
        }
        resolved = composition.resolve(bundle.docs, document)
        snap = composition.snapshot(resolved)
        result = compiler.compile_launch(
            docs=bundle.docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh("11111111-1111-4111-8111-111111111111"),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), strict_json.bundle_digest(snap),
                "11111111-1111-4111-8111-111111111111",
            ),
        )
        # Inherited values are always unset; when policy requires the variable
        # the compiled env_set value is applied after the unset at launch.
        self.assertIn("CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS", result.env_unset)
        self.assertEqual(
            result.env_set["CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS"], "1"
        )


class DurableDenyPlacementTests(unittest.TestCase):
    def _durable_result(self):
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        snap = composition.snapshot(resolved)
        session_id = "11111111-1111-4111-8111-111111111111"
        return compiler.compile_launch(
            docs=bundle.docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh(session_id),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), strict_json.bundle_digest(snap), session_id
            ),
            durable=True,
            scope_dir=Path("/state") / "scopes" / session_id,
            hook_command="/state/bin/claude-multi-hook",
        )

    def test_denies_move_to_compiled_settings_not_argv(self) -> None:
        result = self._durable_result()
        self.assertNotIn("--disallowedTools", result.argv)
        self.assertEqual(
            result.scope_plan.settings["permissions"]["deny"],
            ["Agent(Explore)", "Agent(general-purpose)", "Agent(claude)"],
        )

    def test_plan_native_never_denied_in_durable_settings(self) -> None:
        result = self._durable_result()
        self.assertNotIn(
            "Agent(Plan)", result.scope_plan.settings["permissions"]["deny"]
        )


if __name__ == "__main__":
    unittest.main()
