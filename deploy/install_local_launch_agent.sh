#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_ID="com.alexchuguev.tenders.local"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/$AGENT_ID.plist"
LOG_DIR="${LOCAL_PIPELINE_LOG_DIR:-$BASE_DIR/logs/local}"
STDOUT_LOG="$LOG_DIR/launchd.stdout.log"
STDERR_LOG="$LOG_DIR/launchd.stderr.log"
OUTPUT_XLSX="${1:-$BASE_DIR/tender_analysis.xlsx}"

mkdir -p "$PLIST_DIR" "$LOG_DIR"

cat > "$PLIST_PATH" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$AGENT_ID</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd "$BASE_DIR" &amp;&amp; bash ./run_seldon_api_local_daemon.sh "$OUTPUT_XLSX"</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$STDOUT_LOG</string>
  <key>StandardErrorPath</key>
  <string>$STDERR_LOG</string>
  <key>WorkingDirectory</key>
  <string>$BASE_DIR</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl load "$PLIST_PATH"

echo "Installed launch agent: $PLIST_PATH"
echo "stdout: $STDOUT_LOG"
echo "stderr: $STDERR_LOG"
