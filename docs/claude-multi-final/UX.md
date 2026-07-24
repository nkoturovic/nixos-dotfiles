# UX — startup TUI, sessions, Doctor, errors, runbook

Design intent: the TUI already has the right bones (quick-confirm + full
editor). The rethink adds **truthful visibility**: what is durable, what is
behavioral, what is a residual — without new screens where a line suffices.

## 1. Startup quick-confirm (no-argument TUI)

```
claude-multi — composition: default · Trusted default · Fresh
────────────────────────────────────────────────────────────
lead      Opus 5 · 1M selector · effort ultracode   workflows: native
agents    6 selected · 3 roles · durable scope (per-session files)
  role          model                   lane
  Analyst       GPT-5.6 Sol             high    ★ preferred
  Analyst       Kimi K3 · 1M selector   max
  Implementer   GPT-5.6 Sol             high    ★ preferred  (worktree)
  Implementer   Kimi K3 · 1M selector   max       (worktree)
  Reviewer      GPT-5.6 Sol             xhigh   ★ preferred
  Reviewer      Opus 5 · 1M selector    xhigh
policy    Explore→cm-analyst · Plan native · general-purpose off · generic denied
project   no project agents discovered (native precedence; none colliding)
health    gateway ok · pin 2.1.218
update    Claude 2.1.219 available · pinned 2.1.218 · press U to update

Status  Ready
Enter launch · E edit · Tab preset · W wf · D details · S sessions · ? help · U update · H health · Esc cancel
```

(The `update` row and the **U** binding appear only when a newer Claude is
installed than the pin; the `health` row always shows the gateway and pin
state from one loopback check per card open.)

Elements and their honesty semantics:

- **Durability badge** (`durable scope` vs `legacy argv`): tells the user
  which persistence class the session will have. New sessions always durable.
- **Workflow badge**: `workflows: native` or `off`. Help line (`?`) shows the
  weaker-guarantee panel (§3).
- **Project agents line**: visible participation (Q10) — count, and collision
  status. Exact `cm-*` collision blocks launch with the path named.
- **Policy line**: one compact sentence, durable-by-settings.

## 2. Editor

The editor is ONE form-based curses editor (`tui.py` widgets): labeled
sections, role→model/lane SelectLists (Space toggle, P prefer), workflows
checkbox, native-agent policy checkbox group, name/description TextInputs,
save/cancel Modals, availability-invalidation confirm Modal. `Ctrl+G` opens
the raw composition JSON in `$EDITOR` (the only external-editor integration;
validated on reload). `TERM=dumb`/no-curses prints the plan read-only plus
the exact `$EDITOR` command — no second interactive implementation.

## 2.1 Key and layout conventions (2.4.0)

- **Esc is the universal exit/back key on every curses screen** — card,
  sessions, editor, transition, modals, choosers. `Q` is never an exit key:
  text inputs must be free to type it, so a "sometimes-exits" key is a trap
  (the editor proved it). Line-mode flows accept the words `q`/`quit`/
  `cancel` because line input is a word modality, not a key modality.
- **Uniform left padding:** every screen's content starts at column 2
  (title, tables, forms, status rows, keybars) — one visual margin.
- **Keybars wrap upward** (to the row above) instead of clipping keys —
  the exit binding is always fully visible.
- **Health surface on the card (2.4.1):** one loopback gateway check per
  card open (never per redraw) drives the `health` strip (gateway status +
  pin state, with an operator-override marker); when a newer Claude is
  installed than the pin, an `update` badge appears with the **U** action
  (the whole evidence-gated re-pin in place), and **H** runs doctor in
  place with an optional repair-all prompt. Line mode shows the update
  line and a `u` command.
- Navigation: arrows everywhere; `k`/`j` only on the sessions screen (no
  text inputs there); `?` opens help everywhere; `^C` interrupts.

## 3. Workflow guarantee panel (shown on `?`, in Doctor, and in README)

```
Native workflows (ultracode): ON
  · workflow agents run as the SESSION model by default (lead family),
    always acceptEdits — scripts may route stages to other models, so
    unobserved workflow output is family-unknown/mixed and never counts
    as an independent review verdict
  · no cm-role contract, no worktree isolation, ≤16 concurrent / 1000 per run
  · cm-* selected agents keep their own model/effort/isolation contracts
```

`off` mode: `disableWorkflows:true` compiled; lead `ultracode`→`xhigh`;
keyword inert; `/deep-research` unavailable (documented consequences, WF
L327). The TUI never presents `off` as "safer subagents" — just different.

## 4. Sessions screen

```
sessions
──────────────────────────────────────────────
● a1b2…  cm:kimi-sol   durable(g2)  /repo/dotfiles   2h ago   [r]esume [t]ransition [f]orget
○ 9c8e…  cm:kimi-sol   legacy       /repo/other      3d ago   [r]esume (upgrades to durable) [f]orget
○ 7f3a…  linked        legacy       (native session, adopted) [r]esume (upgrades) [f]orget
```

- `transition` opens the semantic diff view (TRANSITIONS §3) and asks for
  explicit confirmation that **the target process has exited** before
  anything is mutated; from inside the target session it prints the diff and
  the exact post-exit command instead.
- `resume` on legacy records states the one-time upgrade plainly.
- `forget` states exactly what is deleted (record + generated scope) and what
  is never touched (transcripts).

## 5. Doctor

```
claude-multi doctor
binary      OK   2.1.217 verified (sha256 2630fc5d…)
gateway     OK   127.0.0.1:8317 /healthz 200
catalog     OK   v1 hash 9f3…  compositions: default, kimi-sol
sessions    2 recorded · 1 durable · 1 legacy
scope       a1b2… OK (6 files, gen 2) · 9c8e… legacy (no scope)
collisions  none (cwd project agents: 2, none cm-*)
evidence    add-dir carry: backgrounding documented · takeover binary-consistent, acceptance-pending(U1)
daemon      present (pid 1898189) — informational only
```

Fail-closed items keep today's exact-recovery style. `--repair <uuid>`
recompiles a scope from its record; `--prune` removes stale scopes (records
gone). No transcript paths are ever printed.

## 6. Error language (examples)

- Collision: `project agent 'cm-reviewer-sol-xhigh' at
  .claude/agents/cm-reviewer-sol-xhigh.md collides with the managed cm-*
  namespace. Rename or remove it, or launch from a different directory.`
- Takeover aftermath: `scope files for a1b2… are missing (3/6). Run
  claude-multi doctor --repair a1b2… to regenerate them from the record.`
- Legacy resume: `this session predates durable scopes; resuming will attach
  a scope and keep the transcript. Use --legacy to keep old argv behavior.`

## 7. Runbook (user-facing, mirrored in README)

1. `claude-multi` → Enter → session with durable agents.
2. After a Claude upgrade/restart: agents reappear automatically (files);
   `doctor` confirms scope integrity.
3. Change composition mid-session: `claude-multi sessions transition <uuid>`
   → review diff → relaunch resumes the same transcript.
4. Something odd: `claude-multi doctor`; scope repair; `--legacy` escape
   hatch; rollback = HM generation + `--legacy` records (MIGRATION-ROLLBACK).

## 8. Accessibility & TTY

Existing PTY/no-controlling-terminal/keyboard/cancellation behavior is
preserved (G0' work kept). New output stays line-based first; curses remains
optional. All badges have text forms (no color-only meaning).
