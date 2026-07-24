# VERIFICATION — evidence plan (no provider, no live daemon)

Gates: nothing here contacts a real provider or the live shared daemon.
Fake-provider work uses loopback fixtures with disposable roots (probe.py
discipline). User-performed steps are the only ones touching real Claude.

## 1. Unit/focused (host, hermetic)

- Scope compiler: file tree, frontmatter shape (name/model/effort/isolation),
  canonical prompt bodies, atomic `.new` swap, idempotence.
- Settings overlay: key allowlist, deny list composition, workflow on/off,
  `availableModels` contents, `worktree.baseRef` rule, unknown-key refusal.
- Launch: argv shape (no `--agents`/`--disallowedTools`), ordering contract,
  execve-failure cleanup (record forgotten, pointer cleared, scope removed).
- Records: v1/v2 → side-effect-free in-memory v3 migration; stable managed ID
  vs current runtime UUID; alias lookup; duplicate runtime ownership refusal;
  ordinary/managed discriminators; SessionEnd advisory behavior.
- Lifecycle hooks: generated settings shape, 5-second synchronous commands,
  startup/resume/clear/compact idempotence, fork pending state, stdin pipe
  consumption (never `/dev/tty`), transcript-path discard, model-fence breach
  diagnostics.
- Collision gate: synthetic project trees (exact `cm-*` hit, non-cm pass,
  nested closest-wins documentation case).
- Transitions: diff classification, generation swap, runtime-UUID resume with
  stable scope identity, restore-on-failure, crash-converge (record authority),
  hot-flow refusal without watcher evidence.
- Ordinary gateway: zero generated agents/appendix, context-profile model fence,
  implicit resume preserves native `/model` choice, explicit cross-profile
  relaunch pins the requested model, unified picker/doctor behavior.
- Context: every supported lead's client/provider/reactive tuple using the
  pinned 20K output reservation, runtime-controlled proactive preparation,
  mixed scalar/client-cap protection, Qwen `[1m]` suffix-to-wire mapping, and
  Sol/large-profile environment policy.
- Doctor: scope integrity, repair convergence, prune, evidence-level lines.
- Goldens: scope tree + argv-set goldens with a `claude-multi-dev bless`
  regenerator (kills manual golden churn; bless diff must be review-empty
  after intentional changes).

## 2. Integration (fake provider, disposable roots)

Via probe.py fixtures (loopback `FakeAnthropicProvider`, private HOME/XDG,
credential scan, pinned binary, metadata-only evidence).

**Daemon-domain precondition**: F1–F4 run the real pinned binary. They are
permitted in automation only if per-config-root daemon domains verify
(static evidence + a fixture run that observes its own domain and never
touches the uid-shared `/tmp/cc-daemon-<uid>`). probe.py's real-binary gate
is extended to allow exactly this case and refuses otherwise. If the
precondition fails, F1/F2/F4 move to the user-acceptance list and M2+
proceeds on documented-backgrounding evidence only.

- **F1 fresh durable launch**: scripted first turn forces an
  `Agent(subagent_type=cm-analyst-sol-high)` tool_use echo ⇒ client-side
  registry accepts the type (proves on-disk discovery through `--add-dir`).
- **F2 legacy upgrade resume**: v1 record resumes durable; same discovery
  assertion.
- **F3 transition relaunch**: model-change transition resumes same UUID with
  new scope generation; record/scope converge (fixture if permitted, else
  unit-level via injected exec boundary).
- **F4 watcher probe (U3)**: after session start, add a new agent file into
  the live scope, force a scripted delegation to the new type ⇒ accepted
  (watched) or refused (restart-needed). Records the U3 verdict.
- **F5 collision refusal** (hermetic): project tree with exact `cm-*` file ⇒
  launch fails closed, error names path.
- **F6 workflow off** (hermetic): compiled settings contain
  `disableWorkflows:true`; lead argv effort is `xhigh` when composition asks
  ultracode+off.
- **F7 takeover probe (U1 stop-gate)**: fixture with its own config root and
  both retained binaries; launch a session on 2.1.216, relink the fixture
  symlink to 2.1.217, let the fixture supervisor take over, then assert the
  roster still contains all six `cm-*` types (scripted delegation to one).
  Only when the daemon-domain precondition holds; otherwise this is exactly
  acceptance step L2.
- **F8 runtime-ID divergence**: launch under stable ID A, deliver a synthetic
  SessionStart with runtime UUID B, assert record/scope stay keyed by A and the
  next compiled argv is exactly `--resume B`; repeat for compact/clear.
- **F9 ordinary mode**: fresh and resume with empty agent tree, native model
  availability per profile, no appended composition prompt, fake `/v1/models`,
  and cross-profile explicit pinning.
- **F10 CWD/adoption matrix**: inaccessible original CWD leaves no committed
  launch state; duplicate native UUID rows deduplicate; ambiguous project slugs
  fail closed; runtime aliases never reappear as unmanaged rows.
- **F11 compaction lifecycle**: pinned 2.1.217, five visible PTY turns,
  cache-aware usage, auxiliary-request classification, and metadata-only hooks
  prove both manual and automatic `SessionStart(source=compact)` without
  `/count_tokens`, a claimed proactive threshold, or a real provider.
- **F12 delegated prompt shape**: bounded request metadata records only canonical
  system/message byte counts and per-message hashes. Differential fake-model
  cases distinguish inherited parent context, generated child system context,
  and deterministic low-context overflow without retaining prompt text.

## 3. Package/Nix

- `nix-build` package.nix; `tests/default.nix` (full suite in sandbox);
  flake check target. Store-path contents: catalog/schemas/src/settings/
  version/bin only (no p0 files — asserted).

## 4. Static/native-contract

- `claude --version`, `--help` (offline) re-pinned; binary sha256;
  `native-contract.json` consistency tests.
- Repo hygiene: `git diff --check`; no `p0` references; no secrets in diff
  scan (existing test suite coverage).

## 5. Live acceptance (user-performed, exact script, no automation)

L0. `claude-multi` → Enter: TUI shows durable badge; session starts; Agent
    tool roster lists all six `cm-*` types (typeahead/exact-ID dispatch works).
L1. **Kill-resume proof**: user kills the TUI process (their own session),
    runs `claude-multi -r <uuid>` ⇒ roster still complete. (Process
    replacement with registry rebuild — the original failure shape.)
L2. **Takeover proof (U1)**: at the next natural Claude upgrade (or a
    user-initiated relink between retained versions 2.1.216/2.1.217),
    supervisor restarts ⇒ roster complete afterwards; `doctor` scope check OK.
L3. Transition: user runs `sessions transition <uuid>` with a model change ⇒
    diff shown, relaunch resumes same transcript, new model in effect.
L4. Workflow off composition: `/effort` menu lacks ultracode; keyword inert;
    `disableWorkflows` honored.
L5. Project agents: a project `helper` agent appears and dispatches; an exact
    `cm-*` copy blocks launch with the named error.
L6. Plain `claude`: no `cm-*` types, no policy denies, unaffected settings.
L7. `claude-gateway`: no `cm-*` roster, `/model` shows only the selected safe
    profile; switch within the profile, exit, and resume — native choice is
    preserved. Cross-profile `-r UUID --model MODEL` relaunches explicitly.
L8. Identity/compact: compare `sessions show` stable/runtime IDs with native
    `/status`; compact once and verify subsequent resume still targets the
    current runtime UUID. Sol compacts before 372K; Qwen before 983616;
    Fable/Opus/Kimi retain large-context operation.
L9. Rollback drill: `home-manager switch` to the pre-activation generation ⇒
    old launcher returns with the old catalog; v1 records resume normally;
    schema-v3 transcripts remain recoverable with the runtime UUID displayed
    by `sessions show`; the retained new-package store path (recorded in
    STATUS) can still be invoked directly if needed. The old launcher cannot
    consume v3 records — that is expected and stated.

Evidence per step: what the user saw + `doctor` output pasted into STATUS.md
(or a short checklist sign-off). Failures route to PLAN §6 fallbacks.

## 6. What is deliberately not tested in automation

- Real provider routing/model quality (user's domain).
- Live daemon behavior (prohibited; observed only via user acceptance).
- Transcript internals (never read by anyone, human or tool).
