CLAUDE_MULTI_PROXY_VERSION="1.1.0"
STATE_DIR="/home/kotur/.local/share/claude-multi"
AUTH_DIR="${STATE_DIR}/auth"
TRACE_DIR="${STATE_DIR}/traces"
CONFIG_DIR="/home/kotur/.config/claude-multi"
KEY_FILE="${CONFIG_DIR}/api-key"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"
VALIDATION_SESSION_FILE="${CONFIG_DIR}/validation-session-id"
CONFIG_TEMPLATE="@CLAUDE_MULTI_CONFIG_TEMPLATE@"
KIMI_ENV_FILE="/home/kotur/.config/secrets/claude.env"
PROXY_BIN="@CLI_PROXY_API_BIN@"
HEALTH_URL="http://127.0.0.1:8317/healthz"

usage() {
  cat <<'EOF'
Usage: claude-multi-proxy COMMAND [ARG]

Commands:
  init                              Create private runtime directories, key, and config
  status                            Report initialization and proxy health without secrets
  run                               Initialize and run the proxy in the foreground
  allow-validation-resume UUID      Allow one exact scratch session ID in validation mode
  clear-validation-resume           Clear the validation resume allowlist
  claude-login                      Start a fresh proxy-owned Claude OAuth flow
  codex-device-login                Start a fresh proxy-owned Codex device flow
  -h, --help                        Show this help
  -v, --version                     Show the control script version
EOF
}

die() {
  printf 'claude-multi-proxy: %s\n' "$*" >&2
  exit 1
}

ensure_private_directory() {
  local directory="$1"
  if [[ -e "$directory" && (! -d "$directory" || -L "$directory") ]]; then
    die "unsafe runtime path: $directory"
  fi
  install -d -m 0700 "$directory"
}

read_kimi_api_key() {
  [[ -f "$KIMI_ENV_FILE" && ! -L "$KIMI_ENV_FILE" ]] || die "Kimi credential file is unavailable"
  [[ "$(stat -c '%a' "$KIMI_ENV_FILE")" == "600" ]] || die "Kimi credential file must have mode 0600"

  kimi_api_key=""
  local assignment_count=0
  local line value
  while IFS= read -r line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?KIMI_CLAUDE_API_KEY[[:space:]]*= ]]; then
      [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?KIMI_CLAUDE_API_KEY[[:space:]]*=[[:space:]]*(.*)[[:space:]]*$ ]] || die "invalid Kimi credential assignment"
      ((assignment_count += 1))
      [[ "$assignment_count" -eq 1 ]] || die "Kimi credential must be assigned exactly once"
      value="${BASH_REMATCH[2]}"
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"
      if [[ "$value" == \'* ]]; then
        [[ ${#value} -ge 2 && "${value: -1}" == "'" ]] || die "invalid Kimi credential quoting"
        value="${value:1:${#value}-2}"
      elif [[ "$value" == \"* ]]; then
        [[ ${#value} -ge 2 && "${value: -1}" == '"' ]] || die "invalid Kimi credential quoting"
        value="${value:1:${#value}-2}"
      fi
      [[ -n "$value" && "$value" =~ ^[A-Za-z0-9._~+/=@:-]+$ ]] || die "Kimi credential contains unsupported characters"
      kimi_api_key="$value"
    fi
  done < "$KIMI_ENV_FILE"
  [[ "$assignment_count" -eq 1 && -n "$kimi_api_key" ]] || die "Kimi credential assignment is missing"
}

initialize_runtime() {
  umask 077

  ensure_private_directory "$STATE_DIR"
  ensure_private_directory "$AUTH_DIR"
  ensure_private_directory "$TRACE_DIR"
  ensure_private_directory "$CONFIG_DIR"

  [[ -f "$CONFIG_TEMPLATE" && ! -L "$CONFIG_TEMPLATE" ]] || die "configuration template is unavailable"
  [[ ! -e "$KEY_FILE" || (-f "$KEY_FILE" && ! -L "$KEY_FILE") ]] || die "unsafe API key path"
  [[ ! -e "$CONFIG_FILE" || (-f "$CONFIG_FILE" && ! -L "$CONFIG_FILE") ]] || die "unsafe config path"
  [[ ! -e "$VALIDATION_SESSION_FILE" || (-f "$VALIDATION_SESSION_FILE" && ! -L "$VALIDATION_SESSION_FILE") ]] || die "unsafe validation allowlist path"

  if [[ ! -e "$KEY_FILE" ]]; then
    local temporary_key
    temporary_key=$(mktemp "${CONFIG_DIR}/.api-key.XXXXXX")
    if ! openssl rand -hex 32 > "$temporary_key"; then
      rm -f "$temporary_key"
      die "failed to generate local API key"
    fi
    chmod 0600 "$temporary_key"
    if [[ -e "$KEY_FILE" ]]; then
      rm -f "$temporary_key"
    else
      mv "$temporary_key" "$KEY_FILE"
    fi
  fi
  chmod 0600 "$KEY_FILE"

  local api_key
  api_key=""
  IFS= read -r api_key < "$KEY_FILE" || true
  [[ "$api_key" =~ ^[0-9a-f]{64}$ ]] || die "local API key file is invalid"

  local kimi_api_key
  read_kimi_api_key

  local local_placeholder_count kimi_placeholder_count
  local_placeholder_count=$(grep -o '__CLAUDE_MULTI_API_KEY__' "$CONFIG_TEMPLATE" | wc -l)
  kimi_placeholder_count=$(grep -o '__KIMI_CLAUDE_API_KEY__' "$CONFIG_TEMPLATE" | wc -l)
  [[ "$local_placeholder_count" -eq 1 ]] || die "configuration template must contain exactly one local key placeholder"
  [[ "$kimi_placeholder_count" -eq 1 ]] || die "configuration template must contain exactly one Kimi key placeholder"

  local temporary_config
  temporary_config=$(mktemp "${CONFIG_DIR}/.config.yaml.XXXXXX")
  if ! while IFS= read -r template_line || [[ -n "$template_line" ]]; do
    template_line="${template_line//__CLAUDE_MULTI_API_KEY__/$api_key}"
    printf '%s\n' "${template_line//__KIMI_CLAUDE_API_KEY__/$kimi_api_key}"
  done < "$CONFIG_TEMPLATE" > "$temporary_config"; then
    rm -f "$temporary_config"
    die "failed to render runtime configuration"
  fi
  chmod 0600 "$temporary_config"
  mv -f "$temporary_config" "$CONFIG_FILE"

  if [[ ! -e "$VALIDATION_SESSION_FILE" ]]; then
    install -m 0600 /dev/null "$VALIDATION_SESSION_FILE"
  fi
  chmod 0600 "$VALIDATION_SESSION_FILE"
}

show_status() {
  if [[ -d "$STATE_DIR" && -d "$AUTH_DIR" && -d "$TRACE_DIR" && -d "$CONFIG_DIR" && -f "$KEY_FILE" && -f "$CONFIG_FILE" ]]; then
    printf 'runtime: initialized\n'
  else
    printf 'runtime: not initialized\n'
  fi

  if curl --fail --silent --max-time 1 "$HEALTH_URL" >/dev/null; then
    printf 'proxy: running\n'
  else
    printf 'proxy: stopped\n'
  fi
}

set_validation_session() {
  local session_id="${1:-}"
  [[ "$session_id" =~ ^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$ ]] || die "validation session ID must be a UUID"
  initialize_runtime
  local temporary_allowlist
  temporary_allowlist=$(mktemp "${CONFIG_DIR}/.validation-session-id.XXXXXX")
  printf '%s\n' "$session_id" > "$temporary_allowlist"
  chmod 0600 "$temporary_allowlist"
  mv -f "$temporary_allowlist" "$VALIDATION_SESSION_FILE"
  printf 'validation resume allowlist updated\n'
}

clear_validation_session() {
  initialize_runtime
  install -m 0600 /dev/null "$VALIDATION_SESSION_FILE"
  printf 'validation resume allowlist cleared\n'
}

command="${1:-}"
case "$command" in
  init)
    initialize_runtime
    printf 'claude-multi runtime initialized\n'
    ;;
  status)
    show_status
    ;;
  run)
    initialize_runtime
    exec "$PROXY_BIN" --config "$CONFIG_FILE" --local-model
    ;;
  allow-validation-resume)
    set_validation_session "${2:-}"
    ;;
  clear-validation-resume)
    clear_validation_session
    ;;
  claude-login)
    initialize_runtime
    exec "$PROXY_BIN" --config "$CONFIG_FILE" --local-model --claude-login
    ;;
  codex-device-login)
    initialize_runtime
    exec "$PROXY_BIN" --config "$CONFIG_FILE" --local-model --codex-device-login
    ;;
  -h|--help|help)
    usage
    ;;
  -v|--version|version)
    printf 'claude-multi-proxy %s\n' "$CLAUDE_MULTI_PROXY_VERSION"
    ;;
  "")
    usage >&2
    exit 2
    ;;
  *)
    die "unknown command: $command"
    ;;
esac
