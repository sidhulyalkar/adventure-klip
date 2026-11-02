#!/bin/sh
set -eu
LABEL=dev.adventure-intelligence.card-import
FILE="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ ! -f "$FILE" ]; then
  echo "LaunchAgent not installed"
  exit 0
fi
launchctl bootout "gui/$(id -u)/$LABEL" || true
rm "$FILE"
echo "Uninstalled $LABEL (no footage or catalogs removed)"
