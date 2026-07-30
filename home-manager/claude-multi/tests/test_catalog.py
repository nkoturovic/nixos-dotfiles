"""Tests for the trusted catalog seed, references, and bundle hash."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from claude_multi import catalog, strict_json, validate
from claude_multi.catalog import CatalogError


CATALOG_ROOT = Path(__file__).resolve().parents[1]

RETAINED_SELECTOR_BASES = {
    "claude-fable-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-multi-kimi-k3",
    "claude-multi-opus-5",
    "claude-multi-opus-4-8",
    "gpt-multi-sol-high",
    "gpt-multi-sol-xhigh",
    "gpt-multi-gpt55-high",
    "claude-multi-qwen38-max",
    "claude-multi-glm52-max",
}
REMOVED_PATTERNS = (
    "claude-multi-fable-5",
    "claude-multi-sol-",
    "claude-multi-gpt55",
    "conserve-",
)


def _raw() -> dict:
    return catalog.load_raw(CATALOG_ROOT)


def _mutate(mutator) -> list[str]:
    raw = _raw()
    mutator(raw)
    return catalog.validate_catalog(raw)


def _copy_tree(case: unittest.TestCase) -> Path:
    temporary = Path(tempfile.mkdtemp(prefix="claude-multi-catalog-"))
    case.addCleanup(lambda: shutil.rmtree(temporary, ignore_errors=True))
    shutil.copytree(CATALOG_ROOT, temporary / "claude-multi")
    # Copies may originate from a read-only store path; make them writable.
    for root_dir, dirs, files in os.walk(temporary / "claude-multi"):
        os.chmod(root_dir, 0o755)
        for name in files:
            os.chmod(Path(root_dir) / name, 0o644)
    return temporary / "claude-multi"


class SeedLoadTests(unittest.TestCase):
    def test_seed_loads_clean(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        self.assertEqual(
            set(bundle.providers), {"anthropic", "kimi", "openai", "qwen"}
        )
        self.assertEqual(
            set(bundle.models), {"fable", "opus", "opus5", "kimi-k3", "sol", "gpt55", "qwen38", "glm52"}
        )
        self.assertEqual(
            set(bundle.roles), {"cm-lead", "cm-analyst", "cm-reviewer", "cm-implementer"}
        )

    def test_validate_catalog_accepts_seed(self) -> None:
        self.assertEqual(catalog.validate_catalog(_raw()), [])

    def test_retained_alias_split_exact(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        bases: set[str] = set()
        for provider in bundle.providers.values():
            for route in provider["passthrough_routes"]:
                bases.add(route["name"])
        for model in bundle.models.values():
            for lane in model["lanes"].values():
                selector = lane["client_selector"]
                bases.add(selector.removesuffix("[1m]"))
        self.assertEqual(bases, RETAINED_SELECTOR_BASES)

    def test_removed_aliases_absent_from_bundle(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        blob = strict_json.canonical_bytes(bundle.bundle).decode("utf-8")
        for pattern in REMOVED_PATTERNS:
            self.assertNotIn(pattern, blob)

    def test_fork_rule_only_on_canonical_routes(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        forked = [
            route["name"]
            for provider in bundle.providers.values()
            for route in provider["passthrough_routes"]
            if route["fork"]
        ]
        self.assertEqual(sorted(forked), ["claude-fable-5", "claude-opus-4-8", "claude-opus-5"])

    def test_context_evidence_separated(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        kimi = bundle.models["kimi-k3"]["context"]
        self.assertEqual(kimi["client_tokens"], 1000000)
        self.assertEqual(kimi["provider_tokens"], 1000000)
        self.assertIsNone(kimi["scalar_tokens"])
        self.assertEqual(kimi["ordinary_profile"], "large")
        self.assertEqual(kimi["declared_tokens"], 1048576)
        self.assertEqual(kimi["validated_tokens"], 208034)
        self.assertEqual(kimi["provider_stated_limit_tokens"], 262144)
        self.assertEqual(kimi["user_reported_tokens"], 1000000)
        self.assertEqual(bundle.models["sol"]["context"]["scalar_tokens"], 372000)
        self.assertEqual(bundle.models["gpt55"]["context"]["scalar_tokens"], 272000)

    def test_settings_carry_no_worktree_keys(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        self.assertNotIn("worktree", bundle.docs["settings"])
        self.assertEqual(
            set(bundle.docs["settings"]),
            {"disableWorkflows", "workflowSizeGuideline", "workflowKeywordTriggerEnabled"},
        )

    def test_version_json_matches_v2_2_schema_and_catalog_change(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        self.assertEqual(bundle.docs["version"]["launcher_version"], "2.12.0")
        self.assertEqual(bundle.docs["version"]["catalog_version"], 13)


class ReferenceViolationTests(unittest.TestCase):
    def test_unknown_provider_reference(self) -> None:
        errors = _mutate(
            lambda raw: raw["docs"]["models"]["models"]["sol"].update(provider="nope")
        )
        self.assertTrue(any("unknown provider" in error for error in errors))

    def test_unknown_role_reference(self) -> None:
        errors = _mutate(
            lambda raw: raw["docs"]["models"]["models"]["sol"]["compatible_roles"].append("cm-nope")
        )
        self.assertTrue(any("unknown role" in error for error in errors))

    def test_missing_default_lane(self) -> None:
        errors = _mutate(
            lambda raw: raw["docs"]["models"]["models"]["sol"].update(default_lane="turbo")
        )
        self.assertTrue(any("default_lane" in error for error in errors))

    def test_lane_effort_outside_vocabulary(self) -> None:
        errors = _mutate(
            lambda raw: raw["docs"]["models"]["models"]["sol"]["lanes"]["high"].update(
                agent_effort="ludicrous"
            )
        )
        self.assertTrue(any("effort vocabulary" in error for error in errors))

    def test_lane_contract_undeclared_by_provider(self) -> None:
        errors = _mutate(
            lambda raw: raw["docs"]["models"]["models"]["sol"]["lanes"]["high"].update(
                proxy_effort_contract="output-config-max"
            )
        )
        self.assertTrue(any("proxy effort contract" in error for error in errors))

    def test_duplicate_lane_selector_conflict(self) -> None:
        def mutate(raw):
            raw["docs"]["models"]["models"]["gpt55"]["lanes"]["high"][
                "client_selector"
            ] = "gpt-multi-sol-high"

        errors = _mutate(mutate)
        self.assertTrue(any("duplicates" in error for error in errors))

    def test_fork_rejected_on_noncanonical_route(self) -> None:
        def mutate(raw):
            raw["docs"]["providers"]["providers"]["anthropic"][
                "passthrough_routes"
            ].append({"name": "claude-multi-fable-5", "fork": True})

        errors = _mutate(mutate)
        self.assertTrue(any("fork:true is trusted only on canonical" in error for error in errors))

    def test_passthrough_without_fork_rejected(self) -> None:
        def mutate(raw):
            raw["docs"]["providers"]["providers"]["anthropic"]["passthrough_routes"][0][
                "fork"
            ] = False

        errors = _mutate(mutate)
        self.assertTrue(any("must carry" in error for error in errors))

    def test_secret_like_value_rejected(self) -> None:
        errors = _mutate(
            lambda raw: raw["docs"]["models"]["models"]["sol"].update(
                routing_note="temporary note sk-live123456789"
            )
        )
        self.assertTrue(any("secret-like value" in error for error in errors))

    def test_env_secret_reference_allowed(self) -> None:
        self.assertEqual(
            catalog.validate_catalog(_raw()),
            [],
            "env: secret references must remain acceptable",
        )

    def test_prompt_traversal_rejected(self) -> None:
        root = _copy_tree(self)
        roles_path = root / "catalog" / "roles.json"
        roles_doc = json.loads(roles_path.read_text())
        roles_doc["roles"]["cm-analyst"]["prompt_file"] = "../escape.md"
        roles_path.write_text(json.dumps(roles_doc))
        with self.assertRaises(CatalogError):
            catalog.load_raw(root)

    def test_prompt_symlink_escape_rejected(self) -> None:
        root = _copy_tree(self)
        prompt = root / "catalog" / "prompts" / "cm-analyst.md"
        prompt.unlink()
        prompt.symlink_to(Path("/etc/hostname"))
        with self.assertRaises(CatalogError):
            catalog.load_raw(root)

    def test_prompt_symlink_inside_catalog_rejected(self) -> None:
        root = _copy_tree(self)
        prompt = root / "catalog" / "prompts" / "cm-analyst.md"
        prompt.unlink()
        prompt.symlink_to(root / "catalog" / "prompts" / "cm-lead.md")
        with self.assertRaises(CatalogError):
            catalog.load_raw(root)

    def test_lane_selector_riding_cross_provider_route(self) -> None:
        def mutate(raw):
            raw["docs"]["models"]["models"]["kimi-k3"]["lanes"]["max"][
                "client_selector"
            ] = "claude-opus-4-8[1m]"

        errors = _mutate(mutate)
        self.assertTrue(
            any("rides another provider's passthrough route" in error for error in errors),
            f"errors: {errors}",
        )

    def test_unverified_settings_key_rejected(self) -> None:
        errors = _mutate(
            lambda raw: raw["docs"]["settings"].update(worktree={"baseRef": "head"})
        )
        self.assertTrue(any("worktree" in error for error in errors))


class CatalogMutationMatrixTests(unittest.TestCase):
    def test_enforced_checks(self) -> None:
        cases: list[tuple[str, object, str]] = []

        def case(name: str, mutator, needle: str) -> None:
            cases.append((name, mutator, needle))

        case(
            "empty prompt body",
            lambda raw: raw["prompt_bodies"].__setitem__("cm-analyst", b""),
            "prompt body is empty",
        )
        case(
            "whitespace-only prompt body",
            lambda raw: raw["prompt_bodies"].__setitem__("cm-analyst", b"  \n"),
            "prompt body is empty",
        )
        case(
            "duplicate prompt bodies across roles",
            lambda raw: raw["prompt_bodies"].__setitem__(
                "cm-reviewer", raw["prompt_bodies"]["cm-analyst"]
            ),
            "prompt body duplicates",
        )
        case(
            "missing version.json launcher_version",
            lambda raw: raw["docs"]["version"].pop("launcher_version"),
            "missing 'launcher_version'",
        )
        case(
            "missing version.json catalog_version",
            lambda raw: raw["docs"]["version"].pop("catalog_version"),
            "missing 'catalog_version'",
        )
        case(
            "non CLAUDE_CODE_ lead env key",
            lambda raw: raw["docs"]["models"]["models"]["kimi-k3"]["lead"]["env"].__setitem__(
                "HOME", "1"
            ),
            "unexpected variable",
        )
        case(
            "duplicate passthrough route names",
            lambda raw: raw["docs"]["providers"]["providers"]["anthropic"][
                "passthrough_routes"
            ].append({"name": "claude-fable-5", "fork": True}),
            "duplicate passthrough route",
        )
        case(
            "client selector differs from default lane selector",
            lambda raw: raw["docs"]["models"]["models"]["sol"].__setitem__(
                "client_selector", "gpt-multi-sol-xhigh"
            ),
            "must equal the default lane",
        )
        case(
            "settings version key forbidden",
            lambda raw: raw["docs"]["settings"].__setitem__("version", 1),
            "must not contain",
        )
        for reserved in (
            "CLAUDE_CODE_SUBAGENT_MODEL",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
            "CLAUDE_CODE_DISABLE_WORKFLOWS",
        ):
            case(
                f"reserved lead env key {reserved}",
                lambda raw, key=reserved: raw["docs"]["models"]["models"]["kimi-k3"][
                    "lead"
                ]["env"].__setitem__(key, "1"),
                "compiler-owned and reserved",
            )
        case(
            "zero lead env value",
            lambda raw: raw["docs"]["models"]["models"]["kimi-k3"]["lead"]["env"].__setitem__(
                "CLAUDE_CODE_EXAMPLE_LIMIT", "0"
            ),
            "must be a positive integer",
        )
        case(
            "provider context exceeds client",
            lambda raw: raw["docs"]["models"]["models"]["sol"]["context"].__setitem__(
                "provider_tokens", 400000
            ),
            "exceeds client_tokens",
        )
        case(
            "scalar context exceeds provider",
            lambda raw: raw["docs"]["models"]["models"]["sol"]["context"].__setitem__(
                "scalar_tokens", 400000
            ),
            "exceeds provider_tokens",
        )
        case(
            "validated context exceeds declaration",
            lambda raw: raw["docs"]["models"]["models"]["sol"]["context"].__setitem__(
                "validated_tokens", 400000
            ),
            "exceeds declared_tokens",
        )
        case(
            "unvalidated provider context lacks matching attestation",
            lambda raw: raw["docs"]["models"]["models"]["kimi-k3"]["context"].__setitem__(
                "user_reported_tokens", 900000
            ),
            "must match user_reported_tokens",
        )
        case(
            "selector and client context disagree",
            lambda raw: raw["docs"]["models"]["models"]["sol"]["context"].__setitem__(
                "client_tokens", 1000000
            ),
            "selector classification",
        )
        case(
            "lead missing ordinary profile",
            lambda raw: raw["docs"]["models"]["models"]["sol"]["context"].__setitem__(
                "ordinary_profile", None
            ),
            "lead-capable models must belong",
        )
        case(
            "empty anthropic passthrough routes",
            lambda raw: raw["docs"]["providers"]["providers"]["anthropic"].__setitem__(
                "passthrough_routes", []
            ),
            "missing canonical passthrough route 'claude-fable-5'",
        )
        case(
            "missing canonical fable route",
            lambda raw: raw["docs"]["providers"]["providers"]["anthropic"][
                "passthrough_routes"
            ].pop(0),
            "missing canonical passthrough route 'claude-fable-5'",
        )
        case(
            "missing canonical opus route",
            lambda raw: raw["docs"]["providers"]["providers"]["anthropic"][
                "passthrough_routes"
            ].pop(-1),
            "missing canonical passthrough route 'claude-opus-4-8'",
        )
        for name, mutator, needle in cases:
            with self.subTest(case=name):
                errors = _mutate(mutator)
                if needle == "__NO_ERROR_EXPECTED__":
                    self.assertEqual(errors, [], f"{name}: unexpected errors {errors}")
                else:
                    self.assertTrue(
                        any(needle in error for error in errors),
                        f"{name}: expected {needle!r} in {errors}",
                    )

    def test_empty_routes_reports_both_canonical_missing(self) -> None:
        errors = _mutate(
            lambda raw: raw["docs"]["providers"]["providers"]["anthropic"].__setitem__(
                "passthrough_routes", []
            )
        )
        self.assertTrue(
            any("missing canonical passthrough route 'claude-fable-5'" in e for e in errors)
        )
        self.assertTrue(
            any("missing canonical passthrough route 'claude-opus-4-8'" in e for e in errors)
        )


class CompositionViolationTests(unittest.TestCase):
    def _composition(self) -> dict:
        return copy.deepcopy(catalog.load_raw(CATALOG_ROOT)["docs"]["compositions/default"])

    def _validate(self, composition: dict) -> list[str]:
        raw = _raw()
        return catalog.validate_composition(
            composition,
            raw["docs"]["models"]["models"],
            raw["docs"]["roles"]["roles"],
            raw["docs"]["providers"]["providers"],
        )

    def test_default_seed_valid(self) -> None:
        self.assertEqual(self._validate(self._composition()), [])

    def test_model_scope_exceeding_provider_blocked(self) -> None:
        composition = self._composition()
        composition["availability"]["providers"]["kimi"] = "off"
        errors = self._validate(composition)
        self.assertTrue(any("exceeds provider" in error for error in errors))

    def test_model_scope_exceeding_capability_blocked(self) -> None:
        composition = self._composition()
        composition["availability"]["models"]["gpt55"] = "lead+agents"
        errors = self._validate(composition)
        self.assertTrue(any("exceeds immutable catalog capability" in error for error in errors))

    def test_two_preferred_variants_blocked(self) -> None:
        composition = self._composition()
        composition["slots"][1]["preferred"] = True
        composition["slots"][2]["preferred"] = True
        errors = self._validate(composition)
        self.assertTrue(any("exactly one preferred" in error for error in errors))

    def test_no_preferred_variant_blocked(self) -> None:
        composition = self._composition()
        for slot in composition["slots"]:
            if slot["role"] == "cm-analyst":
                slot["preferred"] = False
        errors = self._validate(composition)
        self.assertTrue(any("exactly one preferred" in error for error in errors))

    def test_duplicate_triple_blocked(self) -> None:
        composition = self._composition()
        composition["slots"].append(
            {"role": "cm-analyst", "model": "sol", "preferred": False}
        )
        errors = self._validate(composition)
        self.assertTrue(any("duplicate slot" in error for error in errors))

    def test_role_incompatible_slot_blocked(self) -> None:
        composition = self._composition()
        composition["slots"].append(
            {"role": "cm-analyst", "model": "opus", "preferred": False}
        )
        errors = self._validate(composition)
        self.assertTrue(any("not compatible" in error for error in errors))

    def test_lead_capability_enforced_exactly(self) -> None:
        composition = self._composition()
        composition["slots"][0] = {"role": "cm-lead", "model": "gpt55"}
        composition["availability"]["models"]["gpt55"] = "lead+agents"
        errors = self._validate(composition)
        self.assertTrue(
            any("lacks the lead capability" in error for error in errors),
            f"errors: {errors}",
        )

    def test_lead_block_enforced_exactly(self) -> None:
        raw = _raw()
        models = copy.deepcopy(raw["docs"]["models"]["models"])
        models["sol"]["lead"] = None
        composition = self._composition()
        composition["slots"][0] = {"role": "cm-lead", "model": "sol"}
        errors = catalog.validate_composition(
            composition,
            models,
            raw["docs"]["roles"]["roles"],
            raw["docs"]["providers"]["providers"],
        )
        self.assertTrue(
            any("has no lead block" in error for error in errors),
            f"errors: {errors}",
        )

    def test_lead_slot_with_lane_blocked(self) -> None:
        composition = self._composition()
        lead_model = _raw()["docs"]["models"]["models"][composition["slots"][0]["model"]]
        composition["slots"][0]["lane"] = lead_model["default_lane"]
        errors = self._validate(composition)
        self.assertTrue(
            any("lane does not apply to the lead slot" in error for error in errors),
            f"errors: {errors}",
        )

    def test_lead_slot_with_preferred_blocked(self) -> None:
        composition = self._composition()
        composition["slots"][0]["preferred"] = True
        errors = self._validate(composition)
        self.assertTrue(
            any("preferred does not apply to the lead slot" in error for error in errors),
            f"errors: {errors}",
        )

    def test_explore_replace_requires_analyst(self) -> None:
        composition = self._composition()
        composition["slots"] = [
            slot for slot in composition["slots"] if slot["role"] != "cm-analyst"
        ]
        errors = self._validate(composition)
        self.assertTrue(any("cm-analyst" in error for error in errors))

    def test_missing_lead_blocked(self) -> None:
        composition = self._composition()
        composition["slots"] = [
            slot for slot in composition["slots"] if slot["role"] != "cm-lead"
        ]
        errors = self._validate(composition)
        self.assertTrue(any("exactly one cm-lead" in error for error in errors))

    def test_workflows_native_and_off_accepted(self) -> None:
        for value in ("native", "off"):
            with self.subTest(value=value):
                composition = self._composition()
                composition["workflows"] = value
                self.assertEqual(self._validate(composition), [])

    def test_workflows_invalid_value_blocked(self) -> None:
        composition = self._composition()
        composition["workflows"] = "semi"
        errors = self._validate(composition)
        self.assertTrue(any("workflows" in error for error in errors))

    def test_composition_schema_accepts_optional_workflows(self) -> None:
        schema = strict_json.load(CATALOG_ROOT / "schemas" / "composition.schema.json")
        document = self._composition()
        self.assertEqual(validate.validate(document, schema, "$"), [])
        document["workflows"] = "off"
        self.assertEqual(validate.validate(document, schema, "$"), [])
        document["workflows"] = "sometimes"
        self.assertTrue(validate.validate(document, schema, "$"))


class BundleHashTests(unittest.TestCase):
    def test_bundle_hash_deterministic(self) -> None:
        first = catalog.load_catalog(CATALOG_ROOT)
        second = catalog.load_catalog(CATALOG_ROOT)
        self.assertEqual(first.bundle_sha256, second.bundle_sha256)
        self.assertTrue(first.bundle_sha256.startswith("sha256:"))

    def test_bundle_hash_matches_canonical_bytes(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        expected = "sha256:" + strict_json.sha256_hex(
            strict_json.canonical_bytes(bundle.bundle)
        )
        self.assertEqual(bundle.bundle_sha256, expected)

    def test_bundle_hash_changes_with_content(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        mutated = copy.deepcopy(bundle.bundle)
        mutated["models"]["models"]["sol"]["display"] = "changed"
        self.assertNotEqual(
            strict_json.bundle_digest(mutated), bundle.bundle_sha256
        )

    def test_bundle_records_prompt_identity_hashes(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        for role_id, body in bundle.prompt_bodies.items():
            expected = "sha256:" + strict_json.sha256_hex(body)
            self.assertEqual(bundle.bundle["prompts"][role_id], expected)


class SupportNoteTests(unittest.TestCase):
    def test_anthropic_transport_support_is_honest(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        anthropic = bundle.providers["anthropic"]
        self.assertEqual(anthropic["support"], "anthropic-supported")
        self.assertIn("not officially supported by Anthropic", anthropic["support_note"])
        for provider_id in ("kimi", "openai"):
            provider = bundle.providers[provider_id]
            self.assertEqual(provider["support"], "locally-validated-experimental")
            self.assertIn("not officially supported", provider["support_note"])

    def test_missing_support_note_rejected(self) -> None:
        root = _copy_tree(self)
        providers_path = root / "catalog" / "providers.json"
        document = json.loads(providers_path.read_text())
        del document["providers"]["kimi"]["support_note"]
        providers_path.write_text(json.dumps(document))
        with self.assertRaisesRegex(CatalogError, "support_note"):
            catalog.load_raw(root)


class ReviewSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = strict_json.load(CATALOG_ROOT / "schemas" / "review.schema.json")
        validate.check_schema(cls.schema)

    def _review(self, path: str) -> dict:
        return {
            "version": 1,
            "draft": "d1",
            "draft_hash": "sha256:" + "1" * 64,
            "repo": {"path": "/repo", "revision": "deadbeef"},
            "files": [
                {
                    "path": path,
                    "pre_image_hash": None,
                    "post_image_hash": "sha256:" + "0" * 64,
                }
            ],
            "results": {
                "bundle_valid": True,
                "render_sha256": "sha256:" + "2" * 64,
                "unavailable_providers": [],
                "diff": "",
            },
            "created_at": "2026-07-21T00:00:00Z",
        }

    def test_results_shape_enforced(self) -> None:
        document = self._review("a")
        self.assertEqual(validate.validate(document, self.schema), [])
        document["results"]["secret"] = "x"
        self.assertTrue(validate.validate(document, self.schema))
        document = self._review("a")
        del document["results"]["diff"]
        self.assertTrue(validate.validate(document, self.schema))

    def test_accepted_paths(self) -> None:
        for path in (
            "a",
            "home-manager/claude-multi/catalog/models.json",
            "x/y_z-1.2/q.md",
        ):
            with self.subTest(path=path):
                self.assertEqual(validate.validate(self._review(path), self.schema), [])

    def test_rejected_paths(self) -> None:
        for path in (
            "",
            "/abs",
            "a/../b",
            "a/./b",
            ".hidden/x",
            "a//b",
            "a/.x",
            "../x",
            "a/",
        ):
            with self.subTest(path=path):
                self.assertTrue(
                    validate.validate(self._review(path), self.schema),
                    f"path {path!r} must be rejected",
                )


class GatewayStaticSchemaTests(unittest.TestCase):
    def test_static_blob_typo_rejected(self) -> None:
        def mutate(raw):
            raw["docs"]["gateway"]["gateway"]["cliproxy_static"]["request-retry-x"] = 1

        raw = _raw()
        mutate(raw)
        schema = strict_json.load(CATALOG_ROOT / "schemas" / "gateway.schema.json")
        problems = validate.validate(raw["docs"]["gateway"], schema, "$")
        self.assertTrue(any("unexpected key" in problem for problem in problems))

    def test_static_blob_seed_valid(self) -> None:
        raw = _raw()
        schema = strict_json.load(CATALOG_ROOT / "schemas" / "gateway.schema.json")
        self.assertEqual(validate.validate(raw["docs"]["gateway"], schema, "$"), [])


class NativeContractTests(unittest.TestCase):
    def test_record_is_offline_and_unverified_where_required(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        record = bundle.docs["native-contract"]
        self.assertEqual(record["claude"]["validated_version"], "2.1.220")
        inspection = record["claude"]["executable"]["inspection"]
        self.assertIn("--version", inspection)
        self.assertIn("--help", inspection)
        self.assertIn("no prompt", inspection)
        self.assertIn("no provider request", inspection)
        self.assertIn("no daemon contact", inspection)
        acceptance = record["acceptance"]
        self.assertEqual(set(acceptance), {"U1", "U2", "U5", "U6", "U10"})
        for name, entry in acceptance.items():
            with self.subTest(acceptance=name):
                if name in ("U5", "U10"):
                    # D39: the fence is acceptance-verified by the offline
                    # delegation probe on the pinned binary. D43: the
                    # SubagentStop hook contract is probe-verified (fires on
                    # completion, honors block; silent on API-error death).
                    self.assertEqual(entry["status"], "verified")
                else:
                    self.assertEqual(entry["status"], "unverified")
        # The same-launch delivery record is deleted: contingency is the only
        # lead delivery, so no delivery record remains to drift.
        self.assertNotIn("lead_delivery", record)

    def test_promoted_executable_facts_locked(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        executable = bundle.docs["native-contract"]["claude"]["executable"]
        self.assertEqual(executable["configured_path"], "/home/kotur/.local/bin/claude")
        self.assertEqual(
            executable["resolved_path"],
            "/home/kotur/.local/share/claude/versions/2.1.220",
        )
        self.assertEqual(
            executable["sha256"],
            "674f61f20ff306f3100cf9200e4c36c4b70278b5bef2884549819b942a89c863",
        )
        self.assertEqual(executable["inspected_at"], "2026-07-27")
        self.assertEqual(bundle.docs["native-contract"]["recorded_at"], "2026-07-22")
        self.assertEqual(bundle.docs["native-contract"]["evidence_version"], 2)

    def test_models_minimum_tested_floor_locked_separately(self) -> None:
        # No provider/model requests are allowed in this automation, so model
        # compatibility at the inspected 2.1.217 binary is untested; 2.1.216
        # remains the truthful minimum-tested floor, decoupled from the native
        # artifact version.
        bundle = catalog.load_catalog(CATALOG_ROOT)
        validated = bundle.docs["native-contract"]["claude"]["validated_version"]
        self.assertEqual(validated, "2.1.220")
        floors = {"opus5": "2.1.218"}
        for model_id, model in bundle.models.items():
            with self.subTest(model=model_id):
                self.assertEqual(
                    model["minimum_tested"]["claude_code"],
                    floors.get(model_id, "2.1.216"),
                )

    def test_generic_agent_aliases_recorded_for_policy(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        aliases = bundle.docs["native-contract"]["generic_agent_aliases"]
        self.assertEqual(aliases["values"], ["claude"])
        self.assertEqual(aliases["status"], "provisionally-trusted")
        self.assertIn("not functional verification", aliases["evidence"])

    def test_capabilities_all_pending(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        capabilities = bundle.docs["native-contract"]["capabilities"]
        self.assertEqual(
            set(capabilities),
            {
                "root_agent_discovery",
                "persistent_denies",
                "onboarding_trust",
                "transcript_location_exact_resume",
                "selectors_effort_tools_worktree",
                "background_supervisor_lifecycle",
                "root_memory_reinjection",
                "project_agent_collision",
            },
        )
        for name, entry in capabilities.items():
            with self.subTest(capability=name):
                self.assertEqual(entry["status"], "pending")

    def test_nested_subagent_decision_pending(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        nested = bundle.docs["native-contract"]["nested_subagents"]
        self.assertEqual(nested["decision"], "pending")
        self.assertEqual(
            nested["depth_key"],
            {"name": "CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH", "status": "unverified"},
        )
        self.assertEqual(
            nested["concurrency_key"],
            {"name": "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "status": "unverified"},
        )
        self.assertIn("not functional verification", nested["evidence"])

    def test_lifecycle_evidence_identifier_matches_inspected_version(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        record = bundle.docs["native-contract"]
        lifecycle = record["lifecycle_evidence"]
        self.assertEqual(
            lifecycle["inspected_version"], record["claude"]["validated_version"]
        )
        self.assertIn("phase0", lifecycle["evidence_id"])

    def test_effort_vocabulary_evidenced(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        vocabulary = bundle.docs["native-contract"]["effort_vocabulary"]
        self.assertEqual(
            vocabulary["values"], ["high", "xhigh", "max", "ultracode"]
        )
        self.assertEqual(vocabulary["status"], "provisionally-trusted")


class NativeContractSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = strict_json.load(
            CATALOG_ROOT / "schemas" / "native-contract.schema.json"
        )
        validate.check_schema(cls.schema)

    def _document(self) -> dict:
        return copy.deepcopy(_raw()["docs"]["native-contract"])

    def test_seed_document_valid(self) -> None:
        self.assertEqual(validate.validate(self._document(), self.schema, "$"), [])

    def test_unknown_top_level_key_rejected(self) -> None:
        document = self._document()
        document["surprise"] = 1
        problems = validate.validate(document, self.schema, "$")
        self.assertTrue(any("unexpected key" in problem for problem in problems))

    def test_missing_new_required_fields_rejected(self) -> None:
        for field in (
            "generic_agent_aliases",
            "capabilities",
            "nested_subagents",
            "acceptance",
            "lifecycle_evidence",
        ):
            with self.subTest(field=field):
                document = self._document()
                del document[field]
                self.assertTrue(validate.validate(document, self.schema, "$"))

    def test_capability_entry_is_per_entry_closed(self) -> None:
        document = self._document()
        document["capabilities"]["onboarding_trust"]["probe"] = "g1-p3"
        problems = validate.validate(document, self.schema, "$")
        self.assertTrue(any("unexpected key" in problem for problem in problems))

    def test_capability_status_outside_enum_rejected(self) -> None:
        document = self._document()
        document["capabilities"]["onboarding_trust"]["status"] = "passed"
        self.assertTrue(validate.validate(document, self.schema, "$"))

    def test_acceptance_entry_shape_enforced(self) -> None:
        document = self._document()
        document["acceptance"]["U1"]["status"] = "passed"
        self.assertTrue(validate.validate(document, self.schema, "$"))
        document = self._document()
        document["acceptance"]["U1"]["probe"] = "phase2"
        problems = validate.validate(document, self.schema, "$")
        self.assertTrue(any("unexpected key" in problem for problem in problems))

    def test_nested_decision_outside_enum_rejected(self) -> None:
        document = self._document()
        document["nested_subagents"]["decision"] = "assumed"
        self.assertTrue(validate.validate(document, self.schema, "$"))

    def test_nested_key_name_is_const_locked(self) -> None:
        document = self._document()
        document["nested_subagents"]["depth_key"]["name"] = "CLAUDE_CODE_OTHER"
        self.assertTrue(validate.validate(document, self.schema, "$"))

    def test_lifecycle_evidence_shape_enforced(self) -> None:
        document = self._document()
        document["lifecycle_evidence"]["surprise"] = "x"
        problems = validate.validate(document, self.schema, "$")
        self.assertTrue(any("unexpected key" in problem for problem in problems))
        document = self._document()
        document["lifecycle_evidence"]["inspected_version"] = "2.1"
        self.assertTrue(validate.validate(document, self.schema, "$"))


if __name__ == "__main__":
    unittest.main()


class ContractOverrideTests(unittest.TestCase):
    """The layered trust anchor: operator contract override loading."""

    def _override_file(self, case_root: Path, document: dict) -> Path:
        from claude_multi import state as state_mod

        target = case_root / "config" / "native-contract.json"
        state_mod.ensure_private_dir(target.parent)
        state_mod.atomic_write(
            target, strict_json.canonical_file_bytes(document)
        )
        return target

    def _override_doc(self, version: str) -> dict:
        import copy as _copy

        raw = _raw()
        document = _copy.deepcopy(raw["docs"]["native-contract"])
        document["claude"]["validated_version"] = version
        document["claude"]["executable"]["resolved_path"] = (
            f"/home/kotur/.local/share/claude/versions/{version}"
        )
        return document

    def test_newer_override_replaces_the_effective_contract(self) -> None:
        root = _copy_tree(self)
        override = self._override_file(root, self._override_doc("2.1.300"))
        bundle = catalog.load_catalog(root, contract_override=override)
        self.assertEqual(
            bundle.docs["native-contract"]["claude"]["validated_version"], "2.1.300"
        )
        self.assertEqual(bundle.contract_source, "override")
        # The packaged bundle hash is unchanged by the override.
        packaged = catalog.load_catalog(root)
        self.assertEqual(bundle.bundle_sha256, packaged.bundle_sha256)
        self.assertEqual(packaged.contract_source, "packaged")

    def test_stale_override_is_ignored_not_applied(self) -> None:
        root = _copy_tree(self)
        override = self._override_file(root, self._override_doc("2.1.100"))
        bundle = catalog.load_catalog(root, contract_override=override)
        self.assertEqual(bundle.contract_source, "override-ignored-stale")
        self.assertEqual(
            bundle.docs["native-contract"]["claude"]["validated_version"], "2.1.220"
        )

    def test_invalid_override_fails_closed(self) -> None:
        root = _copy_tree(self)
        target = self._override_file(root, {"claude": {"bogus": True}})
        with self.assertRaisesRegex(catalog.CatalogError, "invalid native contract"):
            catalog.load_catalog(root, contract_override=target)


class GatewayManifestConsistencyTests(unittest.TestCase):
    """S2: the machine-readable patch manifest matches the build's patches."""

    def test_manifest_covers_every_patch_the_nix_module_applies(self) -> None:
        import re as _re

        bundle = catalog.load_catalog(CATALOG_ROOT)
        manifest = set(bundle.docs["gateway"]["gateway"]["patches"])
        nix = (CATALOG_ROOT / "claude-multi.nix").read_text(encoding="utf-8")
        applied = set(_re.findall(r"\.\./(cli-proxy-api-[a-z0-9-]+\.patch)", nix))
        self.assertTrue(applied, "no patches found in claude-multi.nix?")
        self.assertEqual(manifest, applied)

    def test_manifest_entries_exist_as_files(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        for name in bundle.docs["gateway"]["gateway"]["patches"]:
            with self.subTest(patch=name):
                self.assertTrue(
                    (CATALOG_ROOT.parent / name).is_file(),
                    f"{name} in the manifest but not next to the module",
                )
