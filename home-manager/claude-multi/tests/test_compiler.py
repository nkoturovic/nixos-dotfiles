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
        hook_command="/stable/hook-shim" if durable else None,
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
            "cm-reviewer-opus5-xhigh",
        ):
            self.assertIn(variant_id, prompt)
        self.assertIn("Explore: replaced", prompt)
        self.assertIn("Plan: native.", prompt)
        self.assertIn("general-purpose: off.", prompt)
        self.assertIn("Enabled provider families: anthropic, moonshot, openai.", prompt)
        self.assertIn("must not receive its sole verdict", prompt)
        self.assertIn("One writer owns an overlapping file scope", prompt)
        self.assertIn("never pass a per-invocation model override", prompt)
        self.assertIn("Lead context: 1000000 client tokens", prompt)
        self.assertIn(
            "process compaction capacity 1000000; deterministic reactive trigger 882000",
            prompt,
        )
        self.assertIn("Proactive summary preparation is runtime-controlled", prompt)
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

    def test_user_attested_kimi_bound_is_not_labeled_provider_safe(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        document["slots"][0] = {"role": "cm-lead", "model": "kimi-k3"}
        resolved = composition.resolve(bundle.docs, document)
        appendix = compiler.generate_lead_appendix(
            resolved,
            bundle.docs["providers"]["providers"],
            session_id=FIXED_SESSION,
            composition_name="kimi-sol",
        )
        self.assertIn("user-attested configured provider bound 1000000", appendix)
        self.assertIn("not near-limit benchmark-verified", appendix)
        self.assertNotIn("provider-safe bound 1000000", appendix)

    def test_lower_context_native_agents_get_prompt_overflow_guidance(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        document["slots"][0] = {"role": "cm-lead", "model": "sol"}
        resolved = composition.resolve(bundle.docs, document)
        appendix = compiler.generate_lead_appendix(
            resolved,
            bundle.docs["providers"]["providers"],
            session_id=FIXED_SESSION,
            composition_name="sol-native",
        )
        self.assertIn("Lead context: 372000 client tokens", appendix)
        self.assertIn("Native agents inherit this lower-context lead", appendix)
        self.assertIn("loaded skills bounded", appendix)
        self.assertIn("does not raise this process capacity", appendix)
        self.assertIn("separate ordinary large-profile session", appendix)

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
        self.assertNotIn("cm-reviewer-opus5-xhigh", reduced)
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
        docs["models"]["models"]["opus5"]["lead"]["env"][
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
                "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
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
            result.env_set["ANTHROPIC_DEFAULT_OPUS_MODEL"], "claude-opus-5[1m]"
        )
        self.assertEqual(result.env_set["CLAUDE_MULTI_GATEWAY"], "1")
        self.assertEqual(result.env_set["CLAUDE_MULTI_SESSION_ID"], FIXED_SESSION)
        self.assertEqual(result.env_set["DISABLE_AUTOUPDATER"], "1")
        self.assertEqual(result.env_set["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "372000")
        self.assertEqual(result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000")
        self.assertEqual(result.env_set["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "90")
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
                docs["models"]["models"]["opus5"]["lead"]["env"][key] = "1"
                resolved = composition.resolve(docs, bundle.default_composition)
                with self.assertRaisesRegex(
                    compiler.CompilerError, "compiler-owned and reserved"
                ):
                    compiler.compile_environment(
                        docs["gateway"], docs["providers"]["providers"], resolved
                    )

    def test_kimi_uses_1m_capacity_with_explicit_90_percent(self) -> None:
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
        self.assertIn("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", result.env_unset)
        # Kimi remains a 1M main-loop lead even with 372K Sol variants. Claude
        # Code caps each model, reserves 20K output, then applies the common
        # preparation and reactive percentage policy to the prompt budget.
        self.assertEqual(
            result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000"
        )
        self.assertEqual(result.env_set["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "90")


if __name__ == "__main__":
    unittest.main()


class CompactionPolicyTests(unittest.TestCase):
    def _compile(self, document, *, docs=None):
        bundle = catalog.load_catalog(CATALOG_ROOT)
        active_docs = docs or bundle.docs
        resolved = composition.resolve(active_docs, document)
        digest = strict_json.bundle_digest(composition.snapshot(resolved))
        return compiler.compile_launch(
            docs=active_docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh(FIXED_SESSION),
            passthrough=[],
            settings_path=SETTINGS_PATH,
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), digest, FIXED_SESSION
            ),
        )

    def test_fable_lead_uses_1m_capacity_and_90_percent_trigger(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        # Default Fable lead stays at 1M even though selected Sol variants keep
        # the process scalar at 372K. The effective lead trigger is 900K.
        result = self._compile(document)
        self.assertEqual(
            result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "1000000"
        )
        self.assertEqual(result.env_set["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "90")

    def test_qwen_variant_narrows_process_capacity_for_fable_lead(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        document["slots"][1]["model"] = "qwen38"
        document["slots"][1].pop("lane", None)
        document["availability"]["models"]["qwen38"] = "lead+agents"
        document["availability"]["providers"]["qwen"] = "lead+agents"
        result = self._compile(document)
        self.assertEqual(
            result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "983616"
        )
        resolved = composition.resolve(bundle.docs, document)
        self.assertEqual(resolved.auto_compact_window_tokens, 983616)
        self.assertEqual(resolved.lead.auto_compact_tokens, 867254)

    def test_explicit_provider_window_controls_compaction_capacity(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        docs = copy.deepcopy(bundle.docs)
        docs["models"]["models"]["opus5"]["context"]["provider_tokens"] = 500000
        result = self._compile(document, docs=docs)
        self.assertEqual(
            result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "500000"
        )
        self.assertEqual(result.env_set["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "90")
        resolved = composition.resolve(docs, document)
        self.assertEqual(resolved.auto_compact_window_tokens, 500000)
        self.assertEqual(resolved.lead.auto_compact_tokens, 432000)


class RuntimeIdentityAndDirectCompileTests(unittest.TestCase):
    def test_resume_argv_targets_runtime_not_managed_id(self) -> None:
        runtime_id = "22222222-2222-4222-8222-222222222222"
        bundle, resolved, _ = _compile()
        snap = composition.snapshot(resolved)
        result = compiler.compile_launch(
            docs=bundle.docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_resume(FIXED_SESSION, runtime_id),
            passthrough=[],
            settings_path=SETTINGS_PATH,
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), strict_json.bundle_digest(snap), FIXED_SESSION
            ),
            durable=True,
            scope_dir=Path("/state/scopes") / FIXED_SESSION,
            hook_command="/nix/store/test/bin/claude-multi",
        )
        self.assertEqual(result.argv[:2], ["--resume", runtime_id])
        self.assertEqual(result.session_action.managed_id, FIXED_SESSION)
        self.assertEqual(result.env_set["CLAUDE_MULTI_MANAGED_ID"], FIXED_SESSION)

    def test_direct_large_profile_has_native_model_picker_without_agents(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        result = compiler.compile_direct_launch(
            docs=bundle.docs,
            session_action=compiler.build_fresh(FIXED_SESSION),
            model_id="qwen38",
            passthrough=["--verbose"],
            scope_dir=Path("/state/scopes") / FIXED_SESSION,
            hook_command="/nix/store/test/bin/claude-multi",
            state_root=Path("/state"),
        )
        self.assertFalse(result.write_lead_prompt)
        self.assertEqual(result.scope_plan.agent_files, {})
        self.assertIn("claude-multi-qwen38-max[1m]", result.scope_plan.settings["availableModels"])
        self.assertIn("claude-fable-5[1m]", result.scope_plan.settings["availableModels"])
        self.assertEqual(
            result.scope_plan.settings["model"], "claude-multi-qwen38-max[1m]"
        )
        self.assertEqual(
            result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "983616"
        )
        self.assertEqual(result.env_set["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "90")
        self.assertEqual(
            compiler.direct_profile_context(bundle.docs, "large"),
            (None, 983616, 867254),
        )
        self.assertNotIn("--agents", result.argv)
        self.assertNotIn("--append-system-prompt-file", result.argv)
        self.assertIn("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", result.env_unset)
        self.assertNotIn("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", result.env_set)

    def test_direct_selector_resolver_accepts_wire_and_client_forms(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        self.assertEqual(
            compiler.direct_model_for_selector(bundle.docs, "claude-fable-5"),
            ("fable", "large"),
        )
        self.assertEqual(
            compiler.direct_model_for_selector(bundle.docs, "gpt-multi-sol-xhigh"),
            ("sol", "sol"),
        )
        self.assertEqual(
            compiler.direct_model_for_selector(bundle.docs, "claude-opus-4-8[1m]"),
            ("opus", "large"),
        )
        self.assertIsNone(
            compiler.direct_model_for_selector(bundle.docs, "unknown-provider-model")
        )

    def test_direct_implicit_resume_omits_model_pin(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        result = compiler.compile_direct_launch(
            docs=bundle.docs,
            session_action=compiler.build_resume(FIXED_SESSION),
            model_id="qwen38",
            passthrough=[],
            scope_dir=Path("/state/scopes") / FIXED_SESSION,
            hook_command="/nix/store/test/bin/claude-multi",
            state_root=Path("/state"),
            pin_model=False,
        )
        self.assertNotIn("--model", result.argv)
        self.assertNotIn("--effort", result.argv)
        self.assertEqual(
            result.scope_plan.settings["model"], "claude-multi-qwen38-max[1m]"
        )

    def test_direct_sol_profile_excludes_large_context_models(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        result = compiler.compile_direct_launch(
            docs=bundle.docs,
            session_action=compiler.build_fresh(FIXED_SESSION),
            model_id="sol",
            passthrough=[],
            scope_dir=Path("/state/scopes") / FIXED_SESSION,
            hook_command="/nix/store/test/bin/claude-multi",
            state_root=Path("/state"),
        )
        self.assertEqual(
            result.scope_plan.settings["availableModels"],
            ["gpt-multi-sol-high", "gpt-multi-sol-xhigh"],
        )
        self.assertEqual(result.env_set["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "372000")
        self.assertEqual(result.env_set["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "372000")
        self.assertEqual(result.env_set["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "90")
        self.assertEqual(
            compiler.direct_profile_context(bundle.docs, "sol"),
            (372000, 372000, 316800),
        )

    def test_direct_profile_context_is_derived_from_catalog(self) -> None:
        import copy

        bundle = catalog.load_catalog(CATALOG_ROOT)
        docs = copy.deepcopy(bundle.docs)
        sol = docs["models"]["models"]["sol"]["context"]
        sol.update(
            client_tokens=400000,
            provider_tokens=400000,
            scalar_tokens=400000,
            declared_tokens=400000,
            validated_tokens=400000,
        )
        self.assertEqual(
            compiler.direct_profile_context(docs, "sol"),
            (400000, 400000, 342000),
        )
        qwen = docs["models"]["models"]["qwen38"]["context"]
        qwen.update(
            provider_tokens=900000,
            scalar_tokens=900000,
            declared_tokens=900000,
            validated_tokens=900000,
        )
        self.assertEqual(
            compiler.direct_profile_context(docs, "large"),
            (None, 900000, 792000),
        )


class SessionDisplayNameTests(unittest.TestCase):
    """--name gains the project basename (issue 013); old form without cwd."""

    def test_no_cwd_keeps_plain_prefix(self) -> None:
        self.assertEqual(compiler.session_display_name("cm:default", None), "cm:default")

    def test_root_cwd_keeps_plain_prefix(self) -> None:
        self.assertEqual(compiler.session_display_name("cg:sol", "/"), "cg:sol")

    def test_basename_appended(self) -> None:
        self.assertEqual(
            compiler.session_display_name("cm:kimi-sol", "/home/kotur/projects/occams-agent-flow"),
            "cm:kimi-sol@occams-agent-flow",
        )

    def test_long_basename_capped(self) -> None:
        name = compiler.session_display_name(
            "cg:glm52", "/x/" + "a" * 40
        )
        self.assertEqual(name, "cg:glm52@" + "a" * 24)

    def test_control_characters_stripped(self) -> None:
        name = compiler.session_display_name("cm:default", "/x/evil\x1b[2k\x07dir\n")
        self.assertNotIn("\x1b", name)
        self.assertNotIn("\x07", name)
        self.assertNotIn("\n", name)
        self.assertTrue(name.startswith("cm:default@evil"))

    def test_compile_launch_threads_session_cwd(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        snap = composition.snapshot(resolved)
        digest = strict_json.bundle_digest(snap)
        result = compiler.compile_launch(
            docs=bundle.docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh(FIXED_SESSION),
            passthrough=[],
            settings_path=SETTINGS_PATH,
            lead_prompt_path=compiler.lead_prompt_path(Path("/state"), digest, FIXED_SESSION),
            session_cwd="/home/kotur/projects/occams-agent-flow",
        )
        index = result.argv.index("--name")
        self.assertEqual(result.argv[index + 1], "cm:default@occams-agent-flow")

    def test_compile_launch_without_cwd_keeps_old_name(self) -> None:
        _bundle, _resolved, result = _compile()
        index = result.argv.index("--name")
        self.assertEqual(result.argv[index + 1], "cm:default")
