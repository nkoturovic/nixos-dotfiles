"""Tests for launch: executable contract, readiness, ordering, execve boundary."""

from __future__ import annotations

import copy
import hashlib
import os
import stat
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from claude_multi import catalog, compiler, composition, launch, scope, sessions, state, strict_json, transition
from claude_multi.launch import LaunchError


CATALOG_ROOT = Path(__file__).resolve().parents[1]
FIXED_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ID = "22222222-2222-4222-8222-222222222222"


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
        self.project = self.root / "project"
        self.project.mkdir()
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
            "generic_agent_aliases": {
                "values": ["claude"],
                "status": "provisionally-trusted",
                "evidence": "test fixture",
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
                self.root / "state", self.digest, FIXED_ID
            ),
        )

    def _compile_result_durable(self, action=None, session_id=FIXED_ID, passthrough=None):
        return compiler.compile_launch(
            docs=self.docs,
            prompt_bodies=self.bundle.prompt_bodies,
            resolved=self.resolved,
            session_action=action or compiler.build_fresh(session_id),
            passthrough=passthrough or [],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                self.root / "state", self.digest, session_id
            ),
            durable=True,
            scope_dir=scope.scope_dir(self.root / "state", session_id),
            hook_command=str(scope.hook_shim_path(self.root / "state")),
        )

    def _record(self):
        return sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.0.0",
            now="2026-07-21T00:00:00Z",
        )


class ResolveClaudeTests(LaunchTestCase):
    def _contract(self, **executable_overrides):
        return {
            **self.native_contract,
            "claude": {
                **self.native_contract["claude"],
                "executable": {
                    **self.native_contract["claude"]["executable"],
                    **executable_overrides,
                },
            },
        }

    def test_verifies_inspected_artifact_and_reports_status(self) -> None:
        status = launch.resolve_claude(self.native_contract)
        self.assertEqual(status.inspected_path.name, "2.1.216")
        self.assertEqual(status.validated_version, "2.1.216")
        self.assertEqual(
            status.sha256,
            self.native_contract["claude"]["executable"]["sha256"],
        )
        self.assertEqual(
            status.configured_path,
            Path(self.native_contract["claude"]["executable"]["configured_path"]),
        )
        self.assertEqual(status.configured_target, status.inspected_path)
        self.assertTrue(status.configured_matches)
        self.assertIsNone(status.advisory)

    def test_moved_symlink_does_not_block_when_artifact_intact(self) -> None:
        newer = self.root / "install" / "versions" / "2.1.218"
        newer.write_bytes(b"#!/bin/fake-claude-newer\n")
        newer.chmod(0o755)
        link = self.root / "bin" / "claude"
        link.unlink()
        link.symlink_to(newer)
        status = launch.resolve_claude(self.native_contract)
        self.assertEqual(status.inspected_path.name, "2.1.216")
        self.assertFalse(status.configured_matches)
        self.assertEqual(status.configured_target, newer)
        self.assertIn("2.1.218", status.advisory)

    def test_unresolvable_symlink_is_advisory_only(self) -> None:
        (self.root / "bin" / "claude").unlink()
        status = launch.resolve_claude(self.native_contract)
        self.assertTrue(status.inspected_path.is_file())
        self.assertIsNone(status.configured_target)
        self.assertFalse(status.configured_matches)
        self.assertIn("unresolvable", status.advisory)

    def _relink(self, name: str) -> None:
        target = self.root / "install" / "versions" / name
        target.write_bytes(b"#!/bin/fake-claude-other\n")
        target.chmod(0o755)
        link = self.root / "bin" / "claude"
        link.unlink()
        link.symlink_to(target)

    def test_repin_suggestion_on_newer_version(self) -> None:
        self._relink("2.1.218")
        line = launch.repin_suggestion(self.native_contract)
        self.assertIsNotNone(line)
        self.assertIn("2.1.218", line)
        self.assertIn("2.1.216", line)
        self.assertIn("re-pin", line)

    def test_repin_suggestion_none_when_matched(self) -> None:
        self.assertIsNone(launch.repin_suggestion(self.native_contract))

    def test_repin_suggestion_none_for_older_or_unparseable(self) -> None:
        self._relink("2.1.200")
        self.assertIsNone(launch.repin_suggestion(self.native_contract))
        self._relink("claude-nightly")
        self.assertIsNone(launch.repin_suggestion(self.native_contract))

    def test_missing_artifact_fails_closed(self) -> None:
        (self.root / "bin" / "claude").unlink()
        (self.root / "install" / "versions" / "2.1.216").unlink()
        with self.assertRaisesRegex(LaunchError, "missing"):
            launch.resolve_claude(self.native_contract)

    def test_symlinked_artifact_fails_closed(self) -> None:
        artifact = self.root / "install" / "versions" / "2.1.216"
        real = self.root / "install" / "real-2.1.216"
        artifact.rename(real)
        artifact.symlink_to(real)
        with self.assertRaisesRegex(LaunchError, "symlink"):
            launch.resolve_claude(self.native_contract)

    def test_non_regular_artifact_fails_closed(self) -> None:
        artifact = self.root / "install" / "versions" / "2.1.216"
        artifact.unlink()
        artifact.mkdir()
        with self.assertRaisesRegex(LaunchError, "regular file"):
            launch.resolve_claude(self.native_contract)

    def test_wrong_basename_fails_closed(self) -> None:
        other = self.root / "install" / "versions" / "claude"
        other.write_bytes(b"#!/bin/fake-claude\n")
        other.chmod(0o755)
        tampered = self._contract(
            resolved_path=str(other),
            sha256=hashlib.sha256(other.read_bytes()).hexdigest(),
        )
        with self.assertRaisesRegex(LaunchError, "does not match trusted version"):
            launch.resolve_claude(tampered)

    def test_hash_mismatch_fails_closed(self) -> None:
        tampered = self._contract(sha256="0" * 64)
        with self.assertRaisesRegex(LaunchError, "hash"):
            launch.resolve_claude(tampered)

    def test_non_executable_artifact_fails_closed(self) -> None:
        artifact = self.root / "install" / "versions" / "2.1.216"
        artifact.chmod(0o644)
        with self.assertRaisesRegex(LaunchError, "not executable"):
            launch.resolve_claude(self.native_contract)

    def test_hash_called_on_each_resolution(self) -> None:
        from unittest import mock

        with mock.patch.object(
            launch.hashlib, "sha256", wraps=launch.hashlib.sha256
        ) as spy:
            launch.resolve_claude(self.native_contract)
            launch.resolve_claude(self.native_contract)
        self.assertEqual(spy.call_count, 2)


class DoctorBinaryReportTests(LaunchTestCase):
    def test_clean_fixture_reports_version_and_no_problems(self) -> None:
        problems, info = launch.doctor_binary_report(self.native_contract)
        self.assertEqual(problems, [])
        self.assertTrue(any("2.1.216" in line for line in info))
        self.assertTrue(any("resolves to the inspected artifact" in line for line in info))

    def test_advisory_drift_names_newer_unqualified_version(self) -> None:
        newer = self.root / "install" / "versions" / "2.1.218"
        newer.write_bytes(b"#!/bin/fake-claude-newer\n")
        newer.chmod(0o755)
        link = self.root / "bin" / "claude"
        link.unlink()
        link.symlink_to(newer)
        problems, info = launch.doctor_binary_report(self.native_contract)
        self.assertEqual(problems, [])
        self.assertTrue(any("2.1.218" in line for line in info))
        self.assertTrue(any("advisory drift only" in line for line in info))

    def test_failure_matches_launch_failure_exactly(self) -> None:
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
        with self.assertRaises(LaunchError) as raised:
            launch.resolve_claude(tampered)
        problems, info = launch.doctor_binary_report(tampered)
        self.assertEqual(info, [])
        self.assertEqual(len(problems), 1)
        self.assertIn(str(raised.exception), problems[0])

    def test_malformed_contract_is_a_problem_not_a_crash(self) -> None:
        for bad in (
            {},
            {"claude": {}},
            {"claude": {"executable": {}}},
            {"claude": {"validated_version": 1, "executable": None}},
        ):
            with self.subTest(bad=bad):
                problems, info = launch.doctor_binary_report(bad)
                self.assertEqual(info, [])
                self.assertEqual(len(problems), 1)
                self.assertIn("malformed native-contract record", problems[0])


class SharedDaemonTests(LaunchTestCase):
    def _domain(self) -> Path:
        domain = self.root / f"cc-daemon-{os.geteuid()}"
        domain.mkdir()
        return domain

    def test_absent_domain_is_informational(self) -> None:
        status = launch.inspect_shared_daemon(
            domain_dir=self.root / "no-such-daemon", uid=os.geteuid()
        )
        self.assertEqual(status.state, "absent")
        self.assertIsNone(status.pid)
        self.assertIsNone(status.version)

    def test_domain_without_metadata_is_present_without_pid(self) -> None:
        domain = self._domain()
        status = launch.inspect_shared_daemon(domain_dir=domain, uid=os.geteuid())
        self.assertEqual(status.state, "present")
        self.assertIsNone(status.pid)
        self.assertIn("pid not exposed", status.summary)

    def test_metadata_exposes_pid(self) -> None:
        domain = self._domain()
        (domain / "metadata.json").write_bytes(b'{"pid": 4321, "version": "2.1.217"}')
        status = launch.inspect_shared_daemon(domain_dir=domain, uid=os.geteuid())
        self.assertEqual(status.state, "present")
        self.assertEqual(status.pid, 4321)
        # The simplified reader never renders version strings.
        self.assertIsNone(status.version)
        self.assertNotIn("2.1.217", status.summary)

    def test_unparseable_metadata_degrades_to_no_pid(self) -> None:
        domain = self._domain()
        (domain / "metadata.json").write_bytes(b"not json at all")
        status = launch.inspect_shared_daemon(domain_dir=domain, uid=os.geteuid())
        self.assertEqual(status.state, "present")
        self.assertIsNone(status.pid)

    def test_wrong_owner_domain_is_absent(self) -> None:
        domain = self._domain()
        nobody = 65534 if os.geteuid() != 65534 else 65533
        status = launch.inspect_shared_daemon(domain_dir=domain, uid=nobody)
        self.assertEqual(status.state, "absent")
        self.assertIsNone(status.pid)

    def test_symlinked_domain_is_absent(self) -> None:
        real = self._domain()
        link = self.root / "cc-daemon-link"
        link.symlink_to(real)
        status = launch.inspect_shared_daemon(domain_dir=link, uid=os.geteuid())
        self.assertEqual(status.state, "absent")

    def test_ansi_escape_in_metadata_never_rendered(self) -> None:
        domain = self._domain()
        (domain / "metadata.json").write_bytes(
            b'{"pid": 7, "version": "2.1.217\\u001b[2J evil"}'
        )
        status = launch.inspect_shared_daemon(domain_dir=domain, uid=os.geteuid())
        self.assertEqual(status.state, "present")
        self.assertEqual(status.pid, 7)
        self.assertNotIn("\x1b", status.summary)


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
                       "CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS": "x",
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
        self.assertNotIn("CLAUDE_CODE_DISABLE_EXPLORE_PLAN_AGENTS", env)
        self.assertEqual(env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "372000")
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

    def test_binary_failure_aborts_before_readiness_and_state(self) -> None:
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
        result = self._compile_result()
        with self.assertRaisesRegex(LaunchError, "hash"):
            launch.perform_launch(
                result,
                record=self._record(),
                store=self.store,
                native_contract=tampered,
                gateway=self.bundle.docs["gateway"],
                readiness=lambda *a, **k: self.fail("readiness must not run"),
                execve=lambda *_a: self.fail("execve must not run"),
                environ={},
                home=self.home,
            )
        self.assertFalse(self.store.exists(FIXED_ID))
        self.assertFalse(result.lead_prompt_path.exists())
        self.assertIsNone(self.store.last(str(self.project)))

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
        self.assertEqual(self.store.last(str(self.project)), FIXED_ID)

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
        self.assertIsNone(self.store.last(str(self.project)))

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
                self.root / "state", strict_json.bundle_digest(snap), FIXED_ID
            ),
        )
        outcome, captured = self._perform(
            result,
            sessions.make_record(
                session_id=FIXED_ID,
                cwd=str(self.project),
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
        self.assertEqual(captured["env"]["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "372000")

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

    def test_overlapping_same_composition_launches_write_distinct_sentinels(self) -> None:
        other_id = "22222222-2222-4222-8222-222222222222"
        result_a = self._compile_result()
        result_b = compiler.compile_launch(
            docs=self.docs,
            prompt_bodies=self.bundle.prompt_bodies,
            resolved=self.resolved,
            session_action=compiler.build_fresh(other_id),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                self.root / "state", self.digest, other_id
            ),
        )
        self.assertNotEqual(result_a.lead_prompt_path, result_b.lead_prompt_path)
        record_b = sessions.make_record(
            session_id=other_id,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.0.0",
            now="2026-07-21T00:00:00Z",
        )
        self._perform(result_a, self._record())
        self._perform(result_b, record_b)
        # Neither launch overwrote the other's exact sentinel.
        self.assertTrue(result_a.lead_prompt_path.exists())
        self.assertTrue(result_b.lead_prompt_path.exists())
        self.assertIn(FIXED_ID, result_a.lead_prompt_path.read_text())
        self.assertIn(other_id, result_b.lead_prompt_path.read_text())
        self.assertNotIn(other_id, result_a.lead_prompt_path.read_text())

    def test_transition_new_digest_never_prunes_previous_sentinel(self) -> None:
        import copy

        result_a = self._compile_result()
        self._perform(result_a, self._record())
        previous = result_a.lead_prompt_path
        self.assertTrue(previous.exists())
        # Resume the same UUID under a changed composition (new digest).
        document = copy.deepcopy(self.bundle.default_composition)
        document["slots"] = [
            {"role": "cm-lead", "model": "fable"},
            {"role": "cm-analyst", "model": "kimi-k3", "preferred": True},
            {"role": "cm-implementer", "model": "kimi-k3", "preferred": True},
        ]
        resolved_b = composition.resolve(self.bundle.docs, document)
        snap_b = composition.snapshot(resolved_b)
        result_b = compiler.compile_launch(
            docs=self.docs,
            prompt_bodies=self.bundle.prompt_bodies,
            resolved=resolved_b,
            session_action=compiler.build_resume(FIXED_ID),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                self.root / "state", strict_json.bundle_digest(snap_b), FIXED_ID
            ),
        )
        self.assertNotEqual(previous, result_b.lead_prompt_path)
        self.assertIn(FIXED_ID, result_b.lead_prompt_path.name)
        self._perform(result_b, self._record())
        self.assertTrue(previous.exists())
        self.assertTrue(result_b.lead_prompt_path.exists())


    def test_missing_original_cwd_fails_before_state_commit(self) -> None:
        result = self._compile_result()
        record = self._record()
        record["cwd"] = str(self.root / "missing-project")
        with self.assertRaisesRegex(LaunchError, "original project directory"):
            self._perform(result, record)
        self.assertFalse(self.store.exists(FIXED_ID))
        self.assertIsNone(self.store.last(record["cwd"]))
        self.assertFalse(result.lead_prompt_path.exists())


class DurablePerformLaunchTests(LaunchTestCase):
    def _perform(self, result, record, environ=None, execve=None, **kwargs):
        captured: dict = {}

        def fake_execve(executable, argv, env):
            captured["executable"] = executable
            captured["argv"] = argv
            captured["env"] = env
            return "EXECUTED"

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
                execve=execve or fake_execve,
                environ=environ if environ is not None else {},
                home=self.home,
                **kwargs,
            )
        finally:
            server.shutdown()
            server.server_close()
        return outcome, captured

    def _scope_dir(self, session_id=FIXED_ID):
        return scope.scope_dir(self.root / "state", session_id)

    def test_durable_launch_writes_scope_before_record_and_execs(self) -> None:
        result = self._compile_result_durable()
        outcome, captured = self._perform(result, self._record())
        self.assertEqual(outcome, "EXECUTED")
        argv = captured["argv"]
        self.assertEqual(argv[1:], result.argv)
        self.assertNotIn("--agents", argv)
        self.assertNotIn("--disallowedTools", argv)
        live = self._scope_dir()
        self.assertTrue((live / "settings.json").is_file())
        self.assertEqual(
            (live / "settings.json").read_bytes(),
            strict_json.canonical_file_bytes(result.scope_plan.settings),
        )
        agent = live / ".claude" / "agents" / "cm-analyst-sol-high.md"
        self.assertTrue(agent.is_file())
        self.assertEqual(stat.S_IMODE(os.lstat(agent).st_mode), 0o600)
        self.assertTrue(self.store.exists(FIXED_ID))
        self.assertEqual(self.store.last(str(self.project)), FIXED_ID)

    def test_fresh_launch_refuses_to_overwrite_existing_record(self) -> None:
        prior = self._record()
        self.store.save(prior)
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        result = self._compile_result_durable()
        with self.assertRaisesRegex(LaunchError, "already has a record"):
            self._perform(result, self._record())
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)
        self.assertFalse(self._scope_dir().exists())
        self.assertFalse(result.lead_prompt_path.exists())

    def test_resume_requires_preexisting_record_before_scope_write(self) -> None:
        result = self._compile_result_durable(action=compiler.build_resume(FIXED_ID))
        with self.assertRaisesRegex(LaunchError, "has no managed record"):
            self._perform(result, self._record())
        self.assertFalse(self._scope_dir().exists())
        self.assertFalse(result.lead_prompt_path.exists())

    def test_compiled_action_session_must_match_record(self) -> None:
        result = self._compile_result_durable(
            action=compiler.build_resume("22222222-2222-4222-8222-222222222222")
        )
        with self.assertRaisesRegex(LaunchError, "compiled session action targets"):
            self._perform(result, self._record())
        self.assertFalse(self._scope_dir().exists())
        self.assertFalse(result.lead_prompt_path.exists())

    def test_scope_dir_mismatch_fails_closed(self) -> None:
        result = compiler.compile_launch(
            docs=self.docs,
            prompt_bodies=self.bundle.prompt_bodies,
            resolved=self.resolved,
            session_action=compiler.build_fresh(FIXED_ID),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                self.root / "state", self.digest, FIXED_ID
            ),
            durable=True,
            scope_dir=self.root / "elsewhere" / "scopes" / FIXED_ID,
            hook_command=str(scope.hook_shim_path(self.root / "state")),
        )
        with self.assertRaisesRegex(LaunchError, "does not match"):
            self._perform(result, self._record())
        self.assertFalse(self.store.exists(FIXED_ID))

    def test_compiled_action_and_record_session_must_match(self) -> None:
        result = self._compile_result_durable(session_id=OTHER_ID)
        with self.assertRaisesRegex(LaunchError, "compiled session action targets"):
            self._perform(result, self._record())
        self.assertFalse(result.lead_prompt_path.exists())
        self.assertFalse(self.store.exists(FIXED_ID))
        self.assertFalse(self._scope_dir(OTHER_ID).exists())

    def test_fresh_launch_refuses_existing_record_before_scope_write(self) -> None:
        self.store.save(self._record())
        prior = self.store.read_record_bytes(FIXED_ID)
        result = self._compile_result_durable()
        with self.assertRaisesRegex(LaunchError, "already has a record"):
            self._perform(result, self._record())
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior)
        self.assertFalse(result.lead_prompt_path.exists())
        self.assertFalse(self._scope_dir().exists())

    def test_resume_requires_existing_record_before_scope_write(self) -> None:
        result = self._compile_result_durable(
            action=compiler.build_resume(FIXED_ID)
        )
        with self.assertRaisesRegex(LaunchError, "has no managed record"):
            self._perform(result, self._record())
        self.assertFalse(result.lead_prompt_path.exists())
        self.assertFalse(self._scope_dir().exists())

    def test_collision_gate_fails_before_any_state_write(self) -> None:
        project = self.root / "colliding"
        agents = project / ".claude" / "agents"
        agents.mkdir(parents=True)
        (project / ".git").mkdir()
        offender = agents / "cm-analyst-sol-high.md"
        offender.write_text("---\nname: cm-analyst-sol-high\n---\n\nshadow\n")
        record = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            now="2026-07-21T00:00:00Z",
        )
        result = self._compile_result_durable()
        with self.assertRaisesRegex(LaunchError, "cm-analyst-sol-high"):
            self._perform(result, record)
        self.assertIn(str(offender), str(offender))
        self.assertFalse(self.store.exists(FIXED_ID))
        self.assertFalse(self._scope_dir().exists())
        self.assertFalse(result.lead_prompt_path.exists())

    def test_non_colliding_project_agents_do_not_block(self) -> None:
        project = self.root / "fine"
        agents = project / ".claude" / "agents"
        agents.mkdir(parents=True)
        (project / ".git").mkdir()
        (agents / "cm-unrelated.md").write_text("---\nname: cm-unrelated\n---\n\nok\n")
        (agents / "helper.md").write_text("---\nname: helper\n---\n\nok\n")
        record = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            now="2026-07-21T00:00:00Z",
        )
        outcome, _ = self._perform(self._compile_result_durable(), record)
        self.assertEqual(outcome, "EXECUTED")

    def test_execve_oserror_fresh_forgets_record_pointer_and_scope(self) -> None:
        result = self._compile_result_durable()

        def failing_execve(_executable, _argv, _env):
            raise OSError("boom")

        with self.assertRaises(OSError):
            self._perform(result, self._record(), execve=failing_execve)
        self.assertFalse(self.store.exists(FIXED_ID))
        self.assertIsNone(self.store.last(str(self.project)))
        self.assertFalse(self._scope_dir().exists())

    def test_execve_oserror_resume_restores_record_and_pointer_preserves_scope(self) -> None:
        prior = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            now="2026-07-20T00:00:00Z",
        )
        self.store.save(prior)
        self.store.update_last(str(self.project), FIXED_ID)
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        # Audit L4: a durable resume's failed exec must NOT delete the valid
        # scope; it converges to the record-authoritative compile.
        expected = transition._expected_plan(
            self.store.load(FIXED_ID), self.bundle, state_root=self.store.root
        )
        newer = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            now="2026-07-21T00:00:00Z",
        )
        result = self._compile_result_durable(action=compiler.build_resume(FIXED_ID))

        def failing_execve(_executable, _argv, _env):
            raise OSError("boom")

        with self.assertRaises(OSError):
            self._perform(
                result, newer, execve=failing_execve, trusted=self.bundle
            )
        # Exact pre-read bytes restored; pointer preserved; scope kept and
        # equal to the record-authoritative compile (not deleted).
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)
        self.assertEqual(self.store.last(str(self.project)), FIXED_ID)
        live = self._scope_dir()
        self.assertTrue(live.is_dir())
        self.assertEqual(
            (live / "settings.json").read_bytes(),
            strict_json.canonical_file_bytes(expected.settings),
        )
        for relpath, data in expected.agent_files.items():
            self.assertEqual(live.joinpath(*relpath.split("/")).read_bytes(), data)

    def test_execve_oserror_resume_restores_different_prior_pointer(self) -> None:
        prior = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            now="2026-07-20T00:00:00Z",
        )
        self.store.save(prior)
        other_id = "22222222-2222-4222-8222-222222222222"
        self.store.update_last(str(self.project), other_id)
        pointer_bytes = self.store.read_pointer_bytes(str(self.project))
        result = self._compile_result_durable(action=compiler.build_resume(FIXED_ID))

        def failing_execve(_executable, _argv, _env):
            raise OSError("boom")

        with self.assertRaises(OSError):
            self._perform(result, prior, execve=failing_execve)
        self.assertEqual(self.store.last(str(self.project)), other_id)
        self.assertEqual(
            self.store.read_pointer_bytes(str(self.project)), pointer_bytes
        )

    def test_execve_oserror_resume_restores_absent_prior_pointer(self) -> None:
        prior = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            now="2026-07-20T00:00:00Z",
        )
        self.store.save(prior)
        result = self._compile_result_durable(action=compiler.build_resume(FIXED_ID))

        def failing_execve(_executable, _argv, _env):
            raise OSError("boom")

        with self.assertRaises(OSError):
            self._perform(result, prior, execve=failing_execve)
        self.assertIsNone(self.store.last(str(self.project)))
        self.assertIsNone(self.store.read_pointer_bytes(str(self.project)))

    def test_execve_oserror_legacy_upgrade_restores_legacy_record_bytes(self) -> None:
        # A v1 (legacy) record being resumed into durable mode: the exact
        # pre-read legacy bytes come back, untouched.
        legacy_record = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.0.0",
            now="2026-07-19T00:00:00Z",
        )
        legacy_record["version"] = 1
        legacy_record["session_id"] = legacy_record.pop("managed_id")
        for key in (
            "runtime_session_id",
            "runtime_aliases",
            "session_type",
            "identity_state",
            "last_event_source",
            "last_seen_at",
            "pending_forks",
            "launch_epoch",
            "migrated_from_version",
            "mode",
            "scope_generation",
            "workflows",
        ):
            del legacy_record[key]
        self.store.save(legacy_record)
        legacy_bytes = self.store.read_record_bytes(FIXED_ID)
        upgraded = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            now="2026-07-21T00:00:00Z",
        )
        result = self._compile_result_durable(action=compiler.build_resume(FIXED_ID))

        def failing_execve(_executable, _argv, _env):
            raise OSError("boom")

        with self.assertRaises(OSError):
            self._perform(result, upgraded, execve=failing_execve)
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), legacy_bytes)
        loaded = self.store.load(FIXED_ID)
        self.assertEqual(loaded["version"], 3)
        self.assertEqual(loaded["migrated_from_version"], 1)
        self.assertEqual(loaded["mode"], "legacy")
        self.assertFalse(self._scope_dir().exists())

    def test_execve_oserror_legacy_resume_preserves_record_and_pointer(self) -> None:
        prior = self._record()
        self.store.save(prior)
        self.store.update_last(str(self.project), FIXED_ID)
        result = compiler.compile_launch(
            docs=self.docs,
            prompt_bodies=self.bundle.prompt_bodies,
            resolved=self.resolved,
            session_action=compiler.build_resume(FIXED_ID),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                self.root / "state", self.digest, FIXED_ID
            ),
        )

        def failing_execve(_executable, _argv, _env):
            raise OSError("boom")

        with self.assertRaises(OSError):
            self._perform(result, self._record(), execve=failing_execve)
        self.assertTrue(self.store.exists(FIXED_ID))
        self.assertEqual(self.store.last(str(self.project)), FIXED_ID)


    def test_ordinary_gateway_launch_writes_hook_scope_without_prompt(self) -> None:
        result = compiler.compile_direct_launch(
            docs=self.docs,
            session_action=compiler.build_fresh(FIXED_ID),
            model_id="qwen38",
            passthrough=[],
            scope_dir=scope.scope_dir(self.root / "state", FIXED_ID),
            hook_command=str(scope.hook_shim_path(self.root / "state")),
            state_root=self.root / "state",
        )
        record = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=str(self.project),
            model="qwen38",
            context_profile="large",
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
        )
        outcome, captured = self._perform(result, record)
        self.assertEqual(outcome, "EXECUTED")
        self.assertEqual(captured["argv"][1:3], ["--session-id", FIXED_ID])
        self.assertFalse(result.lead_prompt_path.exists())
        live = self._scope_dir()
        self.assertTrue((live / "settings.json").is_file())
        self.assertFalse((live / ".claude" / "agents").exists())
        self.assertEqual(self.store.load(FIXED_ID)["session_type"], sessions.SESSION_TYPE_ORDINARY)


class LifecycleCleanupTests(LaunchTestCase):
    """Audit L1/L2/L4: lock-placed guards, CAS cleanup, scope convergence."""

    def _durable_prior(self, now="2026-07-20T00:00:00Z", snapshot=None) -> dict:
        record = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=snapshot if snapshot is not None else self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            now=now,
        )
        self.store.save(record)
        return record

    def _override(self):
        import copy

        document = copy.deepcopy(self.bundle.default_composition)
        document["slots"].append(
            {"role": "cm-analyst", "model": "sol", "lane": "xhigh", "preferred": False}
        )
        resolved = composition.resolve(self.bundle.docs, document)
        return resolved, composition.snapshot(resolved)

    def _compile_durable_resume(
        self, resolved, snapshot, *, launch_epoch: int = 0
    ) -> object:
        return compiler.compile_launch(
            docs=self.docs,
            prompt_bodies=self.bundle.prompt_bodies,
            resolved=resolved,
            session_action=compiler.build_resume(FIXED_ID),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                self.root / "state", strict_json.bundle_digest(snapshot), FIXED_ID
            ),
            durable=True,
            scope_dir=scope.scope_dir(self.root / "state", FIXED_ID),
            hook_command=str(scope.hook_shim_path(self.root / "state")),
            launch_epoch=launch_epoch,
        )

    def _scope_dir(self, session_id=FIXED_ID):
        return scope.scope_dir(self.root / "state", session_id)

    def _perform(self, result, record, execve, **kwargs):
        server = _serve(200)
        try:
            gateway = {
                "gateway": {
                    **self.bundle.docs["gateway"]["gateway"],
                    "base_url": f"http://127.0.0.1:{server.server_port}",
                }
            }
            return launch.perform_launch(
                result,
                record=record,
                store=self.store,
                native_contract=self.native_contract,
                gateway=gateway,
                execve=execve,
                environ={},
                home=self.home,
                **kwargs,
            )
        finally:
            server.shutdown()
            server.server_close()

    def test_guards_and_state_mutation_wait_for_lifecycle_lock(self) -> None:
        # L1: the resume guard and every state mutation run after the lock is
        # acquired; a held lifecycle lock blocks the whole critical section.
        self._durable_prior()
        result = self._compile_durable_resume(self.resolved, self.snapshot)
        record = self._durable_prior(now="2026-07-21T00:00:00Z")
        lock = self.store.lifecycle_lock(FIXED_ID)
        self.assertTrue(lock.acquire(blocking=False))
        done: list[str] = []
        errors: list[BaseException] = []

        def worker() -> None:
            try:
                self._perform(result, record, lambda *_a: done.append("EXECUTED"))
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=0.5)
        try:
            self.assertTrue(thread.is_alive())
            self.assertEqual(done, [])
            self.assertFalse(self._scope_dir().exists())
        finally:
            lock.release()
            thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        if errors:
            raise errors[0]
        self.assertEqual(done, ["EXECUTED"])
        self.assertTrue(self._scope_dir().is_dir())

    def test_runtime_change_after_prepare_aborts_without_mutation(self) -> None:
        prepared_record = self._durable_prior()
        result = self._compile_durable_resume(self.resolved, self.snapshot)
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="compact",
            cwd=str(self.project),
            now="2026-07-22T00:00:00Z",
        )
        newer_bytes = self.store.read_record_bytes(FIXED_ID)
        with self.assertRaisesRegex(launch.LaunchError, "now targets runtime"):
            self._perform(result, prepared_record, lambda *_args: "EXECUTED")
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), newer_bytes)
        self.assertFalse(self._scope_dir().exists())
        self.assertIsNone(self.store.last(str(self.project)))

    def test_pending_fork_arriving_after_prepare_blocks_launch(self) -> None:
        prepared_record = self._durable_prior()
        result = self._compile_durable_resume(self.resolved, self.snapshot)
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd=str(self.project),
            now="2026-07-22T00:00:00Z",
        )
        newer_bytes = self.store.read_record_bytes(FIXED_ID)
        with self.assertRaisesRegex(launch.LaunchError, "unresolved native fork"):
            self._perform(result, prepared_record, lambda *_args: "EXECUTED")
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), newer_bytes)
        self.assertFalse(self._scope_dir().exists())

    def test_guard_failure_preserves_prior_scope_and_pointer(self) -> None:
        # Regression: a resume failing a pre-mutation guard must NOT roll
        # anything back — this attempt never touched the record, the live
        # scope, or the per-CWD pointer (previously the rollback deleted the
        # pointer and rewrote the valid scope).
        prepared_record = self._durable_prior()
        prior_plan = transition._expected_plan(
            self.store.load(FIXED_ID), self.bundle, state_root=self.store.root
        )
        scope.write_scope(self.store.root, FIXED_ID, prior_plan)
        scope_bytes = (self._scope_dir() / "settings.json").read_bytes()
        self.assertTrue(self.store.update_last(str(self.project), FIXED_ID))
        pointer_bytes = self.store.read_pointer_bytes(str(self.project))
        self.assertIsNotNone(pointer_bytes)
        result = self._compile_durable_resume(self.resolved, self.snapshot)
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="fork",
            cwd=str(self.project),
            now="2026-07-22T00:00:00Z",
        )
        with self.assertRaisesRegex(launch.LaunchError, "unresolved native fork"):
            self._perform(result, prepared_record, lambda *_args: "EXECUTED")
        self.assertEqual((self._scope_dir() / "settings.json").read_bytes(), scope_bytes)
        self.assertEqual(self.store.read_pointer_bytes(str(self.project)), pointer_bytes)
        self.assertEqual(self.store.last(str(self.project)), FIXED_ID)

    def _precommitted_prior(self, epoch: int, token: str) -> dict:
        record = self._durable_prior()
        updated = {
            **self.store.load(FIXED_ID),
            "launch_epoch": epoch,
            "mutation_token": token,
        }
        self.store.save(updated)
        return updated

    def test_precommitted_epoch_guard_accepts_transition_committed_record(self) -> None:
        # The transition relaunch path (precommitted=True): the record was
        # already advanced by the transition engine, so the prepared record's
        # epoch must equal the on-disk epoch — not epoch+1.
        token = "33333333-3333-4333-8333-333333333333"
        prior = self._precommitted_prior(epoch=2, token=token)
        result = self._compile_durable_resume(
            self.resolved, self.snapshot, launch_epoch=2
        )
        prepared_record = {**prior, "mutation_token": sessions.new_mutation_token()}
        outcome = self._perform(
            result,
            prepared_record,
            lambda *_args: "EXECUTED",
            expected_launch_epoch=2,
            expected_mutation_token=token,
            precommitted=True,
        )
        self.assertEqual(outcome, "EXECUTED")

    def test_precommitted_epoch_guard_aborts_when_hook_advanced_record(self) -> None:
        # A lifecycle observation landing between the transition commit and
        # the relaunch must abort the launch without touching state.
        token = "33333333-3333-4333-8333-333333333333"
        prior = self._precommitted_prior(epoch=2, token=token)
        result = self._compile_durable_resume(
            self.resolved, self.snapshot, launch_epoch=2
        )
        prepared_record = {**prior, "mutation_token": sessions.new_mutation_token()}
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=OTHER_ID,
            source="compact",
            cwd=str(self.project),
            launch_epoch=3,
            now="2026-07-22T00:00:00Z",
        )
        newer_bytes = self.store.read_record_bytes(FIXED_ID)
        with self.assertRaisesRegex(
            launch.LaunchError, "authority changed after preparation"
        ):
            self._perform(
                result,
                prepared_record,
                lambda *_args: "EXECUTED",
                expected_launch_epoch=2,
                expected_mutation_token=token,
                precommitted=True,
            )
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), newer_bytes)
        self.assertFalse(self._scope_dir().exists())

    def test_benign_lifecycle_update_is_carried_into_launch_commit(self) -> None:
        prepared_record = self._durable_prior()
        result = self._compile_durable_resume(self.resolved, self.snapshot)
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            source="compact",
            cwd=str(self.project),
            now="2026-07-22T00:00:00Z",
        )
        self.store.record_session_end(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            reason="other",
            now="2026-07-22T00:00:01Z",
        )
        outcome = self._perform(result, prepared_record, lambda *_args: "EXECUTED")
        self.assertEqual(outcome, "EXECUTED")
        stored = self.store.load(FIXED_ID)
        self.assertEqual(stored["last_event_source"], "end")
        self.assertEqual(stored["last_end_reason"], "other")
        self.assertEqual(stored["last_seen_at"], "2026-07-22T00:00:01Z")
        self.assertEqual(stored["identity_state"], sessions.IDENTITY_AUTHORITATIVE)

    def test_v2_same_generation_resume_heals_snapshot_and_persists_v3(self) -> None:
        current = self._durable_prior()
        old_snapshot = copy.deepcopy(self.snapshot)
        for key in (
            "client_context_tokens",
            "provider_context_tokens",
            "auto_compact_tokens",
        ):
            old_snapshot["lead"].pop(key, None)
        old_snapshot.pop("auto_compact_window_tokens", None)
        raw_v2 = {
            **current,
            "version": 2,
            "session_id": current["managed_id"],
            "snapshot": old_snapshot,
            "composition_hash": strict_json.bundle_digest(old_snapshot),
        }
        raw_v2.pop("managed_id")
        for key in (
            "runtime_session_id",
            "runtime_aliases",
            "session_type",
            "identity_state",
            "last_event_source",
            "last_seen_at",
            "pending_forks",
            "launch_epoch",
            "migrated_from_version",
            "mutation_token",
        ):
            raw_v2.pop(key, None)
        state.atomic_write(
            self.store._record_path(FIXED_ID),
            strict_json.canonical_file_bytes(raw_v2),
        )
        source = self.store.load(FIXED_ID)
        self.assertEqual(source["migrated_from_version"], 2)
        target = {
            **source,
            "snapshot": self.snapshot,
            "composition_hash": strict_json.bundle_digest(self.snapshot),
            "launch_epoch": 1,
        }
        result = self._compile_durable_resume(
            self.resolved, self.snapshot, launch_epoch=1
        )

        outcome = self._perform(
            result,
            target,
            lambda *_args: "EXECUTED",
            expected_launch_epoch=0,
            expected_mutation_token=None,
            expected_source_scope_generation=source["scope_generation"],
            expected_source_composition_hash=source["composition_hash"],
        )

        self.assertEqual(outcome, "EXECUTED")
        on_disk = strict_json.loads(self.store.read_record_bytes(FIXED_ID))
        self.assertEqual(on_disk["version"], 3)
        self.assertIn("auto_compact_window_tokens", on_disk["snapshot"])
        self.assertEqual(on_disk["scope_generation"], source["scope_generation"])

    def test_source_hash_guard_allows_same_generation_snapshot_healing(self) -> None:
        current = self._durable_prior()
        current["mutation_token"] = sessions.new_mutation_token()
        self.store.save(current)
        resolved, healed_snapshot = self._override()
        prepared = {
            **current,
            "snapshot": healed_snapshot,
            "composition_hash": strict_json.bundle_digest(healed_snapshot),
            "launch_epoch": current["launch_epoch"] + 1,
        }
        result = self._compile_durable_resume(
            resolved, healed_snapshot, launch_epoch=prepared["launch_epoch"]
        )

        outcome = self._perform(
            result,
            prepared,
            lambda *_args: "EXECUTED",
            expected_launch_epoch=current["launch_epoch"],
            expected_mutation_token=current["mutation_token"],
            expected_source_scope_generation=current["scope_generation"],
            expected_source_composition_hash=current["composition_hash"],
        )

        self.assertEqual(outcome, "EXECUTED")
        stored = self.store.load(FIXED_ID)
        self.assertEqual(stored["scope_generation"], current["scope_generation"])
        self.assertEqual(stored["composition_hash"], prepared["composition_hash"])

    def test_source_hash_change_after_prepare_aborts_without_mutation(self) -> None:
        current = self._durable_prior()
        current["mutation_token"] = sessions.new_mutation_token()
        self.store.save(current)
        prepared = {**current, "launch_epoch": current["launch_epoch"] + 1}
        result = self._compile_durable_resume(
            self.resolved, self.snapshot, launch_epoch=prepared["launch_epoch"]
        )
        _resolved, changed_snapshot = self._override()
        changed = {
            **current,
            "snapshot": changed_snapshot,
            "composition_hash": strict_json.bundle_digest(changed_snapshot),
        }
        self.store.save(changed)
        changed_bytes = self.store.read_record_bytes(FIXED_ID)

        with self.assertRaisesRegex(launch.LaunchError, "source composition changed"):
            self._perform(
                result,
                prepared,
                lambda *_args: "EXECUTED",
                expected_launch_epoch=current["launch_epoch"],
                expected_mutation_token=current["mutation_token"],
                expected_source_scope_generation=current["scope_generation"],
                expected_source_composition_hash=current["composition_hash"],
            )
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), changed_bytes)
        self.assertFalse(self._scope_dir().exists())

    def test_second_stale_prepared_resume_cannot_reuse_launch_epoch(self) -> None:
        current = self._durable_prior()
        current["mutation_token"] = sessions.new_mutation_token()
        self.store.save(current)
        baseline_epoch = current["launch_epoch"]
        baseline_token = current["mutation_token"]
        prepared = {**current, "launch_epoch": baseline_epoch + 1}
        result = self._compile_durable_resume(
            self.resolved, self.snapshot, launch_epoch=prepared["launch_epoch"]
        )
        outcome = self._perform(
            result,
            prepared,
            lambda *_args: "EXECUTED",
            expected_launch_epoch=baseline_epoch,
            expected_mutation_token=baseline_token,
        )
        self.assertEqual(outcome, "EXECUTED")
        first_commit = self.store.read_record_bytes(FIXED_ID)
        with self.assertRaisesRegex(
            launch.LaunchError, "authority changed after preparation"
        ):
            self._perform(
                result,
                prepared,
                lambda *_args: "EXECUTED",
                expected_launch_epoch=baseline_epoch,
                expected_mutation_token=baseline_token,
            )
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), first_commit)

    def test_pre_exec_pointer_failure_restores_record_and_scope(self) -> None:
        from unittest import mock

        current = self._durable_prior()
        current["mutation_token"] = sessions.new_mutation_token()
        self.store.save(current)
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        prior_plan = transition._expected_plan(current, self.bundle, state_root=self.store.root)
        scope.write_scope(self.store.root, FIXED_ID, prior_plan)
        prepared = {**current, "launch_epoch": current["launch_epoch"] + 1}
        result = self._compile_durable_resume(
            self.resolved, self.snapshot, launch_epoch=prepared["launch_epoch"]
        )
        with mock.patch.object(
            self.store, "update_last", side_effect=OSError("pointer write failed")
        ):
            with self.assertRaisesRegex(OSError, "pointer write failed"):
                self._perform(
                    result,
                    prepared,
                    lambda *_args: "EXECUTED",
                    trusted=self.bundle,
                    expected_launch_epoch=current["launch_epoch"],
                    expected_mutation_token=current["mutation_token"],
                )
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)
        self.assertEqual(
            (self._scope_dir() / "settings.json").read_bytes(),
            strict_json.canonical_file_bytes(prior_plan.settings),
        )
        self.assertIsNone(self.store.last(str(self.project)))

    def test_explicit_ordinary_model_relaunch_clears_model_repair_pending_hook(self) -> None:
        current = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=str(self.project),
            model="qwen38",
            context_profile="large",
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_REPAIR_NEEDED,
        )
        current["observed_model"] = "gpt-multi-sol-high"
        self.store.save(current)
        prepared = {
            **current,
            "ordinary_model": "sol",
            "context_profile": "sol",
            "scope_generation": 2,
        }
        result = compiler.compile_direct_launch(
            docs=self.docs,
            session_action=compiler.build_resume(FIXED_ID),
            model_id="sol",
            passthrough=[],
            scope_dir=self._scope_dir(),
            hook_command=str(scope.hook_shim_path(self.root / "state")),
            state_root=self.root / "state",
            pin_model=True,
        )
        outcome = self._perform(
            result,
            prepared,
            lambda *_args: "EXECUTED",
            allow_model_relaunch=True,
        )
        self.assertEqual(outcome, "EXECUTED")
        stored = self.store.load(FIXED_ID)
        self.assertEqual(stored["ordinary_model"], "sol")
        self.assertEqual(stored["context_profile"], "sol")
        self.assertEqual(stored["identity_state"], sessions.IDENTITY_UNVERIFIED)
        self.assertNotIn("observed_model", stored)

    def test_failed_ordinary_model_relaunch_restores_prior_scope_and_lifecycle(self) -> None:
        current = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=str(self.project),
            model="qwen38",
            context_profile="large",
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_REPAIR_NEEDED,
        )
        current["observed_model"] = "gpt-multi-sol-high"
        self.store.save(current)
        prior_plan = scope.compile_ordinary_scope(
            managed_id=FIXED_ID,
            hook_command=str(scope.hook_shim_path(self.root / "state")),
            available_models=compiler.direct_profile_selectors(self.docs, "large"),
            default_model=self.docs["models"]["models"]["qwen38"]["client_selector"],
            launch_epoch=current["launch_epoch"],
            gateway_base_url=self.docs["gateway"]["gateway"]["base_url"],
            token_helper_command=str(
                scope.gateway_token_shim_path(self.root / "state")
            ),
        )
        scope.write_scope(self.store.root, FIXED_ID, prior_plan)
        prepared = {
            **current,
            "ordinary_model": "sol",
            "context_profile": "sol",
            "scope_generation": 2,
        }
        result = compiler.compile_direct_launch(
            docs=self.docs,
            session_action=compiler.build_resume(FIXED_ID),
            model_id="sol",
            passthrough=[],
            scope_dir=self._scope_dir(),
            hook_command=str(scope.hook_shim_path(self.root / "state")),
            state_root=self.root / "state",
            pin_model=True,
        )
        hook_done = threading.Event()
        hook_thread: list[threading.Thread] = []

        def failing_execve(*_args):
            thread = threading.Thread(
                target=lambda: (
                    self.store.record_session_end(
                        FIXED_ID,
                        observed_runtime_id=FIXED_ID,
                        reason="other",
                        now="2026-07-22T00:00:01Z",
                    ),
                    hook_done.set(),
                )
            )
            hook_thread.append(thread)
            thread.start()
            thread.join(timeout=0.2)
            self.assertTrue(thread.is_alive())
            raise OSError("boom")

        with self.assertRaises(OSError):
            self._perform(
                result,
                prepared,
                failing_execve,
                allow_model_relaunch=True,
                trusted=self.bundle,
            )
        hook_thread[0].join(timeout=30)
        self.assertTrue(hook_done.is_set())
        stored = self.store.load(FIXED_ID)
        self.assertEqual(stored["ordinary_model"], "qwen38")
        self.assertEqual(stored["context_profile"], "large")
        self.assertEqual(stored["scope_generation"], 1)
        self.assertEqual(stored["last_end_reason"], "other")
        self.assertEqual(
            (self._scope_dir() / "settings.json").read_bytes(),
            strict_json.canonical_file_bytes(prior_plan.settings),
        )

    def test_same_profile_ordinary_model_change_after_prepare_aborts(self) -> None:
        current = sessions.make_ordinary_record(
            managed_id=FIXED_ID,
            runtime_session_id=FIXED_ID,
            cwd=str(self.project),
            model="qwen38",
            context_profile="large",
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.2.0",
            identity_state=sessions.IDENTITY_AUTHORITATIVE,
        )
        self.store.save(current)
        prepared = {**current, "scope_generation": 2}
        result = compiler.compile_direct_launch(
            docs=self.docs,
            session_action=compiler.build_resume(FIXED_ID),
            model_id="qwen38",
            passthrough=[],
            scope_dir=self._scope_dir(),
            hook_command=str(scope.hook_shim_path(self.root / "state")),
            state_root=self.root / "state",
            pin_model=False,
        )
        self.store.reconcile_runtime(
            FIXED_ID,
            observed_runtime_id=FIXED_ID,
            source="compact",
            cwd=str(self.project),
            model="fable",
            model_profile="large",
            observed_model="claude-fable-5[1m]",
        )
        after_hook = self.store.read_record_bytes(FIXED_ID)
        with self.assertRaisesRegex(launch.LaunchError, "ordinary model changed"):
            self._perform(result, prepared, lambda *_args: "EXECUTED")
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), after_hook)
        self.assertFalse(self._scope_dir().exists())

    def test_model_repair_cannot_bypass_pending_fork(self) -> None:
        current = self._durable_prior()
        current["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        current["observed_model"] = "gpt-multi-sol-high"
        current["pending_forks"] = [
            {"session_id": OTHER_ID, "observed_at": "2026-07-22T00:00:00Z"}
        ]
        self.store.save(current)
        result = self._compile_durable_resume(self.resolved, self.snapshot)
        before = self.store.read_record_bytes(FIXED_ID)
        # The in-lock guard must carry the actionable message (H8): fork UUID
        # plus the adopt/discard commands, not a dead-end.
        with self.assertRaises(launch.LaunchError) as raised:
            self._perform(
                result,
                current,
                lambda *_args: "EXECUTED",
                allow_model_relaunch=True,
            )
        message = str(raised.exception)
        self.assertIn("unresolved native fork", message)
        self.assertIn(OTHER_ID, message)
        self.assertIn("resolve-fork", message)
        self.assertIn("sessions link", message)
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)

    def test_repair_needed_launch_guard_carries_exact_relink_command(self) -> None:
        # Issue 002: the in-lock guard must render the runnable command —
        # both UUIDs and the --cwd guidance — not a bare command name.
        current = self._durable_prior()
        current["identity_state"] = sessions.IDENTITY_REPAIR_NEEDED
        current["observed_cwd"] = "/wrong/project"
        self.store.save(current)
        result = self._compile_durable_resume(self.resolved, self.snapshot)
        before = self.store.read_record_bytes(FIXED_ID)
        with self.assertRaises(launch.LaunchError) as raised:
            self._perform(result, current, lambda *_args: "EXECUTED")
        message = str(raised.exception)
        base = f"claude-multi sessions relink-runtime {FIXED_ID} {FIXED_ID}"
        self.assertIn(f"`{base} --cwd {self.project}`", message)
        self.assertIn(f"`{base} --cwd /wrong/project`", message)
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), before)

    def test_execve_oserror_resume_cleanup_noops_when_newer_record_committed(self) -> None:
        # L2: the failing launch restores only while the record is exactly
        # what it wrote; a newer commit owns the record AND the scope.
        self._durable_prior()
        expected = transition._expected_plan(
            self.store.load(FIXED_ID), self.bundle, state_root=self.store.root
        )
        scope.write_scope(self.store.root, FIXED_ID, expected)
        resolved_override, snap_override = self._override()
        result = self._compile_durable_resume(resolved_override, snap_override)
        record = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=snap_override,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=2,
            now="2026-07-21T00:00:00Z",
        )
        newest = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=3,
            now="2026-07-22T00:00:00Z",
        )

        def interposing_execve(_executable, _argv, _env):
            # A newer attempt commits between this launch's save and cleanup.
            self.store.save(newest)
            raise OSError("boom")

        with self.assertRaises(OSError):
            self._perform(result, record, interposing_execve)
        self.assertEqual(
            self.store.read_record_bytes(FIXED_ID),
            strict_json.canonical_file_bytes(newest),
        )
        # The attempted (override) scope is left untouched for the new owner.
        live = self._scope_dir()
        self.assertEqual(
            (live / "settings.json").read_bytes(),
            strict_json.canonical_file_bytes(result.scope_plan.settings),
        )
        self.assertTrue(
            (live / ".claude" / "agents" / "cm-analyst-sol-xhigh.md").is_file()
        )

    def test_execve_oserror_durable_resume_rewrites_record_authoritative_scope(self) -> None:
        # L4: with the installed catalog, a durable resume's failed exec
        # restores the prior record and rewrites the record-authoritative
        # scope instead of deleting it — even when the attempted scope was
        # compiled from a different (override) composition.
        self._durable_prior()
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        expected = transition._expected_plan(
            self.store.load(FIXED_ID), self.bundle, state_root=self.store.root
        )
        scope.write_scope(self.store.root, FIXED_ID, expected)
        resolved_override, snap_override = self._override()
        result = self._compile_durable_resume(resolved_override, snap_override)
        record = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=snap_override,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=2,
            now="2026-07-21T00:00:00Z",
        )

        def failing_execve(_executable, _argv, _env):
            raise OSError("boom")

        with self.assertRaises(OSError):
            self._perform(result, record, failing_execve, trusted=self.bundle)
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)
        live = self._scope_dir()
        self.assertTrue(live.is_dir())
        # Not the attempted override scope, not deleted: the recompile.
        self.assertFalse(
            (live / ".claude" / "agents" / "cm-analyst-sol-xhigh.md").exists()
        )
        self.assertEqual(
            (live / "settings.json").read_bytes(),
            strict_json.canonical_file_bytes(expected.settings),
        )
        for relpath, data in expected.agent_files.items():
            self.assertEqual(live.joinpath(*relpath.split("/")).read_bytes(), data)

    def test_execve_oserror_durable_resume_without_catalog_removes_contradicting_scope(self) -> None:
        # L4 fallback: no catalog to recompile with, and the attempted scope
        # contradicts the restored record — remove it (converge rebuilds).
        self._durable_prior()
        prior_bytes = self.store.read_record_bytes(FIXED_ID)
        expected = transition._expected_plan(
            self.store.load(FIXED_ID), self.bundle, state_root=self.store.root
        )
        scope.write_scope(self.store.root, FIXED_ID, expected)
        resolved_override, snap_override = self._override()
        result = self._compile_durable_resume(resolved_override, snap_override)
        record = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=snap_override,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=2,
            now="2026-07-21T00:00:00Z",
        )

        def failing_execve(_executable, _argv, _env):
            raise OSError("boom")

        with self.assertRaises(OSError):
            self._perform(result, record, failing_execve)
        self.assertEqual(self.store.read_record_bytes(FIXED_ID), prior_bytes)
        self.assertFalse(self._scope_dir().exists())


if __name__ == "__main__":
    unittest.main()


class LegacyResumeScopeRewriteTests(DurablePerformLaunchTests):
    """H4: --legacy resume of a durable record still advances the scope."""

    def test_legacy_resume_rewrites_scope_with_the_new_epoch(self) -> None:
        prior = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            launch_epoch=1,
            now="2026-07-20T00:00:00Z",
        )
        self.store.save(prior)
        # Pre-existing scope at epoch 1 (the session's current durable files).
        old_plan = scope.compile_scope(
            self.resolved,
            self.bundle.docs["roles"]["roles"],
            self.bundle.prompt_bodies,
            scope.catalog_meta_from_docs(self.bundle.docs),
            managed_id=FIXED_ID,
            hook_command=str(scope.hook_shim_path(self.root / "state")),
            launch_epoch=1,
            token_helper_command=str(
                scope.gateway_token_shim_path(self.root / "state")
            ),
        )
        scope.write_scope(self.store.root, FIXED_ID, old_plan)

        # A --legacy resume: argv-mode result (durable=False), record keeps
        # mode durable, epoch advances to 2.
        result = compiler.compile_launch(
            docs=self.docs,
            prompt_bodies=self.bundle.prompt_bodies,
            resolved=self.resolved,
            session_action=compiler.build_resume(FIXED_ID, FIXED_ID),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                self.root / "state", self.digest, FIXED_ID
            ),
        )
        committed = {
            **prior,
            "launch_epoch": 2,
            "mutation_token": None,
        }
        captured: dict = {}

        def fake_execve(executable, argv, env):
            captured["argv"] = argv
            return "EXECUTED"

        self._perform(result, committed, execve=fake_execve, trusted=self.bundle)

        settings = strict_json.loads(
            (self.store.root / "scopes" / FIXED_ID / "settings.json").read_bytes()
        )
        self.assertEqual(settings["env"]["CLAUDE_MULTI_LAUNCH_EPOCH"], "2")
        self.assertEqual(settings["apiKeyHelper"], str(
            scope.gateway_token_shim_path(self.store.root)
        ))
        # The scope remains the record-authoritative shape (fence + hooks).
        self.assertIn("SessionStart", settings["hooks"])


class LegacyRollbackConvergeTests(DurablePerformLaunchTests):
    """The pre-exec rollback converges the legacy-rewritten scope too."""

    def test_mutation_failure_after_scope_write_restores_prior_epoch_scope(self) -> None:
        from unittest import mock

        prior = sessions.make_record(
            session_id=FIXED_ID,
            cwd=str(self.project),
            composition_name="default",
            snapshot=self.snapshot,
            catalog_version=1,
            catalog_hash="sha256:" + "0" * 64,
            launcher_version="2.1.0",
            mode="durable",
            scope_generation=1,
            launch_epoch=1,
            now="2026-07-20T00:00:00Z",
        )
        self.store.save(prior)
        old_plan = scope.compile_scope(
            self.resolved,
            self.bundle.docs["roles"]["roles"],
            self.bundle.prompt_bodies,
            scope.catalog_meta_from_docs(self.bundle.docs),
            managed_id=FIXED_ID,
            hook_command=str(scope.hook_shim_path(self.root / "state")),
            launch_epoch=1,
            token_helper_command=str(
                scope.gateway_token_shim_path(self.root / "state")
            ),
        )
        scope.write_scope(self.store.root, FIXED_ID, old_plan)
        result = compiler.compile_launch(
            docs=self.docs,
            prompt_bodies=self.bundle.prompt_bodies,
            resolved=self.resolved,
            session_action=compiler.build_resume(FIXED_ID, FIXED_ID),
            passthrough=[],
            settings_path=Path("/trusted/settings.json"),
            lead_prompt_path=compiler.lead_prompt_path(
                self.root / "state", self.digest, FIXED_ID
            ),
        )
        committed = {**prior, "launch_epoch": 2, "mutation_token": None}

        def boom_update_last(*_args, **_kwargs):
            raise OSError("pointer write failed")

        with mock.patch.object(
            self.store, "update_last", side_effect=boom_update_last
        ):
            with self.assertRaises(OSError):
                self._perform(result, committed, trusted=self.bundle)
        # Record restored to the prior bytes; the scope converged with it
        # (epoch 1 again), not left diverged at epoch 2.
        self.assertEqual(
            self.store.read_record_bytes(FIXED_ID),
            strict_json.canonical_file_bytes(prior),
        )
        settings = strict_json.loads(
            (self.store.root / "scopes" / FIXED_ID / "settings.json").read_bytes()
        )
        self.assertEqual(settings["env"]["CLAUDE_MULTI_LAUNCH_EPOCH"], "1")
