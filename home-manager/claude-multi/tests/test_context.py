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
        self.assertEqual(composition.compute_scalar(_models("sol")), 372000)

    def test_gpt55_only_272k(self) -> None:
        self.assertEqual(composition.compute_scalar(_models("gpt55")), 272000)

    def test_mixed_selector_and_scalar(self) -> None:
        selected = _models("fable", "kimi-k3", "sol", "gpt55")
        self.assertEqual(composition.compute_scalar(selected), 272000)

    def test_selector_1m_never_reduces_scalar(self) -> None:
        # Kimi's validated 208,034 is below GPT-5.5's 272,000 but selector-1m
        # entries are excluded from minimization entirely.
        selected = _models("kimi-k3", "gpt55")
        self.assertEqual(composition.compute_scalar(selected), 272000)

    def test_declared_tokens_never_used(self) -> None:
        selected = _models("sol")
        selected[0]["context"]["declared_tokens"] = 999999
        selected[0]["context"]["validated_tokens"] = 100000
        self.assertEqual(composition.compute_scalar(selected), 100000)

    def test_empty_selection_unset(self) -> None:
        self.assertIsNone(composition.compute_scalar([]))


class ResolvedScalarTests(unittest.TestCase):
    def test_default_seed_scalar(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        self.assertEqual(resolved.scalar_context_tokens, 272000)


if __name__ == "__main__":
    unittest.main()
