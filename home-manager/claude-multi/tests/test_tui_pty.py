"""Deterministic PTY smoke tests for quick confirm, curses form, and dumb mode."""

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
# Exit waits are load-tolerant: the full suite starves PTY children.
FINISH_TIMEOUT = 30.0


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

    def finish(self, timeout: float = FINISH_TIMEOUT) -> tuple[int, bytes, list]:
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


def editor_code(missing_lead: bool = False, editor_command: str | None = None) -> str:
    editor_env = (
        f"environ['EDITOR'] = {editor_command!r}" if editor_command else ""
    )
    return f"""
import os
import sys
import copy
from pathlib import Path
from claude_multi import catalog
from claude_multi.tui import EditorState, run_form_editor
root = Path({str(CATALOG_ROOT)!r})
bundle = catalog.load_catalog(root)
document = copy.deepcopy(bundle.default_composition)
if {missing_lead!r}:
    document['slots'] = [slot for slot in document['slots'] if slot['role'] != 'cm-lead']
state = EditorState(bundle.docs, document, bundle.default_composition)
environ = dict(os.environ)
{editor_env}
result = run_form_editor(state, input_stream=sys.stdin, output_stream=sys.stdout, environ=environ)
print('RESULT=' + ('cancel' if result is None else result.action), flush=True)
print('NAME=' + state.document['name'], flush=True)
"""


class CursesPTYTests(unittest.TestCase):
    def test_curses_cancel_restores_terminal(self) -> None:
        child = PTYProcess(editor_code())
        self.addCleanup(child.close)
        child.read_until(b"Edit default")
        child.send(b"\x1b")
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
        child.send(b"\x1b")
        code, output, after = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"RESULT=cancel", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)

    def test_too_small_cancel_restores_terminal(self) -> None:
        child = PTYProcess(editor_code(), rows=8, columns=35)
        self.addCleanup(child.close)
        child.read_until(b"Terminal too small")
        child.send(b"\x1b")
        code, output, after = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"RESULT=cancel", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)

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
        child.send(b"\x1b")
        code, output, after = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"RESULT=cancel", output)
        self.assertNotIn(b"Traceback", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)

    def test_ctrl_g_applies_editor_json_and_restores_terminal(self) -> None:
        editor_command = (
            f"{sys.executable} -c \"import sys,pathlib; "
            "p=pathlib.Path(sys.argv[1]); "
            "p.write_bytes(p.read_bytes().replace(b'default', b'renamed'))\""
        )
        child = PTYProcess(editor_code(editor_command=editor_command))
        self.addCleanup(child.close)
        child.read_until(b"Edit default")
        child.send(b"\x07")  # Ctrl+G: raw composition JSON in $EDITOR
        child.read_until(b"JSON applied")
        child.send(b"\x1b")  # dirty: discard Modal
        child.read_until(b"Discard")
        child.send(b"\n")
        code, output, after = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"RESULT=cancel", output)
        self.assertIn(b"NAME=renamed", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)


class DumbTerminalPTYTests(unittest.TestCase):
    """TERM=dumb: no interactive editor; printed plan + $EDITOR guidance."""

    def _edit_code(self, temp: str, secret_file: Path) -> str:
        return f"""
import sys
from pathlib import Path
from claude_multi.cli import Runtime, main
root = Path({str(CATALOG_ROOT)!r})
base = Path({temp!r})
env = {{'HOME': str(base/'home'), 'XDG_CONFIG_HOME': str(base/'config'), 'XDG_STATE_HOME': str(base/'state'), 'TERM': 'dumb', 'CLAUDE_MULTI_SECRET_ENV': {str(secret_file)!r}}}
runtime = Runtime(asset_root=root, environ=env, cwd=base/'project', launch_callback=lambda prepared: 0)
raise SystemExit(main(['compose', 'edit', 'default'], runtime=runtime, input_stream=sys.stdin, output_stream=sys.stdout, interactive=True))
"""

    def test_dumb_terminal_prints_plan_and_editor_command(self) -> None:
        temp = tempfile.mkdtemp(prefix="claude-multi-pty-dumb-")
        self.addCleanup(__import__("shutil").rmtree, temp, True)
        child = PTYProcess(
            self._edit_code(temp, _secret_file(temp)),
            extra_env={"TERM": "dumb", "NO_COLOR": "1"},
        )
        self.addCleanup(child.close)
        child.read_until(b"$EDITOR")
        code, output, _ = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"Status         Ready", output)
        self.assertIn(b"default.json", output)
        self.assertNotIn(b"\x1b", output)


class QuickConfirmPTYTests(unittest.TestCase):
    def _runtime_code(self, temp: str, secret_file: Path) -> str:
        return f"""
import sys
from pathlib import Path
from claude_multi.cli import Runtime, main
root = Path({str(CATALOG_ROOT)!r})
base = Path({temp!r})
env = {{'HOME': str(base/'home'), 'XDG_CONFIG_HOME': str(base/'config'), 'XDG_STATE_HOME': str(base/'state'), 'CLAUDE_MULTI_SECRET_ENV': {str(secret_file)!r}}}
def fake(prepared):
    print('FAKE_LAUNCH=' + prepared.result.session_action.kind, flush=True)
    return 0
runtime = Runtime(asset_root=root, environ=env, cwd=base/'project', launch_callback=fake, doctor_binary_callback=lambda contract: ([], ['fixture binary verified.']), doctor_callback=lambda _runtime: [])
raise SystemExit(main([], runtime=runtime, input_stream=sys.stdin, output_stream=sys.stdout, interactive=True))
"""

    def _temp_runtime(self, prefix: str) -> tuple[str, Path]:
        temp = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(__import__("shutil").rmtree, temp, True)
        return temp, _secret_file(temp)

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
runtime = Runtime(asset_root=root, environ=env, cwd=base/'project', launch_callback=fake, doctor_binary_callback=lambda contract: ([], ['fixture binary verified.']), doctor_callback=lambda _runtime: [])
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

    def test_quick_confirm_curses_enter_launches(self) -> None:
        temp, secret_file = self._temp_runtime("claude-multi-pty-curses-")
        child = PTYProcess(self._runtime_code(temp, secret_file))
        self.addCleanup(child.close)
        child.read_until(b"composition: default")
        child.send(b"\n")
        returncode, output, after = child.finish()
        self.assertEqual(returncode, 0, output)
        self.assertIn(b"Status  Ready", output)
        # The KeyBar accent splits "Enter" and "launch" with escape codes.
        self.assertIn(b"Enter", output)
        self.assertIn(b"launch", output)
        self.assertIn(b"workflows: native", output)
        self.assertIn(b"FAKE_LAUNCH=fresh", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)

    def test_quick_confirm_curses_cancel_never_launches(self) -> None:
        temp, secret_file = self._temp_runtime("claude-multi-pty-curses-q-")
        child = PTYProcess(self._runtime_code(temp, secret_file))
        self.addCleanup(child.close)
        child.read_until(b"composition: default")
        child.send(b"\x1b")
        returncode, output, after = child.finish()
        self.assertEqual(returncode, 0, output)
        self.assertNotIn(b"FAKE_LAUNCH", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)

    def test_quick_confirm_curses_details_and_guarantee_modal(self) -> None:
        temp, secret_file = self._temp_runtime("claude-multi-pty-curses-d-")
        child = PTYProcess(self._runtime_code(temp, secret_file))
        self.addCleanup(child.close)
        child.read_until(b"composition: default")
        child.send(b"?")
        child.read_until(b"quick-confirm \xe2\x80\x94 help")
        child.read_until(b"Native workflows (ultracode): ON")
        child.send(b"\n")  # close the Modal
        child.read_until(b"composition: default")
        child.send(b"d")
        child.read_until(b"availability")
        child.send(b"\x1b")
        returncode, output, _ = child.finish()
        self.assertEqual(returncode, 0, output)
        self.assertIn(b"Native workflows (ultracode): ON", output)
        self.assertNotIn(b"FAKE_LAUNCH", output)


if __name__ == "__main__":
    unittest.main()


def _bare_launch_code(temp: str, secret_file: Path) -> str:
    return f"""
import sys
from pathlib import Path
import faulthandler
# Self-diagnosing flake guard (this test has timed out twice under heavy
# suite load with output byte-identical to a clean exit): dump the child
# stack into the captured output before the harness deadline kills it.
faulthandler.dump_traceback_later(20, exit=True)
from claude_multi.cli import Runtime, main
root = Path({str(CATALOG_ROOT)!r})
base = Path({temp!r})
env = {{'HOME': str(base/'home'), 'XDG_CONFIG_HOME': str(base/'config'), 'XDG_STATE_HOME': str(base/'state'), 'TERM': 'dumb', 'CLAUDE_MULTI_SECRET_ENV': {str(secret_file)!r}}}
def fake(prepared):
    print('FAKE_LAUNCH=' + prepared.result.session_action.kind, flush=True)
    return 0
runtime = Runtime(asset_root=root, environ=env, cwd=base/'project', launch_callback=fake, doctor_binary_callback=lambda contract: ([], ['fixture binary verified.']), doctor_callback=lambda _runtime: [])
raise SystemExit(main([], runtime=runtime))
"""


def _secret_file(temp: str) -> Path:
    secret_dir = Path(temp) / "secrets"
    secret_dir.mkdir(mode=0o700, exist_ok=True)
    secret_file = secret_dir / "claude.env"
    secret_file.write_text("KIMI_CLAUDE_API_KEY=pty-test-dummy\n")
    secret_file.chmod(0o600)
    return secret_file


class _NoCttyChild:
    """Child in a new session without a controlling terminal, TTY stdio."""

    def __init__(self, code: str, *, extra_env=None):
        self.master, slave = pty.openpty()
        _set_size(slave, 24, 80)
        env = dict(os.environ)
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(SRC_ROOT), "TERM": "dumb"})
        if extra_env:
            env.update(extra_env)
        self.process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            preexec_fn=os.setsid,  # new session: no controlling terminal
            close_fds=True,
            env=env,
        )
        os.close(slave)
        self.output = bytearray()

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

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def finish(self, timeout: float = FINISH_TIMEOUT) -> tuple[int, bytes]:
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
            raise AssertionError(f"child timed out; output={bytes(self.output)!r}")
        try:
            while True:
                chunk = os.read(self.master, 65536)
                if not chunk:
                    break
                self.output.extend(chunk)
        except OSError:
            pass
        os.close(self.master)
        return self.process.returncode, bytes(self.output)

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=2)


class _CttyPipesChild:
    """Child with the slave as controlling terminal but piped stdin/stdout.

    Proves /dev/tty is genuinely preferred: interaction must flow over the
    PTY master (the child's controlling terminal), never over the pipes.
    """

    def __init__(self, code: str, *, extra_env=None):
        self.master, slave = pty.openpty()
        _set_size(slave, 24, 80)
        env = dict(os.environ)
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(SRC_ROOT), "TERM": "dumb"})
        if extra_env:
            env.update(extra_env)

        def _attach_ctty() -> None:
            os.setsid()
            fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

        self.process = subprocess.Popen(
            [sys.executable, "-c", code],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=_attach_ctty,
            close_fds=True,
            env=env,
        )
        os.close(slave)
        self.output = bytearray()

    def read_until(self, needle: bytes, timeout: float = TIMEOUT) -> bytes:
        deadline = time.monotonic() + timeout
        while needle not in self.output and time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], max(0, deadline - time.monotonic()))
            if not ready:
                continue
            try:
                chunk = os.read(self.master, 65536)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    # Transient: slave side fully closed between spawn and the
                    # child opening /dev/tty; data queues on the master later.
                    time.sleep(0.05)
                    continue
                raise
            if not chunk:
                break
            self.output.extend(chunk)
        if needle not in self.output:
            raise AssertionError(f"did not observe {needle!r}; output={bytes(self.output)!r}")
        return bytes(self.output)

    def send(self, data: bytes) -> None:
        os.write(self.master, data)

    def finish(self, timeout: float = TIMEOUT) -> tuple[int, bytes, bytes]:
        deadline = time.monotonic() + timeout
        while self.process.poll() is None and time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        time.sleep(0.05)
                        continue
                    raise
                if chunk:
                    self.output.extend(chunk)
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=2)
            raise AssertionError(f"child timed out; output={bytes(self.output)!r}")
        while True:
            ready, _, _ = select.select([self.master], [], [], 0.2)
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
        os.close(self.master)
        pipe_out = self.process.stdout.read() if self.process.stdout else b""
        if self.process.stdin is not None:
            self.process.stdin.close()
        if self.process.stdout is not None:
            self.process.stdout.close()
        return self.process.returncode, bytes(self.output), pipe_out

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=2)
        try:
            os.close(self.master)
        except OSError:
            pass
        if self.process.stdin is not None:
            self.process.stdin.close()
        if self.process.stdout is not None:
            self.process.stdout.close()


class SessionsAndTransitionPTYTests(unittest.TestCase):
    """Interactive sessions Table and transition diff view on a real PTY."""

    def _temp(self, prefix: str) -> tuple[str, Path]:
        temp = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(__import__("shutil").rmtree, temp, True)
        return temp, _secret_file(temp)

    def _sessions_code(self, temp: str, secret_file: Path, argv: list[str]) -> str:
        return f"""
import sys
from pathlib import Path
from claude_multi import composition, sessions, scope as scope_mod
from claude_multi.cli import Runtime, main
root = Path({str(CATALOG_ROOT)!r})
base = Path({temp!r})
env = {{'HOME': str(base/'home'), 'XDG_CONFIG_HOME': str(base/'config'), 'XDG_STATE_HOME': str(base/'state'), 'CLAUDE_MULTI_SECRET_ENV': {str(secret_file)!r}}}
def fake(prepared):
    print('FAKE_LAUNCH=' + prepared.result.session_action.kind, flush=True)
    return 0
runtime = Runtime(asset_root=root, environ=env, cwd=base/'project', launch_callback=fake, doctor_binary_callback=lambda contract: ([], ['fixture binary verified.']), doctor_callback=lambda _runtime: [])
document = runtime.compositions.load('default')
resolved = runtime.resolve_document(document)
record = sessions.make_record(
    session_id='11111111-1111-4111-8111-111111111111',
    cwd=runtime.cwd,
    composition_name='default',
    snapshot=composition.snapshot(resolved),
    catalog_version=runtime.catalog_version,
    catalog_hash=runtime.catalog.bundle_sha256,
    launcher_version=runtime.launcher_version,
    mode='durable',
    scope_generation=2,
)
runtime.session_store.save(record)
plan = scope_mod.compile_scope(
    resolved,
    runtime.catalog.docs['roles']['roles'],
    runtime.catalog.prompt_bodies,
    scope_mod.catalog_meta_from_docs(runtime.catalog.docs),
)
scope_mod.write_scope(runtime.session_store.root, record['managed_id'], plan)
shifted = runtime.compositions.load('default')
shifted['name'] = 'shifted'
shifted['slots'][0]['model'] = 'sol'
runtime.compositions.save(shifted)
raise SystemExit(main({argv!r}, runtime=runtime, input_stream=sys.stdin, output_stream=sys.stdout, interactive=True))
"""

    def test_sessions_table_quit_restores_terminal(self) -> None:
        temp, secret_file = self._temp("claude-multi-pty-sessions-")
        child = PTYProcess(self._sessions_code(temp, secret_file, ["sessions", "list"]))
        self.addCleanup(child.close)
        child.read_until(b"durable(g2)")
        child.send(b"\x1b")
        code, output, after = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"cm:default", output)
        mask = termios.ECHO | termios.ICANON
        self.assertEqual(child.before[3] & mask, after[3] & mask)

    def test_sessions_table_resume_launches(self) -> None:
        temp, secret_file = self._temp("claude-multi-pty-sessions-r-")
        child = PTYProcess(self._sessions_code(temp, secret_file, ["sessions", "list"]))
        self.addCleanup(child.close)
        child.read_until(b"durable(g2)")
        child.send(b"r")
        child.read_until(b"Resume")
        child.send(b"\n")
        code, output, _ = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"FAKE_LAUNCH=resume", output)

    def test_transition_diff_modal_relaunches(self) -> None:
        temp, secret_file = self._temp("claude-multi-pty-transition-")
        child = PTYProcess(
            self._sessions_code(
                temp,
                secret_file,
                [
                    "sessions",
                    "transition",
                    "11111111-1111-4111-8111-111111111111",
                    "--composition",
                    "shifted",
                ],
            )
        )
        self.addCleanup(child.close)
        child.read_until(b"semantic diff")
        child.read_until(b"lead model: opus5 -> sol")
        child.send(b"\n")
        child.read_until(b"EXITED (not merely idle)")
        child.send(b"\n")
        code, output, _ = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"FAKE_LAUNCH=resume", output)

    def test_transition_esc_prints_command_without_mutating(self) -> None:
        temp, secret_file = self._temp("claude-multi-pty-transition-esc-")
        child = PTYProcess(
            self._sessions_code(
                temp,
                secret_file,
                [
                    "sessions",
                    "transition",
                    "11111111-1111-4111-8111-111111111111",
                    "--composition",
                    "shifted",
                ],
            )
        )
        self.addCleanup(child.close)
        child.read_until(b"semantic diff")
        child.send(b"\x1b")
        code, output, _ = child.finish()
        self.assertEqual(code, 0, output)
        self.assertIn(b"claude-multi sessions transition", output)
        self.assertNotIn(b"FAKE_LAUNCH", output)


class BareLaunchAutoDetectionPTYTests(unittest.TestCase):
    """Bare launch auto-detection with no injected interactive flag."""

    def test_no_controlling_terminal_still_reaches_quick_confirm(self) -> None:
        temp = tempfile.mkdtemp(prefix="claude-multi-pty-bare-")
        self.addCleanup(__import__("shutil").rmtree, temp, True)
        child = _NoCttyChild(_bare_launch_code(temp, _secret_file(temp)))
        self.addCleanup(child.close)
        # /dev/tty cannot be opened here (no controlling terminal), but both
        # standard streams are TTYs, so the fallback must engage.
        child.read_until(b"Status         Ready")
        child.send(b"q\n")  # cancel at quick-confirm; never launches
        returncode, output = child.finish()
        self.assertEqual(returncode, 0, output)
        self.assertNotIn(b"FAKE_LAUNCH", output)

    def test_controlling_terminal_pipes_stdio_still_uses_dev_tty(self) -> None:
        temp = tempfile.mkdtemp(prefix="claude-multi-pty-ctty-")
        self.addCleanup(__import__("shutil").rmtree, temp, True)
        child = _CttyPipesChild(_bare_launch_code(temp, _secret_file(temp)))
        self.addCleanup(child.close)
        # stdin/stdout are pipes, but /dev/tty exists: the quick-confirm must
        # render on the controlling terminal (PTY master), not the pipes.
        child.read_until(b"Status         Ready")
        child.send(b"q\n")  # cancel at quick-confirm; never launches
        returncode, tty_output, pipe_output = child.finish()
        self.assertEqual(returncode, 0, tty_output)
        self.assertIn(b"Status         Ready", tty_output)
        self.assertNotIn(b"Status         Ready", pipe_output)
        self.assertNotIn(b"FAKE_LAUNCH", tty_output + pipe_output)

    def test_curses_quick_confirm_routes_to_controlling_terminal(self) -> None:
        temp = tempfile.mkdtemp(prefix="claude-multi-pty-ctty-curses-")
        self.addCleanup(__import__("shutil").rmtree, temp, True)
        child = _CttyPipesChild(
            _bare_launch_code(temp, _secret_file(temp)),
            extra_env={"TERM": "xterm-256color"},
        )
        self.addCleanup(child.close)
        # Full-screen UI must paint on /dev/tty (the PTY master), never the pipes.
        child.read_until(b"composition: default")
        child.send(b"\x1b")
        returncode, tty_output, pipe_output = child.finish()
        self.assertEqual(returncode, 0, tty_output)
        self.assertIn(b"composition: default", tty_output)
        self.assertNotIn(b"composition: default", pipe_output)
        self.assertNotIn(b"FAKE_LAUNCH", tty_output + pipe_output)

    def test_genuine_pipe_fails_with_explicit_composition_error(self) -> None:
        temp = tempfile.mkdtemp(prefix="claude-multi-pty-pipe-")
        self.addCleanup(__import__("shutil").rmtree, temp, True)
        env = dict(os.environ)
        env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(SRC_ROOT), "TERM": "dumb"})
        process = subprocess.Popen(
            [sys.executable, "-c", _bare_launch_code(temp, _secret_file(temp))],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            preexec_fn=os.setsid,  # hermetic: no controlling terminal at all
            env=env,
        )
        self.addCleanup(lambda: process.poll() is None and (process.kill(), process.wait()))
        output, _ = process.communicate(timeout=TIMEOUT * 2)
        self.assertEqual(process.returncode, 2, output)
        self.assertIn(b"noninteractive launch requires --composition NAME", output)
        self.assertNotIn(b"Status         Ready", output)
        self.assertNotIn(b"FAKE_LAUNCH", output)
