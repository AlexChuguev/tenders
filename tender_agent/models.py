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
    decision: str
    confidence_percent: int
    summary_text: str
    downloaded_files: list[str]
    analysis_markdown: str
    nmck_rub: float | None = None
    document_completeness: str = ""
    error: str = ""
    seldon_added_at: datetime | None = None
    batch_name: str = ""
    batch_path: str = ""
    facts_stack: str = ""
    facts_requirements: str = ""
    facts_docs: str = ""
    facts_payment: str = ""
    facts_req_sro: str = ""
    facts_req_turnover: str = ""
    facts_req_analog_projects: str = ""
    facts_req_legacy_experience: str = ""
    facts_req_roles: str = ""
    facts_req_licenses: str = ""
    reason_codes: list[str] = field(default_factory=list)
