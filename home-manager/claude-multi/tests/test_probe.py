"""Tests for the Phase-1B disposable no-provider probe harness.

Every safety refusal and the fake loopback endpoint are exercised
deterministically. These tests never execute the installed Claude binary,
never contact a real provider, never read user transcripts, and never
contact, restart, or stop the live daemon. Native execution is tested only
against fake executables created inside each test's private scratch tree.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import io
import os
import shutil
import socket
import stat
import sys
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from claude_multi import dev, probe, state, strict_json
from claude_multi.probe import ProbeError


_FAKE_CLAUDE_BODY = """import http.client
import json
import os
import urllib.parse

env = os.environ
print("HOME=" + env.get("HOME", ""))
print("CLAUDE_CONFIG_DIR=" + env.get("CLAUDE_CONFIG_DIR", ""))
print("AMBIENT=" + env.get("PROBE_AMBIENT_MARKER", "absent"))
print("AUTO_WINDOW=" + env.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "absent"))
print("AUTO_PERCENT=" + env.get("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "absent"))
print("MAX_CONTEXT=" + env.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS", "absent"))
url = urllib.parse.urlsplit(env["ANTHROPIC_BASE_URL"])
body = json.dumps({
    "model": "probe-model",
    "max_tokens": 8,
    "messages": [{"role": "user", "content": "probe-prompt-content"}],
}).encode("utf-8")
connection = http.client.HTTPConnection(url.hostname, url.port, timeout=5)
connection.request(
    "POST",
    "/v1/messages",
    body=body,
    headers={"Content-Type": "application/json", "x-api-key": env["ANTHROPIC_AUTH_TOKEN"]},
)
response = connection.getresponse()
print("STATUS=" + str(response.status))
print("BODY=" + response.read().decode("utf-8"))
"""

_PTY_BODY = """import os
import sys

print("READY", flush=True)
first = input()
print("ACK=" + first, flush=True)
second = input()
print("DONE=" + second, flush=True)
"""

_SPAWNER_BODY = """import subprocess
import sys
import time

child = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(30)"],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
with open(sys.argv[1], "w") as handle:
    handle.write(str(child.pid))
time.sleep(30)
"""


class ProbeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-probe-test-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(self._cleanup)
        # Simulated "live" environment roots; the fixture must stay disjoint.
        live = self.root / "live"
        self.environ = {
            "HOME": str(live / "home"),
            "PATH": "/usr/bin:/bin",
        }
        self.fixture_root = self.root / "fixture"

    def _cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _live(self, *parts: str) -> Path:
        return Path(self.environ["HOME"]).joinpath(*parts)

    def _flags(self) -> dict:
        return {
            "allow-local-claude": True,
            "fixture-root": str(self.fixture_root),
        }

    def _write_fake_executable(self, name: str = "fake-claude", body: str = _FAKE_CLAUDE_BODY) -> Path:
        script = self.root / "bin" / name
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(f"#!{sys.executable}\n" + body)
        script.chmod(0o700)
        return script

    def _trusted(
        self, script: Path, *, fake: bool = True, digest: str | None = None
    ) -> probe.TrustedExecutable:
        return probe.TrustedExecutable(
            script,
            digest
            if digest is not None
            else hashlib.sha256(script.read_bytes()).hexdigest(),
            fake=fake,
        )

    def _contract_file(self, script: Path, *, digest: str | None = None) -> Path:
        contract = {
            "claude": {
                "executable": {
                    "resolved_path": str(script),
                    "sha256": digest
                    if digest is not None
                    else hashlib.sha256(script.read_bytes()).hexdigest(),
                }
            }
        }
        path = self.root / "native-contract.json"
        path.write_bytes(strict_json.canonical_file_bytes(contract))
        return path

    def _post(
        self,
        port: int,
        body: bytes,
        headers: dict | None = None,
        path: str = "/v1/messages",
    ) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request(
            "POST",
            path,
            body=body,
            headers=headers or {"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, payload

    def _get(self, port: int, path: str) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        return response.status, payload

    def _raw_request(self, port: int, request: bytes) -> tuple[int, bytes]:
        """Raw HTTP/1.1 exchange for malformed requests http.client cannot send.

        Only used for fail-closed error paths where the server closes the
        connection after its deterministic error response.
        """

        with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
            sock.sendall(request)
            chunks = []
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
        raw = b"".join(chunks)
        status = int(raw.split(b" ", 2)[1])
        body = raw.split(b"\r\n\r\n", 1)[1] if b"\r\n\r\n" in raw else b""
        return status, body


class GatingTests(ProbeTestCase):
    def test_missing_allow_local_claude_refused(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.probe_cli(
                ["init"], {"fixture-root": str(self.fixture_root)}, [], environ=self.environ
            )
        self.assertEqual(code, 2)
        self.assertIn("--allow-local-claude", stderr.getvalue())

    def test_missing_fixture_root_refused(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.probe_cli(
                ["init"], {"allow-local-claude": True}, [], environ=self.environ
            )
        self.assertEqual(code, 2)
        self.assertIn("--fixture-root", stderr.getvalue())

    def test_flag_shaped_fixture_root_refused(self) -> None:
        # `--fixture-root --allow-local-claude` parses fixture-root as True.
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.probe_cli(
                ["init"],
                {"allow-local-claude": True, "fixture-root": True},
                [],
                environ=self.environ,
            )
        self.assertEqual(code, 2)
        self.assertIn("--fixture-root", stderr.getvalue())

    def test_unknown_action_refused(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.probe_cli(
                ["dance"], self._flags(), [], environ=self.environ
            )
        self.assertEqual(code, 2)
        self.assertIn("unknown probe action", stderr.getvalue())
        self.assertIn("'init' or 'run'", stderr.getvalue())

    def test_dev_main_dispatches_probe(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = dev.main(["probe", "init", "--fixture-root", str(self.fixture_root)])
        self.assertEqual(code, 2)
        self.assertIn("--allow-local-claude", stderr.getvalue())

    def test_dev_main_probe_creates_no_live_draft_state(self) -> None:
        live_state = self.root / "xdg-live"
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(live_state)}):
            with contextlib.redirect_stderr(io.StringIO()):
                code = dev.main(["probe", "init"])
        self.assertEqual(code, 2)
        self.assertFalse((live_state / "claude-multi" / "drafts").exists())


class CredentialRefusalTests(ProbeTestCase):
    def test_refuses_each_provider_credential_shape(self) -> None:
        for name in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "OPENAI_API_KEY",
            "KIMI_API_KEY",
            "MOONSHOT_API_KEY",
            "AWS_SECRET_ACCESS_KEY",
            "AZURE_CLIENT_SECRET",
            "GROQ_API_KEY",
            "XAI_API_KEY",
            "HF_TOKEN",
            "HUGGINGFACE_API_KEY",
            "OPENROUTER_API_KEY",
            "LITELLM_API_KEY",
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(ProbeError, name):
                    probe.assert_no_provider_credentials(
                        {"HOME": "/x", name: "dummy-secret-value"}
                    )

    def test_refusal_names_variables_never_values(self) -> None:
        with self.assertRaises(ProbeError) as raised:
            probe.assert_no_provider_credentials(
                {"ANTHROPIC_API_KEY": "dummy-secret-value"}
            )
        self.assertIn("ANTHROPIC_API_KEY", str(raised.exception))
        self.assertNotIn("dummy-secret-value", str(raised.exception))

    def test_benign_environment_passes(self) -> None:
        probe.assert_no_provider_credentials(
            {"HOME": "/x", "PATH": "/usr/bin", "EDITOR": "vi", "ANTHROPIC_MODEL": "m"}
        )

    def test_cli_refuses_ambient_credentials_before_any_effect(self) -> None:
        script = self._write_fake_executable()
        ambient = {**self.environ, "ANTHROPIC_API_KEY": "dummy-secret-value"}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.probe_cli(
                ["run"], self._flags(), [str(script)], environ=ambient
            )
        self.assertEqual(code, 2)
        self.assertIn("ANTHROPIC_API_KEY", stderr.getvalue())
        self.assertNotIn("dummy-secret-value", stderr.getvalue())
        self.assertFalse((self.fixture_root / "evidence").exists())


class FixtureRootRefusalTests(ProbeTestCase):
    def test_relative_root_refused(self) -> None:
        with self.assertRaisesRegex(ProbeError, "absolute"):
            probe.build_fixture("relative/fixture", environ=self.environ)

    def test_dotdot_root_refused(self) -> None:
        with self.assertRaisesRegex(ProbeError, r"\.\."):
            probe.build_fixture(self.root / "a" / ".." / "fixture", environ=self.environ)

    def test_symlink_root_refused(self) -> None:
        real = self.root / "real-fixture"
        real.mkdir(mode=0o700)
        link = self.root / "link-fixture"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ProbeError, "symlink"):
            probe.build_fixture(link, environ=self.environ)

    def test_symlink_ancestor_refused(self) -> None:
        real = self.root / "real-parent"
        real.mkdir(mode=0o700)
        link = self.root / "link-parent"
        link.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(ProbeError, "symlink"):
            probe.build_fixture(link / "child", environ=self.environ)

    def test_existing_non_private_root_refused(self) -> None:
        public = self.root / "public-fixture"
        public.mkdir(mode=0o755)
        with self.assertRaisesRegex(ProbeError, "private"):
            probe.build_fixture(public, environ=self.environ)

    def test_existing_file_root_refused(self) -> None:
        file_root = self.root / "file-fixture"
        file_root.write_text("x")
        with self.assertRaisesRegex(ProbeError, "private"):
            probe.build_fixture(file_root, environ=self.environ)

    def test_live_home_refused(self) -> None:
        with self.assertRaisesRegex(ProbeError, "live HOME"):
            probe.build_fixture(self._live(), environ=self.environ)

    def test_live_xdg_config_refused(self) -> None:
        with self.assertRaisesRegex(ProbeError, "live XDG_CONFIG_HOME"):
            probe.build_fixture(self._live(".config"), environ=self.environ)

    def test_live_claude_config_refused(self) -> None:
        with self.assertRaisesRegex(ProbeError, "live Claude config root"):
            probe.build_fixture(self._live(".claude"), environ=self.environ)

    def test_inside_live_claude_config_refused(self) -> None:
        with self.assertRaisesRegex(ProbeError, "inside the live Claude config root"):
            probe.build_fixture(self._live(".claude", "probe"), environ=self.environ)

    def test_ancestor_of_live_home_refused(self) -> None:
        with self.assertRaisesRegex(ProbeError, "contains the live"):
            probe.build_fixture(self.root / "live", environ=self.environ)

    def test_live_state_root_refused(self) -> None:
        with self.assertRaisesRegex(ProbeError, "live claude-multi state root"):
            probe.build_fixture(
                self._live(".local", "state", "claude-multi"), environ=self.environ
            )

    def test_inside_live_state_root_refused(self) -> None:
        with self.assertRaisesRegex(
            ProbeError, "inside the live claude-multi state root"
        ):
            probe.build_fixture(
                self._live(".local", "state", "claude-multi", "probe"),
                environ=self.environ,
            )

    def test_live_uid_daemon_socket_refused(self) -> None:
        with self.assertRaisesRegex(ProbeError, "live uid-shared daemon socket"):
            probe.build_fixture(
                Path(f"/tmp/cc-daemon-{os.geteuid()}"), environ=self.environ
            )

    def test_ancestor_of_uid_daemon_socket_refused(self) -> None:
        # Live roots only need path computation; point the simulated HOME
        # outside /tmp so the uid-shared daemon socket is the tightest
        # contained live domain.
        environ = {"HOME": "/nonexistent-probe-home", "PATH": "/usr/bin"}
        with self.assertRaisesRegex(
            ProbeError, "contains the live uid-shared daemon socket"
        ):
            probe.build_fixture(Path("/tmp"), environ=environ)

    def test_live_runtime_dir_refused(self) -> None:
        environ = {**self.environ, "XDG_RUNTIME_DIR": str(self.root / "run")}
        with self.assertRaisesRegex(ProbeError, "live XDG_RUNTIME_DIR"):
            probe.build_fixture(self.root / "run", environ=environ)

    def test_live_claude_config_dir_env_refused(self) -> None:
        environ = {**self.environ, "CLAUDE_CONFIG_DIR": str(self.root / "ccd")}
        with self.assertRaisesRegex(ProbeError, "live CLAUDE_CONFIG_DIR"):
            probe.build_fixture(self.root / "ccd", environ=environ)

    def test_inside_live_claude_config_dir_env_refused(self) -> None:
        environ = {**self.environ, "CLAUDE_CONFIG_DIR": str(self.root / "ccd")}
        with self.assertRaisesRegex(ProbeError, "inside the live CLAUDE_CONFIG_DIR"):
            probe.build_fixture(self.root / "ccd" / "inner", environ=environ)

    def test_containing_live_claude_config_dir_env_refused(self) -> None:
        environ = {
            **self.environ,
            "CLAUDE_CONFIG_DIR": str(self.root / "other" / "ccd"),
        }
        with self.assertRaisesRegex(ProbeError, "contains the live CLAUDE_CONFIG_DIR"):
            probe.build_fixture(self.root / "other", environ=environ)

    def test_symlinked_live_home_root_cannot_hide_overlap(self) -> None:
        real_home = self.root / "real-home"
        (real_home / ".claude").mkdir(parents=True, mode=0o700)
        link_home = self.root / "link-home"
        link_home.symlink_to(real_home, target_is_directory=True)
        environ = {"HOME": str(link_home), "PATH": "/usr/bin"}
        with self.assertRaisesRegex(ProbeError, "live Claude config root"):
            probe.build_fixture(real_home / ".claude", environ=environ)

    def test_symlinked_xdg_state_root_cannot_hide_overlap(self) -> None:
        real_state = self.root / "real-state"
        real_state.mkdir(mode=0o700)
        link_state = self.root / "link-state"
        link_state.symlink_to(real_state, target_is_directory=True)
        environ = {**self.environ, "XDG_STATE_HOME": str(link_state)}
        with self.assertRaisesRegex(ProbeError, "live claude-multi state root"):
            probe.build_fixture(real_state / "claude-multi", environ=environ)


class BuildFixtureTests(ProbeTestCase):
    def test_directories_created_private(self) -> None:
        fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        for path in (
            fixture.root,
            fixture.home,
            fixture.xdg_config_home,
            fixture.xdg_state_home,
            fixture.xdg_cache_home,
            fixture.xdg_data_home,
            fixture.xdg_runtime_dir,
            fixture.claude_config_dir,
        ):
            with self.subTest(path=path):
                info = os.lstat(path)
                self.assertTrue(stat.S_ISDIR(info.st_mode))
                self.assertFalse(stat.S_ISLNK(info.st_mode))
                self.assertEqual(stat.S_IMODE(info.st_mode), 0o700)
                self.assertEqual(info.st_uid, os.geteuid())

    def test_environ_without_provider_has_zero_tokens(self) -> None:
        fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        env = fixture.environ()
        self.assertNotIn("ANTHROPIC_BASE_URL", env)
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_environ_layout_is_disposable(self) -> None:
        fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        env = fixture.environ()
        for key in (
            "HOME",
            "XDG_CONFIG_HOME",
            "XDG_STATE_HOME",
            "XDG_CACHE_HOME",
            "XDG_DATA_HOME",
            "XDG_RUNTIME_DIR",
            "CLAUDE_CONFIG_DIR",
        ):
            with self.subTest(key=key):
                self.assertTrue(env[key].startswith(str(fixture.root) + os.sep))
        self.assertNotEqual(env["CLAUDE_CONFIG_DIR"], str(self._live(".claude")))
        self.assertEqual(env["DISABLE_AUTOUPDATER"], "1")

    def test_environ_with_provider_uses_dummy_token(self) -> None:
        fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        env = fixture.environ(base_url="http://127.0.0.1:8317")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://127.0.0.1:8317")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], probe.DUMMY_TOKEN)

    def test_environ_rejects_non_loopback_base_url(self) -> None:
        fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        with self.assertRaisesRegex(ProbeError, "loopback"):
            fixture.environ(base_url="http://10.0.0.9:8317")

    def test_environ_accepts_only_typed_compaction_controls(self) -> None:
        fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        policy = probe.ProbeCompactionPolicy(
            auto_compact_window=983616,
            auto_compact_percent=90,
            max_context_tokens=372000,
        )
        env = fixture.environ(compaction=policy)
        self.assertEqual(env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "983616")
        self.assertEqual(env["CLAUDE_AUTOCOMPACT_PCT_OVERRIDE"], "90")
        self.assertEqual(env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"], "372000")
        self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)

    def test_invalid_compaction_controls_fail_closed(self) -> None:
        fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        for policy, needle in (
            (probe.ProbeCompactionPolicy(0, 90), "between 100000 and 1000000"),
            (probe.ProbeCompactionPolicy(100000, 0), "percentage must be 1..100"),
            (probe.ProbeCompactionPolicy(100000, 90, 0), "max context tokens"),
        ):
            with self.subTest(policy=policy):
                with self.assertRaisesRegex(ProbeError, needle):
                    fixture.environ(compaction=policy)

    def test_environ_path_is_explicit_without_cwd_entry(self) -> None:
        fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        env = fixture.environ()
        self.assertEqual(env["PATH"], "/usr/bin:/bin")
        self.assertTrue(all(env["PATH"].split(":")))

    def test_reentry_reuses_existing_fixture(self) -> None:
        first = probe.build_fixture(self.fixture_root, environ=self.environ)
        again = probe.build_fixture(self.fixture_root, environ=self.environ)
        self.assertEqual(again, first)


class LoopbackTests(ProbeTestCase):
    def test_accepts_loopback_literal(self) -> None:
        self.assertEqual(
            probe.check_loopback_url("http://127.0.0.1:8317"),
            "http://127.0.0.1:8317",
        )

    def test_refuses_non_loopback(self) -> None:
        for url in (
            "http://localhost:8317",
            "http://[::1]:8317",
            "http://10.0.0.9:8317",
            "http://192.168.1.5:8317",
            "http://api.example.com:8317",
            "https://127.0.0.1:8317",
            "http://127.0.0.1",
            "http://127.0.0.1:not-a-port",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ProbeError):
                    probe.check_loopback_url(url)


class FakeProviderTests(ProbeTestCase):
    def test_constructor_refuses_non_loopback_host(self) -> None:
        for host in ("0.0.0.0", "10.0.0.9", "example.com", "::1"):
            with self.subTest(host=host):
                with self.assertRaisesRegex(ProbeError, "loopback"):
                    probe.FakeAnthropicProvider(host=host)

    def test_base_url_requires_start(self) -> None:
        provider = probe.FakeAnthropicProvider()
        with self.assertRaisesRegex(ProbeError, "not started"):
            _ = provider.base_url

    def test_healthz_and_unknown_get(self) -> None:
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            status, payload = self._get(port, "/healthz")
            self.assertEqual(status, 200)
            self.assertEqual(strict_json.loads(payload), {"status": "ok"})
            status, payload = self._get(port, "/v1/models")
            self.assertEqual(status, 200)
            models = strict_json.loads(payload)
            self.assertIn("gpt-multi-sol-high", [item["id"] for item in models["data"]])
            self.assertFalse(models["has_more"])
            status, payload = self._get(port, "/nope")
            self.assertEqual(status, 404)
            self.assertEqual(
                strict_json.loads(payload)["error"]["type"], "not_found_error"
            )
            # GET endpoints are not probe traffic: nothing is recorded.
            self.assertEqual(provider.requests, ())

    def test_messages_text_response_and_record(self) -> None:
        body = (
            b'{"model":"probe-model","max_tokens":8,'
            b'"messages":[{"role":"user","content":"probe-prompt-content"}]}'
        )
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            status, payload = self._post(port, body)
            self.assertEqual(status, 200)
            reply = strict_json.loads(payload)
            self.assertEqual(reply["content"], [{"type": "text", "text": "PROBE-OK"}])
            self.assertEqual(reply["model"], "probe-model")
            self.assertEqual(reply["stop_reason"], "end_turn")
            self.assertEqual(len(provider.requests), 1)
            record = provider.requests[0]
            self.assertEqual(record.method, "POST")
            self.assertEqual(record.path, "/v1/messages")
            self.assertEqual(record.model, "probe-model")
            self.assertEqual(record.tool_names, ())
            self.assertEqual(record.auth, "absent")
            self.assertEqual(record.body_sha256, hashlib.sha256(body).hexdigest())

    def test_request_query_is_hashed_in_metadata(self) -> None:
        body = b'{"model":"probe-model","messages":[]}'
        target = "/v1/messages?token=query-secret"
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            status, _payload = self._post(port, body, path=target)
            self.assertEqual(status, 200)
            record = provider.requests[0]
            self.assertRegex(record.path, r"^sha256:[0-9a-f]{64}:bytes=\d+$")
            self.assertNotIn("query-secret", record.path)

    def test_compaction_responder_exposes_one_near_limit_turn(self) -> None:
        responder = probe.CompactionResponder(near_limit_tokens=900)
        status, payload = responder({}, "/v1/messages/count_tokens")
        self.assertEqual((status, payload), (200, {"input_tokens": 1}))
        status, payload = responder(
            {"model": "probe-model", "stream": False}, "/v1/messages"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["usage"]["input_tokens"], 900)
        status, payload = responder({}, "/v1/messages/count_tokens")
        self.assertEqual((status, payload), (200, {"input_tokens": 900}))
        status, payload = responder(
            {"model": "probe-model", "stream": False}, "/v1/messages"
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["usage"]["input_tokens"], 1)
        status, payload = responder({}, "/v1/messages/count_tokens")
        self.assertEqual((status, payload), (200, {"input_tokens": 1}))
        self.assertEqual(responder.message_requests, 2)
        self.assertEqual(responder.count_token_requests, 3)

    def test_automatic_compaction_responder_uses_cache_aware_phases(self) -> None:
        responder = probe.AutomaticCompactionResponder()
        main = {
            "model": "probe-model",
            "stream": False,
            "tools": [{"name": "Agent"}],
        }
        for _ordinal in (1, 2):
            status, payload = responder(main, "/v1/messages")
            self.assertEqual(status, 200)
            self.assertEqual(payload["content"][0]["text"], "PROBE-OK")
        status, payload = responder(main, "/v1/messages")
        self.assertEqual(status, 200)
        self.assertEqual(
            sum(
                payload["usage"][key]
                for key in (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                    "output_tokens",
                )
            ),
            150000,
        )
        status, auxiliary = responder(
            {"model": "probe-model", "stream": False}, "/v1/messages"
        )
        self.assertEqual(status, 200)
        self.assertEqual(auxiliary["content"][0]["text"], "PROBE-AUX")
        status, payload = responder(main, "/v1/messages")
        self.assertEqual(status, 200)
        self.assertEqual(
            sum(
                payload["usage"][key]
                for key in (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                    "output_tokens",
                )
            ),
            165000,
        )
        responder(main, "/v1/messages")
        self.assertEqual(responder.message_requests, 6)
        self.assertEqual(responder.main_requests, 5)
        self.assertEqual(responder.auxiliary_requests, 1)

    def test_record_never_retains_prompt_content(self) -> None:
        body = (
            b'{"model":"probe-model","max_tokens":8,"system":"probe-system-content",'
            b'"messages":[{"role":"user","content":"probe-prompt-content"}]}'
        )
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            self._post(port, body)
            record = provider.requests[0]
            self.assertTrue(record.has_system)
            messages = [{"role": "user", "content": "probe-prompt-content"}]
            self.assertEqual(
                record.system_json_bytes,
                len(strict_json.canonical_bytes("probe-system-content")),
            )
            self.assertEqual(
                record.messages_json_bytes,
                len(strict_json.canonical_bytes(messages)),
            )
            self.assertEqual(record.message_count, 1)
            self.assertEqual(
                record.message_sha256s,
                (
                    strict_json.sha256_hex(
                        strict_json.canonical_bytes(messages[0])
                    ),
                ),
            )
            self.assertFalse(record.message_hashes_truncated)
            rendered = repr(record)
            self.assertNotIn("probe-prompt-content", rendered)
            self.assertNotIn("probe-system-content", rendered)

    def test_record_bounds_message_hash_metadata(self) -> None:
        messages = [
            {"role": "user", "content": f"message-{index}"}
            for index in range(70)
        ]
        body = strict_json.canonical_bytes(
            {"model": "probe-model", "max_tokens": 8, "messages": messages}
        )
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            self._post(port, body)
            record = provider.requests[0]
        self.assertEqual(record.message_count, 70)
        self.assertEqual(len(record.message_sha256s), 64)
        self.assertTrue(record.message_hashes_truncated)
        self.assertNotIn("message-0", repr(record))

    def test_record_bounds_and_hashes_request_controlled_identifiers(self) -> None:
        secret = "prompt-like-secret-" * 32
        tools = [
            {"name": secret if index == 0 else f"tool-{index}"}
            for index in range(70)
        ]
        body = strict_json.canonical_bytes(
            {
                "model": secret,
                "max_tokens": 8,
                "messages": [],
                "tools": tools,
            }
        )
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            self._post(port, body)
            record = provider.requests[0]
        self.assertTrue(record.model.startswith("sha256:"))
        self.assertEqual(record.tool_count, 70)
        self.assertEqual(len(record.tool_names), 64)
        self.assertTrue(record.tool_names_truncated)
        self.assertTrue(record.tool_names[0].startswith("sha256:"))
        self.assertNotIn(secret, repr(record))

    def test_auth_classification(self) -> None:
        body = b'{"model":"probe-model","max_tokens":8,"messages":[]}'
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            self._post(port, body, headers={"x-api-key": probe.DUMMY_TOKEN})
            self._post(
                port,
                body,
                headers={"Authorization": f"Bearer {probe.DUMMY_TOKEN}"},
            )
            self._post(port, body, headers={"x-api-key": "dummy-wrong-token"})
            auths = [record.auth for record in provider.requests]
            self.assertEqual(auths, ["dummy", "dummy", "other"])

    def test_invalid_json_body_deterministic_400_and_recorded(self) -> None:
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            status, payload = self._post(port, b"{not json")
            self.assertEqual(status, 400)
            self.assertEqual(
                strict_json.loads(payload)["error"]["type"], "invalid_request_error"
            )
            self.assertEqual(len(provider.requests), 1)
            self.assertIsNone(provider.requests[0].model)

    def test_tool_use_echo_for_registry_and_deny_probes(self) -> None:
        body = strict_json.canonical_bytes(
            {
                "model": "probe-model",
                "max_tokens": 8,
                "messages": [{"role": "user", "content": "x"}],
                "tools": [
                    {"name": "probe_tool_alpha", "input_schema": {"type": "object"}},
                    {"name": "probe_tool_beta", "input_schema": {"type": "object"}},
                ],
                "tool_choice": {"type": "any"},
            }
        )
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            status, payload = self._post(port, body)
            self.assertEqual(status, 200)
            reply = strict_json.loads(payload)
            self.assertEqual(reply["stop_reason"], "tool_use")
            self.assertEqual(
                reply["content"],
                [
                    {
                        "type": "tool_use",
                        "id": "toolu_probe_0001",
                        "name": "probe_tool_alpha",
                        "input": {},
                    }
                ],
            )
            self.assertEqual(
                provider.requests[0].tool_names,
                ("probe_tool_alpha", "probe_tool_beta"),
            )

    def test_forced_tool_choice_honored(self) -> None:
        body = strict_json.canonical_bytes(
            {
                "model": "probe-model",
                "max_tokens": 8,
                "messages": [],
                "tools": [
                    {"name": "probe_tool_alpha"},
                    {"name": "probe_tool_beta"},
                ],
                "tool_choice": {"type": "tool", "name": "probe_tool_beta"},
            }
        )
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            _, payload = self._post(port, body)
            reply = strict_json.loads(payload)
            self.assertEqual(reply["content"][0]["name"], "probe_tool_beta")

    def test_responses_are_byte_deterministic(self) -> None:
        body = b'{"model":"probe-model","max_tokens":8,"messages":[]}'
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            first = self._post(port, body)
            second = self._post(port, body)
            self.assertEqual(first, second)

    def test_request_records_bounded_with_truncation_metadata(self) -> None:
        provider = probe.FakeAnthropicProvider()
        document = {"model": "probe-model", "messages": []}
        headers = {"x-api-key": probe.DUMMY_TOKEN}
        for index in range(probe._RECORD_CAP + 10):
            provider._record("POST", "/v1/messages", headers, b"{}", document)
        self.assertEqual(len(provider.requests), probe._RECORD_CAP)
        self.assertEqual(provider.dropped_requests, 10)

    def test_missing_content_length_deterministic_411(self) -> None:
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            status, payload = self._raw_request(
                port,
                b"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Connection: close\r\n\r\n",
            )
            self.assertEqual(status, 411)
            self.assertEqual(
                strict_json.loads(payload)["error"]["type"], "invalid_request_error"
            )
            self.assertEqual(provider.requests, ())

    def test_non_numeric_content_length_deterministic_400(self) -> None:
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            status, _ = self._raw_request(
                port,
                b"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Length: abc\r\nConnection: close\r\n\r\n",
            )
            self.assertEqual(status, 400)
            self.assertEqual(provider.requests, ())

    def test_negative_content_length_deterministic_400(self) -> None:
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            status, _ = self._raw_request(
                port,
                b"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Length: -5\r\nConnection: close\r\n\r\n",
            )
            self.assertEqual(status, 400)

    def test_conflicting_content_length_deterministic_400(self) -> None:
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            status, _ = self._raw_request(
                port,
                b"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Length: 5\r\nContent-Length: 6\r\n"
                b"Connection: close\r\n\r\n",
            )
            self.assertEqual(status, 400)

    def test_oversize_content_length_deterministic_413(self) -> None:
        with probe.FakeAnthropicProvider() as provider:
            port = int(provider.base_url.rsplit(":", 1)[1])
            status, _ = self._raw_request(
                port,
                b"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                + f"Content-Length: {probe._MAX_BODY_BYTES + 1}\r\n".encode()
                + b"Connection: close\r\n\r\n",
            )
            self.assertEqual(status, 413)
            self.assertEqual(provider.requests, ())


class TrustedContractTests(ProbeTestCase):
    def test_derives_real_spec_from_contract(self) -> None:
        contract = {
            "claude": {
                "executable": {"resolved_path": "/x/2.1.217", "sha256": "a" * 64}
            }
        }
        trusted = probe.trusted_from_contract(contract)
        self.assertEqual(trusted.resolved_path, Path("/x/2.1.217"))
        self.assertEqual(trusted.sha256, "a" * 64)
        self.assertFalse(trusted.fake)

    def test_rejects_missing_identity(self) -> None:
        with self.assertRaisesRegex(ProbeError, "claude.executable"):
            probe.trusted_from_contract({"claude": {}})

    def test_rejects_malformed_digest(self) -> None:
        contract = {
            "claude": {"executable": {"resolved_path": "/x", "sha256": "zz"}}
        }
        with self.assertRaisesRegex(ProbeError, "sha256"):
            probe.trusted_from_contract(contract)


class RunNativeTests(ProbeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.fixture = probe.build_fixture(self.fixture_root, environ=self.environ)

    def test_refuses_relative_executable(self) -> None:
        with self.assertRaisesRegex(ProbeError, "absolute"):
            probe.run_native(
                [],
                trusted=probe.TrustedExecutable(
                    Path("relative/fake"), "0" * 64, fake=True
                ),
                fixture=self.fixture,
            )

    def test_refuses_non_normal_executable_path(self) -> None:
        script = self._write_fake_executable()
        weird = script.parent / ".." / "bin" / script.name
        with self.assertRaisesRegex(ProbeError, "non-normal"):
            probe.run_native(
                [], trusted=self._trusted(weird), fixture=self.fixture
            )

    def test_refuses_missing_executable(self) -> None:
        missing = self.root / "bin" / "nope"
        with self.assertRaisesRegex(ProbeError, "does not exist"):
            probe.run_native(
                [],
                trusted=probe.TrustedExecutable(missing, "0" * 64, fake=True),
                fixture=self.fixture,
            )

    def test_refuses_directory_executable(self) -> None:
        with self.assertRaisesRegex(ProbeError, "regular file"):
            probe.run_native(
                [],
                trusted=probe.TrustedExecutable(
                    self.fixture.root, "0" * 64, fake=True
                ),
                fixture=self.fixture,
            )

    def test_refuses_non_executable_file(self) -> None:
        plain = self.root / "bin" / "plain"
        plain.parent.mkdir(parents=True, exist_ok=True)
        plain.write_text("x")
        plain.chmod(0o600)
        with self.assertRaisesRegex(ProbeError, "not executable"):
            probe.run_native(
                [], trusted=self._trusted(plain), fixture=self.fixture
            )

    def test_refuses_group_other_writable_executable(self) -> None:
        script = self._write_fake_executable()
        script.chmod(0o775)
        with self.assertRaisesRegex(ProbeError, "writable"):
            probe.run_native(
                [], trusted=self._trusted(script), fixture=self.fixture
            )

    def test_refuses_symlink_executable(self) -> None:
        script = self._write_fake_executable()
        link = self.root / "bin" / "linked-claude"
        link.symlink_to(script)
        with self.assertRaisesRegex(ProbeError, "symlink"):
            probe.run_native(
                [], trusted=self._trusted(link), fixture=self.fixture
            )

    def test_refuses_hash_mismatch(self) -> None:
        script = self._write_fake_executable()
        with self.assertRaisesRegex(ProbeError, "hash"):
            probe.run_native(
                [],
                trusted=self._trusted(script, digest="0" * 64),
                fixture=self.fixture,
            )

    def test_refuses_malformed_spec_digest(self) -> None:
        script = self._write_fake_executable()
        with self.assertRaisesRegex(ProbeError, "sha256"):
            probe.run_native(
                [],
                trusted=self._trusted(script, digest="not-hex"),
                fixture=self.fixture,
            )

    def test_refuses_non_positive_timeout(self) -> None:
        script = self._write_fake_executable()
        with self.assertRaisesRegex(ProbeError, "positive"):
            probe.run_native(
                [], trusted=self._trusted(script), fixture=self.fixture, timeout=0
            )

    def test_real_native_spec_fails_closed_without_allow_real(self) -> None:
        script = self._write_fake_executable()
        with self.assertRaisesRegex(ProbeError, "refusing real native execution without allow_real"):
            probe.run_native(
                [],
                trusted=self._trusted(script, fake=False),
                fixture=self.fixture,
            )

    def test_runs_fake_executable_with_disposable_env(self) -> None:
        script = self._write_fake_executable()
        with mock.patch.dict(os.environ, {"PROBE_AMBIENT_MARKER": "present"}):
            result = probe.run_native(
                [], trusted=self._trusted(script), fixture=self.fixture, timeout=15
            )
        self.assertFalse(result.timed_out)
        self.assertEqual(result.returncode, 0)
        self.assertIn(f"HOME={self.fixture.home}", result.stdout)
        self.assertIn(
            f"CLAUDE_CONFIG_DIR={self.fixture.claude_config_dir}", result.stdout
        )
        # The ambient environment never leaks into the probed process.
        self.assertIn("AMBIENT=absent", result.stdout)
        self.assertIn("STATUS=200", result.stdout)
        self.assertIn("PROBE-OK", result.stdout)
        self.assertEqual(len(result.requests), 1)
        record = result.requests[0]
        self.assertEqual(record.method, "POST")
        self.assertEqual(record.path, "/v1/messages")
        self.assertEqual(record.model, "probe-model")
        self.assertEqual(record.auth, "dummy")

    def test_run_native_threads_typed_compaction_policy(self) -> None:
        script = self._write_fake_executable()
        result = probe.run_native(
            [],
            trusted=self._trusted(script),
            fixture=self.fixture,
            timeout=15,
            compaction=probe.ProbeCompactionPolicy(
                auto_compact_window=983616,
                auto_compact_percent=90,
                max_context_tokens=372000,
            ),
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("AUTO_WINDOW=983616", result.stdout)
        self.assertIn("AUTO_PERCENT=90", result.stdout)
        self.assertIn("MAX_CONTEXT=372000", result.stdout)

    def test_run_native_pty_drives_bounded_interactions(self) -> None:
        script = self._write_fake_executable("pty-client", _PTY_BODY)
        result = probe.run_native_pty(
            [],
            (
                probe.PTYInteraction(b"READY", b"hello\n"),
                probe.PTYInteraction(b"ACK=hello", b"exit\n"),
            ),
            trusted=self._trusted(script),
            fixture=self.fixture,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("DONE=exit", result.stdout)
        self.assertEqual(result.requests, ())

    def test_run_native_pty_preserves_markers_after_match(self) -> None:
        script = self._write_fake_executable(
            "pty-buffered-markers",
            """import os

os.write(1, b"READY-NEXT\\n")
first = input()
second = input()
print("DONE=" + first + ":" + second, flush=True)
""",
        )
        result = probe.run_native_pty(
            [],
            (
                probe.PTYInteraction(
                    b"READY", b"hello\n", preserve_after_wait=True
                ),
                probe.PTYInteraction(b"NEXT", b"exit\n"),
            ),
            trusted=self._trusted(script),
            fixture=self.fixture,
            timeout=15,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("DONE=hello:exit", result.stdout)

    def test_caller_owned_provider_keeps_lifecycle(self) -> None:
        script = self._write_fake_executable()
        with probe.FakeAnthropicProvider() as provider:
            result = probe.run_native(
                [],
                trusted=self._trusted(script),
                fixture=self.fixture,
                provider=provider,
                timeout=15,
            )
            self.assertEqual(result.returncode, 0)
            self.assertTrue(provider.base_url.startswith("http://127.0.0.1:"))
            self.assertEqual(len(provider.requests), 1)
            self.assertEqual(result.requests, provider.requests)

    def test_nonzero_exit_propagates(self) -> None:
        script = self._write_fake_executable("exiter", "import sys\nsys.exit(3)\n")
        result = probe.run_native(
            [], trusted=self._trusted(script), fixture=self.fixture, timeout=15
        )
        self.assertEqual(result.returncode, 3)
        self.assertFalse(result.timed_out)

    def test_timeout_kills_process_group_and_reaps(self) -> None:
        pidfile = self.fixture_root / "grandchild.pid"
        spawner = self._write_fake_executable("spawner", _SPAWNER_BODY)
        result = probe.run_native(
            [str(pidfile)],
            trusted=self._trusted(spawner),
            fixture=self.fixture,
            timeout=0.5,
        )
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.returncode)
        grandchild = int(pidfile.read_text())
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            self.fail("grandchild survived the process-group kill")
        # Cleanup: the timed-out run left no listener or zombie state behind;
        # a fresh run works deterministically.
        script = self._write_fake_executable()
        followup = probe.run_native(
            [], trusted=self._trusted(script), fixture=self.fixture, timeout=15
        )
        self.assertEqual(followup.returncode, 0)


class EvidenceTests(ProbeTestCase):
    def test_write_evidence_private_strict(self) -> None:
        fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        path = probe.write_evidence(fixture, "p1-sample", {"version": 1, "ok": True})
        self.assertEqual(path.parent, fixture.root / "evidence")
        self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o600)
        self.assertEqual(
            strict_json.loads(path.read_bytes()), {"version": 1, "ok": True}
        )

    def test_write_evidence_validates_name(self) -> None:
        fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        with self.assertRaises(state.StateError):
            probe.write_evidence(fixture, "Bad Name!", {})


class ProbeCliTests(ProbeTestCase):
    def test_init_prints_path_only_manifest(self) -> None:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = probe.probe_cli(["init"], self._flags(), [], environ=self.environ)
        self.assertEqual(code, 0)
        manifest = strict_json.loads(stdout.getvalue())
        self.assertEqual(manifest["root"], str(self.fixture_root))
        self.assertEqual(
            manifest["dirs"]["claude_config_dir"],
            str(self.fixture_root / "claude-config"),
        )
        self.assertNotIn(probe.DUMMY_TOKEN, stdout.getvalue())

    def test_init_rejects_argv_tail(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.probe_cli(
                ["init"], self._flags(), ["/bin/x"], environ=self.environ
            )
        self.assertEqual(code, 2)
        self.assertIn("no executable argv", stderr.getvalue())

    def test_run_evidence_is_metadata_only(self) -> None:
        script = self._write_fake_executable()
        flags = {**self._flags(), "native-contract": str(self._contract_file(script))}
        fake_result = probe.NativeRunResult(
            argv=(str(script), "--distinctive-arg-xyz"),
            returncode=0,
            timed_out=False,
            stdout="distinctive-stdout-xyz",
            stderr="",
            requests=(),
        )
        with mock.patch.object(probe, "run_native", return_value=fake_result):
            with contextlib.redirect_stdout(io.StringIO()):
                code = probe.probe_cli(
                    ["run"], flags, ["--distinctive-arg-xyz"], environ=self.environ
                )
        self.assertEqual(code, 0)
        raw = (self.fixture_root / "evidence" / "last-run.json").read_bytes()
        # Raw argv and stdio text never persist, even on the fake-only path.
        self.assertNotIn(b"distinctive-arg-xyz", raw)
        self.assertNotIn(b"distinctive-stdout-xyz", raw)
        evidence = strict_json.loads(raw)
        self.assertNotIn("argv", evidence["run"])
        self.assertEqual(evidence["run"]["argv_count"], 2)
        self.assertEqual(evidence["run"]["returncode"], 0)
        self.assertTrue(evidence["run"]["stdout_sha256"])

    def test_run_requires_native_contract(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.probe_cli(["run"], self._flags(), [], environ=self.environ)
        self.assertEqual(code, 2)
        self.assertIn("--native-contract", stderr.getvalue())

    def test_run_real_contract_fails_closed_without_consent(self) -> None:
        script = self._write_fake_executable()
        flags = {**self._flags(), "native-contract": str(self._contract_file(script))}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.probe_cli(["run"], flags, [], environ=self.environ)
        self.assertEqual(code, 2)
        self.assertIn("refusing real native execution without allow_real", stderr.getvalue())
        self.assertFalse((self.fixture_root / "evidence").exists())

    def test_run_rejects_malformed_contract(self) -> None:
        bad = self.root / "bad-contract.json"
        bad.write_bytes(strict_json.canonical_file_bytes({"claude": {}}))
        flags = {**self._flags(), "native-contract": str(bad)}
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.probe_cli(["run"], flags, [], environ=self.environ)
        self.assertEqual(code, 2)
        self.assertIn("claude.executable", stderr.getvalue())

    def test_run_rejects_contract_hash_mismatch(self) -> None:
        script = self._write_fake_executable()
        flags = {
            **self._flags(),
            "native-contract": str(self._contract_file(script, digest="0" * 64)),
        }
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            code = probe.probe_cli(["run"], flags, [], environ=self.environ)
        self.assertEqual(code, 2)
        # Real (contract) specs are refused before any hashing without
        # --allow-real-execution.
        self.assertIn("without allow_real", stderr.getvalue())
        self.assertFalse((self.fixture_root / "evidence").exists())

    def test_dev_main_run_fails_closed_without_consent(self) -> None:
        script = self._write_fake_executable()
        contract = self._contract_file(script)
        # Scrub the ambient environment exactly as the gated operator
        # invocation requires; probe_cli reads os.environ by default.
        scrubbed = {"HOME": self.environ["HOME"], "PATH": "/usr/bin:/bin"}
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, scrubbed, clear=True):
            with contextlib.redirect_stderr(stderr):
                code = dev.main(
                    [
                        "probe",
                        "run",
                        "--allow-local-claude",
                        "--fixture-root",
                        str(self.fixture_root),
                        "--native-contract",
                        str(contract),
                        "--",
                    ]
                )
        self.assertEqual(code, 2)
        self.assertIn("refusing real native execution without allow_real", stderr.getvalue())
        self.assertFalse((self.fixture_root / "evidence").exists())

    def test_dev_main_probe_split_preserves_dashed_argv(self) -> None:
        captured: dict = {}

        def fake_cli(positionals, flags, run_argv, *, environ=None):
            captured["positionals"] = positionals
            captured["flags"] = flags
            captured["run_argv"] = run_argv
            return 0

        with mock.patch.object(probe, "probe_cli", fake_cli):
            code = dev.main(
                [
                    "probe",
                    "run",
                    "--allow-local-claude",
                    "--fixture-root",
                    "/x",
                    "--",
                    "/bin/fake",
                    "--resume",
                    "1234",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(captured["positionals"], ["run"])
        self.assertEqual(captured["run_argv"], ["/bin/fake", "--resume", "1234"])
        self.assertEqual(captured["flags"]["fixture-root"], "/x")


class DaemonDomainGateTests(ProbeTestCase):
    """Config-root daemon-domain gate: CLAUDE_CONFIG_DIR allowance matrix."""

    def setUp(self) -> None:
        super().setUp()
        self.fixture = probe.build_fixture(self.fixture_root, environ=self.environ)

    def _allow(self, fixture: probe.ProbeFixture, environ: dict | None = None) -> None:
        probe._assert_real_run_allowed(
            fixture, self.environ if environ is None else environ
        )

    def test_valid_fixture_passes_the_allowance(self) -> None:
        self._allow(self.fixture)

    def test_config_dir_equal_to_root_refused(self) -> None:
        variant = replace(self.fixture, claude_config_dir=self.fixture.root)
        with self.assertRaisesRegex(ProbeError, "not inside the disposable fixture"):
            self._allow(variant)

    def test_config_dir_outside_root_refused(self) -> None:
        outside = state.ensure_private_dir(self.root / "elsewhere-config")
        variant = replace(self.fixture, claude_config_dir=outside)
        with self.assertRaisesRegex(ProbeError, "not inside the disposable fixture"):
            self._allow(variant)

    def test_config_dir_symlink_escape_refused(self) -> None:
        target = state.ensure_private_dir(self.root / "escaped-config")
        link = self.fixture_root / "linked-config"
        link.symlink_to(target, target_is_directory=True)
        variant = replace(self.fixture, claude_config_dir=link)
        with self.assertRaisesRegex(ProbeError, "not inside the disposable fixture"):
            self._allow(variant)

    def test_missing_config_dir_refused(self) -> None:
        variant = replace(
            self.fixture, claude_config_dir=self.fixture_root / "no-such-config"
        )
        with self.assertRaisesRegex(ProbeError, "does not exist"):
            self._allow(variant)

    def test_non_private_config_dir_refused(self) -> None:
        loose = self.fixture_root / "loose-config"
        loose.mkdir(mode=0o755)
        variant = replace(self.fixture, claude_config_dir=loose)
        with self.assertRaisesRegex(ProbeError, "owner-private"):
            self._allow(variant)

    def test_ambient_provider_credentials_refused(self) -> None:
        ambient = {**self.environ, "ANTHROPIC_API_KEY": "dummy-secret-value"}
        with self.assertRaises(ProbeError) as raised:
            self._allow(self.fixture, ambient)
        self.assertIn("ANTHROPIC_API_KEY", str(raised.exception))
        self.assertNotIn("dummy-secret-value", str(raised.exception))

    def test_allowance_runs_before_executable_hashing(self) -> None:
        # A bogus spec digest must not be reached when the config-root gate
        # already refuses: the refusal names the config rule, never a hash.
        script = self._write_fake_executable()
        outside = state.ensure_private_dir(self.root / "elsewhere-config")
        variant = replace(self.fixture, claude_config_dir=outside)
        with self.assertRaisesRegex(ProbeError, "not inside the disposable fixture"):
            probe.run_native(
                [],
                trusted=self._trusted(script, fake=False, digest="0" * 64),
                fixture=variant,
                allow_real=True,
                environ=self.environ,
            )


class DaemonDomainSnapshotTests(ProbeTestCase):
    """Live-domain snapshot, tamper diff, and fail-closed enforcement."""

    def setUp(self) -> None:
        super().setUp()
        self.fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        self.domain = state.ensure_private_dir(self.root / "cc-daemon-test")

    def _snapshot(
        self, entries: tuple[probe.DaemonDomainEntry, ...], *, present: bool = True
    ) -> probe.DaemonDomainSnapshot:
        return probe.DaemonDomainSnapshot(
            self.domain, present, 100 if present else None, entries
        )

    def test_expected_subdomain_is_config_root_hash_prefix(self) -> None:
        resolved = os.path.realpath(self.fixture.claude_config_dir)
        expected = hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:8]
        self.assertEqual(
            probe.expected_daemon_subdomain(self.fixture.claude_config_dir), expected
        )

    def test_snapshot_absent_domain_tolerated(self) -> None:
        snap = probe.snapshot_daemon_domain(self.root / "absent-domain")
        self.assertFalse(snap.present)
        self.assertEqual(snap.entries, ())
        self.assertIsNone(snap.dir_mtime_ns)

    def test_snapshot_records_entry_kinds_sorted(self) -> None:
        (self.domain / "z-file").write_text("x")
        (self.domain / "a-dir").mkdir()
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.domain / "m-sock"))
            snap = probe.snapshot_daemon_domain(self.domain)
        finally:
            listener.close()
        self.assertTrue(snap.present)
        names = [entry.name for entry in snap.entries]
        self.assertEqual(names, ["a-dir", "m-sock", "z-file"])
        kinds = {entry.name: entry.kind for entry in snap.entries}
        self.assertEqual(kinds, {"a-dir": "dir", "m-sock": "sock", "z-file": "file"})

    def test_domain_changes_detects_added_removed_changed(self) -> None:
        before = self._snapshot(
            (
                probe.DaemonDomainEntry("keep", "file", 1, 1),
                probe.DaemonDomainEntry("drop", "file", 1, 1),
                probe.DaemonDomainEntry("mutate", "file", 1, 1),
            )
        )
        after = self._snapshot(
            (
                probe.DaemonDomainEntry("keep", "file", 1, 1),
                probe.DaemonDomainEntry("mutate", "file", 2, 1),
                probe.DaemonDomainEntry("new", "dir", 1, 0),
            )
        )
        self.assertEqual(
            probe._domain_changes(before, after),
            ("added:new", "removed:drop", "changed:mutate"),
        )

    def test_domain_changes_detects_appear_vanish_and_dir_mtime(self) -> None:
        entry = probe.DaemonDomainEntry("keep", "file", 1, 1)
        present = self._snapshot((entry,))
        absent = self._snapshot((), present=False)
        self.assertEqual(
            probe._domain_changes(absent, present),
            ("domain-appeared", "added:keep"),
        )
        self.assertEqual(
            probe._domain_changes(present, absent),
            ("domain-vanished", "removed:keep"),
        )
        churned = probe.DaemonDomainSnapshot(self.domain, True, 200, (entry,))
        self.assertEqual(
            probe._domain_changes(present, churned), ("domain-dir-mtime",)
        )

    def test_domain_changes_refuses_mismatched_domains(self) -> None:
        other = probe.DaemonDomainSnapshot(self.root / "other", True, 1, ())
        with self.assertRaisesRegex(ProbeError, "different domains"):
            probe._domain_changes(self._snapshot(()), other)

    def test_enforce_untouched_returns_metadata_observation(self) -> None:
        before = probe.snapshot_daemon_domain(self.domain)
        after = probe.snapshot_daemon_domain(self.domain)
        observation = probe._enforce_live_domain_untouched(
            before, after, fixture=self.fixture
        )
        self.assertTrue(observation.unchanged)
        self.assertEqual(observation.domain, str(self.domain))
        self.assertTrue(observation.present_before)
        self.assertTrue(observation.present_after)
        self.assertEqual(
            observation.expected_fixture_subdomain,
            probe.expected_daemon_subdomain(self.fixture.claude_config_dir),
        )
        self.assertFalse(observation.fixture_subdomain_observed)

    def test_enforce_tamper_fails_closed_without_remediation(self) -> None:
        before = probe.snapshot_daemon_domain(self.domain)
        rogue = self.domain / "rogue-entry"
        rogue.mkdir()
        after = probe.snapshot_daemon_domain(self.domain)
        with self.assertRaises(ProbeError) as raised:
            probe._enforce_live_domain_untouched(before, after, fixture=self.fixture)
        message = str(raised.exception)
        self.assertIn("live daemon domain touched", message)
        self.assertIn("added:rogue-entry", message)
        self.assertIn("no remediation performed", message)
        # A foreign entry is never modified by the tripwire.
        self.assertTrue(rogue.is_dir())

    def test_enforce_fixture_subdomain_is_never_remediated(self) -> None:
        before = probe.snapshot_daemon_domain(self.domain)
        subdomain = self.domain / probe.expected_daemon_subdomain(
            self.fixture.claude_config_dir
        )
        subdomain.mkdir()
        after = probe.snapshot_daemon_domain(self.domain)
        with self.assertRaises(ProbeError) as raised:
            probe._enforce_live_domain_untouched(before, after, fixture=self.fixture)
        message = str(raised.exception)
        self.assertIn("live daemon domain touched", message)
        self.assertIn("observe-only; no remediation performed", message)
        # Even a fixture-looking entry lives in the live domain and is never
        # modified by the probe.
        self.assertTrue(subdomain.exists())

    def test_fixture_daemon_domains_lists_siblings_only(self) -> None:
        (self.root / "cc-daemon-deadbeef-1000").mkdir()
        (self.root / "cc-daemon-0123456789abcdef").mkdir()
        (self.root / "not-a-domain").mkdir()
        names = probe.fixture_daemon_domains(self.domain)
        self.assertEqual(
            names, ("cc-daemon-0123456789abcdef", "cc-daemon-deadbeef-1000")
        )

    def test_fixture_daemon_domains_tolerates_absent_parent(self) -> None:
        missing = self.root / "no-such-parent" / "cc-daemon-test"
        self.assertEqual(probe.fixture_daemon_domains(missing), ())

    def test_enforce_records_fixture_sibling_observation(self) -> None:
        expected = probe.expected_daemon_subdomain(self.fixture.claude_config_dir)
        sibling = f"cc-daemon-{expected}-4242"
        (self.root / sibling).mkdir()
        before = probe.snapshot_daemon_domain(self.domain)
        after = probe.snapshot_daemon_domain(self.domain)
        observation = probe._enforce_live_domain_untouched(
            before,
            after,
            fixture=self.fixture,
            fixture_domains_before=(),
            fixture_domains_after=(sibling,),
        )
        self.assertTrue(observation.fixture_subdomain_observed)
        self.assertEqual(observation.fixture_domains_after, (sibling,))
        self.assertEqual(observation.fixture_domains_before, ())


class RunNativeAllowRealTests(ProbeTestCase):
    """run_native(allow_real=True): allow/refuse matrix and live tripwire."""

    def setUp(self) -> None:
        super().setUp()
        self.fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        self.live_domain = state.ensure_private_dir(self.root / "cc-daemon-test")

    def _real_trusted(self, script: Path) -> probe.TrustedExecutable:
        return self._trusted(script, fake=False)

    def _run(
        self,
        script: Path,
        args: list | None = None,
        **overrides,
    ) -> probe.NativeRunResult:
        options = {
            "trusted": self._real_trusted(script),
            "fixture": self.fixture,
            "timeout": 15,
            "allow_real": True,
            "environ": self.environ,
            "live_daemon_domain": self.live_domain,
        }
        options.update(overrides)
        return probe.run_native(args or [], **options)

    def test_allow_real_runs_with_clean_tripwire(self) -> None:
        script = self._write_fake_executable()
        result = self._run(script)
        self.assertEqual(result.returncode, 0)
        self.assertIn("STATUS=200", result.stdout)
        self.assertIsNotNone(result.daemon)
        assert result.daemon is not None
        self.assertTrue(result.daemon.unchanged)
        self.assertEqual(result.daemon.domain, str(self.live_domain))
        self.assertFalse(result.daemon.fixture_subdomain_observed)
        self.assertEqual(result.daemon.fixture_domains_after, ())

    def test_allow_real_refuses_config_dir_outside_fixture(self) -> None:
        script = self._write_fake_executable()
        outside = state.ensure_private_dir(self.root / "elsewhere-config")
        variant = replace(self.fixture, claude_config_dir=outside)
        with self.assertRaisesRegex(ProbeError, "not inside the disposable fixture"):
            self._run(script, fixture=variant)

    def test_allow_real_refuses_ambient_credentials(self) -> None:
        script = self._write_fake_executable()
        ambient = {**self.environ, "OPENAI_API_KEY": "dummy-secret-value"}
        with self.assertRaises(ProbeError) as raised:
            self._run(script, environ=ambient)
        self.assertIn("OPENAI_API_KEY", str(raised.exception))
        self.assertNotIn("dummy-secret-value", str(raised.exception))

    def test_allow_real_requires_the_loopback_fake_provider(self) -> None:
        script = self._write_fake_executable()
        with self.assertRaisesRegex(ProbeError, "must be the loopback fake"):
            self._run(script, provider=object())

    def test_allow_real_tamper_fails_closed(self) -> None:
        body = (
            "import os\n"
            "import sys\n"
            "os.makedirs(sys.argv[1], exist_ok=True)\n"
        )
        script = self._write_fake_executable("tamper-claude", body)
        rogue = self.live_domain / "rogue-entry"
        with self.assertRaisesRegex(ProbeError, "live daemon domain touched"):
            self._run(script, args=[str(rogue)])
        # Foreign entries are evidence, never remediated.
        self.assertTrue(rogue.is_dir())

    def test_allow_real_absent_live_domain_tolerated(self) -> None:
        script = self._write_fake_executable()
        result = self._run(script, live_daemon_domain=self.root / "absent-domain")
        self.assertEqual(result.returncode, 0)
        self.assertIsNotNone(result.daemon)
        assert result.daemon is not None
        self.assertTrue(result.daemon.unchanged)
        self.assertFalse(result.daemon.present_before)
        self.assertFalse(result.daemon.present_after)

    def test_allow_real_records_fixture_sibling_domain(self) -> None:
        expected = probe.expected_daemon_subdomain(self.fixture.claude_config_dir)
        sibling = f"cc-daemon-{expected}-1000"
        (self.root / sibling).mkdir()
        script = self._write_fake_executable()
        result = self._run(script)
        self.assertEqual(result.returncode, 0)
        self.assertIsNotNone(result.daemon)
        assert result.daemon is not None
        self.assertTrue(result.daemon.unchanged)
        self.assertTrue(result.daemon.fixture_subdomain_observed)
        self.assertEqual(result.daemon.fixture_domains_before, (sibling,))
        self.assertEqual(result.daemon.fixture_domains_after, (sibling,))


if __name__ == "__main__":
    unittest.main()
