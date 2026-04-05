#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="joormann-media-jarvis-displayplayer.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

if systemctl list-unit-files "$SERVICE_NAME" --no-pager >/dev/null 2>&1; then
  sudo systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
fi

if [[ -f "$SERVICE_PATH" ]]; then
  sudo rm -f "$SERVICE_PATH"
fi

sudo systemctl daemon-reload
sudo systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true

echo "Deinstalliert: $SERVICE_NAME"
