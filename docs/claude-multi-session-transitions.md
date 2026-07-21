# claude-multi session transitions — gaps and remediation plan

Status: observed and documented on 2026-07-21. This is a design/remediation
note, not evidence that the proposed behavior is implemented.

## Purpose

`claude-multi` can resume an exact managed Claude Code session with a different
composition, but cross-provider transitions currently expose an incomplete
fork workflow. The failure is most visible when an old Anthropic-backed session
contains needed context, Anthropic credits are unavailable, and the user wants
to continue that transcript with Kimi or OpenAI.

The safe product goal is:

> Select an exact existing session, preserve its visible transcript, and move
> work to a new composition without contacting the exhausted source provider.
> Fork when possible so the source branch remains independently resumable;
> otherwise offer an explicit in-place transition with honest warnings.

Re-enabling dynamic gateway model discovery in Claude Code's `/models` picker
is not the proposed solution. Model selection, context bounds, effort lanes,
agent inventory, provider availability, and session provenance must continue to
move together as one validated composition.

## Current contract

- `/models` is Claude Code's native picker. Gateway discovery is deliberately
  disabled, so it does not enumerate Kimi or OpenAI routes.
- A composition owns the lead model and all generated agent variants. Changing
  the lead is therefore a launcher/session transition, not an in-session model
  preference.
- `-c` targets the last managed session in the current directory; `-r UUID`
  targets an exact managed session.
- When the selected composition changes provider, the launcher defaults to a
  fork because that preserves the source branch and separates worker/model
  state.
- The pinned Claude CLI contract for
  `--resume OLD --fork-session --session-id NEW` is still marked `unverified`
  in `catalog/native-contract.json`. `claude-multi` consequently blocks the
  fork instead of guessing.
- `O resume current without fork` is the working escape hatch. It retains the
  visible transcript and reasserts the selected composition on the same native
  session ID, but hidden reasoning continuity may be lost and the session now
  has mixed-provider history.
- `sessions link UUID` records launcher metadata without inspecting Claude's
  private state. This preserves the no-private-file-scraping boundary, but it
  also cannot prove that the native conversation exists.

## Observed failure sequence

The following sequence occurred during a real user-led transition:

1. An old managed session was recorded with the trusted `default` composition
   and an Anthropic/Fable lead.
2. The user selected the exact session with `-r OLD` and requested the
   `kimi-sol` composition.
3. The quick-confirm correctly detected composition drift and an
   Anthropic-to-Kimi lead-provider change.
4. The default action became `Fork`, but `Status BLOCKED` reported that the
   triple-flag fork contract was unverified.
5. The documented manual fallback, `claude --resume OLD --fork-session`,
   displayed a new UUID through `/status`.
6. The user exited before sending a normal turn to avoid consuming Anthropic
   credits. Claude Code did not persist a resumable conversation for that new
   UUID.
7. `claude-multi sessions link NEW` accepted the UUID and created a plausible
   launcher record because linking intentionally does not inspect native Claude
   state.
8. Resuming `NEW` later failed with `No conversation found with session ID`.
9. Returning to `OLD`, selecting `O`, and then reaching `Status Ready` worked.
   The UI still required a second, empty Enter to launch. Repeatedly pressing
   `O` only reselected the override and redrew the same Ready screen.

This established two separate facts:

- A UUID shown by a native fork is not proof that the fork has been persisted.
- A valid launcher session record is not proof that the corresponding native
  Claude conversation exists.

## Problem inventory

| ID | Priority | Problem | User impact |
| --- | --- | --- | --- |
| ST-01 | P0 | `F fork` is presented as the safe default but is always blocked while the native contract is unverified. | The recommended action is impossible at the moment it is needed. |
| ST-02 | P0 | `sessions link` can create a record for a nonexistent native conversation. | A record looks valid until launch fails; cleanup is manual. |
| ST-03 | P0 | Native fork creation is not persisted before the first real turn. | The no-source-provider-call manual workaround does not produce a usable branch. |
| ST-04 | P1 | The quick-confirm enters a blocked fork plan before making the usable in-place path clear. | Cross-provider resume looks broken rather than deliberately guarded. |
| ST-05 | P1 | After `O`, the footer still exposes `O` and requires an unlabeled empty Enter to launch. | Users repeatedly press `O` or type `0` instead of launching. |
| ST-06 | P1 | There is no first-class “transition exact session to composition” command. | Users must understand `-r`, drift choices, fork policy, and override semantics. |
| ST-07 | P1 | `/models` shows only native Claude choices and does not explain managed composition ownership. | Users reasonably assume Kimi/Sol are missing or broken. |
| ST-08 | P1 | Existing workers retain their original model and tools during in-place transition. | One session may contain old workers plus a new lead/inventory without strict separation. |
| ST-09 | P1 | The warning continues to say “Fork is the default” after the user explicitly selects in-place resume. | The final Ready plan does not read as a committed decision. |
| ST-10 | P2 | `sessions show` emits raw JSON and does not distinguish launcher metadata from verified native existence. | Diagnosis requires understanding internal hashes and snapshot fields. |
| ST-11 | P2 | Session provenance does not visibly summarize provider transitions and reduced-independence history. | Later users cannot quickly tell which branch/model handled which continuation. |
| ST-12 | P2 | The manual recovery documentation implied that `/status` plus `/exit` was sufficient to preserve a native fork. | The documented fallback was incomplete and produced a dangling record. |

## Safety and product requirements

Any remediation must satisfy these requirements:

1. **Exact identity:** the user can select `OLD` explicitly; no “most recent”
   ambiguity is required.
2. **Source preservation:** a successful fork leaves `OLD` unchanged and
   independently resumable.
3. **Target ownership:** the first request on the new branch uses the selected
   target composition, including lead, agents, context, effort, provider
   availability, and native-agent policy.
4. **No exhausted-provider call:** creating a Kimi/OpenAI fork must not require
   a turn against Anthropic merely to persist the branch.
5. **Native proof:** launcher state must not claim that a native session exists
   when it has not been persisted.
6. **No private scraping:** do not parse or copy Claude's private conversation
   files to synthesize a fork.
7. **Atomic lineage:** `NEW` records `forked_from: OLD`; partial failure cannot
   leave a normal-looking resumable record.
8. **Honest continuity:** visible transcript continuity is supported; hidden
   model reasoning continuity is never promised across providers.
9. **Worker isolation:** forked sessions do not inherit live workers. In-place
   transitions continue to warn that existing workers retain old definitions.
10. **Fail closed:** unsupported native behavior remains blocked rather than
    silently falling back to an in-place mutation.

## Proposed target UX

### Exact transition command

Add a first-class command or equivalent explicit mode:

```text
claude-multi sessions transition OLD --composition kimi-sol
```

It should render:

```text
Source session   OLD · default · Anthropic
Target           NEW · kimi-sol · Kimi
Mode             Fork (recommended)
Source call      None
Continuity       Visible transcript retained; hidden reasoning not guaranteed
Status           Ready

Enter fork and launch · I switch OLD in place · D details · Q cancel
```

The existing `claude-multi --composition NAME -r UUID` entry point may remain,
but it should converge on the same transition plan and terminology.

### Fork behavior

- `F`/Enter performs a verified launcher-owned fork and launches the target
  composition.
- The source and target UUIDs are shown before launch.
- The target record contains `forked_from` and is not reported as a normal
  resumable session until the native contract guarantees persistence.
- If the pinned CLI cannot support this safely, fork is displayed as
  `Unavailable`, not as a selectable default that produces `Status BLOCKED`.

### In-place behavior

- Rename the action from the vague `O resume current without fork` to an
  explicit `I switch in place` or `O override in place`.
- Require one deliberate confirmation when changing provider.
- After confirmation, remove the override action from the footer and make
  `Enter launch` visually unambiguous.
- Replace “Fork is the default” with “In-place transition selected; source
  session will continue with mixed-provider history.”
- Continue to show the worker-retention warning.

### Session inspection

Make human-readable output the default:

```text
Session          UUID
Native state     Verified | Pending | Missing | Unverified
Composition      default (recorded)
Current target   kimi-sol
Lead history     Anthropic -> Kimi
Forked from      UUID | None
Workers          Native state not inspected
```

Keep raw JSON behind `sessions show --json`.

`sessions link` should either verify native existence through a supported
Claude CLI interface or record `Native state: Unverified`. A missing session
must not look equivalent to a verified managed session. If no supported native
existence query is available, linking should require an explicit `--unverified`
or `--force` acknowledgement and the subsequent launch error should recommend
`sessions forget UUID`.

### `/models` guidance

Do not re-enable broad gateway discovery merely to solve session transitions.
Instead:

- Document that `/models` controls Claude Code's native model preference, not
  a managed composition.
- Add a concise managed-session hint to generated lead instructions or a
  launcher-provided status surface.
- Expose composition and exact agent inventory through `claude-multi show` and
  session inspection.
- Consider a `/cm-status` command only if it can be implemented without
  pretending that in-session model selection updates composition state.

## Verification-first implementation plan

### Phase 1 — isolated native-contract probe

Use the pinned Claude CLI with:

- a disposable `HOME` and state directory;
- disposable `OLD` and `NEW` UUIDs;
- a loopback fake provider only;
- a transcript marker in `OLD`;
- external network disabled or observed so only loopback is reachable.

Probe exactly:

```text
--resume OLD --fork-session --session-id NEW
```

Establish:

1. whether the triple-flag combination is accepted;
2. whether the first fork turn uses the target provider/model environment;
3. when `NEW` becomes persistently resumable;
4. whether cancelling before the first turn creates no session, a pending
   session, or a persisted fork;
5. whether `OLD` and `NEW` retain independent subsequent histories;
6. whether the source provider receives zero requests during target-provider
   fork creation;
7. whether a failed fork leaves any native or launcher residue.

Do not use a real user session for this probe.

### Phase 2 — decision gate

If the contract passes:

- record pinned-version evidence in `catalog/native-contract.json`;
- enable the existing compiler fork path;
- retain the fail-closed check for unknown/unverified Claude versions.

If the contract fails:

- do not synthesize a fork by copying private files;
- remove the blocked fork as the default interactive action;
- make in-place transition the only supported path;
- document native manual fork as requiring a persisted real turn, not as a
  no-provider-call workaround;
- investigate an upstream-supported fork API or machine-readable new-session
  identifier before attempting another implementation.

### Phase 3 — state and UX changes

Likely surfaces:

- `catalog/native-contract.json`: fork acceptance evidence and pinned version;
- session schema/state: pending/verified/missing native state and lineage;
- compiler: verified fork argv only;
- CLI quick-confirm: transition terminology, disabled-state rendering, and
  one-way confirmation flow;
- session commands: exact transition command and human-readable inspection;
- runbook/README: supported flows and recovery.

The ordinary launch architecture should remain one-shot compile then `execve`.
Do not add a resident scheduler or per-turn interception merely for forking.

### Phase 4 — focused tests

Add coverage for:

- exact `OLD` and `NEW` UUID ownership;
- verified and unverified native-contract states;
- cross-provider fork, same-provider fork, and in-place override;
- cancel before launch and failure cleanup;
- old/new independent resume;
- target composition on the first new-branch turn;
- no source-provider request;
- dangling link detection and `sessions forget` recovery;
- `O`/`I` confirmation followed by one clear Enter launch;
- old workers warning for in-place transition;
- human-readable and JSON session inspection;
- unchanged trusted default and unchanged gateway-discovery policy.

### Phase 5 — rollout

1. Run focused compiler/session/CLI/PTTY tests.
2. Run the full offline suite.
3. Build the Home Manager activation package.
4. Activate and confirm `claude-multi doctor` plus proxy health.
5. Repeat the disposable fork acceptance through the packaged binary.
6. Only then ask the user to fork a real session by exact UUID.

## Immediate supported workaround

Until fork verification is implemented, use exact in-place transition:

```bash
claude-multi --composition kimi-sol -r OLD
```

At the quick-confirm:

1. select `O resume current without fork` once;
2. confirm `Action Resume` and `Status Ready`;
3. press an empty Enter to launch.

This retains the visible transcript and avoids a source-provider call, but it
does not preserve an independent source branch.

If a manual fork was linked but Claude later reports `No conversation found`,
remove only the dangling launcher record:

```bash
claude-multi sessions forget NEW
```

Do not repeat the `/status` plus `/exit` manual-fork procedure and assume it
created a persistent native conversation.

## Non-goals

- Do not hot-swap the model or agent inventory inside a running Claude process.
- Do not migrate or rewrite existing live workers.
- Do not expose every proxy route through `/models` as a substitute for
  composition-aware transitions.
- Do not scrape, copy, or mutate Claude's private session files.
- Do not promise preservation of hidden chain-of-thought across providers.
- Do not silently turn a requested fork into an in-place resume.

## Definition of done

Session-transition work is complete only when:

- the interactive default is executable rather than blocked;
- exact source and target IDs are visible;
- a successful fork preserves and independently resumes both branches;
- the first target turn requires no source-provider credit;
- dangling metadata cannot masquerade as a verified native session;
- in-place transition is explicit and requires only one confirmation plus one
  clearly labeled launch action;
- failure recovery is actionable from the rendered error;
- `/models`, compositions, and session transitions have distinct documented
  responsibilities;
- disposable packaged acceptance passes before any real-session fork.
