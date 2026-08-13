#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
INTERVAL_SECONDS="${LOCAL_PIPELINE_INTERVAL_SECONDS:-1800}"
OUTPUT_XLSX="${1:-$BASE_DIR/tender_analysis.xlsx}"

cd "$BASE_DIR"

while true; do
  bash ./run_seldon_api_local_pipeline.sh "" "$OUTPUT_XLSX" || true
  sleep "$INTERVAL_SECONDS"
done
