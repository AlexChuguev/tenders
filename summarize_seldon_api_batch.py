from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: .venv/bin/python summarize_seldon_api_batch.py /path/to/prepared_api_batch [output_xlsx]")

    batch_dir = Path(sys.argv[1]).expanduser().resolve()
    output_xlsx = Path(sys.argv[2]).expanduser().resolve() if len(sys.argv) >= 3 else None
    manifest_path = batch_dir / "_manifest.csv"
    if not batch_dir.exists():
        raise SystemExit(f"Batch directory not found: {batch_dir}")
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")

    rows = []
    with manifest_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            folder_name = str(row.get("folder_name", "")).strip()
            title = str(row.get("title", "")).strip()
            order = str(row.get("order", "")).strip()
            if folder_name:
                rows.append((order, title, folder_name))

    status_by_folder: dict[str, dict[str, str]] = {}
    status_path = batch_dir / "_download_status.csv"
    if status_path.exists():
        with status_path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                folder_name = str(row.get("folder_name", "")).strip()
                if folder_name:
                    status_by_folder[folder_name] = {key: str(value or "").strip() for key, value in row.items()}

    total = len(rows)
    with_files = 0
    empty = []
    failed = []
    for order, title, folder_name in rows:
        folder = batch_dir / folder_name
        has_files = _has_valid_files(folder)
        if has_files:
            with_files += 1
        else:
            empty.append((order, title))
            status = status_by_folder.get(folder_name)
            if status:
                failed.append((order, title, status.get("status", ""), status.get("reason", "")))

    print(f"batch_dir={batch_dir}")
    print(f"manifest={manifest_path}")
    print(f"total_tenders={total}")
    print(f"folders_with_files={with_files}")
    print(f"folders_without_files={len(empty)}")
    if output_xlsx is not None:
        print(f"output_xlsx={output_xlsx}")
        print(f"output_xlsx_exists={output_xlsx.exists()}")
    if empty:
        print("empty_folders:")
        for order, title in empty[:20]:
            print(f"- {order}: {title}")
    if failed:
        print("failed_downloads:")
        for order, title, status, reason in failed[:20]:
            line = f"- {order}: {title} [{status}]"
            if reason:
                line += f" {reason}"
            print(line)


def _has_valid_files(folder: Path) -> bool:
    if not folder.exists():
        return False
    return any(_is_valid_file(path) for path in folder.rglob("*"))


def _is_valid_file(path: Path) -> bool:
    if not path.is_file() or path.name.startswith("."):
        return False
    try:
        body = path.read_bytes()
    except Exception:
        return False
    if not body:
        return False
    return not _looks_like_error_json(body)


def _looks_like_error_json(body: bytes) -> bool:
    stripped = body.lstrip()
    if not (stripped.startswith(b"{") or stripped.startswith(b"[")):
        return False
    try:
        payload = json.loads(body.decode("utf-8", errors="ignore"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    if isinstance(payload.get("error"), dict):
        return True
    status = payload.get("status")
    if isinstance(status, dict):
        try:
            return int(status.get("code") or 0) >= 400
        except Exception:
            return True
    return False


if __name__ == "__main__":
    main()
