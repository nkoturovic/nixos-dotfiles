"""Phase-1B disposable no-provider capability probe harness.

This module backs the explicitly gated ``claude-multi-dev probe`` command
used by the G1 capability probes (blueprint §12, plan §§5.1–5.5). It builds
a fully disposable fixture — private HOME/XDG/config/state/cache/runtime
directories, zero provider tokens — and a deterministic loopback-only fake
Anthropic endpoint, so the pinned Claude binary can later be exercised
without any real provider, credential, user transcript, or contact with the
live shared daemon.

Fail-closed refusals:

- missing ``--allow-local-claude`` or ``--fixture-root``
  (``--allow-local-claude`` is presence-based explicit consent: its value
  is ignored and it never relaxes any other check);
- fixture roots that are relative, contain ``..``, resolve through a
  symlink (leaf or any ancestor), are not owner-private, or that equal,
  contain, or enter the live HOME, XDG, ``CLAUDE_CONFIG_DIR``, Claude
  config, claude-multi state, or uid-shared daemon paths (live roots are
  canonicalized before comparison, so symlinked environment roots cannot
  hide an overlap);
- inherited provider credentials in the ambient environment;
- non-loopback or non-http provider endpoints (only the ``127.0.0.1``
  literal is accepted; IPv6 is rejected consistently rather than bound);
- native execution whose trusted executable spec does not pin the exact
  absolute non-symlink path and full SHA-256 of the artifact, or whose
  artifact is group/other-writable.

Daemon-isolation posture: the fixture does NOT redirect Claude's uid-keyed
``/tmp/cc-daemon-<uid>`` daemon domain. Static evidence in
the pinned 2.1.217 binary shows the domain is parameterized per config root
(``cc-daemon-${h5g()}-${e}``, a ``sha256(config-root)[:8]`` subdomain, and the
redaction pattern ``cc-daemon-[0-9a-f]{16}``), so a fixture
``CLAUDE_CONFIG_DIR`` should yield a private daemon domain. The real pinned
binary may therefore run under a narrow allowance (``run_native(...,
allow_real=True)``): CLAUDE_CONFIG_DIR inside the disposable fixture root,
the trusted path+sha256 check, the loopback fake provider, a clean ambient
provider-credential scan, and a pre/post snapshot of the live uid-shared
domain that fails closed with "live daemon domain touched" on any new,
removed, or changed entry. Sibling per-config-root domains next to the
live one (the parameterized ``cc-daemon-${h5g()}-${e}`` naming) are
recorded before and after the run, names only, as the fixture-domain
observation: positive evidence that the fixture received its own domain.
Without ``allow_real`` only fake-executable unit probes (trusted spec
marked ``fake=True``) run.

The probed process never inherits the ambient environment: it receives only
the constructed disposable variables plus a fixed non-secret dummy token.
Captured request evidence is metadata only (method, path, model, tool
names, auth classification, body SHA-256); prompt and transcript content is
never persisted to disk or evidence.

Residual: the lstat/hash-to-exec window cannot be fully closed without
exec-time fd-based verification; a same-uid attacker replacing the pinned
artifact between hashing and exec is a recorded, accepted residual for this
probe harness.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import pty
import re
import select
import signal
import stat
import struct
import subprocess
import sys
import termios
import threading
import time
import urllib.parse
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from . import state, strict_json


class ProbeError(RuntimeError):
    """Raised on any probe safety violation (fail closed)."""


DUMMY_TOKEN = "claude-multi-probe-dummy-token"

_LOOPBACK_HOSTS = ("127.0.0.1",)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MAX_BODY_BYTES = 1024 * 1024
_RECORD_CAP = 1024
_MESSAGE_HASH_CAP = 64
_TOOL_NAME_CAP = 64
_METADATA_TEXT_MAX_BYTES = 160
_METADATA_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,127}")
_PUBLIC_METADATA_PATHS = frozenset(
    {"/healthz", "/v1/messages", "/v1/messages/count_tokens", "/v1/models"}
)
_MIN_AUTO_COMPACT_WINDOW = 100_000
_MAX_AUTO_COMPACT_WINDOW = 1_000_000
_REAL_RUN_REFUSAL = (
    "refusing real native execution without allow_real: the pinned Claude "
    "binary runs only under the narrow allowance — run_native(..., "
    "allow_real=True) with CLAUDE_CONFIG_DIR inside the disposable fixture "
    "root, the loopback fake provider, a clean ambient credential scan, and "
    "the live-domain pre/post snapshot tripwire; without it only "
    "fake-executable unit probes (trusted spec marked fake=True) run."
)
_PROVIDER_PREFIXES = (
    "ANTHROPIC",
    "CLAUDE",
    "OPENAI",
    "GEMINI",
    "GOOGLE",
    "MISTRAL",
    "COHERE",
    "MOONSHOT",
    "KIMI",
    "DEEPSEEK",
    "AWS",
    "AZURE",
    "GROQ",
    "XAI",
    "HF",
    "HUGGINGFACE",
    "OPENROUTER",
    "TOGETHER",
    "FIREWORKS",
    "PERPLEXITY",
    "LITELLM",
    "HELICONE",
    "PORTKEY",
)
_CREDENTIAL_MARKERS = ("KEY", "TOKEN", "SECRET", "PASS")


# ------------------------------------------------------------- credentials


def assert_no_provider_credentials(environ: Mapping[str, str]) -> None:
    """Refuse an ambient environment carrying provider credentials.

    Offending variable names are reported; values are never read into
    messages or evidence.
    """

    offenders = sorted(
        name
        for name, value in environ.items()
        if value
        and any(marker in name.upper() for marker in _CREDENTIAL_MARKERS)
        and name.upper().startswith(_PROVIDER_PREFIXES)
    )
    if offenders:
        raise ProbeError(
            "inherited provider credentials are forbidden in the probe "
            "environment; unset them first: " + ", ".join(offenders)
        )


# ------------------------------------------------------------ path safety


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _live_roots(environ: Mapping[str, str]) -> dict[str, Path]:
    """Live paths a fixture root must never equal, contain, or enter.

    Ordered most-specific first so containment refusals name the tightest
    live domain; equality is checked in a separate first pass.
    """

    home = Path(environ.get("HOME") or Path.home())
    state_home = Path(environ.get("XDG_STATE_HOME") or home / ".local" / "state")
    roots = {
        "Claude config root": home / ".claude",
        "claude-multi state root": state_home / "claude-multi",
        "uid-shared daemon socket": Path(f"/tmp/cc-daemon-{os.geteuid()}"),
        "XDG_CONFIG_HOME": Path(environ.get("XDG_CONFIG_HOME") or home / ".config"),
        "XDG_STATE_HOME": state_home,
        "XDG_CACHE_HOME": Path(environ.get("XDG_CACHE_HOME") or home / ".cache"),
        "XDG_DATA_HOME": Path(
            environ.get("XDG_DATA_HOME") or home / ".local" / "share"
        ),
        "HOME": home,
    }
    runtime = environ.get("XDG_RUNTIME_DIR")
    if runtime:
        roots["XDG_RUNTIME_DIR"] = Path(runtime)
    claude_dir = environ.get("CLAUDE_CONFIG_DIR")
    if claude_dir:
        roots["CLAUDE_CONFIG_DIR"] = Path(claude_dir)
    return roots


def _resolve_fixture_root(root: Path | str) -> Path:
    candidate = Path(root)
    if not candidate.is_absolute():
        raise ProbeError(f"fixture root {candidate} must be an absolute path")
    if ".." in candidate.parts:
        raise ProbeError(f"fixture root {candidate} must not contain '..'")
    real = Path(os.path.realpath(candidate))
    if real != candidate:
        raise ProbeError(
            f"fixture root {candidate} resolves through a symlink or non-normal "
            f"component to {real}; only real private paths are allowed"
        )
    return real


def _check_live_roots(root: Path, environ: Mapping[str, str]) -> None:
    # Canonicalize every live root: an environment root that passes through
    # a symlink must still refuse a fixture overlapping its real target.
    live_roots = {
        label: Path(os.path.realpath(live))
        for label, live in _live_roots(environ).items()
    }
    for label, live in live_roots.items():
        if root == live:
            raise ProbeError(f"fixture root {root} is the live {label} path")
    for label, live in live_roots.items():
        if _is_within(live, root):
            raise ProbeError(
                f"fixture root {root} contains the live {label} path {live}"
            )
        # Disposable trees under HOME itself are fine; nesting inside a live
        # config/state/daemon tree is never fine.
        if label != "HOME" and _is_within(root, live):
            raise ProbeError(
                f"fixture root {root} is inside the live {label} path {live}"
            )


def _ensure_private(path: Path, what: str) -> Path:
    try:
        return state.ensure_private_dir(path)
    except (state.StateError, OSError) as exc:
        raise ProbeError(
            f"{what} {path} is not a safe private directory: {exc}"
        ) from exc


# --------------------------------------------------------------- endpoints


def check_loopback_url(base_url: str) -> str:
    """Require an explicit http loopback URL with an explicit port."""

    parts = urllib.parse.urlsplit(base_url)
    if parts.scheme != "http":
        raise ProbeError(f"provider endpoint {base_url!r} is not loopback http")
    if parts.hostname not in _LOOPBACK_HOSTS:
        raise ProbeError(f"provider endpoint {base_url!r} is not loopback http")
    try:
        port = parts.port
    except ValueError as exc:
        raise ProbeError(
            f"provider endpoint {base_url!r} has an invalid port"
        ) from exc
    if port is None:
        raise ProbeError(f"provider endpoint {base_url!r} requires an explicit port")
    return base_url


# ----------------------------------------------------------- daemon domain


def live_daemon_domain() -> Path:
    """The uid-shared daemon domain the pinned binary namespaces per root."""

    return Path(f"/tmp/cc-daemon-{os.geteuid()}")


def expected_daemon_subdomain(config_dir: Path | str) -> str:
    """Config-root subdomain name the pinned binary derives (static evidence).

    The 2.1.217 binary computes ``sha256(resolve(config_root))[:8]``; fixture
    paths are symlink-free by construction, so ``os.path.realpath`` matches
    the binary's ``path.resolve``.
    """

    resolved = os.path.realpath(config_dir)
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class DaemonDomainEntry:
    """One live-domain entry: hash-level metadata only, never contents."""

    name: str
    kind: str  # "dir" | "file" | "sock" | "other" | "vanished"
    mtime_ns: int
    size: int


@dataclass(frozen=True)
class DaemonDomainSnapshot:
    """Shallow snapshot of the uid-shared domain: entry set plus mtimes."""

    domain: Path
    present: bool
    dir_mtime_ns: int | None
    entries: tuple[DaemonDomainEntry, ...]


def snapshot_daemon_domain(domain: Path | None = None) -> DaemonDomainSnapshot:
    """Snapshot the live daemon domain's entry set and mtimes.

    Absence is tolerated and recorded as ``present=False``; the scan is
    shallow (one level) so the live supervisor's internal socket churn
    beneath its own subdomain does not register as a change.
    """

    target = domain if domain is not None else live_daemon_domain()
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return DaemonDomainSnapshot(target, False, None, ())
    if not stat.S_ISDIR(info.st_mode):
        raise ProbeError(f"live daemon domain {target} is not a directory")
    entries: list[DaemonDomainEntry] = []
    with os.scandir(target) as iterator:
        for entry in iterator:
            try:
                meta = os.lstat(entry.path)
            except FileNotFoundError:
                entries.append(DaemonDomainEntry(entry.name, "vanished", 0, 0))
                continue
            if stat.S_ISDIR(meta.st_mode):
                kind = "dir"
            elif stat.S_ISREG(meta.st_mode):
                kind = "file"
            elif stat.S_ISSOCK(meta.st_mode):
                kind = "sock"
            else:
                kind = "other"
            entries.append(
                DaemonDomainEntry(entry.name, kind, meta.st_mtime_ns, meta.st_size)
            )
    entries.sort(key=lambda item: item.name)
    return DaemonDomainSnapshot(target, True, info.st_mtime_ns, tuple(entries))


def fixture_daemon_domains(live: Path) -> tuple[str, ...]:
    """Names of sibling per-config-root daemon domains next to ``live``.

    Metadata-only directory listing (names only, never contents). The
    parameterized ``cc-daemon-${h5g()}-${e}`` naming implies non-default
    config roots get sibling domains next to the uid-shared one; the live
    uid-shared domain itself is excluded. Absence is tolerated.
    """

    try:
        with os.scandir(live.parent) as iterator:
            names = [
                entry.name
                for entry in iterator
                if entry.name.startswith("cc-daemon-") and entry.name != live.name
            ]
    except FileNotFoundError:
        return ()
    return tuple(sorted(names))


def _domain_changes(
    before: DaemonDomainSnapshot, after: DaemonDomainSnapshot
) -> tuple[str, ...]:
    """Name-level diff of two snapshots; empty means byte-identical."""

    if before.domain != after.domain:
        raise ProbeError("daemon domain snapshots cover different domains")
    changes: list[str] = []
    if before.present != after.present:
        changes.append("domain-appeared" if after.present else "domain-vanished")
    elif before.present and before.dir_mtime_ns != after.dir_mtime_ns:
        changes.append("domain-dir-mtime")
    prior = {entry.name: entry for entry in before.entries}
    current = {entry.name: entry for entry in after.entries}
    for name in sorted(current.keys() - prior.keys()):
        changes.append(f"added:{name}")
    for name in sorted(prior.keys() - current.keys()):
        changes.append(f"removed:{name}")
    for name in sorted(prior.keys() & current.keys()):
        if prior[name] != current[name]:
            changes.append(f"changed:{name}")
    return tuple(changes)


@dataclass(frozen=True)
class DaemonDomainObservation:
    """Metadata-only pre/post record of the live-domain tripwire.

    ``fixture_domains_before``/``fixture_domains_after`` are the sibling
    per-config-root domain names observed next to the live domain (never
    their contents); ``fixture_subdomain_observed`` is true when the
    fixture's expected config-root hash subdomain is visible either as a
    live-domain entry or inside a sibling domain name.
    """

    domain: str
    present_before: bool
    present_after: bool
    entries_before: tuple[str, ...]
    entries_after: tuple[str, ...]
    unchanged: bool
    expected_fixture_subdomain: str
    fixture_subdomain_observed: bool
    fixture_domains_before: tuple[str, ...] = ()
    fixture_domains_after: tuple[str, ...] = ()


def _enforce_live_domain_untouched(
    before: DaemonDomainSnapshot,
    after: DaemonDomainSnapshot,
    *,
    fixture: "ProbeFixture",
    fixture_domains_before: tuple[str, ...] = (),
    fixture_domains_after: tuple[str, ...] = (),
) -> DaemonDomainObservation:
    """Fail closed unless the live uid-shared domain is byte-identical.

    Any new, removed, or changed entry (or the domain (dis)appearing) is a
    hard failure: the run touched the live daemon domain. This tripwire is
    strictly observe-only and never attempts remediation inside the live
    domain, including entries that appear fixture-owned.
    """

    expected = expected_daemon_subdomain(fixture.claude_config_dir)
    observed = any(entry.name == expected for entry in after.entries) or any(
        expected in name for name in fixture_domains_after
    )
    changes = _domain_changes(before, after)
    if changes:
        raise ProbeError(
            "live daemon domain touched: "
            + ", ".join(changes)
            + " (observe-only; no remediation performed)"
        )
    return DaemonDomainObservation(
        domain=str(after.domain),
        present_before=before.present,
        present_after=after.present,
        entries_before=tuple(entry.name for entry in before.entries),
        entries_after=tuple(entry.name for entry in after.entries),
        unchanged=True,
        expected_fixture_subdomain=expected,
        fixture_subdomain_observed=observed,
        fixture_domains_before=fixture_domains_before,
        fixture_domains_after=fixture_domains_after,
    )


# ----------------------------------------------------------------- fixture


@dataclass(frozen=True)
class ProbeCompactionPolicy:
    """Narrow allowlisted compaction controls for disposable native probes."""

    auto_compact_window: int
    auto_compact_percent: int
    max_context_tokens: int | None = None

    def environ(self) -> dict[str, str]:
        if not _MIN_AUTO_COMPACT_WINDOW <= self.auto_compact_window <= _MAX_AUTO_COMPACT_WINDOW:
            raise ProbeError(
                "probe auto-compaction window must be between 100000 and 1000000"
            )
        if not 1 <= self.auto_compact_percent <= 100:
            raise ProbeError("probe auto-compaction percentage must be 1..100")
        env = {
            "CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(self.auto_compact_window),
            "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": str(self.auto_compact_percent),
        }
        if self.max_context_tokens is not None:
            if self.max_context_tokens <= 0:
                raise ProbeError("probe max context tokens must be positive")
            env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = str(self.max_context_tokens)
        return env


@dataclass(frozen=True)
class ProbeFixture:
    """Disposable probe filesystem domain; every path lives under ``root``."""

    root: Path
    home: Path
    xdg_config_home: Path
    xdg_state_home: Path
    xdg_cache_home: Path
    xdg_data_home: Path
    xdg_runtime_dir: Path
    claude_config_dir: Path

    def environ(
        self,
        *,
        base_url: str | None = None,
        compaction: ProbeCompactionPolicy | None = None,
    ) -> dict[str, str]:
        """Disposable process environment; never derived from os.environ.

        With ``base_url`` the loopback fake provider is wired in using the
        fixed non-secret dummy token; without it no provider variable exists.
        ``compaction`` may add only the three typed context controls above.
        """

        env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.xdg_config_home),
            "XDG_STATE_HOME": str(self.xdg_state_home),
            "XDG_CACHE_HOME": str(self.xdg_cache_home),
            "XDG_DATA_HOME": str(self.xdg_data_home),
            "XDG_RUNTIME_DIR": str(self.xdg_runtime_dir),
            "CLAUDE_CONFIG_DIR": str(self.claude_config_dir),
            # Explicit minimal PATH: no empty entry, never the ambient PATH.
            "PATH": "/usr/bin:/bin",
            "TERM": "xterm-256color",
            "LANG": "C.UTF-8",
            "DISABLE_AUTOUPDATER": "1",
        }
        if base_url is not None:
            env["ANTHROPIC_BASE_URL"] = check_loopback_url(base_url)
            env["ANTHROPIC_AUTH_TOKEN"] = DUMMY_TOKEN
        if compaction is not None:
            env.update(compaction.environ())
        return env


def build_fixture(
    root: Path | str, *, environ: Mapping[str, str] | None = None
) -> ProbeFixture:
    """Create and validate the disposable fixture domain (fail closed).

    An existing fixture is revalidated and reused; every directory must be
    owner-controlled mode 0700 and no path may be a symlink.
    """

    ambient = os.environ if environ is None else environ
    resolved = _resolve_fixture_root(root)
    _check_live_roots(resolved, ambient)
    _ensure_private(resolved, "fixture root")
    home = _ensure_private(resolved / "home", "disposable HOME")
    xdg = _ensure_private(resolved / "xdg", "disposable XDG root")
    runtime = _ensure_private(resolved / "runtime", "disposable runtime root")
    return ProbeFixture(
        root=resolved,
        home=home,
        xdg_config_home=_ensure_private(xdg / "config", "disposable XDG config"),
        xdg_state_home=_ensure_private(xdg / "state", "disposable XDG state"),
        xdg_cache_home=_ensure_private(xdg / "cache", "disposable XDG cache"),
        xdg_data_home=_ensure_private(xdg / "data", "disposable XDG data"),
        xdg_runtime_dir=runtime,
        claude_config_dir=_ensure_private(
            resolved / "claude-config", "disposable Claude config root"
        ),
    )


def fixture_manifest(fixture: ProbeFixture) -> dict[str, Any]:
    """Machine-readable fixture summary: paths only, never tokens."""

    return {
        "version": 1,
        "root": str(fixture.root),
        "dirs": {
            "home": str(fixture.home),
            "xdg_config_home": str(fixture.xdg_config_home),
            "xdg_state_home": str(fixture.xdg_state_home),
            "xdg_cache_home": str(fixture.xdg_cache_home),
            "xdg_data_home": str(fixture.xdg_data_home),
            "xdg_runtime_dir": str(fixture.xdg_runtime_dir),
            "claude_config_dir": str(fixture.claude_config_dir),
        },
    }


def write_evidence(
    fixture: ProbeFixture, name: str, record: dict[str, Any]
) -> Path:
    """Persist a machine-readable probe artifact inside the private fixture."""

    evidence_dir = state.ensure_private_dir(fixture.root / "evidence")
    path = evidence_dir / f"{state.check_name(name)}.json"
    state.atomic_write(path, strict_json.canonical_file_bytes(record))
    return path


# ------------------------------------------------------------ fake provider


def _bounded_metadata_text(value: str, pattern: re.Pattern[str]) -> str:
    """Keep short safe identifiers; hash arbitrary request-controlled text."""

    data = value.encode("utf-8")
    if len(data) <= _METADATA_TEXT_MAX_BYTES and pattern.fullmatch(value):
        return value
    return f"sha256:{strict_json.sha256_hex(data)}:bytes={len(data)}"


def _bounded_request_path(value: str) -> str:
    """Retain only known credential-free endpoints; hash every other target."""

    parsed = urllib.parse.urlsplit(value)
    if not parsed.query and not parsed.fragment and parsed.path in _PUBLIC_METADATA_PATHS:
        return parsed.path
    data = value.encode("utf-8")
    return f"sha256:{strict_json.sha256_hex(data)}:bytes={len(data)}"


@dataclass(frozen=True)
class RequestRecord:
    """Captured request metadata; prompt/transcript content is never stored."""

    method: str
    path: str
    model: str | None
    tool_names: tuple[str, ...]
    tool_count: int
    tool_names_truncated: bool
    has_system: bool
    system_json_bytes: int
    messages_json_bytes: int
    message_count: int
    message_sha256s: tuple[str, ...]
    message_hashes_truncated: bool
    auth: str  # "dummy" | "other" | "absent"
    body_sha256: str


@dataclass(frozen=True)
class SseResponse:
    """Server-sent-events reply for clients that request ``stream: true``.

    Each event is an ``(event, data)`` pair serialized per the Anthropic
    streaming wire shape; payloads carry the same canned content as the
    JSON replies, never request content.
    """

    events: tuple[tuple[str, dict[str, Any]], ...]


def _default_responder(document: dict[str, Any], path: str) -> tuple[int, dict[str, Any]]:
    """Deterministic canned Anthropic-shaped reply.

    Echoes the requested model; emits a fixed ``tool_use`` block for the
    first (or forced) tool only when the client supplies tools with an
    ``any``/``tool`` tool_choice, else a fixed text block. This keeps
    registry/deny tool-use probes deterministic: the fake only ever echoes
    what the client actually sent.
    """

    model = document.get("model")
    tools = document.get("tools") or []
    tool_names = [
        tool["name"]
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    ]
    tool_choice = document.get("tool_choice")
    if tool_names and isinstance(tool_choice, dict) and tool_choice.get("type") in (
        "any",
        "tool",
    ):
        forced = tool_choice.get("name")
        name = forced if forced in tool_names else tool_names[0]
        content = [
            {"type": "tool_use", "id": "toolu_probe_0001", "name": name, "input": {}}
        ]
        stop_reason = "tool_use"
    else:
        content = [{"type": "text", "text": "PROBE-OK"}]
        stop_reason = "end_turn"
    return 200, {
        "id": "msg_probe_0001",
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model if isinstance(model, str) else "probe-model",
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


class _ProviderServer(ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        # A probed client SIGKILLed mid-request resets the connection; that
        # teardown noise is expected and never evidentiary.
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


class _ProviderHandler(BaseHTTPRequestHandler):
    server_version = "claude-multi-probe/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = strict_json.canonical_file_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_sse(self, status: int, payload: SseResponse) -> None:
        chunks: list[bytes] = []
        for event, data in payload.events:
            chunks.append(f"event: {event}\n".encode("utf-8"))
            chunks.append(b"data: " + strict_json.canonical_bytes(data) + b"\n\n")
        body = b"".join(chunks)
        self.send_response(status)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.wfile.flush()

    def _error(self, status: int, kind: str, message: str) -> None:
        self._send_json(
            status, {"type": "error", "error": {"type": kind, "message": message}}
        )

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
            return
        if self.path == "/v1/models":
            self._send_json(
                200,
                {
                    "data": [
                        {"id": "gpt-multi-sol-high", "type": "model"},
                        {"id": "claude-multi-qwen38-max", "type": "model"},
                        {"id": "claude-multi-kimi-k3", "type": "model"},
                        {"id": "claude-fable-5", "type": "model"},
                    ],
                    "has_more": False,
                    "first_id": "gpt-multi-sol-high",
                    "last_id": "claude-fable-5",
                },
            )
            return
        self._error(404, "not_found_error", "probe: unknown path")

    def do_POST(self) -> None:
        provider: "FakeAnthropicProvider" = self.server.provider  # type: ignore[attr-defined]
        values = self.headers.get_all("Content-Length") or []
        if not values:
            self.close_connection = True
            self._error(411, "invalid_request_error", "probe: missing Content-Length")
            return
        if len(set(values)) != 1:
            self.close_connection = True
            self._error(
                400, "invalid_request_error", "probe: conflicting Content-Length"
            )
            return
        try:
            length = int(values[0])
        except ValueError:
            self.close_connection = True
            self._error(
                400, "invalid_request_error", "probe: non-numeric Content-Length"
            )
            return
        if length < 0:
            self.close_connection = True
            self._error(
                400, "invalid_request_error", "probe: negative Content-Length"
            )
            return
        if length > _MAX_BODY_BYTES:
            self.close_connection = True
            self._error(
                413,
                "invalid_request_error",
                f"probe: body exceeds {_MAX_BODY_BYTES} bytes",
            )
            return
        body = self.rfile.read(length) if length else b""
        document: Any = None
        try:
            if body:
                document = strict_json.loads(body)
        except strict_json.StrictJSONError:
            document = None
        provider._record(self.command, self.path, self.headers, body, document)
        if not isinstance(document, dict):
            self._error(
                400, "invalid_request_error", "probe: body is not a JSON object"
            )
            return
        status, payload = provider._respond(document, self.path)
        if isinstance(payload, SseResponse):
            self._send_sse(status, payload)
        else:
            self._send_json(status, payload)


class FakeAnthropicProvider:
    """Deterministic loopback-only fake Anthropic endpoint.

    Binds ``127.0.0.1`` on an ephemeral port, answers ``GET /healthz`` and
    ``POST`` JSON requests with canned deterministic responses, and records
    request metadata in memory only. Nothing is written to disk and no
    request body, header value, or credential is retained — only the
    metadata captured in :class:`RequestRecord`.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        responder: Callable[
            [dict[str, Any], str], tuple[int, dict[str, Any] | SseResponse]
        ]
        | None = None,
    ):
        if host not in _LOOPBACK_HOSTS:
            raise ProbeError(f"fake provider host {host!r} is not a loopback literal")
        self._host = host
        self._responder = responder or _default_responder
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._records: list[RequestRecord] = []
        self._dropped = 0
        self._records_lock = threading.Lock()

    def start(self) -> "FakeAnthropicProvider":
        if self._server is not None:
            raise ProbeError("fake provider is already started")
        server = _ProviderServer((self._host, 0), _ProviderHandler)
        server.daemon_threads = True
        server.provider = self  # type: ignore[attr-defined]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._server = server
        self._thread = thread
        return self

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> "FakeAnthropicProvider":
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise ProbeError("fake provider is not started")
        host, port = self._server.server_address[:2]
        formatted = f"[{host}]" if ":" in host else host
        return f"http://{formatted}:{port}"

    @property
    def requests(self) -> tuple[RequestRecord, ...]:
        with self._records_lock:
            return tuple(self._records)

    @property
    def dropped_requests(self) -> int:
        """Records dropped after the bounded cap; spam stays evidentiary."""

        with self._records_lock:
            return self._dropped

    def _respond(
        self, document: dict[str, Any], path: str
    ) -> tuple[int, dict[str, Any] | SseResponse]:
        return self._responder(document, path)

    def _record(
        self, method: str, path: str, headers: Any, body: bytes, document: Any
    ) -> None:
        presented = headers.get("x-api-key") or ""
        if not presented:
            bearer = headers.get("Authorization") or ""
            presented = bearer[7:] if bearer.startswith("Bearer ") else bearer
        if not presented:
            auth = "absent"
        elif presented == DUMMY_TOKEN:
            auth = "dummy"
        else:
            auth = "other"
        model: str | None = None
        tool_names: tuple[str, ...] = ()
        tool_count = 0
        tool_names_truncated = False
        has_system = False
        system_json_bytes = 0
        messages_json_bytes = 0
        message_count = 0
        message_sha256s: tuple[str, ...] = ()
        message_hashes_truncated = False
        if isinstance(document, dict):
            candidate = document.get("model")
            model = (
                _bounded_metadata_text(candidate, _METADATA_ID_RE)
                if isinstance(candidate, str)
                else None
            )
            tools = document.get("tools")
            if isinstance(tools, list):
                names = [
                    tool["name"]
                    for tool in tools
                    if isinstance(tool, dict) and isinstance(tool.get("name"), str)
                ]
                tool_count = len(names)
                tool_names = tuple(
                    _bounded_metadata_text(name, _METADATA_ID_RE)
                    for name in names[:_TOOL_NAME_CAP]
                )
                tool_names_truncated = len(names) > len(tool_names)
            has_system = "system" in document
            if has_system:
                system_json_bytes = len(strict_json.canonical_bytes(document["system"]))
            messages = document.get("messages")
            if isinstance(messages, list):
                messages_json_bytes = len(strict_json.canonical_bytes(messages))
                message_count = len(messages)
                hashed = messages[:_MESSAGE_HASH_CAP]
                message_sha256s = tuple(
                    strict_json.sha256_hex(strict_json.canonical_bytes(message))
                    for message in hashed
                )
                message_hashes_truncated = len(messages) > len(hashed)
        record = RequestRecord(
            method=method,
            path=_bounded_request_path(path),
            model=model,
            tool_names=tool_names,
            tool_count=tool_count,
            tool_names_truncated=tool_names_truncated,
            has_system=has_system,
            system_json_bytes=system_json_bytes,
            messages_json_bytes=messages_json_bytes,
            message_count=message_count,
            message_sha256s=message_sha256s,
            message_hashes_truncated=message_hashes_truncated,
            auth=auth,
            body_sha256=strict_json.sha256_hex(body),
        )
        with self._records_lock:
            if len(self._records) >= _RECORD_CAP:
                self._dropped += 1
            else:
                self._records.append(record)


# -------------------------------------------------------------- native run


@dataclass(frozen=True)
class TrustedExecutable:
    """Pinned executable identity: exact resolved path and full SHA-256.

    ``fake=True`` marks a unit-test fake executable; anything else is a
    real native artifact and runs only under the narrow ``allow_real``
    allowance (fixture-root config dir, loopback fake provider, live-domain
    snapshot tripwire).
    """

    resolved_path: Path
    sha256: str
    fake: bool = False


def trusted_from_contract(native_contract: dict[str, Any]) -> TrustedExecutable:
    """Derive the trusted spec from the native-contract executable record."""

    try:
        record = native_contract["claude"]["executable"]
        resolved = record["resolved_path"]
        digest = record["sha256"]
    except (KeyError, TypeError) as exc:
        raise ProbeError(
            f"native contract lacks the claude.executable identity: {exc}"
        ) from exc
    if not isinstance(resolved, str) or not resolved:
        raise ProbeError("native contract resolved_path must be a nonempty string")
    if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
        raise ProbeError("native contract sha256 must be 64 lowercase hex digits")
    return TrustedExecutable(Path(resolved), digest, fake=False)


@dataclass(frozen=True)
class NativeRunResult:
    """Bounded outcome of one gated native run inside the fixture."""

    argv: tuple[str, ...]
    returncode: int | None  # None when the timeout killed the child
    timed_out: bool
    stdout: str
    stderr: str
    requests: tuple[RequestRecord, ...]
    daemon: DaemonDomainObservation | None = None


@dataclass(frozen=True)
class PTYInteraction:
    """Wait for one terminal marker, then send one bounded byte sequence.

    By default, terminal output already buffered after the marker is discarded
    for matching purposes so a repeated UI glyph cannot satisfy the next
    interaction. ``preserve_after_wait`` is for explicit two-stage barriers
    where the following marker may arrive in the same PTY read.
    """

    wait_for: bytes
    send: bytes
    preserve_after_wait: bool = False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_trusted_executable(trusted: TrustedExecutable) -> Path:
    """Verify the artifact exactly matches the pinned spec (fail closed).

    Identity is the exact absolute non-symlink path plus the full content
    SHA-256; group/other-writable artifacts are refused. Owner identity is
    deliberately not required: a root-owned immutable artifact at the exact
    pinned path with the pinned hash is acceptable, and an arbitrary user
    script is rejected unless a spec pins its exact path and hash.
    """

    path = trusted.resolved_path
    if not isinstance(trusted.sha256, str) or not _SHA256_RE.fullmatch(
        trusted.sha256
    ):
        raise ProbeError("trusted executable spec sha256 must be 64 lowercase hex")
    if not path.is_absolute():
        raise ProbeError(f"probe executable {path} must be an absolute path")
    real = Path(os.path.realpath(path))
    if real != path:
        raise ProbeError(
            f"probe executable {path} must not contain non-normal components "
            f"or resolve through a symlink"
        )
    try:
        info = os.lstat(real)
    except FileNotFoundError as exc:
        raise ProbeError(f"probe executable {real} does not exist") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ProbeError(f"probe executable {real} is not a regular file")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ProbeError(
            f"probe executable {real} must not be group/other-writable"
        )
    if not os.access(real, os.X_OK):
        raise ProbeError(f"probe executable {real} is not executable")
    if _sha256_file(real) != trusted.sha256:
        raise ProbeError(
            f"probe executable {real} content hash does not match the trusted "
            "executable spec"
        )
    return real


def _kill_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _assert_real_run_allowed(
    fixture: ProbeFixture, environ: Mapping[str, str]
) -> None:
    """Narrow allowance for the pinned binary (fail closed).

    The real spec may run only when the fixture's CLAUDE_CONFIG_DIR lives
    inside the disposable fixture root — the per-config-root daemon domain
    the static binary evidence implies — and the ambient environment carries
    no provider credentials. The trusted path+sha256 check, the loopback
    fake-provider check, and the live-domain pre/post snapshot tripwire run
    alongside in ``run_native``.
    """

    assert_no_provider_credentials(environ)
    root = Path(os.path.realpath(fixture.root))
    _check_live_roots(root, environ)
    config = Path(os.path.realpath(fixture.claude_config_dir))
    if config == root or not _is_within(config, root):
        raise ProbeError(
            "refusing real native execution: CLAUDE_CONFIG_DIR "
            f"{config} is not inside the disposable fixture root {root}"
        )
    try:
        info = os.lstat(config)
    except FileNotFoundError as exc:
        raise ProbeError(
            f"refusing real native execution: CLAUDE_CONFIG_DIR {config} "
            "does not exist"
        ) from exc
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_uid != os.geteuid()
    ):
        raise ProbeError(
            f"refusing real native execution: CLAUDE_CONFIG_DIR {config} "
            "is not an owner-private directory"
        )


def run_native(
    args: list[str] | tuple[str, ...],
    *,
    trusted: TrustedExecutable,
    fixture: ProbeFixture,
    provider: FakeAnthropicProvider | None = None,
    timeout: float = 60.0,
    allow_real: bool = False,
    environ: Mapping[str, str] | None = None,
    live_daemon_domain: Path | None = None,
    compaction: ProbeCompactionPolicy | None = None,
) -> NativeRunResult:
    """Run the trusted executable once inside the disposable fixture.

    Without ``allow_real`` a real (``fake=False``) spec is refused before any
    hashing or provider startup. With ``allow_real`` the pinned binary may
    run under the narrow allowance: CLAUDE_CONFIG_DIR inside the disposable
    fixture root, the trusted path+sha256 check, the loopback fake provider,
    and a clean ambient provider-credential scan (``environ`` is scan-only;
    the child never inherits it). The live uid-shared daemon domain is
    snapshotted immediately before and after the run and must be
    byte-identical — any new, removed, or changed entry fails closed with
    "live daemon domain touched". Fake-executable unit probes are verified
    against the pinned spec (exact path, full SHA-256, non-writable) before
    anything starts. The child receives only the fixture environment
    (loopback fake provider with the dummy token, zero real credentials, no
    ambient variables), runs in a new session/process group with the
    disposable HOME as CWD and a null stdin. ``compaction`` is a typed narrow
    override for only the context-capacity/percentage controls. The whole
    process group is SIGKILLed and reaped on timeout or error. A caller-supplied provider
    keeps its lifecycle; an internally created one is stopped
    deterministically.
    """

    if timeout <= 0:
        raise ProbeError("probe run timeout must be positive")
    real = not trusted.fake
    if real and not allow_real:
        # Refuse real specs before any hashing or provider startup.
        raise ProbeError(_REAL_RUN_REFUSAL)
    ambient = os.environ if environ is None else environ
    if real:
        _assert_real_run_allowed(fixture, ambient)
    executable = _verify_trusted_executable(trusted)
    command = (str(executable), *[str(argument) for argument in args])
    owned = provider is None
    if owned:
        provider = FakeAnthropicProvider().start()
    assert provider is not None
    try:
        if real:
            if not isinstance(provider, FakeAnthropicProvider):
                raise ProbeError(
                    "refusing real native execution: the provider must be the "
                    "loopback fake"
                )
            check_loopback_url(provider.base_url)
            live = (
                live_daemon_domain
                if live_daemon_domain is not None
                else Path(f"/tmp/cc-daemon-{os.geteuid()}")
            )
            domain_before = snapshot_daemon_domain(live)
            siblings_before = fixture_daemon_domains(live)
        else:
            domain_before = None
            siblings_before = ()
        env = fixture.environ(base_url=provider.base_url, compaction=compaction)
        process = subprocess.Popen(
            list(command),
            cwd=fixture.home,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            returncode: int | None = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(process)
            stdout, stderr = process.communicate()
            returncode = None
        except BaseException:
            _kill_process_group(process)
            process.wait()
            raise
        daemon: DaemonDomainObservation | None = None
        if domain_before is not None:
            domain_after = snapshot_daemon_domain(live)
            siblings_after = fixture_daemon_domains(live)
            # Hard failure on any change: the run touched the live domain.
            daemon = _enforce_live_domain_untouched(
                domain_before,
                domain_after,
                fixture=fixture,
                fixture_domains_before=siblings_before,
                fixture_domains_after=siblings_after,
            )
        return NativeRunResult(
            argv=command,
            returncode=returncode,
            timed_out=timed_out,
            stdout=stdout[-4000:],
            stderr=stderr[-4000:],
            requests=provider.requests,
            daemon=daemon,
        )
    finally:
        if owned:
            provider.stop()


def run_native_pty(
    args: list[str] | tuple[str, ...],
    interactions: list[PTYInteraction] | tuple[PTYInteraction, ...],
    *,
    trusted: TrustedExecutable,
    fixture: ProbeFixture,
    provider: FakeAnthropicProvider | None = None,
    timeout: float = 120.0,
    allow_real: bool = False,
    environ: Mapping[str, str] | None = None,
    live_daemon_domain: Path | None = None,
    compaction: ProbeCompactionPolicy | None = None,
    rows: int = 32,
    columns: int = 120,
) -> NativeRunResult:
    """Drive a trusted native client through a bounded disposable PTY script."""

    if timeout <= 0:
        raise ProbeError("probe PTY timeout must be positive")
    if rows <= 0 or columns <= 0:
        raise ProbeError("probe PTY dimensions must be positive")
    for interaction in interactions:
        if not interaction.wait_for:
            raise ProbeError("probe PTY wait marker must not be empty")
        if len(interaction.wait_for) > 4096 or len(interaction.send) > 65536:
            raise ProbeError("probe PTY interaction exceeds bounded size")
    real = not trusted.fake
    if real and not allow_real:
        raise ProbeError(_REAL_RUN_REFUSAL)
    ambient = os.environ if environ is None else environ
    if real:
        _assert_real_run_allowed(fixture, ambient)
    executable = _verify_trusted_executable(trusted)
    command = (str(executable), *[str(argument) for argument in args])
    owned = provider is None
    if owned:
        provider = FakeAnthropicProvider().start()
    assert provider is not None
    master: int | None = None
    slave: int | None = None
    process: subprocess.Popen[bytes] | None = None
    try:
        if real:
            if not isinstance(provider, FakeAnthropicProvider):
                raise ProbeError(
                    "refusing real native execution: the provider must be the "
                    "loopback fake"
                )
            check_loopback_url(provider.base_url)
            live = (
                live_daemon_domain
                if live_daemon_domain is not None
                else Path(f"/tmp/cc-daemon-{os.geteuid()}")
            )
            domain_before = snapshot_daemon_domain(live)
            siblings_before = fixture_daemon_domains(live)
        else:
            domain_before = None
            siblings_before = ()
        env = fixture.environ(base_url=provider.base_url, compaction=compaction)
        master, slave = pty.openpty()
        fcntl.ioctl(
            slave,
            termios.TIOCSWINSZ,
            struct.pack("HHHH", rows, columns, 0, 0),
        )
        process = subprocess.Popen(
            list(command),
            cwd=fixture.home,
            env=env,
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            process_group=0,
        )
        os.close(slave)
        slave = None
        output = bytearray()
        cursor = 0
        deadline = time.monotonic() + timeout

        def read_more(
            until: bytes | None, *, preserve_after_wait: bool = False
        ) -> None:
            nonlocal cursor
            while until is None or until not in output[cursor:]:
                if process is None or process.poll() is not None:
                    if until is not None and until not in output[cursor:]:
                        raise ProbeError(
                            f"probe PTY exited before marker {until!r}; output="
                            f"{bytes(output[-4000:])!r}"
                        )
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProbeError(
                        f"probe PTY timed out waiting for {until!r}; output="
                        f"{bytes(output[-4000:])!r}"
                    )
                ready, _, _ = select.select([master], [], [], min(0.2, remaining))
                if not ready:
                    if until is None:
                        return
                    continue
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        return
                    raise
                if not chunk:
                    return
                output.extend(chunk)
                if len(output) > 2 * 1024 * 1024:
                    dropped = len(output) - 2 * 1024 * 1024
                    del output[:dropped]
                    cursor = max(0, cursor - dropped)
            if until is not None:
                match = output.find(until, cursor)
                cursor = (
                    match + len(until) if preserve_after_wait else len(output)
                )

        try:
            for interaction in interactions:
                read_more(
                    interaction.wait_for,
                    preserve_after_wait=interaction.preserve_after_wait,
                )
                pending = memoryview(interaction.send)
                while pending:
                    written = os.write(master, pending)
                    if written <= 0:
                        raise ProbeError("probe PTY write made no progress")
                    pending = pending[written:]
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise ProbeError("probe PTY timed out waiting for client exit")
                read_more(None)
            while True:
                ready, _, _ = select.select([master], [], [], 0)
                if not ready:
                    break
                try:
                    chunk = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                output.extend(chunk)
        except BaseException:
            if process.poll() is None:
                _kill_process_group(process)
                process.wait()
            if domain_before is not None:
                domain_after = snapshot_daemon_domain(live)
                siblings_after = fixture_daemon_domains(live)
                _enforce_live_domain_untouched(
                    domain_before,
                    domain_after,
                    fixture=fixture,
                    fixture_domains_before=siblings_before,
                    fixture_domains_after=siblings_after,
                )
            raise
        daemon: DaemonDomainObservation | None = None
        if domain_before is not None:
            domain_after = snapshot_daemon_domain(live)
            siblings_after = fixture_daemon_domains(live)
            daemon = _enforce_live_domain_untouched(
                domain_before,
                domain_after,
                fixture=fixture,
                fixture_domains_before=siblings_before,
                fixture_domains_after=siblings_after,
            )
        rendered = bytes(output[-16000:]).decode("utf-8", errors="replace")
        return NativeRunResult(
            argv=command,
            returncode=process.returncode,
            timed_out=False,
            stdout=rendered,
            stderr="",
            requests=provider.requests,
            daemon=daemon,
        )
    finally:
        if process is not None and process.poll() is None:
            _kill_process_group(process)
            process.wait()
        if master is not None:
            os.close(master)
        if slave is not None:
            os.close(slave)
        if owned:
            provider.stop()


# ------------------------------------------------------ scripted delegation


_SUBAGENT_TYPE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_AGENT_TOOL_NAMES = ("Agent", "Task")
_UNKNOWN_TYPE_RE = re.compile(
    r"unknown|unavailable|not (?:found|available|registered|supported)|"
    r"no such|unregistered",
    re.IGNORECASE,
)


def _message_payload(
    content: list[dict[str, Any]],
    *,
    model: Any,
    stop_reason: str,
    message_id: str,
    input_tokens: int = 1,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> dict[str, Any]:
    return {
        "id": message_id,
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model if isinstance(model, str) else "probe-model",
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": input_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "output_tokens": 1,
        },
    }


def _sse_message(message: dict[str, Any]) -> SseResponse:
    """Wrap a canned message payload in the Anthropic SSE wire shape."""

    start_message = {
        **message,
        "content": [],
        "usage": {**message["usage"], "output_tokens": 0},
    }
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "message_start",
            {"type": "message_start", "message": start_message},
        )
    ]
    for index, block in enumerate(message["content"]):
        if block.get("type") == "text":
            start_block: dict[str, Any] = {"type": "text", "text": ""}
            delta: dict[str, Any] = {"type": "text_delta", "text": block["text"]}
        elif block.get("type") == "tool_use":
            start_block = {
                "type": "tool_use",
                "id": block["id"],
                "name": block["name"],
                "input": {},
            }
            delta = {
                "type": "input_json_delta",
                "partial_json": strict_json.canonical_bytes(
                    block.get("input", {})
                ).decode("utf-8"),
            }
        else:
            continue
        events.append(
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": index,
                    "content_block": start_block,
                },
            )
        )
        events.append(
            (
                "content_block_delta",
                {"type": "content_block_delta", "index": index, "delta": delta},
            )
        )
        events.append(
            ("content_block_stop", {"type": "content_block_stop", "index": index})
        )
    events.append(
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": message["stop_reason"],
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": 1},
            },
        )
    )
    events.append(("message_stop", {"type": "message_stop"}))
    return SseResponse(tuple(events))


class CompactionResponder:
    """Route-aware fake responses that drive one near-limit retained turn.

    The first Messages reply reports ``near_limit_tokens`` and later replies
    report one token. Count-token calls report the near-limit value only while
    exactly one Messages request has completed. Only counters are retained;
    request content is never stored or inspected.
    """

    def __init__(self, near_limit_tokens: int, *, first_response_chars: int = 0):
        if near_limit_tokens <= 0:
            raise ProbeError("compaction responder token count must be positive")
        if first_response_chars < 0 or first_response_chars > _MAX_BODY_BYTES // 2:
            raise ProbeError("compaction responder first response size is unsafe")
        self.near_limit_tokens = near_limit_tokens
        self.first_response_chars = first_response_chars
        self._message_requests = 0
        self._count_token_requests = 0
        self._lock = threading.Lock()

    @property
    def message_requests(self) -> int:
        with self._lock:
            return self._message_requests

    @property
    def count_token_requests(self) -> int:
        with self._lock:
            return self._count_token_requests

    def __call__(
        self, document: dict[str, Any], path: str
    ) -> tuple[int, dict[str, Any] | SseResponse]:
        route = urllib.parse.urlsplit(path).path.rstrip("/")
        with self._lock:
            if route.endswith("/count_tokens"):
                self._count_token_requests += 1
                tokens = (
                    self.near_limit_tokens
                    if self._message_requests == 1
                    else 1
                )
                return 200, {"input_tokens": tokens}
            if not route.endswith("/messages"):
                return 404, {
                    "type": "error",
                    "error": {
                        "type": "not_found_error",
                        "message": "probe: unknown path",
                    },
                }
            first = self._message_requests == 0
            self._message_requests += 1
            filler = ""
            if first and self.first_response_chars:
                parts: list[str] = []
                size = 0
                index = 0
                while size < self.first_response_chars:
                    part = f" probe-token-{index:06d}"
                    parts.append(part)
                    size += len(part)
                    index += 1
                filler = "".join(parts)[: self.first_response_chars]
            text = "PROBE-OK" + filler
            payload = _message_payload(
                [{"type": "text", "text": text}],
                model=document.get("model"),
                stop_reason="end_turn",
                message_id=f"msg_probe_compaction_{self._message_requests:04d}",
                input_tokens=self.near_limit_tokens if first else 1,
            )
            return 200, _sse_message(payload) if document.get("stream") else payload


class AutomaticCompactionResponder:
    """Main-turn script driving deterministic automatic compaction.

    Claude Code also issues small auxiliary model calls with no Agent tool.
    Those receive a separate low-usage response and do not advance the script.
    Main turns one and two seed compactable groups; main turn three reports
    150K cache-aware usage; main turn four reports 165K, above the deterministic
    162K reactive threshold for a 200K capacity; and main turn five exercises
    the post-threshold session. The lifecycle hook, not auxiliary-request
    classification, is the proof of automatic compaction.
    """

    def __init__(self) -> None:
        self._message_requests = 0
        self._main_requests = 0
        self._auxiliary_requests = 0
        self._count_token_requests = 0
        self._lock = threading.Lock()

    @property
    def message_requests(self) -> int:
        with self._lock:
            return self._message_requests

    @property
    def main_requests(self) -> int:
        with self._lock:
            return self._main_requests

    @property
    def auxiliary_requests(self) -> int:
        with self._lock:
            return self._auxiliary_requests

    @property
    def count_token_requests(self) -> int:
        with self._lock:
            return self._count_token_requests

    @staticmethod
    def _is_main_request(document: dict[str, Any]) -> bool:
        tools = document.get("tools")
        if not isinstance(tools, list):
            return False
        return any(
            isinstance(tool, dict) and tool.get("name") in _AGENT_TOOL_NAMES
            for tool in tools
        )

    def __call__(
        self, document: dict[str, Any], path: str
    ) -> tuple[int, dict[str, Any] | SseResponse]:
        route = urllib.parse.urlsplit(path).path.rstrip("/")
        with self._lock:
            if route.endswith("/count_tokens"):
                self._count_token_requests += 1
                return 200, {"input_tokens": 1}
            if not route.endswith("/messages"):
                return 404, {
                    "type": "error",
                    "error": {
                        "type": "not_found_error",
                        "message": "probe: unknown path",
                    },
                }
            self._message_requests += 1
            if self._is_main_request(document):
                self._main_requests += 1
                kind = "main"
                ordinal = self._main_requests
            else:
                self._auxiliary_requests += 1
                kind = "auxiliary"
                ordinal = self._auxiliary_requests

        text = "PROBE-OK" if kind == "main" else "PROBE-AUX"
        input_tokens = 1
        cache_read_input_tokens = 0
        if kind == "main" and ordinal == 1:
            input_tokens = 500
        elif kind == "main" and ordinal == 2:
            input_tokens = 1000
        elif kind == "main" and ordinal == 3:
            input_tokens = 2000
            cache_read_input_tokens = 147999
        elif kind == "main" and ordinal == 4:
            input_tokens = 2000
            cache_read_input_tokens = 162999

        payload = _message_payload(
            [{"type": "text", "text": text}],
            model=document.get("model"),
            stop_reason="end_turn",
            message_id=f"msg_probe_automatic_{kind}_{ordinal:04d}",
            input_tokens=input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )
        if document.get("stream"):
            return 200, _sse_message(payload)
        return 200, payload


class ScriptedDelegationResponder:
    """F1/F4 provider script: force one Agent delegation, classify follow-up.

    The first request that offers an Agent/Task tool gets a canned
    ``tool_use`` reply naming ``subagent_type``; every later request is
    classified in memory (never persisted):

    - ``second-request-stream``: a fresh stream with no tool_result for the
      forced call — the spawn attempt is observable -> accepted;
    - ``tool_result-success``: the forced call came back without an
      unknown-type error -> accepted;
    - ``tool_result-unknown-type``: the forced call came back with an error
      naming an unknown/unavailable type -> refused;
    - ``tool_result-other-error``: an error not naming the type ->
      indeterminate;
    - ``agent-tool-absent``: the client never offered an Agent/Task tool ->
      indeterminate;
    - ``no-followup``: the client ended after the forced call ->
      indeterminate.

    With ``hold`` set, the forced reply waits for the event (bounded by
    ``hold_timeout``) so a caller can keep the session alive while it
    manipulates the fixture (takeover probe).
    """

    def __init__(
        self,
        subagent_type: str,
        *,
        hold: threading.Event | None = None,
        hold_timeout: float = 120.0,
        delegated_input_limit_bytes: int | None = None,
    ):
        if not _SUBAGENT_TYPE_RE.fullmatch(subagent_type or ""):
            raise ProbeError(
                "scripted delegation subagent_type must be a safe agent id"
            )
        if delegated_input_limit_bytes is not None and delegated_input_limit_bytes <= 0:
            raise ProbeError("delegated input limit must be positive")
        self.subagent_type = subagent_type
        self.tool_use_id = "toolu_probe_delegation_0001"
        self._prompt = "Reply with exactly: CLAUDE-MULTI-PROBE-DELEGATION-OK"
        self._hold = hold
        self._hold_timeout = hold_timeout
        self._delegated_input_limit_bytes = delegated_input_limit_bytes
        self._classification = "indeterminate"
        self._branch = "no-followup"
        self._forced = False
        self._lock = threading.Lock()

    @property
    def classification(self) -> str:
        with self._lock:
            return self._classification

    @property
    def branch(self) -> str:
        with self._lock:
            return self._branch

    def _classify(self, branch: str, classification: str) -> None:
        # First decisive signal wins; later requests keep the verdict.
        if self._classification == "indeterminate":
            self._classification = classification
            self._branch = branch

    def _tool_results(self, document: dict[str, Any]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        messages = document.get("messages")
        if not isinstance(messages, list):
            return results
        for message in messages:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") == self.tool_use_id
                ):
                    results.append(block)
        return results

    @staticmethod
    def _result_text(block: dict[str, Any]) -> str:
        content = block.get("content")
        if isinstance(content, str):
            return content
        parts: list[str] = []
        if isinstance(content, list):
            for item in content:
                if (
                    isinstance(item, dict)
                    and item.get("type") == "text"
                    and isinstance(item.get("text"), str)
                ):
                    parts.append(item["text"])
        return "\n".join(parts)

    @staticmethod
    def _request_input_bytes(document: dict[str, Any]) -> int:
        total = 0
        if "system" in document:
            total += len(strict_json.canonical_bytes(document["system"]))
        messages = document.get("messages")
        if isinstance(messages, list):
            total += len(strict_json.canonical_bytes(messages))
        return total

    def __call__(
        self, document: dict[str, Any], path: str
    ) -> tuple[int, dict[str, Any] | SseResponse]:
        route = urllib.parse.urlsplit(path).path.rstrip("/")
        if not route.endswith("/messages"):
            if route.endswith("/count_tokens"):
                return 200, {"input_tokens": 1}
            return 404, {
                "type": "error",
                "error": {"type": "not_found_error", "message": "probe: unknown path"},
            }
        with self._lock:
            return self._respond_locked(document)

    def _respond_locked(
        self, document: dict[str, Any]
    ) -> tuple[int, dict[str, Any] | SseResponse]:
        model = document.get("model")
        stream = bool(document.get("stream"))
        if not self._forced:
            tools = document.get("tools") or []
            names = {
                tool.get("name") for tool in tools if isinstance(tool, dict)
            }
            tool = next(
                (name for name in _AGENT_TOOL_NAMES if name in names), None
            )
            if tool is None:
                self._branch = "agent-tool-absent"
                payload = _message_payload(
                    [{"type": "text", "text": "PROBE-OK"}],
                    model=model,
                    stop_reason="end_turn",
                    message_id="msg_probe_delegation_plain",
                )
                return 200, _sse_message(payload) if stream else payload
            if self._hold is not None:
                # Bounded: a caller that never releases still completes.
                self._hold.wait(timeout=self._hold_timeout)
            self._forced = True
            payload = _message_payload(
                [
                    {
                        "type": "tool_use",
                        "id": self.tool_use_id,
                        "name": tool,
                        "input": {
                            "description": "claude-multi probe delegation",
                            "subagent_type": self.subagent_type,
                            "prompt": self._prompt,
                        },
                    }
                ],
                model=model,
                stop_reason="tool_use",
                message_id="msg_probe_delegation_force",
            )
            return 200, _sse_message(payload) if stream else payload
        results = self._tool_results(document)
        if (
            not results
            and self._delegated_input_limit_bytes is not None
            and self._request_input_bytes(document)
            > self._delegated_input_limit_bytes
        ):
            self._branch = "delegated-model-context-overflow"
            return 400, {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "probe: delegated model context window exceeded",
                },
            }
        if not results:
            if self._delegated_input_limit_bytes is None:
                self._classify("second-request-stream", "accepted")
        else:
            block = results[0]
            if block.get("is_error"):
                if _UNKNOWN_TYPE_RE.search(self._result_text(block)):
                    self._classify("tool_result-unknown-type", "refused")
                else:
                    self._classify("tool_result-other-error", "indeterminate")
            else:
                self._classify("tool_result-success", "accepted")
        payload = _message_payload(
            [{"type": "text", "text": "PROBE-OK"}],
            model=model,
            stop_reason="end_turn",
            message_id="msg_probe_delegation_followup",
        )
        return 200, _sse_message(payload) if stream else payload


@dataclass(frozen=True)
class DelegationResult:
    """Metadata-only outcome of one scripted delegation probe."""

    subagent_type: str
    classification: str  # "accepted" | "refused" | "indeterminate"
    branch: str
    scope_dir: str
    returncode: int | None
    timed_out: bool
    requests: tuple[RequestRecord, ...]
    daemon: DaemonDomainObservation | None
    evidence: Path | None


def _run_metadata(result: NativeRunResult) -> dict[str, Any]:
    stdout_bytes = result.stdout.encode("utf-8")
    stderr_bytes = result.stderr.encode("utf-8")
    return {
        "returncode": result.returncode,
        "timed_out": result.timed_out,
        "argv_count": len(result.argv),
        "argv_flags": [
            token for token in result.argv[1:] if token.startswith("-")
        ],
        "stdout_bytes": len(stdout_bytes),
        "stdout_sha256": strict_json.sha256_hex(stdout_bytes),
        "stderr_bytes": len(stderr_bytes),
        "stderr_sha256": strict_json.sha256_hex(stderr_bytes),
    }


def run_scripted_delegation(
    subagent_type: str,
    *,
    scope_dir: Path | str,
    trusted: TrustedExecutable,
    fixture: ProbeFixture,
    environ: Mapping[str, str] | None = None,
    timeout: float = 120.0,
    hold: threading.Event | None = None,
    evidence_name: str | None = None,
    live_daemon_domain: Path | None = None,
    delegated_input_limit_bytes: int | None = None,
) -> DelegationResult:
    """Run one F1/F4-style scripted delegation against the pinned binary.

    Launches the trusted executable headlessly against the loopback fake
    provider with ``--add-dir <scope_dir>``, forces an
    ``Agent(subagent_type=<name>)`` call on the first turn, and classifies
    the client's follow-up (accepted / refused / indeterminate). The run is
    gated by the narrow real-binary allowance with the live-domain snapshot
    tripwire. Evidence is metadata-only: branch labels, request metadata,
    flag names, hashes — never prompt text, transcripts, full argv, stdio
    text, or response bodies.
    """

    if trusted.fake:
        raise ProbeError("scripted delegation requires the real pinned spec")
    ambient = os.environ if environ is None else environ
    root = Path(os.path.realpath(fixture.root))
    scope = Path(os.path.realpath(scope_dir))
    if scope == root or not _is_within(scope, root):
        raise ProbeError(
            f"delegation scope {scope} is not inside the disposable fixture "
            f"root {root}"
        )
    turn = (
        "Use the Agent tool exactly once with subagent_type "
        f"{subagent_type} and report its reply."
    )
    responder = ScriptedDelegationResponder(
        subagent_type,
        hold=hold,
        delegated_input_limit_bytes=delegated_input_limit_bytes,
    )
    provider = FakeAnthropicProvider(responder=responder).start()
    try:
        argv = (
            "-p",
            turn,
            "--add-dir",
            str(scope),
            "--dangerously-skip-permissions",
        )
        try:
            outcome = run_native(
                argv,
                trusted=trusted,
                fixture=fixture,
                provider=provider,
                timeout=timeout,
                allow_real=True,
                environ=ambient,
                live_daemon_domain=live_daemon_domain,
            )
        except ProbeError as exc:
            if evidence_name is not None:
                write_evidence(
                    fixture,
                    evidence_name,
                    {
                        "version": 1,
                        "kind": "scripted-delegation",
                        "subagent_type": subagent_type,
                        "scope_dir": str(scope),
                        "turn_sha256": strict_json.sha256_hex(
                            turn.encode("utf-8")
                        ),
                        "classification": responder.classification,
                        "branch": responder.branch,
                        "requests": [
                            asdict(record) for record in provider.requests
                        ],
                        "error": str(exc),
                    },
                )
            raise
        result = DelegationResult(
            subagent_type=subagent_type,
            classification=responder.classification,
            branch=responder.branch,
            scope_dir=str(scope),
            returncode=outcome.returncode,
            timed_out=outcome.timed_out,
            requests=outcome.requests,
            daemon=outcome.daemon,
            evidence=None,
        )
        if evidence_name is not None:
            path = write_evidence(
                fixture,
                evidence_name,
                {
                    "version": 1,
                    "kind": "scripted-delegation",
                    "subagent_type": subagent_type,
                    "scope_dir": str(scope),
                    "turn_sha256": strict_json.sha256_hex(turn.encode("utf-8")),
                    "classification": result.classification,
                    "branch": result.branch,
                    "run": _run_metadata(outcome),
                    "requests": [asdict(record) for record in outcome.requests],
                    "daemon": (
                        asdict(outcome.daemon)
                        if outcome.daemon is not None
                        else None
                    ),
                },
            )
            result = replace(result, evidence=path)
        return result
    finally:
        provider.stop()


# ------------------------------------------------------------ takeover probe


def _default_versions_dir(contract: dict[str, Any]) -> Path:
    """Versions directory derived from the contract's recorded resolved_path.

    Fail closed when the contract does not record one; a hardcoded home path
    must never ship in source (hygiene gate).
    """

    try:
        resolved = contract["claude"]["executable"]["resolved_path"]
    except (KeyError, TypeError) as exc:
        raise ProbeError(
            "native contract does not record claude.executable.resolved_path; "
            "cannot locate the retained-versions directory"
        ) from exc
    if not isinstance(resolved, str) or not resolved.startswith("/"):
        raise ProbeError(
            "native contract resolved_path is not an absolute path; cannot "
            "locate the retained-versions directory"
        )
    return Path(resolved).parent


@dataclass(frozen=True)
class RetainedBinary:
    """One hash-verified retained pinned binary."""

    version: str
    path: Path
    sha256: str
    pinned_in_contract: bool


def _retained_record(binary: RetainedBinary) -> dict[str, Any]:
    """JSON-safe metadata record of one retained binary."""

    return {
        "version": binary.version,
        "path": str(binary.path),
        "sha256": binary.sha256,
        "pinned_in_contract": binary.pinned_in_contract,
    }


def _contract_hash_for(
    contract: dict[str, Any], version: str, path: Path
) -> str | None:
    """Pinned hash for a retained version, if the contract records one."""

    if not isinstance(contract, dict):
        return None
    claude = contract.get("claude")
    if not isinstance(claude, dict):
        return None
    executable = claude.get("executable")
    if isinstance(executable, dict):
        resolved = executable.get("resolved_path")
        digest = executable.get("sha256")
        if (
            isinstance(resolved, str)
            and isinstance(digest, str)
            and (resolved == str(path) or resolved.endswith("/" + version))
        ):
            return digest
    retained = claude.get("retained")
    if isinstance(retained, dict):
        entry = retained.get(version)
        if isinstance(entry, dict) and isinstance(entry.get("sha256"), str):
            return entry["sha256"]
    return None


def _retained_binary(
    version: str, *, versions_dir: Path, contract: dict[str, Any]
) -> RetainedBinary:
    """Locate and hash-verify one retained binary (fail closed on mismatch).

    A version the contract does not pin is hash-verified for integrity only;
    the computed hash is recorded in evidence and the probe proceeds.
    """

    path = versions_dir / version
    real = Path(os.path.realpath(path))
    if real != path:
        raise ProbeError(f"retained binary {path} resolves through a symlink")
    try:
        info = os.lstat(real)
    except FileNotFoundError as exc:
        raise ProbeError(f"retained binary {real} does not exist") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ProbeError(f"retained binary {real} is not a regular file")
    if stat.S_IMODE(info.st_mode) & 0o022:
        raise ProbeError(f"retained binary {real} must not be group/other-writable")
    if not os.access(real, os.X_OK):
        raise ProbeError(f"retained binary {real} is not executable")
    digest = _sha256_file(real)
    pinned = _contract_hash_for(contract, version, real)
    if pinned is not None and pinned != digest:
        raise ProbeError(
            f"retained binary {version} content hash does not match the "
            "native contract"
        )
    return RetainedBinary(version, real, digest, pinned is not None)


@dataclass(frozen=True)
class TakeoverProbeResult:
    """Metadata-only outcome of the F7 fixture takeover probe."""

    verdict: str  # "takeover-delegation-accepted" | "takeover-delegation-refused" | "takeover-delegation-indeterminate"
    old_binary: RetainedBinary
    new_binary: RetainedBinary
    supervisor_artifacts: tuple[str, ...]
    takeover_markers: tuple[str, ...]
    delegation: DelegationResult | None
    daemon: DaemonDomainObservation | None
    evidence: Path | None


def _daemon_artifacts(config_dir: Path) -> tuple[str, ...]:
    """Names under the fixture config root's own daemon dir (fixture-local)."""

    daemon = config_dir / "daemon"
    try:
        return tuple(sorted(entry.name for entry in os.scandir(daemon)))
    except (FileNotFoundError, NotADirectoryError):
        return ()


def run_takeover_probe(
    subagent_type: str,
    *,
    scope_dir: Path | str,
    fixture: ProbeFixture,
    contract: dict[str, Any],
    environ: Mapping[str, str] | None = None,
    versions_dir: Path | None = None,
    old_version: str = "2.1.216",
    new_version: str = "2.1.217",
    supervisor_timeout: float = 20.0,
    takeover_timeout: float = 45.0,
    session_timeout: float = 180.0,
    evidence_name: str = "takeover-probe",
    live_daemon_domain: Path | None = None,
) -> TakeoverProbeResult:
    """F7 / U1 stop-gate: fixture supervisor takeover keeps the scope.

    Launches a fixture session through a fixture-owned ``claude`` symlink
    targeting ``old_version`` (both retained binaries hash-verified; an
    unpinned old version's computed hash is recorded and the probe
    proceeds), holds the scripted first turn, waits bounded seconds for the
    fixture's own supervisor to appear under the fixture config root, swaps
    the symlink to ``new_version``, waits bounded seconds for takeover
    evidence in the fixture-root daemon artifacts, then releases the turn
    and asserts scripted delegation to ``subagent_type`` still succeeds.

    Every precondition fails closed with a recorded verdict: a fixture that
    runs no resident supervisor headlessly, a still uid-shared domain, or
    takeover never observed all raise ProbeError after writing evidence —
    F7 then moves to user acceptance L2.
    """

    ambient = os.environ if environ is None else environ
    _assert_real_run_allowed(fixture, ambient)
    if versions_dir is None:
        versions_dir = _default_versions_dir(contract)
    root = Path(os.path.realpath(fixture.root))
    scope = Path(os.path.realpath(scope_dir))
    if scope == root or not _is_within(scope, root):
        raise ProbeError(
            f"takeover scope {scope} is not inside the disposable fixture "
            f"root {root}"
        )
    old_binary = _retained_binary(
        old_version, versions_dir=versions_dir, contract=contract
    )
    new_binary = _retained_binary(
        new_version, versions_dir=versions_dir, contract=contract
    )

    def _fail(message: str, **extra: Any) -> None:
        record: dict[str, Any] = {
            "version": 1,
            "kind": "takeover-probe",
            "subagent_type": subagent_type,
            "scope_dir": str(scope),
            "old_binary": _retained_record(old_binary),
            "new_binary": _retained_record(new_binary),
            "error": message,
        }
        record.update(extra)
        write_evidence(fixture, evidence_name, record)
        raise ProbeError(message)

    bin_dir = state.ensure_private_dir(fixture.root / "bin")
    link = bin_dir / "claude"
    try:
        os.unlink(link)
    except FileNotFoundError:
        pass
    if link.exists() or link.is_symlink():
        raise ProbeError(f"fixture claude link {link} could not be replaced")
    os.symlink(old_binary.path, link)

    hold = threading.Event()
    responder = ScriptedDelegationResponder(
        subagent_type, hold=hold, hold_timeout=session_timeout
    )
    provider = FakeAnthropicProvider(responder=responder).start()
    live = (
        live_daemon_domain
        if live_daemon_domain is not None
        else Path(f"/tmp/cc-daemon-{os.geteuid()}")
    )
    domain_before = snapshot_daemon_domain(live)
    siblings_before = fixture_daemon_domains(live)
    turn = (
        "Use the Agent tool exactly once with subagent_type "
        f"{subagent_type} and report its reply."
    )
    argv = (
        str(link),
        "-p",
        turn,
        "--add-dir",
        str(scope),
        "--dangerously-skip-permissions",
    )
    process = subprocess.Popen(
        list(argv),
        cwd=fixture.home,
        env=fixture.environ(base_url=provider.base_url),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        # Precondition: the fixture must run its own resident supervisor,
        # observable via fixture-root daemon artifacts, without touching the
        # live uid-shared domain.
        deadline = time.monotonic() + supervisor_timeout
        artifacts: tuple[str, ...] = ()
        while time.monotonic() < deadline:
            artifacts = _daemon_artifacts(fixture.claude_config_dir)
            if artifacts:
                break
            interim = snapshot_daemon_domain(live_daemon_domain)
            if _domain_changes(domain_before, interim):
                _fail(
                    "takeover probe precondition unmet: the fixture session "
                    "touched the live uid-shared daemon domain "
                    "(domain still uid-shared); F7 moves to user acceptance "
                    "L2",
                    live_domain_changes=list(
                        _domain_changes(domain_before, interim)
                    ),
                )
            time.sleep(0.25)
        if not artifacts:
            _fail(
                "takeover probe precondition unmet: no resident fixture "
                "supervisor observable headlessly within "
                f"{supervisor_timeout}s (no daemon artifacts under the "
                "fixture config root); F7 moves to user acceptance L2",
                supervisor_artifacts=[],
            )

        # Swap the fixture symlink to the new binary; bounded takeover wait.
        os.unlink(link)
        os.symlink(new_binary.path, link)
        markers: list[str] = []
        baseline = artifacts
        deadline = time.monotonic() + takeover_timeout
        while time.monotonic() < deadline:
            current = _daemon_artifacts(fixture.claude_config_dir)
            if current != baseline:
                markers.append("fixture-daemon-artifacts-changed")
            if process.poll() is not None:
                markers.append("session-process-replaced")
                break
            if markers:
                break
            time.sleep(0.5)
        if not markers:
            _fail(
                "takeover not observed within "
                f"{takeover_timeout}s after the fixture relink; U1 "
                "unresolved headlessly and moves to user acceptance L2",
                supervisor_artifacts=list(artifacts),
            )

        # Release the held turn; the (possibly respawned) session must still
        # delegate to the marker agent through the carried scope.
        hold.set()
        timed_out = False
        try:
            stdout, stderr = process.communicate(timeout=session_timeout)
            returncode: int | None = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(process)
            stdout, stderr = process.communicate()
            returncode = None
        domain_after = snapshot_daemon_domain(live)
        siblings_after = fixture_daemon_domains(live)
        daemon = _enforce_live_domain_untouched(
            domain_before,
            domain_after,
            fixture=fixture,
            fixture_domains_before=siblings_before,
            fixture_domains_after=siblings_after,
        )
        delegation = DelegationResult(
            subagent_type=subagent_type,
            classification=responder.classification,
            branch=responder.branch,
            scope_dir=str(scope),
            returncode=returncode,
            timed_out=timed_out,
            requests=provider.requests,
            daemon=daemon,
            evidence=None,
        )
        verdict = f"takeover-delegation-{delegation.classification}"
        run_record = {
            "returncode": returncode,
            "timed_out": timed_out,
            "argv_count": len(argv),
            "argv_flags": [token for token in argv[1:] if token.startswith("-")],
            "stdout_bytes": len(stdout.encode("utf-8")),
            "stdout_sha256": strict_json.sha256_hex(stdout.encode("utf-8")),
            "stderr_bytes": len(stderr.encode("utf-8")),
            "stderr_sha256": strict_json.sha256_hex(stderr.encode("utf-8")),
        }
        path = write_evidence(
            fixture,
            evidence_name,
            {
                "version": 1,
                "kind": "takeover-probe",
                "subagent_type": subagent_type,
                "scope_dir": str(scope),
                "verdict": verdict,
                "old_binary": _retained_record(old_binary),
                "new_binary": _retained_record(new_binary),
                "supervisor_artifacts": list(artifacts),
                "takeover_markers": markers,
                "delegation": {
                    "classification": delegation.classification,
                    "branch": delegation.branch,
                },
                "run": run_record,
                "requests": [asdict(record) for record in provider.requests],
                "daemon": asdict(daemon) if daemon is not None else None,
            },
        )
        return TakeoverProbeResult(
            verdict=verdict,
            old_binary=old_binary,
            new_binary=new_binary,
            supervisor_artifacts=artifacts,
            takeover_markers=tuple(markers),
            delegation=delegation,
            daemon=daemon,
            evidence=path,
        )
    finally:
        hold.set()
        if process.poll() is None:
            _kill_process_group(process)
            process.wait()
        for stream in (process.stdout, process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        provider.stop()


# --------------------------------------------------------------------- CLI


def probe_cli(
    positionals: list[str],
    flags: dict[str, Any],
    run_argv: list[str],
    *,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Gated CLI for the disposable probe harness; returns a process exit code.

    ``--allow-local-claude`` is presence-based explicit consent: its value
    is ignored and it never relaxes any other check. ``probe init`` takes no
    executable argv. ``probe run`` requires ``--native-contract FILE`` and
    passes any argv after ``--`` to the contract-pinned executable; with the
    separate presence-based ``--allow-real-execution`` flag the real contract
    binary runs under the narrow allowance, otherwise the CLI run path
    refuses the real (contract) spec, so its evidence branch is reachable
    only from API-level fake-spec unit probes.
    """

    ambient = os.environ if environ is None else environ
    try:
        if "allow-local-claude" not in flags:
            raise ProbeError("probe requires explicit --allow-local-claude consent")
        fixture_root = flags.get("fixture-root")
        if not isinstance(fixture_root, str) or not fixture_root:
            raise ProbeError("probe requires --fixture-root PATH")
        assert_no_provider_credentials(ambient)
        action = positionals[0] if positionals else ""
        if action == "init":
            if run_argv:
                raise ProbeError("probe init takes no executable argv")
            fixture = build_fixture(fixture_root, environ=ambient)
            sys.stdout.write(
                strict_json.canonical_file_bytes(fixture_manifest(fixture)).decode(
                    "utf-8"
                )
            )
            return 0
        if action == "run":
            contract_path = flags.get("native-contract")
            if not isinstance(contract_path, str) or not contract_path:
                raise ProbeError("probe run requires --native-contract FILE")
            contract = strict_json.load(Path(contract_path))
            if not isinstance(contract, dict):
                raise ProbeError("native contract must be a JSON object")
            trusted = trusted_from_contract(contract)
            fixture = build_fixture(fixture_root, environ=ambient)
            result = run_native(
                run_argv,
                trusted=trusted,
                fixture=fixture,
                allow_real="allow-real-execution" in flags,
            )
            # Metadata-only evidence (ora-28): this branch is reachable only
            # from API-level fake-spec probes, and even there raw argv and
            # stdio text are never persisted.
            argv_joined = "\n".join(result.argv).encode("utf-8")
            stdout_bytes = result.stdout.encode("utf-8")
            stderr_bytes = result.stderr.encode("utf-8")
            evidence = {
                "version": 1,
                "kind": "probe-run",
                "run": {
                    "returncode": result.returncode,
                    "timed_out": result.timed_out,
                    "argv_count": len(result.argv),
                    "argv_sha256": strict_json.sha256_hex(argv_joined),
                    "stdout_bytes": len(stdout_bytes),
                    "stdout_sha256": strict_json.sha256_hex(stdout_bytes),
                    "stderr_bytes": len(stderr_bytes),
                    "stderr_sha256": strict_json.sha256_hex(stderr_bytes),
                },
                "requests": [asdict(record) for record in result.requests],
            }
            path = write_evidence(fixture, "last-run", evidence)
            print(
                f"probe run: returncode={result.returncode} "
                f"timed_out={result.timed_out} requests={len(result.requests)}"
            )
            print(f"evidence: {path}")
            if result.timed_out or result.returncode != 0:
                return 2
            return 0
        raise ProbeError(
            f"unknown probe action {action!r}; "
            "expected 'init' or 'run'"
        )
    except (ProbeError, state.StateError, strict_json.StrictJSONError, OSError) as exc:
        print(f"claude-multi-dev: probe: {exc}", file=sys.stderr)
        return 2
