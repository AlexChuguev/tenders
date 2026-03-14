from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class TenderRow:
    row_number: int
    tender_id: str
    title: str
    url: str
    deadline_at: datetime | None
    customer: str
    customer_inn: str
    raw: dict[str, object]


@dataclass
class DownloadedTender:
    tender: TenderRow
    directory: Path
    files: list[Path] = field(default_factory=list)


@dataclass
class TenderAnalysisResult:
    tender_id: str
    title: str
    url: str
    deadline_at: datetime | None
    customer: str
    customer_inn: str
    status: str
    classification_tag: str
    confidence_percent: int
    classification_comment: str
    downloaded_files: list[str]
    analysis_markdown: str
    error: str = ""
