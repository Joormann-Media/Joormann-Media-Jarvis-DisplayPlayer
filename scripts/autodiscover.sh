#!/usr/bin/env bash
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CONFIG_DIR="$PROJECT_ROOT/config"
PORTS_ENV_FILE="${JARVIS_PORTS_FILE:-$CONFIG_DIR/ports.env}"
PORTS_LOCAL_FILE="${JARVIS_PORTS_LOCAL_FILE:-$CONFIG_DIR/ports.local.env}"

if [[ -f "$PORTS_ENV_FILE" ]]; then
  set -a; source "$PORTS_ENV_FILE"; set +a
fi
if [[ -f "$PORTS_LOCAL_FILE" ]]; then
  set -a; source "$PORTS_LOCAL_FILE"; set +a
fi

PORTAL_AUTODISCOVER_URL="${PORTAL_AUTODISCOVER_URL:-${AUTODISCOVER_URL:-}}"
if [[ -z "$PORTAL_AUTODISCOVER_URL" ]]; then
  PORTAL_HOST="${PORTAL_HOST:-127.0.0.1}"
  PORTAL_PORT="${PORTAL_PORT:-5070}"
  PORTAL_AUTODISCOVER_URL="http://${PORTAL_HOST}:${PORTAL_PORT}/autodiscover"
fi

if ! command -v curl >/dev/null 2>&1; then
  exit 0
fi

repo_name="$(basename "$PROJECT_ROOT")"
repo_link=""
if command -v git >/dev/null 2>&1; then
  repo_link="$(git -C "$PROJECT_ROOT" remote get-url origin 2>/dev/null || true)"
fi
if [[ "$repo_link" =~ ^git@github.com:(.+)$ ]]; then
  repo_link="https://github.com/${BASH_REMATCH[1]}"
fi
if [[ "$repo_link" =~ \.git$ ]]; then
  :
elif [[ -n "$repo_link" ]]; then
  repo_link="${repo_link}.git"
fi

service_name="${AUTODISCOVER_SERVICE_NAME:-${JARVIS_SERVICE_NAME:-}}"
service_user="${AUTODISCOVER_SERVICE_USER:-${USER:-}}"
install_dir="${AUTODISCOVER_INSTALL_DIR:-$PROJECT_ROOT}"

service_port="${AUTODISCOVER_PORT:-${FLASK_PORT:-${CORE_API_PORT:-${SERVICE_PORT:-${PORT:-}}}}}"
lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
host_ip="${AUTODISCOVER_HOST_IP:-${lan_ip:-127.0.0.1}}"

api_base_url="${AUTODISCOVER_API_BASE_URL:-}"
if [[ -z "$api_base_url" && -n "$service_port" ]]; then
  api_base_url="http://${host_ip}:${service_port}"
fi
health_url="${AUTODISCOVER_HEALTH_URL:-}"
if [[ -z "$health_url" && -n "$api_base_url" ]]; then
  health_url="${api_base_url}/health"
fi

node_name="${AUTODISCOVER_NODE_NAME:-$(hostname 2>/dev/null || true)}"
instance_id="${AUTODISCOVER_INSTANCE_ID:-${node_name}-${repo_name}}"

payload="$(python3 - <<PY
import json
print(json.dumps({
  "repo_name": ${repo_name@Q},
  "repo_link": ${repo_link@Q},
  "repo_branch": "main",
  "install_dir": ${install_dir@Q},
  "service_name": ${service_name@Q},
  "service_user": ${service_user@Q},
  "service_port": int(${service_port@Q}) if str(${service_port@Q}).isdigit() else None,
  "api_base_url": ${api_base_url@Q},
  "health_url": ${health_url@Q},
  "hostname": ${node_name@Q},
  "node_name": ${node_name@Q},
  "instance_id": ${instance_id@Q},
  "tags": ["jarvis", "autodiscover"],
}))
PY
)"

curl -fsS --max-time 4 --connect-timeout 2 \
  -H "Content-Type: application/json" \
  -X POST "$PORTAL_AUTODISCOVER_URL" \
  --data-binary "$payload" >/dev/null 2>&1 || true
