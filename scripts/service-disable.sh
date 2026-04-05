#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME="joormann-media-jarvis-displayplayer.service"
sudo systemctl disable "$SERVICE_NAME"
echo "Autostart deaktiviert: $SERVICE_NAME"
