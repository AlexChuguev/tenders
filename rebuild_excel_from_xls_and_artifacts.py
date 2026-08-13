from __future__ import annotations

import argparse
import re
from pathlib import Path

from tender_agent.artifact_reader import iter_artifacts, unique_latest_results
from tender_agent.config import Settings
from tender_agent.excel_loader import load_tenders
from tender_agent.export.excel_writer import ExcelWriter
from tender_agent.models import TenderAnalysisResult
from tender_agent.rerender import apply_rerender_to_result, rerender_artifact_result


DEADLINE_SUMMARY = (
    "1. Риски: тендер не анализировался, потому что дедлайн уже прошёл\n"
    "2. Стек: не определён\n"
    "3. Требования к контрагенту: не определены\n"
    "4. Документация: анализ не выполнялся\n"
    "5. Оплата и обеспечение: не определены"
)

NO_FILES_SUMMARY = "Файлы не добавлены\nОжидает загрузки документов"


def _parse_safe_batch_log(path: Path) -> tuple[set[str], set[str], set[int], set[int]]:
    no_files: set[str] = set()
    deadline: set[str] = set()
    no_files_rows: set[int] = set()
    deadline_rows: set[int] = set()
    if not path.exists():
        return no_files, deadline, no_files_rows, deadline_rows
    pattern = re.compile(r"Skip:\s+(\S+)\s+(deadline passed|no files)")
    row_pattern = re.compile(r"skip_(deadline|no_files)\s+(\d{1,4})\b", re.IGNORECASE)
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = pattern.search(line)
        if not match:
            row_match = row_pattern.search(line)
            if not row_match:
                continue
            reason = row_match.group(1).lower()
            try:
                row_number = int(row_match.group(2))
            except ValueError:
                continue
            if reason == "no_files":
                no_files_rows.add(row_number)
            elif reason == "deadline":
                deadline_rows.add(row_number)
            continue
        tender_id = match.group(1).strip()
        reason = match.group(2)
        if reason == "no files":
            no_files.add(tender_id)
        elif reason == "deadline passed":
            deadline.add(tender_id)
    return no_files, deadline, no_files_rows, deadline_rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild tender_analysis.xlsx from XLS + artifacts (preserve XLS order)."
    )
    parser.add_argument("source_xls", help="Path to Seldon XLS export.")
    parser.add_argument("output_xlsx", help="Output Excel file path.")
    parser.add_argument(
        "--artifacts-dir",
        dest="artifacts_dir",
        help="Override artifacts root directory.",
    )
    parser.add_argument(
        "--batch",
        dest="batch_name",
        help="Filter artifacts by batch name.",
    )
    parser.add_argument(
        "--safe-log",
        dest="safe_log",
        help="Path to _safe_batch.log for no_files/deadline statuses.",
    )
    parser.add_argument(
        "--preserve-status",
        action="store_true",
        help="Do not overwrite status values during rebuild.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace existing output file instead of appending.",
    )
    args = parser.parse_args()

    base = Path("/Users/alexchuguev/Documents/tenders")
    settings = Settings.load(base)

    source_xls = Path(args.source_xls).expanduser().resolve()
    output_xlsx = Path(args.output_xlsx).expanduser().resolve()
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve() if args.artifacts_dir else settings.artifact_dir
    batch_name = args.batch_name
    safe_log = Path(args.safe_log).expanduser().resolve() if args.safe_log else None
    batch_label = batch_name or source_xls.stem
    batch_path = base / "manual_downloads" / batch_label

    tenders = load_tenders(
        xls_path=source_xls,
        url_column=settings.platform_tender_url_column,
        id_column=settings.platform_tender_id_column,
        title_column=settings.platform_tender_title_column,
    )

    artifacts = iter_artifacts(artifacts_dir, source="local_review", batch_name=batch_name)
    results = unique_latest_results(artifacts)
    by_url = {result.url.strip(): result for result in results if result.url}
    by_id = {result.tender_id.strip(): result for result in results if result.tender_id}
    artifact_by_url = {
        artifact.result.url.strip(): artifact for artifact in artifacts if artifact.result.url
    }
    artifact_by_id = {
        artifact.result.tender_id.strip(): artifact
        for artifact in artifacts
        if artifact.result.tender_id
    }

    if safe_log:
        no_files, deadline, no_files_rows, deadline_rows = _parse_safe_batch_log(safe_log)
    else:
        no_files, deadline, no_files_rows, deadline_rows = set(), set(), set(), set()

    if output_xlsx.exists() and args.replace:
        output_xlsx.unlink()

    writer = ExcelWriter(output_path=output_xlsx, preserve_status=args.preserve_status)
    writer.ensure_header()

    for row_number, tender in enumerate(tenders, start=1):
        result = None
        if tender.url and tender.url.strip() in by_url:
            result = by_url[tender.url.strip()]
            artifact = artifact_by_url.get(tender.url.strip())
        elif tender.tender_id and tender.tender_id.strip() in by_id:
            result = by_id[tender.tender_id.strip()]
            artifact = artifact_by_id.get(tender.tender_id.strip())
        else:
            artifact = None
        if artifact is not None:
            rerendered = rerender_artifact_result(artifact)
            if rerendered is not None:
                payload, facts = rerendered
                apply_rerender_to_result(artifact, payload, facts)
                result = artifact.result

        if result is None:
            summary = ""
            if tender.tender_id in no_files or (not tender.tender_id and row_number in no_files_rows):
                summary = NO_FILES_SUMMARY
            elif tender.tender_id in deadline or (not tender.tender_id and row_number in deadline_rows):
                summary = DEADLINE_SUMMARY
            result = TenderAnalysisResult(
                tender_id=tender.tender_id,
                title=tender.title,
                url=tender.url,
                batch_name=batch_label,
                batch_path=str(batch_path),
                deadline_at=tender.deadline_at,
                customer=tender.customer,
                customer_inn=tender.customer_inn,
                decision="",
                confidence_percent=0,
                summary_text=summary,
                downloaded_files=[],
                analysis_markdown="",
                nmck_rub=None,
                document_completeness="",
                error="",
                seldon_added_at=None,
                facts_stack="",
                facts_requirements="",
                facts_docs="",
                facts_payment="",
            )
        elif batch_label and not result.batch_name:
            result.batch_name = batch_label
        if batch_label and not result.batch_path and batch_path.exists():
            result.batch_path = str(batch_path)
        if artifact is not None:
            if not result.facts_stack and artifact.result.facts_stack:
                result.facts_stack = artifact.result.facts_stack
            if not result.facts_requirements and artifact.result.facts_requirements:
                result.facts_requirements = artifact.result.facts_requirements
            if not result.facts_docs and artifact.result.facts_docs:
                result.facts_docs = artifact.result.facts_docs
            if not result.facts_payment and artifact.result.facts_payment:
                result.facts_payment = artifact.result.facts_payment
            if not result.facts_req_sro and artifact.result.facts_req_sro:
                result.facts_req_sro = artifact.result.facts_req_sro
            if not result.facts_req_turnover and artifact.result.facts_req_turnover:
                result.facts_req_turnover = artifact.result.facts_req_turnover
            if not result.facts_req_analog_projects and artifact.result.facts_req_analog_projects:
                result.facts_req_analog_projects = artifact.result.facts_req_analog_projects
            if not result.facts_req_legacy_experience and artifact.result.facts_req_legacy_experience:
                result.facts_req_legacy_experience = artifact.result.facts_req_legacy_experience
            if not result.facts_req_roles and artifact.result.facts_req_roles:
                result.facts_req_roles = artifact.result.facts_req_roles
            if not result.facts_req_licenses and artifact.result.facts_req_licenses:
                result.facts_req_licenses = artifact.result.facts_req_licenses
            facts = artifact.extra.get("facts") or {}
            if not result.facts_stack and facts.get("stack"):
                result.facts_stack = ", ".join([str(x) for x in facts.get("stack") or []][:8])
            if not result.facts_requirements:
                participant = facts.get("participant_requirements") or {}
                req_items = (
                    (participant.get("turnover_texts") or [])[:2]
                    + (participant.get("experience_texts") or [])[:2]
                    + (participant.get("required_roles") or [])[:2]
                    + (participant.get("licenses_or_certs") or [])[:2]
                    + (facts.get("turnover_requirements") or [])[:1]
                    + (facts.get("project_requirements") or [])[:1]
                    + (facts.get("team_requirements") or [])[:1]
                    + (facts.get("licenses") or [])[:1]
                )
                result.facts_requirements = "; ".join(dict.fromkeys([str(x) for x in req_items])) if req_items else ""
            participant = facts.get("participant_requirements") or {}
            if not result.facts_req_sro and participant.get("requires_sro"):
                result.facts_req_sro = "Да"
            if not result.facts_req_turnover:
                if participant.get("min_turnover_rub"):
                    result.facts_req_turnover = f"от {int(participant['min_turnover_rub']):,} руб.".replace(",", " ")
                elif (participant.get("turnover_texts") or []):
                    result.facts_req_turnover = str((participant.get("turnover_texts") or [])[0])
            if not result.facts_req_analog_projects and participant.get("requires_analog_projects"):
                result.facts_req_analog_projects = "Да"
            if not result.facts_req_legacy_experience and participant.get("requires_existing_system_modification_experience"):
                result.facts_req_legacy_experience = "Да"
            if not result.facts_req_roles and (participant.get("required_roles") or []):
                result.facts_req_roles = ", ".join([str(x) for x in (participant.get("required_roles") or [])[:4]])
            if not result.facts_req_licenses and (participant.get("licenses_or_certs") or []):
                result.facts_req_licenses = ", ".join([str(x) for x in (participant.get("licenses_or_certs") or [])[:4]])
            if not result.facts_docs:
                docs_parts = []
                for key, values in (facts.get("document_roles") or {}).items():
                    if values:
                        docs_parts.append(f"{key}: {', '.join([str(x) for x in values][:2])}")
                result.facts_docs = "; ".join(docs_parts) if docs_parts else ""
            if not result.facts_payment and facts.get("payment"):
                result.facts_payment = "; ".join([str(x) for x in facts.get("payment") or []][:3])

        writer.append_result(result)

    print(f"Rebuilt {output_xlsx} from XLS ({len(tenders)} rows) + {len(results)} artifacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
