#!/usr/bin/env bash
set -euo pipefail

: "${HARNESSBENCH_PROMPT:?}"
: "${HARNESSBENCH_CONFIG:?}"
: "${HARNESSBENCH_WORKSPACE:?}"
: "${HARNESSBENCH_STATE:?}"
: "${HARNESSBENCH_TRACE:?}"

read_config() {
  python3 -c \
    'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))[sys.argv[2]])' \
    "${HARNESSBENCH_CONFIG}" "$1"
}

export HOME="${HARNESSBENCH_STATE}/home"
export HARNESSBENCH_BASE_URL="$(read_config base_url)"
export HARNESSBENCH_MODEL="$(read_config model)"
export HARNESSBENCH_EFFORT="$(read_config thinking)"

AGY_CONFIG_DIR="${HOME}/.gemini/antigravity-cli"
export TMPDIR="${HARNESSBENCH_STATE}/tmp"
mkdir -p "${HOME}" "${TMPDIR}" "${HARNESSBENCH_TRACE}" "${AGY_CONFIG_DIR}"

# 1. Stage agy permissions settings (deny read_url only) and modelProvider configuration
cat << 'EOF' > "${AGY_CONFIG_DIR}/settings.json"
{
  "modelProvider": "gemini",
  "permissions": {
    "deny": [
      "read_url(*)"
    ]
  }
}
EOF

export GEMINI_API_KEY="harnessbench-eval-key"
export GOOGLE_GEMINI_BASE_URL="http://127.0.0.1:8080"

cat << 'EOF' > "${HOME}/.gemini/google_accounts.json"
{
  "active": "eval@harnessbench.internal",
  "old": []
}
EOF

cat << 'EOF' > "${HOME}/.gemini/oauth_creds.json"
{
  "access_token": "harnessbench-mock-token",
  "scope": "https://www.googleapis.com/auth/cloud-platform",
  "token_type": "Bearer",
  "id_token": "mock-id-token",
  "expiry_date": 253402300799000,
  "refresh_token": "mock-refresh-token"
}
EOF
cp "${HOME}/.gemini/oauth_creds.json" "${AGY_CONFIG_DIR}/oauth_creds.json" 2>/dev/null || true
cp "${HOME}/.gemini/google_accounts.json" "${AGY_CONFIG_DIR}/google_accounts.json" 2>/dev/null || true

# Mirror to /home/node in case agy queries getpwuid
mkdir -p /home/node/.gemini/antigravity-cli 2>/dev/null || true
cp -r "${HOME}/.gemini/"* /home/node/.gemini/ 2>/dev/null || true
cp "${AGY_CONFIG_DIR}/settings.json" /home/node/.gemini/antigravity-cli/settings.json 2>/dev/null || true

# 2. Configure and start LiteLLM Proxy on 127.0.0.1:8080
cat << EOF > "${AGY_CONFIG_DIR}/litellm_config.yaml"
model_list:
  - model_name: gemini-target
    litellm_params:
      model: openai/${HARNESSBENCH_MODEL}
      api_base: ${HARNESSBENCH_BASE_URL}
      api_key: local-vllm-no-auth

router_settings:
  model_group_alias:
    "gemini-3.1-pro-preview": "gemini-target"
    "gemini-3.1-flash-lite-preview": "gemini-target"
    "gemini-3.8-flash": "gemini-target"
    "gemini-3.8-pro": "gemini-target"
    "gemini-2.5-pro": "gemini-target"
    "gemini-2.5-flash": "gemini-target"
    "gemini-1.5-pro": "gemini-target"
    "gemini-1.5-flash": "gemini-target"

general_settings:
  master_key: "harnessbench-eval-key"
EOF

/opt/litellm-venv/bin/litellm \
  --config "${AGY_CONFIG_DIR}/litellm_config.yaml" \
  --port 8080 \
  --host 127.0.0.1 \
  --telemetry False \
  >> "${HARNESSBENCH_TRACE}/litellm.log" 2>&1 &
LITELLM_PID=$!

# Wait for LiteLLM readiness
for _ in {1..60}; do
  if python3 -c "import socket; s = socket.socket(); s.settimeout(0.2); s.connect(('127.0.0.1', 8080)); s.close()" 2>/dev/null; then
    break
  fi
  sleep 0.1
done

prompt="$(<"${HARNESSBENCH_PROMPT}")"

effort_args=()
if [[ "${HARNESSBENCH_MODEL}" == *"thinking"* ]]; then
  case "${HARNESSBENCH_EFFORT}" in
    low|medium|high|xhigh|max)
      effort_args=("--effort" "${HARNESSBENCH_EFFORT}")
      ;;
  esac
fi

cd "${HARNESSBENCH_WORKSPACE}"

# 3. Run agy CLI in headless non-interactive print mode
set +e
agy --print "${prompt}" \
    --dangerously-skip-permissions \
    --disable-slash-commands \
    "${effort_args[@]}" \
    --output-format stream-json \
    > >(tee "${HARNESSBENCH_TRACE}/events.jsonl") \
    2> >(tee "${HARNESSBENCH_TRACE}/stderr.log" >&2)
status=$?
set -e

# 4. Quiesce background LiteLLM proxy
kill -TERM "${LITELLM_PID}" 2>/dev/null || true

# Remove offline npm dependencies before the trusted workspace safety check.
rm -rf -- "${HARNESSBENCH_WORKSPACE}/web/node_modules" \
  2>/dev/null || true
chmod -R a+rX "${HARNESSBENCH_WORKSPACE}" "${HARNESSBENCH_TRACE}" \
  2>/dev/null || true

exit "${status}"
