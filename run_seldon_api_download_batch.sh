#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ $# -lt 1 ]]; then
  echo "Usage: bash $0 /path/to/prepared_folders [export.xls]" >&2
  exit 1
fi

FOLDERS_DIR="$1"
XLS_PATH="${2:-}"

if [[ ! -d "$FOLDERS_DIR" ]]; then
  echo "Prepared folders directory not found: $FOLDERS_DIR" >&2
  exit 1
fi

if [[ -n "$XLS_PATH" && ! -f "$XLS_PATH" ]]; then
  echo "Export file not found: $XLS_PATH" >&2
  exit 1
fi

cd "$BASE_DIR"
if [[ -n "$XLS_PATH" ]]; then
  .venv/bin/python download_seldon_api_documents.py "$FOLDERS_DIR" "$XLS_PATH"
else
  .venv/bin/python download_seldon_api_documents.py "$FOLDERS_DIR"
fi
