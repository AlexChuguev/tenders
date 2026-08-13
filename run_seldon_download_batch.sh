#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ $# -lt 2 ]]; then
  echo "Usage: bash $0 /path/to/prepared_folders /path/to/export.xls" >&2
  exit 1
fi

cd "$BASE_DIR"

PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python download_seldon_with_chrome.py "$1" "$2"
