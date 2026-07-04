from __future__ import annotations

import csv
import re
import shutil
import zipfile
import json
import subprocess
import time
from functools import lru_cache
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader
from tender_agent.analysis import TenderAnalyzer
from tender_agent.artifact_reader import iter_artifacts, unique_latest_artifacts
from tender_agent.artifact_writer import ArtifactWriter
from tender_agent.config import Settings
from tender_agent.evidence import (
    build_fact_columns,
    build_requirement_fact_columns,
    render_summary_points_with_evidence,
)
from tender_agent.extraction_report import build_input_file_report, write_batch_extraction_summary
from tender_agent.excel_loader import load_tenders
from tender_agent.excel_writer import ExcelWriter
from tender_agent.llm import create_llm_provider
from tender_agent.models import TenderAnalysisResult, TenderRow
from tender_agent.seldon_added_at import (
    NullSeldonAddedAtResolver,
    SeldonAddedAtResolver,
    should_resolve_seldon_added_at,
)
from tender_agent.text_extract import extract_doc_with_textutil


class LocalTenderReviewer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        max_chars = (
            settings.deep_review_max_chars_per_file
            if settings.review_mode == "deep"
            else settings.analysis_max_chars_per_file
        )
        self.analyzer = TenderAnalyzer(
            provider_name=settings.llm_provider,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            prompt_template_path=settings.prompt_template_path,
            max_chars_per_file=max_chars,
            triage_policy_path=settings.triage_policy_path,
        )
        self.matcher_provider = create_llm_provider(
            provider_name=settings.llm_provider,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url or None,
        )
        self.sheet_writer = ExcelWriter(output_path=settings.output_xlsx)
        self.artifact_writer = ArtifactWriter(
            root=settings.artifact_dir,
            source="local_review",
            batch_name=settings.local_files_dir.name or "manual_downloads",
            policy_metadata=self.analyzer.policy_metadata,
        )
        self.network_failures_path = self.artifact_writer.batch_dir / "_network_failures.jsonl"

    def run(self) -> None:
        stats = {
            "scope_total": 0,
            "written": 0,
            "skipped_existing_success": 0,
            "skipped_no_files": 0,
            "skipped_deadline": 0,
            "network_failed": 0,
        }
        _expand_archives(self.settings.local_files_dir)
        tenders = load_tenders(
            xls_path=self.settings.input_xls,
            url_column=self.settings.platform_tender_url_column,
            id_column=self.settings.platform_tender_id_column,
            title_column=self.settings.platform_tender_title_column,
        )
        manifest_entries = _load_prepared_manifest(self.settings.local_files_dir)

        if manifest_entries is None:
            file_count = sum(
                1
                for path in self.settings.local_files_dir.rglob("*")
                if path.is_file() and not path.name.startswith(".")
            )
            if file_count > 50:
                print(
                    f"[warn] _manifest.csv not found: file-to-tender matching will use LLM for {file_count} files; "
                    "prefer prepare_folders.py for large batches",
                    flush=True,
                )
            target_date = _resolve_target_date(self.settings.review_target_date)
            tenders = [t for t in tenders if t.deadline_at and t.deadline_at.date() == target_date]
            tenders.sort(key=lambda item: (item.deadline_at is None, item.deadline_at))

        if self.settings.tender_skip:
            if manifest_entries is not None:
                manifest_entries = manifest_entries[self.settings.tender_skip :]
            else:
                tenders = tenders[self.settings.tender_skip :]
        if self.settings.tender_limit is not None:
            if manifest_entries is not None:
                manifest_entries = manifest_entries[: self.settings.tender_limit]
            else:
                tenders = tenders[: self.settings.tender_limit]

        self.settings.local_files_dir.mkdir(parents=True, exist_ok=True)
        self.sheet_writer.ensure_header()
        self._ensure_llm_preflight()
        existing_successes = self._existing_success_ids()

        resolver_cm = (
            SeldonAddedAtResolver(self.settings)
            if should_resolve_seldon_added_at(self.settings, (t.url for t in tenders))
            else NullSeldonAddedAtResolver()
        )
        with resolver_cm as added_at_resolver:
            if manifest_entries is not None:
                tender_map = {t.tender_id: t for t in tenders}
                for entry in manifest_entries:
                    tender = tender_map.get(entry.tender_id)
                    if tender is None:
                        continue
                    stats["scope_total"] += 1
                    if tender.tender_id in existing_successes:
                        stats["skipped_existing_success"] += 1
                        continue
                    files = _collect_files_from_prepared_folder(self.settings.local_files_dir / entry.folder_name)
                    result = self._process_one(tender, files, added_at_resolver.get(tender.url), stats)
                    if result is not None:
                        self.sheet_writer.append_result(result)
                        stats["written"] += 1
                self._write_batch_report(stats)
                return

            scored_matches = _build_scored_file_map(
                self.settings.local_files_dir,
                tenders,
                self.matcher_provider,
            )
            for tender in tenders:
                stats["scope_total"] += 1
                if tender.tender_id in existing_successes:
                    stats["skipped_existing_success"] += 1
                    continue
                result = self._process_one(tender, scored_matches.get(tender.tender_id), added_at_resolver.get(tender.url), stats)
                if result is not None:
                    self.sheet_writer.append_result(result)
                    stats["written"] += 1
        self._write_batch_report(stats)

    def _process_one(
        self,
        tender: TenderRow,
        candidate_files: list[Path] | None,
        seldon_added_at: datetime | None,
        stats: dict[str, int] | None = None,
    ) -> TenderAnalysisResult | None:
        files = _find_local_files(
            self.settings.local_files_dir,
            tender,
            candidate_files or [],
        )
        if tender.deadline_at and tender.deadline_at < datetime.now():
            _bump_stat(stats, "skipped_deadline")
            result = TenderAnalysisResult(
                tender_id=tender.tender_id,
                title=tender.title,
                url=tender.url,
                batch_name=self.settings.local_files_dir.name or "manual_downloads",
                batch_path=str(self.settings.local_files_dir),
                deadline_at=tender.deadline_at,
                nmck_rub=_extract_tender_price_rub(tender),
                seldon_added_at=seldon_added_at,
                customer=tender.customer,
                customer_inn=tender.customer_inn,
                decision="Уточнить",
                confidence_percent=0,
                summary_text=(
                    "1. Риски: тендер не анализировался, потому что дедлайн уже прошёл\n"
                    "2. Стек: не определён\n"
                    "3. Требования к контрагенту: не определены\n"
                    "4. Документация: анализ не выполнялся\n"
                    "5. Оплата и обеспечение: не определены"
                ),
                downloaded_files=[],
                analysis_markdown="Технический статус: дедлайн подачи уже прошёл на момент обработки.",
                document_completeness="",
                error="deadline_passed",
                facts_stack="",
                facts_requirements="",
                facts_docs="",
                facts_payment="",
            )
            self.artifact_writer.write(
                result=result,
                extra={
                    "tender_raw": tender.raw,
                    "input_file_report": [],
                    "extraction_report": {},
                },
            )
            return result
        if not files:
            _bump_stat(stats, "skipped_no_files")
            result = TenderAnalysisResult(
                tender_id=tender.tender_id,
                title=tender.title,
                url=tender.url,
                batch_name=self.settings.local_files_dir.name or "manual_downloads",
                batch_path=str(self.settings.local_files_dir),
                deadline_at=tender.deadline_at,
                nmck_rub=_extract_tender_price_rub(tender),
                seldon_added_at=seldon_added_at,
                customer=tender.customer,
                customer_inn=tender.customer_inn,
                decision="Уточнить",
                confidence_percent=0,
                summary_text="Файлы не добавлены\nОжидает загрузки документов",
                downloaded_files=[],
                analysis_markdown="Технический статус: файлы не добавлены в папку тендера.",
                document_completeness="",
                error="no_files",
                facts_stack="",
                facts_requirements="",
                facts_docs="",
                facts_payment="",
            )
            self.artifact_writer.write(
                result=result,
                extra={
                    "tender_raw": tender.raw,
                    "input_file_report": [],
                    "extraction_report": {},
                },
            )
            return result
        file_limit = (
            self.settings.deep_review_max_files_per_tender
            if self.settings.review_mode == "deep"
            else self.settings.max_files_per_tender
        )
        all_files = list(files)
        files = _select_analysis_files(files, file_limit)
        input_file_report = build_input_file_report(
            all_files=all_files,
            selected_files=files,
            role_resolver=_analysis_role_with_content,
        )
        if not files:
            _bump_stat(stats, "skipped_no_files")
            result = TenderAnalysisResult(
                tender_id=tender.tender_id,
                title=tender.title,
                url=tender.url,
                batch_name=self.settings.local_files_dir.name or "manual_downloads",
                batch_path=str(self.settings.local_files_dir),
                deadline_at=tender.deadline_at,
                nmck_rub=_extract_tender_price_rub(tender),
                seldon_added_at=seldon_added_at,
                customer=tender.customer,
                customer_inn=tender.customer_inn,
                decision="Уточнить",
                confidence_percent=0,
                summary_text="Файлы не добавлены\nОжидает загрузки документов",
                downloaded_files=[],
                analysis_markdown="Технический статус: нет файлов, пригодных для анализа.",
                document_completeness="",
                error="no_files",
                facts_stack="",
                facts_requirements="",
                facts_docs="",
                facts_payment="",
            )
            self.artifact_writer.write(
                result=result,
                extra={
                    "tender_raw": tender.raw,
                    "input_file_report": input_file_report,
                    "extraction_report": {},
                },
            )
            return result

        analysis = None
        for attempt in range(max(1, self.settings.tender_network_retries)):
            analysis = self.analyzer.analyze_with_context(
                tender_url=tender.url,
                files=files,
                tender_title=tender.title,
                tender_price_rub=_extract_tender_price_rub(tender),
            )
            if analysis.error_type != "network_error":
                break
            if attempt < self.settings.tender_network_retries - 1:
                time.sleep(2 * (attempt + 1))
        assert analysis is not None
        facts_stack, facts_requirements, facts_docs, facts_payment = build_fact_columns(analysis.facts)
        req_sro, req_turnover, req_analog, req_legacy, req_roles, req_licenses = build_requirement_fact_columns(
            analysis.facts
        )
        result = TenderAnalysisResult(
            tender_id=tender.tender_id,
            title=tender.title,
            url=tender.url,
            batch_name=self.settings.local_files_dir.name or "manual_downloads",
            batch_path=str(self.settings.local_files_dir),
            deadline_at=tender.deadline_at,
            nmck_rub=_extract_tender_price_rub(tender),
            seldon_added_at=seldon_added_at,
            customer=tender.customer,
            customer_inn=tender.customer_inn,
            decision=analysis.decision,
            confidence_percent=analysis.confidence_percent,
            summary_text="\n".join(
                render_summary_points_with_evidence(
                    facts=analysis.facts,
                    decision=analysis.decision,
                    files=files,
                    deadline_at=tender.deadline_at,
                )
            ),
            downloaded_files=[str(path) for path in files],
            analysis_markdown=analysis.analysis_markdown,
            document_completeness=analysis.completeness_label,
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
        if analysis.error_type == "network_error":
            _append_network_failure(
                self.network_failures_path,
                tender=tender,
                files=files,
                attempts=max(1, self.settings.tender_network_retries),
            )
            _bump_stat(stats, "network_failed")
            result.decision = "Техсбой LLM"
            result.confidence_percent = 0
            result.summary_text = "Техсбой LLM\nТребуется повторный прогон анализа"
            result.analysis_markdown = "Технический статус: сетевой сбой при обращении к LLM."
        elif analysis.error_type == "llm_error":
            result.decision = "Техсбой LLM"
            result.confidence_percent = 0
            result.summary_text = "Техсбой LLM\nТребуется повторный прогон анализа"
            result.analysis_markdown = "Технический статус: ошибка LLM при анализе документов."
        self.artifact_writer.write(
            result=result,
            analysis=analysis,
            extra={
                "tender_raw": tender.raw,
                "input_file_report": input_file_report,
                "extraction_report": analysis.extraction_report,
            },
        )
        return result

    def _ensure_llm_preflight(self) -> None:
        if self.settings.llm_provider == "stub":
            return
        checker = getattr(self.analyzer.provider, "health_check", None)
        if checker is None:
            return
        ok, reason = checker(
            checks=self.settings.llm_preflight_checks,
            required_successes=self.settings.llm_preflight_required_successes,
        )
        if not ok:
            raise RuntimeError(f"LLM preflight failed: {reason or 'network is unstable'}")

    def _existing_success_ids(self) -> set[str]:
        artifacts = unique_latest_artifacts(
            iter_artifacts(
                self.settings.artifact_dir,
                source="local_review",
                batch_name=self.settings.local_files_dir.name or "manual_downloads",
            )
        )
        success_ids: set[str] = set()
        for artifact in artifacts:
            result = artifact.result
            if result.error in {"network_error", "llm_error"}:
                continue
            analysis_payload = artifact.analysis or {}
            if result.downloaded_files and not str(analysis_payload.get("llm_raw_text") or "").strip():
                continue
            if result.downloaded_files and not result.reason_codes:
                continue
            if result.decision == "Уточнить" and int(result.confidence_percent or 0) == 0:
                continue
            if result.tender_id:
                success_ids.add(result.tender_id)
        return success_ids

    def _write_batch_report(self, stats: dict[str, int]) -> None:
        report = {
            "written_at": datetime.now().isoformat(timespec="seconds"),
            "batch_name": self.settings.local_files_dir.name or "manual_downloads",
            "source": "local_review",
            "review_mode": self.settings.review_mode,
            **stats,
        }
        (self.artifact_writer.batch_dir / "_batch_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        write_batch_extraction_summary(self.artifact_writer.batch_dir)


def _bump_stat(stats: dict[str, int] | None, key: str) -> None:
    if stats is None:
        return
    stats[key] = int(stats.get(key, 0)) + 1


def _resolve_target_date(value: str) -> date:
    raw = value.strip().lower()
    today = datetime.now().date()
    if raw == "yesterday":
        return today - timedelta(days=1)
    return datetime.strptime(value, "%Y-%m-%d").date()


def _extract_tender_price_rub(tender: TenderRow) -> float | None:
    raw = tender.raw or {}
    priority_keys = [
        "нмцк",
        "нмцд",
        "начальная максимальная цена",
        "начальная цена",
        "максимальная цена",
        "цена",
        "стоимость",
        "сумма",
    ]
    normalized_items = [
        (str(key).strip(), str(key).strip().casefold().replace("ё", "е"), value)
        for key, value in raw.items()
    ]
    for target in priority_keys:
        for _orig_key, key_norm, value in normalized_items:
            if key_norm == target or key_norm.startswith(target):
                parsed = _parse_price_value(value)
                if parsed is not None:
                    return parsed
    for key, value in raw.items():
        key_norm = str(key).strip().casefold().replace("ё", "е")
        if not any(token in key_norm for token in ("цена", "нмц", "начальная", "максимальная", "стоимость", "сумма")):
            continue
        parsed = _parse_price_value(value)
        if parsed is not None:
            return parsed
    return None


def _parse_price_value(value: object) -> float | None:
    if value in ("", None):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("\xa0", " ")
    normalized = re.sub(r"[^0-9,.\s]", "", normalized)
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


class PreparedManifestEntry:
    def __init__(self, tender_id: str, folder_name: str) -> None:
        self.tender_id = tender_id
        self.folder_name = folder_name


def _load_prepared_manifest(root: Path) -> list[PreparedManifestEntry] | None:
    manifest_path = root / "_manifest.csv"
    if not manifest_path.exists():
        return None

    entries: list[PreparedManifestEntry] = []
    with manifest_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tender_id = str(row.get("tender_id", "")).strip()
            folder_name = str(row.get("folder_name", "")).strip()
            if not tender_id or not folder_name:
                continue
            entries.append(PreparedManifestEntry(tender_id=tender_id, folder_name=folder_name))
    return entries


def _append_network_failure(path: Path, *, tender: TenderRow, files: list[Path], attempts: int) -> None:
    entry = {
        "tender_id": tender.tender_id,
        "title": tender.title,
        "url": tender.url,
        "files": [str(file.name) for file in files],
        "attempts": attempts,
        "written_at": datetime.now().isoformat(timespec="seconds"),
    }
    existing: list[dict] = []
    if path.exists():
        try:
            existing = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except Exception:
            existing = []
    filtered = [item for item in existing if str(item.get("tender_id") or "") != tender.tender_id]
    filtered.append(entry)
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in filtered) + "\n", encoding="utf-8")


def _collect_files_from_prepared_folder(folder: Path) -> list[Path]:
    if not folder.exists():
        return []

    files: list[Path] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith(".") or path.name == ".done":
            continue
        files.append(path)
    return sorted(files)


def _find_local_files(root: Path, tender: TenderRow, scored_files: list[Path]) -> list[Path]:
    direct_folder = root / tender.tender_id
    if direct_folder.exists():
        return sorted(
            path
            for path in direct_folder.rglob("*")
            if path.is_file() and not path.name.startswith(".") and path.name != ".done"
        )

    id_match = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and not path.name.startswith(".") and path.name != ".done" and tender.tender_id in str(path)
    )
    if id_match:
        return id_match

    normalized_title = _normalize_name(tender.title)
    for folder in root.iterdir():
        if folder.is_dir() and normalized_title and normalized_title in _normalize_name(folder.name):
            return sorted(
                path
                for path in folder.rglob("*")
                if path.is_file() and not path.name.startswith(".") and path.name != ".done"
            )

    return scored_files


def _normalize_name(value: str) -> str:
    return (
        value.lower()
        .replace("ё", "е")
        .replace("й", "и")
        .replace("̆", "")
        .replace("_", " ")
        .replace("-", " ")
        .strip()
    )


def _prioritize_files(files: list[Path]) -> list[Path]:
    ranked = sorted(files, key=_file_priority_key)
    strong = [path for path in ranked if _file_priority_band(path) <= 2]
    if strong:
        return strong + [path for path in ranked if path not in strong]
    return ranked


def _select_analysis_files(files: list[Path], limit: int) -> list[Path]:
    ranked = _prioritize_files(files)
    slot_priority = ["тз", "извещение", "договор", "требования_к_участнику", "нмцк"]
    slot_best: dict[str, Path] = {}
    fallback_by_role: dict[str, list[Path]] = {slot: [] for slot in slot_priority}
    generic: list[Path] = []
    for path in ranked:
        role = _analysis_role_with_content(path)
        if role == "exclude":
            continue
        if role in fallback_by_role:
            fallback_by_role[role].append(path)
            if role not in slot_best:
                slot_best[role] = path
        else:
            generic.append(path)

    selected: list[Path] = []
    for slot in slot_priority:
        path = slot_best.get(slot)
        if path and path not in selected:
            selected.append(path)
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for slot in slot_priority:
            for path in fallback_by_role[slot]:
                if len(selected) >= limit:
                    break
                if path not in selected:
                    selected.append(path)
            if len(selected) >= limit:
                break

    if len(selected) < limit:
        for path in generic:
            if len(selected) >= limit:
                break
            if path not in selected:
                selected.append(path)

    if selected:
        return selected[:limit]
    return []


def _file_priority_key(path: Path) -> tuple[int, int, str]:
    band = _file_priority_band(path)
    suffix = path.suffix.lower()
    if suffix in {".pdf", ".docx"}:
        ext_bonus = 0
    elif suffix == ".doc":
        ext_bonus = 1
    else:
        ext_bonus = 2
    return (band, ext_bonus, path.name.lower())


def _file_priority_band(path: Path) -> int:
    normalized = _normalize_name(path.name)

    high_priority = [
        "техническое задание",
        "технические требования",
        "техзадание",
        "тз",
        "документация закупки",
        "документация о закупке",
        "закупочная документация",
        "извещение",
        "конкурсная документация",
        "аукционная документация",
        "требования к участникам",
    ]
    medium_priority = [
        "проект договора",
        "договор",
        "квалификац",
        "критерии",
        "оценк",
        "требования",
        "спецификация",
        "описание объекта закупки",
        "обоснование нмц",
    ]
    low_value = [
        "nda",
        "анкета",
        "коммерческ",
        "кп",
        "форма заявки",
        "заявка",
        "согласие",
        "реквизит",
        "доверенность",
        "карточка предприятия",
        "опись",
        "письмо",
        "ответ",
        "разъяснен",
        "разъяснение",
        "вопрос",
    ]

    if any(_name_contains_token(normalized, keyword) for keyword in high_priority):
        return 0
    if any(_name_contains_token(normalized, keyword) for keyword in medium_priority):
        return 1
    if any(_name_contains_token(normalized, keyword) for keyword in low_value):
        return 4

    suffix = path.suffix.lower()
    if suffix in {".pdf", ".docx", ".doc"}:
        return 2
    if suffix in {".xlsx", ".xls"}:
        return 3
    return 5


def _analysis_role(path: Path) -> str:
    normalized = _normalize_name(path.name)
    if path.suffix.lower() in {".zip", ".rar", ".7z"}:
        return "exclude"

    excluded_tokens = [
        "nda",
        "реквизит",
        "карточка предприятия",
        "анкета",
        "кп к заполнению",
        "согласие",
        "доверенность",
        "опись",
        "ответ",
        "разъяснен",
        "разъяснение",
        "вопрос",
        ".hash",
    ]
    if any(_name_contains_token(normalized, token) for token in excluded_tokens):
        return "exclude"

    tz_tokens = [
        "техническое задание",
        "технические требования",
        "техзадание",
        "тз",
        "техническая часть",
    ]
    if any(_name_contains_token(normalized, token) for token in tz_tokens):
        return "тз"

    notice_tokens = [
        "извещение",
        "документация о закупке",
        "документация закупки",
        "закупочная документация",
        "конкурсная документация",
        "документы о закупке",
        "rfp",
        "rfi",
    ]
    if any(_name_contains_token(normalized, token) for token in notice_tokens):
        return "извещение"

    participant_tokens = [
        "требования к участникам",
        "описание объекта закупки",
        "квалификац",
        "критерии",
        "оценк",
        "наличия сотрудников",
        "общие требования к участникам",
        "подтверждения наличия сотрудников",
        "форма подтверждения наличия сотрудников",
    ]
    if any(_name_contains_token(normalized, token) for token in participant_tokens):
        return "требования_к_участнику"

    contract_tokens = [
        "договор",
        "проект договора",
        "контракт",
        "типовой договор",
        "форма договора",
    ]
    if any(_name_contains_token(normalized, token) for token in contract_tokens):
        return "договор"

    price_tokens = [
        "обоснование",
        "нмц",
        "спецификация",
        "ткп",
        "смет",
        "расчет",
        "ценов",
        "стоимост",
    ]
    if any(_name_contains_token(normalized, token) for token in price_tokens):
        return "нмцк"

    suffix = path.suffix.lower()
    if suffix in {".pdf", ".docx"}:
        return "прочее"
    if suffix == ".doc":
        return "прочее"
    if suffix in {".xlsx", ".xls"}:
        return "нмцк"
    return "прочее"


def _name_contains_token(normalized: str, token: str) -> bool:
    token_norm = _normalize_name(token)
    if len(token_norm) <= 3:
        return re.search(rf"(?<![0-9a-zа-я]){re.escape(token_norm)}(?![0-9a-zа-я])", normalized) is not None
    return token_norm in normalized


def _analysis_role_with_content(path: Path) -> str:
    role = _analysis_role(path)
    if role not in {"прочее", "нмцк"}:
        return role
    sample = _normalize_name(_extract_text_sample(path)[:8000])
    if not sample:
        return role
    if (
        ("техническ" in sample and "задан" in sample)
        or ("предмет закупк" in sample and "перечень работ" in sample)
        or ("функциональн" in sample and "требован" in sample)
    ):
        return "тз"
    if (
        "требования к участник" in sample
        or "требования к поставщик" in sample
        or "квалификац" in sample
        or "наличие опыта" in sample
    ):
        return "требования_к_участнику"
    if "условия оплаты" in sample or "порядок расчет" in sample or "проект договора" in sample:
        return "договор"
    if "дата окончания подачи заяв" in sample or "извещение" in sample:
        return "извещение"
    return role


def _expand_archives(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in {".zip", ".rar", ".7z"}:
            continue
        extract_dir = path.parent / f"{path.stem}__extracted"
        marker = extract_dir / ".done"
        if marker.exists():
            continue
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            if suffix == ".zip":
                with zipfile.ZipFile(path) as archive:
                    archive.extractall(extract_dir)
            else:
                _extract_external_archive(path, extract_dir)
            marker.write_text("ok", encoding="utf-8")
        except Exception:
            # Leave the original archive untouched; matching will just skip extracted files.
            continue


def _extract_external_archive(path: Path, extract_dir: Path) -> None:
    unar = shutil.which("unar")
    if unar:
        subprocess.run(
            [unar, "-quiet", "-force-overwrite", "-output-directory", str(extract_dir), str(path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    seven_zip = shutil.which("7z") or shutil.which("7zz")
    if seven_zip:
        subprocess.run(
            [seven_zip, "x", f"-o{extract_dir}", "-y", str(path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    raise RuntimeError("no rar/7z extractor found")


def _score_file_for_tender(path: Path, tender: TenderRow) -> int:
    normalized_path = _normalize_name(str(path))
    text_sample = _normalize_name(_extract_text_sample(path))
    haystack = " ".join([normalized_path, text_sample])
    if tender.tender_id in haystack:
        return 100

    score = 0
    title_tokens = _significant_tokens(tender.title)
    matched_tokens = 0
    for token in title_tokens:
        if token in haystack:
            matched_tokens += 1
            score += 2 if len(token) >= 8 else 1

    if tender.customer and _normalize_name(tender.customer) in haystack:
        score += 3

    if matched_tokens == 0:
        score -= 3
    elif matched_tokens == 1:
        score -= 1

    path_name = _normalize_name(path.name)
    if _is_generic_file_name(path_name):
        score -= 2
    if _looks_like_project_specific_name(path_name):
        score += 2

    return score


def _build_scored_file_map(root: Path, tenders: list[TenderRow], matcher_provider) -> dict[str, list[Path]]:
    explicit_ids = set()
    for tender in tenders:
        if (root / tender.tender_id).exists():
            explicit_ids.add(tender.tender_id)
            continue
        normalized_title = _normalize_name(tender.title)
        if any(
            folder.is_dir() and normalized_title and normalized_title in _normalize_name(folder.name)
            for folder in root.iterdir()
        ):
            explicit_ids.add(tender.tender_id)
            continue
        if any(path.is_file() and tender.tender_id in str(path) for path in root.rglob("*")):
            explicit_ids.add(tender.tender_id)

    assignments: dict[str, list[tuple[int, Path]]] = {t.tender_id: [] for t in tenders}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.name == ".DS_Store":
            continue

        ranked: list[tuple[int, str]] = []
        for tender in tenders:
            if tender.tender_id in explicit_ids:
                continue
            score = _score_file_for_tender(path, tender)
            if score >= 4:
                ranked.append((score, tender.tender_id))

        ranked.sort(key=lambda item: (-item[0], item[1]))
        llm_choice = _match_file_to_tender_with_llm(path, tenders, ranked[:5], matcher_provider)
        if llm_choice is None:
            continue

        top_score = next((score for score, tender_id in ranked if tender_id == llm_choice), 8)
        assignments[llm_choice].append((top_score, path))

    result: dict[str, list[Path]] = {}
    for tender in tenders:
        matched = assignments.get(tender.tender_id, [])
        if not matched:
            continue
        matched.sort(key=lambda item: (-item[0], item[1].name.lower()))
        top_score = matched[0][0]
        result[tender.tender_id] = [path for score, path in matched if score >= max(8, top_score - 1)]
    return result


def _match_file_to_tender_with_llm(
    path: Path,
    tenders: list[TenderRow],
    ranked_candidates: list[tuple[int, str]],
    matcher_provider,
) -> str | None:
    file_text = _extract_text_sample(path)[:5000]
    if not file_text and path.suffix.lower() in {".rar", ".7z"}:
        return None

    candidate_map: dict[str, TenderRow] = {}
    for _, tender_id in ranked_candidates:
        tender = next((item for item in tenders if item.tender_id == tender_id), None)
        if tender is not None:
            candidate_map[tender_id] = tender

    if not candidate_map:
        candidate_map = {t.tender_id: t for t in tenders}

    options = []
    for tender in candidate_map.values():
        deadline = tender.deadline_at.strftime("%d.%m.%Y %H:%M") if tender.deadline_at else ""
        options.append(
            f'- "{tender.tender_id}": {tender.title} | заказчик: {tender.customer or "-"} | дедлайн: {deadline}'
        )

    prompt = (
        "Определи, к какому тендеру относится файл.\n"
        "Смотри только на имя файла и фрагмент текста файла.\n"
        "Выбери ровно один tender_id из списка или верни NONE, если файл нельзя надёжно привязать.\n"
        "Верни только JSON вида "
        '{"tender_id":"...", "confidence": 0-100, "reason":"..."}.\n\n'
        f"Имя файла: {path.name}\n\n"
        f"Фрагмент текста файла:\n{file_text or '[текст не извлечен]'}\n\n"
        "Кандидаты:\n"
        + "\n".join(options)
    )

    try:
        raw = matcher_provider.analyze_documents(prompt=prompt, files=[])
        payload = json.loads(raw)
    except Exception:
        return _fallback_ranked_choice(ranked_candidates)

    tender_id = str(payload.get("tender_id", "")).strip()
    confidence = int(payload.get("confidence", 0) or 0)
    if tender_id.upper() == "NONE" or confidence < 60:
        return None
    if tender_id not in candidate_map:
        return None
    return tender_id


def _fallback_ranked_choice(ranked_candidates: list[tuple[int, str]]) -> str | None:
    if not ranked_candidates:
        return None
    top_score, top_tender_id = ranked_candidates[0]
    second_score = ranked_candidates[1][0] if len(ranked_candidates) > 1 else 0
    if top_score >= 8 and top_score - second_score >= 3:
        return top_tender_id
    return None


def _significant_tokens(value: str) -> list[str]:
    normalized = _normalize_name(value)
    tokens = re.findall(r"[a-zа-я0-9]+", normalized)
    stopwords = {
        "оказание",
        "услуг",
        "работ",
        "закупка",
        "области",
        "продукты",
        "программные",
        "программного",
        "обеспечения",
        "разработка",
        "тестированию",
        "системы",
        "система",
        "информационных",
        "информационной",
        "проект",
        "проекта",
        "для",
        "по",
        "на",
        "и",
        "или",
        "в",
        "из",
        "ао",
        "ооо",
    }
    return [token for token in tokens if len(token) >= 4 and token not in stopwords]


def _is_generic_file_name(value: str) -> bool:
    generic_tokens = {
        "техническое задание",
        "технические требования",
        "документация о закупке",
        "проект договора",
        "извещение",
        "форма коммерческого предложения",
        "договор",
        "смета",
    }
    return any(token in value for token in generic_tokens)


def _looks_like_project_specific_name(value: str) -> bool:
    specific_markers = (
        "crm",
        "энергосфера",
        "сотиассо",
        "seo",
        "аммиач",
        "сейсмо",
        "лаборатор",
        "спрут",
        "аквилон",
        "мобильн",
    )
    return any(marker in value for marker in specific_markers)


@lru_cache(maxsize=512)
def _extract_text_sample(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            reader = PdfReader(str(path))
            parts = []
            for page in reader.pages[:3]:
                parts.append(page.extract_text() or "")
            return " ".join(parts)[:10000]
        if suffix == ".doc":
            return extract_doc_with_textutil(path)[:10000]
        if suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                xml_bytes = archive.read("word/document.xml")
            root = ElementTree.fromstring(xml_bytes)
            texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
            return " ".join(texts)[:10000]
        if suffix in {".txt", ".md"}:
            return path.read_text(encoding="utf-8", errors="ignore")[:10000]
    except Exception:
        return ""
    return ""
