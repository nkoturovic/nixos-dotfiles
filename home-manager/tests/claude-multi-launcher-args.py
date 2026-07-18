#!/usr/bin/env python3
"""Validate launcher defaults and explicit-argument precedence without Claude sessions."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


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


class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/healthz":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> int:
    check(len(sys.argv) == 2, "usage: launcher-args-test CLAUDE_MULTI")
    source = Path(sys.argv[1]).resolve()
    check(source.is_file(), "launcher exists")
    test_root = Path(tempfile.mkdtemp(prefix="claude-multi-launcher-"))
    os.chmod(test_root, 0o700)
    test_home = test_root / "home"
    test_home.mkdir(mode=0o700)
    environment = isolated_environment(test_home)
    test_dir = test_root / "case"
    test_dir.mkdir(mode=0o700)
    health = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    health_thread = threading.Thread(target=health.serve_forever, daemon=True)
    health_thread.start()

    try:
        config_dir = test_dir / "config"
        config_dir.mkdir(mode=0o700)
        (config_dir / "api-key").write_text("a" * 64 + "\n")
        os.chmod(config_dir / "api-key", 0o600)
        (config_dir / "validation-session-id").write_text("scratch-id\n")
        os.chmod(config_dir / "validation-session-id", 0o600)
        plugin_dir = test_dir / "plugin"
        plugin_dir.mkdir(mode=0o700)
        settings_file = test_dir / "settings.json"
        settings_file.write_text("{}\n")
        os.chmod(settings_file, 0o600)
        capture_file = test_dir / "capture.json"
        fake_claude = test_dir / "fake-claude"
        fake_claude.write_text(
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

Path(os.environ["CAPTURE_FILE"]).write_text(json.dumps({
    "args": sys.argv[1:],
    "max_context": os.environ.get("CLAUDE_CODE_MAX_CONTEXT_TOKENS"),
    "auto_compact": os.environ.get("CLAUDE_CODE_AUTO_COMPACT_WINDOW"),
    "max_output": os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"),
    "family_defaults": {
        "fable": os.environ.get("ANTHROPIC_DEFAULT_FABLE_MODEL"),
        "opus": os.environ.get("ANTHROPIC_DEFAULT_OPUS_MODEL"),
    },
    "gateway": all(os.environ.get(name) for name in (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
    )),
    "gateway_marker": os.environ.get("CLAUDE_MULTI_GATEWAY"),
    "forbidden": any(name in os.environ for name in (
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CONFIG_DIR",
        "ENABLE_TOOL_SEARCH",
        "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
    )),
}))
"""
        )
        os.chmod(fake_claude, 0o700)

        replacements = {
            "CLAUDE_BIN=": f'CLAUDE_BIN="{fake_claude}"',
            "CONFIG_DIR=": f'CONFIG_DIR="{config_dir}"',
            "PLUGIN_DIR=": f'PLUGIN_DIR="{plugin_dir}"',
            "SETTINGS_FILE=": f'SETTINGS_FILE="{settings_file}"',
            "BASE_URL=": f'BASE_URL="http://127.0.0.1:{health.server_port}"',
        }
        rendered_lines = []
        for line in source.read_text().splitlines():
            replacement = next((value for prefix, value in replacements.items() if line.startswith(prefix)), None)
            rendered_lines.append(replacement if replacement is not None else line)
        launcher = test_dir / "claude-multi-test"
        launcher.write_text("\n".join(rendered_lines) + "\n")
        os.chmod(launcher, 0o700)

        prefix = ["--settings", str(settings_file), "--plugin-dir", str(plugin_dir)]
        generic_context = ("272000", None)
        kimi_context = ("1048576", "1048576")
        sol_context = ("372000", None)
        conservation_prompt = str(plugin_dir / "fable-conservation-prompt.md")
        conserve_flags = [
            "--disallowedTools",
            "Agent(claude-multi:fable-specialist)",
            "--append-system-prompt-file",
            conservation_prompt,
        ]
        scenarios = [
            ([], prefix + ["--model", "claude-fable-5[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode"], generic_context),
            (["fable"], prefix + ["--model", "claude-fable-5[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode"], generic_context),
            (["kimi"], prefix + ["--model", "claude-multi-kimi-k3[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode"], kimi_context),
            (["opus"], prefix + ["--model", "claude-multi-opus-4-8[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode"], generic_context),
            (["sol"], prefix + ["--model", "gpt-multi-sol-high", "--agent", "claude-multi:orchestrator", "--effort", "ultracode"], sol_context),
            (["conserve-opus"], prefix + ["--model", "claude-multi-opus-4-8[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode"] + conserve_flags, generic_context),
            (["conserve-kimi"], prefix + ["--model", "claude-multi-kimi-k3[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode"] + conserve_flags, kimi_context),
            (["--effort", "high"], prefix + ["--model", "claude-fable-5[1m]", "--agent", "claude-multi:orchestrator", "--effort", "high"], generic_context),
            (["--resume", "scratch-id"], prefix + ["--resume", "scratch-id"], generic_context),
            (["--continue"], prefix + ["--continue"], generic_context),
            (["kimi", "--resume", "scratch-id"], prefix + ["--model", "claude-multi-kimi-k3[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode", "--resume", "scratch-id"], kimi_context),
            (["opus", "--continue"], prefix + ["--model", "claude-multi-opus-4-8[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode", "--continue"], generic_context),
            (["conserve-opus", "--resume", "scratch-id"], prefix + ["--model", "claude-multi-opus-4-8[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode", "--resume", "scratch-id"] + conserve_flags, generic_context),
            (["conserve-kimi", "--continue"], prefix + ["--model", "claude-multi-kimi-k3[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode", "--continue"] + conserve_flags, kimi_context),
            (["--validation", "--resume", "scratch-id"], prefix + ["--resume", "scratch-id"], generic_context),
            (["sol", "--validation", "--resume", "scratch-id"], prefix + ["--model", "gpt-multi-sol-high", "--agent", "claude-multi:orchestrator", "--effort", "ultracode", "--resume", "scratch-id"], sol_context),
            (["--worktree", "lane"], prefix + ["--model", "claude-fable-5[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode", "--worktree", "lane"], generic_context),
            (
                ["kimi", "--model", "explicit-model", "--agent", "explicit-agent", "--effort", "low"],
                prefix + ["--model", "explicit-model", "--agent", "explicit-agent", "--effort", "low"],
                kimi_context,
            ),
            (
                ["conserve-kimi", "--model", "explicit-model", "--agent", "explicit-agent", "--effort", "low"],
                prefix + ["--model", "explicit-model", "--agent", "explicit-agent", "--effort", "low"] + conserve_flags,
                kimi_context,
            ),
            (
                ["conserve-opus", "--disallowedTools", "Agent(other-tool)"],
                prefix + ["--model", "claude-multi-opus-4-8[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode", "--disallowedTools", "Agent(other-tool)"] + conserve_flags,
                generic_context,
            ),
            (
                ["conserve-kimi", "--append-system-prompt-file", "/tmp/custom.md"],
                prefix + ["--model", "claude-multi-kimi-k3[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode", "--append-system-prompt-file", "/tmp/custom.md"] + conserve_flags,
                kimi_context,
            ),
            (
                ["conserve-opus", "--", "-print this"],
                prefix + ["--model", "claude-multi-opus-4-8[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode"] + conserve_flags + ["--", "-print this"],
                generic_context,
            ),
            (
                ["conserve-kimi", "--effort", "high", "--", "print this"],
                prefix + ["--model", "claude-multi-kimi-k3[1m]", "--agent", "claude-multi:orchestrator", "--effort", "high"] + conserve_flags + ["--", "print this"],
                kimi_context,
            ),
            (
                ["opus", "--", "-print this"],
                prefix + ["--model", "claude-multi-opus-4-8[1m]", "--agent", "claude-multi:orchestrator", "--effort", "ultracode", "--", "-print this"],
                generic_context,
            ),
        ]
        scoped_names = (
            "CLAUDE_MULTI_GATEWAY",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
        )
        parent_scoped_environment = {name: os.environ.get(name) for name in scoped_names}
        environment["CAPTURE_FILE"] = str(capture_file)
        for name in (
            "ANTHROPIC_BASE_URL",
            "ANTHROPIC_AUTH_TOKEN",
            "ANTHROPIC_DEFAULT_FABLE_MODEL",
            "ANTHROPIC_DEFAULT_OPUS_MODEL",
            "CLAUDE_MULTI_GATEWAY",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
            "CLAUDE_CODE_MAX_CONTEXT_TOKENS",
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW",
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS",
            "CLAUDE_CODE_SUBAGENT_MODEL",
            "CLAUDE_CONFIG_DIR",
            "ENABLE_TOOL_SEARCH",
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS",
        ):
            environment.pop(name, None)
        environment["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "parent-max-context"
        environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] = "parent-auto-compact"
        environment["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "parent-max-output"

        for supplied, expected, context_policy in scenarios:
            capture_file.unlink(missing_ok=True)
            completed = subprocess.run(
                ["bash", str(launcher), *supplied],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            check(completed.returncode == 0, "launcher scenario exit")
            captured = json.loads(capture_file.read_text())
            check(captured.get("args") == expected, "launcher argument precedence")
            expected_context, expected_auto_compact = context_policy
            check(captured.get("max_context") == expected_context, "profile-scoped context window")
            check(captured.get("auto_compact") == expected_auto_compact, "profile-scoped auto-compact window")
            check(captured.get("max_output") is None, "child max output override absent")
            check(
                captured.get("family_defaults")
                == {
                    "fable": "claude-fable-5[1m]",
                    "opus": "claude-opus-4-8[1m]",
                },
                "child-scoped family model mappings",
            )
            check(captured.get("gateway") is True, "child-scoped gateway variables")
            check(captured.get("gateway_marker") == "1", "child-scoped gateway marker")
            check(captured.get("forbidden") is False, "forbidden launcher variables absent")

        check(
            {name: os.environ.get(name) for name in scoped_names} == parent_scoped_environment,
            "launcher-scoped environment absent from parent process",
        )
        check(environment["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] == "parent-max-context", "parent max context unchanged")
        check(environment["CLAUDE_CODE_AUTO_COMPACT_WINDOW"] == "parent-auto-compact", "parent auto-compact unchanged")
        check(environment["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "parent-max-output", "parent max output unchanged")

        help_result = subprocess.run(
            ["bash", str(launcher), "--help"],
            env=environment,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        check(help_result.returncode == 0, "launcher help exit")
        for profile_help in (
            "fable       Fable 5 + orchestrator + ultracode (default)",
            "kimi        Kimi K3 1M + orchestrator + ultracode",
            "opus        Opus 4.8 + orchestrator + ultracode",
            "sol         Sol high + orchestrator + ultracode (xhigh available explicitly)",
            "conserve-opus  Opus 4.8 + orchestrator + ultracode (Fable conservation mode)",
            "conserve-kimi  Kimi K3 1M + orchestrator + ultracode (Fable conservation mode)",
        ):
            check(profile_help in help_result.stdout, "all lead profiles default to ultracode")
        check("Cross-provider" in help_result.stdout and "plaintext handoff" in help_result.stdout, "resume safety warning")

        for rejected in (["--validation", "--continue"], ["--validation", "--resume", "other-id"]):
            completed = subprocess.run(
                ["bash", str(launcher), *rejected],
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            check(completed.returncode == 1, "validation guard rejection")

        print("PASS launcher settings/plugin/gateway marker scoped to child without parent leakage")
        print("PASS launcher default/fable/kimi/opus/sol/conserve-opus/conserve-kimi profiles")
        print("PASS launcher Sol 372K with default auto-compaction, Kimi 1M, and generic 272K context policies")
        print("PASS launcher generic orchestrator and profile efforts")
        print("PASS launcher explicit overrides, unprofiled/profiled resume/continue, and worktree precedence")
        print("PASS launcher conservation profiles pass exact deny and appended prompt")
        print("PASS launcher conservation profiles match normal profile model/context/effort")
        print("PASS launcher validation allowlist/rejections and cross-provider resume warning")
        print("PASS launcher no max-output/config-dir/subagent/tool-search/team environment")
        return 0
    finally:
        health.shutdown()
        health.server_close()
        health_thread.join(timeout=5)
        shutil.rmtree(test_root)


if __name__ == "__main__":
    raise SystemExit(main())
