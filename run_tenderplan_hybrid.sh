#!/usr/bin/env bash

set -euo pipefail

cd "$(dirname "$0")"

OUTPUT_DIR="${1:-/Users/alexchuguev/Documents/tenders/manual_downloads/tenderplan_candidates}"

.venv/bin/python prepare_tenderplan_batch.py "$OUTPUT_DIR"
. "${PWD}/.env" 2>/dev/null || true
: "${DOWNLOAD_PAUSE_MIN_SECONDS:=2.0}"
: "${DOWNLOAD_PAUSE_MAX_SECONDS:=4.5}"
: "${DOWNLOAD_MAX_TENDERS_TOTAL:=12}"
: "${DOWNLOAD_MAX_TENDERS_PER_HOST:=4}"
: "${DOWNLOAD_MAX_FILES_TOTAL:=40}"
: "${DOWNLOAD_MAX_FILES_PER_HOST:=12}"
: "${DOWNLOAD_MAX_BLOCKED_PER_HOST:=2}"
: "${DOWNLOAD_MAX_ERRORS_PER_HOST:=2}"
.venv/bin/python download_chrome_from_manifest.py "$OUTPUT_DIR"
