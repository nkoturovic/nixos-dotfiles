# Saved reference artifacts

This directory preserves the current uncommitted work independently of the live
working tree.

Expected generated files:

- `tracked-changes.patch` — `git diff --binary` against HEAD `cf04fce`.
- `untracked-reference-files.tar.gz` — selected untracked implementation and
  frozen historical docs, stored with repository-relative paths.
- `untracked-archive-files.txt` — exact archive member list.
- `p0-self-test-evidence.json`, `p0-version-evidence.json` — preserved
  metadata-only P0 acceptance records copied from ephemeral `/tmp` paths.
- `handoff-docs.tar.gz` — self-contained copy of the handoff Markdown files
  (excluding the artifacts directory itself).
- `git-status.txt`, `git-diff-stat.txt`, `git-log.txt` — snapshot metadata.
- `SHA256SUMS` — checksums for patch/archive/snapshots.

## Restoration in a clean checkout

Reference only; do not apply without reviewing the new architecture.

```bash
git checkout cf04fce
git apply --binary docs/claude-multi-rethink-handoff/artifacts/tracked-changes.patch
tar -xzf docs/claude-multi-rethink-handoff/artifacts/untracked-reference-files.tar.gz
```

Then inspect `git status`, every diff and every untracked file. The archive does
not contain this handoff folder itself.
