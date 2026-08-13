from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

from pypdf import PdfReader

from tender_agent.analysis_types import ExtractedFacts
from tender_agent.summary_builder import build_canonical_summary_points


@dataclass(frozen=True)
class EvidenceHit:
    field: str
    term: str
    file_name: str
    fragment: str
    page: int | None = None

    def render(self) -> str:
        rendered = f'Файл: {self.file_name}, фрагмент: "{self.fragment}"'
        if self.page:
            rendered += f", страница {self.page}"
        return rendered


def build_fact_columns(facts: ExtractedFacts | None) -> tuple[str, str, str, str]:
    if facts is None:
        return "", "", "", ""
    stack = ", ".join(facts.stack[:8]) if facts.stack else ""
    requirements_items = (
        facts.participant_requirements.turnover_texts[:2]
        + facts.participant_requirements.experience_texts[:2]
        + facts.participant_requirements.required_roles[:2]
        + facts.participant_requirements.licenses_or_certs[:2]
        + facts.turnover_requirements[:1]
        + facts.project_requirements[:1]
        + facts.team_requirements[:1]
        + facts.licenses[:1]
    )
    requirements = "; ".join(dict.fromkeys(requirements_items)) if requirements_items else ""
    docs_parts = []
    for key, values in (facts.document_roles or {}).items():
        if values:
            docs_parts.append(f"{key}: {', '.join(values[:2])}")
    docs = "; ".join(docs_parts) if docs_parts else ""
    payment = "; ".join(facts.payment[:3]) if facts.payment else ""
    return stack, requirements, docs, payment


def build_requirement_fact_columns(facts: ExtractedFacts | None) -> tuple[str, str, str, str, str, str]:
    if facts is None:
        return "", "", "", "", "", ""
    req = facts.participant_requirements
    sro = "Да" if req.requires_sro else ""
    turnover = ""
    if req.min_turnover_rub:
        turnover = f"от {req.min_turnover_rub:,} руб.".replace(",", " ")
    elif req.turnover_texts:
        turnover = req.turnover_texts[0]
    analog = "Да" if req.requires_analog_projects else ""
    legacy = "Да" if req.requires_existing_system_modification_experience else ""
    roles = ", ".join(req.required_roles[:4]) if req.required_roles else ""
    licenses = ", ".join(req.licenses_or_certs[:4]) if req.licenses_or_certs else ""
    return sro, turnover, analog, legacy, roles, licenses


def render_summary_points_with_evidence(
    *,
    facts: ExtractedFacts,
    decision: str,
    files: list[Path],
    deadline_at: datetime | None,
    summary_points: list[str] | None = None,
) -> list[str]:
    base = list(summary_points or build_canonical_summary_points(facts, decision))
    if not base:
        return []
    if deadline_at is None or deadline_at.date() < datetime.now().date():
        return base

    evidence_map = collect_summary_evidence(facts=facts, files=files)
    rendered: list[str] = []
    for line in base:
        if line.startswith("1. Стек:") and evidence_map.get("stack"):
            line = f"{line} ({evidence_map['stack'].render()})"
        elif line.startswith("2. Требования к контрагенту:") and evidence_map.get("requirements"):
            line = f"{line} ({evidence_map['requirements'].render()})"
        rendered.append(line)
    return rendered


def collect_summary_evidence(
    *,
    facts: ExtractedFacts,
    files: list[Path],
) -> dict[str, EvidenceHit]:
    hits: dict[str, EvidenceHit] = {}
    anchored_stack = _first_fact_hit(facts, "stack")
    anchored_req = _first_fact_hit(facts, "requirements")
    if anchored_stack:
        hits["stack"] = anchored_stack
    if anchored_req:
        hits["requirements"] = anchored_req
    if hits:
        return hits

    stack_term = (facts.stack or [""])[0]
    req_term = ""
    if facts.participant_requirements.required_roles:
        req_term = facts.participant_requirements.required_roles[0]
    elif facts.participant_requirements.turnover_texts:
        req_term = facts.participant_requirements.turnover_texts[0]
    elif facts.participant_requirements.experience_texts:
        req_term = facts.participant_requirements.experience_texts[0]
    elif facts.participant_requirements.licenses_or_certs:
        req_term = facts.participant_requirements.licenses_or_certs[0]
    elif facts.turnover_requirements:
        req_term = facts.turnover_requirements[0]
    elif facts.project_requirements:
        req_term = facts.project_requirements[0]
    elif facts.team_requirements:
        req_term = facts.team_requirements[0]
    elif facts.licenses:
        req_term = facts.licenses[0]

    stack_hit = find_evidence_for_term(files, stack_term, field="stack")
    req_hit = find_evidence_for_term(files, req_term, field="requirements") if req_term else None
    if stack_hit:
        hits["stack"] = stack_hit
    if req_hit:
        hits["requirements"] = req_hit
    return hits


def find_evidence_for_term(files: list[Path], term: str, *, field: str) -> EvidenceHit | None:
    if not term:
        return None
    for normalized_term in expand_term_variants(term):
        for path in files:
            if not path.exists():
                continue
            suffix = path.suffix.lower()
            if suffix == ".pdf":
                hit = _find_in_pdf(path, normalized_term, field)
            elif suffix == ".docx":
                hit = _find_in_docx(path, normalized_term, field)
            elif suffix == ".xlsx":
                hit = _find_in_xlsx(path, normalized_term, field)
            else:
                hit = _find_in_text(path, normalized_term, field)
            if hit:
                return hit
    return None


def _first_fact_hit(facts: ExtractedFacts, field: str) -> EvidenceHit | None:
    for item in (facts.fact_hits or {}).get(field, []):
        fragment = str(getattr(item, "fragment", "") or "").strip()
        file_name = str(getattr(item, "file_name", "") or "").strip()
        if not fragment or not file_name:
            continue
        return EvidenceHit(
            field=field,
            term=str(getattr(item, "term", "") or ""),
            file_name=file_name,
            fragment=fragment,
            page=getattr(item, "page", None),
        )
    return None


def expand_term_variants(term: str) -> list[str]:
    normalized = term.casefold().replace("ё", "е")
    variants = [normalized]
    if normalized == "tia portal":
        variants.extend(["portal 13", "portal 15", "portal 16", "tia"])
    if normalized == "codesys":
        variants.extend(["codesys 2.3", "codesys 3.5"])
    if normalized == "scada":
        variants.extend(["wincc", "movicon"])
    if normalized == "lad":
        variants.extend(["ld"])
    if normalized == "промышленная автоматизация":
        variants.extend(["пнр", "пусконалад", "смр"])
    if normalized == "электроучет":
        variants.extend(["аскуэ", "учета электроэнергии", "точкам учета"])
    seen: set[str] = set()
    out: list[str] = []
    for item in variants:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _find_in_pdf(path: Path, term: str, field: str) -> EvidenceHit | None:
    try:
        reader = PdfReader(str(path))
    except Exception:
        return None
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").replace("\n", " ")
        normalized = text.casefold().replace("ё", "е")
        pos = normalized.find(term)
        if pos >= 0:
            snippet = text[max(0, pos - 60): pos + len(term) + 60].strip()
            return EvidenceHit(field=field, term=term, file_name=path.name, fragment=snippet, page=index)
    return None


def _find_in_docx(path: Path, term: str, field: str) -> EvidenceHit | None:
    try:
        with zipfile.ZipFile(path) as archive:
            xml_bytes = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml_bytes)
        texts = [node.text or "" for node in root.iter() if node.tag.endswith("}t")]
        text = " ".join(texts)
        normalized = text.casefold().replace("ё", "е")
        pos = normalized.find(term)
        if pos >= 0:
            snippet = text[max(0, pos - 60): pos + len(term) + 60].strip()
            return EvidenceHit(field=field, term=term, file_name=path.name, fragment=snippet)
    except Exception:
        return None
    return None


def _find_in_xlsx(path: Path, term: str, field: str) -> EvidenceHit | None:
    try:
        import openpyxl

        wb = openpyxl.load_workbook(path, data_only=True)
        for sheet in wb.worksheets:
            for row in sheet.iter_rows(values_only=True):
                for value in row:
                    if value is None:
                        continue
                    text = str(value)
                    normalized = text.casefold().replace("ё", "е")
                    pos = normalized.find(term)
                    if pos >= 0:
                        snippet = text[max(0, pos - 60): pos + len(term) + 60].strip()
                        return EvidenceHit(field=field, term=term, file_name=path.name, fragment=snippet)
    except Exception:
        return None
    return None


def _find_in_text(path: Path, term: str, field: str) -> EvidenceHit | None:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    normalized = text.casefold().replace("ё", "е")
    pos = normalized.find(term)
    if pos >= 0:
        snippet = text[max(0, pos - 60): pos + len(term) + 60].strip().replace("\n", " ")
        return EvidenceHit(field=field, term=term, file_name=path.name, fragment=snippet)
    return None
