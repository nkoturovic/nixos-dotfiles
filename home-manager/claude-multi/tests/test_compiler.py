"""Tests for the deterministic compiler: agents, lead content, env, argv, goldens."""

from __future__ import annotations

import unittest
from pathlib import Path

from claude_multi import catalog, compiler, composition, strict_json


CATALOG_ROOT = Path(__file__).resolve().parents[1]
GOLDENS = CATALOG_ROOT / "tests" / "goldens" / "default"
FIXED_SESSION = "11111111-1111-4111-8111-111111111111"
OTHER_SESSION = "22222222-2222-4222-8222-222222222222"
SETTINGS_PATH = Path("/trusted/settings.json")
SCOPE_DIR = Path("/state") / "scopes" / FIXED_SESSION


def _compile(passthrough=None, action=None, durable=False):
    bundle = catalog.load_catalog(CATALOG_ROOT)
    resolved = composition.resolve(bundle.docs, bundle.default_composition)
    snap = composition.snapshot(resolved)
    digest = strict_json.bundle_digest(snap)
    lead_path = compiler.lead_prompt_path(Path("/state"), digest, FIXED_SESSION)
    result = compiler.compile_launch(
        docs=bundle.docs,
        prompt_bodies=bundle.prompt_bodies,
        resolved=resolved,
        session_action=action or compiler.build_fresh(FIXED_SESSION),
        passthrough=passthrough if passthrough is not None else ["--verbose"],
        settings_path=SETTINGS_PATH,
        lead_prompt_path=lead_path,
        durable=durable,
        scope_dir=SCOPE_DIR if durable else None,
    )
    return bundle, resolved, result


class LeadPromptPathTests(unittest.TestCase):
    def test_session_scoped_and_deterministic(self) -> None:
        digest_a = "sha256:" + "a" * 64
        digest_b = "sha256:" + "b" * 64
        path = compiler.lead_prompt_path(Path("/state"), digest_a, FIXED_SESSION)
        # resume of the same UUID is stable
        self.assertEqual(
            path, compiler.lead_prompt_path(Path("/state"), digest_a, FIXED_SESSION)
        )
        self.assertIn(FIXED_SESSION, path.name)
        self.assertIn(digest_a.removeprefix("sha256:")[:16], path.name)
        # fresh launches with different UUIDs never collide
        self.assertNotEqual(
            path, compiler.lead_prompt_path(Path("/state"), digest_a, OTHER_SESSION)
        )
        # composition transition for the same UUID lands on the new digest
        self.assertNotEqual(
            path, compiler.lead_prompt_path(Path("/state"), digest_b, FIXED_SESSION)
        )


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
            resolved,
            bundle.docs["providers"]["providers"],
            session_id=FIXED_SESSION,
            composition_name="default",
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


class DurableGoldenTests(unittest.TestCase):
    def test_argv_fresh_durable_golden(self) -> None:
        _, _, result = _compile(durable=True)
        expected = strict_json.loads((GOLDENS / "argv-fresh-durable.json").read_bytes())
        self.assertEqual(result.argv, expected)

    def test_argv_resume_durable_golden(self) -> None:
        _, _, result = _compile(
            action=compiler.build_resume(FIXED_SESSION), durable=True
        )
        expected = strict_json.loads((GOLDENS / "argv-resume-durable.json").read_bytes())
        self.assertEqual(result.argv, expected)
        self.assertNotIn("--session-id", result.argv)


class DurableArgvTests(unittest.TestCase):
    def test_durable_argv_drops_agents_and_disallowed_tools(self) -> None:
        _, _, result = _compile(durable=True)
        self.assertNotIn("--agents", result.argv)
        self.assertNotIn("--disallowedTools", result.argv)
        self.assertEqual(result.agents_json, "")

    def test_durable_argv_points_at_scope(self) -> None:
        _, _, result = _compile(durable=True)
        settings = result.argv[result.argv.index("--settings") + 1]
        self.assertEqual(settings, str(SCOPE_DIR / "settings.json"))
        add_dir = result.argv[result.argv.index("--add-dir") + 1]
        self.assertEqual(add_dir, str(SCOPE_DIR))
        # SPEC 3 order: --settings, --model, --effort, --add-dir, appendix.
        order = [
            result.argv.index(flag)
            for flag in ("--settings", "--model", "--effort", "--add-dir")
        ]
        self.assertEqual(order, sorted(order))
        self.assertGreater(
            result.argv.index("--append-system-prompt-file"),
            result.argv.index("--add-dir"),
        )

    def test_durable_compiles_scope_plan(self) -> None:
        _, resolved, result = _compile(durable=True)
        self.assertTrue(result.durable)
        self.assertIsNotNone(result.scope_plan)
        self.assertEqual(
            set(result.scope_plan.agent_files),
            {f".claude/agents/{variant.id}.md" for variant in resolved.variants},
        )
        self.assertEqual(result.scope_dir, SCOPE_DIR)

    def test_durable_requires_scope_dir(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        with self.assertRaisesRegex(compiler.CompilerError, "scope directory"):
            compiler.compile_launch(
                docs=bundle.docs,
                prompt_bodies=bundle.prompt_bodies,
                resolved=resolved,
                session_action=compiler.build_fresh(FIXED_SESSION),
                passthrough=[],
                settings_path=SETTINGS_PATH,
                lead_prompt_path=Path("/state/lead.md"),
                durable=True,
            )

    def test_legacy_mode_has_no_scope_plan(self) -> None:
        _, _, result = _compile()
        self.assertFalse(result.durable)
        self.assertIsNone(result.scope_plan)
        self.assertIsNone(result.scope_dir)


class PassthroughFlagTests(unittest.TestCase):
    def test_disable_slash_commands_blocked_per_d18(self) -> None:
        for token in ("--disable-slash-commands", "--disable-slash-commands=x"):
            with self.subTest(token=token):
                with self.assertRaisesRegex(
                    compiler.CompilerError, "agent-directory watching"
                ):
                    compiler.validate_passthrough([token])

    def test_add_dir_passthrough_allowed_and_extracted(self) -> None:
        # SPEC section 6: user --add-dir dirs load alongside the managed
        # scope; the collision gate scans them. Both argv forms pass through.
        _, _, result = _compile(
            passthrough=["--add-dir", "/extra/one", "--add-dir=/extra/two"],
            durable=True,
        )
        self.assertEqual(
            result.passthrough_add_dirs, ("/extra/one", "/extra/two")
        )
        self.assertIn("--add-dir", result.argv)
        # The launcher's own scope add-dir is separate from passthrough dirs.
        self.assertNotIn(str(SCOPE_DIR), result.passthrough_add_dirs)

    def test_add_dir_extraction_in_legacy_mode(self) -> None:
        _, _, result = _compile(passthrough=["--add-dir", "/extra"])
        self.assertEqual(result.passthrough_add_dirs, ("/extra",))

    def test_bare_add_dir_without_value_rejected(self) -> None:
        with self.assertRaisesRegex(compiler.CompilerError, "requires a value"):
            _compile(passthrough=["--add-dir"])


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
        self.assertNotIn("cm-lead", agents)
        self.assertEqual(len(agents), 6)
        self.assertIn("--append-system-prompt-file", result.argv)
        self.assertNotIn("--agent", result.argv)
        self.assertTrue(result.lead_prompt)

    def test_lead_prompt_contains_dynamic_contract(self) -> None:
        _, _, result = _compile()
        prompt = result.lead_prompt
        self.assertIn("# cm-lead", prompt)  # canonical body retained
        for variant_id in (
            "cm-analyst-sol-high",
            "cm-analyst-kimi-k3-max",
            "cm-implementer-sol-high",
            "cm-implementer-kimi-k3-max",
            "cm-reviewer-sol-xhigh",
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
        # G0' sentinel: exact session UUID, no-substitution clause, relaunch.
        self.assertIn(f"Managed session: {FIXED_SESSION}", prompt)
        self.assertIn("Never substitute a native or generic agent", prompt)
        self.assertIn(
            f"`claude-multi --composition default -r {FIXED_SESSION}`", prompt
        )

    def test_independence_rules_match_enabled_families(self) -> None:
        bundle, resolved, _ = _compile()
        appendix = compiler.generate_lead_appendix(
            resolved,
            bundle.docs["providers"]["providers"],
            session_id=FIXED_SESSION,
            composition_name="default",
        )
        # Reviewer families enabled: anthropic (opus), openai (gpt55).
        self.assertIn("anthropic-family variant while a reviewer from openai", appendix)
        self.assertIn("openai-family variant while a reviewer from anthropic", appendix)
        self.assertIn("moonshot-family variant while a reviewer from anthropic/openai", appendix)

    def test_independence_same_family_label_when_no_cross_reviewer(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        # Remove the Sol (openai) reviewer → openai loses its cross-family reviewer.
        document["slots"] = [
            slot
            for slot in document["slots"]
            if not (slot["role"] == "cm-reviewer" and slot["model"] == "sol")
        ]
        for slot in document["slots"]:
            if slot["role"] == "cm-reviewer":
                slot["preferred"] = True
        resolved = composition.resolve(bundle.docs, document)
        appendix = compiler.generate_lead_appendix(
            resolved,
            bundle.docs["providers"]["providers"],
            session_id=FIXED_SESSION,
            composition_name="default",
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
            resolved,
            bundle.docs["providers"]["providers"],
            session_id=FIXED_SESSION,
            composition_name="default",
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
                Path("/state"), strict_json.bundle_digest(snap), FIXED_SESSION
            ),
        )

    def test_scalar_absent_unsets_inherited_context(self) -> None:
        _, _, result = self._selector_only_result()
        self.assertIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", result.env_unset)
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", result.env_set)

    def test_scalar_present_sets_exact_value(self) -> None:
        _, _, result = _compile()
        self.assertEqual(result.env_set["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "372000")
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
                "CLAUDE_CONFIG_DIR",
                "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH",
                "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS",
                "CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS",
                "CLAUDE_CODE_DISABLE_WORKFLOWS",
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
        self.assertEqual(result.env_set["CLAUDE_MULTI_SESSION_ID"], FIXED_SESSION)
        self.assertEqual(result.env_set["DISABLE_AUTOUPDATER"], "1")
        self.assertEqual(result.env_set["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "372000")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", result.env_set)

    def test_nested_spawn_keys_never_compiled_while_pending(self) -> None:
        _, _, result = _compile()
        contract = catalog.load_catalog(CATALOG_ROOT).docs["native-contract"]
        self.assertEqual(contract["nested_subagents"]["decision"], "pending")
        for key in (
            "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH",
            "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS",
        ):
            self.assertNotIn(key, result.env_set)
            self.assertIn(key, result.env_unset)

    def test_g0_reserved_lead_env_keys_rejected(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        for key in (
            "CLAUDE_CONFIG_DIR",
            "DISABLE_AUTOUPDATER",
            "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH",
            "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS",
            "CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS",
            "CLAUDE_CODE_DISABLE_WORKFLOWS",
        ):
            with self.subTest(key=key):
                docs = copy.deepcopy(bundle.docs)
                docs["models"]["models"]["fable"]["lead"]["env"][key] = "1"
                resolved = composition.resolve(docs, bundle.default_composition)
                with self.assertRaisesRegex(
                    compiler.CompilerError, "compiler-owned and reserved"
                ):
                    compiler.compile_environment(
                        docs["gateway"], docs["providers"]["providers"], resolved
                    )

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
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), digest, FIXED_SESSION
            ),
        )
        self.assertIn("CLAUDE_CODE_AUTO_COMPACT_WINDOW", result.env_unset)
        # Clamped to 90% of the 372K scalar so compaction fires before the
        # provider cap — an explicit value above the cap can never trigger.
        self.assertEqual(
            result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "334800"
        )


if __name__ == "__main__":
    unittest.main()


class CompactionWindowClampTests(unittest.TestCase):
    def _compile(self, document):
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, document)
        digest = strict_json.bundle_digest(composition.snapshot(resolved))
        return compiler.compile_launch(
            docs=bundle.docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh(FIXED_SESSION),
            passthrough=[],
            settings_path=SETTINGS_PATH,
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), digest, FIXED_SESSION
            ),
        )

    def test_absent_window_derives_ninety_percent_of_scalar(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        # default (fable lead) has no explicit window; scalar is 372000.
        result = self._compile(document)
        self.assertEqual(
            result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "334800"
        )

    def test_lower_explicit_window_is_respected(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        # A composition-level earlier trigger beats the 90% default.
        # Lead env comes from the model entry; simulate via a lower value on
        # the resolved lead env by compiling with a patched document model.
        docs = copy.deepcopy(bundle.docs)
        docs["models"]["models"]["fable"]["lead"]["env"][
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW"
        ] = "200000"
        resolved = composition.resolve(docs, document)
        digest = strict_json.bundle_digest(composition.snapshot(resolved))
        result = compiler.compile_launch(
            docs=docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh(FIXED_SESSION),
            passthrough=[],
            settings_path=SETTINGS_PATH,
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), digest, FIXED_SESSION
            ),
        )
        self.assertEqual(
            result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "200000"
        )
