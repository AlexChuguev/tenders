from __future__ import annotations

import sys
from pathlib import Path

from tender_agent.batch_audit import audit_batch, write_batch_audit


def main() -> int:
    if len(sys.argv) < 3:
        print(
            "Usage: .venv/bin/python audit_batch_completeness.py "
            "/path/to/prepared_batch /path/to/export.xls [artifacts_dir]"
        )
        return 2

    batch_dir = Path(sys.argv[1]).expanduser().resolve()
    xls_path = Path(sys.argv[2]).expanduser().resolve()
    artifacts_dir = Path(sys.argv[3]).expanduser().resolve() if len(sys.argv) > 3 else None

    if not batch_dir.exists():
        print(f"Batch dir not found: {batch_dir}")
        return 2
    if not xls_path.exists():
        print(f"XLS not found: {xls_path}")
        return 2

    base = Path("/Users/alexchuguev/Documents/tenders")
    excel_path = base / "tender_analysis.xlsx"
    from tender_agent.config import Settings

    settings = Settings.load(base)
    artifacts_root = artifacts_dir or settings.artifact_dir
    report = audit_batch(
        batch_dir=batch_dir,
        xls_path=xls_path,
        artifacts_root=artifacts_root,
        excel_path=excel_path if excel_path.exists() else None,
    )
    csv_path, json_path = write_batch_audit(report, batch_dir=batch_dir)
    issues = [row for row in report.rows if row.issue]
    print(f"CSV: {csv_path}")
    print(f"JSON: {json_path}")
    print(f"Health: {report.health}")
    print(f"XLS rows: {report.xls_rows}")
    print(f"Folders found: {report.folders_found}")
    print(f"Folders with files: {report.folders_with_files}")
    print(f"Artifacts written: {report.artifacts_written}")
    print(f"Excel rows: {report.excel_rows}")
    print(f"Issues: {report.issues_total}")
    for row in issues[:20]:
        print(f"{row.row_number}: {row.issue} {row.tender_id} {row.title}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
