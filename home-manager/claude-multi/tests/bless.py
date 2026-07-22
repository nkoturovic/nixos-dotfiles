"""Regenerate the checked-in golden files for the default composition.

Usage (from ``home-manager/claude-multi/``)::

    PYTHONPATH=src:tests python3 tests/bless.py

The goldens under ``tests/goldens/default/`` are byte-exact expectations for
the deterministic compiler and scope compiler: legacy argv (parity with the
pre-rethink launcher), durable argv, environment, generated ``--agents``
JSON, the lead appendix, and the durable scope tree (compiled
``settings.json`` plus one agent file per selected variant). Every file is a
pure function of the trusted catalog and the fixed session UUID below — no
timestamps, no randomness, no machine paths — so regeneration is stable
across checkouts. Run this after any intentional compiler/scope change and
review the diff before committing; the golden tests in ``tests/test_compiler.py``
and ``tests/test_scope.py`` assert these exact bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from claude_multi import catalog, compiler, composition, scope, strict_json


CATALOG_ROOT = Path(__file__).resolve().parents[1]
GOLDENS = CATALOG_ROOT / "tests" / "goldens" / "default"
FIXED_SESSION = "11111111-1111-4111-8111-111111111111"
SETTINGS_PATH = Path("/trusted/settings.json")
SCOPE_DIR = Path("/state") / "scopes" / FIXED_SESSION


def _compile(*, durable: bool, action):
    bundle = catalog.load_catalog(CATALOG_ROOT)
    resolved = composition.resolve(bundle.docs, bundle.default_composition)
    snap = composition.snapshot(resolved)
    digest = strict_json.bundle_digest(snap)
    return compiler.compile_launch(
        docs=bundle.docs,
        prompt_bodies=bundle.prompt_bodies,
        resolved=resolved,
        session_action=action,
        passthrough=["--verbose"],
        settings_path=SETTINGS_PATH,
        lead_prompt_path=compiler.lead_prompt_path(
            Path("/state"), digest, FIXED_SESSION
        ),
        durable=durable,
        scope_dir=SCOPE_DIR if durable else None,
    )


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"wrote {path.relative_to(CATALOG_ROOT)}")


def main() -> int:
    bundle = catalog.load_catalog(CATALOG_ROOT)
    resolved = composition.resolve(bundle.docs, bundle.default_composition)

    # Legacy parity argv/env/agents (byte-identical to the pre-rethink form).
    fresh_legacy = _compile(durable=False, action=compiler.build_fresh(FIXED_SESSION))
    resume_legacy = _compile(durable=False, action=compiler.build_resume(FIXED_SESSION))
    _write(GOLDENS / "argv-fresh.json", strict_json.canonical_file_bytes(fresh_legacy.argv))
    _write(GOLDENS / "argv-resume.json", strict_json.canonical_file_bytes(resume_legacy.argv))
    _write(
        GOLDENS / "env.json",
        strict_json.canonical_file_bytes(
            {"set": fresh_legacy.env_set, "unset": list(fresh_legacy.env_unset)}
        ),
    )
    _write(
        GOLDENS / "agents-contingency.json",
        strict_json.canonical_bytes(
            strict_json.loads(fresh_legacy.agents_json.encode("utf-8"))
        ),
    )
    appendix = compiler.generate_lead_appendix(
        resolved,
        bundle.docs["providers"]["providers"],
        session_id=FIXED_SESSION,
        composition_name="default",
    )
    _write(GOLDENS / "lead-appendix.md", appendix.encode("utf-8"))

    # Durable argv plus the compiled scope tree.
    fresh_durable = _compile(durable=True, action=compiler.build_fresh(FIXED_SESSION))
    resume_durable = _compile(durable=True, action=compiler.build_resume(FIXED_SESSION))
    _write(
        GOLDENS / "argv-fresh-durable.json",
        strict_json.canonical_file_bytes(fresh_durable.argv),
    )
    _write(
        GOLDENS / "argv-resume-durable.json",
        strict_json.canonical_file_bytes(resume_durable.argv),
    )
    plan = fresh_durable.scope_plan
    scope_goldens = GOLDENS / "scope"
    _write(
        scope_goldens / "settings.json",
        strict_json.canonical_file_bytes(plan.settings),
    )
    for relpath, data in sorted(plan.agent_files.items()):
        _write(scope_goldens.joinpath(*relpath.split("/")), data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
