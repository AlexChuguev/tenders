from __future__ import annotations

import csv
import sys
from pathlib import Path

from tender_agent.excel_loader import load_tenders


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: .venv/bin/python prepare_folders.py /path/to/export.xls [output_dir]")

    xls_path = Path(sys.argv[1]).expanduser().resolve()
    if not xls_path.exists():
        raise SystemExit(f"XLS file not found: {xls_path}")

    base_dir = Path(__file__).resolve().parent
    output_root = (
        Path(sys.argv[2]).expanduser().resolve()
        if len(sys.argv) > 2
        else base_dir / "manual_downloads" / xls_path.stem
    )
    output_root.mkdir(parents=True, exist_ok=True)

    tenders = load_tenders(
        xls_path=xls_path,
        url_column="Ссылка",
        id_column="Номер",
        title_column="Наименование лота",
    )
    tenders.sort(key=lambda item: (item.deadline_at is None, item.deadline_at, item.title.lower()))

    manifest_rows: list[dict[str, str]] = []
    used_names: set[str] = set()
    for index, tender in enumerate(tenders, start=1):
        base_name = f"{index:03d}. {_sanitize_folder_name(tender.title)}"
        folder_name = _make_unique_folder_name(base_name, used_names)
        (output_root / folder_name).mkdir(exist_ok=True)
        manifest_rows.append(
            {
                "order": str(index),
                "tender_id": tender.tender_id,
                "title": tender.title,
                "folder_name": folder_name,
                "deadline_at": tender.deadline_at.strftime("%Y-%m-%d %H:%M") if tender.deadline_at else "",
                "url": tender.url,
            }
        )

    manifest_path = output_root / "_manifest.csv"
    with manifest_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["order", "tender_id", "title", "folder_name", "deadline_at", "url"],
        )
        writer.writeheader()
        writer.writerows(manifest_rows)

    print(f"Created {len(manifest_rows)} folders in {output_root}")
    print(f"Manifest: {manifest_path}")


def _sanitize_folder_name(value: str) -> str:
    sanitized = (
        value.replace("/", "／")
        .replace("\0", "")
        .strip()
        .rstrip(". ")
    )
    sanitized = _truncate_utf8(sanitized or "Без названия", max_bytes=180)
    return sanitized or "Без названия"


def _make_unique_folder_name(name: str, used_names: set[str]) -> str:
    candidate = name
    suffix = 2
    while candidate in used_names:
        candidate = f"{name} ({suffix})"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _truncate_utf8(value: str, max_bytes: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value

    ellipsis = "..."
    ellipsis_bytes = len(ellipsis.encode("utf-8"))
    trimmed = raw[: max_bytes - ellipsis_bytes]
    while trimmed:
        try:
            return trimmed.decode("utf-8").rstrip() + ellipsis
        except UnicodeDecodeError:
            trimmed = trimmed[:-1]
    return ellipsis


if __name__ == "__main__":
    main()
