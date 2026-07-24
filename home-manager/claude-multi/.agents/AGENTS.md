# claude-multi/.agents — development workspace

This is the agent **workspace** for the claude-multi product (scratch and
short-lived working notes only). It is **not a documentation store** — the
canonical docs live one directory up and in the design package:

| You need | Go to |
| --- | --- |
| Development guide (start here for product work) | [`../AGENTS.md`](../AGENTS.md) |
| User guide | [`../USAGE.md`](../USAGE.md) |
| Project entry point / current state + open items | `docs/claude-multi-final/checkpoints/` → latest `handoff/README.md` |
| Live ledger | `docs/claude-multi-final/STATUS.md` |
| Design assessment | `docs/claude-multi-final/SANITY.md` |

(`docs/` = `/home/kotur/personal/nixos-dotfiles/docs`)

## What belongs here

- `scratch/` — ephemeral per-session work (repro scripts, temp notes).
  Nothing here is durable; clean it when done.
- `wiki/` — optional small working notes that are NOT yet worth promoting
  into the canonical docs. Promote or delete; never accumulate.

## What never belongs here

- Anything a fresh agent would need to find (that goes into `../AGENTS.md`,
  the design package, or a checkpoint handoff).
- Secrets, tokens, transcripts, or live-state copies.
- Generated/build artifacts (this folder is not packaged by `package.nix`;
  keep it that way).

## Keeping it tidy (standing rule)

Whoever uses this workspace leaves it empty or promotes the content. If a
note here would matter to a future agent, it is in the wrong place — move it
to the canonical doc and delete it here.
