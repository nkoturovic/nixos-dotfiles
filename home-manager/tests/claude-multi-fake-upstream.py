#!/usr/bin/env python3
"""Validate the patched Claude API-key path using fake loopback credentials."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


LOCAL_KEY = "fake-local-key"
PROVIDER_KEY = "fake-provider-key"
ALIAS = "claude-multi-kimi-k3"
UPSTREAM_MODEL = "k3"
NORMAL_ALIAS = "claude-existing-default"
CONTROL_ALIAS = "claude-multi-opus-4-8"
CONTROL_UPSTREAM_MODEL = "claude-opus-4-8"
SYNTHETIC_THINKING = {"type": "adaptive"}


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


def isolated_environment(home: Path) -> dict[str, str]:
    environment = os.environ.copy()
    environment["HOME"] = str(home)
    for variable, name in (
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_STATE_HOME", "state"),
        ("TMPDIR", "tmp"),
    ):
        directory = home / name
        directory.mkdir(mode=0o700)
        environment[variable] = str(directory)
    return environment


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class Capture:
    def __init__(self) -> None:
        self.messages = False
        self.control_messages = False
        self.count_tokens = False
        self.errors: list[str] = []


def make_handler(capture: Capture) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            return

        def send_json(self, status: int, payload: dict[str, object]) -> None:
            encoded = json.dumps(payload, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
                check(0 < length <= 2 * 1024 * 1024, "upstream payload length")
                payload = json.loads(self.rfile.read(length))
                path = urlsplit(self.path).path
                check(self.headers.get("x-api-key") == PROVIDER_KEY, "x-api-key header")
                check(self.headers.get("Authorization") is None, "Authorization header absent")
                model = payload.get("model")

                if path == "/coding/v1/messages":
                    if model == UPSTREAM_MODEL:
                        check("thinking" not in payload, "Claude thinking field absent")
                        check(payload.get("output_config", {}).get("effort") == "max", "max effort")
                        capture.messages = True
                    elif model == CONTROL_UPSTREAM_MODEL:
                        check(payload.get("thinking", {}).get("type") == "adaptive", "non-Kimi thinking preserved")
                        check("output_config" not in payload, "Kimi effort override scoped")
                        capture.control_messages = True
                    else:
                        raise AssertionError("unexpected upstream model")
                    self.send_json(
                        200,
                        {
                            "id": "msg_fake",
                            "type": "message",
                            "role": "assistant",
                            "model": model,
                            "content": [{"type": "text", "text": "ok"}],
                            "stop_reason": "end_turn",
                            "stop_sequence": None,
                            "usage": {"input_tokens": 1, "output_tokens": 1},
                        },
                    )
                    return

                if path == "/coding/v1/messages/count_tokens":
                    check(model == UPSTREAM_MODEL, "count_tokens upstream model")
                    capture.count_tokens = True
                    self.send_json(200, {"input_tokens": 7})
                    return

                raise AssertionError("unexpected upstream path")
            except Exception as exc:  # keep failures body-free and allowlisted
                capture.errors.append(str(exc))
                self.send_json(500, {"error": "fake upstream assertion failed"})

    return Handler


def request_json(url: str, method: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {LOCAL_KEY}",
            "Anthropic-Version": "2023-06-01",
            "Content-Type": "application/json",
            "User-Agent": "claude-cli/2.1.211 (external, cli)",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"proxy request failed with status {exc.code}") from None


def wait_for_proxy(base_url: str) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/healthz", timeout=0.5) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise RuntimeError("proxy health check timed out")


def main() -> int:
    check(len(sys.argv) == 2, "usage: fake-upstream-test CLI_PROXY_API")
    binary = Path(sys.argv[1]).resolve()
    check(binary.is_file(), "CLIProxyAPI binary exists")

    test_dir = Path(tempfile.mkdtemp(prefix="claude-multi-fake-k3-"))
    os.chmod(test_dir, 0o700)
    home = test_dir / "home"
    home.mkdir(mode=0o700)
    environment = isolated_environment(home)
    capture = Capture()
    upstream_port = free_port()
    proxy_port = free_port()
    upstream = ThreadingHTTPServer(("127.0.0.1", upstream_port), make_handler(capture))
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    process: subprocess.Popen[bytes] | None = None

    try:
        auth_dir = test_dir / "auth"
        auth_dir.mkdir(mode=0o700)
        config_path = test_dir / "config.yaml"
        config_path.write_text(
            f'''host: "127.0.0.1"
port: {proxy_port}
tls:
  enable: false
remote-management:
  allow-remote: false
  secret-key: ""
  disable-control-panel: true
  disable-auto-update-panel: true
auth-dir: "{auth_dir}"
api-keys:
  - "{LOCAL_KEY}"
debug: false
pprof:
  enable: false
plugins:
  enabled: false
commercial-mode: true
logging-to-file: false
usage-statistics-enabled: false
request-retry: 0
max-retry-credentials: 1
quota-exceeded:
  switch-project: false
  switch-preview-model: false
  antigravity-credits: false
claude-api-key:
  - api-key: "{PROVIDER_KEY}"
    base-url: "http://127.0.0.1:{upstream_port}/coding"
    auth-header: "x-api-key"
    cloak:
      mode: "never"
    models:
      - name: "{UPSTREAM_MODEL}"
        alias: "{ALIAS}"
        display-name: "Kimi K3 1M"
        owned-by: "moonshot"
        context-length: 1048576
        max-completion-tokens: 12345
        force-mapping: true
      - name: "claude-existing-default"
        alias: "{NORMAL_ALIAS}"
        display-name: "Existing Default"
      - name: "{CONTROL_UPSTREAM_MODEL}"
        alias: "{CONTROL_ALIAS}"
        display-name: "Opus Control"
        force-mapping: true
payload:
  override:
    - models:
        - name: "{ALIAS}"
          protocol: "claude"
      params:
        "output_config.effort": "max"
  filter:
    - models:
        - name: "{ALIAS}"
          protocol: "claude"
      params:
        - "thinking"
'''
        )
        os.chmod(config_path, 0o600)

        process = subprocess.Popen(
            [str(binary), "--config", str(config_path), "--local-model"],
            cwd=test_dir,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base_url = f"http://127.0.0.1:{proxy_port}"
        wait_for_proxy(base_url)

        message = request_json(
            f"{base_url}/v1/messages",
            "POST",
            {
                "model": ALIAS,
                "max_tokens": 64,
                "thinking": SYNTHETIC_THINKING,
                "messages": [{"role": "user", "content": "fake test"}],
            },
        )
        check(message.get("model") == ALIAS, "force-mapped response model")

        control_message = request_json(
            f"{base_url}/v1/messages",
            "POST",
            {
                "model": CONTROL_ALIAS,
                "max_tokens": 64,
                "thinking": SYNTHETIC_THINKING,
                "messages": [{"role": "user", "content": "scope control"}],
            },
        )
        check(control_message.get("model") == CONTROL_ALIAS, "control response model")

        count = request_json(
            f"{base_url}/v1/messages/count_tokens",
            "POST",
            {"model": ALIAS, "messages": [{"role": "user", "content": "fake test"}]},
        )
        check(count.get("input_tokens") == 7, "count_tokens response")

        models = request_json(f"{base_url}/v1/models", "GET")
        entries = {item.get("id"): item for item in models.get("data", [])}
        check(ALIAS in entries, "K3 model discovery")
        check(entries[ALIAS].get("owned_by") == "moonshot", "K3 owner discovery")
        check(entries[ALIAS].get("max_input_tokens") == 1048576, "K3 context discovery")
        check(entries[ALIAS].get("max_tokens") == 12345, "K3 completion discovery")
        check(entries[NORMAL_ALIAS].get("owned_by") == "anthropic", "default Claude owner unchanged")
        check(entries[NORMAL_ALIAS].get("max_input_tokens") == 200000, "default Claude context unchanged")

        check(capture.messages, "messages upstream observed")
        check(capture.control_messages, "non-Kimi messages upstream observed")
        check(capture.count_tokens, "count_tokens upstream observed")
        check(not capture.errors, "fake upstream assertions")

        print("PASS messages path=/coding/v1/messages model=k3 auth=x-api-key-only inbound-thinking-filtered/max")
        print("PASS Kimi filter scope preserves top-level thinking and omits Kimi effort override for Opus control")
        print("PASS count_tokens path=/coding/v1/messages/count_tokens auth=x-api-key-only")
        print("PASS discovery alias=claude-multi-kimi-k3 owned_by=moonshot max_input_tokens=1048576 max_tokens=12345")
        print("PASS discovery default Claude owner=anthropic max_input_tokens=200000")
        return 0
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=5)
        shutil.rmtree(test_dir)


if __name__ == "__main__":
    raise SystemExit(main())
