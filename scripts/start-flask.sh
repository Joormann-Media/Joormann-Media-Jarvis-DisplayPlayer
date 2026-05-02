#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/runtime/logs"
PID_FILE="$LOG_DIR/jarvis-displayplayer-flask.pid"
LOG_FILE="$LOG_DIR/jarvis-displayplayer-flask.log"
ENV_FILE="$PROJECT_ROOT/config/ports.env"

mkdir -p "$LOG_DIR"

VENV_DIR="$PROJECT_ROOT/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Erstelle virtuelle Umgebung: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
  echo "Installiere/aktualisiere Requirements ..."
  "$PYTHON_BIN" -m pip install -q --upgrade pip
  "$PYTHON_BIN" -m pip install -q -r "$PROJECT_ROOT/requirements.txt"
fi

if [[ -f "$PID_FILE" ]]; then
  existing_pid="$(cat "$PID_FILE")"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "Flask-Server bereits aktiv (PID $existing_pid)"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

# Load env vars
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

(
  cd "$PROJECT_ROOT"
  nohup "$PYTHON_BIN" app.py >>"$LOG_FILE" 2>&1 &
  echo $! > "$PID_FILE"
)

sleep 1
pid="$(cat "$PID_FILE")"
if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
  echo "Gestartet: DisplayPlayer Flask-Server (PID $pid) auf Port ${FLASK_PORT:-5092}"
  echo "Log: $LOG_FILE"
else
  rm -f "$PID_FILE"
  echo "Fehlgeschlagen. Siehe Log: $LOG_FILE"
  exit 1
fi
