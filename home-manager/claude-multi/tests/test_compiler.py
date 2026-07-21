"""Tests for the deterministic compiler: agents, lead content, env, argv, goldens."""

from __future__ import annotations

import unittest
from pathlib import Path

from claude_multi import catalog, compiler, composition, strict_json


CATALOG_ROOT = Path(__file__).resolve().parents[1]
GOLDENS = CATALOG_ROOT / "tests" / "goldens" / "default"
FIXED_SESSION = "11111111-1111-4111-8111-111111111111"
SETTINGS_PATH = Path("/trusted/settings.json")


def _compile(passthrough=None, action=None):
    bundle = catalog.load_catalog(CATALOG_ROOT)
    resolved = composition.resolve(bundle.docs, bundle.default_composition)
    snap = composition.snapshot(resolved)
    digest = strict_json.bundle_digest(snap)
    lead_path = compiler.lead_prompt_path(Path("/state"), digest)
    result = compiler.compile_launch(
        docs=bundle.docs,
        prompt_bodies=bundle.prompt_bodies,
        resolved=resolved,
        session_action=action or compiler.build_fresh(FIXED_SESSION),
        passthrough=passthrough if passthrough is not None else ["--verbose"],
        settings_path=SETTINGS_PATH,
        lead_prompt_path=lead_path,
    )
    return bundle, resolved, result


class GoldenTests(unittest.TestCase):
    def test_agents_json_golden(self) -> None:
        _, _, result = _compile()
        expected = (GOLDENS / "agents-contingency.json").read_bytes()
        self.assertEqual(
            strict_json.canonical_bytes(
                strict_json.loads(result.agents_json.encode("utf-8"))
            ),
            expected,
        )

    def test_lead_appendix_golden(self) -> None:
        bundle, resolved, _ = _compile()
        appendix = compiler.generate_lead_appendix(
            resolved, bundle.docs["providers"]["providers"]
        )
        expected = (GOLDENS / "lead-appendix.md").read_bytes()
        self.assertEqual(appendix.encode("utf-8"), expected)

    def test_argv_fresh_golden(self) -> None:
        _, _, result = _compile()
        expected = strict_json.loads((GOLDENS / "argv-fresh.json").read_bytes())
        self.assertEqual(result.argv, expected)

    def test_argv_resume_golden(self) -> None:
        _, _, result = _compile(action=compiler.build_resume(FIXED_SESSION))
        expected = strict_json.loads((GOLDENS / "argv-resume.json").read_bytes())
        self.assertEqual(result.argv, expected)
        self.assertNotIn("--session-id", result.argv)

    def test_env_golden(self) -> None:
        _, _, result = _compile()
        expected = strict_json.loads((GOLDENS / "env.json").read_bytes())
        self.assertEqual({"set": result.env_set, "unset": list(result.env_unset)}, expected)


class AgentDefinitionTests(unittest.TestCase):
    def test_identical_role_prompt_bodies_across_variants(self) -> None:
        _, _, result = _compile()
        agents = strict_json.loads(result.agents_json.encode("utf-8"))
        analyst_prompts = {
            key: value["prompt"]
            for key, value in agents.items()
            if key.startswith("cm-analyst-")
        }
        self.assertEqual(len(analyst_prompts), 2)
        self.assertEqual(len(set(analyst_prompts.values())), 1)
        implementer_prompts = {
            key: value["prompt"]
            for key, value in agents.items()
            if key.startswith("cm-implementer-")
        }
        self.assertEqual(len(set(implementer_prompts.values())), 1)
        self.assertNotEqual(
            set(analyst_prompts.values()), set(implementer_prompts.values())
        )

    def test_variant_differences_limited(self) -> None:
        _, _, result = _compile()
        agents = strict_json.loads(result.agents_json.encode("utf-8"))
        sol = agents["cm-analyst-sol-high"]
        kimi = agents["cm-analyst-kimi-k3-max"]
        self.assertNotEqual(sol["description"], kimi["description"])
        self.assertEqual(sol["model"], "gpt-multi-sol-high")
        self.assertEqual(kimi["model"], "claude-multi-kimi-k3[1m]")
        self.assertEqual(sol["effort"], "high")
        self.assertEqual(kimi["effort"], "max")

    def test_implementer_isolation_field(self) -> None:
        _, _, result = _compile()
        agents = strict_json.loads(result.agents_json.encode("utf-8"))
        self.assertEqual(agents["cm-implementer-sol-high"]["isolation"], "worktree")
        self.assertEqual(agents["cm-implementer-kimi-k3-max"]["isolation"], "worktree")
        self.assertNotIn("isolation", agents["cm-analyst-sol-high"])

    def test_preferred_marker_in_description(self) -> None:
        _, _, result = _compile()
        agents = strict_json.loads(result.agents_json.encode("utf-8"))
        self.assertIn(
            "Preferred cm-analyst variant.",
            agents["cm-analyst-sol-high"]["description"],
        )
        self.assertNotIn(
            "Preferred", agents["cm-analyst-kimi-k3-max"]["description"]
        )

    def test_contingency_mode_excludes_cm_lead(self) -> None:
        _, _, result = _compile()
        agents = strict_json.loads(result.agents_json.encode("utf-8"))
        self.assertEqual(result.lead_mode, compiler.CONTINGENCY_MODE)
        self.assertNotIn("cm-lead", agents)
        self.assertEqual(len(agents), 6)
        self.assertIn("--append-system-prompt-file", result.argv)
        self.assertNotIn("--agent", result.argv)
        self.assertIsNotNone(result.lead_prompt)

    def test_lead_prompt_contains_dynamic_contract(self) -> None:
        _, _, result = _compile()
        prompt = result.lead_prompt
        self.assertIn("# cm-lead", prompt)  # canonical body retained
        for variant_id in (
            "cm-analyst-sol-high",
            "cm-analyst-kimi-k3-max",
            "cm-implementer-sol-high",
            "cm-implementer-kimi-k3-max",
            "cm-reviewer-gpt55-high",
            "cm-reviewer-opus-xhigh",
        ):
            self.assertIn(variant_id, prompt)
        self.assertIn("Explore: replaced", prompt)
        self.assertIn("Plan: native.", prompt)
        self.assertIn("general-purpose: off.", prompt)
        self.assertIn("Enabled provider families: anthropic, moonshot, openai.", prompt)
        self.assertIn("must not receive its sole verdict", prompt)
        self.assertIn("One writer owns an overlapping file scope", prompt)
        self.assertIn("never pass a per-invocation model override", prompt)

    def test_independence_rules_match_enabled_families(self) -> None:
        bundle, resolved, _ = _compile()
        appendix = compiler.generate_lead_appendix(
            resolved, bundle.docs["providers"]["providers"]
        )
        # Reviewer families enabled: anthropic (opus), openai (gpt55).
        self.assertIn("anthropic-family variant while a reviewer from openai", appendix)
        self.assertIn("openai-family variant while a reviewer from anthropic", appendix)
        self.assertIn("moonshot-family variant while a reviewer from anthropic/openai", appendix)

    def test_independence_same_family_label_when_no_cross_reviewer(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        # Remove the GPT-5.5 (openai) reviewer → anthropic loses cross-family review.
        document["slots"] = [
            slot
            for slot in document["slots"]
            if not (slot["role"] == "cm-reviewer" and slot["model"] == "gpt55")
        ]
        for slot in document["slots"]:
            if slot["role"] == "cm-reviewer":
                slot["preferred"] = True
        resolved = composition.resolve(bundle.docs, document)
        appendix = compiler.generate_lead_appendix(
            resolved, bundle.docs["providers"]["providers"]
        )
        self.assertIn(
            "No enabled cross-family reviewer for anthropic-authored changes",
            appendix,
        )
        self.assertIn("same-family (reduced independence)", appendix)

    def test_appendix_changes_with_inventory(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-reviewer"
        ][:2]
        resolved = composition.resolve(bundle.docs, document)
        reduced = compiler.generate_lead_appendix(
            resolved, bundle.docs["providers"]["providers"]
        )
        self.assertNotIn("cm-reviewer-opus-xhigh", reduced)
        self.assertIn("cm-analyst-sol-high", reduced)

    def test_compile_deterministic(self) -> None:
        _, _, first = _compile()
        _, _, second = _compile()
        self.assertEqual(first.argv, second.argv)
        self.assertEqual(first.agents_json, second.agents_json)
        self.assertEqual(first.env_set, second.env_set)


class EnvironmentTests(unittest.TestCase):
    def test_missing_canonical_routes_fail_closed(self) -> None:
        bundle, resolved, _ = _compile()
        providers = bundle.docs["providers"]["providers"]
        for missing, kept in (("fable", ["claude-opus-4-8"]), ("opus", ["claude-fable-5"])):
            with self.subTest(missing=missing):
                mutated = {
                    **providers,
                    "anthropic": {
                        **providers["anthropic"],
                        "passthrough_routes": [
                            {"name": kept[0], "fork": True}
                        ],
                    },
                }
                with self.assertRaisesRegex(compiler.CompilerError, missing):
                    compiler.compile_environment(
                        bundle.docs["gateway"], mutated, resolved
                    )

    def test_reserved_lead_env_defensive_rejection(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        docs = copy.deepcopy(bundle.docs)
        docs["models"]["models"]["fable"]["lead"]["env"][
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS"
        ] = "1"
        resolved = composition.resolve(docs, bundle.default_composition)
        with self.assertRaisesRegex(compiler.CompilerError, "compiler-owned and reserved"):
            compiler.compile_environment(
                docs["gateway"], docs["providers"]["providers"], resolved
            )

    def _selector_only_result(self):
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        # Fable lead + Kimi variants only → no scalar-classified selection.
        document["slots"] = [
            {"role": "cm-lead", "model": "fable"},
            {"role": "cm-analyst", "model": "kimi-k3", "preferred": True},
            {"role": "cm-implementer", "model": "kimi-k3", "preferred": True},
        ]
        resolved = composition.resolve(bundle.docs, document)
        self.assertIsNone(resolved.scalar_context_tokens)
        snap = composition.snapshot(resolved)
        return bundle, resolved, compiler.compile_launch(
            docs=bundle.docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh(FIXED_SESSION),
            passthrough=[],
            settings_path=SETTINGS_PATH,
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), strict_json.bundle_digest(snap)
            ),
        )

    def test_scalar_absent_unsets_inherited_context(self) -> None:
        _, _, result = self._selector_only_result()
        self.assertIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", result.env_unset)
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", result.env_set)

    def test_scalar_present_sets_exact_value(self) -> None:
        _, _, result = _compile()
        self.assertEqual(result.env_set["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "272000")
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", result.env_unset)

    def test_env_set_and_unset(self) -> None:
        _, _, result = _compile()
        self.assertEqual(
            result.env_unset,
            (
                "CLAUDE_CODE_SUBAGENT_MODEL",
                "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
                "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
                "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            ),
        )
        self.assertEqual(
            result.env_set["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8317"
        )
        self.assertEqual(
            result.env_set["ANTHROPIC_DEFAULT_FABLE_MODEL"], "claude-fable-5[1m]"
        )
        self.assertEqual(
            result.env_set["ANTHROPIC_DEFAULT_OPUS_MODEL"], "claude-opus-4-8[1m]"
        )
        self.assertEqual(result.env_set["CLAUDE_MULTI_GATEWAY"], "1")
        self.assertEqual(result.env_set["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "272000")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", result.env_set)

    def test_lead_env_applied_after_unset(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        document["slots"][0] = {"role": "cm-lead", "model": "kimi-k3"}
        resolved = composition.resolve(bundle.docs, document)
        snap = composition.snapshot(resolved)
        digest = strict_json.bundle_digest(snap)
        result = compiler.compile_launch(
            docs=bundle.docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh(FIXED_SESSION),
            passthrough=[],
            settings_path=SETTINGS_PATH,
            lead_prompt_path=compiler.lead_prompt_path(Path("/state"), digest),
        )
        self.assertIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW", result.env_unset)
        self.assertEqual(
            result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1048576"
        )


if __name__ == "__main__":
    unittest.main()
