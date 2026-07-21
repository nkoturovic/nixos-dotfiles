"""Tests for native-contract consumption and the skipped real probe."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

from claude_multi import catalog, compiler, composition, strict_json
from claude_multi.compiler import CompilerError


CATALOG_ROOT = Path(__file__).resolve().parents[1]
FIXED_ID = "11111111-1111-4111-8111-111111111111"
OLD_ID = "33333333-3333-4333-8333-333333333333"


def _docs():
    return catalog.load_raw(CATALOG_ROOT)["docs"]


class LeadDeliveryModeTests(unittest.TestCase):
    def test_unverified_record_selects_contingency(self) -> None:
        docs = _docs()
        record = docs["native-contract"]
        self.assertEqual(
            record["acceptance"]["same_launch_agents_and_agent_cm_lead"]["status"],
            "unverified",
        )
        self.assertEqual(compiler.effective_lead_mode(record), compiler.CONTINGENCY_MODE)

    def test_verified_record_selects_same_launch(self) -> None:
        docs = _docs()
        record = dict(docs["native-contract"])
        record = {
            **record,
            "acceptance": {
                **record["acceptance"],
                "same_launch_agents_and_agent_cm_lead": {
                    "status": "verified",
                    "probe": "phase2-native-contract",
                },
            },
            "lead_delivery": {**record["lead_delivery"], "status": "verified"},
        }
        self.assertEqual(compiler.effective_lead_mode(record), compiler.SAME_LAUNCH_MODE)


class ForkContractTests(unittest.TestCase):
    def test_fork_fails_closed_while_unverified(self) -> None:
        docs = _docs()
        with self.assertRaises(CompilerError) as raised:
            compiler.build_fork(docs["native-contract"], OLD_ID, FIXED_ID)
        message = str(raised.exception)
        self.assertIn("unverified", message)
        self.assertIn("sessions link", message)

    def test_fork_compiles_when_verified(self) -> None:
        docs = _docs()
        record = {
            **docs["native-contract"],
            "acceptance": {
                **docs["native-contract"]["acceptance"],
                "fork_triple_flag": {"status": "verified", "probe": "phase2-native-contract"},
            },
        }
        action = compiler.build_fork(record, OLD_ID, FIXED_ID)
        self.assertEqual(action.kind, "fork")
        self.assertEqual(action.resume_id, OLD_ID)
        self.assertEqual(action.new_id, FIXED_ID)

    def test_resume_never_repeats_session_id_flag(self) -> None:
        bundle = catalog.load_catalog(CATALOG_ROOT)
        resolved = composition.resolve(bundle.docs, bundle.default_composition)
        result = compiler.compile_launch(
            docs=bundle.docs,
            prompt_bodies=bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_resume(OLD_ID),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                Path("/state"), strict_json.bundle_digest(composition.snapshot(resolved))
            ),
        )
        self.assertIn("--resume", result.argv)
        self.assertNotIn("--session-id", result.argv)


class PassthroughContractTests(unittest.TestCase):
    def test_structural_long_split_and_equals_rejected(self) -> None:
        for token in ("--model", "--model=x", "--agents={}", "--session-id=abc",
                      "--disallowedTools=Agent(X)", "--disallowed-tools", "--settings=/tmp/s"):
            with self.subTest(token=token):
                with self.assertRaises(CompilerError):
                    compiler.validate_passthrough(
                        [token], lead_mode=compiler.CONTINGENCY_MODE
                    )

    def test_plugin_and_fallback_rejected(self) -> None:
        for token in ("--plugin-url", "--plugin-url=https://x", "--plugin-dir",
                      "--fallback-model", "--fallback-model=x"):
            with self.subTest(token=token):
                with self.assertRaises(CompilerError):
                    compiler.validate_passthrough(
                        [token], lead_mode=compiler.CONTINGENCY_MODE
                    )

    def test_short_forms_rejected(self) -> None:
        for token in ("-c", "-r", "-rABC", "-r=ABC", "-n", "-nNAME"):
            with self.subTest(token=token):
                with self.assertRaises(CompilerError):
                    compiler.validate_passthrough(
                        [token], lead_mode=compiler.CONTINGENCY_MODE
                    )

    def test_contingency_prompt_flags_rejected(self) -> None:
        for token in ("--system-prompt", "--append-system-prompt",
                      "--append-system-prompt-file"):
            with self.subTest(token=token):
                with self.assertRaises(CompilerError):
                    compiler.validate_passthrough(
                        [token], lead_mode=compiler.CONTINGENCY_MODE
                    )

    def test_prompt_flags_allowed_in_same_launch_mode(self) -> None:
        # Not launcher-owned outside contingency; they pass through unchanged.
        args = compiler.validate_passthrough(
            ["--append-system-prompt", "extra"], lead_mode=compiler.SAME_LAUNCH_MODE
        )
        self.assertEqual(args, ["--append-system-prompt", "extra"])

    def test_safe_arguments_pass_byte_order_preserving(self) -> None:
        args = ["--verbose", "--debug=x", "positional", "-p", "print me"]
        self.assertEqual(
            compiler.validate_passthrough(args, lead_mode=compiler.CONTINGENCY_MODE),
            args,
        )

    def test_rejection_gives_ownership_guidance(self) -> None:
        with self.assertRaises(CompilerError) as raised:
            compiler.validate_passthrough(["--model"], lead_mode=compiler.CONTINGENCY_MODE)
        self.assertIn("composition lead/model", str(raised.exception))


class RealProbeGateTests(unittest.TestCase):
    """The real Claude probe stays skipped in this workflow."""

    def test_probe_requires_explicit_gate_and_fixture(self) -> None:
        gated = os.environ.get("CM_RUN_NATIVE_CONTRACT") == "1"
        fixture = os.environ.get("CM_NATIVE_CONTRACT_FIXTURE")
        if not (gated and fixture):
            self.skipTest(
                "native probe disabled: requires CM_RUN_NATIVE_CONTRACT=1 and a "
                "proven loopback fixture; not authorized in this workflow"
            )
        self.fail("probe gate must not be enabled in this workflow")


if __name__ == "__main__":
    unittest.main()
