from __future__ import annotations

import csv
import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from tender_agent.analysis import TenderAnalyzer
from tender_agent.artifact_writer import ArtifactWriter
from tender_agent.config import Settings
from tender_agent.excel_writer import ExcelWriter
from tender_agent.extraction_report import build_input_file_report, write_batch_extraction_summary
from tender_agent.evidence import (
    build_fact_columns,
    build_requirement_fact_columns,
    render_summary_points_with_evidence,
)
from tender_agent.local_review import (
    _analysis_role_with_content,
    _collect_files_from_prepared_folder,
    _expand_archives,
    _mark_analysis_as_technical_failure,
    _select_analysis_files,
)
from tender_agent.models import TenderAnalysisResult
from tender_agent.seldon_added_at import (
    NullSeldonAddedAtResolver,
    SeldonAddedAtResolver,
    should_resolve_seldon_added_at,
)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: .venv/bin/python review_seldon_api_batch.py /path/to/prepared_api_batch [output_xlsx]"
        )

    base_dir = Path(__file__).resolve().parent
    settings = Settings.load(base_dir)
    batch_dir = Path(sys.argv[1]).expanduser().resolve()
    output_xlsx = Path(sys.argv[2]).expanduser().resolve() if len(sys.argv) >= 3 else settings.output_xlsx
    settings = replace(settings, output_xlsx=output_xlsx)

    manifest_path = batch_dir / "_manifest.csv"
    if not batch_dir.exists():
        raise SystemExit(f"Prepared batch directory not found: {batch_dir}")
    if not manifest_path.exists():
        raise SystemExit(f"Manifest not found: {manifest_path}")

    analyzer = TenderAnalyzer(
        provider_name=settings.llm_provider,
        api_key=settings.llm_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        prompt_template_path=settings.prompt_template_path,
        max_chars_per_file=settings.analysis_max_chars_per_file,
        triage_policy_path=settings.triage_policy_path,
    )
    writer = ExcelWriter(output_path=output_xlsx)
    artifact_writer = ArtifactWriter(
        root=settings.artifact_dir,
        source="review_seldon_api_batch",
        batch_name=batch_dir.name,
        policy_metadata=analyzer.policy_metadata,
    )
    writer.ensure_header()
    stats = {
        "scope_total": 0,
        "written": 0,
        "skipped_deadline": 0,
        "skipped_no_files": 0,
        "network_failed": 0,
        "llm_failed": 0,
    }

    _expand_archives(batch_dir)
    rows = _load_manifest_rows(manifest_path)
    resolver_cm = (
        SeldonAddedAtResolver(settings)
        if should_resolve_seldon_added_at(settings, (row["url"] for row in rows))
        else NullSeldonAddedAtResolver()
    )
    with resolver_cm as added_at_resolver:
        for row in rows:
            stats["scope_total"] += 1
            deadline_at = _parse_deadline(row["deadline_at"])
            if deadline_at and deadline_at < datetime.now():
                result = _technical_result(
                    row=row,
                    batch_dir=batch_dir,
                    deadline_at=deadline_at,
                    error="deadline_passed",
                    summary=(
                        "1. Риски: тендер не анализировался, потому что дедлайн уже прошёл\n"
                        "2. Стек: не определён\n"
                        "3. Требования к контрагенту: не определены\n"
                        "4. Документация: анализ не выполнялся\n"
                        "5. Оплата и обеспечение: не определены"
                    ),
                    analysis_markdown="Технический статус: дедлайн подачи уже прошёл на момент обработки.",
                    seldon_added_at=added_at_resolver.get(row["url"]),
                )
                artifact_writer.write(result=result, extra={"manifest_row": row, "input_file_report": [], "extraction_report": {}})
                writer.append_result(result)
                stats["skipped_deadline"] += 1
                stats["written"] += 1
                continue
            folder = batch_dir / row["folder_name"]
            all_files = [
                path
                for path in _collect_files_from_prepared_folder(folder)
                if _is_valid_review_file(path)
            ]
            files = _select_analysis_files(all_files, settings.max_files_per_tender)
            input_file_report = build_input_file_report(
                all_files=all_files,
                selected_files=files,
                role_resolver=_analysis_role_with_content,
            )
            if not files:
                result = _technical_result(
                    row=row,
                    batch_dir=batch_dir,
                    deadline_at=deadline_at,
                    error="no_files",
                    summary="Файлы не добавлены\nОжидает загрузки документов",
                    analysis_markdown="Технический статус: файлы не добавлены в папку тендера.",
                    seldon_added_at=added_at_resolver.get(row["url"]),
                )
                artifact_writer.write(result=result, extra={"manifest_row": row, "input_file_report": input_file_report, "extraction_report": {}})
                writer.append_result(result)
                stats["skipped_no_files"] += 1
                stats["written"] += 1
                continue

            analysis = analyzer.analyze_with_context(
                tender_url=row["url"],
                files=files,
                tender_title=row["title"],
                tender_price_rub=_parse_price(row.get("price_rub", "")),
            )
            facts_stack, facts_requirements, facts_docs, facts_payment = build_fact_columns(analysis.facts)
            req_sro, req_turnover, req_analog, req_legacy, req_roles, req_licenses = build_requirement_fact_columns(
                analysis.facts
            )
            summary_text = "\n".join(
                render_summary_points_with_evidence(
                    facts=analysis.facts,
                    decision=analysis.decision,
                    files=files,
                    deadline_at=deadline_at,
                    summary_points=analysis.summary_points,
                )
            ) if analysis.facts else "\n".join(analysis.summary_points)
            result = TenderAnalysisResult(
                tender_id=row["tender_id"],
                title=row["title"],
                url=row["url"],
                batch_name=batch_dir.name,
                batch_path=str(batch_dir),
                deadline_at=deadline_at,
                nmck_rub=_parse_price(row.get("price_rub", "")),
                customer=row["customer"],
                customer_inn=row["customer_inn"],
                decision=analysis.decision,
                confidence_percent=analysis.confidence_percent,
                summary_text=summary_text,
                downloaded_files=[str(path) for path in files],
                analysis_markdown=analysis.analysis_markdown,
                document_completeness=analysis.completeness_label,
                seldon_added_at=added_at_resolver.get(row["url"]),
                error=analysis.error_type,
                facts_stack=facts_stack,
                facts_requirements=facts_requirements,
                facts_docs=facts_docs,
                facts_payment=facts_payment,
                facts_req_sro=req_sro,
                facts_req_turnover=req_turnover,
                facts_req_analog_projects=req_analog,
                facts_req_legacy_experience=req_legacy,
                facts_req_roles=req_roles,
                facts_req_licenses=req_licenses,
                reason_codes=list((analysis.facts.reason_codes if analysis.facts else [])),
            )
            if analysis.error_type in {"network_error", "llm_error"}:
                result.decision = "Техсбой LLM"
                result.confidence_percent = 0
                result.summary_text = "Техсбой LLM\nТребуется повторный прогон анализа"
                if analysis.error_type == "network_error":
                    stats["network_failed"] += 1
                    markdown = "Технический статус: сетевой сбой при обращении к LLM."
                else:
                    stats["llm_failed"] += 1
                    markdown = "Технический статус: ошибка LLM при анализе документов."
                result.analysis_markdown = markdown
                _mark_analysis_as_technical_failure(analysis, markdown=markdown)
            artifact_writer.write(
                result=result,
                analysis=analysis,
                extra={
                    "manifest_row": row,
                    "input_file_report": input_file_report,
                    "extraction_report": analysis.extraction_report,
                },
            )
            writer.append_result(result)
            stats["written"] += 1

    (artifact_writer.batch_dir / "_batch_report.json").write_text(
        json.dumps(
            {
                "written_at": datetime.now().isoformat(timespec="seconds"),
                "batch_name": batch_dir.name,
                "source": "review_seldon_api_batch",
                **stats,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_batch_extraction_summary(artifact_writer.batch_dir)

    print(f"Done: {output_xlsx}")


def _technical_result(
    *,
    row: dict[str, str],
    batch_dir: Path,
    deadline_at,
    error: str,
    summary: str,
    analysis_markdown: str,
    seldon_added_at,
) -> TenderAnalysisResult:
    return TenderAnalysisResult(
        tender_id=row["tender_id"],
        title=row["title"],
        url=row["url"],
        batch_name=batch_dir.name,
        batch_path=str(batch_dir),
        deadline_at=deadline_at,
        nmck_rub=_parse_price(row.get("price_rub", "")),
        customer=row["customer"],
        customer_inn=row["customer_inn"],
        decision="Уточнить",
        confidence_percent=0,
        summary_text=summary,
        downloaded_files=[],
        analysis_markdown=analysis_markdown,
        document_completeness="",
        error=error,
        seldon_added_at=seldon_added_at,
        facts_stack="",
        facts_requirements="",
        facts_docs="",
        facts_payment="",
    )


def _is_valid_review_file(path: Path) -> bool:
    if not path.is_file() or path.name.startswith("."):
        return False
    try:
        body = path.read_bytes()
    except Exception:
        return False
    if not body:
        return False
    if len(body) < 200 and _looks_like_error_json(body):
        return False
    if _looks_like_error_json(body):
        return False
    return True


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
    error = payload.get("error")
    if isinstance(error, dict):
        return True
    status = payload.get("status")
    if isinstance(status, dict):
        try:
            return int(status.get("code") or 0) >= 400
        except Exception:
            return True
    return False


def _load_manifest_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            folder_name = str(row.get("folder_name", "")).strip()
            tender_id = str(row.get("tender_id", "")).strip()
            title = str(row.get("title", "")).strip()
            if not folder_name or not tender_id or not title:
                continue
            rows.append(
                {
                    "tender_id": tender_id,
                    "title": title,
                    "folder_name": folder_name,
                    "deadline_at": str(row.get("deadline_at", "")).strip(),
                    "url": str(row.get("url", "")).strip(),
                    "customer": str(row.get("customer", "")).strip(),
                    "customer_inn": str(row.get("customer_inn", "")).strip(),
                    "price_rub": str(
                        row.get("price_rub")
                        or row.get("max_price")
                        or row.get("price")
                        or row.get("nmck")
                        or ""
                    ).strip(),
                }
            )
    return rows


def _parse_deadline(value: str):
    raw = value.strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            from datetime import datetime

            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_price(value: str) -> float | None:
    raw = value.strip()
    if not raw:
        return None
    normalized = raw.replace("\xa0", " ")
    normalized = "".join(ch for ch in normalized if ch.isdigit() or ch in ",. ")
    normalized = normalized.replace(" ", "")
    if not normalized:
        return None
    if "," in normalized and "." in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")
    else:
        parts = normalized.split(".")
        if len(parts) > 2:
            normalized = "".join(parts)
    try:
        return float(normalized)
    except ValueError:
        return None


if __name__ == "__main__":
    main()
