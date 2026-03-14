from __future__ import annotations

from pathlib import Path
import re
from datetime import datetime
from urllib.parse import urlparse

import xlrd

from tender_agent.models import TenderRow


COMMON_URL_COLUMNS = (
    "Ссылка",
    "URL",
    "Url",
    "Ссылка на закупку",
    "Карточка",
    "Link",
    "Номер извещения (ссылка на источник)",
)
COMMON_ID_COLUMNS = ("Номер", "№", "Номер закупки", "ID", "Код", "Номер извещения (ссылка на источник)")
COMMON_TITLE_COLUMNS = ("Наименование", "Предмет", "Название", "Объект закупки", "Наименование лота")
COMMON_DEADLINE_COLUMNS = ("Дата окончания приема заявок", "Дата окончания подачи заявок", "Окончание подачи")
COMMON_CUSTOMER_COLUMNS = ("Заказчик", "Организатор")
COMMON_CUSTOMER_INN_COLUMNS = ("ИНН Заказчика", "ИНН Организатора")


def load_tenders(
    xls_path: Path,
    url_column: str,
    id_column: str,
    title_column: str,
) -> list[TenderRow]:
    workbook = xlrd.open_workbook(str(xls_path), formatting_info=True)
    sheet = workbook.sheet_by_index(0)
    headers = [str(sheet.cell_value(0, col)).strip() for col in range(sheet.ncols)]
    header_map = {header: idx for idx, header in enumerate(headers) if header}
    url_idx = _find_column(header_map, url_column, COMMON_URL_COLUMNS)
    id_idx = _find_column(header_map, id_column, COMMON_ID_COLUMNS)
    title_idx = _find_column(header_map, title_column, COMMON_TITLE_COLUMNS)
    deadline_idx = _find_column(header_map, "", COMMON_DEADLINE_COLUMNS)
    customer_idx = _find_column(header_map, "", COMMON_CUSTOMER_COLUMNS)
    customer_inn_idx = _find_column(header_map, "", COMMON_CUSTOMER_INN_COLUMNS)

    tenders: list[TenderRow] = []
    for row_idx in range(1, sheet.nrows):
        row_values = [sheet.cell_value(row_idx, col) for col in range(sheet.ncols)]
        row = {headers[col]: row_values[col] for col in range(sheet.ncols) if headers[col]}
        all_row_hyperlinks = _extract_row_hyperlinks(sheet, row_idx)
        url = _find_seldon_tender_url(all_row_hyperlinks) or _extract_cell_url(sheet, row_idx, url_idx) or str(row_values[url_idx]).strip()
        if not url:
            continue
        tender_id = _extract_cell_display_value(row_values[id_idx]) if id_idx is not None else ""
        if not tender_id or tender_id.lower() in {"источник", "source"}:
            tender_id = _derive_tender_id(url, row_idx + 1)
        title = str(row_values[title_idx]).strip() if title_idx is not None else ""
        deadline_at = _extract_excel_datetime(workbook, row_values[deadline_idx]) if deadline_idx is not None else None
        customer = _extract_cell_display_value(row_values[customer_idx]) if customer_idx is not None else ""
        customer_inn = _extract_cell_display_value(row_values[customer_inn_idx]) if customer_inn_idx is not None else ""
        if all_row_hyperlinks:
            row["_hyperlinks"] = all_row_hyperlinks
        tenders.append(
            TenderRow(
                row_number=row_idx + 1,
                tender_id=tender_id,
                title=title,
                url=url,
                deadline_at=deadline_at,
                customer=customer,
                customer_inn=customer_inn,
                raw=row,
            )
        )
    return tenders


def _find_column(header_map: dict[str, int], preferred: str, fallbacks: tuple[str, ...]) -> int | None:
    candidates = [preferred, *fallbacks]
    for name in candidates:
        if name in header_map:
            return header_map[name]
    if preferred:
        raise RuntimeError(
            f"Column '{preferred}' was not found in XLS. Available columns: {', '.join(header_map)}"
        )
    return None


def _extract_cell_url(sheet: xlrd.sheet.Sheet, row_idx: int, col_idx: int) -> str:
    hyperlink_map = getattr(sheet, "hyperlink_map", {})
    hyperlink = hyperlink_map.get((row_idx, col_idx))
    if hyperlink and hyperlink.url_or_path:
        return str(hyperlink.url_or_path).strip()
    return ""


def _extract_row_hyperlinks(sheet: xlrd.sheet.Sheet, row_idx: int) -> list[str]:
    hyperlink_map = getattr(sheet, "hyperlink_map", {})
    links: list[str] = []
    for col_idx in range(sheet.ncols):
        hyperlink = hyperlink_map.get((row_idx, col_idx))
        if hyperlink and hyperlink.url_or_path:
            links.append(str(hyperlink.url_or_path).strip())
    return links


def _extract_cell_display_value(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _extract_excel_datetime(workbook: xlrd.book.Book, value: object) -> datetime | None:
    if value in ("", None):
        return None
    if isinstance(value, (int, float)):
        try:
            return xlrd.xldate.xldate_as_datetime(value, workbook.datemode)
        except Exception:
            return None
    raw = str(value).strip()
    for fmt in ("%d.%m.%Y", "%d.%m.%Y %H:%M", "%d.%m.%y", "%d.%m.%y %H:%M"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _derive_tender_id(url: str, row_number: int) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    for candidate in reversed(parts):
        if candidate.isdigit():
            return candidate
    for candidate in reversed(parts):
        if re.search(r"[A-Za-z0-9]{6,}", candidate):
            return candidate
    return f"row-{row_number}"


def _find_seldon_tender_url(links: list[str]) -> str:
    for link in links:
        parsed = urlparse(link)
        if "pro.myseldon.com" in parsed.netloc.lower() and "/tender/" in parsed.path.lower():
            return link
    return ""
