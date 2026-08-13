#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
VPN_SERVICE="${VPN_SERVICE:-VPN}"

if [[ $# -lt 1 ]]; then
  echo "Usage: bash $0 /path/to/prepared_api_batch [output_xlsx]" >&2
  exit 1
fi

BATCH_DIR="$1"
OUTPUT_PATH="${2:-$BASE_DIR/tender_analysis.xlsx}"

if [[ ! -d "$BATCH_DIR" ]]; then
  echo "Prepared batch directory not found: $BATCH_DIR" >&2
  exit 1
fi

restore_vpn() {
  if scutil --nc status "$VPN_SERVICE" >/dev/null 2>&1; then
    local status
    status="$(scutil --nc status "$VPN_SERVICE" | head -n 1 || true)"
    if [[ "$status" != "Connected" ]]; then
      echo "[vpn] connecting $VPN_SERVICE"
      scutil --nc start "$VPN_SERVICE" || true
    fi
  fi
}

trap restore_vpn EXIT

cd "$BASE_DIR"

echo "[vpn] disconnecting $VPN_SERVICE for document download"
scutil --nc stop "$VPN_SERVICE" || true
sleep 3

echo "[step] downloading documents"
bash ./run_seldon_api_download_batch.sh "$BATCH_DIR"

echo "[vpn] connecting $VPN_SERVICE for analysis"
scutil --nc start "$VPN_SERVICE"
sleep 5

echo "[step] reviewing documents"
bash ./run_seldon_api_review_batch.sh "$BATCH_DIR" "$OUTPUT_PATH"

echo "Done: $OUTPUT_PATH"
