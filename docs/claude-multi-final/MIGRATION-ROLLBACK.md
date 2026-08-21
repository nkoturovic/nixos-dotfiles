# MIGRATION & ROLLBACK

## 1. Working-tree reconciliation (G0/P0 disposition)

From the saved artifacts + fresh assessment (map:g0-diff, map:p0-probe):

| Item | Disposition | Why |
| --- | --- | --- |
| `native-contract.json` 2.1.217 promotion + schema | **Keep**, collapse schema ceremony in M3 | truthful, verified vs live artifact |
| `launch.py` immutable resolver + Doctor parity | **Keep** | core trust fix; failure-ordered |
| Generic `Agent(claude)` deny, env reservations, updater hygiene | **Keep**, deny moves argv→settings (SPEC §2.2) | same policy, durable channel |
| Session-scoped lead sentinel (`lead_prompt_path` digest+uuid) | **Keep** | fixes a demonstrated race; becomes the appendix path |
| Prompt/roles interim "no nested delegation" flip | **Rewrite** (M3): bounded delegation restored (U6), reviewer finisher clause | docs say nesting is on; finisher per REVIEW-STRATEGY |
| `dev.py` probe dispatch | **Rewrite**: lazy import inside subcommand | tracked file must not hard-import untracked module |
| `p0.py`, `p0inner.py`, `test_p0.py` | **Delete** (reference remains in `docs/claude-multi-rethink-handoff/artifacts/`) | no production requirement; SIMPLICITY bars sandbox creep |
| `probe.py`, `test_probe.py` | **Keep, dev-only**, used for F1–F6 | the only no-provider way to prove discovery/watcher semantics |
| Goldens (6 files) | **Re-bless** under new argv/scope outputs; add bless tool | brittle but valuable; tooling fixes the churn |
| Frozen C\* docs + handoff | **Unchanged** (historical) | evidence only |

## 2. Existing sessions and records

- **v1 records** (argv era): load with defaults; resume upgrades them to
  durable (scope attached, transcript untouched); `--legacy` preserves exact
  old behavior as a compatibility escape hatch (not a durability answer).
- **Linked/native sessions**: adopted against a user-selected composition
  (as today) and follow the **same uniform rule**: resume upgrades them to
  durable from that composition. No origin discriminator, no special case.
- **The currently-live kimi-sol session** (the one running this rethink): it
  is an argv-mode session. After activation the user may simply let it end;
  its record upgrades on next resume like any v1 record. Nothing is done to
  it by automation (no restarts without approval).

## 3. State migration

- New state: `scopes/` tree — additive only. Old launchers ignore it; old
  records lack `mode` and load as legacy (forward/backward compatible by
  defaults, one read path).
- `version.json` bump makes the **new catalog unreadable to the installed
  2.0.0 launcher** (closed schema) — deliberate fail-closed; the rollback
  path restores the old catalog with the old generation.
- Lead prompt files from v2 (`lead-prompt-<digest>-<uuid>.md`) remain valid;
  naming scheme unchanged.

## 4. Rollback anchors (verified 2026-07-22)

- Source: HEAD `cf04fce` + saved artifact bundle
  (`docs/claude-multi-rethink-handoff/artifacts/`, SHA256SUMS).
- Runtime: HM generation **76**
  (`/nix/store/xngnqdgicr77dzmfksdbn1jmy2v7h423-home-manager-generation`),
  immediate rollback **75**.
- Package: active `/nix/store/sjkxlr3aax7hycvn2hk548h1065lq7js-claude-multi-2.0.0`.

Rollback drill (user, after activation): `home-manager switch` to the pre-
activation generation ⇒ old launcher + old catalog return. Reality of record
compatibility: **v1 records** resume under the old launcher normally;
**v2 records** (durable era) are not consumable by the old launcher (its
session schema requires `version:1` and rejects new fields) — their
transcripts remain recoverable with native `claude --resume <uuid>`, and the
retained new-package store path (recorded in STATUS) can still be invoked
directly. **No state deletion is ever part of rollback.**

## 5. Transcript safety

- The launcher never reads, writes, moves, or deletes
  `~/.claude/**` (transcripts, projects, settings) — asserted by code review
  + the F-series fixtures using disposable roots.
- Scope dirs contain only generated files (agent md, settings json); deleting
  one can never lose a transcript.
- `sessions forget` = record + scope only; the transcript remains resumable
  natively (`claude --resume <uuid>`) — documented in the forget prompt.

## 6. Gateway/service continuity

- Activation restarts `cli-proxy-api` (systemd user service) — brief gateway
  interruption expected; in-flight sessions see provider errors, not state
  loss. The activation prompt tells the user to activate when no managed
  session is mid-run.
- Proxy auth dir and token file are untouched by the package change.

## 7. Catalog19 DeepSeek Pro/Grok 4.6 rollback (verified 2026-08-19)

Catalog19 changes both the installed catalog and three existing XDG
compositions while adding `deepseek-flash.json`; rolling back Home Manager
alone is incomplete. The verified sequence is:

1. Invoke the pre-catalog19 generation's `/activate` (generation 125's store
   path; after the 2026-08-19 drill, current history generation 127 points at
   that same store).
2. Save the three documents from
   `home-manager/claude-multi/tests/fixtures/compositions/024-rollback-catalog18/`
   through `CompositionStore` so live files are private mode 0600.
3. Delete only the generated XDG composition `deepseek-flash.json`; do not
   delete records, scopes, or any transcript-bearing path.
4. Run `claude-multi-proxy init`, restart `cli-proxy-api`, and run
   `claude-multi doctor --repair-all`.
5. Verify 2.18.0/catalog18 serves `claude-multi-grok45`, does not serve the
   Pro/Grok 4.6 aliases, the three restored documents match the rollback
   fixtures, and the gateway is healthy.

Attempt 1 exercised this sequence successfully after DeepSeek rejected the
probe's named forced `tool_choice` in thinking mode. Thirty-five repairable
records reconverged; the pre-existing ordinary `sol` record with retired
profile remains operator-owned and is not rewritten by rollback automation.
If a catalog19 session had been created, its record/transcript would remain
intact but could be unresolvable under catalog18; use the retained catalog19
package to transition it, never delete state as part of rollback.
