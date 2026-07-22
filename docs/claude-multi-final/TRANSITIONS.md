# TRANSITIONS — changing composition mid-session

Requirement: deliberate changes to agents, models, effort, workflow mode, or
policy for an existing managed session — with an explicit diff, truthful
classification, exact same-session resume, and rollback. Never contact or
control the shared daemon.

## 1. Classification (v1: relaunch-only)

| Change | v1 mechanism |
| --- | --- |
| variant add/remove/prompt change (catalog or composition) | relaunch |
| workflow `native` ⇄ `off` | relaunch (settings rewrite is process-start-read, U8) |
| lead model / effort | relaunch (new argv; record persists them) |
| permission/policy denies | relaunch |
| cwd / `--add-dir` set change | out of scope — start a new session |

**v1 has no hot path.** A same-process watcher probe (U3) runs in M1 to
inform a possible future hot mode; until evidence exists, every transition is
a controlled relaunch. No mixed old/new composition is ever applied to a
running process.

## 2. Authority model

- **Record = intent** (composition snapshot + catalog hash).
- **Catalog = trusted source of bodies/selectors** (installed, versioned).
- **Scope = pure function of (record composition, installed catalog)**.
- Repair/resume recompiles the scope deterministically. If the installed
  catalog hash differs from the record's, the recompile uses the current
  catalog and the drift is displayed (catalog updates legitimately refresh
  role contracts; composition changes always require an explicit transition).

## 3. Transition flow

1. From any shell **other than the target session**, run
   `claude-multi sessions transition <uuid> --composition <name>`
   (or the TUI action).
2. The tool prints the **semantic diff**: variants added/removed/changed,
   lead model/effort, workflow mode, denies — plus the catalog-drift line
   when the record's catalog hash differs from installed.
3. It then requires explicit confirmation that **the target Claude process
   has exited** (not merely idle). If the user cannot confirm, nothing is
   mutated and the exact post-exit command is printed instead.
4. From **inside** the target session (sentinel-detected), the command never
   mutates anything: it prints the diff and the exact command to run after
   `/exit`.
5. On confirmation: compile generation N+1 into the sibling staging dir
   `scopes/.<uuid>.new/`; then swap:
   - `scopes/<uuid>` → `scopes/.<uuid>.prev` (if a live scope exists)
   - `scopes/.<uuid>.new` → `scopes/<uuid>`
   - fsync the `scopes/` directory after each rename.
6. Save the record (generation N+1), then exec
   `claude --resume <uuid>` with the newly compiled argv.
7. `.prev` is removed by the next successful transition or `doctor --prune`.

Sibling staging paths are required: staging inside `scopes/<uuid>/.new`
makes the first rename move the staging dir along with the parent.

## 4. Crash windows (all enumerated, all converge)

| Window | State left | Convergence |
| --- | --- | --- |
| before first rename | live = N, `.new` staged, record = N | `doctor --prune` removes `.new`; nothing changed |
| between renames | **no live dir**, `.prev` = N, `.new` = N+1, record = N | repair: if live missing and `.prev` exists, rename `.prev` back (or recompile N) — record is still N so N is truth |
| after promotion, before record save | live = N+1, `.prev` = N, record = N | record (N) is authority: repair recompiles scope at N from record+catalog (deterministic), `.prev`/`live` discrepancy resolved to N |
| after record save, before exec | live = N+1, record = N+1, process unchanged | next resume uses N+1; consistent |
| exec `OSError` | restore `.prev` → live (if swapped), restore **pre-read prior record bytes** (never regenerated) | printed exact recovery command |

Record save is deliberately after swap and before exec; record remains the
authority in every window, and scope is always re-derivable from
record+catalog.

## 5. Rollback matrix

| Failure | State after | Recovery |
| --- | --- | --- |
| compile error | nothing touched | fix composition; retry |
| exec `OSError` | `.prev` restored, prior record bytes restored | printed command |
| hard crash | see §4 windows | `doctor --repair <uuid>` converges to record |
| user transitioned to a wrong composition | record + live scope hold the **selected** (wrong) composition; `.prev` may hold the prior generation | run `transition` back to the intended composition (same flow) |

## 6. Active-work and lifecycle guard

- Transitions never restart/signal/query the shared supervisor and never
  kill the target process.
- The relaunch only happens after the user confirms the target process has
  exited; running subagents/workflows are therefore never silently mixed
  with a new composition. The confirmation text says exiting restarts the
  turn; `/background` carry-over is the user's native alternative.
- A transition never changes the session UUID or fork lineage, and never
  touches transcripts, `~/.claude`, or project files.

## 7. Fork boundary (U7)

Native `/fork` copies model/effort/dirs but not launch flags it can't
inherit; sessions launched with a replaced system prompt may be **refused**
outright (AV L330), and our `--append-system-prompt-file` may trigger that
refusal. U7 therefore tests: (a) whether `/fork` is allowed at all for
managed sessions; (b) if allowed, whether the scope pointer carries. Until
verified, UX says managed forks may be refused; the supported paths are
transition or a new session.
