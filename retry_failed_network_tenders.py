from __future__ import annotations

import csv
import json
import sys
from dataclasses import replace
from pathlib import Path

from tender_agent.config import Settings
from tender_agent.excel_loader import load_tenders
from tender_agent.local_review import (
    LocalTenderReviewer,
    _collect_files_from_prepared_folder,
)


def main() -> None:
    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    if len(sys.argv) >= 2:
        settings = replace(settings, local_files_dir=Path(sys.argv[1]).expanduser().resolve())
    if len(sys.argv) >= 3:
        settings = replace(settings, input_xls=Path(sys.argv[2]).expanduser().resolve())
    if len(sys.argv) >= 4:
        settings = replace(settings, output_xlsx=Path(sys.argv[3]).expanduser().resolve())

    reviewer = LocalTenderReviewer(settings)
    reviewer.sheet_writer.ensure_header()
    reviewer._ensure_llm_preflight()

    failures_path = reviewer.network_failures_path
    if not failures_path.exists():
        print(f"No network failure queue found: {failures_path}")
        return

    tenders = load_tenders(
        xls_path=settings.input_xls,
        url_column=settings.platform_tender_url_column,
        id_column=settings.platform_tender_id_column,
        title_column=settings.platform_tender_title_column,
    )
    tenders_by_id = {t.tender_id: t for t in tenders}
    queued = [
        json.loads(line)
        for line in failures_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    remaining: list[dict] = []
    for item in queued:
        tender = tenders_by_id.get(str(item.get("tender_id") or ""))
        if tender is None:
            continue
        files = _collect_files_from_prepared_folder(settings.local_files_dir / _find_folder_name(settings.local_files_dir, tender.tender_id))
        if not files:
            remaining.append(item)
            continue
        result = reviewer._process_one(tender, files, None)
        if result is not None:
            reviewer.sheet_writer.append_result(result)
        else:
            remaining.append(item)

    if remaining:
        failures_path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in remaining) + "\n",
            encoding="utf-8",
        )
    else:
        failures_path.unlink(missing_ok=True)


def _find_folder_name(root: Path, tender_id: str) -> str:
    manifest = root / "_manifest.csv"
    if manifest.exists():
        with manifest.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if str(row.get("tender_id") or "").strip() == tender_id:
                    return str(row.get("folder_name") or tender_id).strip()
    direct = root / tender_id
    if direct.exists():
        return tender_id
    for folder in root.iterdir():
        if folder.is_dir() and folder.name.startswith(tuple("0123456789")) and tender_id in folder.name:
            return folder.name
    return tender_id


if __name__ == "__main__":
    main()
