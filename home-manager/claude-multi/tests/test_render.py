"""Tests for the pure deterministic CLIProxyAPI renderer."""

from __future__ import annotations

import copy
import os
import unittest
from pathlib import Path

from claude_multi import catalog, render
from claude_multi.render import RenderError


CATALOG_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = CATALOG_ROOT / "tests" / "goldens" / "render" / "gateway-default.yaml"


def _render(resolve_secret=None, models=None, providers=None):
    bundle = catalog.load_catalog(CATALOG_ROOT)
    return render.render_config(
        bundle.docs["gateway"],
        providers or bundle.docs["providers"]["providers"],
        models or bundle.docs["models"]["models"],
        home=Path("/home/test"),
        gateway_token="a" * 64,
        resolve_secret=resolve_secret or (lambda name: "dummy-kimi-key"),
    )


class GoldenTests(unittest.TestCase):
    def test_default_render_matches_golden(self) -> None:
        result = _render()
        self.assertEqual(result.yaml.encode("utf-8"), GOLDEN.read_bytes())
        self.assertTrue(result.yaml.endswith("\n"))

    def test_render_deterministic(self) -> None:
        self.assertEqual(_render().yaml, _render().yaml)

    def test_v1_static_settings_preserved(self) -> None:
        yaml = _render().yaml
        for needle in (
            'host: "127.0.0.1"',
            "port: 8317",
            "tls:",
            "request-retry: 0",
            "disable-cooling: true",
            'strategy: "fill-first"',
            "session-affinity: true",
            "ws-auth: true",
            "disable-control-panel: true",
        ):
            self.assertIn(needle, yaml)

    def test_retained_and_removed_aliases(self) -> None:
        yaml = _render().yaml
        for retained in (
            "claude-multi-kimi-k3",
            "claude-multi-opus-4-8",
            "gpt-multi-sol-high",
            "gpt-multi-sol-xhigh",
            "gpt-multi-gpt55-high",
        ):
            self.assertIn(retained, yaml)
        for removed in (
            "claude-multi-fable-5",
            "claude-multi-sol-",
            "claude-multi-gpt55",
            "conserve-",
        ):
            self.assertNotIn(removed, yaml)

    def test_kimi_metadata_and_contracts(self) -> None:
        yaml = _render().yaml
        self.assertIn('"output_config.effort": "max"', yaml)
        self.assertIn('- "thinking"', yaml)
        self.assertIn('"reasoning.effort": "high"', yaml)
        self.assertIn('"reasoning.effort": "xhigh"', yaml)
        self.assertIn('owned-by: "moonshot"', yaml)
        self.assertIn("context-length: 1000000", yaml)
        self.assertIn('auth-header: "x-api-key"', yaml)


class ForkRuleTests(unittest.TestCase):
    def test_validated_fork_routes_render_true(self) -> None:
        yaml = _render().yaml
        self.assertIn("fork: true", yaml)
        # codex pool has no fork policy: aliases render fork: false
        self.assertIn("fork: false", yaml)

    def test_mutated_fork_false_renders_false(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        providers = copy.deepcopy(bundle.docs["providers"]["providers"])
        providers["anthropic"]["passthrough_routes"][0]["fork"] = False
        yaml = _render(providers=providers).yaml
        fable_block = yaml.split('alias: "claude-fable-5"')[1].split("- name:")[0]
        self.assertIn("fork: false", fable_block)


class CustomRegistryRenderTests(unittest.TestCase):
    """020: custom providers/models render as ordinary direct routes."""

    def _merged(self):
        from claude_multi import custom

        registry = {
            "version": 1,
            "providers": {
                "my-lab": {
                    "base_url": "https://lab.example.com/apps/anthropic",
                    "auth_kind": "bearer",
                    "secret_env": "MY_LAB_API_KEY",
                }
            },
            "models": {
                "lab-model": {
                    "wire_model": "lab-1",
                    "provider": "my-lab",
                    "context_tokens": 262144,
                    "created_via": "manual",
                }
            },
        }
        bundle = catalog.load_catalog(CATALOG_ROOT)
        return custom.merge_docs(bundle.docs, registry)

    def test_custom_provider_and_model_render(self) -> None:
        docs = self._merged()
        yaml = _render(
            models=docs["models"]["models"],
            providers=docs["providers"]["providers"],
        ).yaml
        self.assertIn('name: "lab-1"', yaml)
        self.assertIn('alias: "custom-lab-model"', yaml)
        self.assertIn("https://lab.example.com/apps/anthropic", yaml)
        self.assertIn("context-length: 262144", yaml)

    def test_rendered_selectors_cover_customs(self) -> None:
        docs = self._merged()
        result = _render(
            models=docs["models"]["models"],
            providers=docs["providers"]["providers"],
        )
        document, _avail, _unavail = render.build_config_document(
            docs["gateway"],
            docs["providers"]["providers"],
            docs["models"]["models"],
            home=Path("/home/test"),
            gateway_token="a" * 64,
            resolve_secret=lambda name: "dummy",
        )
        selectors = render.rendered_selectors(document)
        self.assertIn("custom-lab-model", selectors)
        self.assertIn("claude-multi-kimi-k3", selectors)


class DirectProviderLaneTests(unittest.TestCase):
    def test_every_lane_selector_rendered_for_direct_provider(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        models = copy.deepcopy(bundle.docs["models"]["models"])
        models["kimi-k3"]["lanes"]["turbo"] = {
            "client_selector": "claude-multi-kimi-k3-turbo[1m]",
            "agent_effort": "max",
            "proxy_effort_contract": "output-config-max",
        }
        yaml = _render(models=models).yaml
        self.assertIn('alias: "claude-multi-kimi-k3"', yaml)
        self.assertIn('alias: "claude-multi-kimi-k3-turbo"', yaml)

    def test_kimi_output_single_lane_unchanged(self) -> None:
        yaml = _render().yaml
        self.assertEqual(yaml.count('alias: "claude-multi-kimi-k3"'), 1)

    def test_qwen_1m_client_suffix_is_stripped_from_exact_wire_mapping(self) -> None:
        yaml = _render().yaml
        block = yaml.split('name: "qwen3.8-max"', 1)[1]
        self.assertIn('alias: "claude-multi-qwen38-max"', block)
        self.assertNotIn('alias: "claude-multi-qwen38-max[1m]"', block)
        self.assertIn("context-length: 983616", block)

    def test_glm52_alias_and_max_reasoning_override_rendered(self) -> None:
        yaml = _render().yaml
        block = yaml.split('name: "glm-5.2"', 1)[1]
        self.assertIn('alias: "claude-multi-glm52-max"', block)
        self.assertNotIn('alias: "claude-multi-glm52-max[1m]"', block)
        self.assertIn("context-length: 1000000", block)
        self.assertIn('owned-by: "alibaba"', block)
        override = (
            '- models:\n'
            '        - name: "claude-multi-glm52-max"\n'
            '          protocol: "claude"\n'
            '      params:\n'
            '        reasoning_effort: "max"'
        )
        self.assertIn(override, yaml)


class SecretBoundaryTests(unittest.TestCase):
    def test_missing_secret_omits_provider_atomically(self) -> None:
        def resolver(name: str):
            assert name in ("KIMI_CLAUDE_API_KEY", "QWEN_CLAUDE_API_KEY", "DEEPSEEK_CLAUDE_API_KEY", "OPENROUTER_CLAUDE_API_KEY")
            return None

        result = _render(resolve_secret=resolver)
        self.assertEqual(result.available_providers, ("anthropic", "openai"))
        self.assertEqual(len(result.unavailable), 4)
        self.assertEqual(
            {entry["provider"] for entry in result.unavailable}, {"kimi", "qwen", "deepseek", "openrouter"}
        )
        self.assertNotIn("claude-multi-kimi-k3", result.yaml)
        self.assertNotIn("claude-multi-qwen38-max", result.yaml)
        self.assertNotIn("claude-multi-deepseek-flash", result.yaml)
        self.assertNotIn("claude-multi-deepseek-pro", result.yaml)
        self.assertNotIn("claude-multi-grok46-xhigh", result.yaml)
        self.assertNotIn("output_config.effort", result.yaml)
        self.assertNotIn("reasoning_effort", result.yaml)
        self.assertNotIn('"thinking"', result.yaml)
        self.assertIn("gpt-multi-sol-high", result.yaml)
        self.assertIn("claude-fable-5", result.yaml)

    def test_renderer_never_reads_process_env(self) -> None:
        prior = os.environ.get("KIMI_CLAUDE_API_KEY")
        os.environ["KIMI_CLAUDE_API_KEY"] = "env-value-must-be-ignored"
        try:
            result = _render(resolve_secret=lambda name: None)
        finally:
            # Restore, not delete: a pre-existing value must survive the test.
            if prior is None:
                del os.environ["KIMI_CLAUDE_API_KEY"]
            else:
                os.environ["KIMI_CLAUDE_API_KEY"] = prior
        self.assertNotIn("env-value-must-be-ignored", result.yaml)
        self.assertEqual(result.available_providers, ("anthropic", "openai"))

    def test_resolver_value_used_verbatim(self) -> None:
        result = _render(resolve_secret=lambda name: "resolved-dummy-value")
        self.assertIn('api-key: "resolved-dummy-value"', result.yaml)

    def test_unavailable_providers_matches_renderer_report(self) -> None:
        # The UI helper and the renderer's omission pass are two readers of
        # one rule; this parity pin fails if they ever drift (D46).
        bundle = catalog.load_catalog(CATALOG_ROOT)
        providers = bundle.docs["providers"]["providers"]
        for resolver in (
            lambda name: None,
            lambda name: "dummy",
            lambda name: "dummy" if name == "QWEN_CLAUDE_API_KEY" else None,
        ):
            via_helper = render.unavailable_providers(
                providers, resolve_secret=resolver
            )
            via_render = _render(resolve_secret=resolver).unavailable
            self.assertEqual(via_helper, via_render)


class EmitterTests(unittest.TestCase):
    def test_special_characters_escaped(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        models = copy.deepcopy(bundle.docs["models"]["models"])
        models["kimi-k3"]["display"] = 'Quote "and" : colon # hash ünïcode'
        yaml = _render(models=models).yaml
        self.assertIn(
            'display-name: "Quote \\"and\\" : colon # hash ünïcode"', yaml
        )

    def test_document_root_must_be_mapping(self) -> None:
        with self.assertRaises(RenderError):
            render.emit_yaml([1, 2])

    def test_unknown_payload_contract_rejected(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        providers = copy.deepcopy(bundle.docs["providers"]["providers"])
        providers["kimi"]["payload_contracts"] = ["nonexistent-contract"]
        with self.assertRaisesRegex(RenderError, "unknown payload contract"):
            _render(providers=providers)

    def test_unknown_adapter_has_no_contracts(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        providers = copy.deepcopy(bundle.docs["providers"]["providers"])
        providers["kimi"]["adapter"] = "cliproxy-unknown-v9"
        with self.assertRaisesRegex(RenderError, "unknown payload contract"):
            _render(providers=providers)


if __name__ == "__main__":
    unittest.main()


class BearerAuthAndReasoningContractTests(unittest.TestCase):
    def test_bearer_auth_omits_auth_header_field(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        providers = copy.deepcopy(bundle.docs["providers"]["providers"])
        providers["qwen"] = {
            "display": "Qwen",
            "independence_family": "alibaba",
            "support": "locally-validated-experimental",
            "support_note": "test",
            "adapter": "cliproxy-claude-compatible-v1",
            "transport": {
                "kind": "direct",
                "base_url": "https://token-plan.example.com/apps/anthropic",
                "auth": {"kind": "bearer", "secret_ref": "env:QWEN_CLAUDE_API_KEY"},
            },
            "passthrough_routes": [],
            "payload_contracts": ["reasoning-effort-xhigh"],
        }
        yaml = _render(
            providers=providers,
            resolve_secret=lambda name: "dummy" if name == "QWEN_CLAUDE_API_KEY" else None,
        ).yaml
        self.assertIn("token-plan.example.com", yaml)
        self.assertNotIn("auth-header", yaml)

    def test_header_auth_still_emits_auth_header(self) -> None:
        self.assertIn('auth-header: "x-api-key"', _render().yaml)

    def test_reasoning_effort_contract_rendered_for_declaring_lanes(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        models = copy.deepcopy(bundle.docs["models"]["models"])
        models["kimi-k3"]["lanes"]["max"]["proxy_effort_contract"] = "reasoning-effort-xhigh"
        providers = copy.deepcopy(bundle.docs["providers"]["providers"])
        providers["kimi"]["payload_contracts"] = ["reasoning-effort-xhigh"]
        yaml = _render(models=models, providers=providers).yaml
        self.assertIn("reasoning_effort", yaml)
        self.assertIn("xhigh", yaml)


class DeepSeekProRenderTests(unittest.TestCase):
    """024: Pro uses the stable wire alias and both existing effort contracts."""

    def test_pro_aliases_and_overrides_render(self) -> None:
        yaml = _render().yaml
        block = yaml.split('base-url: "https://api.deepseek.com/anthropic"', 1)[1]
        self.assertEqual(block.count('name: "deepseek-v4-pro"'), 2)
        self.assertIn('alias: "claude-multi-deepseek-pro-high"', block)
        self.assertIn('alias: "claude-multi-deepseek-pro-max"', block)
        self.assertNotIn('deepseek-v4-pro-0813', block)
        self.assertIn('owned-by: "deepseek"', block)
        self.assertIn('context-length: 1000000', block)
        high = (
            '- models:\n'
            '        - name: "claude-multi-deepseek-flash-high"\n'
            '          protocol: "claude"\n'
            '        - name: "claude-multi-deepseek-pro-high"\n'
            '          protocol: "claude"\n'
            '      params:\n'
            '        "output_config.effort": "high"'
        )
        max_ = (
            '- models:\n'
            '        - name: "claude-multi-deepseek-flash-max"\n'
            '          protocol: "claude"\n'
            '        - name: "claude-multi-deepseek-pro-max"\n'
            '          protocol: "claude"\n'
            '      params:\n'
            '        "output_config.effort": "max"'
        )
        self.assertIn(high, yaml)
        self.assertIn(max_, yaml)


class Grok46RenderTests(unittest.TestCase):
    """025: rendered active route is exact Grok 4.6 with xhigh override."""

    def test_exact_46_route_and_no_active_45(self) -> None:
        yaml=_render().yaml
        self.assertIn('name: "x-ai/grok-4.6"',yaml)
        self.assertIn('alias: "claude-multi-grok46-xhigh"',yaml)
        self.assertNotIn('x-ai/grok-4.5',yaml)
        self.assertNotIn('claude-multi-grok45',yaml)
        high=(
            '- models:\n'
            '        - name: "claude-multi-grok46-high"\n'
            '          protocol: "claude"\n'
            '      params:\n'
            '        "output_config.effort": "high"'
        )
        xhigh=(
            '- models:\n'
            '        - name: "claude-multi-grok46-xhigh"\n'
            '          protocol: "claude"\n'
            '      params:\n'
            '        "output_config.effort": "xhigh"'
        )
        self.assertIn(high,yaml)
        self.assertIn(xhigh,yaml)
        self.assertNotIn('reasoning_effort: "xhigh"', yaml.split('payload:',1)[1].split('claude-multi-qwen38-max',1)[0])
