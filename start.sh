#!/usr/bin/env bash
# One-command bridge startup. The token persists in
# ~/.config/nob-bridge/token, so you paste it into the sidepanel once ever.
set -u
PORT="${NOB_PORT:-8787}"
TOKEN_FILE="${NOB_TOKEN_FILE:-$HOME/.config/nob-bridge/token}"
DIR="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$(dirname "$TOKEN_FILE")"
chmod 700 "$(dirname "$TOKEN_FILE")" 2>/dev/null || true
if [ -s "$TOKEN_FILE" ]; then
  NOB_TOKEN="$(cat "$TOKEN_FILE")"
else
  NOB_TOKEN="$(openssl rand -hex 16)"
  printf '%s' "$NOB_TOKEN" > "$TOKEN_FILE"
  chmod 600 "$TOKEN_FILE"
fi
export NOB_TOKEN
export NOB_PORT="$PORT"
if curl -sf "http://127.0.0.1:$PORT/health" >/dev/null 2>&1; then
  echo "bridge already running on http://127.0.0.1:$PORT"
else
  nohup python3 "$DIR/server/bridge.py" >"$DIR/bridge.log" 2>&1 &
  sleep 1.5
fi
if curl -sf "http://127.0.0.1:$PORT/health"; then
  echo
else
  echo "FAILED to start - see $DIR/bridge.log"
  exit 1
fi
echo "token (paste once into the sidepanel, it remembers it): $NOB_TOKEN"
