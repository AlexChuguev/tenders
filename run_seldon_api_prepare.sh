#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="${1:-$BASE_DIR/manual_downloads/seldon_api_$(date +%Y-%m-%d_%H.%M.%S)}"

cd "$BASE_DIR"
.venv/bin/python prepare_seldon_api_batch.py "$OUTPUT_DIR"

echo "Done: $OUTPUT_DIR"
