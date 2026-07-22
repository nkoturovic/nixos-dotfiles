# Current state at handoff

Date: 2026-07-22. Revalidate every runtime fact before using it.

## Repository

- Repository: `/home/kotur/personal/nixos-dotfiles`
- Source HEAD: `cf04fce` — `Document claude-multi session transition gaps`
- Earlier relevant commits:
  - `6c4a51b` — terminal/reviewer routing fixes
  - `23a5ddf` — claude-multi v2 composition compiler
- No durable-session/G0/P0 work has been committed.
- The working tree contains substantial tracked modifications and untracked
  files; inspect `WORKING-TREE.md` and the saved artifact bundle.
- Nix flakes ignore untracked source files. Do not trust a flake build until
  intended files are tracked or deliberately evaluated from a prepared path.

## Last recorded active runtime

- Home Manager generation 76:
  `/nix/store/xngnqdgicr77dzmfksdbn1jmy2v7h423-home-manager-generation`
- Immediate rollback generation 75:
  `/nix/store/myjl86j988wmb7va0yzhzdfxinj8si66-home-manager-generation`
- Active installed package last verified:
  `/nix/store/fxapndpszjxdxz61jm5m12q1ngw014i4-claude-multi-2.0.0`
- Proxy/Doctor were Ready before the rethink request.
- None of the current uncommitted changes have been activated.

## Native Claude evidence

- Configured symlink last resolved to 2.1.217.
- Inspected binary:
  `/home/kotur/.local/share/claude/versions/2.1.217`
- SHA-256:
  `2630fc5dc6db61bc03f86b95daf47766e5ed5b61873f7bb7cfea764c5ac5a9ba`
- Retained 2.1.216 SHA-256:
  `74deca45220b8080ec75ab099bd5a5980e41a2b5879846a008fb115d436de085`
- Model catalog `minimum_tested.claude_code` remains 2.1.216; no provider/model
  compatibility request was made against 2.1.217.

## Test evidence

- G0 and P0 code-review loops reached clean approval before the rethink.
- Latest reported full Python discovery after final P0 cleanup: 651 tests OK,
  1 intentional skip.
- Independent focused verification: 210 tests OK.
- P0 fake self-test: 26/26 assertions, zero host canary contacts.
- P0 contract-pinned real operation: Claude `--version` only, return 0, zero
  provider requests. Evidence paths are in `EVIDENCE.md`.

These results do not approve the frozen architecture. They show only that the
uncommitted G0/P0 implementation behaves as tested.
