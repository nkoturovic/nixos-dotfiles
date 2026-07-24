"""Tests for composition loading and deterministic resolution."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from claude_multi import catalog, composition
from claude_multi.composition import CompositionError


CATALOG_ROOT = Path(__file__).resolve().parents[1]


def _bundle():
    return catalog.load_catalog(CATALOG_ROOT)


def _resolved():
    bundle = _bundle()
    return bundle, composition.resolve(bundle.docs, bundle.default_composition)


class ResolutionTests(unittest.TestCase):
    def test_default_seed_resolution(self) -> None:
        _, resolved = _resolved()
        self.assertEqual(resolved.name, "default")
        self.assertEqual(resolved.lead.model, "fable")
        self.assertEqual(resolved.lead.client_selector, "claude-fable-5[1m]")
        self.assertEqual(resolved.lead.effort, "ultracode")
        self.assertEqual(resolved.lead.env, {})
        self.assertEqual(
            [variant.id for variant in resolved.variants],
            [
                "cm-analyst-sol-high",
                "cm-analyst-kimi-k3-max",
                "cm-implementer-sol-high",
                "cm-implementer-kimi-k3-max",
                "cm-reviewer-sol-xhigh",
                "cm-reviewer-opus-xhigh",
            ],
        )
        preferred = [variant.id for variant in resolved.variants if variant.preferred]
        self.assertEqual(
            preferred,
            ["cm-analyst-sol-high", "cm-implementer-sol-high", "cm-reviewer-sol-xhigh"],
        )

    def test_default_lane_resolution(self) -> None:
        _, resolved = _resolved()
        by_id = {variant.id: variant for variant in resolved.variants}
        self.assertEqual(by_id["cm-analyst-sol-high"].lane, "high")
        self.assertEqual(by_id["cm-analyst-kimi-k3-max"].lane, "max")
        self.assertEqual(by_id["cm-reviewer-opus-xhigh"].lane, "xhigh")
        self.assertEqual(by_id["cm-analyst-sol-high"].agent_effort, "high")
        self.assertEqual(by_id["cm-analyst-kimi-k3-max"].agent_effort, "max")

    def test_variant_metadata(self) -> None:
        _, resolved = _resolved()
        by_id = {variant.id: variant for variant in resolved.variants}
        self.assertEqual(
            by_id["cm-implementer-sol-high"].isolation, "worktree"
        )
        self.assertIsNone(by_id["cm-analyst-sol-high"].isolation)
        self.assertEqual(
            by_id["cm-analyst-sol-high"].routing_hint,
            "Use for routine reconnaissance and bounded reasoning.",
        )
        self.assertEqual(by_id["cm-analyst-sol-high"].family, "openai")
        self.assertEqual(by_id["cm-analyst-kimi-k3-max"].family, "moonshot")
        self.assertEqual(resolved.lead.family, "anthropic")

    def test_explicit_lane_override(self) -> None:
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        document["slots"][1]["lane"] = "xhigh"
        resolved = composition.resolve(bundle.docs, document)
        self.assertEqual(resolved.variants[0].id, "cm-analyst-sol-xhigh")
        self.assertEqual(resolved.variants[0].agent_effort, "xhigh")

    def test_id_derivation_deterministic(self) -> None:
        self.assertEqual(
            composition.variant_id("cm-analyst", "sol", "high"),
            "cm-analyst-sol-high",
        )
        _, first = _resolved()
        _, second = _resolved()
        self.assertEqual(first, second)


class SolReviewerTests(unittest.TestCase):
    def _sol_reviewer_doc(self, lanes=("high", "xhigh")):
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        slots = [
            {"role": "cm-lead", "model": "fable"},
            {"role": "cm-analyst", "model": "sol", "preferred": True},
        ]
        for index, lane in enumerate(lanes):
            slots.append(
                {
                    "role": "cm-reviewer",
                    "model": "sol",
                    "lane": lane,
                    "preferred": index == 0,
                }
            )
        document["slots"] = slots
        return bundle, document

    def test_both_sol_lanes_resolve_as_reviewer_variants(self) -> None:
        bundle, document = self._sol_reviewer_doc()
        resolved = composition.resolve(bundle.docs, document)
        by_id = {variant.id: variant for variant in resolved.variants}
        self.assertEqual(by_id["cm-reviewer-sol-high"].client_selector, "gpt-multi-sol-high")
        self.assertEqual(by_id["cm-reviewer-sol-high"].agent_effort, "high")
        self.assertEqual(by_id["cm-reviewer-sol-xhigh"].client_selector, "gpt-multi-sol-xhigh")
        self.assertEqual(by_id["cm-reviewer-sol-xhigh"].agent_effort, "xhigh")
        self.assertEqual(by_id["cm-reviewer-sol-xhigh"].role, "cm-reviewer")
        self.assertEqual(
            by_id["cm-reviewer-sol-xhigh"].routing_hint,
            "Use for focused review of bounded small-to-medium changes; prefer xhigh for deeper review within bounded scope.",
        )
        self.assertTrue(by_id["cm-reviewer-sol-high"].preferred)
        self.assertFalse(by_id["cm-reviewer-sol-xhigh"].preferred)

    def test_sol_xhigh_as_sole_preferred_reviewer(self) -> None:
        bundle, document = self._sol_reviewer_doc(lanes=("xhigh",))
        resolved = composition.resolve(bundle.docs, document)
        reviewers = [v for v in resolved.variants if v.role == "cm-reviewer"]
        self.assertEqual(len(reviewers), 1)
        self.assertEqual(reviewers[0].id, "cm-reviewer-sol-xhigh")
        self.assertEqual(reviewers[0].client_selector, "gpt-multi-sol-xhigh")
        self.assertEqual(reviewers[0].agent_effort, "xhigh")
        self.assertTrue(reviewers[0].preferred)

    def test_scalar_372k_when_gpt55_absent(self) -> None:
        bundle, document = self._sol_reviewer_doc(lanes=("xhigh",))
        resolved = composition.resolve(bundle.docs, document)
        self.assertNotIn("gpt55", {v.model for v in resolved.variants})
        self.assertEqual(resolved.scalar_context_tokens, 372000)


class KimiReviewerTests(unittest.TestCase):
    def _kimi_reviewer_doc(self):
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        document["slots"] = [
            {"role": "cm-lead", "model": "fable"},
            {"role": "cm-analyst", "model": "sol", "preferred": True},
            {"role": "cm-reviewer", "model": "sol", "lane": "xhigh", "preferred": True},
            {"role": "cm-reviewer", "model": "kimi-k3", "preferred": False},
        ]
        return bundle, document

    def test_kimi_max_reviewer_exact_variant(self) -> None:
        bundle, document = self._kimi_reviewer_doc()
        resolved = composition.resolve(bundle.docs, document)
        by_id = {variant.id: variant for variant in resolved.variants}
        kimi = by_id["cm-reviewer-kimi-k3-max"]
        self.assertEqual(kimi.role, "cm-reviewer")
        self.assertEqual(kimi.client_selector, "claude-multi-kimi-k3[1m]")
        self.assertEqual(kimi.agent_effort, "max")
        self.assertEqual(kimi.lane, "max")
        self.assertEqual(
            kimi.routing_hint,
            "Use for architecture/plan validation, security review, and broad cross-cutting, complex, high-risk, or large-context changes.",
        )
        self.assertFalse(kimi.preferred)

    def test_coexistence_with_preferred_sol_xhigh(self) -> None:
        bundle, document = self._kimi_reviewer_doc()
        resolved = composition.resolve(bundle.docs, document)
        reviewers = {v.id: v for v in resolved.variants if v.role == "cm-reviewer"}
        self.assertEqual(set(reviewers), {"cm-reviewer-sol-xhigh", "cm-reviewer-kimi-k3-max"})
        self.assertTrue(reviewers["cm-reviewer-sol-xhigh"].preferred)
        self.assertFalse(reviewers["cm-reviewer-kimi-k3-max"].preferred)

    def test_scalar_bound_remains_372k(self) -> None:
        bundle, document = self._kimi_reviewer_doc()
        resolved = composition.resolve(bundle.docs, document)
        # Fable and Kimi have no process scalar; only Sol counts.
        self.assertEqual(resolved.scalar_context_tokens, 372000)

    def test_independence_rules_cover_both_reviewer_families(self) -> None:
        from claude_multi import compiler

        bundle, document = self._kimi_reviewer_doc()
        resolved = composition.resolve(bundle.docs, document)
        appendix = compiler.generate_lead_appendix(
            resolved,
            bundle.docs["providers"]["providers"],
            session_id="11111111-1111-4111-8111-111111111111",
            composition_name="default",
        )
        self.assertIn("openai-family variant while a reviewer from moonshot", appendix)
        self.assertIn("moonshot-family variant while a reviewer from openai", appendix)
        self.assertIn("Enabled provider families: anthropic, moonshot, openai.", appendix)


class ConflictAggregationTests(unittest.TestCase):
    def test_multiple_conflicts_all_reported(self) -> None:
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        document["availability"]["providers"]["kimi"] = "off"  # invalidates kimi slots
        document["slots"][1]["preferred"] = True
        document["slots"][2]["preferred"] = True  # two preferred analysts
        with self.assertRaises(CompositionError) as raised:
            composition.resolve(bundle.docs, document)
        message = str(raised.exception)
        self.assertIn("exceeds provider", message)
        self.assertIn("exactly one preferred", message)

    def test_no_silent_deselection_of_unavailable_model(self) -> None:
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        del document["availability"]["models"]["sol"]  # absent means off
        with self.assertRaises(CompositionError) as raised:
            composition.resolve(bundle.docs, document)
        self.assertIn("does not allow use", str(raised.exception))

    def test_unknown_model_reference(self) -> None:
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        document["slots"][1] = {"role": "cm-analyst", "model": "nope", "preferred": True}
        with self.assertRaises(CompositionError) as raised:
            composition.resolve(bundle.docs, document)
        self.assertIn("unknown model", str(raised.exception))

    def test_duplicate_triple_rejected(self) -> None:
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        document["slots"].append({"role": "cm-analyst", "model": "sol"})
        with self.assertRaises(CompositionError) as raised:
            composition.resolve(bundle.docs, document)
        self.assertIn("duplicate slot", str(raised.exception))

    def test_missing_lead_rejected(self) -> None:
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        document["slots"] = [
            slot for slot in document["slots"] if slot["role"] != "cm-lead"
        ]
        with self.assertRaises(CompositionError) as raised:
            composition.resolve(bundle.docs, document)
        self.assertIn("exactly one cm-lead", str(raised.exception))


class WorkflowsTests(unittest.TestCase):
    def _document(self, workflows):
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        if workflows is not None:
            document["workflows"] = workflows
        return bundle, document

    def test_default_is_native(self) -> None:
        _, resolved = _resolved()
        self.assertEqual(resolved.workflows, "native")
        self.assertEqual(resolved.lead.effort, "ultracode")

    def test_explicit_native_keeps_ultracode(self) -> None:
        bundle, document = self._document("native")
        resolved = composition.resolve(bundle.docs, document)
        self.assertEqual(resolved.workflows, "native")
        self.assertEqual(resolved.lead.effort, "ultracode")

    def test_off_maps_lead_ultracode_to_xhigh(self) -> None:
        bundle, document = self._document("off")
        resolved = composition.resolve(bundle.docs, document)
        self.assertEqual(resolved.workflows, "off")
        self.assertEqual(resolved.lead.effort, "xhigh")

    def test_off_keeps_non_ultracode_lead_effort(self) -> None:
        bundle, document = self._document("off")
        document["slots"][0] = {"role": "cm-lead", "model": "kimi-k3"}
        # Kimi's lead block is also ultracode; patch the model doc instead.
        docs = copy.deepcopy(bundle.docs)
        docs["models"]["models"]["kimi-k3"]["lead"]["effort"] = "max"
        resolved = composition.resolve(docs, document)
        self.assertEqual(resolved.lead.effort, "max")

    def test_invalid_workflows_rejected(self) -> None:
        bundle, document = self._document("semi")
        with self.assertRaises(CompositionError) as raised:
            composition.resolve(bundle.docs, document)
        self.assertIn("workflows", str(raised.exception))

    def test_snapshot_records_workflows_only_when_off(self) -> None:
        _, resolved_native = _resolved()
        snap_native = composition.snapshot(resolved_native)
        self.assertNotIn("workflows", snap_native)
        bundle, document = self._document("off")
        snap_off = composition.snapshot(composition.resolve(bundle.docs, document))
        self.assertEqual(snap_off["workflows"], "off")
        self.assertEqual(snap_off["lead"]["effort"], "xhigh")

    def test_workflows_included_in_snapshot_hash(self) -> None:
        from claude_multi import strict_json

        _, resolved_native = _resolved()
        bundle, document = self._document("off")
        resolved_off = composition.resolve(bundle.docs, document)
        self.assertNotEqual(
            strict_json.bundle_digest(composition.snapshot(resolved_native)),
            strict_json.bundle_digest(composition.snapshot(resolved_off)),
        )


class SnapshotTests(unittest.TestCase):
    def test_snapshot_semantics(self) -> None:
        _, resolved = _resolved()
        snap = composition.snapshot(resolved)
        self.assertEqual(snap["lead"]["model"], "fable")
        self.assertEqual(len(snap["variants"]), 6)
        self.assertEqual(snap["scalar_context_tokens"], 372000)
        self.assertEqual(snap["auto_compact_window_tokens"], 1000000)
        self.assertEqual(
            snap["native_agents"],
            {"explore": "replace", "plan": "native", "general_purpose": "off"},
        )
        self.assertEqual(
            snap["availability"]["models"]["gpt55"], "agents"
        )
        # No secrets or prompt bodies leak into the snapshot.
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", str(snap))
        self.assertNotIn("prompt", str(snap).lower().replace("cm-lead", ""))

    def test_snapshot_hash_deterministic(self) -> None:
        from claude_multi import strict_json

        _, first = _resolved()
        _, second = _resolved()
        self.assertEqual(
            strict_json.bundle_digest(composition.snapshot(first)),
            strict_json.bundle_digest(composition.snapshot(second)),
        )


if __name__ == "__main__":
    unittest.main()
