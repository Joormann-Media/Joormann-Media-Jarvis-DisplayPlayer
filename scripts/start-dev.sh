#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$PROJECT_ROOT/runtime/logs"
PID_FILE_RENDERER="$LOG_DIR/jarvis-displayplayer.pid"
PID_FILE_FLASK="$LOG_DIR/jarvis-displayplayer-flask.pid"
LOG_RENDERER="$LOG_DIR/jarvis-displayplayer.log"
LOG_FLASK="$LOG_DIR/jarvis-displayplayer-flask.log"
ENV_FILE="$PROJECT_ROOT/config/ports.env"

mkdir -p "$LOG_DIR"

# --- Virtuelle Umgebung ---
VENV_DIR="$PROJECT_ROOT/.venv"
PYTHON_BIN="$VENV_DIR/bin/python"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "Erstelle virtuelle Umgebung: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

if [[ -f "$PROJECT_ROOT/requirements.txt" ]]; then
  echo "Installiere/aktualisiere Requirements ..."
  "$PYTHON_BIN" -m pip install -q --upgrade pip
  "$PYTHON_BIN" -m pip install -q -r "$PROJECT_ROOT/requirements.txt"
fi

# --- Port & IP ---
FLASK_PORT=5092
if [[ -f "$ENV_FILE" ]]; then
  _port=$(grep -E '^FLASK_PORT=' "$ENV_FILE" | tail -1 | cut -d= -f2 | tr -d '[:space:]"')
  [[ -n "$_port" ]] && FLASK_PORT="$_port"
fi
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")

# --- Pygame-Renderer (run.py) ---
if [[ -f "$PID_FILE_RENDERER" ]]; then
  existing_pid="$(cat "$PID_FILE_RENDERER")"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "[Renderer] Bereits aktiv (PID $existing_pid) — übersprungen"
  else
    rm -f "$PID_FILE_RENDERER"
    (
      cd "$PROJECT_ROOT"
      nohup "$PYTHON_BIN" run.py >>"$LOG_RENDERER" 2>&1 &
      echo $! > "$PID_FILE_RENDERER"
    )
    sleep 1
    _pid="$(cat "$PID_FILE_RENDERER" 2>/dev/null || true)"
    if [[ -n "$_pid" ]] && kill -0 "$_pid" 2>/dev/null; then
      echo "[Renderer] Gestartet (PID $_pid)"
    else
      echo "[Renderer] Fehlgeschlagen — siehe Log: $LOG_RENDERER"
    fi
  fi
else
  (
    cd "$PROJECT_ROOT"
    nohup "$PYTHON_BIN" run.py >>"$LOG_RENDERER" 2>&1 &
    echo $! > "$PID_FILE_RENDERER"
  )
  sleep 1
  _pid="$(cat "$PID_FILE_RENDERER" 2>/dev/null || true)"
  if [[ -n "$_pid" ]] && kill -0 "$_pid" 2>/dev/null; then
    echo "[Renderer] Gestartet (PID $_pid)"
  else
    echo "[Renderer] Fehlgeschlagen — siehe Log: $LOG_RENDERER"
  fi
fi

# --- Flask Management-Server (app.py) ---
if [[ -f "$PID_FILE_FLASK" ]]; then
  existing_pid="$(cat "$PID_FILE_FLASK")"
  if [[ -n "$existing_pid" ]] && kill -0 "$existing_pid" 2>/dev/null; then
    echo "[Flask]    Bereits aktiv (PID $existing_pid) — übersprungen"
  else
    rm -f "$PID_FILE_FLASK"
    (
      cd "$PROJECT_ROOT"
      [[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a || true
      nohup "$PYTHON_BIN" app.py >>"$LOG_FLASK" 2>&1 &
      echo $! > "$PID_FILE_FLASK"
    )
    sleep 1
    _pid="$(cat "$PID_FILE_FLASK" 2>/dev/null || true)"
    if [[ -n "$_pid" ]] && kill -0 "$_pid" 2>/dev/null; then
      echo "[Flask]    Gestartet (PID $_pid)"
    else
      echo "[Flask]    Fehlgeschlagen — siehe Log: $LOG_FLASK"
    fi
  fi
else
  (
    cd "$PROJECT_ROOT"
    [[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a || true
    nohup "$PYTHON_BIN" app.py >>"$LOG_FLASK" 2>&1 &
    echo $! > "$PID_FILE_FLASK"
  )
  sleep 1
  _pid="$(cat "$PID_FILE_FLASK" 2>/dev/null || true)"
  if [[ -n "$_pid" ]] && kill -0 "$_pid" 2>/dev/null; then
    echo "[Flask]    Gestartet (PID $_pid)"
  else
    echo "[Flask]    Fehlgeschlagen — siehe Log: $LOG_FLASK"
  fi
fi

# --- Autodiscover (optional) ---
"$SCRIPT_DIR/autodiscover.sh" 2>/dev/null || true

# --- URL-Ausgabe ---
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  DisplayPlayer läuft"
echo ""
echo "  Dashboard:  http://${LOCAL_IP}:${FLASK_PORT}/"
echo "  Media:      http://${LOCAL_IP}:${FLASK_PORT}/media"
echo "  Link:       http://${LOCAL_IP}:${FLASK_PORT}/link"
echo "  API-Doku:   http://${LOCAL_IP}:${FLASK_PORT}/info"
echo "  Health:     http://${LOCAL_IP}:${FLASK_PORT}/health"
echo ""
echo "  Renderer-Log: $LOG_RENDERER"
echo "  Flask-Log:    $LOG_FLASK"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
