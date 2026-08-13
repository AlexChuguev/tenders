from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tender_agent.config import Settings
from tender_agent.artifact_writer import ArtifactWriter
from tender_agent.excel_writer import ExcelWriter
from tender_agent.models import TenderAnalysisResult
from tender_agent.search_profile import SearchProfile, SearchProfileMatch, evaluate_text
from tender_agent.tenderplan_api import TenderplanApiClient, TenderplanApiConfig


@dataclass(frozen=True)
class TenderplanCandidate:
    tender_id: str
    title: str
    url: str
    deadline_at: datetime | None
    customer: str
    customer_inn: str
    match: SearchProfileMatch
    raw: dict[str, Any]


class TenderplanPipeline:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = TenderplanApiClient(
            TenderplanApiConfig(
                base_url=settings.tenderplan_base_url,
                pat=settings.tenderplan_pat,
                timeout_seconds=settings.tenderplan_timeout_seconds,
            )
        )
        self.profile = SearchProfile.load(settings.search_profile_path)
        self.sheet_writer = ExcelWriter(output_path=settings.output_xlsx)
        self.artifact_writer = ArtifactWriter(
            root=settings.artifact_dir,
            source="tenderplan_pipeline",
            batch_name=settings.search_profile_path.stem or "tenderplan_candidates",
        )

    def fetch_candidates(self) -> list[TenderplanCandidate]:
        payload = self.client.request_json("/api/tenders/v2/getlist")
        tenders = payload.get("tenders", [])
        candidates: list[TenderplanCandidate] = []
        for item in tenders:
            text = self._compose_search_text(item)
            match = evaluate_text(self.profile, text)
            if not match.is_candidate:
                continue
            candidates.append(
                TenderplanCandidate(
                    tender_id=str(item.get("id") or item.get("_id") or item.get("number") or ""),
                    title=str(item.get("orderName") or "").strip(),
                    url=str(item.get("href") or ""),
                    deadline_at=_epoch_ms_to_datetime(item.get("submissionCloseDateTime")),
                    customer=_extract_customer_name(item),
                    customer_inn="",
                    match=match,
                    raw=item,
                )
            )
        candidates.sort(key=lambda item: (item.deadline_at is None, item.deadline_at, item.title.lower()))
        return candidates

    def write_candidates_to_excel(self, candidates: list[TenderplanCandidate]) -> None:
        self.sheet_writer.ensure_header()
        for candidate in candidates:
            include_preview = ", ".join(candidate.match.include_hits[:3])
            secondary_preview = ", ".join(candidate.match.secondary_hits[:2])
            comment = f"Совпадения: {include_preview or 'нет'}"
            if secondary_preview:
                comment += f". Доп. сигналы: {secondary_preview}"
            result = TenderAnalysisResult(
                tender_id=candidate.tender_id,
                title=candidate.title,
                url=candidate.url,
                batch_name=self.settings.search_profile_path.stem or "tenderplan_candidates",
                batch_path="",
                deadline_at=candidate.deadline_at,
                customer=candidate.customer,
                customer_inn=candidate.customer_inn,
                decision="Уточнить",
                confidence_percent=0,
                summary_text="\n".join(
                    [
                        f"1. {comment}",
                        "2. Тендер прошёл через поисковый профиль по ключевым словам.",
                        "3. Документы ещё не анализировались моделью.",
                        "4. Решение по участию пока предварительное.",
                        "5. Нужен следующий шаг с документами для финального вывода.",
                    ]
                ),
                downloaded_files=[],
                analysis_markdown="Кандидат отобран по поисковому профилю. Анализ документов ещё не выполнялся.",
                facts_stack="",
                facts_requirements="",
                facts_docs="",
                facts_payment="",
            )
            self.artifact_writer.write(
                result=result,
                analysis=None,
                extra={"candidate_raw": candidate.raw, "match": candidate.match.__dict__},
            )
            self.sheet_writer.append_result(result)

    def _compose_search_text(self, item: dict[str, Any]) -> str:
        parts = [
            str(item.get("orderName") or ""),
            str(item.get("number") or ""),
            str(item.get("globalSearch") or ""),
            str(item.get("tenderSearch") or ""),
            str(item.get("json") or ""),
        ]
        for customer in item.get("customers") or []:
            if isinstance(customer, dict):
                parts.append(str(customer.get("name") or ""))
        return " ".join(parts)


def _extract_customer_name(item: dict[str, Any]) -> str:
    customers = item.get("customers") or []
    if customers and isinstance(customers[0], dict):
        return str(customers[0].get("name") or "")
    return ""


def _epoch_ms_to_datetime(value: Any) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value / 1000)
