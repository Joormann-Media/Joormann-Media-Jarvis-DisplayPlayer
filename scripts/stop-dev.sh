#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/runtime/logs"

_stop_pid() {
  local label="$1"
  local pid_file="$2"
  if [[ ! -f "$pid_file" ]]; then
    echo "[$label] Kein PID-File — nicht gestartet"
    return
  fi
  local pid
  pid="$(cat "$pid_file")"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    sleep 1
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
    fi
    echo "[$label] Gestoppt (PID $pid)"
  else
    echo "[$label] Verwaiste PID-Datei entfernt"
  fi
  rm -f "$pid_file"
}

_stop_pid "Flask"    "$LOG_DIR/jarvis-displayplayer-flask.pid"
_stop_pid "Renderer" "$LOG_DIR/jarvis-displayplayer.pid"
