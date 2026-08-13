#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date +%Y-%m-%d_%H.%M.%S)"
OUTPUT_DIR="${1:-$BASE_DIR/manual_downloads/seldon_api_$STAMP}"
OUTPUT_XLSX="${2:-$BASE_DIR/tender_analysis.xlsx}"
LOG_DIR="${LOCAL_PIPELINE_LOG_DIR:-$BASE_DIR/logs/local}"
LOG_FILE="$LOG_DIR/seldon_api_local_pipeline_$STAMP.log"
SUMMARY_FILE="$LOG_DIR/last_local_run_summary.txt"
LOCK_DIR="$BASE_DIR/.run_seldon_api_local_pipeline.lock"
VPN_SERVICE="${VPN_SERVICE:-VPN}"
LOCAL_SWITCH_VPN="${LOCAL_SWITCH_VPN:-true}"

mkdir -p "$LOG_DIR"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "[lock] local pipeline is already running: $LOCK_DIR" >&2
  exit 1
fi

cleanup() {
  rmdir "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT

exec > >(tee -a "$LOG_FILE") 2>&1

vpn_service_exists() {
  scutil --nc status "$VPN_SERVICE" >/dev/null 2>&1
}

vpn_start() {
  if [[ "$LOCAL_SWITCH_VPN" != "true" ]]; then
    return 0
  fi
  if vpn_service_exists; then
    echo "[vpn] connecting $VPN_SERVICE"
    scutil --nc start "$VPN_SERVICE" || true
  fi
}

vpn_stop() {
  if [[ "$LOCAL_SWITCH_VPN" != "true" ]]; then
    return 0
  fi
  if vpn_service_exists; then
    echo "[vpn] disconnecting $VPN_SERVICE"
    scutil --nc stop "$VPN_SERVICE" || true
  fi
}

cd "$BASE_DIR"

echo "[step] prepare batch"
bash ./run_seldon_api_prepare.sh "$OUTPUT_DIR"

MANIFEST_PATH="$OUTPUT_DIR/_manifest.csv"
if [[ ! -f "$MANIFEST_PATH" ]]; then
  echo "[error] manifest was not created: $MANIFEST_PATH" >&2
  exit 1
fi

MANIFEST_ROWS="$(python3 - <<PY
import csv
from pathlib import Path
path = Path("$MANIFEST_PATH")
with path.open(encoding="utf-8", newline="") as fh:
    reader = csv.reader(fh)
    rows = list(reader)
print(max(0, len(rows) - 1))
PY
)"

if [[ "$MANIFEST_ROWS" == "0" ]]; then
  echo "[skip] prepared batch is empty, download/review steps will not run"
  echo "[step] summarize batch"
  {
    echo "timestamp=$STAMP"
    .venv/bin/python summarize_seldon_api_batch.py "$OUTPUT_DIR" "$OUTPUT_XLSX"
  } | tee "$SUMMARY_FILE"
  echo "Done: $OUTPUT_XLSX"
  exit 0
fi

echo "[step] download documents"
vpn_stop
sleep 3
bash ./run_seldon_api_download_batch.sh "$OUTPUT_DIR"

echo "[step] review documents"
vpn_start
sleep 5
bash ./run_seldon_api_review_batch.sh "$OUTPUT_DIR" "$OUTPUT_XLSX"

echo "[step] summarize batch"
{
  echo "timestamp=$STAMP"
  .venv/bin/python summarize_seldon_api_batch.py "$OUTPUT_DIR" "$OUTPUT_XLSX"
} | tee "$SUMMARY_FILE"

echo "Done: $OUTPUT_XLSX"
