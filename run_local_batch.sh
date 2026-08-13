#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ $# -lt 2 ]]; then
  echo "Usage: bash $0 /path/to/prepared_folders /path/to/export.xls [output_xlsx]" >&2
  exit 1
fi

FOLDERS_DIR="$1"
XLS_PATH="$2"
OUTPUT_PATH="${3:-$BASE_DIR/tender_analysis.xlsx}"
RESET_OUTPUT="${RESET_OUTPUT:-0}"

if [[ ! -d "$FOLDERS_DIR" ]]; then
  echo "Prepared folders directory not found: $FOLDERS_DIR" >&2
  exit 1
fi

if [[ ! -f "$XLS_PATH" ]]; then
  echo "Export file not found: $XLS_PATH" >&2
  exit 1
fi

cd "$BASE_DIR"

if [[ "$RESET_OUTPUT" == "1" ]]; then
  rm -f "$OUTPUT_PATH"
fi

PYTHONDONTWRITEBYTECODE=1 \
MAX_FILES_PER_TENDER="${MAX_FILES_PER_TENDER:-4}" \
TENDER_SKIP=0 \
TENDER_LIMIT= \
OUTPUT_XLSX="$OUTPUT_PATH" \
.venv/bin/python review_local.py "$FOLDERS_DIR" "$XLS_PATH" "$OUTPUT_PATH"

echo "Done: $OUTPUT_PATH"
