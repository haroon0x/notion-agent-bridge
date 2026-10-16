#!/usr/bin/env bash
# Stop the locally running bridge.
if pkill -f 'server/bridge.py'; then
  echo "bridge stopped"
else
  echo "bridge was not running"
fi
