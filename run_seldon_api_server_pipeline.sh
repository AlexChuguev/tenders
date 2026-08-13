#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date +%Y-%m-%d_%H.%M.%S)"
OUTPUT_DIR="${1:-$BASE_DIR/manual_downloads/seldon_api_$STAMP}"
OUTPUT_XLSX="${2:-$BASE_DIR/tender_analysis.xlsx}"
LOG_DIR="${LOG_DIR:-$BASE_DIR/logs}"
LOG_FILE="$LOG_DIR/seldon_api_pipeline_$STAMP.log"
SUMMARY_FILE="$LOG_DIR/last_run_summary.txt"
export SELDON_PREPARE_LOOKBACK_DAYS="${SELDON_PREPARE_LOOKBACK_DAYS:-30}"
export SELDON_PREPARE_POLL_ATTEMPTS="${SELDON_PREPARE_POLL_ATTEMPTS:-120}"
export SELDON_PREPARE_POLL_SECONDS="${SELDON_PREPARE_POLL_SECONDS:-5}"
export SELDON_PREPARE_RESULTS_PAGE_LIMIT="${SELDON_PREPARE_RESULTS_PAGE_LIMIT:-120}"
export SELDON_PREPARE_TASK_RETRIES="${SELDON_PREPARE_TASK_RETRIES:-3}"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

cd "$BASE_DIR"

echo "[step] prepare batch"
bash ./run_seldon_api_prepare.sh "$OUTPUT_DIR"

echo "[step] download documents"
bash ./run_seldon_api_download_batch.sh "$OUTPUT_DIR"

echo "[step] review documents"
bash ./run_seldon_api_review_batch.sh "$OUTPUT_DIR" "$OUTPUT_XLSX"

echo "[step] summarize batch"
{
  echo "timestamp=$STAMP"
  .venv/bin/python summarize_seldon_api_batch.py "$OUTPUT_DIR" "$OUTPUT_XLSX"
} | tee "$SUMMARY_FILE"

echo "Done: $OUTPUT_XLSX"
