from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

from tender_agent.config import Settings
from tender_agent.excel_loader import load_tenders
from tender_agent.local_review import (
    LocalTenderReviewer,
    _collect_files_from_prepared_folder,
)


def _env_flag(name: str) -> bool:
    import os

    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit(
            "Usage: .venv/bin/python review_one_tender.py /path/to/prepared_batch /path/to/export.xls <tender_id|folder_prefix> [output_xlsx]"
        )

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    settings = replace(settings, local_files_dir=Path(sys.argv[1]).expanduser().resolve())
    settings = replace(settings, input_xls=Path(sys.argv[2]).expanduser().resolve())
    target = sys.argv[3].strip()
    if len(sys.argv) >= 5:
        settings = replace(settings, output_xlsx=Path(sys.argv[4]).expanduser().resolve())

    reviewer = LocalTenderReviewer(settings)
    reviewer.sheet_writer.ensure_header()
    if not _env_flag("SKIP_LLM_PREFLIGHT"):
        reviewer._ensure_llm_preflight()

    tenders = load_tenders(
        xls_path=settings.input_xls,
        url_column=settings.platform_tender_url_column,
        id_column=settings.platform_tender_id_column,
        title_column=settings.platform_tender_title_column,
    )
    tender = _find_tender(tenders, settings.local_files_dir, target)
    if tender is None:
        raise SystemExit(f"Tender not found for target: {target}")

    folder = _find_folder(settings.local_files_dir, tender.tender_id, target)
    if folder is None:
        files = []
    else:
        files = _collect_files_from_prepared_folder(folder)
    stats = {
        "scope_total": 1,
        "written": 0,
        "skipped_existing_success": 0,
        "skipped_no_files": 0,
        "skipped_deadline": 0,
        "network_failed": 0,
    }
    result = reviewer._process_one(tender, files, None, stats)
    if result is None:
        if stats["skipped_deadline"]:
            print(f"Skip: {tender.tender_id} deadline passed")
            raise SystemExit(10)
        if stats["skipped_no_files"]:
            print(f"Skip: {tender.tender_id} no files")
            raise SystemExit(11)
        if stats["network_failed"]:
            print(f"Network failed: {tender.tender_id}")
            raise SystemExit(12)
        raise SystemExit("Tender was not written for an unknown reason.")
    reviewer.sheet_writer.append_result(result)
    if result.error == "deadline_passed":
        print(f"Skip: {tender.tender_id} deadline passed")
        raise SystemExit(10)
    if result.error == "no_files":
        print(f"Skip: {tender.tender_id} no files")
        raise SystemExit(11)
    if result.error == "network_error":
        print(f"Network failed: {tender.tender_id}")
        raise SystemExit(12)
    print(f"Done: {result.tender_id} -> {result.decision} {result.confidence_percent}%")


def _find_tender(tenders, root: Path, target: str):
    for tender in tenders:
        if tender.tender_id == target:
            return tender
    if target.isdigit():
        tender_id = _lookup_tender_id_from_manifest(root, target)
        if tender_id:
            for tender in tenders:
                if tender.tender_id == tender_id:
                    return tender
        prefix = f"{int(target):03d}."
        for folder in root.iterdir():
            if folder.is_dir() and folder.name.startswith(prefix):
                idx = int(target) - 1
                if 0 <= idx < len(tenders):
                    return tenders[idx]
    return None


def _find_folder(root: Path, tender_id: str, target: str) -> Path | None:
    direct = root / tender_id
    if direct.exists():
        return direct
    if target.isdigit():
        prefix = f"{int(target):03d}."
        for folder in root.iterdir():
            if folder.is_dir() and folder.name.startswith(prefix):
                return folder
    manifest = root / "_manifest.csv"
    if manifest.exists():
        import csv

        with manifest.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                if str(row.get("tender_id") or "").strip() == tender_id:
                    folder_name = str(row.get("folder_name") or "").strip()
                    if folder_name:
                        candidate = root / folder_name
                        if candidate.exists():
                            return candidate
    return None


def _lookup_tender_id_from_manifest(root: Path, target: str) -> str | None:
    manifest = root / "_manifest.csv"
    if not manifest.exists():
        return None
    import csv

    prefix = f"{int(target):03d}."
    with manifest.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            folder_name = str(row.get("folder_name") or "").strip()
            if folder_name.startswith(prefix):
                return str(row.get("tender_id") or "").strip() or None
    return None


if __name__ == "__main__":
    main()
