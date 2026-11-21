#!/bin/sh
# Writes the runtime API base URL into the static bundle, so the same image can
# point at a local or a deployed backend without rebuilding.
set -e
CONFIG_FILE=/usr/share/nginx/html/config.js
API_BASE_URL="${API_BASE_URL:-}"
echo "window.__APP_CONFIG__ = { API_BASE_URL: \"${API_BASE_URL}\" };" > "$CONFIG_FILE"
echo "[entrypoint] API_BASE_URL=${API_BASE_URL:-<build-time default>}"
