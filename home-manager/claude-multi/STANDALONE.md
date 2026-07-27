# Standalone Claude Code — how it works (and how it relates to claude-multi)

This guide covers **plain `claude`** — upstream Claude Code exactly as
Anthropic ships it — how it is set up on this machine, and how it relates
to claude-multi. The short version: **claude-multi never configures,
wraps, or hijacks plain `claude`.** Everything below is native behavior;
knowing it explains a few things that surprise everyone eventually.

## How Claude Code is set up on this machine

- **Binary:** upstream's own installer layout. `~/.local/bin/claude` is a
  symlink into `~/.local/share/claude/versions/<X.Y.Z>`; the updater keeps
  recent versions there and moves the symlink on update. claude-multi
  launches the pinned version file directly (hash-verified), never the
  floating symlink.
- **Integration via Home Manager (Nix):** the things *around* Claude Code
  are declarative — the `claude-multi` package and its three entrypoints,
  the `cli-proxy-api` **systemd user service** (the local model gateway on
  `127.0.0.1:8317`), and the CLIProxy registry patches. A
  `home-manager switch` rebuilds those and restarts the gateway; it does
  **not** touch the Claude binary, your transcripts, or your Claude
  settings. Roll back with a previous Home Manager generation.
- **Your Claude state:** `~/.claude/` — settings, OAuth login, projects
  (transcripts), todos, daemon state. Owned by you and upstream; nothing
  Nix-managed writes here.
- **claude-multi state:** records/scopes/pointers under
  `~/.local/state/claude-multi/`; your compositions + gateway config +
  update override under `~/.config/claude-multi/` (contains secrets —
  keep private).
- **Secrets:** the gateway token lives at
  `~/.config/claude-multi/api-key` (mode 0600, owner-only); provider keys
  referenced by the gateway live under `~/.config/secrets/`. None of these
  ever appear in scope files, records, logs, or argv.

## The basics

```bash
claude                    # interactive session in this directory
claude -c                 # continue the last session here
claude --resume <uuid>    # resume an exact session
claude -p "..."           # one-shot print mode (scripts)
```

- **Auth:** your normal Anthropic account (OAuth login). Plain `claude`
  does **not** use the local gateway — the gateway exists only for
  claude-multi / `claude-gateway` sessions.
- **Transcripts:** every session is a JSONL file under
  `~/.claude/projects/<encoded-cwd>/<session-uuid>.jsonl`. They are yours,
  they are never deleted by any tool here, and any of them can be resumed
  natively with `claude --resume <uuid>` from the same directory.
- **Settings:** `~/.claude/settings.json` (yours), project `.claude/`
  dirs, and native `/model`, `/effort`, `/compact` all behave exactly as
  upstream documents them.

## Updates: upstream auto-update is ON (and that's intended)

Claude Code updates itself: its built-in updater downloads new versions and
moves the `~/.local/bin/claude` symlink — on its own schedule, even while
sessions run. Running processes keep their start-time binary; new sessions
get the new version. This is upstream's channel and stays enabled: **plain
`claude` always tracks the latest release.**

claude-multi deliberately does *not* follow automatically — it launches a
hash-verified pinned binary so managed sessions are always correct. When
upstream lands a newer version than the pin, the launcher card shows an
**update badge** and `claude-multi doctor` an **Attention** line; one
keypress (**U**) or `claude-multi update` re-pins with full offline
evidence. So: plain `claude` is always fresh, managed sessions are always
verified, and you choose when the two re-align.

## The background daemon (why sessions seem "active in the background")

Claude Code runs a shared supervisor (the "daemon"). When you background a
session — or your terminal detaches — the daemon **adopts** it: the session
keeps running under a daemon-owned host process, visible in the
**`claude agents`** view. Practical consequences:

- A session listed in `claude agents` may be **owned by the daemon**, not
  by any terminal. The claude-multi sessions screen marks these **●** live.
- **Reattaching to a daemon-owned session forks it.** Two processes can't
  own one session, so Claude Code creates a *fork*: a new session UUID with
  a copy of the transcript. This is native behavior, not a bug — but it
  surprises everyone. To avoid it: exit the backgrounded session first (or
  resume it after it exits), instead of re-entering from a menu.
- After an upstream auto-update, the daemon runs the **new** version and
  relaunches its adopted sessions with it — preserving `--settings` /
  `--add-dir` / `--model`, but **scrubbing `ANTHROPIC_*` environment
  variables** from its children. (That last bit broke managed sessions
  before claude-multi 2.6.0 made gateway routing durable through the scope
  settings; managed sessions are unaffected now.)

## Forks in plain `claude`

A fork is a full copy: the new session's transcript starts identical to the
original at the fork point, then diverges as you work. Both files stay on
disk forever. Nothing is lost — but the two branches are independent, so
pick one and continue there. If the forked session was a claude-multi
managed session, the launcher detects the fork and helps you resolve it
(see USAGE.md → "Native forks"): adopt the fork, discard the marker, or
let it self-clear when the fork is the live branch.

## Which tool when

| Want | Use |
| --- | --- |
| Plain upstream Claude Code, native everything | `claude` |
| One local transport for many models (incl. non-Anthropic), safe native `/model` within a context profile | `claude-gateway` |
| A durable team: lead model + generated specialists, compositions, transitions, fork-safe records | `claude-multi` |

All three share the same transcripts directory and can see each other's
sessions: a plain session can be adopted into a composition later
(`claude-multi sessions link <uuid> --composition NAME`, or **L** in the
picker), and any session can always be resumed natively.

## What claude-multi will never do to plain `claude`

- Never writes `~/.claude/settings.json`, `~/.claude/agents/`, or any
  global Claude config.
- Never sets gateway env for plain sessions (your auth is untouched).
- Never reads transcripts, and never deletes anything under `~/.claude`.
- Never touches the daemon/supervisor — it only *observes* (read-only)
  to show you the ● live markers.

If something in plain `claude` behaves oddly, it is upstream behavior or
upstream config — `claude doctor` (upstream's own) and `/status` are the
native diagnostics; `claude-multi doctor` only reports on launcher-managed
state.
