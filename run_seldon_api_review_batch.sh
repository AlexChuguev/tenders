#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

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

cd "$BASE_DIR"
OUTPUT_XLSX="$OUTPUT_PATH" .venv/bin/python review_seldon_api_batch.py "$BATCH_DIR" "$OUTPUT_PATH"
