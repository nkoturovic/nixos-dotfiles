# claude-multi — user guide

**What it is:** a small launcher that starts Claude Code sessions with a
chosen **composition** — one lead model plus a team of generated helper
agents (`cm-*`) that can use *other* models — and keeps that setup **durable**:
your agent team survives restarts, upgrades, and background takeovers because
it lives in per-session files instead of a one-time command line.

**What it is not:** it never changes plain `claude`. Your normal Claude Code
install, settings, and transcripts stay exactly as they were. For how plain
Claude Code itself works here (auto-updates, the background daemon, native
forks, auth) — and when to use which tool — see
[`STANDALONE.md`](STANDALONE.md).

## The three tools

| Command | What it does |
| --- | --- |
| `claude-multi` | Start/resume a **managed composition** session (lead + `cm-*` team) |
| `claude-gateway` | Start/resume an **ordinary** session through the local model gateway — normal Claude Code, native `/model`, no agent team |
| `claude-multi direct` | Same as `claude-gateway`, explicit form |
| plain `claude` | Upstream Claude Code, untouched by all of this — see [`STANDALONE.md`](STANDALONE.md) |

## Daily use

### Start a session

```bash
claude-multi
```

A card shows the composition (lead, team, policy, context). Then:

- **Enter** — launch
- **Tab / P** — cycle presets (`default` (Opus 5 + Sol + Kimi), `opus-sol` (Opus 5 + Sol), `opus-kimi` (Opus 5 + Kimi), `fable`, `kimi-sol`, `qwen-sol`, `sol-direct`)
- **W** — toggle workflows on/off
- **E** — edit the composition (form editor; `?` explains each field)
- **S** — open the sessions picker
- **H** — health: doctor in place (with an optional repair-all prompt)
- **U** — shown only when a Claude update is available; runs the whole
  evidence-gated update in place (see "Claude version updates" below)
- **Esc** — cancel (the universal exit, everywhere)

The card also shows a **health strip**: gateway status and the pinned Claude
version (marked when an operator override is in effect), and — when a newer
Claude is installed than the pin — an **update badge** naming the version
and the U key. That badge is the update notification: you will see it the
next time you open the launcher after an upstream Claude update.

Non-interactive (scripts): `claude-multi --composition kimi-sol`.

### The composition profiles

`default` is the only built-in trusted seed; the rest are the named profiles
on this machine (all creatable in seconds with `compose new` /
`use-as-template` — see "Managing compositions"):

| Preset | Lead | Subagents | Use it for |
| --- | --- | --- | --- |
| `default` (built-in) | **Opus 5** | Sol preferred · Kimi alternates · opus5 reviewer | everyday flagship work |
| `opus-sol` | **Opus 5** | Sol only | the clean Opus+Sol pair |
| `opus-kimi` | **Opus 5** | Kimi K3 only | Opus lead with Kimi agents |
| `fable` | Fable 5 | Sol preferred · Kimi alternates · opus reviewer | the previous default |
| `kimi-sol` | Kimi K3 | Sol preferred · Kimi alternates | Kimi 1M lead work |
| `qwen-sol` | Qwen3.8 Max (preview) | Sol preferred · Kimi alternates | Qwen lead work |
| `sol-direct` | GPT 5.6 Sol | — | single-model direct sessions |

Every profile keeps workflows native, worktree isolation on implementers,
cross-provider subagent preference, the model fence, and the compaction pin.
Cycle them with **Tab** on the card, or pick one directly with
`claude-multi --composition <name>`.

### Resume

```bash
claude-multi -c                 # continue the last session in this directory
claude-multi -r <uuid>          # resume an exact session
claude-multi -r cm:kimi-sol     # resume by composition name (if unique)
claude-multi -r                 # open the picker (managed + native sessions)
```

Resume re-opens the **same transcript** with the **same composition** — the
recorded intent is re-compiled fresh, so repairs and catalog updates apply
automatically.

**The resume gate (2.8.0).** Every resume is checked before launch, and the
TUI shows a popup when something needs a decision — never a dead-end native
error:

- **Identity repair needed** (`!` row marker, from a conflicting
  resume-CWD observation): offers **Repair & resume** (one keypress runs
  the exact `relink-runtime` repair, keeping the recorded project dir)
  or Cancel. In text mode you get the full copy-paste command. (A
  model-only observation is different: the launcher reconciles it on the
  next resume through the recorded model — no gate popup needed.)
- **Live in the background** (`●` row marker): resuming a daemon-owned
  session natively forks it — the incident shape. Offers **Stop & resume**
  (stops it through upstream's own `claude stop`, then resumes), **Resume
  anyway** (the marker is a heuristic; choose this if you know it is stale),
  or **Cancel**. Text mode prints the exact stop command;
  `claude-multi -r <uuid> --force` bypasses only this check.
- **Transcript missing**: resume can never work without the file — the gate
  says so and offers the way out (restore a backup, or
  `claude-multi sessions forget <uuid>`; transcripts are never deleted by
  claude-multi). Found under a different project dir → the exact
  `relink-runtime --cwd` command for the intentional re-home case.

### The sessions screen (`claude-multi -r` or **S**)

Managed sessions on top, plain-Claude (native) sessions below. Rows are
**sorted by last used** (hooks keep it current) so recent work floats to
the top. The *last used* age is always shown; *created* appears too when
the terminal is wide enough — `sessions show` has every exact timestamp.
Keys:

- **R** resume · **T** switch composition · **X** resolve fork ·
  **E** end session (stop a live ● one) · **F** forget · **L** adopt a native
  session · **C** filter to this directory · **?** help · **Esc** quit

Row markers: **●** — the session is live right now, owned by the background
daemon (reattaching to it from a Claude menu forks natively; exit it first or
resume after it exits) · **⚠** — fork-blocked: a native fork awaits your
adopt/discard decision (press **X**; resume is blocked until then) · **!** —
identity repair needed (press **R** for the repair popup; the exact command is
in `sessions show`). Native rows marked `(fork)` are forks of a managed
session.

**Ending a live session safely:** **E** on a ● row (or `claude-multi sessions
stop <uuid> [--yes]`) stops the background process with upstream's own
`claude stop <id>` — never a signal, never the transcript; the conversation is
always kept and **R** resumes it later. Self-stops (the session you're inside)
and non-live sessions are refused with a message. A transition on a ● live
session warns and names this command.

### Native forks (what they are and how to resolve them)

Claude Code **forks a session natively** when a second process wants a session
that is already owned — the common case: your session got backgrounded (the
supervisor daemon adopts it), and you re-enter it from the `claude agents`
menu. The fork is a new session UUID with a copy of the transcript; nothing is
deleted either way.

claude-multi sees the fork through its lifecycle hooks and **blocks resume of
the parent until you decide** — the two branches could diverge, and guessing
would be worse. You always get the fork UUID and the exact commands (the card,
`sessions show <uuid>`, and doctor all print them):

- **Keep the fork** as its own session: `claude-multi sessions link <fork-uuid>
  --composition NAME` (or **L** on its native row). The parent's marker clears
  automatically.
- **Discard the marker** (you'll never use the fork):
  `claude-multi sessions resolve-fork <parent-uuid> <fork-uuid>`, or press
  **X** on the parent in the sessions screen. The fork transcript stays on
  disk, adoptable later.
- Either decision **advances the parent's launch epoch**, which retires the
  fork's hook credential — a fork left running after adopt/discard can no
  longer claim the parent's identity. (Side effect of the same rule: a
  parent app still running while you resolve its fork reconciles again on
  its next launcher resume — nothing to do, nothing lost.)
- **No decision needed** when the fork already *is* the live branch (the
  daemon relaunched it and authority followed): the marker self-clears on
  the next hook, a resume, `doctor --repair-all`, or **X**.

One structural fix makes forks far less painful: managed settings now carry
the non-secret gateway base URL plus an `apiKeyHelper` shim, so a
daemon-relaunched session keeps its gateway routing instead of failing with
`invalid model: claude-multi-…` (the daemon scrubs `ANTHROPIC_*` from its
children's environment; the token itself is still never written to any file).

### Change a session's composition (transition)

```bash
claude-multi sessions transition <uuid> --composition qwen-sol
```

Shows a semantic diff (what changes), asks you to confirm the session has
**exited**, then relaunches with the exact same transcript under the new
composition. Model/agent/effort/workflow changes all go through this — the
managed `/model` menu is deliberately roster-shaped (lead + your agent
team's models, lead pinned as Default); a manual switch away from the lead
is flagged by the identity machinery with the exact relaunch guidance.

### Ordinary gateway sessions

```bash
claude-gateway                       # ordinary session, default model (sol)
claude-gateway --model qwen38        # pick a model
claude-gateway -c                    # continue
claude-gateway -r <uuid>             # resume
claude-gateway -r <uuid> --model sol # explicit cross-profile relaunch
```

Native `/model` works inside one safe context profile; switching profiles is
an explicit relaunch (a note reminds you the old process must have exited).

### Adopt an existing plain-Claude session

```bash
claude-multi sessions link <uuid> --composition kimi-sol
# or press L on it in the sessions screen
```

The session becomes managed (new stable ID; transcript untouched).

### Managing compositions

The presets cover the common shapes; your own compositions live in
`~/.config/claude-multi/compositions/` (owner-only files):

```bash
claude-multi compose list                       # your compositions + trusted seeds
claude-multi compose show <name>                # effective summary (lead, team, policy)
claude-multi compose edit <name>                # the form editor (same as E on the card)
claude-multi compose new <name>                 # new composition from the default shape
claude-multi compose duplicate <src> <dst>      # copy under a new name
claude-multi compose use-as-template <src> <dst># same idea, explicitly as a starting point
claude-multi compose rename <src> <dst>
claude-multi compose delete <name>
claude-multi compose restore-default            # reset 'default' to the trusted seed
```

The editor edits slots (lead, per-role model/lane/preferred), workflow
mode, and native-agent policy, with `?` explaining each field. Compositions
are schema-validated at save and fail closed at resolve (an unknown model,
lane, or role is an error, never a silent default). The shipped presets
also follow the house conventions — workflows native, worktree isolation
on implementers, cross-provider subagent preference, the model fence, the
compaction pin — and `new`/`use-as-template` start you from them.

### Housekeeping

```bash
claude-multi doctor              # health: Ready, Attention (lazy upgrades), or BLOCKED (real damage)
claude-multi doctor --repair-all # converge every session's files to its record (the older-session answer)
claude-multi doctor --prune      # sweep stale generated files (never transcripts)
claude-multi sessions forget <uuid>  # delete a session's record + generated files (never the transcript)
```

`Attention` lines always name the exact fix command. `BLOCKED` means
something is actually broken and says what.

### Supported vs not-recommended vs never

The tool is fail-proof by design: the main flows can't be broken by usage.
Some side doors exist on purpose — they work, are **loudly not recommended**,
and are never the default:

| Level | What | Why |
| --- | --- | --- |
| **Supported** | everything in this guide: launch/resume/transition, adopt, doctor repairs, update, TUI keys | tested, guarded, recoverable |
| **Not recommended** | plain `claude --resume <uuid>` of a managed session | works (durable scope loads), but skips binary verification + record guards |
| **Not recommended** | `--legacy` launch flag | restores the pre-durable argv behavior whose fragility caused the original incident; a compatibility hatch, not a fallback |
| **Not recommended** | reattaching to a **●** (background-owned) session from `claude agents` | native behavior forks the session; resolvable (X / resolve-fork) but avoidable — exit it first |
| **Never** | editing generated scope files by hand | regenerated from the record on every converge/repair; edits vanish |
| **Never** | deleting anything under `~/.claude` to "fix" a session | transcripts are sacred; every launcher repair is metadata-only |

### Claude version updates (how pinning works)

Claude Code **auto-updates itself** (the built-in updater downloads new
versions and moves the `~/.local/bin/claude` symlink — this happens on its
own, even while sessions run; running processes keep their start-time
binary, and the shared supervisor adopts the new version for new and
backgrounded sessions). That is upstream's channel and it stays **on** —
plain `claude` tracks upstream, which is what you want.

claude-multi deliberately does **not** auto-follow: it launches the
hash-verified pinned binary so managed sessions are always correct. The
loop is fully automatic except one command (or one key):

1. The launcher card shows the **update badge**, and `claude-multi doctor`
   shows an **Attention** line, when a newer Claude is installed than the pin.
2. Press **U** on the card (or run **`claude-multi update`**). It inspects
   the new binary offline, runs the full offline test suite (including the
   real-binary probes) against it, and writes an operator contract
   override — **effective immediately, no rebuild, no restart, no Home
   Manager switch needed.** Every phase prints as it happens (the evidence
   suite takes about two minutes — the command tells you so; it is not
   frozen). Concurrent runs serialize through a lock, so pressing U in two
   terminals queues instead of racing.
3. The source checkout is promoted in the same run, so the packaged
   baseline lands at the next natural activation (or right away with
   `claude-multi update --activate`, which runs `home-manager switch`).

Already-running sessions keep their start-time binary (normal for any
process); new launches pick up the pin at once. The background daemon
follows upstream's own channel, so a re-pin also keeps claude-multi
binary-consistent with sessions the daemon adopted.

That's it: upstream updates itself, the card flags it, one keypress
re-pins with evidence. Managed sessions never break across upgrades, and
the rollback point is the previous Home Manager generation plus your
transcripts (always untouched).

## Supported use cases

1. **Multi-model delegation** — an Opus 5/Kimi/Qwen/Fable lead with Sol/GPT analysts,
   implementers, and independent reviewers (`kimi-sol`, `qwen-sol`, `default`
   presets). Cross-family review is enforced by the generated rules.
2. **Durable agent teams** — agent definitions are per-session files, so they
   survive Claude restarts, version upgrades, and supervisor takeovers (the
   original failure this project exists to fix).
3. **Safe single-model sessions** — `claude-gateway` gives ordinary Claude
   Code a gateway transport and a context-safe `/model` profile without any
   composition machinery.
4. **Mid-session composition changes** — transition keeps the transcript,
   swaps the team (diff first, exited-confirm, exact resume).
5. **Bringing existing sessions under management** — adopt native sessions
   from the picker; repair identity drift with `sessions relink-runtime`.
6. **Session hygiene at scale** — `doctor` shows every session's state;
   `--repair-all` converges all of them in one pass; `--prune` and `forget`
   clean up (never transcripts).

## FAQ / troubleshooting

- **Which profile?** — `default` (Opus 5 + Sol + Kimi) for most work;
  `opus-sol` when you want the clean Opus+Sol pair; `opus-kimi` for Opus
  lead with Kimi agents; `kimi-sol`/`qwen-sol` when you need a Kimi or Qwen
  1M lead; `fable` is the previous default, kept around; `sol-direct` is
  one model, no team.
- **Why does `/model` show only the lead plus the roster models in my
  managed session?** — the pool is deliberately roster-shaped: the lead
  (pinned as Default) plus exactly the models your agent team uses, so
  subagent dispatch always resolves correctly. Switching the lead
  mid-session is still not the supported path (compaction thresholds and
  identity tracking are lead-shaped) — the session will flag it as an
  identity mismatch with the exact relaunch guidance; the supported model
  change is `sessions transition`. For model-flexible sessions use
  `claude-gateway`, whose `/model` menu offers every model inside one
  safe context profile.
- **A session forked when I came back to it — why?** — it was backgrounded
  and the supervisor daemon adopted it; reattaching from a menu forks
  natively. See "Native forks" above. The **●** marker in the sessions
  screen shows which sessions are background-owned right now.
- **Can I resume a managed session with plain `claude --resume`?** — it
  works (the durable scope's settings, agents, hooks, and gateway routing
  all load), but it's **not recommended**: you bypass the binary
  verification, record guards, and repair checks the launcher applies.
  Use `claude-multi -r`.
- **`doctor` says BLOCKED** — read the lines: each names the session and the
  fix (usually `claude-multi doctor --repair-all`). `Attention` is not damage.
- **"`contract override is invalid and was IGNORED"`** — the operator pin file
  (`~/.config/claude-multi/native-contract.json`) is corrupt; it is never
  applied (the packaged baseline is in effect), and one command heals it:
  `claude-multi update` (removes the broken file; re-run U afterwards if a
  newer Claude is still waiting).
- **Opus 4.8 vs Opus 5** — Opus 5 is the default lead and the canonical
  Opus default (`ANTHROPIC_DEFAULT_OPUS_MODEL`). Opus 4.8 stays in the
  catalog for existing sessions and Anthropic's own safety fallback; no
  dedicated 4.8 profile exists on purpose (strictly inferior at equal price).
- **Resume says "session doesn't exist"** — Claude finds transcripts by their
  original directory; resume from the session's recorded cwd (the launcher
  does this for you; adopted sessions record it at link time).
- **"observed an unsafe model/profile change"** — the session ran under a
  different model than recorded (e.g. a `/model` change). Relaunch explicitly:
  `claude-gateway -r <uuid> --model <recorded>` or transition for managed.
- **Where are my transcripts?** — untouched in `~/.claude/projects/…`, always
  resumable with plain `claude --resume <runtime-uuid>` (`sessions show`
  prints the runtime UUID). Rollback never deletes them.
- **Compaction** — managed sessions pin auto-compaction on with documented
  per-model thresholds (Sol 316,800 · 1M process 882,000 · Qwen 867,254;
  proactive preparation may occur earlier).
- **What's running where** — managed/ordinary sessions route through the
  loopback gateway (`127.0.0.1:8317`, systemd user service `cli-proxy-api`);
  plain `claude` uses your normal Anthropic auth, untouched.
- **"API Error: An error occurred while processing"** — that text is the
  *upstream's* 500 body, shown after the client's retries run out (the
  pinned client retries transient errors 10× by itself). Since 2.8.0,
  managed sessions pin watchdog retry mode (`CLAUDE_CODE_RETRY_WATCHDOG`):
  up to 300 transient retries with per-wait backoff capped at 5 minutes
  (no total wall-clock cap — a session can keep recovering for a long
  while; interrupt it with Esc if you'd rather give up). What remains
  unrecoverable: mid-stream failures after content started — no client
  or gateway retries those at this pin; the lead continues a dead
  subagent by messaging it (its context survives). Ordinary
  `claude-gateway` sessions are unpinned — export the same variable
  yourself if you want it there.
- **A subagent reported `Cannot enter worktree … is the repository root`** —
  a reviewer/analyst tried to *enter* an implementer's worktree with the
  native worktree tool, which the pinned Claude refuses from a
  repository-root session. Since 2.7.2 the roster prompts steer agents to
  the outside-in pattern instead: read-mostly agents inspect worktrees
  without entering them (direct file reads, `git -C <path> diff`, running
  checks in a subshell), and implementers report their worktree
  path/branch/base ref so the next leg can find them. On an older
  session, simply resuming it regenerates the prompts from the installed
  catalog (`doctor --repair-all` covers sessions you don't resume).
  Nothing is lost — the error is annoying, not corrupting.
- **Rollback** — switch to the previous Home Manager generation; old launchers
  fail closed on schema-v3 records, and transcripts always stay recoverable.

## Files you might look at

```text
~/.local/state/claude-multi/      sessions, scopes (generated), pointers, locks
                                  bin/claude-multi-hook (lifecycle shim)
                                  bin/claude-multi-gateway-token (apiKeyHelper shim)
~/.config/claude-multi/           your compositions, gateway config (secrets — keep private),
                                  native-contract.json (operator override from `update`)
~/.claude/projects/…              transcripts (Claude's own; never touched by the launcher)
```

Developer documentation (architecture, invariants, how to change things):
[`AGENTS.md`](AGENTS.md) and [`../../docs/claude-multi-final/`](../../docs/claude-multi-final/README.md).
