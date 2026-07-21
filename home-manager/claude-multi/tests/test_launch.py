"""Tests for launch: executable contract, readiness, ordering, execve boundary."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from claude_multi import catalog, compiler, composition, launch, sessions, state, strict_json
from claude_multi.launch import LaunchError


CATALOG_ROOT = Path(__file__).resolve().parents[1]
FIXED_ID = "11111111-1111-4111-8111-111111111111"


class _HealthHandler(BaseHTTPRequestHandler):
    status = 200

    def log_message(self, *_args) -> None:
        return

    def do_GET(self) -> None:
        if self.path != "/healthz":
            self.send_error(404)
            return
        self.send_response(type(self).status)
        self.send_header("Content-Length", "0")
        self.end_headers()


def _serve(status: int = 200):
    handler = type("Handler", (_HealthHandler,), {"status": status})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


class LaunchTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-launch-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(self._cleanup)
        self.bundle = catalog.load_catalog(CATALOG_ROOT)
        self.resolved = composition.resolve(
            self.bundle.docs, self.bundle.default_composition
        )
        self.snapshot = composition.snapshot(self.resolved)
        self.digest = strict_json.bundle_digest(self.snapshot)
        self.store = sessions.SessionStore(
            self.root / "state",
            strict_json.load(CATALOG_ROOT / "schemas" / "session.schema.json"),
        )
        # Fake external Claude executable: resolved path basename == version.
        install = self.root / "install" / "versions" / "2.1.216"
        install.parent.mkdir(parents=True)
        install.write_bytes(b"#!/bin/fake-claude\n")
        install.chmod(0o755)
        link = self.root / "bin" / "claude"
        link.parent.mkdir(parents=True)
        link.symlink_to(install)
        self.native_contract = {
            "claude": {
                "validated_version": "2.1.216",
                "executable": {
                    "configured_path": str(link),
                    "resolved_path": str(install),
                    "sha256": hashlib.sha256(install.read_bytes()).hexdigest(),
                    "inspection": "test fixture",
                    "inspected_at": "2026-07-21",
                },
            },
            "acceptance": {
                "same_launch_agents_and_agent_cm_lead": {
                    "status": "unverified",
                    "probe": "phase2-native-contract",
                },
                "fork_triple_flag": {"status": "unverified", "probe": "phase2-native-contract"},
            },
            "lead_delivery": {
                "mode": "same-launch-agent",
                "status": "pending-probe",
                "contingency": "main-thread-append-system-prompt-file",
                "decision_gate": "P1",
            },
        }
        # Docs copy that consumes the fixture native contract.
        self.docs = dict(self.bundle.docs)
        self.docs["native-contract"] = self.native_contract
        self.home = self.root / "home"
        self.home.mkdir()
        os.chmod(self.home, 0o700)
        token_dir = state.ensure_private_dir(self.home / ".config" / "claude-multi")
        self.token_file = token_dir / "api-key"
        state.atomic_write(self.token_file, b"a" * 64 + b"\n")

    def _cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.root, ignore_errors=True)

    def _compile_result(self):
        return compiler.compile_launch(
            docs=self.docs,
            prompt_bodies=self.bundle.prompt_bodies,
            resolved=self.resolved,
            session_action=compiler.build_fresh(FIXED_ID),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                self.root / "state", self.digest
            ),
        )

    def _record(self):
        return sessions.make_record(
            session_id=FIXED_ID,
            cwd="/project/path",
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.0.0",
            now="2026-07-21T00:00:00Z",
        )


class ResolveClaudeTests(LaunchTestCase):
    def test_resolves_and_validates(self) -> None:
        resolved = launch.resolve_claude(self.native_contract)
        self.assertEqual(resolved.name, "2.1.216")

    def test_hash_mismatch_fails_closed(self) -> None:
        tampered = {
            **self.native_contract,
            "claude": {
                **self.native_contract["claude"],
                "executable": {
                    **self.native_contract["claude"]["executable"],
                    "sha256": "0" * 64,
                },
            },
        }
        with self.assertRaisesRegex(LaunchError, "hash"):
            launch.resolve_claude(tampered)

    def test_resolved_path_drift_fails_closed(self) -> None:
        tampered = {
            **self.native_contract,
            "claude": {
                **self.native_contract["claude"],
                "executable": {
                    **self.native_contract["claude"]["executable"],
                    "resolved_path": "/elsewhere/claude",
                },
            },
        }
        with self.assertRaisesRegex(LaunchError, "drifted"):
            launch.resolve_claude(tampered)

    def test_missing_executable_fails_closed(self) -> None:
        (self.root / "bin" / "claude").unlink()
        (self.root / "install" / "versions" / "2.1.216").unlink()
        with self.assertRaises(LaunchError):
            launch.resolve_claude(self.native_contract)

    def test_hash_called_on_each_resolution(self) -> None:
        from unittest import mock

        with mock.patch.object(
            launch.hashlib, "sha256", wraps=launch.hashlib.sha256
        ) as spy:
            launch.resolve_claude(self.native_contract)
            launch.resolve_claude(self.native_contract)
        self.assertEqual(spy.call_count, 2)


class ReadinessTests(LaunchTestCase):
    def test_readiness_success_returns_token(self) -> None:
        server = _serve(200)
        try:
            gateway = {
                "gateway": {
                    **self.bundle.docs["gateway"]["gateway"],
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                }
            }
            token = launch.check_readiness(gateway, home=self.home)
            self.assertEqual(token, "a" * 64)
        finally:
            server.shutdown()
            server.server_close()

    def test_readiness_health_failure(self) -> None:
        server = _serve(500)
        try:
            gateway = {
                "gateway": {
                    **self.bundle.docs["gateway"]["gateway"],
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                }
            }
            with self.assertRaisesRegex(LaunchError, "status 500"):
                launch.check_readiness(gateway, home=self.home)
        finally:
            server.shutdown()
            server.server_close()

    def test_readiness_bad_token_shape(self) -> None:
        state.atomic_write(self.token_file, b"short\n")
        with self.assertRaisesRegex(LaunchError, "token shape"):
            launch.check_readiness(self.bundle.docs["gateway"], home=self.home)

    def test_readiness_missing_token_file(self) -> None:
        self.token_file.unlink()
        with self.assertRaisesRegex(LaunchError, "key file"):
            launch.check_readiness(self.bundle.docs["gateway"], home=self.home)

    def test_readiness_connection_refused(self) -> None:
        gateway = {
            "gateway": {
                **self.bundle.docs["gateway"]["gateway"],
                "base_url": "http://127.0.0.1:1",  # nothing listens here
            }
        }
        with self.assertRaisesRegex(LaunchError, "health check failed"):
            launch.check_readiness(gateway, home=self.home)

    def test_non_loopback_gateway_rejected(self) -> None:
        gateway = {
            "gateway": {
                **self.bundle.docs["gateway"]["gateway"],
                "base_url": "http://10.0.0.9:8317",
            }
        }
        with self.assertRaisesRegex(LaunchError, "loopback"):
            launch.check_readiness(gateway, home=self.home)


class PerformLaunchTests(LaunchTestCase):
    def _perform(self, result, record, environ=None, **kwargs):
        captured: dict = {}

        def fake_execve(executable, argv, env):
            captured["executable"] = executable
            captured["argv"] = argv
            captured["env"] = env
            return "EXECUTED"

        if environ is None:
            environ = {"PATH": "/usr/bin", "CLAUDE_CODE_SUBAGENT_MODEL": "x",
                       "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "999999",
                       "ANTHROPIC_AUTH_TOKEN": "inherited-must-be-replaced"}
        server = _serve(200)
        try:
            gateway = {
                "gateway": {
                    **self.bundle.docs["gateway"]["gateway"],
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                }
            }
            outcome = launch.perform_launch(
                result,
                record=record,
                store=self.store,
                native_contract=self.native_contract,
                gateway=gateway,
                execve=fake_execve,
                environ=environ,
                home=self.home,
                **kwargs,
            )
        finally:
            server.shutdown()
            server.server_close()
        return outcome, captured

    def test_execve_exact_argv_env_and_replacement(self) -> None:
        result = self._compile_result()
        outcome, captured = self._perform(result, self._record())
        self.assertEqual(outcome, "EXECUTED")
        argv = captured["argv"]
        self.assertEqual(argv[0], captured["executable"])
        self.assertEqual(argv[1:], result.argv)
        env = captured["env"]
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "a" * 64)
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertNotIn("CLAUDE_CODE_SUBAGENT_MODEL", env)
        self.assertEqual(env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "272000")
        # session snapshot persisted before exec and carries no token
        stored = self.store.load(FIXED_ID)
        self.assertNotIn(
            "a" * 64, strict_json.canonical_bytes(stored).decode("utf-8")
        )
        # contingency lead prompt written
        self.assertTrue(result.lead_prompt_path.exists())
        self.assertEqual(
            result.lead_prompt_path.read_bytes(), result.lead_prompt.encode("utf-8")
        )

    def test_readiness_failure_aborts_before_state_write(self) -> None:
        self.token_file.unlink()
        result = self._compile_result()
        with self.assertRaisesRegex(LaunchError, "key file"):
            launch.perform_launch(
                result,
                record=self._record(),
                store=self.store,
                native_contract=self.native_contract,
                gateway=self.bundle.docs["gateway"],
                execve=lambda *_a: self.fail("execve must not run"),
                environ={},
                home=self.home,
            )
        self.assertFalse(self.store.exists(FIXED_ID))
        self.assertFalse(result.lead_prompt_path.exists())

    def test_execve_signal_identity_preserved(self) -> None:
        result = self._compile_result()

        def raising_execve(_executable, _argv, _env):
            raise SystemExit(42)

        server = _serve(200)
        try:
            gateway = {
                "gateway": {
                    **self.bundle.docs["gateway"]["gateway"],
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                }
            }
            with self.assertRaises(SystemExit) as raised:
                launch.perform_launch(
                    result,
                    record=self._record(),
                    store=self.store,
                    native_contract=self.native_contract,
                    gateway=gateway,
                    execve=raising_execve,
                    environ={},
                    home=self.home,
                )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(raised.exception.code, 42)

    def test_successful_launch_updates_pointer(self) -> None:
        result = self._compile_result()
        self._perform(result, self._record())
        self.assertEqual(self.store.last("/project/path"), FIXED_ID)

    def test_execve_oserror_forgets_record_and_clears_pointer(self) -> None:
        result = self._compile_result()

        def failing_execve(_executable, _argv, _env):
            raise OSError("boom")

        server = _serve(200)
        try:
            gateway = {
                "gateway": {
                    **self.bundle.docs["gateway"]["gateway"],
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                }
            }
            with self.assertRaises(OSError) as raised:
                launch.perform_launch(
                    result,
                    record=self._record(),
                    store=self.store,
                    native_contract=self.native_contract,
                    gateway=gateway,
                    execve=failing_execve,
                    environ={},
                    home=self.home,
                )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(str(raised.exception), "boom")
        self.assertFalse(self.store.exists(FIXED_ID))
        self.assertIsNone(self.store.last("/project/path"))

    def test_scalar_absent_unsets_inherited_context_at_exec(self) -> None:
        import copy

        document = copy.deepcopy(self.bundle.default_composition)
        document["slots"] = [
            {"role": "cm-lead", "model": "fable"},
            {"role": "cm-analyst", "model": "kimi-k3", "preferred": True},
            {"role": "cm-implementer", "model": "kimi-k3", "preferred": True},
        ]
        resolved = composition.resolve(self.bundle.docs, document)
        self.assertIsNone(resolved.scalar_context_tokens)
        snap = composition.snapshot(resolved)
        result = compiler.compile_launch(
            docs=self.docs,
            prompt_bodies=self.bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_fresh(FIXED_ID),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                self.root / "state", strict_json.bundle_digest(snap)
            ),
        )
        outcome, captured = self._perform(
            result,
            sessions.make_record(
                session_id=FIXED_ID,
                cwd="/project/path",
                composition_name="default",
                snapshot=snap,
                catalog_version=1,
                catalog_hash="sha256:" + "0" * 64,
                launcher_version="2.0.0",
                now="2026-07-21T00:00:00Z",
            ),
        )
        self.assertEqual(outcome, "EXECUTED")
        self.assertNotIn("CLAUDE_CODE_MAX_CONTEXT_TOKENS", captured["env"])

    def test_scalar_present_overrides_inherited_context_at_exec(self) -> None:
        result = self._compile_result()
        _, captured = self._perform(result, self._record())
        self.assertEqual(captured["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "272000")

    def test_no_provider_path_taken(self) -> None:
        # The readiness probe only ever touches the loopback stub; a gateway
        # config pointing anywhere else fails before any network use.
        gateway = {
            "gateway": {
                **self.bundle.docs["gateway"]["gateway"],
                "base_url": "https://api.example.invalid",
            }
        }
        result = self._compile_result()
        with self.assertRaises(LaunchError):
            launch.perform_launch(
                result,
                record=self._record(),
                store=self.store,
                native_contract=self.native_contract,
                gateway=gateway,
                execve=lambda *_a: self.fail("execve must not run"),
                environ={},
                home=self.home,
            )


if __name__ == "__main__":
    unittest.main()
