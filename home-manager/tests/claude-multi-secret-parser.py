#!/usr/bin/env python3
"""Validate safe Kimi assignment parsing with fake values only."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


FAKE_VALUE = "fake-kimi-key"


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


def render_script(source: Path, target: Path, state: Path, config: Path, template: Path, env_file: Path) -> None:
    replacements = {
        "STATE_DIR=": f'STATE_DIR="{state}"',
        "CONFIG_DIR=": f'CONFIG_DIR="{config}"',
        "CONFIG_TEMPLATE=": f'CONFIG_TEMPLATE="{template}"',
        "KIMI_ENV_FILE=": f'KIMI_ENV_FILE="{env_file}"',
        "PROXY_BIN=": 'PROXY_BIN="/bin/false"',
    }
    lines = []
    for line in source.read_text().splitlines():
        replacement = next((value for prefix, value in replacements.items() if line.startswith(prefix)), None)
        lines.append(replacement if replacement is not None else line)
    target.write_text("\n".join(lines) + "\n")
    os.chmod(target, 0o700)


def run_case(
    source: Path,
    root: Path,
    environment: dict[str, str],
    name: str,
    assignment: str,
    expected_success: bool,
) -> None:
    case = root / name
    case.mkdir(mode=0o700)
    state = case / "state"
    config = case / "config"
    template = case / "template.yaml"
    template.write_text('local: "__CLAUDE_MULTI_API_KEY__"\nkimi: "__KIMI_CLAUDE_API_KEY__"\n')
    os.chmod(template, 0o600)
    env_file = case / "claude.env"
    env_file.write_text(f"OTHER_VARIABLE=ignored\n{assignment}\n")
    os.chmod(env_file, 0o600)
    script = case / "control"
    render_script(source, script, state, config, template, env_file)

    completed = subprocess.run(
        ["bash", str(script), "init"],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    check((completed.returncode == 0) is expected_success, f"secret parser case {name}")
    if expected_success:
        rendered = (config / "config.yaml").read_text()
        check(rendered.count(FAKE_VALUE) == 1, f"secret rendered once {name}")
        check("__CLAUDE_MULTI_API_KEY__" not in rendered, f"local placeholder removed {name}")
        check("__KIMI_CLAUDE_API_KEY__" not in rendered, f"Kimi placeholder removed {name}")
        check((config / "config.yaml").stat().st_mode & 0o777 == 0o600, f"config mode {name}")


def main() -> int:
    check(len(sys.argv) == 2, "usage: secret-parser-test CLAUDE_MULTI_PROXY")
    source = Path(sys.argv[1]).resolve()
    check(source.is_file(), "control script exists")
    root = Path(tempfile.mkdtemp(prefix="claude-multi-secret-parser-"))
    os.chmod(root, 0o700)
    home = root / "home"
    home.mkdir(mode=0o700)
    environment = isolated_environment(home)
    try:
        run_case(source, root, environment, "unquoted", f"KIMI_CLAUDE_API_KEY={FAKE_VALUE}", True)
        run_case(source, root, environment, "single-quoted", f"export KIMI_CLAUDE_API_KEY='{FAKE_VALUE}'", True)
        run_case(source, root, environment, "double-quoted", f'  KIMI_CLAUDE_API_KEY = "{FAKE_VALUE}"  ', True)
        run_case(
            source,
            root,
            environment,
            "duplicate",
            f"KIMI_CLAUDE_API_KEY={FAKE_VALUE}\nKIMI_CLAUDE_API_KEY={FAKE_VALUE}",
            False,
        )
        run_case(source, root, environment, "unsafe", "KIMI_CLAUDE_API_KEY=$(unsupported)", False)
        print("PASS secret parser unquoted/export/single-quoted/double-quoted assignments")
        print("PASS secret parser rejects duplicate and unsafe assignments")
        print("PASS secret renderer substitutes each fake placeholder once at mode 0600")
        return 0
    finally:
        shutil.rmtree(root)


if __name__ == "__main__":
    raise SystemExit(main())
