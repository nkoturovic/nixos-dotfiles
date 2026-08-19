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
health    gateway ok · pin 2.1.218
update    Claude 2.1.219 available · pinned 2.1.218 · press U to update

Status  Ready
Enter launch · E edit · Tab preset · W wf · D details · S sessions · G gateway models · H health · ? help · U update · Esc cancel
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
  status. Exact `cm-*` collision blocks launch with the path named. Renders
  only when there are project agents or a collision to report (2.16.0).
- **Policy line**: one compact sentence, durable-by-settings.

## 1.5 Gateway picker (G, 2.10.0; providers pane P, 2.13.0)

**G gateway models** (shared keybar tail, available on every card state) opens
the ordinary-session launch picker — the TUI twin of `claude-gateway`:

```
gateway session — no composition
────────────────────────────────────────────────────────────
plain Claude through the local gateway · native /model within a group · no roster, no workflow pins

large · 1M context · /model switches freely within this group
  fable — Fable 5 · 1M selector · anthropic
  glm52 — GLM-5.2 · alibaba  (no secret)
  kimi-k3 — Kimi K3 · 1M selector · moonshot
  opus — Opus 4.8 · 1M selector · anthropic
  opus5 — Opus 5 · 1M selector · anthropic
  qwen38 — Qwen3.8 Max · alibaba  (no secret)
  sol — GPT-5.6 Sol · 1M selector · openai

grok · 500K context · /model switches freely within this group
  grok46 — Grok 4.6 · x-ai  (no secret)

in-session: /model claude-multi-grok46-high · /model claude-multi-grok46-xhigh
(catalog19 is active at HM generation 129; Grok 4.5 no longer appears)

in-session: /model gpt-multi-sol-high[1m] · /model gpt-multi-sol-xhigh[1m]
Enter launch · P providers · ? help · Esc back
```

2.13.0 additions (D50): the selected row's detail line shows the exact typed
`/model` selectors (the native picker display-filters custom aliases — typed
selectors hit the allow-list, issue 009), and **P** opens the providers pane:

```
providers — local status
────────────────────────────────────────────────────────────
served = registered by the running local gateway; upstream auth, quota, and reachability stay unknown

> Anthropic · OAuth pool · 1 credential record
    selectors 6 rendered · 6/6 served · sign in: `claude-multi-proxy claude-login`
  OpenAI · OAuth pool · no credential record
    selectors 5 rendered · unknown · sign in: `claude-multi-proxy codex-device-login`
  Kimi · direct key · KIMI_CLAUDE_API_KEY present
    selectors 1 rendered · 1/1 served · set KIMI_CLAUDE_API_KEY (masked) with Enter
  Qwen · direct key · QWEN_CLAUDE_API_KEY missing
    selectors 0 rendered · unknown · set QWEN_CLAUDE_API_KEY (masked) with Enter

Enter setup · R refresh · Esc back
```

Enter on a direct provider opens masked key entry (value never echoed;
written to the standard `~/.config/secrets/claude.env`, 0600 atomic,
parse-preserving; the confirmation shows name + length only). Enter on an
OAuth pool shows the exact `claude-multi-proxy` login command. The pane never
auto-runs init/restart and never claims "connected": present/rendered/served
are separate facts.

Honesty semantics (D46):

- **Groups are the /model fence.** Sections are ordinary context profiles;
  the launched session's native `/model` allow-list is its group. Display
  nuance (2.1.220, verified against the binary): the picker keeps the
  Default row (the pinned lead/route), allow-list-permitted built-in
  Anthropic rows (e.g. Fable 5), and the current model (always appended)
  — custom selectors (Kimi, Qwen, GLM) get no row of their own but stay
  switchable by typing the selector (`/model claude-multi-kimi-k3[1m]`,
  binary-verified) or via T/relaunch.
  Rows are models, not lanes — lanes switch in-session; the launch uses the
  model's default selector (cursor starts on `sol`, the CLI default).
- **`(no secret)` is render-time availability, not a live guarantee.** The
  gateway omits providers rendered without their secret; the row dims and
  the detail line spells out the exact `missing required secret env:X`
  reason for the selected row. Enter **rechecks the secret file** (never a
  cached verdict) and asks for explicit confirmation (default Cancel)
  before launching anyway — the running gateway may legitimately still
  serve an older config. The marking speaks for the initial model only.
- **Rows come from the compiler's accepted ordinary-lead domain**
  (`direct_context_profile`): agent-only or profile-less models never
  appear, so a row can never fail validation after Enter.
- Line mode parity: `g` prints the same grouped listing + the
  `claude-gateway --model <model>` hint; the CLI `direct` path warns
  (non-blocking) before a launch whose provider secret is missing, and
  never on `--print-launch`.

## 2. Editor

The editor is ONE form-based curses editor (`tui.py` widgets): labeled
sections, role→model/lane SelectLists (Space toggle, P prefer), workflows
checkbox, native-agent policy checkbox group, name/description TextInputs,
save/cancel Modals, availability-invalidation confirm Modal. `Ctrl+G` opens
the raw composition JSON in `$EDITOR` (the only external-editor integration;
validated on reload). `Ctrl+O` opens the Save-or-launch menu from anywhere
(2.13.0; text rows keep every printable key, so the save chord is a control
key — ^S was rejected as IXON/XOFF-hazardous). `TERM=dumb`/no-curses prints
the plan read-only plus the exact `$EDITOR` command — no second interactive
implementation.

## 2.1 Key and layout conventions (2.4.0)

- **Esc is the universal exit/back key on every curses screen** — card,
  sessions, editor, transition, modals, choosers. `Q` is never an exit key:
  text inputs must be free to type it, so a "sometimes-exits" key is a trap
  (the editor proved it). Line-mode flows accept the words `q`/`quit`/
  `cancel` because line input is a word modality, not a key modality.
- **Uniform left padding:** every screen's content starts at column 2
  (title, tables, forms, status rows, keybars) — one visual margin.
- **Keybars wrap upward** (to the row above) instead of clipping keys —
  the exit binding is always fully visible. A bar may occupy up to 2 rows;
  screens reserve `KeyBar.rows(width)` above the bar so the message row is
  never overdrawn (2.6.0, after the sessions keybar grew the X binding).
  **Past 2 rows (2.6.2):** middle bindings compact behind an `…` ellipsis
  on the top row and the exit binding always gets the bottom row — the
  "exit never clips" contract holds at any width.
- **Bottom-zone reservation is computed, never assumed (2.6.2):** screens
  with a Status/message block above the keybar reserve it up front
  (`bottom = height - bar_rows`); optional rows drop first at small
  sizes, and the Status/BLOCKED badge plus the first error line survive
  down to the floor. The sessions screen has a hard minimum-size floor
  (below 14 rows / 44 cols it shows a resize note instead of overdrawing).
- **Health surface on the card (2.4.1):** one loopback gateway check per
  card open (never per redraw) drives the `health` strip (gateway status +
  pin state, with an operator-override marker); when a newer Claude is
  installed than the pin, an `update` badge appears with the **U** action
  (the whole evidence-gated re-pin in place), and **H** runs doctor in
  place with an optional repair-all prompt. Line mode shows the update
  line and a `u` command. Long actions narrate themselves (2.6.0): the U
  flow releases curses first, then prints one line per phase as it happens
  (inspect → evidence suite with a duration note → override → optional
  activation) — a silent frozen card always means something is wrong.
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
● ⚠ a1b2…  cm:kimi-sol   durable(g2)  /repo/dotfiles   last used 2h ago · created 3d ago   [x] resolve fork [f]orget
○ 9c8e…  cm:kimi-sol   legacy       /repo/other      last used 4d ago · created 4d ago   [r]esume (upgrades) [f]orget
○ 7f3a…  linked        legacy       (native session, adopted) [r]esume [f]orget
```

Managed rows sort by **last used** (hooks keep it current); last used is
always shown, created appears when width permits (2.8.4).

- Row markers (2.6.0): **●** live — the session is owned by the background
  daemon right now (reattaching from a Claude menu forks natively); **⚠**
  fork-blocked — a native fork awaits an adopt/discard decision; **!**
  (2.8.0) — identity repair needed (R shows the repair popup). Native
  rows may show `(fork of <parent>)`.
- `resume` runs the **resume gate** (2.8.0): action-needed states show a
  Modal instead of a later native error — repair-needed → **Repair &
  resume** (runs the exact relink, keeps the recorded dir) / Cancel; ●
  live → **Stop & resume** / **Resume anyway** (heuristic escape) /
  Cancel; transcript missing → restore-or-forget guidance / Cancel;
  recorded project dir gone (2.16.0) → rename-back or move-transcript +
  relink guidance / Cancel. Text
  mode prints the exact commands; `-r --force` bypasses only the ●
  branch.
- `transition` opens the semantic diff view (TRANSITIONS §3) and asks for
  explicit confirmation that **the target process has exited** before
  anything is mutated; from inside the target session it prints the diff and
  the exact post-exit command instead.
- `resume` on legacy records states the one-time upgrade plainly; on a
  fork-blocked record it explains the block (resolve first) instead of
  failing later.
- `resolve fork` (X, 2.6.0): clears an already-resolved marker instantly,
  or asks once before discarding a genuinely pending marker — the fork
  transcript is always kept; the full commands are one `sessions show` away.
- `end session` (E, 2.7.0): shown on ● live rows — stops the background
  process with upstream `claude stop` (one confirm; conversation always
  kept; R resumes later). Self-stops and non-live rows are refused with a
  message. E was chosen because K collides with the screen's vim
  `k`=navigate-up.
- `forget` states exactly what is deleted (record + generated scope) and what
  is never touched (transcripts). A corrupt record forgets load-free (2.16.0);
  live rows get stop-first (E) guidance instead of the modal, and both the
  picker and the CLI re-check liveness under the lifecycle lock — a live
  session is never forgotten under a running process.
- The native section shows the newest 20 unmanaged sessions; beyond the cap
  the header says `+N more (newest 20 shown)` (2.16.0).

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
