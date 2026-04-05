#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME="joormann-media-jarvis-displayplayer.service"
sudo systemctl enable "$SERVICE_NAME"
echo "Autostart aktiv: $SERVICE_NAME"
