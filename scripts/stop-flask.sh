#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PID_FILE="$PROJECT_ROOT/runtime/logs/jarvis-displayplayer-flask.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "Kein PID-File gefunden. Flask-Server läuft nicht."
  exit 0
fi

pid="$(cat "$PID_FILE")"
if [[ -z "$pid" ]]; then
  rm -f "$PID_FILE"
  exit 0
fi

if kill -0 "$pid" 2>/dev/null; then
  kill "$pid"
  sleep 1
  if kill -0 "$pid" 2>/dev/null; then
    kill -9 "$pid" 2>/dev/null || true
  fi
  echo "Gestoppt: DisplayPlayer Flask-Server (PID $pid)"
else
  echo "Prozess $pid läuft nicht mehr."
fi
rm -f "$PID_FILE"
