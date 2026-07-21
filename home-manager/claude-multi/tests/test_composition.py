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
                "cm-reviewer-gpt55-high",
                "cm-reviewer-opus-xhigh",
            ],
        )
        preferred = [variant.id for variant in resolved.variants if variant.preferred]
        self.assertEqual(
            preferred,
            ["cm-analyst-sol-high", "cm-implementer-sol-high", "cm-reviewer-gpt55-high"],
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


class SnapshotTests(unittest.TestCase):
    def test_snapshot_semantics(self) -> None:
        _, resolved = _resolved()
        snap = composition.snapshot(resolved)
        self.assertEqual(snap["lead"]["model"], "fable")
        self.assertEqual(len(snap["variants"]), 6)
        self.assertEqual(snap["scalar_context_tokens"], 272000)
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
