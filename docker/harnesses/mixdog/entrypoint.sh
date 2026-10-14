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

model="$(read_config model)"
export HOME="${HARNESSBENCH_STATE}/home"
export MIXDOG_HOME="${HARNESSBENCH_STATE}/mixdog"
export MIXDOG_DATA_DIR="${HARNESSBENCH_STATE}/mixdog/data"
export NODE_PATH="/usr/local/lib/node_modules"
export MIXDOG_TELEMETRY_MODE="DISABLED"
export MIXDOG_NO_DAEMON="1"
mkdir -p "${HOME}" "${MIXDOG_HOME}" "${MIXDOG_DATA_DIR}" "${HARNESSBENCH_TRACE}"
prompt="$(<"${HARNESSBENCH_PROMPT}")"

# Mixdog's transport policy allows plain HTTP only on loopback, and the
# per-trial model proxy listens on the host over the bridge gateway. Front it
# with a loopback TCP forwarder so the provider config stays HTTP on
# 127.0.0.1. Model credentials never appear in either hop: the proxy injects
# the upstream key itself.
# Parse the port from the rendered base URL (http://host.docker.internal:<port>).
proxy_port="$(python3 -c 'import os,sys; from urllib.parse import urlsplit; print(urlsplit(sys.argv[1]).port or (443 if urlsplit(sys.argv[1]).scheme=="https" else 80))' "$(read_config base_url)")"
node /opt/mixdog/port-forward.mjs "18443" "host.docker.internal" "${proxy_port}" &
forwarder_pid=$!
# Wait for the forwarder to accept connections before the turn starts.
for _ in $(seq 1 50); do
  if python3 -c "import socket,sys; s=socket.create_connection(('127.0.0.1', 18443), timeout=0.2); s.close()" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
if ! kill -0 "${forwarder_pid}" 2>/dev/null; then
  echo "[mixdog-harness] loopback forwarder failed to start" >&2
  exit 1
fi

# The proxy URL carries the per-trial path prefix and upstream path; strip the
# host:port (fronted by the forwarder) and keep the path.
BASE_URL_PATH="$(python3 -c 'import sys; from urllib.parse import urlsplit; u=urlsplit(sys.argv[1]); print("http://127.0.0.1:18443" + u.path.rstrip("/"))' "$(read_config base_url)")"

# The harness config carries the benchmark provider contract (base URL, model,
# context window, thinking effort); the launcher resolves the installed mixdog
# runtime and runs one headless exec turn against the per-trial model proxy.
set +e
MIXDOG_MODEL="${model}" \
MIXDOG_BASE_URL="${BASE_URL_PATH}" \
MIXDOG_CONTEXT_WINDOW="$(read_config context_window)" \
MIXDOG_MAX_TOKENS="$(read_config max_tokens)" \
MIXDOG_EFFORT="$(read_config thinking)" \
MIXDOG_MAX_TURNS="$(read_config max_turns)" \
node /opt/mixdog/harness-launch.mjs "${prompt}" \
  > >(tee "${HARNESSBENCH_TRACE}/events.jsonl") \
  2> >(tee "${HARNESSBENCH_TRACE}/stderr.log" >&2)
status=$?
set -e
kill "${forwarder_pid}" 2>/dev/null || true
# Remove offline npm dependencies before the trusted workspace safety check.
rm -rf -- "${HARNESSBENCH_WORKSPACE}/web/node_modules" \
  2>/dev/null || true
chmod -R a+rX "${HARNESSBENCH_WORKSPACE}" "${HARNESSBENCH_TRACE}" \
  2>/dev/null || true
exit "${status}"