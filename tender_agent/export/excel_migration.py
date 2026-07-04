from __future__ import annotations

from tender_agent.export.excel_schema import (
    apply_deadline_format,
    apply_status_fill,
    column_index,
    column_count,
    coerce_deadline_value,
    ensure_sheet_structure,
)
from tender_agent.models import TenderAnalysisResult


COMPLETENESS_LABELS = {"низкая", "средняя", "высокая"}


def cleanup_legacy_workbook(workbook) -> None:
    if "Tenders" in workbook.sheetnames and len(workbook.sheetnames) > 1:
        del workbook["Tenders"]
    if "Temp" in workbook.sheetnames and len(workbook.sheetnames) > 1:
        del workbook["Temp"]
    for sheet_name in list(workbook.sheetnames):
        worksheet = workbook[sheet_name]
        if worksheet.max_row >= 1 and worksheet.cell(row=1, column=3).value == "Статус":
            rebuild_sheet(worksheet)
            continue
        if worksheet.max_row >= 1 and worksheet.cell(row=1, column=8).value == "Полнота документов":
            rebuild_current_sheet(worksheet)
            continue
        if worksheet.max_row >= 1 and worksheet.cell(row=1, column=7).value == "НМЦК":
            rebuild_current_sheet(worksheet)
            continue
        if worksheet.max_row >= 1 and worksheet.cell(row=1, column=9).value == "Дата появления в Seldon":
            rebuild_current_sheet(worksheet)
            continue
        if _sheet_has_shifted_completeness_column(worksheet):
            rebuild_current_sheet(worksheet)
            continue
        expected_headers = {
            column_index("number"): "№",
            column_index("status"): "Статус",
            column_index("summary"): "Summary",
            column_index("title"): "Наименование лота",
            column_index("deadline"): "Дата окончания приема заявок",
            column_index("comment"): "Комментарий",
            column_index("batch"): "Выгрузка",
            column_index("facts_stack"): "Факты: стек",
            column_index("facts_requirements"): "Факты: требования",
            column_index("facts_docs"): "Факты: документация",
            column_index("facts_payment"): "Факты: оплата",
            column_index("facts_req_sro"): "Факт: СРО",
            column_index("facts_req_turnover"): "Факт: оборот",
            column_index("facts_req_analog_projects"): "Факт: аналогичный опыт",
            column_index("facts_req_legacy_experience"): "Факт: опыт доработки",
            column_index("facts_req_roles"): "Факт: роли",
            column_index("facts_req_licenses"): "Факт: лицензии/сертификаты",
        }
        extra_header = any(
            worksheet.cell(row=1, column=c).value
            for c in range(column_count() + 1, max(column_count() + 6, worksheet.max_column + 1))
        )
        if worksheet.max_row >= 1 and (
            any(worksheet.cell(row=1, column=index).value != header for index, header in expected_headers.items())
            or extra_header
        ):
            rebuild_current_sheet(worksheet)


def remove_existing_rows(workbook, result: TenderAnalysisResult) -> None:
    tender_id = (result.tender_id or "").strip()
    target_url = (result.url or "").strip()
    title = (result.title or "").strip()
    deadline = result.deadline_at
    for worksheet in workbook.worksheets:
        row_index = 2
        while row_index <= worksheet.max_row:
            hyperlink = worksheet.cell(row=row_index, column=column_index("title")).hyperlink
            target = hyperlink.target if hyperlink else ""
            row_title = str(worksheet.cell(row=row_index, column=column_index("title")).value or "").strip()
            row_deadline = worksheet.cell(row=row_index, column=column_index("deadline")).value
            matches = False
            if target_url and isinstance(target, str) and target == target_url:
                matches = True
            elif tender_id and isinstance(target, str) and tender_id in target:
                matches = True
            elif title and row_title == title:
                if deadline and row_deadline == deadline:
                    matches = True
            if matches:
                worksheet.delete_rows(row_index, 1)
                continue
            row_index += 1


def remove_technical_rows(workbook) -> None:
    for worksheet in workbook.worksheets:
        row_index = 2
        while row_index <= worksheet.max_row:
            confidence = 0
            summary_text = str(worksheet.cell(row=row_index, column=3).value or "").strip()
            if is_technical_retry_result(summary_text, confidence) and not _has_user_entered_row_data(
                worksheet, row_index
            ):
                worksheet.delete_rows(row_index, 1)
                continue
            row_index += 1


def remove_malformed_rows(workbook) -> None:
    for worksheet in workbook.worksheets:
        row_index = 2
        while row_index <= worksheet.max_row:
            title_value = str(worksheet.cell(row=row_index, column=column_index("title")).value or "").strip()
            summary_text = str(worksheet.cell(row=row_index, column=column_index("summary")).value or "").strip()
            number_value = worksheet.cell(row=row_index, column=column_index("number")).value
            if title_value.startswith("http") and not summary_text and not number_value:
                worksheet.delete_rows(row_index, 1)
                continue
            row_index += 1


def normalize_deadline_cells(workbook) -> None:
    for worksheet in workbook.worksheets:
        for row_index in range(2, worksheet.max_row + 1):
            cell = worksheet.cell(row=row_index, column=column_index("deadline"))
            parsed = coerce_deadline_value(cell.value)
            if parsed is None:
                continue
            cell.value = parsed
            apply_deadline_format(cell)


def renumber_rows(worksheet) -> None:
    current_number = 1
    for row_index in range(2, worksheet.max_row + 1):
        title = worksheet.cell(row=row_index, column=column_index("title")).value
        if not title:
            worksheet.cell(row=row_index, column=column_index("number")).value = None
            continue
        worksheet.cell(row=row_index, column=column_index("number")).value = current_number
        current_number += 1


def rebuild_sheet(worksheet) -> None:
    rows: list[list[object]] = []
    for row_index in range(12, worksheet.max_row + 1):
        title = worksheet.cell(row=row_index, column=8).value
        if not title:
            continue
        url = ""
        hyperlink = worksheet.cell(row=row_index, column=8).hyperlink
        if hyperlink and hyperlink.target:
            url = hyperlink.target
        old_tag = worksheet.cell(row=row_index, column=3).value or "Уточнить"
        old_confidence = worksheet.cell(row=row_index, column=4).value or 0
        old_comment = worksheet.cell(row=row_index, column=5).value or ""
        deadline = worksheet.cell(row=row_index, column=9).value or ""
        number = worksheet.cell(row=row_index, column=6).value or ""
        header_j = worksheet.cell(row=1, column=10).value
        added_at = worksheet.cell(row=row_index, column=10).value or ""
        seldon_added_at = added_at if header_j == "Дата появления в Seldon" else ""
        rows.append([old_comment, number, title, deadline, url])

    worksheet.delete_rows(1, worksheet.max_row)
    ensure_sheet_structure(worksheet)
    for summary_text, number, title, deadline, url in rows:
        worksheet.append([number, "", summary_text, title, deadline, "", "", "", "", "", "", "", "", "", "", "", ""])
        apply_deadline_format(worksheet.cell(row=worksheet.max_row, column=5))
        title_cell = worksheet.cell(row=worksheet.max_row, column=4)
        if url:
            title_cell.hyperlink = url
            title_cell.style = "Hyperlink"


def rebuild_current_sheet(worksheet) -> None:
    rows: list[list[object]] = []
    header_a = worksheet.cell(row=1, column=1).value
    header_b = worksheet.cell(row=1, column=2).value
    header_c = worksheet.cell(row=1, column=3).value
    header_d = worksheet.cell(row=1, column=4).value
    header_e = worksheet.cell(row=1, column=5).value
    header_f = worksheet.cell(row=1, column=6).value
    header_g = worksheet.cell(row=1, column=7).value
    header_h = worksheet.cell(row=1, column=8).value
    for row_index in range(2, worksheet.max_row + 1):
        shifted_completeness = (
            header_g == "Наименование лота"
            and str(worksheet.cell(row=row_index, column=7).value or "").strip().lower() in COMPLETENESS_LABELS
            and worksheet.cell(row=row_index, column=8).value
        )
        if header_d == "Наименование лота":
            title = worksheet.cell(row=row_index, column=4).value
        elif shifted_completeness:
            title = worksheet.cell(row=row_index, column=8).value
        elif header_g == "Наименование лота":
            title = worksheet.cell(row=row_index, column=7).value
        elif header_h == "Наименование лота":
            title = worksheet.cell(row=row_index, column=8).value
        else:
            title = (
                worksheet.cell(row=row_index, column=8).value
                or worksheet.cell(row=row_index, column=9).value
                or worksheet.cell(row=row_index, column=7).value
            )
        if not title:
            continue
        url = ""
        hyperlink = None
        if header_d == "Наименование лота":
            hyperlink = worksheet.cell(row=row_index, column=4).hyperlink
        if hyperlink is None:
            hyperlink = (
                (worksheet.cell(row=row_index, column=8).hyperlink if shifted_completeness else worksheet.cell(row=row_index, column=7).hyperlink)
                or worksheet.cell(row=row_index, column=8).hyperlink
                or worksheet.cell(row=row_index, column=9).hyperlink
            )
        if hyperlink and hyperlink.target:
            url = hyperlink.target
        if header_c == "Summary":
            summary_text = worksheet.cell(row=row_index, column=3).value or ""
            number = worksheet.cell(row=row_index, column=1).value or ""
        elif header_b == "Summary":
            summary_text = worksheet.cell(row=row_index, column=2).value or ""
            number = worksheet.cell(row=row_index, column=1).value or ""
        elif header_a == "Решение":
            summary_text = worksheet.cell(row=row_index, column=5).value or ""
            number = worksheet.cell(row=row_index, column=6).value or ""
        else:
            number = worksheet.cell(row=row_index, column=1).value or worksheet.cell(row=row_index, column=3).value or ""
            summary_text = worksheet.cell(row=row_index, column=2).value or worksheet.cell(row=row_index, column=3).value or ""
        if shifted_completeness:
            deadline = worksheet.cell(row=row_index, column=9).value or ""
        elif header_e == "Дата окончания приема заявок":
            deadline = worksheet.cell(row=row_index, column=5).value or ""
        elif header_d == "Дата окончания приема заявок":
            deadline = worksheet.cell(row=row_index, column=4).value or ""
        elif header_g == "Наименование лота":
            deadline = worksheet.cell(row=row_index, column=8).value or ""
        elif header_h == "Наименование лота":
            deadline = worksheet.cell(row=row_index, column=9).value or ""
        else:
            deadline = worksheet.cell(row=row_index, column=5).value or worksheet.cell(row=row_index, column=4).value or ""
        status = ""
        if header_b == "Статус":
            status = worksheet.cell(row=row_index, column=2).value or ""
        elif header_e == "Статус":
            status = worksheet.cell(row=row_index, column=5).value or ""
        comment = ""
        if header_f == "Комментарий":
            comment = worksheet.cell(row=row_index, column=6).value or ""
        batch_name = ""
        if header_g == "Выгрузка":
            batch_name = worksheet.cell(row=row_index, column=7).value or ""
        rows.append([number, status, summary_text, title, deadline, url, comment, batch_name, "", "", "", "", "", "", "", "", "", ""])

    worksheet.delete_rows(1, worksheet.max_row)
    ensure_sheet_structure(worksheet)
    for (
        number,
        status,
        summary_text,
        title,
        deadline,
        url,
        comment,
        batch_name,
        facts_stack,
        facts_requirements,
        facts_docs,
        facts_payment,
        facts_req_sro,
        facts_req_turnover,
        facts_req_analog_projects,
        facts_req_legacy_experience,
        facts_req_roles,
        facts_req_licenses,
    ) in rows:
        worksheet.append(
            [
                number,
                status,
                summary_text,
                title,
                deadline,
                comment,
                batch_name,
                facts_stack,
                facts_requirements,
                facts_docs,
                facts_payment,
                facts_req_sro,
                facts_req_turnover,
                facts_req_analog_projects,
                facts_req_legacy_experience,
                facts_req_roles,
                facts_req_licenses,
            ]
        )
        apply_deadline_format(worksheet.cell(row=worksheet.max_row, column=column_index("deadline")))
        title_cell = worksheet.cell(row=worksheet.max_row, column=column_index("title"))
        if url:
            title_cell.hyperlink = url
            title_cell.style = "Hyperlink"


def is_technical_retry_result(summary_text: str, confidence: object) -> bool:
    try:
        normalized_confidence = int(confidence or 0)
    except Exception:
        normalized_confidence = 0
    normalized_summary = summary_text.lower()
    if normalized_summary.startswith("техсбой llm"):
        return False
    return normalized_confidence == 0 and (
        "требуется повторный прогон анализа" in normalized_summary
        or "технической ошибки анализа" in normalized_summary
        or "не удалось получить ответ модели" in normalized_summary
        or "dns resolution failed" in normalized_summary
    )


def _sheet_has_shifted_completeness_column(worksheet) -> bool:
    if worksheet.max_row < 2:
        return False
    if worksheet.cell(row=1, column=7).value != "Наименование лота":
        return False
    for row_index in range(2, min(worksheet.max_row, 12) + 1):
        value = str(worksheet.cell(row=row_index, column=7).value or "").strip().lower()
        if value in COMPLETENESS_LABELS and worksheet.cell(row=row_index, column=8).value:
            return True
    return False


def sort_sheet_by_deadline(worksheet) -> None:
    if worksheet.max_row < 3:
        return
    rows = []
    max_column = max(worksheet.max_column, column_count())
    for row_index in range(2, worksheet.max_row + 1):
        row = [
            worksheet.cell(row=row_index, column=col).value
            for col in range(1, max_column + 1)
        ]
        title_cell = worksheet.cell(row=row_index, column=column_index("title"))
        summary_cell = worksheet.cell(row=row_index, column=column_index("summary"))
        batch_cell = worksheet.cell(row=row_index, column=column_index("batch"))
        rows.append(
            (
                row,
                {
                    "title": title_cell.hyperlink.target if title_cell.hyperlink else "",
                    "summary": summary_cell.hyperlink.target if summary_cell.hyperlink else "",
                    "batch": batch_cell.hyperlink.target if batch_cell.hyperlink else "",
                },
            )
        )
    rows.sort(key=lambda item: _deadline_sort_key(item[0][column_index("deadline") - 1]))
    worksheet.delete_rows(2, worksheet.max_row - 1)
    for row, hyperlinks in rows:
        while len(row) < column_count():
            row.append("")
        target_row = worksheet.max_row + 1
        for col_index, value in enumerate(row, start=1):
            worksheet.cell(row=target_row, column=col_index, value=value)
        title_cell = worksheet.cell(row=target_row, column=column_index("title"))
        if hyperlinks.get("title"):
            title_cell.hyperlink = hyperlinks["title"]
            title_cell.style = "Hyperlink"
        summary_cell = worksheet.cell(row=target_row, column=column_index("summary"))
        if hyperlinks.get("summary"):
            summary_cell.hyperlink = hyperlinks["summary"]
            summary_cell.style = "Hyperlink"
        batch_cell = worksheet.cell(row=target_row, column=column_index("batch"))
        if hyperlinks.get("batch"):
            batch_cell.hyperlink = hyperlinks["batch"]
            batch_cell.style = "Hyperlink"
        apply_status_fill(worksheet.cell(row=target_row, column=column_index("status")))


def _has_user_entered_row_data(worksheet, row_index: int) -> bool:
    status = str(worksheet.cell(row=row_index, column=column_index("status")).value or "").strip()
    comment = str(worksheet.cell(row=row_index, column=column_index("comment")).value or "").strip()
    if status and status not in {"Документы не загружены", "Техсбой LLM", "LLM", "Системой обработано"}:
        return True
    if comment:
        return True
    for col_index in range(column_count() + 1, worksheet.max_column + 1):
        if worksheet.cell(row=row_index, column=col_index).value not in (None, ""):
            return True
    return False


def _deadline_sort_key(value):
    parsed = coerce_deadline_value(value)
    if parsed is None:
        return (True, "")
    return (False, parsed)
