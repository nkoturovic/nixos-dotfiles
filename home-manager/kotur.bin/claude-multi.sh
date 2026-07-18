CLAUDE_MULTI_VERSION="1.2.12"
CLAUDE_BIN="/home/kotur/.local/bin/claude"
CONFIG_DIR="/home/kotur/.config/claude-multi"
KEY_FILE="${CONFIG_DIR}/api-key"
VALIDATION_SESSION_FILE="${CONFIG_DIR}/validation-session-id"
PLUGIN_DIR="@CLAUDE_MULTI_PLUGIN_DIR@"
SETTINGS_FILE="@CLAUDE_MULTI_SETTINGS_FILE@"
BASE_URL="http://127.0.0.1:8317"

usage() {
  cat <<'EOF'
Usage: claude-multi [PROFILE] [--validation] [CLAUDE_ARGS...]

Runs the unchanged Claude Code executable through the local claude-multi gateway.

Profiles for fresh sessions:
  fable       Fable 5 + orchestrator + ultracode (default)
  kimi        Kimi K3 1M + orchestrator + ultracode
  opus        Opus 4.8 + orchestrator + ultracode
  sol         Sol high + orchestrator + ultracode (xhigh available explicitly)
  conserve-opus  Opus 4.8 + orchestrator + ultracode (Fable conservation mode)
  conserve-kimi  Kimi K3 1M + orchestrator + ultracode (Fable conservation mode)

Wrapper options:
  --validation  Reject --continue and permit --resume only for the allowlisted scratch session ID
  -h, --help    Show this help without contacting the proxy
  -v, --version Show the launcher version

By default, substantive tasks run bounded multi-agent Dynamic workflows
(orchestrator + sol/kimi/fable/opus specialist agents); small requests stay single-model.

An explicit profile on --resume/--continue applies that profile. Cross-provider
Fable↔Kimi/Sol resumes are unsafe; prefer a fresh session with a plaintext handoff.
Always continue/resume a claude-multi session with the same profile
(e.g. `claude-multi opus -c`). Old pre-claude-multi sessions with native
Fable/Opus model IDs also resume through the gateway; plain `claude` remains
the fully-native path (claude.ai connectors, first-party fidelity).

Initialize the runtime first with: claude-multi-proxy init
EOF
}

die() {
  printf 'claude-multi: %s\n' "$*" >&2
  exit 1
}

validation_mode=false
profile=""
profile_explicit=false
while (($# > 0)); do
  case "$1" in
    fable|kimi|opus|sol|conserve-opus|conserve-kimi)
      [[ "$profile_explicit" == false ]] || die "only one profile may be selected"
      profile="$1"
      profile_explicit=true
      shift
      ;;
    --validation)
      validation_mode=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -v|--version)
      printf 'claude-multi %s\n' "$CLAUDE_MULTI_VERSION"
      exit 0
      ;;
    *)
      break
      ;;
  esac
done

args=("$@")
has_model=false
has_agent=false
has_effort=false
is_resume=false
is_continue=false
resume_id=""

caller_options=()
caller_positionals=()
separator_found=false

for ((i = 0; i < ${#args[@]}; i++)); do
  arg="${args[$i]}"
  if [[ "$separator_found" == false && "$arg" == "--" ]]; then
    separator_found=true
    continue
  fi
  if [[ "$separator_found" == false ]]; then
    caller_options+=("$arg")
    case "$arg" in
      --model|--model=*)
        has_model=true
        ;;
      --agent|--agent=*)
        has_agent=true
        ;;
      --effort|--effort=*)
        has_effort=true
        ;;
      -c|--continue|--continue=*)
        is_continue=true
        ;;
      -r|--resume)
        is_resume=true
        if ((i + 1 < ${#args[@]})) && [[ "${args[$((i + 1))]}" != -* ]]; then
          resume_id="${args[$((i + 1))]}"
        fi
        ;;
      --resume=*)
        is_resume=true
        resume_id="${arg#--resume=}"
        ;;
    esac
  else
    caller_positionals+=("$arg")
  fi
done

if [[ "$validation_mode" == true ]]; then
  [[ "$is_continue" == false ]] || die "--continue is disabled in validation mode"
  if [[ "$is_resume" == true ]]; then
    [[ -n "$resume_id" ]] || die "validation resume requires an explicit session ID"
    [[ -f "$VALIDATION_SESSION_FILE" && ! -L "$VALIDATION_SESSION_FILE" ]] || die "validation session allowlist is not initialized"
    allowed_session=""
    IFS= read -r allowed_session < "$VALIDATION_SESSION_FILE" || true
    [[ -n "$allowed_session" && "$resume_id" == "$allowed_session" ]] || die "resume session is not the allowlisted validation session"
  fi
fi

[[ -x "$CLAUDE_BIN" ]] || die "Claude executable not found at $CLAUDE_BIN"
[[ -d "$PLUGIN_DIR" ]] || die "launcher plugin is unavailable"
[[ -f "$SETTINGS_FILE" ]] || die "launcher settings are unavailable"
[[ -f "$KEY_FILE" && ! -L "$KEY_FILE" && -r "$KEY_FILE" ]] || die "runtime is not initialized; run claude-multi-proxy init"

api_key=""
IFS= read -r api_key < "$KEY_FILE" || true
[[ "$api_key" =~ ^[0-9a-f]{64}$ ]] || die "local API key file is invalid"

curl --fail --silent --max-time 1 "${BASE_URL}/healthz" >/dev/null || die "local proxy is not running"

max_context_tokens=272000
auto_compact_env=()
conservation_args=()
defaults=()
if [[ "$profile_explicit" == true || ("$is_resume" == false && "$is_continue" == false) ]]; then
  [[ -n "$profile" ]] || profile="fable"
  case "$profile" in
    fable)
      profile_model="claude-fable-5[1m]"
      profile_effort="ultracode"
      ;;
    kimi)
      profile_model="claude-multi-kimi-k3[1m]"
      profile_effort="ultracode"
      max_context_tokens=1048576
      auto_compact_env=(CLAUDE_CODE_AUTO_COMPACT_WINDOW=1048576)
      ;;
    opus)
      profile_model="claude-multi-opus-4-8[1m]"
      profile_effort="ultracode"
      ;;
    sol)
      profile_model="gpt-multi-sol-high"
      profile_effort="ultracode"
      max_context_tokens=372000
      ;;
    conserve-opus)
      profile_model="claude-multi-opus-4-8[1m]"
      profile_effort="ultracode"
      conservation_args=(--disallowedTools "Agent(claude-multi:fable-specialist)" --append-system-prompt-file "$PLUGIN_DIR/fable-conservation-prompt.md")
      ;;
    conserve-kimi)
      profile_model="claude-multi-kimi-k3[1m]"
      profile_effort="ultracode"
      max_context_tokens=1048576
      auto_compact_env=(CLAUDE_CODE_AUTO_COMPACT_WINDOW=1048576)
      conservation_args=(--disallowedTools "Agent(claude-multi:fable-specialist)" --append-system-prompt-file "$PLUGIN_DIR/fable-conservation-prompt.md")
      ;;
  esac
  [[ "$has_model" == true ]] || defaults+=(--model "$profile_model")
  [[ "$has_agent" == true ]] || defaults+=(--agent claude-multi:orchestrator)
  [[ "$has_effort" == true ]] || defaults+=(--effort "$profile_effort")
fi

if [[ "$profile_explicit" == true && ("$is_resume" == true || "$is_continue" == true) ]]; then
  printf 'claude-multi: warning: resuming with an explicit profile forces that model/agent onto the saved transcript. Cross-provider resumes may drop hidden-thinking history; prefer a fresh session with a plaintext handoff, or add --fork-session to keep the original session untouched.\n' >&2
fi

final_argv=("${defaults[@]}" "${caller_options[@]}" "${conservation_args[@]}")
if [[ "$separator_found" == true ]]; then
  final_argv+=("--")
fi
final_argv+=("${caller_positionals[@]}")

# Sol uses its normal 372K OAuth context window and Claude Code's derived
# auto-compaction default; direct-API 1M is not configured.
exec env \
  -u CLAUDE_CODE_AUTO_COMPACT_WINDOW \
  -u CLAUDE_CODE_MAX_OUTPUT_TOKENS \
  ANTHROPIC_BASE_URL="$BASE_URL" \
  ANTHROPIC_AUTH_TOKEN="$api_key" \
  ANTHROPIC_DEFAULT_FABLE_MODEL="claude-fable-5[1m]" \
  ANTHROPIC_DEFAULT_OPUS_MODEL="claude-opus-4-8[1m]" \
  CLAUDE_MULTI_GATEWAY=1 \
  CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY=1 \
  CLAUDE_CODE_MAX_CONTEXT_TOKENS="$max_context_tokens" \
  "${auto_compact_env[@]}" \
  "$CLAUDE_BIN" \
  --settings "$SETTINGS_FILE" \
  --plugin-dir "$PLUGIN_DIR" \
  "${final_argv[@]}"
