#!/usr/bin/env bash

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"

if [[ $# -lt 2 ]]; then
  echo "Usage: bash $0 /path/to/prepared_folders /path/to/export.xls [output_xlsx] [start_from]" >&2
  exit 1
fi

FOLDERS_DIR="$1"
XLS_PATH="$2"
OUTPUT_PATH="${3:-$BASE_DIR/tender_analysis.xlsx}"
START_FROM="${4:-}"

if [[ ! -d "$FOLDERS_DIR" ]]; then
  echo "Prepared folders directory not found: $FOLDERS_DIR" >&2
  exit 1
fi

if [[ ! -f "$XLS_PATH" ]]; then
  echo "Export file not found: $XLS_PATH" >&2
  exit 1
fi

cd "$BASE_DIR"

MANIFEST_PATH="$FOLDERS_DIR/_manifest.csv"
if [[ ! -f "$MANIFEST_PATH" ]]; then
  echo "_manifest.csv not found in $FOLDERS_DIR" >&2
  exit 1
fi

LOG_PATH="$FOLDERS_DIR/_safe_batch.log"
: > "$LOG_PATH"

python3 - <<'PY' "$MANIFEST_PATH" > /tmp/tenders_safe_batch_ids.txt
import csv
import sys
from pathlib import Path

manifest = Path(sys.argv[1])
with manifest.open(encoding="utf-8", newline="") as fh:
    reader = csv.DictReader(fh)
    for row in reader:
        folder_name = str(row.get("folder_name") or "").strip()
        if not folder_name:
            continue
        prefix = folder_name.split(". ", 1)[0].strip()
        if prefix.isdigit():
            print(prefix)
PY

TOTAL="$(wc -l < /tmp/tenders_safe_batch_ids.txt | tr -d ' ')"
INDEX=0
STARTED=0

while IFS= read -r TENDER_KEY; do
  [[ -z "$TENDER_KEY" ]] && continue
  if [[ -n "$START_FROM" && "$STARTED" -eq 0 ]]; then
    if [[ "$TENDER_KEY" != "$START_FROM" ]]; then
      continue
    fi
    STARTED=1
  fi
  INDEX=$((INDEX + 1))
  echo "[$INDEX/$TOTAL] review_one_tender $TENDER_KEY" | tee -a "$LOG_PATH"
  if SKIP_LLM_PREFLIGHT=1 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python review_one_tender.py "$FOLDERS_DIR" "$XLS_PATH" "$TENDER_KEY" "$OUTPUT_PATH" >> "$LOG_PATH" 2>&1; then
    echo "[$INDEX/$TOTAL] ok $TENDER_KEY" | tee -a "$LOG_PATH"
  else
    STATUS=$?
    case "$STATUS" in
      10)
        echo "[$INDEX/$TOTAL] skip_deadline $TENDER_KEY" | tee -a "$LOG_PATH"
        ;;
      11)
        echo "[$INDEX/$TOTAL] skip_no_files $TENDER_KEY" | tee -a "$LOG_PATH"
        ;;
      12)
        echo "[$INDEX/$TOTAL] network_failed $TENDER_KEY" | tee -a "$LOG_PATH"
        ;;
      *)
        echo "[$INDEX/$TOTAL] fail $TENDER_KEY exit=$STATUS" | tee -a "$LOG_PATH"
        ;;
    esac
  fi
done < /tmp/tenders_safe_batch_ids.txt

rm -f /tmp/tenders_safe_batch_ids.txt

PYTHONDONTWRITEBYTECODE=1 .venv/bin/python audit_batch_completeness.py \
  "$FOLDERS_DIR" \
  "$XLS_PATH" >> "$LOG_PATH" 2>&1 || true

echo "Done: $OUTPUT_PATH"
echo "Log: $LOG_PATH"
