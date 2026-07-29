# 006 — 5ee2f942 transcript genuinely missing

**Status: open** (needs an operator decision once 003's gate ships)

## Found (2026-07-29, investigator A3, metadata-only)

Managed session `5ee2f942-a367-4e96-a113-72eb4ecbd84c`
(cm:default, durable g6, cwd `/home/kotur/projects/occams-agent-flow`):

- Record healthy: `identity_state: authoritative`, runtime ID equals
  managed ID, no aliases, no pending forks, `last_event_source: end`,
  `last_end_reason: other`, last seen 2026-07-29T05:50:57Z.
- **The expected transcript is absent** at
  `~/.claude/projects/-home-kotur-projects-occams-agent-flow/5ee2f942-….jsonl`
  — and a filename-only search across every `~/.claude/projects/*/`
  directory found **no copy anywhere**.
- `claude-multi -r … --print-launch` still emits
  `--resume 5ee2f942-…` — today's prepare path does not detect the
  absence; the native "No conversation found" error surfaces bare
  (operator report, issue 003 message 2).

## Consequence

Resume can never succeed for this record unless the transcript file is
restored (backup) — no relink or repair can invent it. The 003 resume
gate pre-detects this state and says so, offering `sessions forget` as
the explicit cleanup (claude-multi never deletes transcripts itself; a
missing file is not its doing).

## Open question (unanswerable from metadata)

WHY the file is missing: hook-failure invisibility (known limitation —
a session that never wrote), a crash before first write, or external
deletion. Noted honestly; no evidence distinguishes them.

## Operator decision pending

Restore from backup if one exists; otherwise
`claude-multi sessions forget 5ee2f942-a367-4e96-a113-72eb4ecbd84c`
once the gate makes the state visible.

## Verification round (2026-07-29, second review — approve)

- Record metadata re-confirmed (cwd, runtime==managed, authoritative,
  end/other, last_seen 05:50:57Z); transcript confirmed absent across
  all nine project directories.
- The 2.8.0 gate refuses pre-exec with the exact guidance (exit 2).
- **Why-narrowing attempt (metadata-only, inconclusive):** four other
  records share the `end/other` pattern and still have transcripts, so
  the pattern doesn't explain the loss; lifecycle hooks demonstrably
  worked for this record at some point (authoritative + later End).
  Metadata cannot distinguish crash-before-first-write from later
  external deletion; an early isolated hook failure can't be excluded.
- **Disclosure (bounded probe):** an initial bare-`claude-multi` probe
  hit the INSTALLED 2.7.2 profile binary (its PYTHONPATH override), not
  the checkout — and the pre-gate 2.7.2 launcher committed the resume
  (record epoch advanced to 17) before native exec failed "No
  conversation found". This is exactly the mutate-then-fail shape the
  2.8.0 gate prevents (refuse before mutation). Record remains healthy
  and consistent; no transcript was created. Lesson for live smokes:
  from the checkout use `PYTHONPATH=src ./bin/claude-multi` (or
  `python3 bin/claude-multi`), never a bare `claude-multi`.
