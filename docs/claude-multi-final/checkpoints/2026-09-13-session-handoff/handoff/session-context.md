# Session context — continuity across a context compaction

**Addendum.** The checkpoint convention specifies three files (`README.md`,
`state-snapshot.md`, `open-items.md`); this fourth file was added at the
operator's request to carry *conversation-level* context that compaction
would otherwise discard and that is not project state. The three core files
remain the canonical handoff.

---

## 1. Which session this is, and how to resume it

| Fact | Value |
|---|---|
| managed_id | `7fa62138-459d-4ab6-920c-cf418af34256` |
| record | `~/.local/state/claude-multi/sessions/7fa62138-459d-4ab6-920c-cf418af34256.json` |
| composition | `deepseek-flash` (managed-composition, durable) |
| lead | `deepseek-flash` · `claude-multi-deepseek-flash-high[1m]` · effort `ultracode` · 1M |
| cwd | `/home/kotur/.claude` |
| identity | `authoritative` · launch_epoch 29 · launcher_version 2.24.0 |

**Resume exactly:**

```bash
claude-multi -r 7fa62138-459d-4ab6-920c-cf418af34256     # explicit UUID
claude-multi -c                                          # continue the last session in this cwd
```

The composition lives in the durable record, so no `--composition` flag is
needed on resume.

**Other launch forms**

```bash
claude-multi --composition astra-deepseek-flash      # fresh managed session (any of the 28 presets)
claude-multi direct --model deepseek-flash           # ordinary gateway session (no roster)
claude-multi direct -r <UUID>                        # resume an ordinary session
claude-multi --print-launch                          # print argv/env without launching
```

## 2. Environment facts of this session

- The composition in use is a **cross-family rig**: `deepseek-flash` is the
  lead; the agent variants are also deepseek-family, so reviews are
  **same-family (reduced independence)** — as recorded in the generated
  appendix. There is no cross-family reviewer enabled here.
- A **session goal hook** ("Complete pending claude-multi / claude work") was
  active for part of this session and was **cleared by the operator** at the
  end. Do not re-establish it by default.
- **Ultracode** was on: substantive tasks were run through multi-agent
  `Workflow` fan-outs. Two such runs produced the material for this handoff
  (12 gatherers + 1 synthesis; then a 3-agent gap check).
- The operator has taken over **testing** — do not re-run the full suite
  speculatively; run it when a change requires it.

## 3. Rollback

| Target | Command |
|---|---|
| Home Manager gen 139 (current) | `home-manager generations \| head -3` to list |
| Previous generation (138) | `/nix/store/p5s4yiyk23m1h3m9949qd94qlqfj3rwv-home-manager-generation/activate` |

Per-release anchors: gen 135 (D63), 136 (WS4/D64), 138 (D65), 139 (catalog26).
Rollback never deletes state; transcripts are never touched.

## 4. Operator working preferences observed this session

Worth carrying forward — these shaped the work and are not recorded in the repo:

- **Prefer short, bounded tasks.** The operator explicitly interrupted a
  long-running line of work with *"Don't perform some long extensive tasks,
  basic probing is fine (if needed)"*, and later *"don't over-do the testing"*.
- **They will take over work themselves** when they want to run it — as with
  testing. Hand over cleanly rather than pushing on.
- **Rejected tool calls are deliberate.** A diagnostic PTY run and an
  `AskUserQuestion` prompt were both rejected mid-session; the correct response
  was to stop that line, not to retry it in a different form.
- **Prefer the simplest correct solution** — stated earlier as *"Make sure to
  chose what makes most sense, and not overthink it"*, and repeatedly shown by
  choosing one central mechanism (e.g. the D63 clamp) over many local edits.
- **Ask before touching the LAN host** (`bt-lab-02`) — it is another agent's
  work, stated explicitly.
- **Verify before claiming.** They consistently asked to "double check
  everything" and to distinguish measured facts from inferences.

## 5. What was in flight when the session compacted

- The handoff itself was just written, committed (`dee4948`) and pushed.
- **Nothing is half-applied**: the working tree is clean except the operator's
  own `home-manager/kotur.dotfiles/profile` edit; the re-pin gate left the
  Claude pin at 2.1.220 and restored the repo byte-exactly after each failed
  attempt; the live system is at gen 139, doctor Ready.
- The next actionable item is `open-items.md` §1 (the trust-seed lead for the
  re-pin) or §2 (`CLAUDE_CODE_SUBAGENT_MODEL_FORCE`, which needs no provider
  call at all).
