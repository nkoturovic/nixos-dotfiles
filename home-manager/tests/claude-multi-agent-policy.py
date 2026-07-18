#!/usr/bin/env python3
"""Validate the focused claude-multi delegation policy."""

import json
import re
from pathlib import Path


def check(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)


root = Path(__file__).resolve().parents[1]
agents = root / "claude-multi-plugin" / "agents"
launcher = (root / "kotur.bin" / "claude-multi.sh").read_text()
config_template = (root / "kotur.dotfiles" / "cli-proxy-api" / "config.template.yaml").read_text()
home_module = (root / "kotur.home.nix").read_text()
plugin_manifest = json.loads((root / "claude-multi-plugin" / ".claude-plugin" / "plugin.json").read_text())
prompt_path = root / "claude-multi-plugin" / "fable-conservation-prompt.md"
check(prompt_path.is_file(), "conservation prompt file packaged")
prompt = prompt_path.read_text()
check("FABLE CONSERVATION MODE" in prompt, "conservation mode definition")
check("`Agent(claude-multi:fable-specialist)` is denied" in prompt, "exact denied scoped name")
check("claude-multi:kimi-analyst" in prompt, "conservation routes to Kimi analyst")
check("claude-multi:kimi-specialist" in prompt, "conservation routes to Kimi specialist")
check("claude-multi:sol-reasoner" in prompt, "conservation routes to Sol reasoner")
check("claude-multi:sol-engineer" in prompt, "conservation routes to Sol engineer")
check("claude-multi:gpt55-reviewer" in prompt, "conservation routes to GPT reviewer")
check("claude-multi:opus-reviewer" in prompt, "conservation routes to Opus reviewer")
check("do not recover by dispatching `claude-multi:fable-specialist`" in prompt, "no Fable recovery after failure")
check("Inspect partial state" in prompt, "partial-state inspection guidance")
check("avoid duplicate writes" in prompt, "avoid duplicate writes guidance")
check("repeated failed dispatches" in prompt, "no repeated failed dispatches guidance")
check("reroute to an allowed agent" in prompt, "reroute to allowed agent guidance")
check(not (agents / "orchestrator-conserve.md").exists(), "no orchestrator-conserve agent file")
fable = (agents / "fable-specialist.md").read_text()
kimi_path = agents / "kimi-specialist.md"
check(kimi_path.is_file(), "Kimi specialist file")
kimi = kimi_path.read_text()
sol_explorer_path = agents / "sol-explorer.md"
check(sol_explorer_path.is_file(), "Sol explorer file")
sol_explorer = sol_explorer_path.read_text()
kimi_analyst_path = agents / "kimi-analyst.md"
check(kimi_analyst_path.is_file(), "Kimi analyst file")
kimi_analyst = kimi_analyst_path.read_text()
gpt55_reviewer = (agents / "gpt55-reviewer.md").read_text()
orchestrator = (agents / "orchestrator.md").read_text()
sol_reasoner = (agents / "sol-reasoner.md").read_text()
policies = {
    "Fable specialist": fable,
    "orchestrator": orchestrator,
}

expected_sol_aliases = {
    "claude-multi-sol-high",
    "gpt-multi-sol-high",
    "claude-multi-sol-xhigh",
    "gpt-multi-sol-xhigh",
}
configured_sol_aliases = set(
    re.findall(r'^\s+alias: "((?:claude|gpt)-multi-sol-[^"]+)"$', config_template, re.MULTILINE)
)
check(configured_sol_aliases == expected_sol_aliases, "configured Sol alias set")
sol_consumers = launcher + "\n" + "\n".join(path.read_text() for path in agents.glob("*.md"))
consumed_sol_aliases = set(re.findall(r"\b(?:claude|gpt)-multi-sol-[a-z0-9-]+\b", sol_consumers))
check(consumed_sol_aliases <= configured_sol_aliases, "launcher/plugin Sol aliases are configured")
check(plugin_manifest["version"] == "1.2.9", "active plugin manifest version")
check(f'claude-multi-plugin-{plugin_manifest["version"]}' in home_module, "plugin package version matches manifest")
check('CLAUDE_MULTI_VERSION="1.2.12"' in launcher, "launcher version remains 1.2.12")
check("fable-conservation-prompt.md" in launcher, "launcher references packaged conservation prompt")
check("CLAUDE_CODE_SUBAGENT_MODEL" not in launcher, "launcher has no global subagent model")
check("CLAUDE_CODE_SUBAGENT_MODEL" not in config_template, "proxy config has no global subagent model")

check("model: claude-fable-5[1m]" in fable, "canonical Fable specialist model")
check("effort: max" in fable, "Fable specialist effort")
check("claude-multi:fable-specialist" in orchestrator, "Fable specialist routing")
check("model: claude-multi-kimi-k3[1m]" in kimi, "Kimi specialist model")
check("effort: max" in kimi, "Kimi specialist effort")
check("isolation: worktree" in kimi, "Kimi specialist worktree isolation")
kimi_normalized = re.sub(r"\s+", " ", kimi.lower())
check(
    all(term in kimi_normalized for term in ("high-volume", "multi-file", "context-heavy", "implement")),
    "Kimi specialist implementation scope",
)
check("analyze only what is necessary to implement" in kimi_normalized, "Kimi specialist bounded implementation analysis")
check("do not take standalone analysis or review assignments" in kimi_normalized, "Kimi specialist rejects analysis/review ownership")
check(
    re.search(r"^description:.*\b(?:analysis|review)\b", kimi.lower(), re.MULTILINE) is None
    and "repository-wide analysis" not in kimi_normalized
    and "first-pass review" not in kimi_normalized,
    "Kimi specialist has no analysis or review role leakage",
)
check(
    all(term in kimi_normalized for term in ("worktree path", "changed files", "validation", "never commit or push")),
    "Kimi specialist reporting and repository safety",
)
check("claude-multi:kimi-specialist" in orchestrator, "exact Kimi specialist routing")
check("model: gpt-multi-sol-high" in sol_explorer, "Sol explorer model")
check("effort: high" in sol_explorer, "Sol explorer effort")
check("isolation:" not in sol_explorer, "Sol explorer uses current working tree")
check("model: claude-multi-kimi-k3[1m]" in kimi_analyst, "Kimi analyst model")
check("effort: max" in kimi_analyst, "Kimi analyst effort")
check("isolation:" not in kimi_analyst, "Kimi analyst uses current working tree")
for label, analyst in (("Sol explorer", sol_explorer), ("Kimi analyst", kimi_analyst)):
    analyst_normalized = re.sub(r"\s+", " ", analyst.lower())
    check("primarily read-only" in analyst_normalized, f"{label} is primarily read-only")
    check("do not edit implementation files" in analyst_normalized, f"{label} prohibits implementation-file edits")
    check("perform integration edits" in analyst_normalized, f"{label} prohibits integration edits")
    check("commit/push" in analyst_normalized, f"{label} prohibits commit/push")
    check(
        "bounded, isolated report artifact" in analyst_normalized
        and "explicitly asks" in analyst_normalized
        and "create only that artifact" in analyst_normalized
        and "otherwise do not edit files" in analyst_normalized,
        f"{label} allows an explicitly requested bounded report artifact and otherwise does not edit files",
    )
    check("shared total and concurrency budgets" in analyst_normalized, f"{label} shares delegation budgets")
    check("delegate distinct read-only subproblems" in analyst_normalized, f"{label} permits bounded read-only delegation")
check("model: gpt-multi-gpt55-high" in gpt55_reviewer, "GPT-5.5 reviewer model")
check("effort: high" in gpt55_reviewer, "GPT-5.5 reviewer effort")
check("routine or bounded" in gpt55_reviewer.lower(), "GPT-5.5 routine bounded review")
check("tie-breaker" in gpt55_reviewer.lower(), "GPT-5.5 tie-breaking")
check(
    "independent review of kimi-authored changes" in gpt55_reviewer.lower()
    and "kimi analyst" in gpt55_reviewer.lower()
    and "critical, security-sensitive, or final review" in gpt55_reviewer.lower(),
    "GPT-5.5 independence and Kimi deep-review default",
)
orchestrator_normalized = re.sub(r"\s+", " ", orchestrator.lower())
sol_reasoner_normalized = re.sub(r"\s+", " ", sol_reasoner.lower())
check("broad-context generalist" in orchestrator_normalized, "Kimi broad-generalist intent")
check("multi-file implementation" in orchestrator_normalized, "Kimi multi-file intent")
check("first-pass review" in orchestrator_normalized, "Kimi first-pass review intent")
check("independent parallel lane" in orchestrator_normalized, "Kimi independent parallel intent")
check('agenttype: "claude-multi:sol-explorer"' in orchestrator_normalized, "routine Dynamic lanes use exact Sol explorer routing")
check("claude-multi:kimi-analyst" in orchestrator, "exact Kimi analyst routing")
check("claude-multi:sol-reasoner" in orchestrator, "exact Sol reasoner routing")
check("claude-multi:gpt55-reviewer" in orchestrator, "exact GPT-5.5 reviewer routing")
check("routine substantive" in orchestrator_normalized and "explore or analyze" in orchestrator_normalized, "routine exploration distinction")
check("broad or deep repository analysis" in orchestrator_normalized, "deep analysis distinction")
check(
    "default for focused reasoning after evidence" in orchestrator_normalized
    and "not generic exploration" in orchestrator_normalized,
    "focused reasoning distinction",
)
check(
    all(term in orchestrator_normalized for term in ("local or bounded", "after evidence is gathered", "locally difficult")),
    "Sol routing stays focused, evidence-based, and task-fit",
)
check(
    all(term in sol_reasoner_normalized for term in ("focused", "local", "bounded", "after evidence is gathered")),
    "Sol reasoner scope is local or bounded after evidence",
)
boundary_terms = ("broad", "deep", "cross-cutting", "architecture-level", "security-sensitive", "high-context")
for label, routing_policy in (("orchestrator", orchestrator_normalized), ("Sol reasoner", sol_reasoner_normalized)):
    check(
        all(term in routing_policy for term in boundary_terms)
        and re.search(r"final[- ]judgment", routing_policy) is not None,
        f"{label} states the broad-reasoning boundary",
    )
check(
    all(term in sol_reasoner_normalized for term in ("report", "boundary", "lead"))
    and re.search(r"rather than expand\w*", sol_reasoner_normalized) is not None,
    "Sol reasoner reports scope expansion to the lead",
)
for stale_phrase in ("hard technical reasoning", "especially difficult questions"):
    check(
        stale_phrase not in orchestrator_normalized and stale_phrase not in sol_reasoner_normalized,
        f"stale Sol tier wording absent: {stale_phrase}",
    )
check("routine bounded review" in orchestrator_normalized, "routine review distinction")
check(
    "claude-multi:kimi-analyst as a broad-context generalist" in orchestrator_normalized
    and "normal default for broad or deep repository analysis" in orchestrator_normalized
    and "critical, security-sensitive, or final review" in orchestrator_normalized,
    "Kimi deep and final review default",
)
check(
    "if kimi authored the changes" in orchestrator_normalized
    and "never use kimi as the sole reviewer" in orchestrator_normalized
    and "gpt-5.5 or opus" in orchestrator_normalized,
    "Kimi-authored work receives independent cross-model review",
)
check("native explore and plan agents" in orchestrator_normalized, "native Explore and Plan preserved")
check(
    "bare inherited workers" in orchestrator_normalized
    and "same-model judgment materially helps" in orchestrator_normalized
    and "not an absolute prohibition" in orchestrator_normalized,
    "adaptive manual Fable worker routing preserved",
)
check("fable lead judgment and integration ownership" in orchestrator_normalized, "Fable lead ownership preserved")
check(
    "under the default fable profile" in orchestrator_normalized
    and "claude model remains lead, integrator, and final synthesizer" in orchestrator_normalized,
    "default Claude lead and final synthesis preserved",
)
check(
    "optional claude-native independent check" in orchestrator_normalized
    and "kimi authored the change" in orchestrator_normalized
    and "materially valuable second-model perspective" in orchestrator_normalized
    and "user explicitly requests opus" in orchestrator_normalized
    and "native fallback selects it" in orchestrator_normalized
    and "not the routine or default reviewer" in orchestrator_normalized,
    "Opus remains an optional task-fit independent check",
)
check("never set a global claude_code_subagent_model" in orchestrator_normalized, "global subagent model remains prohibited")
check(
    re.search(r"(?:do not|never) force.{0,40}kimi dispatch", orchestrator_normalized) is not None
    and re.search(r"(?:do not|never) duplicate.{0,50}(?:work|scope)", orchestrator_normalized) is not None,
    "Kimi dispatch is neither forced nor duplicated",
)
check(
    "adaptively by task fit" in orchestrator_normalized
    and "advisory rather than required counts" in orchestrator_normalized,
    "adaptive non-prescriptive scheduling",
)

for label, policy in policies.items():
    normalized = re.sub(r"\s+", " ", policy.lower())
    check(
        re.search(r"\bpermit\w* nested delegation\b", normalized) is not None,
        f"{label} permits nested delegation",
    )
    check(
        re.search(r"budgets? (?:count|include) all descendants", normalized) is not None,
        f"{label} budgets count descendants",
    )
    check(
        "each delegating parent" in normalized
        and "integration" in normalized
        and "validation" in normalized,
        f"{label} parent integration and validation ownership",
    )

check(
    all(value in fable for value in ("1–4", "1–3", "4–8", "9–12"))
    and re.search(r"12 (?:is|remains) a hard ceiling", fable) is not None,
    "Fable specialist finite total and concurrency budgets",
)

blanket_bans = (
    "recursively spawning agent trees",
    "no nested delegation",
    "nested delegation is prohibited",
    "do not delegate",
    "must not delegate",
    "never delegate",
    "leaf agents only",
)
for phrase in blanket_bans:
    check(
        all(phrase not in policy.lower() for policy in policies.values()),
        f"blanket delegation ban absent: {phrase}",
    )

check("disallowedtools" not in fable.lower(), "Fable specialist leaves Agent tools available")

print("PASS claude-multi Fable and Kimi specialist policy")
