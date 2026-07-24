"""Tests for developer onboarding: drafts, lifecycle, promotion, smoke gate."""

from __future__ import annotations

import copy
import io
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from claude_multi import catalog, dev, state, strict_json
from claude_multi.dev import DevError


CATALOG_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = CATALOG_ROOT.parent


def _model_entry(model_id: str = "newmodel") -> dict:
    return {
        "id": model_id,
        "provider": "openai",
        "display": "New Model",
        "client_selector": "gpt-multi-new-high",
        "wire_model": "gpt-new",
        "capabilities": ["agents"],
        "compatible_roles": ["cm-reviewer"],
        "context": {
            "client_tokens": 128000,
            "provider_tokens": 128000,
            "scalar_tokens": 128000,
            "ordinary_profile": None,
            "declared_tokens": 128000,
            "validated_tokens": 128000,
            "qualification": "onboarding test fixture",
        },
        "lanes": {
            "high": {
                "client_selector": "gpt-multi-new-high",
                "agent_effort": "high",
                "proxy_effort_contract": "reasoning-effort-high",
            }
        },
        "default_lane": "high",
        "lead": None,
        "routing_note": "Fixture model for onboarding tests.",
        "role_hints": {"cm-reviewer": "Fixture hint."},
        "minimum_tested": {"claude_code": "2.1.216", "cliproxyapi": "7.2.80"},
    }


def _model_draft(model_id: str = "newmodel") -> dict:
    return dev.make_model_draft(
        name="d1",
        provider="openai",
        entry=_model_entry(model_id),
        fixtures=["fixtures/newmodel.json"],
        now="2026-07-21T00:00:00Z",
    )


def _provider_draft() -> dict:
    profile = {
        "id": "zeta",
        "display": "Zeta",
        "independence_family": "zeta",
        "support": "locally-validated-experimental",
        "support_note": "Fixture provider.",
        "adapter": "cliproxy-claude-compatible-v1",
        "transport": {
            "kind": "direct",
            "base_url": "https://api.example.com/coding",
            "auth": {
                "kind": "header",
                "header": "x-api-key",
                "secret_ref": "env:ZETA_API_KEY",
            },
        },
        "passthrough_routes": [],
        "payload_contracts": ["output-config-max", "filter-thinking"],
    }
    model = _model_entry("zetamodel")
    model["provider"] = "zeta"
    model["client_selector"] = "zeta-multi-z1-max[1m]"
    model["wire_model"] = "z1"
    model["lanes"]["max"] = model["lanes"].pop("high")
    model["lanes"]["max"]["agent_effort"] = "max"
    model["lanes"]["max"]["client_selector"] = "zeta-multi-z1-max[1m]"
    model["lanes"]["max"]["proxy_effort_contract"] = "output-config-max"
    model["default_lane"] = "max"
    model["context"] = {
        "client_tokens": 1000000,
        "provider_tokens": 1000000,
        "scalar_tokens": None,
        "ordinary_profile": None,
        "declared_tokens": 1000000,
        "validated_tokens": 100000,
        "qualification": "fixture",
    }
    return dev.make_provider_draft(
        name="d2",
        provider_profile=profile,
        model_entry=model,
        contract_claims=["routing", "streaming"],
        fixtures=["fixtures/zeta.json"],
        now="2026-07-21T00:00:00Z",
    )


class OnboardingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-dev-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.drafts = dev.DraftStore(self.root / "drafts")
        # Isolated fake checkout: copy only what load_raw/verify need.
        self.repo = self.root / "repo"
        (self.repo / "home-manager").mkdir(parents=True)
        shutil.copytree(CATALOG_ROOT, self.repo / "home-manager" / "claude-multi")
        # Copies may originate from a read-only store path; make them writable.
        for root_dir, dirs, files in os.walk(self.repo / "home-manager" / "claude-multi"):
            os.chmod(root_dir, 0o755)
            for name in files:
                os.chmod(Path(root_dir) / name, 0o644)
        (self.repo / "flake.nix").write_text("{}\n")
        (self.repo / "home-manager" / "kotur.home.nix").write_text("{}\n")

    def _review(self, draft, name="d1", runner=None):
        return dev.review_draft(
            draft, draft_name=name, repo=self.repo,
            revision="deadbeef", now="2026-07-21T00:00:00Z",
            runner=runner or (lambda c, w: {"cmd": c, "returncode": 0}),
            candidate_parent=Path(tempfile.mkdtemp(dir=self.root)),
        )


class DraftTests(OnboardingTestCase):
    def test_draft_store_roundtrip_mode_0600(self) -> None:
        draft = _model_draft()
        path = self.drafts.save("d1", draft)
        self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o600)
        self.assertEqual(self.drafts.load("d1"), draft)

    def test_corrupt_draft_rejected(self) -> None:
        path = self.drafts.save("d1", _model_draft())
        state.atomic_write(path, b"{oops")
        with self.assertRaisesRegex(DevError, "corrupt"):
            self.drafts.load("d1")

    def test_model_draft_schema_valid(self) -> None:
        from claude_multi import validate

        draft = _model_draft()
        schema = strict_json.load(CATALOG_ROOT / "schemas" / "draft.schema.json")
        self.assertEqual(validate.validate(draft, schema), [])

    def test_provider_draft_requires_model_and_claims(self) -> None:
        with self.assertRaisesRegex(DevError, "complete model"):
            dev.make_provider_draft(
                name="d2", provider_profile={}, model_entry={},
                contract_claims=["routing"],
            )
        with self.assertRaisesRegex(DevError, "contract claims"):
            dev.make_provider_draft(
                name="d2", provider_profile={}, model_entry={"id": "x"},
                contract_claims=[],
            )

    def test_model_draft_inherits_provider_transport(self) -> None:
        # Model entry carries no transport/auth fields; inheritance is by reference.
        draft = _model_draft()
        self.assertNotIn("transport", draft["entry"])
        self.assertNotIn("auth", draft["entry"])
        self.assertEqual(draft["provider"], "openai")


class PostImageTests(OnboardingTestCase):
    def test_model_post_image_adds_entry_new_off(self) -> None:
        raw = catalog.load_raw(self.repo / dev.V2_ROOT)
        images = dev.build_post_images(raw["docs"], _model_draft())
        self.assertEqual(
            list(images), ["home-manager/claude-multi/catalog/models.json"]
        )
        post = strict_json.loads(images["home-manager/claude-multi/catalog/models.json"])
        self.assertIn("newmodel", post["models"])
        self.assertNotIn("id", post["models"]["newmodel"])
        # New · Off: absent from the default composition entirely.
        composition = post and raw["docs"]["compositions/default"]
        self.assertNotIn("newmodel", composition["availability"]["models"])
        self.assertFalse(
            any(slot["model"] == "newmodel" for slot in composition["slots"])
        )

    def test_duplicate_model_rejected(self) -> None:
        raw = catalog.load_raw(self.repo / dev.V2_ROOT)
        with self.assertRaisesRegex(DevError, "already exists"):
            dev.build_post_images(raw["docs"], _model_draft("sol"))

    def test_provider_post_images_both_files(self) -> None:
        raw = catalog.load_raw(self.repo / dev.V2_ROOT)
        images = dev.build_post_images(raw["docs"], _provider_draft())
        self.assertEqual(len(images), 2)
        providers_post = strict_json.loads(
            images["home-manager/claude-multi/catalog/providers.json"]
        )
        self.assertIn("zeta", providers_post["providers"])

    def test_provider_post_images_strip_draft_ids(self) -> None:
        raw = catalog.load_raw(self.repo / dev.V2_ROOT)
        images = dev.build_post_images(raw["docs"], _provider_draft())
        providers_post = strict_json.loads(
            images["home-manager/claude-multi/catalog/providers.json"]
        )
        models_post = strict_json.loads(
            images["home-manager/claude-multi/catalog/models.json"]
        )
        self.assertNotIn("id", providers_post["providers"]["zeta"])
        self.assertNotIn("id", models_post["models"]["zetamodel"])

    def test_unknown_candidate_key_rejected_before_review(self) -> None:
        draft = _model_draft()
        draft["entry"]["mystery_field"] = "x"
        with self.assertRaisesRegex(DevError, "trusted schema"):
            dev.check_draft(
                draft, repo=self.repo,
                runner=lambda c, w: {"cmd": c, "returncode": 0},
                candidate_parent=self.root / "cand-x",
            )
        with self.assertRaisesRegex(DevError, "trusted schema"):
            self._review(draft)


class CheckTests(OnboardingTestCase):
    def test_check_runs_exact_offline_commands_and_gates(self) -> None:
        commands: list[list[str]] = []

        def fake_runner(command, cwd):
            commands.append(command)
            return {"cmd": command, "returncode": 0, "stdout": "ok", "stderr": ""}

        parent = self.root / "candidate"
        parent.mkdir()
        result = dev.check_draft(
            _model_draft(), repo=self.repo, runner=fake_runner,
            candidate_parent=parent,
        )
        self.assertTrue(result.bundle_valid)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][:2], ["nix-build", "--no-out-link"])
        self.assertTrue(commands[0][2].endswith("home-manager/claude-multi/tests/default.nix"))
        self.assertTrue(commands[1][2].endswith("home-manager/claude-multi/package.nix"))
        self.assertIn(str(parent), commands[0][2])
        # candidate tree was materialized, used, and deterministically removed
        self.assertFalse(result.candidate.exists())
        self.assertFalse(parent.exists())
        # the post-image evidence survives in memory
        applied = strict_json.loads(
            result.images["home-manager/claude-multi/catalog/models.json"]
        )
        self.assertIn("newmodel", applied["models"])
        # render used dummy secret, never a real one
        self.assertIn("dummy-onboarding-secret", result.render.yaml)

    def test_candidate_cleanup_on_success_and_failure(self) -> None:
        # success path
        ok_parent = self.root / "cand-ok"
        ok_parent.mkdir()
        marker = self.root / "marker.txt"
        marker.write_text("do not touch")
        dev.check_draft(
            _model_draft(), repo=self.repo,
            runner=lambda c, w: {"cmd": c, "returncode": 0},
            candidate_parent=ok_parent,
        )
        self.assertFalse(ok_parent.exists())
        self.assertTrue(marker.is_file())
        # failure path (build gate raises)
        fail_parent = self.root / "cand-fail"
        fail_parent.mkdir()
        with self.assertRaisesRegex(DevError, "candidate build failed"):
            dev.check_draft(
                _model_draft(), repo=self.repo,
                runner=lambda c, w: {"cmd": c, "returncode": 1, "stderr": "boom"},
                candidate_parent=fail_parent,
            )
        self.assertFalse(fail_parent.exists())
        self.assertTrue(marker.is_file())

    def test_candidate_cleanup_on_exception_path(self) -> None:
        parent = self.root / "cand-exc"
        parent.mkdir()

        def exploding_runner(_command, _cwd):
            raise RuntimeError("runner exploded")

        with self.assertRaises(RuntimeError):
            dev.check_draft(
                _model_draft(), repo=self.repo,
                runner=exploding_runner, candidate_parent=parent,
            )
        self.assertFalse(parent.exists())

    def test_no_candidate_accumulation_under_state_root(self) -> None:
        dev.check_draft(
            _model_draft(), repo=self.repo,
            runner=lambda c, w: {"cmd": c, "returncode": 0},
        )
        leftover = list(
            Path(dev.state_root_default()).glob("claude-multi-candidate-*")
        )
        self.assertEqual(leftover, [])

    def test_candidate_copy_exclusions(self) -> None:
        (self.repo / ".git").mkdir()
        (self.repo / ".git" / "HEAD").write_text("ref")
        (self.repo / ".slim").mkdir()
        (self.repo / "result").write_text("link")
        (self.repo / "symlink-file").symlink_to(self.repo / "flake.nix")
        observed = {}

        def inspecting_runner(command, cwd):
            observed["cwd"] = Path(cwd)
            return {"cmd": command, "returncode": 0}

        parent = self.root / "cand2"
        parent.mkdir()
        dev.check_draft(
            _model_draft(), repo=self.repo,
            runner=inspecting_runner,
            candidate_parent=parent,
        )
        candidate = observed["cwd"]
        self.assertTrue(str(candidate).startswith(str(parent)))
        self.assertFalse((candidate / ".git").exists())
        self.assertFalse((candidate / ".slim").exists())
        self.assertFalse((candidate / "result").exists())
        self.assertFalse((candidate / "symlink-file").exists())
        # cleanup removed the transient tree after evidence capture
        self.assertFalse(parent.exists())

    def test_check_rejects_invalid_candidate(self) -> None:
        draft = _model_draft()
        draft["entry"]["compatible_roles"] = ["cm-nonexistent"]
        with self.assertRaisesRegex(DevError, "candidate bundle invalid"):
            dev.check_draft(
                draft, repo=self.repo,
                runner=lambda c, w: {"cmd": c, "returncode": 0},
                candidate_parent=self.root / "cand3",
            )

    def test_provider_draft_check_and_review_succeed(self) -> None:
        result = dev.check_draft(
            _provider_draft(), repo=self.repo,
            runner=lambda c, w: {"cmd": c, "returncode": 0},
            candidate_parent=self.root / "cand-p",
        )
        self.assertTrue(result.bundle_valid)
        record = self._review(_provider_draft(), name="d2")
        self.assertEqual(record["draft"], "d2")
        self.assertEqual(len(record["files"]), 2)

    def test_build_failure_blocks_check_and_review(self) -> None:
        targets = ("tests/default.nix", "package.nix")
        for failing_index in (0, 1):
            with self.subTest(failing=failing_index):
                def runner(command, _cwd):
                    if command[-1].endswith(targets[failing_index]):
                        return {
                            "cmd": command,
                            "returncode": 1,
                            "stdout": "",
                            "stderr": "eval error: broken thing",
                        }
                    return {"cmd": command, "returncode": 0}

                with self.assertRaisesRegex(DevError, "candidate build failed"):
                    dev.check_draft(
                        _model_draft(), repo=self.repo, runner=runner,
                        candidate_parent=self.root / f"cand-f{failing_index}",
                    )
                with self.assertRaisesRegex(DevError, "candidate build failed"):
                    dev.review_draft(
                        _model_draft(), draft_name="d1", repo=self.repo,
                        runner=runner,
                        candidate_parent=Path(tempfile.mkdtemp(dir=self.root)),
                    )

    def test_build_failure_output_sanitized(self) -> None:
        secret_value = "super-secret-value-12345"
        os.environ["KIMI_CLAUDE_API_KEY"] = secret_value
        try:
            def runner(_command, _cwd):
                return {
                    "cmd": _command,
                    "returncode": 1,
                    "stdout": "",
                    "stderr": f"leaked {secret_value} here",
                }

            with self.assertRaises(DevError) as raised:
                dev.check_draft(
                    _model_draft(), repo=self.repo, runner=runner,
                    candidate_parent=self.root / "cand-s",
                )
            self.assertNotIn(secret_value, str(raised.exception))
            self.assertIn("***", str(raised.exception))
            self.assertIn("tests/default.nix", str(raised.exception))
        finally:
            del os.environ["KIMI_CLAUDE_API_KEY"]

    def test_binary_unavailable_skip_is_nonfatal_but_reported(self) -> None:
        def runner(command, _cwd):
            return {"cmd": command, "skipped": "nix-build not available"}

        result = dev.check_draft(
            _model_draft(), repo=self.repo, runner=runner,
            candidate_parent=self.root / "cand-k",
        )
        self.assertTrue(result.bundle_valid)
        self.assertTrue(all("skipped" in build for build in result.builds))


class ReviewTests(OnboardingTestCase):
    def test_review_record_exact_and_schema_valid(self) -> None:
        from claude_multi import validate

        record = self._review(_model_draft())
        schema = strict_json.load(CATALOG_ROOT / "schemas" / "review.schema.json")
        self.assertEqual(validate.validate(record, schema), [])
        self.assertEqual(record["repo"]["revision"], "deadbeef")
        self.assertEqual(record["draft_hash"], dev.draft_hash(_model_draft()))
        entry = record["files"][0]
        self.assertEqual(entry["path"], "home-manager/claude-multi/catalog/models.json")
        current = (self.repo / entry["path"]).read_bytes()
        self.assertEqual(
            entry["pre_image_hash"],
            "sha256:" + strict_json.sha256_hex(current),
        )
        images = dev.build_post_images(
            catalog.load_raw(self.repo / dev.V2_ROOT)["docs"], _model_draft()
        )
        self.assertEqual(
            entry["post_image_hash"],
            "sha256:" + strict_json.sha256_hex(images[entry["path"]]),
        )
        self.assertIn('"newmodel"', record["results"]["diff"])
        self.assertIn("\n+", record["results"]["diff"])

    def test_review_uses_dummy_secrets_only(self) -> None:
        record = self._review(_provider_draft(), name="d2")
        blob = strict_json.canonical_bytes(record).decode("utf-8")
        self.assertNotIn("ZETA_API_KEY-value", blob)
        self.assertNotIn("sk-", blob)


class PromoteTests(OnboardingTestCase):
    def test_promote_applies_reviewed_post_image(self) -> None:
        draft = _model_draft()
        record = self._review(draft)
        result = dev.promote_draft(
            draft, draft_name="d1", repo=self.repo, review_record=record,
            drafts_root=self.root / "drafts",
        )
        self.assertEqual(
            result["applied"], ["home-manager/claude-multi/catalog/models.json"]
        )
        post = strict_json.load(
            self.repo / "home-manager/claude-multi/catalog/models.json"
        )
        self.assertIn("newmodel", post["models"])
        self.assertTrue((self.root / "drafts" / "d1.journal.json").exists())

    def test_promoted_catalog_round_trips_through_load_catalog(self) -> None:
        draft = _model_draft()
        record = self._review(draft)
        dev.promote_draft(
            draft, draft_name="d1", repo=self.repo, review_record=record,
            drafts_root=self.root / "drafts",
        )
        bundle = catalog.load_catalog(self.repo / dev.V2_ROOT)
        self.assertIn("newmodel", bundle.models)
        provider_draft = _provider_draft()
        record2 = self._review(provider_draft, name="d2")
        dev.promote_draft(
            provider_draft, draft_name="d2", repo=self.repo,
            review_record=record2, drafts_root=self.root / "drafts",
        )
        bundle = catalog.load_catalog(self.repo / dev.V2_ROOT)
        self.assertIn("zeta", bundle.providers)
        self.assertIn("zetamodel", bundle.models)

    def test_promote_rejects_source_drift_after_review(self) -> None:
        draft = _model_draft()
        record = self._review(draft)
        target = self.repo / "home-manager/claude-multi/catalog/models.json"
        target.write_bytes(target.read_bytes() + b"\n")
        with self.assertRaisesRegex(DevError, "pre-image hash mismatch"):
            dev.promote_draft(
                draft, draft_name="d1", repo=self.repo, review_record=record
            )
        self.assertNotIn(
            "newmodel",
            strict_json.load(
                self.repo / "home-manager/claude-multi/catalog/models.json"
            )["models"],
        )

    def test_promote_rejects_tampered_review_hash(self) -> None:
        draft = _model_draft()
        record = self._review(draft)
        record["files"][0]["post_image_hash"] = "sha256:" + "f" * 64
        with self.assertRaisesRegex(DevError, "post-image hash mismatch"):
            dev.promote_draft(
                draft, draft_name="d1", repo=self.repo, review_record=record
            )

    def test_promote_rejects_draft_mismatch(self) -> None:
        draft = _model_draft()
        record = self._review(draft)
        with self.assertRaisesRegex(DevError, "does not match"):
            dev.promote_draft(
                _model_draft("other"), draft_name="d1", repo=self.repo,
                review_record=record,
            )

    def test_promote_journal_rollback_on_failure(self) -> None:
        draft = _provider_draft()
        record = self._review(draft, name="d2")
        original_providers = (
            self.repo / "home-manager/claude-multi/catalog/providers.json"
        ).read_bytes()
        calls = {"count": 0}
        real_write = dev._repo_atomic_write

        def flaky_write(path, data):
            calls["count"] += 1
            if calls["count"] == 2:
                raise OSError("injected mid-promote failure")
            return real_write(path, data)

        with mock.patch.object(dev, "_repo_atomic_write", side_effect=flaky_write):
            with self.assertRaisesRegex(DevError, "rolled back"):
                dev.promote_draft(
                    draft, draft_name="d2", repo=self.repo, review_record=record
                )
        # first file restored to its exact pre-image
        self.assertEqual(
            (self.repo / "home-manager/claude-multi/catalog/providers.json").read_bytes(),
            original_providers,
        )
        models_doc = strict_json.load(
            self.repo / "home-manager/claude-multi/catalog/models.json"
        )
        self.assertNotIn("zetamodel", models_doc["models"])

    def test_patch_output_emit_only(self) -> None:
        draft = _model_draft()
        before = (
            self.repo / "home-manager/claude-multi/catalog/models.json"
        ).read_bytes()
        output = self.root / "patch.diff"
        dev.promote_patch_output(draft, repo=self.repo, output=output)
        self.assertEqual(
            (self.repo / "home-manager/claude-multi/catalog/models.json").read_bytes(),
            before,
        )
        content = output.read_text()
        self.assertIn("newmodel", content)
        self.assertIn("---", content)
        self.assertEqual(stat.S_IMODE(os.lstat(output).st_mode), 0o600)
        self.assertNotIn(
            "newmodel",
            strict_json.load(
                self.repo / "home-manager/claude-multi/catalog/models.json"
            )["models"],
        )

    def test_patch_output_overwrites_regular_target_atomically(self) -> None:
        output = self.root / "patch.diff"
        state.atomic_write(output, b"old content")
        dev.promote_patch_output(_model_draft(), repo=self.repo, output=output)
        self.assertIn("newmodel", output.read_text())
        self.assertEqual(stat.S_IMODE(os.lstat(output).st_mode), 0o600)

    def test_patch_output_rejects_symlink_target(self) -> None:
        real = self.root / "real.diff"
        state.atomic_write(real, b"x")
        link = self.root / "link.diff"
        link.symlink_to(real)
        with self.assertRaisesRegex(DevError, "symlink"):
            dev.promote_patch_output(_model_draft(), repo=self.repo, output=link)
        self.assertEqual(real.read_bytes(), b"x")

    def test_patch_output_rejects_non_private_parent(self) -> None:
        public = self.root / "public"
        public.mkdir(mode=0o755)
        with self.assertRaisesRegex(Exception, "state directory"):
            dev.promote_patch_output(
                _model_draft(), repo=self.repo, output=public / "p.diff"
            )

    def test_promote_modes_mutually_exclusive(self) -> None:
        with self.assertRaisesRegex(DevError, "never both"):
            dev.resolve_promote_mode(Path("/repo"), "out.diff")
        with self.assertRaisesRegex(DevError, "requires"):
            dev.resolve_promote_mode(None, None)
        self.assertEqual(dev.resolve_promote_mode(Path("/repo"), None), "apply")
        self.assertEqual(dev.resolve_promote_mode(None, "out.diff"), "patch")

    def test_promote_cli_mutual_exclusion(self) -> None:
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)}):
            code = dev.main(
                ["promote", "d1", "--repo", str(self.repo), "--patch-output", str(self.root / "x.diff")]
            )
        self.assertEqual(code, 2)

    def test_repo_verification(self) -> None:
        with self.assertRaisesRegex(DevError, "Nix store"):
            dev.verify_repo("/nix/store/abcdef")
        with self.assertRaisesRegex(DevError, "missing"):
            dev.verify_repo(self.root)
        link = self.root / "repolink"
        link.symlink_to(self.repo)
        with self.assertRaisesRegex(DevError, "symlink"):
            dev.verify_repo(link)


class SmokeTests(OnboardingTestCase):
    def test_smoke_without_consent_zero_requests(self) -> None:
        outcome = dev.smoke_test(
            "gpt-multi-sol-high",
            allow_provider_call=False,
        )
        self.assertEqual(outcome["status"], "refused")
        self.assertEqual(outcome["requests"], 0)
        self.assertIn("--allow-provider-call", outcome["guidance"])

    def test_smoke_with_consent_fails_closed_without_transport(self) -> None:
        with self.assertRaisesRegex(DevError, "no provider transport"):
            dev.smoke_test(
                "gpt-multi-sol-high",
                allow_provider_call=True,
            )


class ImportHygieneTests(unittest.TestCase):
    def test_runtime_modules_never_import_dev(self) -> None:
        import ast

        src = CATALOG_ROOT / "src" / "claude_multi"
        offenders: list[str] = []
        for module in sorted(src.glob("*.py")):
            if module.name in ("dev.py",):
                continue
            tree = ast.parse(module.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and "dev" in node.module.split("."):
                    offenders.append(f"{module.name}: from {node.module}")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if "dev" in alias.name.split("."):
                            offenders.append(f"{module.name}: import {alias.name}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()


class DevCLIErrorTests(OnboardingTestCase):
    def test_model_add_outside_checkout_exits_2_clean(self) -> None:
        spec = self.root / "spec.json"
        spec.write_text('{"provider": "openai", "entry": {}}')
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)}):
            with mock.patch("sys.argv", ["claude-multi-dev"]):
                code = dev.main(
                    ["model", "add", "--from-json", str(spec), "--repo", str(self.root / "nowhere")]
                )
        self.assertEqual(code, 2)

    def test_malformed_draft_exits_2_without_traceback(self) -> None:
        bad = self.root / "bad.json"
        bad.write_text("{not json")
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)}):
            with mock.patch("sys.stderr", stderr):
                code = dev.main(
                    ["model", "add", "--from-json", str(bad), "--repo", str(self.repo)]
                )
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_missing_spec_field_exits_2(self) -> None:
        spec = self.root / "spec.json"
        spec.write_text('{"entry": {}}')
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)}):
            code = dev.main(
                ["model", "add", "--from-json", str(spec), "--repo", str(self.repo)]
            )
        self.assertEqual(code, 2)

    def test_unsafe_patch_output_parent_exits_2(self) -> None:
        self.drafts.save("d1", _model_draft())
        public = self.root / "public"
        public.mkdir(mode=0o755)
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)}):
            code = dev.main(
                [
                    "promote", "d1", "--patch-output", str(public / "x.diff"),
                ]
            )
        self.assertEqual(code, 2)


class PatchPolicyTests(OnboardingTestCase):
    def test_patch_output_blocked_by_new_entry_policy_no_file(self) -> None:
        # Composition already lists the draft's model: after the post-image
        # adds it to models.json the candidate is valid, but the New · Off
        # policy must block emission.
        composition_path = (
            self.repo / "home-manager/claude-multi/catalog/compositions/default.json"
        )
        composition = strict_json.load(composition_path)
        composition["availability"]["models"]["newmodel"] = "agents"
        dev._repo_atomic_write(
            composition_path, strict_json.canonical_file_bytes(composition)
        )
        output = self.root / "patch.diff"
        with self.assertRaisesRegex(DevError, "New · Off"):
            dev.promote_patch_output(_model_draft(), repo=self.repo, output=output)
        self.assertFalse(output.exists())


class RedactionTests(OnboardingTestCase):
    def test_short_values_redacted(self) -> None:
        with mock.patch.dict(os.environ, {"KIMI_CLAUDE_API_KEY": "abc12"}):
            self.assertEqual(dev._sanitize_output("token abc12 tail"), "token *** tail")

    def test_overlapping_values_fully_redacted(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"A_KEY": "secretkey123", "B_TOKEN": "key123"},
        ):
            text = dev._sanitize_output("leak secretkey123 and key123 done")
        self.assertNotIn("secretkey123", text)
        self.assertNotIn("key123", text)
        self.assertIn("leak", text)
        self.assertIn("done", text)

    def test_nonsecret_diagnostics_retained(self) -> None:
        self.assertEqual(
            dev._sanitize_output("eval error: broken thing"),
            "eval error: broken thing",
        )

    def test_empty_values_ignored(self) -> None:
        with mock.patch.dict(os.environ, {"EMPTY_KEY": ""}):
            self.assertEqual(dev._sanitize_output("plain"), "plain")


class ReviewRecordValidationTests(OnboardingTestCase):
    def _valid_record(self):
        return self._review(_model_draft())

    def test_missing_fields_rejected_deterministically(self) -> None:
        record = self._valid_record()
        del record["files"]
        with self.assertRaisesRegex(DevError, "review record invalid.*files"):
            dev.promote_draft(
                _model_draft(), draft_name="d1", repo=self.repo,
                review_record=record,
            )

    def test_unknown_field_rejected(self) -> None:
        record = self._valid_record()
        record["surprise"] = True
        with self.assertRaisesRegex(DevError, "review record invalid"):
            dev.promote_draft(
                _model_draft(), draft_name="d1", repo=self.repo,
                review_record=record,
            )

    def test_wrong_types_rejected(self) -> None:
        record = self._valid_record()
        record["draft_hash"] = 12345
        with self.assertRaisesRegex(DevError, "review record invalid"):
            dev.promote_draft(
                _model_draft(), draft_name="d1", repo=self.repo,
                review_record=record,
            )

    def test_non_object_record_rejected(self) -> None:
        with self.assertRaisesRegex(DevError, "not a JSON object"):
            dev.promote_draft(
                _model_draft(), draft_name="d1", repo=self.repo,
                review_record=["not", "a", "dict"],
            )

    def test_valid_record_happy_path(self) -> None:
        draft = _model_draft()
        result = dev.promote_draft(
            draft, draft_name="d1", repo=self.repo,
            review_record=self._review(draft),
        )
        self.assertEqual(
            result["applied"], ["home-manager/claude-multi/catalog/models.json"]
        )

    def test_cli_corrupt_review_json_exits_2_clean(self) -> None:
        self.drafts.save("d1", _model_draft())
        state.atomic_write(self.root / "drafts" / "d1.review.json", b"{oops")
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)}):
            with mock.patch("sys.stderr", stderr):
                code = dev.main(["promote", "d1", "--repo", str(self.repo)])
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_unknown_field_review_exits_2(self) -> None:
        draft = _model_draft()
        self.drafts.save("d1", draft)
        record = self._review(draft)
        record["surprise"] = True
        state.atomic_write(
            self.root / "drafts" / "d1.review.json",
            strict_json.canonical_file_bytes(record),
        )
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": str(self.root)}):
            with mock.patch("sys.stderr", stderr):
                code = dev.main(["promote", "d1", "--repo", str(self.repo)])
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", stderr.getvalue())


class CandidateFailureCleanupTests(OnboardingTestCase):
    def test_copy_failure_removes_partial_candidate(self) -> None:
        parent = self.root / "claude-multi-candidate-x"
        parent.mkdir()
        sentinel_dir = self.root / "outside"
        sentinel_dir.mkdir()
        (sentinel_dir / "keep.txt").write_text("untouched")
        with mock.patch.object(
            dev.shutil, "copytree", side_effect=OSError("injected copy failure")
        ):
            with self.assertRaises(OSError):
                dev.materialize_candidate(self.repo, {}, parent)
        self.assertFalse(parent.exists())
        self.assertEqual(
            list(self.root.glob("claude-multi-candidate-*")), []
        )
        self.assertEqual((sentinel_dir / "keep.txt").read_text(), "untouched")

    def test_image_apply_failure_removes_candidate(self) -> None:
        parent = self.root / "claude-multi-candidate-y"
        parent.mkdir()
        images = {"home-manager/claude-multi/catalog/models.json": b"{}\n"}
        with mock.patch.object(
            dev.state, "atomic_write", side_effect=OSError("injected write failure")
        ):
            with self.assertRaises(OSError):
                dev.materialize_candidate(self.repo, images, parent)
        self.assertFalse(parent.exists())
        self.assertEqual(
            list(self.root.glob("claude-multi-candidate-*")), []
        )

    def test_default_location_no_leftovers(self) -> None:
        with mock.patch.object(
            dev.shutil, "copytree", side_effect=OSError("injected copy failure")
        ):
            with self.assertRaises(OSError):
                dev.materialize_candidate(self.repo, {}, None)
        leftover = list(
            Path(dev.state_root_default()).glob("claude-multi-candidate-*")
        )
        self.assertEqual(leftover, [])

class NewEntryPolicyTests(OnboardingTestCase):
    """New · Off applies to provider-kind drafts too (not just model-kind)."""

    def _repo_with_slotted_zetamodel(self) -> Path:
        import json as _json

        target = self.root / "repo-slotted"
        shutil.copytree(self.repo, target)
        composition_path = target / dev.V2_ROOT / "catalog" / "compositions" / "default.json"
        document = _json.loads(composition_path.read_text(encoding="utf-8"))
        document["availability"]["providers"]["zeta"] = "agents"
        document["availability"]["models"]["zetamodel"] = "agents"
        composition_path.write_text(
            _json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return target

    def test_provider_draft_model_must_not_appear_in_compositions(self) -> None:
        repo = self._repo_with_slotted_zetamodel()
        with self.assertRaisesRegex(DevError, "New · Off"):
            dev.check_draft(
                _provider_draft(), repo=repo,
                runner=lambda c, w: {"cmd": c, "returncode": 0},
                candidate_parent=self.root / "cand-newoff",
            )

class ProviderIdNewOffTests(OnboardingTestCase):
    def _repo_with_zeta_provider_in_composition(self) -> Path:
        import json as _json

        target = self.root / "repo-provider-slotted"
        shutil.copytree(self.repo, target)
        composition_path = target / dev.V2_ROOT / "catalog" / "compositions" / "default.json"
        document = _json.loads(composition_path.read_text(encoding="utf-8"))
        document["availability"]["providers"]["zeta"] = "agents"
        composition_path.write_text(
            _json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return target

    def test_provider_id_must_not_appear_in_compositions(self) -> None:
        repo = self._repo_with_zeta_provider_in_composition()
        with self.assertRaisesRegex(DevError, "New · Off"):
            dev.check_draft(
                _provider_draft(), repo=repo,
                runner=lambda c, w: {"cmd": c, "returncode": 0},
                candidate_parent=self.root / "cand-pnewoff",
            )
