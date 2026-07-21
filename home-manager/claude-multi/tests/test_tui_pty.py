"""Deterministic PTY smoke tests for quick confirm, curses, and line mode."""

from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import unittest
from pathlib import Path


CATALOG_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = CATALOG_ROOT / "src"
TIMEOUT = 6.0


def _set_size(fd: int, rows: int, columns: int) -> None:
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))


class PTYProcess:
    def __init__(self, code: str, *, rows: int = 24, columns: int = 80, extra_env=None):
        self.master, self.slave = pty.openpty()
        _set_size(self.slave, rows, columns)
        self.before = termios.tcgetattr(self.slave)
        env = dict(os.environ)
        env.update(
            {
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(SRC_ROOT),
                "TERM": "xterm-256color",
            }
        )
        if extra_env:
            env.update(extra_env)
        self.process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            close_fds=True,
            env=env,
        )
        self.output = bytearray()

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def resize(self, rows: int, columns: int) -> None:
        _set_size(self.slave, rows, columns)
        self.process.send_signal(signal.SIGWINCH)

    def read_until(self, needle: bytes, timeout: float = TIMEOUT) -> bytes:
        deadline = time.monotonic() + timeout
        while needle not in self.output and time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                break
            try:
                chunk = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            self.output.extend(chunk)
        if needle not in self.output:
            raise AssertionError(f"did not observe {needle!r}; output={bytes(self.output)!r}")
        return bytes(self.output)

    def finish(self, timeout: float = TIMEOUT) -> tuple[int, bytes, list]:
        deadline = time.monotonic() + timeout
        while self.process.poll() is None and time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if chunk:
                    self.output.extend(chunk)
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=2)
            raise AssertionError(f"PTY child timed out; output={bytes(self.output)!r}")
        # Drain bytes available after exit.
        while True:
            ready, _, _ = select.select([self.master], [], [], 0)
            if not ready:
                break
            try:
                chunk = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    break
                raise
            if not chunk:
                break
            self.output.extend(chunk)
        after = termios.tcgetattr(self.slave)
        return self.process.returncode, bytes(self.output), after

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=2)
        os.close(self.master)
        os.close(self.slave)


def editor_code(force_line: bool = False, missing_lead: bool = False) -> str:
    return f"""
import sys
import copy
from pathlib import Path
from claude_multi import catalog
from claude_multi.editor import EditorState, run_editor
root = Path({str(CATALOG_ROOT)!r})
bundle = catalog.load_catalog(root)
document = copy.deepcopy(bundle.default_composition)
if {missing_lead!r}:
    document['slots'] = [slot for slot in document['slots'] if slot['role'] != 'cm-lead']
state = EditorState(bundle.docs, document, bundle.default_composition)
result = run_editor(state, sys.stdin, sys.stdout, force_line={force_line!r})
print('RESULT=' + ('cancel' if result is None else result.action), flush=True)
"""


class CursesPTYTests(unittest.TestCase):
    def test_curses_cancel_restores_terminal(self) -> None:
        child = PTYProcess(editor_code())
        self.addCleanup(child.close)
        child.read_until(b"Edit default")
        child.send(b"q")
        code, output, after = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"RESULT=cancel", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)

    def test_resize_to_small_is_safe_and_restores_terminal(self) -> None:
        child = PTYProcess(editor_code())
        self.addCleanup(child.close)
        child.read_until(b"Edit default")
        child.resize(8, 35)
        child.send(b"j")  # wake getch; next draw must observe the resized PTY
        child.read_until(b"Terminal too small")
        child.send(b"q")
        code, output, after = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"RESULT=cancel", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)

    def test_too_small_can_switch_to_line_fallback(self) -> None:
        child = PTYProcess(editor_code(), rows=8, columns=35)
        self.addCleanup(child.close)
        child.read_until(b"Terminal too small")
        child.send(b"l")
        child.read_until(b"0. Cancel")
        child.send(b"0\n")
        code, output, _ = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"RESULT=cancel", output)

    def test_curses_interrupt_cancels_and_restores_terminal(self) -> None:
        child = PTYProcess(editor_code())
        self.addCleanup(child.close)
        child.read_until(b"Edit default")
        child.process.send_signal(signal.SIGINT)
        code, output, after = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"RESULT=cancel", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)

    def test_curses_missing_lead_summary_is_blocked_and_cancel_restores(self) -> None:
        child = PTYProcess(editor_code(missing_lead=True))
        self.addCleanup(child.close)
        child.read_until(b"none selected")
        child.read_until(b"Status: BLOCKED")
        child.send(b"q")
        code, output, after = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"RESULT=cancel", output)
        self.assertNotIn(b"Traceback", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)


class LinePTYTests(unittest.TestCase):
    def test_line_mode_no_color_cancel(self) -> None:
        child = PTYProcess(editor_code(True), extra_env={"NO_COLOR": "1", "TERM": "dumb"})
        self.addCleanup(child.close)
        child.read_until(b"0. Cancel")
        child.send(b"0\n")
        code, output, _ = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"Status: Ready", output)
        self.assertNotIn(b"\x1b", output)

    def test_line_mode_eof_cancels(self) -> None:
        # Ctrl-D at an empty canonical input line produces EOF without sleeping.
        child = PTYProcess(editor_code(True), extra_env={"TERM": "dumb"})
        self.addCleanup(child.close)
        child.read_until(b"> ")
        child.send(b"\x04")
        code, output, _ = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"RESULT=cancel", output)


class QuickConfirmPTYTests(unittest.TestCase):
    def test_quick_confirm_one_enter_uses_fake_launch(self) -> None:
        temp = tempfile.mkdtemp(prefix="claude-multi-pty-runtime-")
        self.addCleanup(__import__("shutil").rmtree, temp, True)
        secret_dir = Path(temp) / "secrets"
        secret_dir.mkdir(mode=0o700)
        secret_file = secret_dir / "claude.env"
        secret_file.write_text("KIMI_CLAUDE_API_KEY=pty-test-dummy\n")
        secret_file.chmod(0o600)
        code = f"""
import sys
from pathlib import Path
from claude_multi.cli import Runtime, main
root = Path({str(CATALOG_ROOT)!r})
base = Path({temp!r})
env = {{'HOME': str(base/'home'), 'XDG_CONFIG_HOME': str(base/'config'), 'XDG_STATE_HOME': str(base/'state'), 'TERM': 'dumb', 'CLAUDE_MULTI_SECRET_ENV': {str(secret_file)!r}}}
def fake(prepared):
    print('FAKE_LAUNCH=' + prepared.result.session_action.kind, flush=True)
    return 0
runtime = Runtime(asset_root=root, environ=env, cwd=base/'project', launch_callback=fake)
raise SystemExit(main([], runtime=runtime, input_stream=sys.stdin, output_stream=sys.stdout, interactive=True))
"""
        child = PTYProcess(code, extra_env={"TERM": "dumb"})
        self.addCleanup(child.close)
        child.read_until(b"Enter launch")
        child.send(b"\n")
        returncode, output, _ = child.finish()
        self.assertEqual(returncode, 0, output)
        self.assertIn(b"Status         Ready", output)
        self.assertIn(b"FAKE_LAUNCH=fresh", output)


if __name__ == "__main__":
    unittest.main()
