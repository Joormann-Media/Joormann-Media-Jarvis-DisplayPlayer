#!/usr/bin/env bash
set -euo pipefail
SERVICE_NAME="joormann-media-jarvis-displayplayer.service"
systemctl status "$SERVICE_NAME" --no-pager
