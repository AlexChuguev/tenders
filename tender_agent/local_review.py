from __future__ import annotations

import re
import zipfile
import json
from functools import lru_cache
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader
from tender_agent.analysis import TenderAnalyzer
from tender_agent.config import Settings
from tender_agent.excel_loader import load_tenders
from tender_agent.excel_writer import ExcelWriter
from tender_agent.llm import create_llm_provider
from tender_agent.models import TenderAnalysisResult, TenderRow


class LocalTenderReviewer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.analyzer = TenderAnalyzer(
            provider_name=settings.llm_provider,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            prompt_template_path=settings.prompt_template_path,
        )
        self.matcher_provider = create_llm_provider(
            provider_name=settings.llm_provider,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            base_url=settings.llm_base_url or None,
        )
        self.sheet_writer = ExcelWriter(output_path=settings.output_xlsx)

    def run(self) -> None:
        _expand_archives(self.settings.local_files_dir)
        tenders = load_tenders(
            xls_path=self.settings.input_xls,
            url_column=self.settings.platform_tender_url_column,
            id_column=self.settings.platform_tender_id_column,
            title_column=self.settings.platform_tender_title_column,
        )
        target_date = _resolve_target_date(self.settings.review_target_date)
        tenders = [t for t in tenders if t.deadline_at and t.deadline_at.date() == target_date]
        tenders.sort(key=lambda item: (item.deadline_at is None, item.deadline_at))

        if self.settings.tender_skip:
            tenders = tenders[self.settings.tender_skip :]
        if self.settings.tender_limit is not None:
            tenders = tenders[: self.settings.tender_limit]

        self.settings.local_files_dir.mkdir(parents=True, exist_ok=True)
        self.sheet_writer.ensure_header()
        scored_matches = _build_scored_file_map(
            self.settings.local_files_dir,
            tenders,
            self.matcher_provider,
        )
        for tender in tenders:
            result = self._process_one(tender, scored_matches.get(tender.tender_id, []))
            if result is not None:
                self.sheet_writer.append_result(result)

    def _process_one(self, tender: TenderRow, scored_files: list[Path]) -> TenderAnalysisResult | None:
        files = _find_local_files(self.settings.local_files_dir, tender, scored_files)
        if not files:
            return None
        files = _prioritize_files(files)[: self.settings.max_files_per_tender]

        analysis = self.analyzer.analyze(tender.url, files)
        return TenderAnalysisResult(
            tender_id=tender.tender_id,
            title=tender.title,
            url=tender.url,
            deadline_at=tender.deadline_at,
            customer=tender.customer,
            customer_inn=tender.customer_inn,
            status="ok",
            classification_tag=analysis.classification_tag,
            confidence_percent=analysis.confidence_percent,
            classification_comment=analysis.classification_comment,
            downloaded_files=[str(path) for path in files],
            analysis_markdown=analysis.analysis_markdown,
        )


def _resolve_target_date(value: str) -> date:
    raw = value.strip().lower()
    today = datetime.now().date()
    if raw == "yesterday":
        return today - timedelta(days=1)
    return datetime.strptime(value, "%Y-%m-%d").date()


def _find_local_files(root: Path, tender: TenderRow, scored_files: list[Path]) -> list[Path]:
    direct_folder = root / tender.tender_id
    if direct_folder.exists():
        return sorted(path for path in direct_folder.rglob("*") if path.is_file())

    id_match = sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and tender.tender_id in str(path)
    )
    if id_match:
        return id_match

    normalized_title = _normalize_name(tender.title)
    for folder in root.iterdir():
        if folder.is_dir() and normalized_title and normalized_title in _normalize_name(folder.name):
            return sorted(path for path in folder.rglob("*") if path.is_file())

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
    preferred_keywords = [
        "техничес",
        "тз",
        "извещ",
        "документац",
        "договор",
        "коммерчес",
        "требован",
    ]

    def sort_key(path: Path) -> tuple[int, int, str]:
        normalized = _normalize_name(path.name)
        keyword_score = sum(1 for keyword in preferred_keywords if keyword in normalized)
        ext_bonus = 1 if path.suffix.lower() in {".pdf", ".docx", ".doc"} else 0
        return (-keyword_score, -ext_bonus, path.name.lower())

    return sorted(files, key=sort_key)


def _expand_archives(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() != ".zip":
            continue
        extract_dir = path.parent / f"{path.stem}__extracted"
        marker = extract_dir / ".done"
        if marker.exists():
            continue
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(path) as archive:
                archive.extractall(extract_dir)
            marker.write_text("ok", encoding="utf-8")
        except Exception:
            # Leave the original archive untouched; matching will just skip extracted files.
            continue


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
