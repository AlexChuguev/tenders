from __future__ import annotations

import sys
from pathlib import Path

from tender_agent.export.excel_writer import ExcelWriter


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: .venv/bin/python sort_tender_analysis.py /path/to/tender_analysis.xlsx")
        return 2

    workbook_path = Path(sys.argv[1]).expanduser().resolve()
    if not workbook_path.exists():
        print(f"Excel not found: {workbook_path}")
        return 2

    writer = ExcelWriter(output_path=workbook_path, preserve_status=True)
    writer.ensure_header()
    print(f"Sorted workbook: {workbook_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
