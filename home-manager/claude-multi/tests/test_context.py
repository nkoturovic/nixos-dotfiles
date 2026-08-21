"""Tests for the process scalar context calculation."""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

from claude_multi import catalog, composition


CATALOG_ROOT = Path(__file__).resolve().parents[1]


def _models(*ids: str) -> list[dict]:
    bundle = catalog.load_catalog(CATALOG_ROOT)
    return [copy.deepcopy(bundle.docs["models"]["models"][model_id]) for model_id in ids]


class ScalarTests(unittest.TestCase):
    def test_selector_only_unset(self) -> None:
        self.assertIsNone(composition.compute_scalar(_models("fable", "kimi-k3")))

    def test_sol_only_372k(self) -> None:
        self.assertIsNone(composition.compute_scalar(_models("sol")))  # D57: 1M-class

    def test_gpt55_only_272k(self) -> None:
        self.assertEqual(composition.compute_scalar(_models("gpt55")), 258400)

    def test_mixed_selector_and_scalar(self) -> None:
        selected = _models("fable", "kimi-k3", "sol", "gpt55")
        self.assertEqual(composition.compute_scalar(selected), 258400)

    def test_extended_model_without_scalar_never_reduces_scalar(self) -> None:
        # Kimi's validated 208,034 evidence is below GPT-5.5's 272,000 route,
        # but its explicit process scalar is null.
        selected = _models("kimi-k3", "gpt55")
        self.assertEqual(composition.compute_scalar(selected), 258400)

    def test_only_explicit_scalar_tokens_are_used(self) -> None:
        selected = _models("sol")
        selected[0]["context"]["declared_tokens"] = 999999
        selected[0]["context"]["validated_tokens"] = 100000
        selected[0]["context"]["scalar_tokens"] = 123000
        self.assertEqual(composition.compute_scalar(selected), 123000)

    def test_empty_selection_unset(self) -> None:
        self.assertIsNone(composition.compute_scalar([]))


class AutoCompactThresholdTests(unittest.TestCase):
    def test_pinned_client_exact_reactive_thresholds(self) -> None:
        self.assertEqual(composition.auto_compact_trigger(1_000_000), 882_000)
        self.assertEqual(composition.auto_compact_trigger(983_616), 867_254)
        self.assertEqual(composition.auto_compact_trigger(372_000), 316_800)
        self.assertEqual(composition.auto_compact_trigger(272_000), 226_800)

    def test_capacity_too_small_for_fixed_reservations_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            composition.CompositionError, "too small"
        ):
            composition.auto_compact_trigger(33_000)


class ResolvedScalarTests(unittest.TestCase):
    def test_default_seed_scalar(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        self.assertIsNone(resolved.scalar_context_tokens)

    def test_default_mixed_scalar_models_keep_large_process_capacity(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        self.assertEqual(resolved.auto_compact_window_tokens, 1_000_000)
        self.assertEqual(resolved.lead.auto_compact_tokens, 882_000)

    def test_qwen_variant_narrows_extended_process_capacity(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        document = copy.deepcopy(bundle.default_composition)
        document["slots"][1]["model"] = "qwen38"
        document["slots"][1].pop("lane", None)
        document["availability"]["models"]["qwen38"] = "lead+agents"
        document["availability"]["providers"]["qwen"] = "lead+agents"
        resolved = composition.resolve(bundle.docs, document)
        self.assertEqual(resolved.lead.model, "opus5")
        self.assertEqual(resolved.auto_compact_window_tokens, 983_616)
        self.assertEqual(resolved.lead.auto_compact_tokens, 867_254)

    def test_every_supported_lead_has_explicit_context_and_compaction_policy(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        expected = {
            "fable": (1_000_000, 1_000_000, 882_000),
            "opus": (1_000_000, 1_000_000, 882_000),
            "opus5": (1_000_000, 1_000_000, 882_000),
            "kimi-k3": (1_000_000, 1_000_000, 882_000),
            "deepseek-flash": (1_000_000, 1_000_000, 882_000),
            "deepseek-pro": (1_000_000, 1_000_000, 882_000),
            # catalog21 (D61): glm52 keeps its 1M lead policy through the
            # GLM-5.3 promotion — client/provider context unchanged.
            "glm52": (1_000_000, 1_000_000, 882_000),
            "grok46": (500_000, 500_000, 432_000),
            "qwen38": (1_000_000, 983_616, 867_254),
            "sol": (1_000_000, 1_000_000, 882_000),
        }
        for model_id, values in expected.items():
            with self.subTest(model=model_id):
                document = copy.deepcopy(bundle.default_composition)
                document["slots"][0] = {"role": "cm-lead", "model": model_id}
                document["availability"]["models"][model_id] = "lead+agents"
                provider_id = bundle.docs["models"]["models"][model_id]["provider"]
                document["availability"]["providers"][provider_id] = "lead+agents"
                resolved = composition.resolve(bundle.docs, document)
                self.assertEqual(
                    (
                        resolved.lead.client_context_tokens,
                        resolved.lead.provider_context_tokens,
                        resolved.lead.auto_compact_tokens,
                    ),
                    values,
                )


if __name__ == "__main__":
    unittest.main()
