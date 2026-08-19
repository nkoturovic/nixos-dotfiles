"""Offline disposable-proxy and proxy-control contract tests.

Loopback only: the proxy loads the v2-rendered config and routes to a fake
upstream; no external provider is ever contacted. Proxy-control tests use
isolated homes and injected exec; the real binary is required only for the
disposable rig, which skips with an explicit boundary when unavailable.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from claude_multi import catalog, proxy, render, state
from claude_multi.proxy import ProxyError


CATALOG_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_TOKEN = "a" * 64
DUMMY_KIMI = "dummy-kimi-key"


def _find_binary() -> str | None:
    for candidate in (
        shutil.which("cli-proxy-api"),
        "/home/kotur/.nix-profile/bin/cli-proxy-api",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _UpstreamCapture(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def log_message(self, *_args) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        type(self).requests.append(
            {
                "path": self.path,
                "x-api-key": self.headers.get("x-api-key"),
                "authorization": self.headers.get("authorization"),
                "body": body,
            }
        )
        if '"trigger-error-400"' in body:
            payload = {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "fake upstream rejection",
                },
            }
            data = json.dumps(payload).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        if '"stream": true' in body or '"stream":true' in body:
            chunks = [
                {"type": "message_start", "message": {"model": "k3"}},
                {"type": "content_block_delta", "delta": {"text": "FAKE"}},
                {"type": "content_block_delta", "delta": {"text": "_OK"}},
                {"type": "message_stop"},
            ]
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return
        payload = {
            "id": "msg_fake",
            "type": "message",
            "role": "assistant",
            "model": "k3",
            "content": [{"type": "text", "text": "FAKE_OK"}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 7, "output_tokens": 3},
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _wait_ready(base_url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            request = urllib.request.Request(
                base_url + "/healthz",
                headers={"Authorization": f"Bearer {GATEWAY_TOKEN}"},
            )
            with urllib.request.urlopen(request, timeout=1) as response:
                if response.status == 200:
                    return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError("disposable proxy did not become ready")


class DisposableProxyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.binary = _find_binary()
        if self.binary is None:
            self.skipTest(
                "boundary: pinned cli-proxy-api binary not available; "
                "disposable proxy contract skipped"
            )
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-proxy-"))
        self.addCleanup(self._cleanup)
        self.process: subprocess.Popen | None = None
        self.upstream: ThreadingHTTPServer | None = None
        self.base_url: str | None = None

    def _cleanup(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self.upstream is not None:
            self.upstream.shutdown()
            self.upstream.server_close()
        shutil.rmtree(self.root, ignore_errors=True)

    def _start_proxy(self) -> None:
        _UpstreamCapture.requests = []
        upstream_port = _free_port()
        proxy_port = _free_port()
        self.upstream = ThreadingHTTPServer(("127.0.0.1", upstream_port), _UpstreamCapture)
        threading.Thread(target=self.upstream.serve_forever, daemon=True).start()

        bundle = catalog.load_catalog(CATALOG_ROOT)
        import copy

        gateway = copy.deepcopy(bundle.docs["gateway"])
        gateway["gateway"]["base_url"] = f"http://127.0.0.1:{proxy_port}"
        providers = copy.deepcopy(bundle.docs["providers"]["providers"])
        providers["kimi"]["transport"]["base_url"] = (
            f"http://127.0.0.1:{upstream_port}/coding"
        )
        result = render.render_config(
            gateway,
            providers,
            bundle.docs["models"]["models"],
            home=self.root,
            gateway_token=GATEWAY_TOKEN,
            resolve_secret=lambda name: DUMMY_KIMI,
        )
        config_path = self.root / "config.yaml"
        state.atomic_write(config_path, result.yaml.encode("utf-8"))

        env = {
            "HOME": str(self.root),
            "PATH": "/usr/bin:/bin",
        }
        self.process = subprocess.Popen(
            [self.binary, "--config", str(config_path), "--local-model"],
            cwd=self.root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.base_url = f"http://127.0.0.1:{proxy_port}"
        _wait_ready(self.base_url)

    def _request(self, base_url: str, path: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            base_url + path,
            data=data,
            headers={
                "Authorization": f"Bearer {GATEWAY_TOKEN}",
                "Anthropic-Version": "2023-06-01",
                "Content-Type": "application/json",
                # CLIProxyAPI enriches model metadata only for Claude CLI clients.
                "User-Agent": "claude-cli/2.1.216 (external, cli)",
            },
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_rendered_config_contracts_with_fake_upstream(self) -> None:
        self._start_proxy()
        base_url = self.base_url

        models = self._request(base_url, "/v1/models")
        entries = {item.get("id"): item for item in models.get("data", [])}
        self.assertIn("claude-multi-kimi-k3", entries)
        self.assertEqual(entries["claude-multi-kimi-k3"].get("owned_by"), "moonshot")
        self.assertEqual(
            entries["claude-multi-kimi-k3"].get("max_input_tokens"), 1000000
        )

        message = self._request(
            base_url,
            "/v1/messages",
            {
                "model": "claude-multi-kimi-k3",
                "max_tokens": 64,
                "thinking": {"type": "enabled", "budget_tokens": 1024},
                "messages": [{"role": "user", "content": "fake test"}],
            },
        )
        self.assertTrue(message.get("content"), "response mapped")

        kimi_hits = [
            request
            for request in _UpstreamCapture.requests
            if urllib.parse.urlsplit(request["path"]).path == "/coding/v1/messages"
        ]
        self.assertTrue(kimi_hits, "upstream observed the kimi route")
        self.assertEqual(kimi_hits[0]["x-api-key"], DUMMY_KIMI)
        body = json.loads(kimi_hits[0]["body"])
        self.assertNotIn("thinking", body, "payload filter strips top-level thinking")
        self.assertEqual(body.get("model"), "k3", "wire model mapping")
        self.assertEqual(body.get("output_config", {}).get("effort"), "max")

        self.process.terminate()
        self.process.wait(timeout=5)
        self.assertIsNotNone(self.process.poll(), "no orphan proxy process")

    def test_sse_streaming_passthrough(self) -> None:
        self._start_proxy()
        request = urllib.request.Request(
            self.base_url + "/v1/messages",
            data=json.dumps(
                {
                    "model": "claude-multi-kimi-k3",
                    "max_tokens": 64,
                    "stream": True,
                    "messages": [{"role": "user", "content": "stream test"}],
                }
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {GATEWAY_TOKEN}",
                "Anthropic-Version": "2023-06-01",
                "Content-Type": "application/json",
                "User-Agent": "claude-cli/2.1.216 (external, cli)",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read().decode("utf-8")
        self.assertIn("text/event-stream", content_type)
        self.assertIn('"FAKE"', body)
        self.assertIn('"_OK"', body)
        self.assertIn("[DONE]", body)

    def test_upstream_error_mapping(self) -> None:
        self._start_proxy()
        request = urllib.request.Request(
            self.base_url + "/v1/messages",
            data=json.dumps(
                {
                    "model": "claude-multi-kimi-k3",
                    "max_tokens": 64,
                    "messages": [{"role": "user", "content": "trigger-error-400"}],
                }
            ).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {GATEWAY_TOKEN}",
                "Anthropic-Version": "2023-06-01",
                "Content-Type": "application/json",
                "User-Agent": "claude-cli/2.1.216 (external, cli)",
            },
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(request, timeout=10)
        self.assertEqual(raised.exception.code, 400)
        error_body = raised.exception.read().decode("utf-8")
        raised.exception.close()
        self.assertIn("invalid_request_error", error_body)
        self.assertIn("fake upstream rejection", error_body)


if __name__ == "__main__":
    unittest.main()


class ProxyControlTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-proxyctl-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.home = self.root / "home"
        self.home.mkdir()
        os.chmod(self.home, 0o700)
        self.secrets = self.root / "secrets"
        state.ensure_private_dir(self.secrets)
        self.secret_file = self.secrets / "claude.env"
        self.environ = {
            "HOME": str(self.home),
            "CLAUDE_MULTI_SECRET_ENV": str(self.secret_file),
            "CLAUDE_MULTI_ASSETS": str(CATALOG_ROOT),
        }
        self.binary = self.root / "bin" / "cli-proxy-api"
        self.binary.parent.mkdir()
        self.binary.write_bytes(b"#!/bin/fake\n")
        self.binary.chmod(0o755)
        self.environ["CLAUDE_MULTI_PROXY_BIN"] = str(self.binary)

    def _write_secret(self, content: bytes = b"KIMI_CLAUDE_API_KEY=test-dummy-value-123\n"):
        state.atomic_write(self.secret_file, content)

    def _init(self) -> str:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = proxy.cmd_init([], environ=self.environ)
        self.assertEqual(code, 0)
        return buffer.getvalue()


class SecretParsingTests(ProxyControlTestCase):
    def test_valid_assignments(self) -> None:
        self._write_secret(b'KIMI_CLAUDE_API_KEY=abc-123_X.Y\nOTHER="quoted:value"\n')
        values = proxy.parse_secret_env(self.secret_file)
        self.assertEqual(values["KIMI_CLAUDE_API_KEY"], "abc-123_X.Y")
        self.assertEqual(values["OTHER"], "quoted:value")

    def test_export_prefix_and_single_quotes(self) -> None:
        self._write_secret(b"export KIMI_CLAUDE_API_KEY='abc+def/ghi='\n")
        values = proxy.parse_secret_env(self.secret_file)
        self.assertEqual(values["KIMI_CLAUDE_API_KEY"], "abc+def/ghi=")

    def test_duplicate_assignment_rejected(self) -> None:
        self._write_secret(b"A=11111111\nA=22222222\n")
        with self.assertRaisesRegex(ProxyError, "duplicate assignment"):
            proxy.parse_secret_env(self.secret_file)

    def test_malformed_line_rejected(self) -> None:
        self._write_secret(b"this is not an assignment\n")
        with self.assertRaisesRegex(ProxyError, "malformed assignment"):
            proxy.parse_secret_env(self.secret_file)

    def test_unsafe_characters_rejected(self) -> None:
        self._write_secret(b"A=$(rm -rf /)\n")
        with self.assertRaisesRegex(ProxyError, "unsupported characters"):
            proxy.parse_secret_env(self.secret_file)

    def test_symlink_secret_file_rejected(self) -> None:
        self._write_secret()
        link = self.secrets / "link.env"
        link.symlink_to(self.secret_file)
        with self.assertRaisesRegex(ProxyError, "unavailable or unsafe"):
            proxy.parse_secret_env(link)

    def test_group_readable_secret_file_rejected(self) -> None:
        self._write_secret()
        os.chmod(self.secret_file, 0o640)
        with self.assertRaisesRegex(ProxyError, "unavailable or unsafe"):
            proxy.parse_secret_env(self.secret_file)

    def test_missing_secret_returns_none(self) -> None:
        self.assertIsNone(proxy.resolve_secret("KIMI_CLAUDE_API_KEY", environ=self.environ))
        self._write_secret(b"OTHER=value1234\n")
        self.assertIsNone(proxy.resolve_secret("KIMI_CLAUDE_API_KEY", environ=self.environ))


class InitRenderTests(ProxyControlTestCase):
    def test_init_creates_dirs_token_config_idempotent(self) -> None:
        self._write_secret()
        first = self._init()
        token_path = proxy.config_dir(self.home) / "api-key"
        config_path = proxy.config_dir(self.home) / "config.yaml"
        self.assertTrue(token_path.is_file())
        token = token_path.read_text().strip()
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        import stat as stat_mod

        self.assertEqual(stat_mod.S_IMODE(os.lstat(token_path).st_mode), 0o600)
        self.assertEqual(stat_mod.S_IMODE(os.lstat(config_path).st_mode), 0o600)
        first_bytes = config_path.read_bytes()
        second = self._init()
        self.assertEqual(config_path.read_bytes(), first_bytes)
        self.assertEqual(token_path.read_text().strip(), token)
        self.assertIn("providers available", first)
        self.assertIn("kimi", second)

    def test_init_missing_secret_omits_provider_reports(self) -> None:
        # no secret file at all → kimi omitted, OAuth providers remain
        output = self._init()
        self.assertIn("provider unavailable: kimi", output)
        self.assertIn("anthropic", output)
        self.assertIn("openai", output)
        config = (proxy.config_dir(self.home) / "config.yaml").read_text()
        self.assertNotIn("claude-multi-kimi-k3", config)
        self.assertIn("gpt-multi-sol-high", config)

    def test_no_secret_in_init_output_or_config_artifacts_except_resolved(self) -> None:
        self._write_secret(b"KIMI_CLAUDE_API_KEY=supersecret-value-999\n")
        output = self._init()
        self.assertNotIn("supersecret-value-999", output)
        # the resolved secret appears only inside the mode-0600 rendered config
        config_path = proxy.config_dir(self.home) / "config.yaml"
        self.assertIn("supersecret-value-999", config_path.read_text())
        import stat as stat_mod

        self.assertEqual(stat_mod.S_IMODE(os.lstat(config_path).st_mode), 0o600)

    def test_auth_dir_preserved(self) -> None:
        auth = proxy.state_dir(self.home) / "auth"
        state.ensure_private_dir(auth)
        marker = auth / "oauth-record.json"
        state.atomic_write(marker, b"{}")
        self._init()
        self.assertTrue(marker.is_file())


class StatusTests(ProxyControlTestCase):
    def test_status_not_initialized(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = proxy.cmd_status([], environ=self.environ)
        self.assertEqual(code, 0)
        self.assertIn("not initialized", buffer.getvalue())

    def test_status_initialized_and_running(self) -> None:
        self._init()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = proxy.cmd_status(
                [], environ=self.environ, health_get=lambda _b, _h: 200
            )
        self.assertEqual(code, 0)
        self.assertIn("initialized", buffer.getvalue())
        self.assertIn("running", buffer.getvalue())

    def test_status_loopback_only_and_stopped(self) -> None:
        self._init()
        calls = []

        def record_get(base, path):
            calls.append((base, path))
            raise OSError("refused")

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            proxy.cmd_status([], environ=self.environ, health_get=record_get)
        self.assertIn("stopped", buffer.getvalue())
        self.assertEqual(calls, [("http://127.0.0.1:8317", "/healthz")])

    def test_status_redacts_secrets(self) -> None:
        self._write_secret(b"KIMI_CLAUDE_API_KEY=supersecret-value-999\n")
        self._init()
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            proxy.cmd_status([], environ=self.environ, health_get=lambda _b, _h: 200)
        self.assertNotIn("supersecret-value-999", buffer.getvalue())
        self.assertNotIn("a" * 64, buffer.getvalue())


class RunLoginTests(ProxyControlTestCase):
    def _capture_exec(self):
        captured = {}

        def fake_execve(executable, argv, env):
            captured["executable"] = executable
            captured["argv"] = argv
            captured["env"] = env
            return "EXECUTED"

        return captured, fake_execve

    def test_run_exact_execve_no_wrapper(self) -> None:
        self._write_secret()
        captured, fake = self._capture_exec()
        outcome = proxy.cmd_run([], environ=self.environ, execve=fake)
        self.assertEqual(outcome, "EXECUTED")
        self.assertEqual(captured["executable"], str(self.binary))
        self.assertEqual(captured["argv"][0], str(self.binary))
        self.assertEqual(captured["argv"][1], "--config")
        self.assertTrue(captured["argv"][2].endswith("config.yaml"))
        self.assertEqual(captured["argv"][3], "--local-model")
        self.assertEqual(len(captured["argv"]), 4)

    def test_login_execve_exact(self) -> None:
        for command, flag in (("claude-login", "--claude-login"), ("codex-device-login", "--codex-device-login")):
            with self.subTest(command=command):
                captured, fake = self._capture_exec()
                outcome = proxy.cmd_login(command, [], environ=self.environ, execve=fake)
                self.assertEqual(outcome, "EXECUTED")
                self.assertEqual(captured["argv"][-1], flag)

    def test_binary_resolution_failure(self) -> None:
        environ = dict(self.environ)
        environ["CLAUDE_MULTI_PROXY_BIN"] = str(self.root / "missing")
        with self.assertRaisesRegex(ProxyError, "not an executable"):
            proxy.resolve_proxy_binary(environ)

    def test_no_secret_in_exec_argv_or_env(self) -> None:
        self._write_secret(b"KIMI_CLAUDE_API_KEY=supersecret-value-999\n")
        captured, fake = self._capture_exec()
        proxy.cmd_run([], environ=self.environ, execve=fake)
        argv_blob = " ".join(captured["argv"])
        self.assertNotIn("supersecret-value-999", argv_blob)


class SelectedSecretReadinessTests(ProxyControlTestCase):
    def _resolved(self):
        bundle = catalog.load_catalog(CATALOG_ROOT)
        from claude_multi import composition

        return bundle, composition.resolve(bundle.docs, bundle.default_composition)

    def test_missing_file_blocks_selected_provider(self) -> None:
        bundle, resolved = self._resolved()
        problems = proxy.selected_secret_problems(
            resolved,
            bundle.docs["models"]["models"],
            bundle.docs["providers"]["providers"],
            environ=self.environ,
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("Kimi (kimi)", problems[0])
        self.assertIn("env:KIMI_CLAUDE_API_KEY", problems[0])
        self.assertIn("missing", problems[0])

    def test_missing_variable_blocks(self) -> None:
        self._write_secret(b"OTHER=value1234\n")
        bundle, resolved = self._resolved()
        problems = proxy.selected_secret_problems(
            resolved,
            bundle.docs["models"]["models"],
            bundle.docs["providers"]["providers"],
            environ=self.environ,
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("KIMI_CLAUDE_API_KEY", problems[0])
        self.assertIn("missing from the secret env file", problems[0])

    def test_unsafe_secret_file_blocks_redacted(self) -> None:
        self._write_secret(b"not-an-assignment\n")
        bundle, resolved = self._resolved()
        problems = proxy.selected_secret_problems(
            resolved,
            bundle.docs["models"]["models"],
            bundle.docs["providers"]["providers"],
            environ=self.environ,
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("unsafe or malformed", problems[0])
        self.assertNotIn("not-an-assignment", problems[0])

    def test_symlink_secret_file_blocks(self) -> None:
        self._write_secret()
        link = self.secrets / "link.env"
        link.symlink_to(self.secret_file)
        environ = dict(self.environ)
        environ["CLAUDE_MULTI_SECRET_ENV"] = str(link)
        bundle, resolved = self._resolved()
        problems = proxy.selected_secret_problems(
            resolved,
            bundle.docs["models"]["models"],
            bundle.docs["providers"]["providers"],
            environ=environ,
        )
        self.assertEqual(len(problems), 1)
        self.assertIn("unsafe", problems[0])

    def test_present_secret_passes_oauth_unaffected(self) -> None:
        self._write_secret()
        bundle, resolved = self._resolved()
        problems = proxy.selected_secret_problems(
            resolved,
            bundle.docs["models"]["models"],
            bundle.docs["providers"]["providers"],
            environ=self.environ,
        )
        self.assertEqual(problems, [])

    def test_unselected_direct_provider_not_checked(self) -> None:
        # selector-only composition: no kimi selection → kimi secret irrelevant
        bundle = catalog.load_catalog(CATALOG_ROOT)
        import copy
        from claude_multi import composition

        document = copy.deepcopy(bundle.default_composition)
        document["slots"] = [
            {"role": "cm-lead", "model": "fable"},
            {"role": "cm-analyst", "model": "sol", "preferred": True},
        ]
        resolved = composition.resolve(bundle.docs, document)
        problems = proxy.selected_secret_problems(
            resolved,
            bundle.docs["models"]["models"],
            bundle.docs["providers"]["providers"],
            environ=self.environ,
        )
        self.assertEqual(problems, [])

    def test_fake_environ_never_touches_real_secret_path(self) -> None:
        # The fake secret file is malformed while the real one parses; any
        # read of the real path would wrongly succeed. The fake must be used.
        real_path = proxy.secret_env_path(None)
        self.assertNotEqual(str(real_path), str(self.secret_file))
        self._write_secret(b"this is malformed\n")
        bundle, resolved = self._resolved()
        with mock.patch.object(proxy.state, "read_private", wraps=proxy.state.read_private) as spy:
            problems = proxy.selected_secret_problems(
                resolved,
                bundle.docs["models"]["models"],
                bundle.docs["providers"]["providers"],
                environ=self.environ,
            )
        self.assertEqual(len(problems), 1)
        self.assertIn("malformed", problems[0])
        touched = {call.args[0] for call in spy.call_args_list}
        self.assertEqual(touched, {self.secret_file})
        self.assertNotIn(real_path, touched)


class TokenStateErrorTests(unittest.TestCase):
    """N1: a wrong-mode token file is a clean one-line error, no traceback."""

    def test_ensure_token_wraps_state_error(self) -> None:
        home = Path(tempfile.mkdtemp(prefix="claude-multi-proxy-"))
        self.addCleanup(shutil.rmtree, home, True)
        token_path = proxy.config_dir(home) / "api-key"
        token_path.parent.mkdir(parents=True)
        token_path.write_text("x" * 64 + "\n")
        token_path.chmod(0o644)  # state.read_private refuses group/other access
        with self.assertRaisesRegex(proxy.ProxyError, "unusable"):
            proxy.ensure_token(home)

    def test_main_reports_state_error_as_one_line(self) -> None:
        import io as _io

        home = Path(tempfile.mkdtemp(prefix="claude-multi-proxy-"))
        self.addCleanup(shutil.rmtree, home, True)
        token_path = proxy.config_dir(home) / "api-key"
        token_path.parent.mkdir(parents=True)
        token_path.write_text("x" * 64 + "\n")
        token_path.chmod(0o644)
        import sys as _sys

        err = _io.StringIO()
        with mock.patch.object(_sys, "stderr", err):
            code = proxy.main(["init"], environ={"HOME": str(home)})
        self.assertEqual(code, 1)
        self.assertIn("claude-multi-proxy:", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())


class SetSecretValueTests(unittest.TestCase):
    """018: proxy.set_secret_value — parse-preserving 0600 secret writes."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-secret-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(shutil.rmtree, self.root, True)
        self.path = self.root / "claude.env"

    def _write(self, text: str) -> None:
        state.ensure_private_dir(self.root)
        state.atomic_write(self.path, text.encode("utf-8"))

    def test_append_to_new_file(self) -> None:
        length = proxy.set_secret_value(self.path, "QWEN_CLAUDE_API_KEY", "sk-new")
        self.assertEqual(length, 6)
        self.assertEqual(self.path.read_text(), "QWEN_CLAUDE_API_KEY=sk-new\n")
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_replace_preserves_other_lines_byte_identical(self) -> None:
        self._write("# comment\nKIMI_CLAUDE_API_KEY=old-kimi\nexport EXTRA=1\n")
        proxy.set_secret_value(self.path, "KIMI_CLAUDE_API_KEY", "new-kimi")
        self.assertEqual(
            self.path.read_text(),
            "# comment\nKIMI_CLAUDE_API_KEY=new-kimi\nexport EXTRA=1\n",
        )

    def test_replace_keeps_export_prefix(self) -> None:
        self._write("export QWEN_CLAUDE_API_KEY=old\n")
        proxy.set_secret_value(self.path, "QWEN_CLAUDE_API_KEY", "new")
        self.assertEqual(self.path.read_text(), "export QWEN_CLAUDE_API_KEY=new\n")

    def test_invalid_name_and_value_rejected(self) -> None:
        with self.assertRaises(proxy.ProxyError):
            proxy.set_secret_value(self.path, "lower-case", "x")
        with self.assertRaises(proxy.ProxyError):
            proxy.set_secret_value(self.path, "QWEN_CLAUDE_API_KEY", "has spaces")
        with self.assertRaises(proxy.ProxyError):
            proxy.set_secret_value(self.path, "QWEN_CLAUDE_API_KEY", "")
        self.assertFalse(self.path.exists())

    def test_symlink_target_refused(self) -> None:
        target = self.root / "real.env"
        state.atomic_write(target, b"KIMI_CLAUDE_API_KEY=x\n")
        os.symlink(target, self.path)
        with self.assertRaises(proxy.ProxyError):
            proxy.set_secret_value(self.path, "QWEN_CLAUDE_API_KEY", "sk-x")

    def test_duplicate_keys_collapse_to_one_consumable_file(self) -> None:
        self._write("QWEN_CLAUDE_API_KEY=old\nexport QWEN_CLAUDE_API_KEY=older\n")
        proxy.set_secret_value(self.path, "QWEN_CLAUDE_API_KEY", "new")
        # The first occurrence wins (its prefix), the duplicate is dropped.
        self.assertEqual(self.path.read_text(), "QWEN_CLAUDE_API_KEY=new\n")
        # The saved file passes the strict parser (duplicates are rejected).
        self.assertEqual(
            proxy.parse_secret_env(self.path), {"QWEN_CLAUDE_API_KEY": "new"}
        )

    def test_export_tab_prefix_preserved_exactly(self) -> None:
        self._write("export\tQWEN_CLAUDE_API_KEY=old\n")
        proxy.set_secret_value(self.path, "QWEN_CLAUDE_API_KEY", "new")
        self.assertEqual(self.path.read_text(), "export\tQWEN_CLAUDE_API_KEY=new\n")

    def test_preexisting_malformed_other_line_rejects_the_save(self) -> None:
        self._write("garbage line\nKIMI_CLAUDE_API_KEY=x\n")
        with self.assertRaises(proxy.ProxyError):
            proxy.set_secret_value(self.path, "QWEN_CLAUDE_API_KEY", "sk-x")
        # Nothing written: the file still holds the original bytes.
        self.assertEqual(
            self.path.read_text(), "garbage line\nKIMI_CLAUDE_API_KEY=x\n"
        )

    def test_concurrent_saves_serialize_through_the_lock(self) -> None:
        self._write("KIMI_CLAUDE_API_KEY=x\n")
        lock = state.FileLock(self.path)
        lock.acquire(blocking=True)
        outcome: list[str] = []

        def writer() -> None:
            try:
                proxy.set_secret_value(self.path, "QWEN_CLAUDE_API_KEY", "sk-later")
            except proxy.ProxyError as exc:
                outcome.append(str(exc))

        thread = threading.Thread(target=writer)
        thread.start()
        # While the lock is held the writer must still be waiting.
        self.assertTrue(thread.is_alive())
        lock.release()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome, [])
        self.assertEqual(
            proxy.parse_secret_env(self.path),
            {"KIMI_CLAUDE_API_KEY": "x", "QWEN_CLAUDE_API_KEY": "sk-later"},
        )

    def test_unrelated_blank_lines_survive_a_duplicate_collapse(self) -> None:
        self._write(
            "# header\n\nQWEN_CLAUDE_API_KEY=old\n\nexport QWEN_CLAUDE_API_KEY=older\n"
        )
        proxy.set_secret_value(self.path, "QWEN_CLAUDE_API_KEY", "new")
        self.assertEqual(
            self.path.read_text(),
            "# header\n\nQWEN_CLAUDE_API_KEY=new\n\n",
        )


class ListProviderModelsTests(unittest.TestCase):
    """2.14.0: the explicit-invocation provider listing (discover)."""

    def _providers(self):
        from claude_multi import catalog

        return catalog.load_catalog(
            Path(__file__).resolve().parents[1]
        ).docs["providers"]["providers"]

    def test_secret_never_leaks_into_fetch_errors(self) -> None:
        secret = "topsecret-fixture"

        def bad_fetch(url, headers):
            raise RuntimeError(f"headers={headers}")  # carries the secret

        with mock.patch.dict(
            os.environ, {}, clear=False
        ):
            with mock.patch.object(
                proxy, "resolve_secret", return_value=secret
            ):
                with self.assertRaises(proxy.ProxyError) as ctx:
                    proxy.list_provider_models(
                        "kimi", self._providers(), fetch=bad_fetch
                    )
        self.assertNotIn(secret, str(ctx.exception))
        self.assertIn("RuntimeError", str(ctx.exception))

    def test_malformed_payload_entries_are_skipped(self) -> None:
        payload = b'{"data": [{"id": "k3"}, {"no_id": 1}, "junk", 42]}'
        with mock.patch.object(proxy, "resolve_secret", return_value="x"):
            entries = proxy.list_provider_models(
                "kimi", self._providers(), fetch=lambda url, headers: payload
            )
        self.assertEqual([e["id"] for e in entries], ["k3"])

    def test_missing_secret_is_a_clean_error(self) -> None:
        with mock.patch.object(proxy, "resolve_secret", return_value=None):
            with self.assertRaises(proxy.ProxyError) as ctx:
                proxy.list_provider_models("kimi", self._providers())
        self.assertIn("KIMI_CLAUDE_API_KEY", str(ctx.exception))


class ListingRedirectAndErrorShapeTests(unittest.TestCase):
    """Deep analysis security/gateway lanes: redirects are never followed
    (the credential header would leak to the target); HTTP/connection/parse
    failures become clean redacted ProxyErrors."""

    def _providers(self, base_url):
        return {
            "fixture": {
                "id": "fixture",
                "transport": {
                    "kind": "direct",
                    "base_url": base_url,
                    "auth": {"kind": "bearer", "secret_ref": "env:FIXTURE_KEY"},
                },
            }
        }

    def test_redirect_is_never_followed_and_secret_never_forwarded(self) -> None:
        hits = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                if self.path == "/v1/models":
                    self.send_response(302)
                    self.send_header("Location", "/elsewhere")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"data": []}')

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        base_url = f"http://127.0.0.1:{server.server_address[1]}"
        with mock.patch.object(proxy, "resolve_secret", return_value="fixture-secret"):
            with self.assertRaises(proxy.ProxyError) as ctx:
                proxy.list_provider_models(
                    "fixture", self._providers(base_url), timeout=5.0
                )
        self.assertIn("HTTP 302", str(ctx.exception))
        # The redirect target was never requested — the credential header
        # never left the original request.
        self.assertEqual(hits, ["/v1/models"])

    def test_http_error_is_a_status_only_message(self) -> None:
        def bad_fetch(_url, _headers):
            raise urllib.error.HTTPError(
                "http://x", 418, "teapot detail must not leak", {}, None
            )

        with mock.patch.object(proxy, "resolve_secret", return_value="x"):
            with self.assertRaises(proxy.ProxyError) as ctx:
                proxy.list_provider_models(
                    "fixture", self._providers("http://127.0.0.1:1"), fetch=bad_fetch
                )
        self.assertIn("HTTP 418", str(ctx.exception))
        self.assertNotIn("teapot detail", str(ctx.exception))

    def test_url_error_is_a_reason_only_message(self) -> None:
        def bad_fetch(_url, _headers):
            raise urllib.error.URLError("connection refused")

        with mock.patch.object(proxy, "resolve_secret", return_value="x"):
            with self.assertRaises(proxy.ProxyError) as ctx:
                proxy.list_provider_models(
                    "fixture", self._providers("http://127.0.0.1:1"), fetch=bad_fetch
                )
        self.assertIn("connection error", str(ctx.exception))

    def test_unexpected_payload_shape_is_a_clean_error(self) -> None:
        with mock.patch.object(proxy, "resolve_secret", return_value="x"):
            with self.assertRaises(proxy.ProxyError) as ctx:
                proxy.list_provider_models(
                    "fixture",
                    self._providers("http://127.0.0.1:1"),
                    fetch=lambda _url, _headers: b'{"data": 42}',
                )
        self.assertIn("unexpected shape", str(ctx.exception))

    def test_non_object_payload_is_a_clean_error(self) -> None:
        with mock.patch.object(proxy, "resolve_secret", return_value="x"):
            with self.assertRaises(proxy.ProxyError) as ctx:
                proxy.list_provider_models(
                    "fixture",
                    self._providers("http://127.0.0.1:1"),
                    fetch=lambda _url, _headers: b"[1, 2]",
                )
        self.assertIn("unexpected shape", str(ctx.exception))


class ListingDescriptorTests(unittest.TestCase):
    """022: per-provider listing descriptors — deepseek attempt,
    openrouter public OpenAI-shape listing."""

    def _providers(self):
        from claude_multi import catalog

        return catalog.load_catalog(
            Path(__file__).resolve().parents[1]
        ).docs["providers"]["providers"]

    def test_openrouter_listing_needs_no_secret_and_parses_openai_shape(self) -> None:
        payload = (
            b'{"data": ['
            b'{"id": "x-ai/grok-4.6", "name": "Grok 4.6", "context_length": 500000,'
            b' "top_provider": {"max_completion_tokens": 64000},'
            b' "reasoning": {"supported_efforts": ["high", "xhigh"]}},'
            b' "junk", {"no_id": true},'
            b' {"id": "deepseek/deepseek-v4-flash", "name": "DeepSeek V4 Flash", "context_length": 1000000}'
            b']}'
        )
        captured = {}

        def fetch(url, headers):
            captured["url"] = url
            captured["headers"] = headers
            return payload

        # resolve_secret raises if called: the public listing must never
        # touch the secret machinery.
        with mock.patch.object(
            proxy, "resolve_secret", side_effect=AssertionError("secret read")
        ):
            entries = proxy.list_provider_models(
                "openrouter", self._providers(), fetch=fetch
            )
        self.assertEqual(captured["url"], "https://openrouter.ai/api/v1/models")
        self.assertNotIn("Authorization", captured["headers"])
        self.assertNotIn("x-api-key", captured["headers"])
        self.assertEqual(
            entries,
            [
                {
                    "id": "x-ai/grok-4.6",
                    "display_name": "Grok 4.6",
                    "context_length": 500000,
                    "max_completion_tokens": 64000,
                    "think_efforts": ["high", "xhigh"],
                },
                {
                    "id": "deepseek/deepseek-v4-flash",
                    "display_name": "DeepSeek V4 Flash",
                    "context_length": 1000000,
                },
            ],
        )

    def test_deepseek_lists_via_openai_shape_with_bearer(self) -> None:
        # Verified 2026-08-12: the Anthropic path 404s; the documented
        # listing is OpenAI-shape GET /models with Bearer.
        captured = {}

        def fetch(url, headers):
            captured["url"] = url
            captured["headers"] = headers
            return b'{"data": [{"id": "deepseek-v4-flash"}, {"id": "deepseek-v4-pro"}]}'

        with mock.patch.object(proxy, "resolve_secret", return_value="ds"):
            entries = proxy.list_provider_models(
                "deepseek", self._providers(), fetch=fetch
            )
        self.assertEqual(captured["url"], "https://api.deepseek.com/models")
        self.assertEqual(captured["headers"]["Authorization"], "Bearer ds")
        self.assertNotIn("x-api-key", captured["headers"])
        # Entries carry ids only — context is asked for in the add flow.
        self.assertEqual(
            entries,
            [
                {"id": "deepseek-v4-flash", "display_name": "", "context_length": None},
                {"id": "deepseek-v4-pro", "display_name": "", "context_length": None},
            ],
        )

    def test_qwen_unsupported_carries_its_note(self) -> None:
        with self.assertRaises(proxy.ProxyError) as ctx:
            proxy.list_provider_models("qwen", self._providers())
        self.assertIn("404", str(ctx.exception))

    def test_unknown_provider_attempts_generic_shape(self) -> None:
        # A custom provider (no descriptor) attempts the Anthropic shape on
        # its own base — the discover path for operator-added providers.
        providers = {
            "lab": {
                "id": "lab",
                "transport": {
                    "kind": "direct",
                    "base_url": "https://lab.example.com/apps/anthropic",
                    "auth": {"kind": "header", "header": "x-api-key", "secret_ref": "env:LAB_KEY"},
                },
            }
        }
        captured = {}

        def fetch(url, headers):
            captured["url"] = url
            return b'{"data": []}'

        with mock.patch.object(proxy, "resolve_secret", return_value="k"):
            entries = proxy.list_provider_models("lab", providers, fetch=fetch)
        self.assertEqual(captured["url"], "https://lab.example.com/apps/anthropic/v1/models")
        self.assertEqual(entries, [])


class ListingReviewHardeningTests(unittest.TestCase):
    """022 review (sol-xhigh leg): parse-boundary hardening pins."""

    def _providers(self):
        from claude_multi import catalog

        return catalog.load_catalog(
            Path(__file__).resolve().parents[1]
        ).docs["providers"]["providers"]

    def test_missing_data_member_is_an_error_not_empty_success(self) -> None:
        # A 200 error body must not read as "the provider has no models".
        with mock.patch.object(proxy, "resolve_secret", return_value="x"):
            with self.assertRaises(proxy.ProxyError) as ctx:
                proxy.list_provider_models(
                    "kimi",
                    self._providers(),
                    fetch=lambda _u, _h: b'{"error": {"message": "fixture"}}',
                )
        self.assertIn("unexpected shape", str(ctx.exception))

    def test_wrongly_typed_fields_drop_to_defaults(self) -> None:
        payload = (
            b'{"data": [{"id": "m1", "name": {"not": "a string"},'
            b' "context_length": "500000"}]}'
        )
        with mock.patch.object(proxy, "resolve_secret", return_value="x"):
            entries = proxy.list_provider_models(
                "kimi", self._providers(), fetch=lambda _u, _h: payload
            )
        self.assertEqual(
            entries, [{"id": "m1", "display_name": "", "context_length": None}]
        )

    def test_deeply_nested_body_is_a_clean_error(self) -> None:
        depth = 60000
        body = b"[" * depth + b"]" * depth
        with mock.patch.object(proxy, "resolve_secret", return_value="x"):
            with self.assertRaises(proxy.ProxyError) as ctx:
                proxy.list_provider_models(
                    "kimi", self._providers(), fetch=lambda _u, _h: body
                )
        self.assertIn("unexpected shape", str(ctx.exception))

    def test_json_booleans_are_not_integers(self) -> None:
        # bool subclasses int: a `true` context must normalize to None
        # (022 review), never render as "True ctx".
        payload = (
            b'{"data": [{"id": "m1", "name": "Valid", "context_length": true,'
            b' "top_provider": {"max_completion_tokens": true}}]}'
        )
        with mock.patch.object(proxy, "resolve_secret", return_value="x"):
            entries = proxy.list_provider_models(
                "openrouter", self._providers(), fetch=lambda _u, _h: payload
            )
        self.assertEqual(
            entries,
            [{"id": "m1", "display_name": "Valid", "context_length": None}],
        )

    def test_descriptors_are_immutable(self) -> None:
        # Review: descriptors route credentials — mutation must be impossible.
        with self.assertRaises(TypeError):
            proxy._LISTING_SUPPORT["kimi"] = {"url": "https://evil.example.com"}
        with self.assertRaises(TypeError):
            proxy._LISTING_SUPPORT["kimi"]["url"] = "https://evil.example.com"
