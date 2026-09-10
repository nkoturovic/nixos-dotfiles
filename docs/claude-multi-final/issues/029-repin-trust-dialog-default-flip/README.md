# Issue 029 — Claude 2.1.261 blocks the re-pin: trust dialog flipped its default to cancel

**Status:** investigating — first fix attempted, **did not work** (see
"Attempt 1" below); pin still 2.1.220.
**Reported:** 2026-09-10, from the failed `claude-multi update` run.
**Release:** none.

## Report

`claude-multi update` correctly refused to re-pin 2.1.261: the offline
evidence suite failed against the candidate contract, the repo was restored
unchanged, and no override was written. Pin stays 2.1.220.

```text
claude_multi.probe.ProbeError: probe PTY timed out waiting for b'WARNING'
```

## Root cause (binary evidence, not inference)

The real-binary probes drive the client through a scripted PTY interaction
table. Two sites hard-code the workspace-trust answer:

- `tests/test_scope_probe.py:1124` — `PTYInteraction(b"Quick safety check", b"1\r")`
- `tests/test_scope_probe.py:1202` — same pair

Both binaries contain the same trust dialog, but the **default focus changed**:

| | 2.1.220 (pinned) | 2.1.261 (candidate) |
|---|---|---|
| dialog props | `confirmLabel:"Yes, I trust this folder"` | `refuseInput:K, hideIndexes:!0, **cancelFirst:!0, focus:"cancel"**, confirmLabel:"Yes, I trust this folder"` |
| option 1 is | the confirm option | **the cancel option** |
| numeric indexes | shown | **hidden** (`hideIndexes`) |

Observed 2.1.261 render (from the failed run):

```text
Accessing workspace: /tmp/claude-multi-scope-probe-…/fixture/home
Quick safety check: Is this a project you created or one you trust? …
❯ No, exit
  Yes, I trust this folder
Enter to confirm · Esc to cancel
```

So the probe's `1\r` selects **"No, exit"** on 2.1.261: the client exits, the
PTY goes quiet, and the probe times out waiting for the next marker
(`WARNING`, the bypass-permissions dialog that follows onboarding). Nothing
is broken in the client or in the launcher — the *scripted answer* is now
wrong for that version. It is a deliberate upstream safety change (default
to NOT trusting an unknown folder).

## Why this is not a one-line fix

There is **no keystroke sequence valid for both versions**. The obvious
candidates each break one side:

- `1\r` — trust on 2.1.220, **exit on 2.1.261**.
- `2\r` — exit on 2.1.220, trust on 2.1.261 (only if hidden indexes still
  accept digits — unverified).
- `\x1b[B\r` (Down, Enter) — moving off a focused confirm on 2.1.220 goes
  the wrong way.

The interaction table is shared by every `RealPinnedBinaryTests` probe, and
it runs against the **pinned** binary on every build, so any change must
keep 2.1.220 green while also working for the candidate.

## Attempt 1 (2026-09-10) — version-aware answer; still fails

Implemented `probe.trust_dialog_answer(trusted)` (probe.py): `< 2.1.261`
returns `1\r`, `>= 2.1.261` returns `\x1b[B\r` (Down, Enter), fail-closed to
`1\r` for an unparseable version. Both probe sites now call it, unit-tested
in `TrustDialogAnswerTests`, and the real pinned-binary suite stayed green
against 2.1.220 (8 tests OK — no regression).

`claude-multi update` then re-ran the gate against 2.1.261 and **failed
again on the same marker**, restoring the repo. The PTY capture is
informative and narrows the fix:

- the dialog rendered cancel-focused (`❯ No, exit`),
- the Down key **did** register — the next frame shows
  `❯ Yes, I trust this folder`,
- but the following frame shows focus back on `❯ No, exit`, i.e. **Enter
  did not confirm** (or re-rendered the dialog), and the probe then timed
  out waiting for `WARNING`.

So the layout/focus part of the diagnosis is confirmed, but `\r` is not the
confirm keystroke in this dialog (plausible causes: the dialog's confirm is
a different key, the two bytes arrived as separate reads with a re-render
between, or `refuseInput`/`hideIndexes` changes input handling). Next
attempt should isolate the keystroke empirically — drive only the trust
dialog in a scratch fixture and try `Down+Enter`, `Down+Space`, and a
single combined write — rather than guessing again inside the 7-minute gate.

## Options (choose at implementation time)

1. **Version-aware interaction.** Teach `probe.run_native_pty` (or the
   interaction table) a per-version onboarding step keyed off the client
   version already known to the caller, with a distinct trust answer per
   layout. Most explicit; keeps the dialog answer deliberate.
2. **Marker-based layout detection.** Add a follow-up interaction that
   distinguishes the two renders (e.g. the presence of the literal
   `❯ No, exit` before `Yes, I trust this folder`) and answers accordingly.
   Avoids a version table but couples the probe to render details.
3. **Do nothing until the next Claude release** re-pins by other means.
   Legitimate: 2.1.220 is working, and the Attention line is by-design lazy
   state, not damage.

Whichever is chosen, the change must land with: the real pinned-binary
suite green against **2.1.220**, a recorded run against 2.1.261, and the
contract's `capabilities.onboarding_trust` (currently `pending`) moved only
with that evidence — not by assertion.

## Constraints

- The trust dialog is a security surface: the probe runs in a disposable
  fixture directory, so answering "trust" there is correct, but the change
  must not make the harness answer `trust` when run anywhere else, and must
  not weaken the gate into passing a client it has not actually driven.
- No provider calls are involved anywhere in this issue.

## Related

- `AGENTS.md` §5 (`claude-multi update`, the evidence gate), §6 rule 4.
- `catalog/native-contract.json` → `capabilities.onboarding_trust: pending`.
- Prior pin: D57-era 2.1.220; the update command and override contract are
  documented in `USAGE.md` / `STANDALONE.md`.
