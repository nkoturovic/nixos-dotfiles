FABLE CONSERVATION MODE: The separate Fable weekly allowance is being conserved. `Agent(claude-multi:fable-specialist)` is denied in this session. Do not attempt to dispatch it.

Route exceptional long-horizon, high-complexity, or high-stakes work through the selected lead's integration:
- `claude-multi:kimi-analyst` for broad/deep analysis, architecture, security/critical/final review;
- `claude-multi:kimi-specialist` for broad or high-volume multi-file implementation;
- `claude-multi:sol-reasoner` for focused local reasoning after evidence;
- `claude-multi:sol-engineer` for bounded implementation;
- `claude-multi:gpt55-reviewer` for routine review or tie-breaking;
- `claude-multi:opus-reviewer` for a Claude-native independent check only when Kimi authored the change or the user explicitly requests Opus.

If a delegated agent fails, do not recover by dispatching `claude-multi:fable-specialist`. Inspect partial state, avoid duplicate writes or repeated failed dispatches, and let the selected lead reroute to an allowed agent or report the blocker.
