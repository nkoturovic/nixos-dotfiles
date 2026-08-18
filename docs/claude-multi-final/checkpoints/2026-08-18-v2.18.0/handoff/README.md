# Checkpoint 2026-08-18 · claude-multi v2.18.0 — START HERE

You are picking up the claude-multi project at its 2026-08-18 **v2.18.0**
checkpoint: **activated (HM gen 125), doctor Ready (one operator-action
note, below), Claude pinned at 2.1.220. Sol is 1M-class on the codex
subscription route — acceptance-probe-verified (343,541 input tokens
accepted 2026-08-18).** Supersedes
[`../../2026-08-12-v2.17.0/handoff/README.md`](../../2026-08-12-v2.17.0/handoff/README.md)
(which carries the full project description — this checkpoint is a
context-window flip on top).

## What changed (D57, blueprint 023)

OpenAI documented a 1M-token context window for GPT-5.6 Sol on the
ChatGPT/Codex subscription route (2026-08-12). Sol now: client/provider
1,000,000 (declared 1,050,000), `[1m]` selectors
(`gpt-multi-sol-high[1m]` / `-xhigh[1m]`), `large` ordinary profile (the
sol-only profile retired), scalar null; **validated 343,541** (the
probe-verified bound; near-limit 1M unverified). gpt55 keeps the D56
fence (258,400 — no 1M for that model). Triggers: 882,000 for a sol-led
composition; 867,254 for ordinary sol sessions (large-profile min via
qwen38's 983,616). The all-1M default rig exports no process scalar;
mixed rigs fence at qwen38's 983,616. The D56 revert path stays recorded
in the qualification text (restore 372,000/258,400 if the enablement is
pulled).

## Recorded state (verify before trusting)

- **Source:** `/home/kotur/personal/nixos-dotfiles` branch `feature/term-only`,
  HEAD `52b6219` + activation-docs commit on top; nothing pushed.
- **Activated:** HM generation **125**; `claude-multi --version` →
  2.18.0 (catalog 18). Rollback: gen 124/123.
- **Health:** `claude-multi doctor --repair-all` converged 33 durable
  sessions onto catalog 18. One expected follow-up: ordinary session
  `d928f2a2` (workspace/news) carries the retired `sol` profile — doctor
  reports it with the exact remedy (`claude-gateway -r d928f2a2-… --model
  sol` re-pins to `large`; operator's call — it's their session).
- **Evidence:** 1,664 host tests OK (skipped=2); sandbox green; golden
  diffs reviewed (exactly `[1m]` selectors + scalar-unset); acceptance
  probe green (343,541 input tokens via the codex pool, HTTP 200);
  verification sweep (glm52 + qwen38, adversarial verify) — 4 confirmed
  findings fixed (qualification precision both trigger paths, retired-
  profile-aware doctor hint, HANDOFF sentence, newline nit), 1 refuted.
- **Docs:** DECISIONS through D57; blueprints 014–023; STATUS/HANDOFF/
  USAGE/UX/AGENTS current; wikis updated.

Verify with:

```bash
claude-multi --version            # 2.18.0
claude-multi doctor               # Ready (one retired-profile note)
claude-multi models | grep -A1 '^sol'   # [1m] selectors
cd /home/kotur/personal/nixos-dotfiles && git log --oneline -4
```

## Canonical docs and hard rules

Unchanged from the [2.17.0 checkpoint](../../2026-08-12-v2.17.0/handoff/README.md)
— same links, same standing rules (no real-provider calls without per-call
approval; never transcripts/daemon; tests before claims; commits on
`feature/term-only`, no push without approval; cross-family review at
meaningful boundaries; simplicity budget).

Now go to [`open-items.md`](open-items.md).
