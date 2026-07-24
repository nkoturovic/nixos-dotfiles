"""Source-level packaging and cutover assertions for claude-multi v2.

These assertions need the repository layout (flake.nix, kotur.home.nix), so
they skip with an explicit boundary inside the Nix sandbox test derivation,
which only carries the v2 tree. They run in the source checkout and in the
flake-verify path copies.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


def _repo_root() -> Path | None:
    probe = Path(__file__).resolve()
    for ancestor in probe.parents:
        if (ancestor / "flake.nix").is_file() and (
            ancestor / "home-manager" / "kotur.home.nix"
        ).is_file():
            return ancestor
    return None


REPO_ROOT = _repo_root()
V2_ROOT = Path(__file__).resolve().parents[1]
HOME_NIX = REPO_ROOT / "home-manager" / "kotur.home.nix" if REPO_ROOT else None
FLAKE_NIX = REPO_ROOT / "flake.nix" if REPO_ROOT else None

DELETED_V1 = (
    "home-manager/kotur.bin/claude-multi.sh",
    "home-manager/kotur.bin/claude-multi-proxy.sh",
    "home-manager/claude-multi-plugin",
    "home-manager/claude-multi-settings.json",
    "home-manager/kotur.dotfiles/cli-proxy-api/config.template.yaml",
    "home-manager/tests/claude-multi-launcher-args.py",
    "home-manager/tests/claude-multi-agent-policy.py",
    "home-manager/tests/claude-multi-fake-upstream.py",
    "home-manager/tests/claude-multi-secret-parser.py",
)

REMOVED_IDENTIFIERS = (
    "claude-multi-plugin",
    "claudeMultiPlugin",
    "claudeMultiLauncher",
    "claudeMultiProxy",
    "conserve-opus",
    "conserve-kimi",
    "fable-specialist",
    "sol-explorer",
    "sol-reasoner",
    "sol-engineer",
    "kimi-analyst",
    "kimi-specialist",
    "gpt55-reviewer",
    "opus-reviewer",
    "claude-multi:orchestrator",
)

REMOVED_ALIASES = (
    "claude-multi-fable-5",
    "claude-multi-sol-high",
    "claude-multi-sol-xhigh",
    "claude-multi-gpt55-high",
)


class EntryPointTests(unittest.TestCase):
    def test_gateway_version_matches_launcher_version(self) -> None:
        gateway = V2_ROOT / "bin" / "claude-gateway"
        if not gateway.is_file():
            self.skipTest("boundary: source entrypoints are absent in sandbox package tree")
        expected = json.loads((V2_ROOT / "version.json").read_text())[
            "launcher_version"
        ]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(V2_ROOT / "src")
        result = subprocess.run(
            [sys.executable, str(gateway), "--version"],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"claude-multi {expected}")


    def test_module_entrypoint_runs_after_native_discovery_helpers(self) -> None:
        source = (V2_ROOT / "src" / "claude_multi" / "cli.py").read_text()
        self.assertGreater(
            source.rfind('if __name__ == "__main__":'),
            source.rfind("def _original_cwd_for_adopt"),
        )


class ModuleWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        if REPO_ROOT is None:
            self.skipTest(
                "boundary: repository layout unavailable (Nix sandbox carries "
                "only the v2 tree); run in the source checkout"
            )

    def test_claude_multi_nix_module_shape(self) -> None:
        text = (V2_ROOT / "claude-multi.nix").read_text()
        self.assertIn("cli-proxy-api-loopback-oauth.patch", text)
        self.assertIn("cli-proxy-api-kimi-claude-compat.patch", text)
        self.assertIn("callPackage ./package.nix", text)
        self.assertIn("cliProxyApiBin", text)
        self.assertIn("systemd.user.services.cli-proxy-api", text)
        self.assertIn("/bin/claude-multi-proxy run", text)
        self.assertNotIn("writeShellApplication", text)
        self.assertNotIn("plugin", text.lower().replace("cli-proxy-api-kimi", ""))

    def test_package_nix_installs_gateway_and_management_bins(self) -> None:
        text = (V2_ROOT / "package.nix").read_text()
        for entry in (
            "claude-multi",
            "claude-multi-dev",
            "claude-multi-proxy",
            "claude-gateway",
        ):
            self.assertIn(entry, text)
        for asset in ("catalog", "schemas", "src", "settings.json", "version.json"):
            self.assertIn(asset, text)
        self.assertIn("CLAUDE_MULTI_ASSETS", text)
        self.assertIn("CLAUDE_MULTI_HOOK_COMMAND", text)
        self.assertIn("CLAUDE_MULTI_PROXY_BIN", text)

    def test_home_nix_imports_module_once_and_drops_v1(self) -> None:
        text = HOME_NIX.read_text()
        self.assertEqual(text.count("./claude-multi/claude-multi.nix"), 1)
        for identifier in (
            "claudeMultiPlugin",
            "claudeMultiLauncher",
            "claudeMultiProxy",
            "cliProxyApi =",
            "writeShellApplication",
        ):
            self.assertNotIn(identifier, text)
        self.assertNotIn("systemd.user.services.cli-proxy-api", text)

    def test_flake_checks_wiring(self) -> None:
        text = FLAKE_NIX.read_text()
        self.assertIn("checks", text)
        self.assertIn("x86_64-linux.claude-multi", text)
        self.assertIn("home-manager/claude-multi/tests/default.nix", text)

    def test_patches_preserved_and_present(self) -> None:
        for patch in (
            "cli-proxy-api-loopback-oauth.patch",
            "cli-proxy-api-kimi-claude-compat.patch",
        ):
            self.assertTrue((REPO_ROOT / "home-manager" / patch).is_file())


class CutoverTests(unittest.TestCase):
    def setUp(self) -> None:
        if REPO_ROOT is None:
            self.skipTest(
                "boundary: repository layout unavailable (Nix sandbox carries "
                "only the v2 tree); run in the source checkout"
            )

    def test_v1_artifacts_deleted(self) -> None:
        for relative in DELETED_V1:
            with self.subTest(path=relative):
                self.assertFalse((REPO_ROOT / relative).exists(), f"{relative} still present")

    def test_v2_replacements_present(self) -> None:
        for relative in (
            "home-manager/claude-multi/claude-multi.nix",
            "home-manager/claude-multi/package.nix",
            "home-manager/claude-multi/src/claude_multi/proxy.py",
            "home-manager/claude-multi/bin/claude-multi-proxy",
            "home-manager/claude-multi/bin/claude-gateway",
            "home-manager/claude-multi/README.md",
            "docs/claude-multi-v2.md",
        ):
            with self.subTest(path=relative):
                self.assertTrue((REPO_ROOT / relative).exists(), f"{relative} missing")

    def test_removed_identifiers_absent_from_v2_runtime_and_wiring(self) -> None:
        scanned = [
            V2_ROOT / "catalog",
            V2_ROOT / "schemas",
            V2_ROOT / "src",
            V2_ROOT / "claude-multi.nix",
            V2_ROOT / "package.nix",
            V2_ROOT / "settings.json",
            HOME_NIX,
            FLAKE_NIX,
        ]
        blob = ""
        for path in scanned:
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        blob += child.read_text(errors="replace")
            else:
                blob += path.read_text(errors="replace")
        for identifier in REMOVED_IDENTIFIERS + REMOVED_ALIASES:
            with self.subTest(identifier=identifier):
                self.assertNotIn(identifier, blob)

    def test_retained_aliases_present_in_catalog(self) -> None:
        blob = (V2_ROOT / "catalog" / "models.json").read_text()
        blob += (V2_ROOT / "catalog" / "providers.json").read_text()
        for retained in (
            "claude-fable-5",
            "claude-opus-4-8",
            "claude-multi-kimi-k3",
            "claude-multi-opus-4-8",
            "gpt-multi-sol-high",
            "gpt-multi-sol-xhigh",
            "gpt-multi-gpt55-high",
        ):
            with self.subTest(alias=retained):
                self.assertIn(retained, blob)

    def test_plugin_dir_only_in_blocklist_contexts(self) -> None:
        offenders: list[str] = []
        for child in sorted(V2_ROOT.rglob("*")):
            if not child.is_file() or child.suffix not in (".py", ".nix"):
                continue
            text = child.read_text()
            if "--plugin-dir" not in text:
                continue
            relative = child.relative_to(V2_ROOT)
            allowed = (
                str(relative) == "src/claude_multi/compiler.py"
                or str(relative).startswith("tests/")
            )
            if not allowed:
                offenders.append(str(relative))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
