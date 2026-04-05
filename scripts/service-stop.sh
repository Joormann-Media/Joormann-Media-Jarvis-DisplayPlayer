#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME="joormann-media-jarvis-displayplayer.service"
sudo systemctl stop "$SERVICE_NAME"
sudo systemctl status "$SERVICE_NAME" --no-pager || true
