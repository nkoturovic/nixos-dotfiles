"""F-series scope probes: scripted delegation and the fixture takeover gate.

Hermetic coverage drives the full real-binary gate — the CLAUDE_CONFIG_DIR
narrow allowance, the trusted path+sha256 check, the loopback fake provider,
and the live-domain snapshot tripwire — using fake client executables pinned
as real (``fake=False``) trusted specs. The scope directory is synthetic:
one marker agent file ``cm-probe-marker.md`` created directly in the
fixture, never through the scope compiler (parallel development boundary).

Tests against the actual pinned binaries run only on the operator host and
skip with an explicit boundary message when the pinned versions are
unavailable. Those runs point the tripwire at the real uid-shared domain:
any change fails the run closed, which the tests accept as a legitimate
fail-closed verdict (and report). No test contacts a real provider; the
loopback fake is the only HTTP endpoint. No test reads prompts,
transcripts, or response bodies — classifications and metadata only.
"""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from claude_multi import probe, state, strict_json
from claude_multi.probe import ProbeError


_MARKER_AGENT = """---
name: cm-probe-marker
description: Synthetic probe marker agent for the F-series fixture scope.
model: probe-model
---
You are the cm-probe-marker fixture agent. Body token TOKEN-XYZZY-MARKER.
"""

_FAKE_CLIENT_BODY = '''
import http.client
import json
import os
import urllib.parse

MODE = "__MODE__"
url = urllib.parse.urlsplit(os.environ["ANTHROPIC_BASE_URL"])
token = os.environ["ANTHROPIC_AUTH_TOKEN"]


def post(document):
    body = json.dumps(document).encode("utf-8")
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=60)
    connection.request(
        "POST",
        "/v1/messages",
        body=body,
        headers={"Content-Type": "application/json", "x-api-key": token},
    )
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, json.loads(payload)


tools = [{"name": "Agent", "input_schema": {"type": "object"}}]
parent_turn = {
    "role": "user",
    "content": "P" * 4096 if MODE in {"inherited", "generated", "low-context"} else "fixture turn",
}
first = {
    "model": "probe-parent" if MODE in {"inherited", "generated", "low-context"} else "probe-model",
    "max_tokens": 16,
    "messages": [parent_turn],
    "tools": tools,
}
status, reply = post(first)
assert status == 200, status
block = next(item for item in reply["content"] if item["type"] == "tool_use")

if MODE in {"inherited", "generated", "low-context"}:
    delegated_turn = {"role": "user", "content": "delegated task"}
    child = {
        "model": "probe-child-low-context" if MODE == "low-context" else "probe-child-wide",
        "max_tokens": 16,
        "system": "child" if MODE == "inherited" else "S" * 4096,
        "messages": [parent_turn, delegated_turn] if MODE == "inherited" else [delegated_turn],
    }
    status, child_reply = post(child)
    if MODE == "low-context":
        assert status == 400, status
        assert child_reply["error"]["type"] == "invalid_request_error"
        assert child_reply["error"]["message"] == "probe: delegated model context window exceeded"
        print("FAKE-CLIENT-DONE")
    else:
        assert status == 200, status
        messages = [
            parent_turn,
            {"role": "assistant", "content": reply["content"]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": "CLAUDE-MULTI-PROBE-DELEGATION-OK",
                    }
                ],
            },
        ]
        followup = {
            "model": "probe-parent",
            "max_tokens": 16,
            "messages": messages,
            "tools": tools,
        }
        status, reply = post(followup)
        assert status == 200, status
        print("FAKE-CLIENT-DONE")
else:
    messages = [
        parent_turn,
        {"role": "assistant", "content": reply["content"]},
    ]
    if MODE == "success":
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "content": "CLAUDE-MULTI-PROBE-DELEGATION-OK",
                    }
                ],
            }
        )
    elif MODE == "refused":
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": block["id"],
                        "is_error": True,
                        "content": "unknown subagent type",
                    }
                ],
            }
        )
    else:  # "stream": a fresh request stream with no tool_result for the call
        messages.append({"role": "user", "content": "continue"})
    followup = {
        "model": "probe-model",
        "max_tokens": 16,
        "messages": messages,
        "tools": tools,
    }
    status, reply = post(followup)
    assert status == 200, status
    print("FAKE-CLIENT-DONE")
'''

_TAKEOVER_CLIENT_BODY = '''
import http.client
import json
import os
import time
import urllib.parse

config = os.environ["CLAUDE_CONFIG_DIR"]
daemon = os.path.join(config, "daemon")
os.makedirs(daemon, exist_ok=True)
with open(os.path.join(daemon, "supervisor.marker"), "w") as handle:
    handle.write("supervisor")

link = "__LINK__"
new_target = "__NEW_TARGET__"
deadline = time.time() + 30
while time.time() < deadline:
    try:
        if os.readlink(link) == new_target:
            break
    except OSError:
        pass
    time.sleep(0.05)
with open(os.path.join(daemon, "takeover.marker"), "w") as handle:
    handle.write("takeover")

url = urllib.parse.urlsplit(os.environ["ANTHROPIC_BASE_URL"])
token = os.environ["ANTHROPIC_AUTH_TOKEN"]


def post(document):
    body = json.dumps(document).encode("utf-8")
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=120)
    connection.request(
        "POST",
        "/v1/messages",
        body=body,
        headers={"Content-Type": "application/json", "x-api-key": token},
    )
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, json.loads(payload)


tools = [{"name": "Agent", "input_schema": {"type": "object"}}]
first = {
    "model": "probe-model",
    "max_tokens": 16,
    "messages": [{"role": "user", "content": "fixture turn"}],
    "tools": tools,
}
status, reply = post(first)
assert status == 200, status
block = next(item for item in reply["content"] if item["type"] == "tool_use")
followup = {
    "model": "probe-model",
    "max_tokens": 16,
    "messages": [
        {"role": "user", "content": "fixture turn"},
        {"role": "assistant", "content": reply["content"]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],
                    "content": "CLAUDE-MULTI-PROBE-DELEGATION-OK",
                }
            ],
        },
    ],
    "tools": tools,
}
status, reply = post(followup)
assert status == 200, status
print("TAKEOVER-CLIENT-DONE")
'''

_QUIET_CLIENT_BODY = "import time\ntime.sleep(30)\n"

_RETAINED_VERSIONS_DIR = Path("/home/kotur/.local/share/claude/versions")
_CONTRACT_PATH = (
    Path(__file__).resolve().parent.parent / "catalog" / "native-contract.json"
)


class ScopeProbeTestCase(unittest.TestCase):
    """Base: disposable fixture plus a synthetic marker-agent scope dir."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-scope-probe-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(self._cleanup)
        live = self.root / "live"
        # Simulated live environment roots; the fixture stays disjoint and
        # the ambient scan sees no provider credentials.
        self.environ = {
            "HOME": str(live / "home"),
            "PATH": "/usr/bin:/bin",
        }
        self.fixture_root = self.root / "fixture"
        self.fixture = probe.build_fixture(self.fixture_root, environ=self.environ)
        # Synthetic stand-in for the uid-shared daemon domain; hermetic
        # runs point the tripwire here and never at the real one.
        self.live_domain = state.ensure_private_dir(self.root / "cc-daemon-test")

    def _cleanup(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _scope_dir(self) -> Path:
        scope = state.ensure_private_dir(self.fixture_root / "scope")
        agents = state.ensure_private_dir(scope / ".claude" / "agents")
        state.atomic_write(
            agents / "cm-probe-marker.md", _MARKER_AGENT.encode("utf-8")
        )
        return scope

    def _trusted_script(
        self, name: str, body: str, *, fake: bool = False
    ) -> probe.TrustedExecutable:
        script = self.root / "bin" / name
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text(f"#!{sys.executable}\n" + body)
        script.chmod(0o700)
        return probe.TrustedExecutable(
            script,
            hashlib.sha256(script.read_bytes()).hexdigest(),
            fake=fake,
        )

    def _fake_client(self, mode: str) -> probe.TrustedExecutable:
        return self._trusted_script(
            f"fake-client-{mode}", _FAKE_CLIENT_BODY.replace("__MODE__", mode)
        )


class ScriptedDelegationResponderTests(ScopeProbeTestCase):
    """The F1/F4 provider script: forced call plus follow-up classification."""

    def _responder(self, **kwargs) -> probe.ScriptedDelegationResponder:
        return probe.ScriptedDelegationResponder("cm-probe-marker", **kwargs)

    def _forced(self, responder: probe.ScriptedDelegationResponder) -> dict:
        status, payload = responder(
            {
                "model": "probe-model",
                "messages": [{"role": "user", "content": "x"}],
                "tools": [{"name": "Agent", "input_schema": {"type": "object"}}],
            },
            "/v1/messages",
        )
        self.assertEqual(status, 200)
        assert isinstance(payload, dict)
        return payload

    def _followup(self, block: dict | None) -> dict:
        messages = [{"role": "user", "content": "x"}]
        if block is not None:
            messages.append({"role": "user", "content": [block]})
        return {"model": "probe-model", "messages": messages}

    def test_rejects_unsafe_subagent_types(self) -> None:
        for bad in ("", "cm marker", "../x", "x;rm", "-leading", "a" * 65):
            with self.subTest(bad=bad):
                with self.assertRaisesRegex(ProbeError, "safe agent id"):
                    probe.ScriptedDelegationResponder(bad)
        for good in ("cm-probe-marker", "A.b_c-d", "x"):
            probe.ScriptedDelegationResponder(good)

    def test_first_request_without_agent_tool_is_plain_text(self) -> None:
        responder = self._responder()
        status, payload = responder(
            {
                "model": "probe-model",
                "messages": [],
                "tools": [{"name": "Bash", "input_schema": {"type": "object"}}],
            },
            "/v1/messages",
        )
        self.assertEqual(status, 200)
        assert isinstance(payload, dict)
        self.assertEqual(payload["stop_reason"], "end_turn")
        self.assertEqual(responder.branch, "agent-tool-absent")
        self.assertEqual(responder.classification, "indeterminate")

    def test_agent_tool_forces_subagent_type_tool_use(self) -> None:
        responder = self._responder()
        payload = self._forced(responder)
        self.assertEqual(payload["stop_reason"], "tool_use")
        block = payload["content"][0]
        self.assertEqual(block["type"], "tool_use")
        self.assertEqual(block["name"], "Agent")
        self.assertEqual(block["input"]["subagent_type"], "cm-probe-marker")
        self.assertEqual(responder.branch, "no-followup")
        self.assertEqual(responder.classification, "indeterminate")

    def test_task_tool_is_an_agent_alias(self) -> None:
        responder = self._responder()
        status, payload = responder(
            {
                "model": "probe-model",
                "messages": [],
                "tools": [{"name": "Task", "input_schema": {"type": "object"}}],
            },
            "/v1/messages",
        )
        self.assertEqual(status, 200)
        assert isinstance(payload, dict)
        self.assertEqual(payload["content"][0]["name"], "Task")

    def test_tool_result_success_classifies_accepted(self) -> None:
        responder = self._responder()
        self._forced(responder)
        responder(
            self._followup(
                {
                    "type": "tool_result",
                    "tool_use_id": responder.tool_use_id,
                    "content": "delegation reply",
                }
            ),
            "/v1/messages",
        )
        self.assertEqual(responder.classification, "accepted")
        self.assertEqual(responder.branch, "tool_result-success")

    def test_tool_result_unknown_type_classifies_refused(self) -> None:
        responder = self._responder()
        self._forced(responder)
        responder(
            self._followup(
                {
                    "type": "tool_result",
                    "tool_use_id": responder.tool_use_id,
                    "is_error": True,
                    "content": "unknown subagent type: cm-probe-marker",
                }
            ),
            "/v1/messages",
        )
        self.assertEqual(responder.classification, "refused")
        self.assertEqual(responder.branch, "tool_result-unknown-type")

    def test_tool_result_other_error_stays_indeterminate(self) -> None:
        responder = self._responder()
        self._forced(responder)
        status, payload = responder(
            self._followup(
                {
                    "type": "tool_result",
                    "tool_use_id": responder.tool_use_id,
                    "is_error": True,
                    "content": "permission denied by policy",
                }
            ),
            "/v1/messages",
        )
        self.assertEqual(status, 200)
        self.assertEqual(responder.classification, "indeterminate")
        self.assertEqual(responder.branch, "tool_result-other-error")

    def test_second_request_stream_classifies_accepted(self) -> None:
        responder = self._responder()
        self._forced(responder)
        responder(self._followup(None), "/v1/messages")
        self.assertEqual(responder.classification, "accepted")
        self.assertEqual(responder.branch, "second-request-stream")

    def test_first_decisive_signal_wins(self) -> None:
        responder = self._responder()
        self._forced(responder)
        responder(
            self._followup(
                {
                    "type": "tool_result",
                    "tool_use_id": responder.tool_use_id,
                    "content": "ok",
                }
            ),
            "/v1/messages",
        )
        responder(
            self._followup(
                {
                    "type": "tool_result",
                    "tool_use_id": responder.tool_use_id,
                    "is_error": True,
                    "content": "unknown subagent type",
                }
            ),
            "/v1/messages",
        )
        self.assertEqual(responder.classification, "accepted")
        self.assertEqual(responder.branch, "tool_result-success")

    def test_stream_requests_get_sse_replies(self) -> None:
        responder = self._responder()
        status, payload = responder(
            {
                "model": "probe-model",
                "stream": True,
                "messages": [],
                "tools": [{"name": "Agent", "input_schema": {"type": "object"}}],
            },
            "/v1/messages",
        )
        self.assertEqual(status, 200)
        self.assertIsInstance(payload, probe.SseResponse)
        assert isinstance(payload, probe.SseResponse)
        events = [name for name, _data in payload.events]
        self.assertIn("message_start", events)
        self.assertIn("content_block_start", events)
        self.assertEqual(events[-1], "message_stop")
        status, payload = responder(
            {
                "model": "probe-model",
                "stream": True,
                "messages": [{"role": "user", "content": "x"}],
            },
            "/v1/messages",
        )
        self.assertIsInstance(payload, probe.SseResponse)
        self.assertEqual(responder.classification, "accepted")
        self.assertEqual(responder.branch, "second-request-stream")

    def test_non_messages_routes(self) -> None:
        responder = self._responder()
        status, payload = responder({}, "/v1/messages/count_tokens")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"input_tokens": 1})
        status, payload = responder({}, "/v1/other")
        self.assertEqual(status, 404)

    def test_hold_blocks_the_forced_reply_until_released(self) -> None:
        hold = threading.Event()
        responder = self._responder(hold=hold, hold_timeout=30)
        outcome: dict = {}

        def call() -> None:
            outcome["payload"] = self._forced(responder)

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        thread.join(0.5)
        self.assertNotIn("payload", outcome)
        hold.set()
        thread.join(5)
        self.assertIn("payload", outcome)
        self.assertEqual(
            outcome["payload"]["content"][0]["input"]["subagent_type"],
            "cm-probe-marker",
        )

    def test_hold_timeout_bounds_the_wait(self) -> None:
        hold = threading.Event()
        responder = self._responder(hold=hold, hold_timeout=0.2)
        started = threading.Event()

        def call() -> None:
            started.set()
            self._forced(responder)
            started.set()

        thread = threading.Thread(target=call, daemon=True)
        thread.start()
        thread.join(5)
        # Never released: the bounded wait still completes the reply.
        self.assertTrue(thread.is_alive() is False)


class ScriptedDelegationRunTests(ScopeProbeTestCase):
    """End-to-end hermetic delegation runs through the real-binary gate."""

    def _run(
        self,
        trusted: probe.TrustedExecutable,
        scope: Path,
        evidence_name: str | None = "delegation",
        **overrides,
    ) -> probe.DelegationResult:
        options = {
            "scope_dir": scope,
            "trusted": trusted,
            "fixture": self.fixture,
            "environ": self.environ,
            "timeout": 30,
            "evidence_name": evidence_name,
            "live_daemon_domain": self.live_domain,
        }
        options.update(overrides)
        return probe.run_scripted_delegation("cm-probe-marker", **options)

    def test_success_delegation_classified_accepted(self) -> None:
        scope = self._scope_dir()
        result = self._run(self._fake_client("success"), scope)
        self.assertEqual(result.classification, "accepted")
        self.assertEqual(result.branch, "tool_result-success")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.timed_out)
        self.assertIsNotNone(result.daemon)
        assert result.daemon is not None
        self.assertTrue(result.daemon.unchanged)
        self.assertTrue(result.requests)
        self.assertIn("Agent", result.requests[0].tool_names)

    def test_stream_spawn_classified_accepted(self) -> None:
        scope = self._scope_dir()
        result = self._run(self._fake_client("stream"), scope)
        self.assertEqual(result.classification, "accepted")
        self.assertEqual(result.branch, "second-request-stream")

    def test_refused_delegation_classified_refused(self) -> None:
        scope = self._scope_dir()
        result = self._run(self._fake_client("refused"), scope)
        self.assertEqual(result.classification, "refused")
        self.assertEqual(result.branch, "tool_result-unknown-type")

    def test_delegated_request_metadata_detects_inherited_parent_context(self) -> None:
        scope = self._scope_dir()
        result = self._run(
            self._fake_client("inherited"),
            scope,
            evidence_name=None,
            delegated_input_limit_bytes=20_000,
        )
        self.assertEqual(result.classification, "accepted")
        self.assertEqual(result.branch, "tool_result-success")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.timed_out)
        self.assertEqual(len(result.requests), 3)
        parent, child, _followup = result.requests
        self.assertEqual(parent.model, "probe-parent")
        self.assertEqual(child.model, "probe-child-wide")
        self.assertEqual(
            set(parent.message_sha256s) & set(child.message_sha256s),
            {parent.message_sha256s[0]},
        )
        self.assertEqual(
            child.system_json_bytes,
            len(strict_json.canonical_bytes("child")),
        )

    def test_delegated_request_metadata_measures_generated_system_prompt(self) -> None:
        scope = self._scope_dir()
        result = self._run(
            self._fake_client("generated"),
            scope,
            evidence_name=None,
            delegated_input_limit_bytes=20_000,
        )
        self.assertEqual(result.classification, "accepted")
        self.assertEqual(result.branch, "tool_result-success")
        self.assertEqual(len(result.requests), 3)
        parent, child, _followup = result.requests
        self.assertFalse(
            set(parent.message_sha256s) & set(child.message_sha256s)
        )
        self.assertEqual(
            child.system_json_bytes,
            len(strict_json.canonical_bytes("S" * 4096)),
        )
        self.assertGreater(child.system_json_bytes, child.messages_json_bytes)

    def test_low_context_delegated_model_overflow_is_distinct(self) -> None:
        scope = self._scope_dir()
        result = self._run(
            self._fake_client("low-context"),
            scope,
            evidence_name=None,
            delegated_input_limit_bytes=512,
        )
        self.assertEqual(result.classification, "indeterminate")
        self.assertEqual(result.branch, "delegated-model-context-overflow")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(result.timed_out)
        self.assertEqual(len(result.requests), 2)
        parent, child = result.requests
        self.assertEqual(child.model, "probe-child-low-context")
        self.assertFalse(
            set(parent.message_sha256s) & set(child.message_sha256s)
        )
        self.assertEqual(
            child.system_json_bytes,
            len(strict_json.canonical_bytes("S" * 4096)),
        )
        self.assertGreater(
            child.system_json_bytes + child.messages_json_bytes,
            512,
        )

    def test_fake_spec_refused(self) -> None:
        scope = self._scope_dir()
        trusted = self._trusted_script("fake-unit", _FAKE_CLIENT_BODY, fake=True)
        with self.assertRaisesRegex(ProbeError, "requires the real pinned spec"):
            self._run(trusted, scope)

    def test_scope_outside_fixture_refused(self) -> None:
        outside = state.ensure_private_dir(self.root / "outside-scope")
        with self.assertRaisesRegex(ProbeError, "not inside the disposable fixture"):
            self._run(self._fake_client("success"), outside)

    def test_unsafe_subagent_type_refused(self) -> None:
        scope = self._scope_dir()
        with self.assertRaisesRegex(ProbeError, "safe agent id"):
            probe.run_scripted_delegation(
                "bad type",
                scope_dir=scope,
                trusted=self._fake_client("success"),
                fixture=self.fixture,
                environ=self.environ,
                live_daemon_domain=self.live_domain,
            )

    def test_live_domain_tamper_fails_run_and_records_error_evidence(self) -> None:
        scope = self._scope_dir()
        rogue = self.live_domain / "rogue-entry"
        body = (
            f"import os\nos.makedirs({str(rogue)!r}, exist_ok=True)\n"
            + _FAKE_CLIENT_BODY.replace("__MODE__", "success")
        )
        trusted = self._trusted_script("tamper-client", body)
        with self.assertRaisesRegex(ProbeError, "live daemon domain touched"):
            self._run(trusted, scope, evidence_name="delegation-tamper")
        evidence = strict_json.loads(
            (self.fixture_root / "evidence" / "delegation-tamper.json").read_bytes()
        )
        self.assertIn("live daemon domain touched", evidence["error"])
        self.assertEqual(evidence["classification"], "accepted")
        self.assertEqual(evidence["branch"], "tool_result-success")
        # Foreign live-domain entries are evidence, never remediated.
        self.assertTrue(rogue.is_dir())

    def test_evidence_is_metadata_only(self) -> None:
        scope = self._scope_dir()
        result = self._run(
            self._fake_client("success"), scope, evidence_name="delegation-meta"
        )
        self.assertIsNotNone(result.evidence)
        assert result.evidence is not None
        raw = result.evidence.read_bytes()
        # The scripted turn text, the marker agent body, and full argv never
        # persist: only hashes, flags, classifications, and paths.
        self.assertNotIn(b"Use the Agent tool exactly once", raw)
        self.assertNotIn(b"TOKEN-XYZZY-MARKER", raw)
        self.assertNotIn(b"CLAUDE-MULTI-PROBE-DELEGATION-OK", raw)
        evidence = strict_json.loads(raw)
        self.assertEqual(evidence["kind"], "scripted-delegation")
        self.assertEqual(evidence["subagent_type"], "cm-probe-marker")
        self.assertEqual(evidence["classification"], "accepted")
        self.assertIn("--add-dir", evidence["run"]["argv_flags"])
        self.assertNotIn("argv", evidence["run"])
        self.assertTrue(evidence["turn_sha256"])
        daemon = evidence["daemon"]
        self.assertTrue(daemon["unchanged"])
        self.assertEqual(
            daemon["expected_fixture_subdomain"],
            probe.expected_daemon_subdomain(self.fixture.claude_config_dir),
        )
        self.assertIn("fixture_domains_after", daemon)


class TakeoverProbeHermeticTests(ScopeProbeTestCase):
    """F7 skeleton with synthetic retained versions (no pinned binaries)."""

    def _versions_dir(self) -> Path:
        return state.ensure_private_dir(self.root / "versions")

    def _write_version(self, versions: Path, version: str, body: str) -> Path:
        path = versions / version
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o700)
        return path

    def test_missing_version_refused(self) -> None:
        with self.assertRaisesRegex(ProbeError, "does not exist"):
            probe._retained_binary(
                "9.9.9", versions_dir=self._versions_dir(), contract={}
            )

    def test_symlink_version_refused(self) -> None:
        versions = self._versions_dir()
        real = self._write_version(versions, "2.1.216", _QUIET_CLIENT_BODY)
        link = versions / "2.1.217"
        link.symlink_to(real)
        with self.assertRaisesRegex(ProbeError, "symlink"):
            probe._retained_binary(
                "2.1.217", versions_dir=versions, contract={}
            )

    def test_group_other_writable_version_refused(self) -> None:
        versions = self._versions_dir()
        path = self._write_version(versions, "2.1.216", _QUIET_CLIENT_BODY)
        path.chmod(0o775)
        with self.assertRaisesRegex(ProbeError, "writable"):
            probe._retained_binary(
                "2.1.216", versions_dir=versions, contract={}
            )

    def test_contract_pin_mismatch_refused(self) -> None:
        versions = self._versions_dir()
        self._write_version(versions, "2.1.216", _QUIET_CLIENT_BODY)
        contract = {"claude": {"retained": {"2.1.216": {"sha256": "0" * 64}}}}
        with self.assertRaisesRegex(ProbeError, "does not match"):
            probe._retained_binary(
                "2.1.216", versions_dir=versions, contract=contract
            )

    def test_unpinned_version_proceeds_and_records_computed_hash(self) -> None:
        versions = self._versions_dir()
        path = self._write_version(versions, "2.1.216", _QUIET_CLIENT_BODY)
        binary = probe._retained_binary(
            "2.1.216", versions_dir=versions, contract={}
        )
        self.assertFalse(binary.pinned_in_contract)
        self.assertEqual(
            binary.sha256, hashlib.sha256(path.read_bytes()).hexdigest()
        )
        self.assertEqual(binary.path, path)

    def test_pinned_version_via_retained_map(self) -> None:
        versions = self._versions_dir()
        path = self._write_version(versions, "2.1.217", _QUIET_CLIENT_BODY)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        contract = {"claude": {"retained": {"2.1.217": {"sha256": digest}}}}
        binary = probe._retained_binary(
            "2.1.217", versions_dir=versions, contract=contract
        )
        self.assertTrue(binary.pinned_in_contract)

    def test_pinned_version_via_executable_record(self) -> None:
        versions = self._versions_dir()
        path = self._write_version(versions, "2.1.217", _QUIET_CLIENT_BODY)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        contract = {
            "claude": {
                "executable": {"resolved_path": str(path), "sha256": digest}
            }
        }
        binary = probe._retained_binary(
            "2.1.217", versions_dir=versions, contract=contract
        )
        self.assertTrue(binary.pinned_in_contract)

    def test_takeover_delegation_accepted_end_to_end(self) -> None:
        versions = self._versions_dir()
        link = self.fixture_root / "bin" / "claude"
        new_path = versions / "2.1.217"
        body = _TAKEOVER_CLIENT_BODY.replace("__LINK__", str(link)).replace(
            "__NEW_TARGET__", str(new_path)
        )
        self._write_version(versions, "2.1.216", body)
        self._write_version(versions, "2.1.217", body)
        scope = self._scope_dir()
        result = probe.run_takeover_probe(
            "cm-probe-marker",
            scope_dir=scope,
            fixture=self.fixture,
            contract={},
            environ=self.environ,
            versions_dir=versions,
            supervisor_timeout=5.0,
            takeover_timeout=8.0,
            session_timeout=30.0,
            live_daemon_domain=self.live_domain,
        )
        self.assertEqual(result.verdict, "takeover-delegation-accepted")
        self.assertIn("fixture-daemon-artifacts-changed", result.takeover_markers)
        self.assertFalse(result.old_binary.pinned_in_contract)
        self.assertFalse(result.new_binary.pinned_in_contract)
        self.assertIsNotNone(result.delegation)
        assert result.delegation is not None
        self.assertEqual(result.delegation.classification, "accepted")
        self.assertEqual(result.delegation.branch, "tool_result-success")
        self.assertIsNotNone(result.daemon)
        assert result.daemon is not None
        self.assertTrue(result.daemon.unchanged)
        self.assertIsNotNone(result.evidence)
        assert result.evidence is not None
        raw = result.evidence.read_bytes()
        self.assertNotIn(b"Use the Agent tool exactly once", raw)
        evidence = strict_json.loads(raw)
        self.assertEqual(evidence["verdict"], "takeover-delegation-accepted")
        self.assertEqual(
            evidence["supervisor_artifacts"], ["supervisor.marker"]
        )

    def test_no_resident_supervisor_fails_closed_with_evidence(self) -> None:
        versions = self._versions_dir()
        self._write_version(versions, "2.1.216", _QUIET_CLIENT_BODY)
        self._write_version(versions, "2.1.217", _QUIET_CLIENT_BODY)
        scope = self._scope_dir()
        with self.assertRaisesRegex(
            ProbeError, "no resident fixture supervisor observable"
        ):
            probe.run_takeover_probe(
                "cm-probe-marker",
                scope_dir=scope,
                fixture=self.fixture,
                contract={},
                environ=self.environ,
                versions_dir=versions,
                supervisor_timeout=1.0,
                takeover_timeout=1.0,
                session_timeout=10.0,
                live_daemon_domain=self.live_domain,
            )
        evidence = strict_json.loads(
            (self.fixture_root / "evidence" / "takeover-probe.json").read_bytes()
        )
        self.assertIn("precondition unmet", evidence["error"])
        self.assertIn("F7 moves to user acceptance L2", evidence["error"])

    def test_scope_outside_fixture_refused(self) -> None:
        versions = self._versions_dir()
        self._write_version(versions, "2.1.216", _QUIET_CLIENT_BODY)
        self._write_version(versions, "2.1.217", _QUIET_CLIENT_BODY)
        outside = state.ensure_private_dir(self.root / "outside-scope")
        with self.assertRaisesRegex(ProbeError, "not inside the disposable fixture"):
            probe.run_takeover_probe(
                "cm-probe-marker",
                scope_dir=outside,
                fixture=self.fixture,
                contract={},
                environ=self.environ,
                versions_dir=versions,
                live_daemon_domain=self.live_domain,
            )


class RealPinnedBinaryTests(ScopeProbeTestCase):
    """Real pinned binaries against the fake provider (operator host only).

    These point the tripwire at the real uid-shared domain. A fail-closed
    "live daemon domain touched" outcome is the gate working (the fixture
    touched the live domain, or the live supervisor churned underneath the
    run); the tests assert exactly that failure shape. Anything else — a
    clean run with a classification, or a takeover verdict — is recorded
    and printed for the stop-gate report.
    """

    trusted: probe.TrustedExecutable
    contract: dict
    boundary_skip: str | None = None

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.boundary_skip = None
        if not _CONTRACT_PATH.is_file():
            cls.boundary_skip = (
                "BOUNDARY: catalog/native-contract.json unavailable; "
                "real-binary F-series probes run only on the operator host "
                "with the pinned Claude installation"
            )
            return
        contract = strict_json.loads(_CONTRACT_PATH.read_bytes())
        if not isinstance(contract, dict):
            cls.boundary_skip = (
                "BOUNDARY: native-contract.json is not a JSON object; "
                "real-binary probes require the pinned contract"
            )
            return
        try:
            trusted = probe.trusted_from_contract(contract)
        except ProbeError as exc:
            cls.boundary_skip = (
                f"BOUNDARY: native contract lacks a usable pinned executable "
                f"({exc}); real-binary probes skipped"
            )
            return
        path = trusted.resolved_path
        if not (path.is_file() and os.access(path, os.X_OK)):
            cls.boundary_skip = (
                f"BOUNDARY: pinned Claude binary {path} unavailable; "
                "real-binary F-series probes run only on the operator host"
            )
            return
        if probe._sha256_file(path) != trusted.sha256:
            cls.boundary_skip = (
                f"BOUNDARY: pinned Claude binary {path} drifted from the "
                "native-contract sha256; re-pin before running real probes"
            )
            return
        cls.contract = contract
        cls.trusted = trusted

    def setUp(self) -> None:
        super().setUp()
        if self.boundary_skip is not None:
            self.skipTest(self.boundary_skip)

    def _install_lifecycle_recorder(self) -> Path:
        events = self.fixture.root / "lifecycle-events.jsonl"
        recorder = self.fixture.root / "record-lifecycle.py"
        recorder.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            f"target = {str(events)!r}\n"
            "event = json.load(sys.stdin)\n"
            "allowed = {key: event.get(key) for key in "
            "('hook_event_name', 'session_id', 'source', 'cwd', 'model')}\n"
            "with open(target, 'a', encoding='utf-8') as handle:\n"
            "    handle.write(json.dumps(allowed, sort_keys=True) + '\\n')\n"
            "    handle.flush()\n"
            "    os.fsync(handle.fileno())\n"
            "print('{}')\n",
            encoding="utf-8",
        )
        recorder.chmod(0o700)
        settings = {
            "hooks": {
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": shlex.quote(str(recorder)),
                                "timeout": 5,
                            }
                        ]
                    }
                ]
            }
        }
        state.atomic_write(
            self.fixture.claude_config_dir / "settings.json",
            strict_json.canonical_file_bytes(settings),
        )
        return events

    def test_real_scripted_delegation_headless(self) -> None:
        scope = self._scope_dir()
        try:
            result = probe.run_scripted_delegation(
                "cm-probe-marker",
                scope_dir=scope,
                trusted=self.trusted,
                fixture=self.fixture,
                environ=self.environ,
                timeout=150,
                evidence_name="real-delegation",
                live_daemon_domain=None,  # the real uid-shared tripwire
            )
        except ProbeError as exc:
            # The fail-closed branch is the gate working, not a test bug.
            self.assertIn("live daemon domain touched", str(exc))
            print(f"real delegation outcome: fail-closed: {exc}")
            return
        self.assertIsNotNone(result.daemon)
        assert result.daemon is not None
        self.assertTrue(result.daemon.unchanged)
        self.assertIn(
            result.classification, ("accepted", "refused", "indeterminate")
        )
        self.assertIsNotNone(result.evidence)
        assert result.evidence is not None
        raw = result.evidence.read_bytes()
        self.assertNotIn(b"Use the Agent tool exactly once", raw)
        print(
            "real delegation outcome: ran "
            f"classification={result.classification} branch={result.branch} "
            f"returncode={result.returncode} timed_out={result.timed_out} "
            f"requests={len(result.requests)} "
            f"fixture_domains_after={list(result.daemon.fixture_domains_after)}"
        )

    def test_real_manual_compaction_emits_metadata_hook(self) -> None:
        events_path = self._install_lifecycle_recorder()
        session_id = "33333333-3333-4333-8333-333333333333"
        model = "claude-fable-5[1m]"
        responder = probe.CompactionResponder(near_limit_tokens=900)
        policy = probe.ProbeCompactionPolicy(
            auto_compact_window=1000000,
            auto_compact_percent=100,
        )
        with probe.FakeAnthropicProvider(responder=responder) as provider:
            try:
                result = probe.run_native_pty(
                    (
                        "--session-id",
                        session_id,
                        "--model",
                        model,
                        "--dangerously-skip-permissions",
                    ),
                    (
                        probe.PTYInteraction(b"Choose", b"2\r"),
                        probe.PTYInteraction(b"Press", b"\r"),
                        probe.PTYInteraction(b"Quick safety check", b"1\r"),
                        probe.PTYInteraction(b"WARNING", b"2\r"),
                        probe.PTYInteraction(
                            "❯".encode("utf-8"),
                            b"CLAUDE-MULTI-COMPACTION-FIRST\r",
                        ),
                        probe.PTYInteraction(
                            b"PROBE-OK", b"CLAUDE-MULTI-COMPACTION-SECOND\r"
                        ),
                        probe.PTYInteraction(b"PROBE-OK", b"/compact\r"),
                        probe.PTYInteraction(b"Compacted", b"/exit\r"),
                    ),
                    trusted=self.trusted,
                    fixture=self.fixture,
                    provider=provider,
                    timeout=120,
                    allow_real=True,
                    environ=self.environ,
                    compaction=policy,
                )
            except ProbeError as exc:
                if "live daemon domain touched" in str(exc):
                    self.skipTest(
                        "BOUNDARY: live daemon-domain churn prevented a clean "
                        f"manual-compaction observation ({exc})"
                    )
                raise
        self.assertFalse(result.timed_out, result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(events_path.is_file())
        events = [
            strict_json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        compact = [event for event in events if event.get("source") == "compact"]
        self.assertTrue(
            compact,
            {
                "events": events,
                "message_requests": responder.message_requests,
                "count_token_requests": responder.count_token_requests,
                "stdout": result.stdout,
                "stderr": result.stderr,
            },
        )
        self.assertEqual(len(compact), 1)
        self.assertEqual([event.get("source") for event in events], ["startup", "compact"])
        self.assertTrue(all("transcript_path" not in event for event in events))
        self.assertGreaterEqual(responder.message_requests, 2)
        self.assertTrue(provider.requests)
        self.assertTrue(all(record.auth == "dummy" for record in provider.requests))
        print(
            "real manual compaction outcome: ran "
            f"messages={responder.message_requests} "
            f"count_tokens={responder.count_token_requests} "
            f"events={len(events)} compact_events={len(compact)}"
        )

    def test_real_auto_compaction_emits_metadata_hook(self) -> None:
        events_path = self._install_lifecycle_recorder()
        session_id = "44444444-4444-4444-8444-444444444444"
        model = "gpt-multi-sol-high"
        auto_window = 200000
        responder = probe.AutomaticCompactionResponder()
        large_turn = " ".join(
            f"probe-token-{index:05d}" for index in range(4000)
        )[:60000].encode("utf-8")
        paste = b"\x1b[200~" + large_turn + b"\x1b[201~\r"
        policy = probe.ProbeCompactionPolicy(
            auto_compact_window=auto_window,
            auto_compact_percent=90,
            max_context_tokens=auto_window,
        )
        prompt = "❯".encode("utf-8")
        interactions = (
            probe.PTYInteraction(b"Choose", b"2\r"),
            probe.PTYInteraction(b"Press", b"\r"),
            probe.PTYInteraction(b"Quick safety check", b"1\r"),
            probe.PTYInteraction(b"WARNING", b"2\r"),
            probe.PTYInteraction(prompt, b"COMPACTION-SEED-A\r"),
            probe.PTYInteraction(
                b"PROBE-OK", b"", preserve_after_wait=True
            ),
            probe.PTYInteraction(prompt, b"COMPACTION-SEED-B\r"),
            probe.PTYInteraction(
                b"PROBE-OK", b"", preserve_after_wait=True
            ),
            probe.PTYInteraction(prompt, paste),
            probe.PTYInteraction(
                b"PROBE-OK", b"", preserve_after_wait=True
            ),
            probe.PTYInteraction(prompt, b"COMPACTION-BRIDGE\r"),
            probe.PTYInteraction(
                b"PROBE-OK", b"", preserve_after_wait=True
            ),
            probe.PTYInteraction(prompt, b"COMPACTION-TRIGGER\r"),
            probe.PTYInteraction(
                b"PROBE-OK", b"", preserve_after_wait=True
            ),
            probe.PTYInteraction(prompt, b"/exit\r"),
        )
        self.assertNotIn(
            b"/compact", b"".join(interaction.send for interaction in interactions)
        )
        with probe.FakeAnthropicProvider(responder=responder) as provider:
            try:
                result = probe.run_native_pty(
                    (
                        "--session-id",
                        session_id,
                        "--model",
                        model,
                        "--dangerously-skip-permissions",
                    ),
                    interactions,
                    trusted=self.trusted,
                    fixture=self.fixture,
                    provider=provider,
                    timeout=180,
                    allow_real=True,
                    environ=self.environ,
                    compaction=policy,
                )
            except ProbeError as exc:
                if "live daemon domain touched" in str(exc):
                    self.skipTest(
                        "BOUNDARY: live daemon-domain churn prevented a clean "
                        f"automatic-compaction observation ({exc})"
                    )
                metadata = [
                    {
                        "ordinal": index,
                        "model": record.model,
                        "system_bytes": record.system_json_bytes,
                        "messages_bytes": record.messages_json_bytes,
                        "message_count": record.message_count,
                        "tools": record.tool_names,
                    }
                    for index, record in enumerate(provider.requests, start=1)
                ]
                raise ProbeError(f"{exc}; request_metadata={metadata}") from exc
        self.assertFalse(result.timed_out, result.stderr)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(responder.main_requests, 5)
        self.assertGreaterEqual(responder.auxiliary_requests, 2)
        self.assertTrue(events_path.is_file())
        events = [
            strict_json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line
        ]
        compact = [event for event in events if event.get("source") == "compact"]
        self.assertTrue(
            compact,
            {
                "events": events,
                "message_requests": responder.message_requests,
                "count_token_requests": responder.count_token_requests,
                "stdout_tail": result.stdout[-2000:],
            },
        )
        self.assertTrue(all("transcript_path" not in event for event in events))
        self.assertTrue(provider.requests)
        self.assertTrue(all(record.auth == "dummy" for record in provider.requests))
        print(
            "real auto compaction outcome: ran "
            f"messages={responder.message_requests} "
            f"count_tokens={responder.count_token_requests} "
            f"events={len(events)} compact_events={len(compact)}"
        )

    def test_real_takeover_probe_stop_gate(self) -> None:
        for version in ("2.1.216", "2.1.217"):
            path = _RETAINED_VERSIONS_DIR / version
            if not (path.is_file() and os.access(path, os.X_OK)):
                self.skipTest(
                    f"BOUNDARY: retained Claude {version} unavailable under "
                    f"{_RETAINED_VERSIONS_DIR}; the F7 stop-gate probe "
                    "requires both retained pinned binaries"
                )
        scope = self._scope_dir()
        try:
            result = probe.run_takeover_probe(
                "cm-probe-marker",
                scope_dir=scope,
                fixture=self.fixture,
                contract=self.contract,
                environ=self.environ,
                supervisor_timeout=10.0,
                takeover_timeout=10.0,
                session_timeout=120.0,
                live_daemon_domain=None,  # the real uid-shared tripwire
            )
        except ProbeError as exc:
            message = str(exc)
            self.assertTrue(
                "precondition unmet" in message
                or "takeover not observed" in message
                or "live daemon domain touched" in message,
                f"unexpected takeover failure mode: {message}",
            )
            evidence = strict_json.loads(
                (self.fixture_root / "evidence" / "takeover-probe.json").read_bytes()
            )
            self.assertIn("error", evidence)
            print(f"real takeover outcome: fail-closed: {message}")
            return
        self.assertTrue(result.verdict.startswith("takeover-delegation-"))
        self.assertIsNotNone(result.evidence)
        print(
            f"real takeover outcome: {result.verdict} "
            f"markers={list(result.takeover_markers)} "
            f"supervisor_artifacts={list(result.supervisor_artifacts)}"
        )


if __name__ == "__main__":
    unittest.main()
