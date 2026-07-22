"""Tests for the durable per-session scope: compile, write, gate, goldens."""

from __future__ import annotations

import copy
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

from claude_multi import catalog, compiler, composition, scope, state, strict_json
from claude_multi.scope import ScopeError


CATALOG_ROOT = Path(__file__).resolve().parents[1]
GOLDENS = CATALOG_ROOT / "tests" / "goldens" / "default"
FIXED_SESSION = "11111111-1111-4111-8111-111111111111"


def _bundle():
    return catalog.load_catalog(CATALOG_ROOT)


def _resolved(document=None):
    bundle = _bundle()
    return bundle, composition.resolve(
        bundle.docs, document if document is not None else bundle.default_composition
    )


def _plan(document=None):
    bundle, resolved = _resolved(document)
    return bundle, resolved, scope.compile_scope(
        resolved,
        bundle.docs["roles"]["roles"],
        bundle.prompt_bodies,
        scope.catalog_meta_from_docs(bundle.docs),
    )


def _off_document():
    bundle = _bundle()
    document = copy.deepcopy(bundle.default_composition)
    document["workflows"] = "off"
    return document


def _frontmatter(data: bytes) -> dict[str, str]:
    text = data.decode("utf-8")
    lines = text.splitlines()
    assert lines[0] == "---"
    fields: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            break
        key, _, value = line.partition(": ")
        fields[key] = value
    return fields


class CompileScopeAgentFileTests(unittest.TestCase):
    def test_one_file_per_variant_named_by_variant_id(self) -> None:
        _, resolved, plan = _plan()
        expected = {
            f".claude/agents/{variant.id}.md" for variant in resolved.variants
        }
        self.assertEqual(set(plan.agent_files), expected)
        self.assertEqual(len(plan.agent_files), 6)
        self.assertEqual(
            plan.agent_names, {variant.id for variant in resolved.variants}
        )

    def test_frontmatter_fields_and_values(self) -> None:
        _, _, plan = _plan()
        fields = _frontmatter(plan.agent_files[".claude/agents/cm-analyst-sol-high.md"])
        self.assertEqual(fields["name"], "cm-analyst-sol-high")
        self.assertEqual(fields["model"], "gpt-multi-sol-high")
        self.assertEqual(fields["effort"], "high")
        self.assertNotIn("isolation", fields)
        for forbidden in (
            "permissionMode",
            "hooks",
            "mcpServers",
            "tools",
            "disallowedTools",
        ):
            self.assertNotIn(forbidden, fields)

    def test_isolation_only_when_the_role_declares_it(self) -> None:
        _, _, plan = _plan()
        implementer = _frontmatter(
            plan.agent_files[".claude/agents/cm-implementer-sol-high.md"]
        )
        self.assertEqual(implementer["isolation"], "worktree")
        analyst = _frontmatter(plan.agent_files[".claude/agents/cm-analyst-sol-high.md"])
        self.assertNotIn("isolation", analyst)

    def test_description_carries_base_routing_and_sentinel_verbatim(self) -> None:
        bundle, resolved = _resolved()
        base = compiler.variant_description(
            bundle.docs["roles"]["roles"]["cm-analyst"],
            {"display": "GPT-5.6 Sol"},
            next(v for v in resolved.variants if v.id == "cm-analyst-sol-high"),
        )
        _, _, plan = _plan()
        fields = _frontmatter(plan.agent_files[".claude/agents/cm-analyst-sol-high.md"])
        description = fields["description"].strip('"')
        self.assertTrue(description.startswith(base))
        self.assertIn(scope.SENTINEL_SUFFIX, description)
        self.assertIn(
            "Managed cm session: if a selected cm-* type is unavailable, stop; "
            "never substitute a generic agent.",
            description,
        )

    def test_every_variant_description_carries_the_sentinel(self) -> None:
        _, _, plan = _plan()
        for relpath, data in plan.agent_files.items():
            with self.subTest(relpath=relpath):
                self.assertIn(
                    scope.SENTINEL_SUFFIX,
                    _frontmatter(data)["description"],
                )

    def test_reviewer_independence_sentence_verbatim_with_family(self) -> None:
        _, _, plan = _plan()
        gpt55 = _frontmatter(
            plan.agent_files[".claude/agents/cm-reviewer-sol-xhigh.md"]
        )["description"]
        self.assertIn(
            "Review independence: a change authored by a openai-family variant "
            "must not receive its sole verdict from another openai-family variant "
            "while a cross-family reviewer is enabled.",
            gpt55,
        )
        opus = _frontmatter(
            plan.agent_files[".claude/agents/cm-reviewer-opus-xhigh.md"]
        )["description"]
        self.assertIn("anthropic-family variant", opus)
        analyst = _frontmatter(
            plan.agent_files[".claude/agents/cm-analyst-sol-high.md"]
        )["description"]
        self.assertNotIn("Review independence:", analyst)
        implementer = _frontmatter(
            plan.agent_files[".claude/agents/cm-implementer-sol-high.md"]
        )["description"]
        self.assertNotIn("Review independence:", implementer)

    def test_body_is_canonical_role_prompt_bytes(self) -> None:
        bundle, resolved, plan = _plan()
        role_by_id = {variant.id: variant.role for variant in resolved.variants}
        for relpath, data in plan.agent_files.items():
            variant_id = relpath.split("/")[-1].removesuffix(".md")
            body = data.split(b"---\n\n", 1)[1]
            self.assertEqual(body, bundle.prompt_bodies[role_by_id[variant_id]])

    def test_identical_bodies_across_variants_of_a_role(self) -> None:
        _, _, plan = _plan()
        analyst_bodies = {
            data.split(b"---\n\n", 1)[1]
            for relpath, data in plan.agent_files.items()
            if "/cm-analyst-" in relpath
        }
        self.assertEqual(len(analyst_bodies), 1)

    def test_ultracode_agent_effort_fails_closed(self) -> None:
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        resolved = composition.resolve(bundle.docs, document)
        mutated = composition.ResolvedComposition(
            **{**resolved.__dict__, "variants": tuple(
                composition.ResolvedVariant(
                    **{**variant.__dict__, "agent_effort": "ultracode"}
                )
                if variant.id == "cm-analyst-sol-high"
                else variant
                for variant in resolved.variants
            )}
        )
        with self.assertRaisesRegex(ScopeError, "ultracode"):
            scope.compile_scope(
                mutated,
                bundle.docs["roles"]["roles"],
                bundle.prompt_bodies,
                scope.catalog_meta_from_docs(bundle.docs),
            )

    def test_declared_frontmatter_tools_fail_closed(self) -> None:
        bundle, resolved = _resolved()
        roles = copy.deepcopy(bundle.docs["roles"]["roles"])
        roles["cm-analyst"]["tools"] = ["Read"]
        with self.assertRaisesRegex(ScopeError, "tools"):
            scope.compile_scope(
                resolved,
                roles,
                bundle.prompt_bodies,
                scope.catalog_meta_from_docs(bundle.docs),
            )


class CompileScopeSettingsTests(unittest.TestCase):
    def test_closed_key_set_exact(self) -> None:
        _, _, plan = _plan()
        self.assertLessEqual(set(plan.settings), scope.COMPILED_SETTINGS_KEYS)
        self.assertEqual(
            set(plan.settings),
            {
                "disableWorkflows",
                "workflowSizeGuideline",
                "workflowKeywordTriggerEnabled",
                "permissions",
                "availableModels",
                "worktree",
            },
        )

    def test_workflow_keys(self) -> None:
        _, _, plan = _plan()
        self.assertIs(plan.settings["disableWorkflows"], False)
        self.assertEqual(plan.settings["workflowSizeGuideline"], "medium")
        self.assertIs(plan.settings["workflowKeywordTriggerEnabled"], False)

    def test_workflows_off_flips_disable_workflows(self) -> None:
        _, _, plan = _plan(_off_document())
        self.assertIs(plan.settings["disableWorkflows"], True)

    def test_permissions_deny_from_policy_and_aliases(self) -> None:
        _, _, plan = _plan()
        self.assertEqual(
            plan.settings["permissions"],
            {"deny": ["Agent(Explore)", "Agent(general-purpose)", "Agent(claude)"]},
        )

    def test_plan_native_means_no_plan_deny(self) -> None:
        _, _, plan = _plan()
        self.assertNotIn("Agent(Plan)", plan.settings["permissions"]["deny"])

    def test_both_builtins_off_uses_env_semantics_not_denies(self) -> None:
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        document["native_agents"] = {
            "explore": "off",
            "plan": "off",
            "general_purpose": "on",
        }
        _, _, plan = _plan(document)
        denies = plan.settings["permissions"]["deny"]
        self.assertNotIn("Agent(Explore)", denies)
        self.assertNotIn("Agent(Plan)", denies)
        self.assertEqual(denies, ["Agent(claude)"])

    def test_available_models_sorted_catalog_selectors_plus_lead(self) -> None:
        _, _, plan = _plan()
        self.assertEqual(
            plan.settings["availableModels"],
            [
                "claude-fable-5[1m]",
                "claude-multi-kimi-k3[1m]",
                "claude-multi-opus-4-8[1m]",
                "gpt-multi-gpt55-high",
                "gpt-multi-sol-high",
                "gpt-multi-sol-xhigh",
            ],
        )

    def test_worktree_baseref_only_with_worktree_isolation(self) -> None:
        _, _, plan = _plan()
        self.assertEqual(plan.settings["worktree"], {"baseRef": "head"})
        bundle = _bundle()
        document = copy.deepcopy(bundle.default_composition)
        document["slots"] = [
            slot
            for slot in document["slots"]
            if slot["role"] != "cm-implementer"
        ]
        _, _, reduced = _plan(document)
        self.assertNotIn("worktree", reduced.settings)

    def test_base_settings_missing_workflow_key_fails_closed(self) -> None:
        bundle, resolved = _resolved()
        meta = scope.CatalogMeta(
            base_settings={"disableWorkflows": False},
            client_selectors=(),
            generic_agent_aliases=(),
        )
        with self.assertRaisesRegex(ScopeError, "workflow keys"):
            scope.compile_scope(
                resolved, bundle.docs["roles"]["roles"], bundle.prompt_bodies, meta
            )

    def test_unknown_base_key_fails_closed(self) -> None:
        bundle, resolved = _resolved()
        meta = scope.catalog_meta_from_docs(bundle.docs)
        meta = scope.CatalogMeta(
            base_settings={**meta.base_settings, "surprise": True},
            client_selectors=meta.client_selectors,
            generic_agent_aliases=meta.generic_agent_aliases,
        )
        with self.assertRaisesRegex(ScopeError, "allowlist"):
            scope.compile_scope(
                resolved, bundle.docs["roles"]["roles"], bundle.prompt_bodies, meta
            )


class PlanHashTests(unittest.TestCase):
    def test_deterministic(self) -> None:
        _, _, first = _plan()
        _, _, second = _plan()
        self.assertEqual(scope.plan_hash(first), scope.plan_hash(second))
        self.assertTrue(scope.plan_hash(first).startswith("sha256:"))

    def test_changes_with_content(self) -> None:
        _, _, plan = _plan()
        mutated = scope.ScopePlan(
            agent_files=dict(plan.agent_files),
            settings={**plan.settings, "disableWorkflows": True},
        )
        self.assertNotEqual(scope.plan_hash(plan), scope.plan_hash(mutated))


class WriteScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-scope-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        _, _, self.plan = _plan()

    def test_writes_tree_with_private_modes(self) -> None:
        live = scope.write_scope(self.root, FIXED_SESSION, self.plan)
        self.assertEqual(live, self.root / "scopes" / FIXED_SESSION)
        self.assertFalse((self.root / "scopes" / f".{FIXED_SESSION}.new").exists())
        for dirpath in (
            self.root / "scopes",
            live,
            live / ".claude",
            live / ".claude" / "agents",
        ):
            mode = stat.S_IMODE(os.lstat(dirpath).st_mode)
            self.assertEqual(mode, 0o700, f"{dirpath}: {oct(mode)}")
        for relpath, data in self.plan.agent_files.items():
            target = live.joinpath(*relpath.split("/"))
            self.assertEqual(target.read_bytes(), data)
            self.assertEqual(stat.S_IMODE(os.lstat(target).st_mode), 0o600)
        settings = live / "settings.json"
        self.assertEqual(
            settings.read_bytes(), strict_json.canonical_file_bytes(self.plan.settings)
        )
        self.assertEqual(stat.S_IMODE(os.lstat(settings).st_mode), 0o600)

    def test_rewrite_replaces_existing_live_scope(self) -> None:
        scope.write_scope(self.root, FIXED_SESSION, self.plan)
        _, _, other = _plan(_off_document())
        live = scope.write_scope(self.root, FIXED_SESSION, other)
        self.assertEqual(
            (live / "settings.json").read_bytes(),
            strict_json.canonical_file_bytes(other.settings),
        )

    def test_stale_staging_dir_removed_first(self) -> None:
        staging = self.root / "scopes" / f".{FIXED_SESSION}.new"
        staging.mkdir(parents=True)
        os.chmod(self.root / "scopes", 0o700)
        os.chmod(staging, 0o700)
        (staging / "junk").write_bytes(b"junk")
        os.chmod(staging / "junk", 0o600)
        live = scope.write_scope(self.root, FIXED_SESSION, self.plan)
        self.assertFalse(staging.exists())
        self.assertFalse((live / "junk").exists())

    def test_symlinked_live_scope_refused(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        scopes_root = self.root / "scopes"
        scopes_root.mkdir()
        os.chmod(scopes_root, 0o700)
        (scopes_root / FIXED_SESSION).symlink_to(outside)
        with self.assertRaises(state.StateError):
            scope.write_scope(self.root, FIXED_SESSION, self.plan)

    def test_traversal_relpath_refused(self) -> None:
        plan = scope.ScopePlan(
            agent_files={"../escape.md": b"x"}, settings=dict(self.plan.settings)
        )
        with self.assertRaises(ScopeError):
            scope.write_scope(self.root, FIXED_SESSION, plan)

    def test_scope_paths_require_uuid4(self) -> None:
        with self.assertRaisesRegex(ScopeError, "UUIDv4"):
            scope.scope_dir(self.root, "safe-but-not-a-uuid")
        with self.assertRaisesRegex(ScopeError, "UUIDv4"):
            scope.write_scope(self.root, "safe-but-not-a-uuid", self.plan)
        with self.assertRaisesRegex(ScopeError, "UUIDv4"):
            scope.remove_scope(self.root, "safe-but-not-a-uuid")

    def test_remove_scope(self) -> None:
        scope.write_scope(self.root, FIXED_SESSION, self.plan)
        self.assertTrue(scope.remove_scope(self.root, FIXED_SESSION))
        self.assertFalse((self.root / "scopes" / FIXED_SESSION).exists())
        self.assertFalse(scope.remove_scope(self.root, FIXED_SESSION))

    def test_remove_scope_clears_staging(self) -> None:
        staging = self.root / "scopes" / f".{FIXED_SESSION}.new"
        staging.mkdir(parents=True)
        os.chmod(self.root / "scopes", 0o700)
        self.assertTrue(scope.remove_scope(self.root, FIXED_SESSION))
        self.assertFalse(staging.exists())


class CollisionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="claude-multi-gate-"))
        os.chmod(self.root, 0o700)
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        _, _, plan = _plan()
        self.names = plan.agent_names

    def _agent_file(self, directory: Path, name: str, *, stem: str | None = None) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{stem or name}.md"
        path.write_text(f"---\nname: {name}\n---\n\nbody\n")
        return path

    def test_project_tree_collision_found(self) -> None:
        project = self.root / "repo"
        cwd = project / "sub" / "dir"
        cwd.mkdir(parents=True)
        (project / ".git").mkdir(parents=True)
        offender = self._agent_file(
            project / ".claude" / "agents", "cm-analyst-sol-high"
        )
        collisions = scope.find_cm_collisions(cwd, (), self.names)
        self.assertEqual(collisions, [(offender, "cm-analyst-sol-high")])

    def test_agent_directories_are_scanned_recursively(self) -> None:
        project = self.root / "repo"
        (project / ".git").mkdir(parents=True)
        offender = self._agent_file(
            project / ".claude" / "agents" / "review" / "security",
            "cm-analyst-sol-high",
        )
        collisions = scope.find_cm_collisions(project, (), self.names)
        self.assertEqual(collisions, [(offender, "cm-analyst-sol-high")])

    def test_walk_stops_after_git_root(self) -> None:
        project = self.root / "repo"
        cwd = project / "sub"
        cwd.mkdir(parents=True)
        (project / ".git").mkdir(parents=True)
        above = self._agent_file(
            self.root / ".claude" / "agents", "cm-analyst-sol-high"
        )
        collisions = scope.find_cm_collisions(cwd, (), self.names)
        self.assertEqual(collisions, [])
        # Without the git marker the walk continues to the filesystem root.
        (project / ".git").rmdir()
        collisions = scope.find_cm_collisions(cwd, (), self.names)
        self.assertEqual(collisions, [(above, "cm-analyst-sol-high")])

    def test_exact_match_only(self) -> None:
        project = self.root / "repo"
        (project / ".git").mkdir(parents=True)
        self._agent_file(project / ".claude" / "agents", "cm-unrelated")
        self._agent_file(project / ".claude" / "agents", "cm-analyst-sol-high-x")
        self._agent_file(project / ".claude" / "agents", "other-agent")
        collisions = scope.find_cm_collisions(project, (), self.names)
        self.assertEqual(collisions, [])

    def test_passthrough_add_dir_scanned(self) -> None:
        project = self.root / "repo"
        (project / ".git").mkdir(parents=True)
        extra = self.root / "extra"
        offender = self._agent_file(
            extra / ".claude" / "agents", "cm-reviewer-sol-xhigh"
        )
        collisions = scope.find_cm_collisions(project, (extra,), self.names)
        self.assertEqual(collisions, [(offender, "cm-reviewer-sol-xhigh")])

    def test_managed_dir_scanned_when_present(self) -> None:
        managed = self.root / "managed"
        offender = self._agent_file(managed, "cm-implementer-sol-high")
        collisions = scope.find_cm_collisions(
            self.root, (), self.names, managed_agents_dir=managed
        )
        self.assertIn((offender, "cm-implementer-sol-high"), collisions)

    def test_managed_dir_absent_is_not_an_error(self) -> None:
        collisions = scope.find_cm_collisions(
            self.root,
            (),
            self.names,
            managed_agents_dir=self.root / "no-such-dir",
        )
        self.assertEqual(collisions, [])

    def test_quoted_name_and_missing_frontmatter(self) -> None:
        project = self.root / "repo"
        (project / ".git").mkdir(parents=True)
        agents = project / ".claude" / "agents"
        offender = self._agent_file(agents, '"cm-analyst-sol-high"', stem="quoted")
        self._agent_file(agents, "no-frontmatter", stem="plain").write_text("no markers\n")
        collisions = scope.find_cm_collisions(project, (), self.names)
        self.assertEqual(collisions, [(offender, "cm-analyst-sol-high")])

    def test_inline_yaml_comments_do_not_hide_collisions(self) -> None:
        project = self.root / "repo"
        (project / ".git").mkdir(parents=True)
        agents = project / ".claude" / "agents"
        agents.mkdir(parents=True)
        unquoted = agents / "unquoted.md"
        unquoted.write_text(
            "---\nname: cm-analyst-sol-high # project override\n---\n\nbody\n"
        )
        quoted = agents / "quoted-comment.md"
        quoted.write_text(
            '---\nname: "cm-reviewer-sol-xhigh" # project override\n---\n\nbody\n'
        )
        collisions = scope.find_cm_collisions(project, (), self.names)
        self.assertEqual(
            collisions,
            [
                (quoted, "cm-reviewer-sol-xhigh"),
                (unquoted, "cm-analyst-sol-high"),
            ],
        )

    def test_non_md_files_ignored(self) -> None:
        project = self.root / "repo"
        (project / ".git").mkdir(parents=True)
        agents = project / ".claude" / "agents"
        agents.mkdir(parents=True)
        (agents / "notes.txt").write_text("name: cm-analyst-sol-high\n")
        collisions = scope.find_cm_collisions(project, (), self.names)
        self.assertEqual(collisions, [])

    def test_result_sorted_deterministically(self) -> None:
        project = self.root / "repo"
        (project / ".git").mkdir(parents=True)
        agents = project / ".claude" / "agents"
        self._agent_file(agents, "cm-reviewer-opus-xhigh")
        self._agent_file(agents, "cm-analyst-sol-high")
        collisions = scope.find_cm_collisions(project, (), self.names)
        self.assertEqual(
            [name for _, name in collisions],
            ["cm-analyst-sol-high", "cm-reviewer-opus-xhigh"],
        )


class ScopeGoldenTests(unittest.TestCase):
    def test_settings_golden(self) -> None:
        _, _, plan = _plan()
        expected = (GOLDENS / "scope" / "settings.json").read_bytes()
        self.assertEqual(
            strict_json.canonical_file_bytes(plan.settings), expected
        )

    def test_agent_files_golden_tree(self) -> None:
        _, _, plan = _plan()
        scope_goldens = GOLDENS / "scope"
        for relpath, data in plan.agent_files.items():
            with self.subTest(relpath=relpath):
                expected = scope_goldens.joinpath(*relpath.split("/")).read_bytes()
                self.assertEqual(data, expected)

    def test_golden_tree_has_no_extra_files(self) -> None:
        _, _, plan = _plan()
        scope_goldens = GOLDENS / "scope"
        on_disk = {
            str(path.relative_to(scope_goldens))
            for path in scope_goldens.rglob("*")
            if path.is_file()
        }
        expected = {*plan.agent_files, "settings.json"}
        self.assertEqual(on_disk, expected)


if __name__ == "__main__":
    unittest.main()
