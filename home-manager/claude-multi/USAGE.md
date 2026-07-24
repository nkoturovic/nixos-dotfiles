# claude-multi — user guide

**What it is:** a small launcher that starts Claude Code sessions with a
chosen **composition** — one lead model plus a team of generated helper
agents (`cm-*`) that can use *other* models — and keeps that setup **durable**:
your agent team survives restarts, upgrades, and background takeovers because
it lives in per-session files instead of a one-time command line.

**What it is not:** it never changes plain `claude`. Your normal Claude Code
install, settings, and transcripts stay exactly as they were.

## The three tools

| Command | What it does |
| --- | --- |
| `claude-multi` | Start/resume a **managed composition** session (lead + `cm-*` team) |
| `claude-gateway` | Start/resume an **ordinary** session through the local model gateway — normal Claude Code, native `/model`, no agent team |
| `claude-multi direct` | Same as `claude-gateway`, explicit form |

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

| Preset | Lead | Subagents | Use it for |
| --- | --- | --- | --- |
| `default` | **Opus 5** | Sol preferred · Kimi alternates · opus5 reviewer | everyday flagship work |
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

### The sessions screen (`claude-multi -r` or **S**)

Managed sessions on top, plain-Claude (native) sessions below. Keys:

- **R** resume · **T** switch composition · **F** forget · **L** adopt a
  native session · **C** filter to this directory · **?** help · **Esc** quit

### Change a session's composition (transition)

```bash
claude-multi sessions transition <uuid> --composition qwen-sol
```

Shows a semantic diff (what changes), asks you to confirm the session has
**exited**, then relaunches with the exact same transcript under the new
composition. Model/agent/effort/workflow changes all go through this — the
managed `/model` menu is fenced to the lead on purpose.

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

### Housekeeping

```bash
claude-multi doctor              # health: Ready, Attention (lazy upgrades), or BLOCKED (real damage)
claude-multi doctor --repair-all # converge every session's files to its record (the older-session answer)
claude-multi doctor --prune      # sweep stale generated files (never transcripts)
claude-multi sessions forget <uuid>  # delete a session's record + generated files (never the transcript)
```

`Attention` lines always name the exact fix command. `BLOCKED` means
something is actually broken and says what.

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
   Manager switch needed.**
3. The source checkout is promoted in the same run, so the packaged
   baseline lands at the next natural activation (or right away with
   `claude-multi update --activate`, which runs `home-manager switch`).

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
- **Opus 4.8 vs Opus 5** — Opus 5 is the default lead and the canonical
  Opus default (`ANTHROPIC_DEFAULT_OPUS_MODEL`). Opus 4.8 stays in the
  catalog for existing sessions and Anthropic's own safety fallback; no
  dedicated 4.8 profile exists on purpose (strictly inferior at equal price).
- **`doctor` says BLOCKED** — read the lines: each names the session and the
  fix (usually `claude-multi doctor --repair-all`). `Attention` is not damage.
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
- **Rollback** — switch to the previous Home Manager generation; old launchers
  fail closed on schema-v3 records, and transcripts always stay recoverable.

## Files you might look at

```text
~/.local/state/claude-multi/      sessions, scopes (generated), pointers, locks
~/.config/claude-multi/           your compositions, gateway config (secrets — keep private),
                                  native-contract.json (operator override from `update`)
~/.claude/projects/…              transcripts (Claude's own; never touched by the launcher)
```

Developer documentation (architecture, invariants, how to change things):
[`AGENTS.md`](AGENTS.md) and [`../../docs/claude-multi-final/`](../../docs/claude-multi-final/README.md).
