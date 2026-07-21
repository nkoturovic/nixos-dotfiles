"""Hygiene tests: import boundaries and secret-shape scanning for v2."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


V2_ROOT = Path(__file__).resolve().parents[1]

_SECRET_SHAPES = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
# Explicitly allowed placeholders used by tests and dev-render contracts.
_ALLOWED_MARKERS = (
    "dummy",
    "supersecret-value",
    "test-dummy-value",
    "fake",
    "a\" * 64",
    "a' * 64",
)


class ImportBoundaryTests(unittest.TestCase):
    def test_runtime_modules_never_import_dev(self) -> None:
        src = V2_ROOT / "src" / "claude_multi"
        offenders: list[str] = []
        for module in sorted(src.glob("*.py")):
            if module.name == "dev.py":
                continue
            tree = ast.parse(module.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if "dev" in node.module.split("."):
                        offenders.append(f"{module.name}: from {node.module}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "dev" in alias.name.split("."):
                            offenders.append(f"{module.name}: import {alias.name}")
        self.assertEqual(offenders, [])

    def test_bins_import_only_their_command_module(self) -> None:
        expected = {
            "claude-multi": "claude_multi.cli",
            "claude-multi-dev": "claude_multi.dev",
            "claude-multi-proxy": "claude_multi.proxy",
        }
        for name, module in expected.items():
            text = (V2_ROOT / "bin" / name).read_text()
            with self.subTest(bin=name):
                self.assertIn(module, text)


class SecretScanTests(unittest.TestCase):
    def test_no_secret_shapes_in_source(self) -> None:
        hits: list[str] = []
        for child in sorted(V2_ROOT.rglob("*")):
            if not child.is_file() or child.suffix not in (".py", ".json", ".md", ".nix", ""):
                continue
            text = child.read_text(errors="replace")
            for shape in _SECRET_SHAPES:
                match = shape.search(text)
                if match and not any(marker in text[max(0, match.start() - 120): match.end() + 120] for marker in _ALLOWED_MARKERS):
                    hits.append(f"{child.relative_to(V2_ROOT)}: {match.group(0)[:12]}…")
        self.assertEqual(hits, [])

    def test_no_hardcoded_home_paths_in_src(self) -> None:
        src = V2_ROOT / "src"
        offenders: list[str] = []
        for module in sorted(src.rglob("*.py")):
            text = module.read_text()
            if "/home/kotur" in text:
                offenders.append(str(module.relative_to(V2_ROOT)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
