from __future__ import annotations

from pathlib import Path
from typing import Callable
import json


def build_input_file_report(
    *,
    all_files: list[Path],
    selected_files: list[Path],
    role_resolver: Callable[[Path], str],
) -> dict[str, object]:
    selected = {path.resolve() for path in selected_files}
    rows: list[dict[str, object]] = []
    for path in sorted(all_files, key=lambda item: str(item).lower()):
        resolved = path.resolve()
        role = role_resolver(path)
        rows.append(
            {
                "path": str(path),
                "name": path.name,
                "suffix": path.suffix.lower(),
                "size_bytes": _safe_size(path),
                "role": role,
                "selected_for_analysis": resolved in selected,
                "selection_note": _selection_note(path=path, role=role, selected=resolved in selected),
            }
        )
    return {
        "total_files": len(all_files),
        "selected_files": len(selected_files),
        "files": rows,
    }


def build_extraction_report(
    *,
    source_files: list[Path],
    prepared_files: list[Path],
    metadata_by_prepared: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    metadata_by_prepared = metadata_by_prepared or {}
    rows: list[dict[str, object]] = []
    for source, prepared in zip(source_files, prepared_files):
        text = _safe_read_text(prepared)
        metadata = metadata_by_prepared.get(str(prepared.resolve()), {})
        row = {
            "source_path": str(source),
            "source_name": source.name,
            "source_suffix": source.suffix.lower(),
            "source_size_bytes": _safe_size(source),
            "prepared_path": str(prepared),
            "prepared_name": prepared.name,
            "converter": metadata.get("converter") or _converter_name(source),
            "cache_hit": bool(metadata.get("cache_hit")) or "state/extraction_cache" in str(prepared),
            "text_chars": len(text),
            "non_whitespace_chars": sum(1 for char in text if not char.isspace()),
            "raw_text_chars": int(metadata.get("raw_text_chars") or len(text) or 0),
            "truncated": "[truncated]" in text,
            "status": _text_status(text),
        }
        for key in (
            "pages_total",
            "pages_read",
            "sheets_total",
            "sheets_read",
            "ocr_attempted",
            "cached_to",
            "cached_from",
        ):
            if key in metadata:
                row[key] = metadata[key]
        if row["truncated"]:
            row["loss_note"] = "text_truncated_before_llm"
        if row.get("pages_total") and row.get("pages_read") and row["pages_read"] < row["pages_total"]:
            row["loss_note"] = "partial_pdf_read"
        rows.append(row)
    return {
        "total_files": len(source_files),
        "prepared_files": len(prepared_files),
        "files": rows,
    }


def _selection_note(*, path: Path, role: str, selected: bool) -> str:
    if selected:
        return "selected"
    suffix = path.suffix.lower()
    if suffix in {".rar", ".7z"} and not (path.parent / f"{path.stem}__extracted" / ".done").exists():
        return "archive_not_extracted:no_supported_extractor_or_extract_failed"
    if suffix == ".zip" and not (path.parent / f"{path.stem}__extracted" / ".done").exists():
        return "archive_not_extracted:extract_failed"
    if role == "exclude":
        return "excluded_by_role"
    return "not_selected_by_limit_or_priority"


def _text_status(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "empty_text"
    if len(stripped) < 200:
        return "very_short_text"
    return "ok"


def _converter_name(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "docx_xml"
    if suffix == ".doc":
        return "textutil"
    if suffix == ".xlsx":
        return "openpyxl"
    if suffix == ".pdf":
        return "pypdf_pdftotext"
    if suffix in {".rar", ".7z"}:
        return "archive_external"
    if suffix == ".zip":
        return "zip"
    return "native_text"


def _safe_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def write_batch_extraction_summary(batch_artifact_dir: Path) -> Path:
    rows: list[dict[str, object]] = []
    for path in sorted(batch_artifact_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        result = payload.get("result") or {}
        extra = payload.get("extra") or {}
        analysis = payload.get("analysis") or {}
        extraction = analysis.get("extraction_report") or extra.get("extraction_report") or {}
        input_report = extra.get("input_file_report") or {}
        issues = _extract_summary_issues(extraction, input_report)
        if not issues:
            continue
        rows.append(
            {
                "artifact": path.name,
                "tender_id": str(result.get("tender_id") or ""),
                "title": str(result.get("title") or ""),
                "url": str(result.get("url") or ""),
                "error": str(result.get("error") or ""),
                "issues": issues,
            }
        )
    out = batch_artifact_dir / "_extraction_summary.json"
    out.write_text(
        json.dumps(
            {
                "problem_tenders": len(rows),
                "items": rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def _extract_summary_issues(extraction: object, input_report: object) -> list[str]:
    issues: list[str] = []
    if isinstance(input_report, dict):
        for row in input_report.get("files") or []:
            if not isinstance(row, dict):
                continue
            note = str(row.get("selection_note") or "")
            if note.startswith("archive_not_extracted"):
                issues.append(f"{row.get('name')}: {note}")
    if isinstance(extraction, dict):
        for row in extraction.get("files") or []:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status") or "")
            if status in {"empty_text", "very_short_text"}:
                issues.append(f"{row.get('source_name')}: {status}")
            if row.get("truncated"):
                issues.append(f"{row.get('source_name')}: truncated")
            if row.get("loss_note"):
                issues.append(f"{row.get('source_name')}: {row.get('loss_note')}")
        budget = extraction.get("llm_budget_report")
        if isinstance(budget, dict):
            for row in budget.get("dropped_files") or []:
                if isinstance(row, dict):
                    issues.append(f"{row.get('name')}: dropped_by_llm_budget")
            for row in budget.get("empty_files") or []:
                if isinstance(row, dict):
                    issues.append(f"{row.get('name')}: empty_llm_chunk")
    return _dedupe_issues(issues)


def _dedupe_issues(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
