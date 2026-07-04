from __future__ import annotations

from tender_agent.analysis_types import AnalysisPayload, ExtractedFacts
from tender_agent.summary_builder import build_canonical_summary_points


def apply_quality_gates(payload: AnalysisPayload, facts: ExtractedFacts) -> AnalysisPayload:
    flags = list(facts.quality_flags)
    stack_line = _find_summary_line(payload.summary_points, "Стек:")
    requirements_line = _find_summary_line(payload.summary_points, "Требования к контрагенту:")
    docs_line = _find_summary_line(payload.summary_points, "Документация:")

    if (
        any(token in " ".join(facts.key_files).casefold().replace("ё", "е") for token in ("аскуэ", "пнр", "смр"))
        and not facts.stack
    ):
        flags.append("industrial_markers_without_stack")

    if facts.stack and _is_generic_stack_line(stack_line):
        flags.append("generic_stack_summary")

    if _has_inferable_stack_context(facts) and _is_generic_stack_line(stack_line):
        flags.append("contextual_stack_missing_in_summary")

    if _has_specific_requirement(facts) and _is_generic_requirements_line(requirements_line):
        flags.append("generic_requirements_summary")

    if _has_structured_requirement_context(facts) and _is_overgeneric_requirements_line(requirements_line):
        flags.append("overgeneric_requirements_summary")

    if len(facts.submission_requirements) > 1 and _is_generic_docs_line(docs_line):
        flags.append("generic_docs_summary")

    if (
        "legacy/существующая ИС / модификация системы" in facts.triage_signals
        and not any("доработ" in line.casefold().replace("ё", "е") for line in payload.summary_points)
    ):
        flags.append("legacy_signal_missing_in_summary")

    if len(facts.licenses) == 1 and facts.licenses[0] == "ГОСТ":
        flags.append("gost_is_not_participant_requirement")

    extraction_flags = _extraction_quality_flags(payload)
    flags.extend(extraction_flags)

    if payload.error_type == "network_error":
        payload.decision = "Техсбой LLM"
        payload.confidence_percent = 0
        payload.summary_points = [
            "Техсбой LLM",
            "Требуется повторный прогон анализа",
        ]
        payload.completeness_label = facts.completeness_label
        facts.quality_flags = _dedupe(flags + ["network_error"])
        return payload

    if flags:
        facts.quality_flags = _dedupe(flags)
        payload.summary_points = build_canonical_summary_points(facts, payload.decision)
        if "industrial_markers_without_stack" in flags:
            payload.decision = "Уточнить"
            payload.confidence_percent = min(payload.confidence_percent, 60)
        if "gost_is_not_participant_requirement" in flags:
            facts.licenses = []
            payload.summary_points = build_canonical_summary_points(facts, payload.decision)
        if any(flag in flags for flag in {"generic_stack_summary", "generic_requirements_summary", "generic_docs_summary"}):
            payload.summary_points = build_canonical_summary_points(facts, payload.decision)
        if any(
            flag in flags
            for flag in {
                "extraction_empty_key_file",
                "extraction_very_short_key_file",
                "all_extracted_files_short",
            }
        ):
            payload.decision = "Уточнить"
            payload.confidence_percent = min(payload.confidence_percent, 40)
        if "files_dropped_by_llm_budget" in flags:
            payload.confidence_percent = min(payload.confidence_percent, 60)
    else:
        facts.quality_flags = []

    return payload


def _find_summary_line(lines: list[str], marker: str) -> str:
    marker_norm = marker.casefold().replace("ё", "е")
    for line in lines:
        normalized = str(line or "").casefold().replace("ё", "е")
        if marker_norm in normalized:
            return str(line)
    return ""


def _extraction_quality_flags(payload: AnalysisPayload) -> list[str]:
    report = payload.extraction_report or {}
    files = report.get("files")
    if not isinstance(files, list):
        files = []
    flags: list[str] = []
    text_statuses = []
    for row in files:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "")
        text_statuses.append(status)
        source_name = str(row.get("source_name") or row.get("name") or "").casefold().replace("ё", "е")
        key_file = _looks_like_key_source_file(source_name)
        if key_file and status == "empty_text":
            flags.append("extraction_empty_key_file")
        elif key_file and status == "very_short_text":
            flags.append("extraction_very_short_key_file")
    if text_statuses and all(status in {"empty_text", "very_short_text"} for status in text_statuses):
        flags.append("all_extracted_files_short")
    budget = report.get("llm_budget_report")
    if isinstance(budget, dict) and budget.get("dropped_files"):
        flags.append("files_dropped_by_llm_budget")
    return _dedupe(flags)


def _looks_like_key_source_file(name: str) -> bool:
    return any(
        token in name
        for token in (
            "тз",
            "техническ",
            "задан",
            "требован",
            "закупочн",
            "документац",
            "извещ",
            "приложение",
            "проект договор",
        )
    )


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = str(value or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _is_generic_stack_line(line: str) -> bool:
    lowered = str(line or "").casefold().replace("ё", "е")
    return any(
        token in lowered
        for token in (
            "явный стек не извлечен",
            "явный стек не извлечён",
            "стек: не определ",
            "требуемый стек не определ",
        )
    )


def _is_generic_requirements_line(line: str) -> bool:
    lowered = str(line or "").casefold().replace("ё", "е")
    generic_tokens = (
        "требования к команде",
        "релевантный опыт",
        "не указано",
        "требования по опыту и финансам требуют проверки",
    )
    return any(token in lowered for token in generic_tokens)


def _is_overgeneric_requirements_line(line: str) -> bool:
    lowered = str(line or "").casefold().replace("ё", "е")
    generic_tokens = (
        "требования к команде",
        "сертификаты",
        "лицензия",
        "не указано",
    )
    return any(token == lowered.strip() or token in lowered for token in generic_tokens)


def _is_generic_docs_line(line: str) -> bool:
    lowered = str(line or "").casefold().replace("ё", "е")
    return (
        "минимум: заявка" in lowered
        or lowered.endswith("вероятно нужны: заявка")
        or "состав документов требует проверки" in lowered
    )


def _has_specific_requirement(facts: ExtractedFacts) -> bool:
    req = facts.participant_requirements
    if (
        req.requires_sro
        or req.min_turnover_rub is not None
        or req.requires_analog_projects
        or req.requires_existing_system_modification_experience
        or req.required_roles
        or req.licenses_or_certs
    ):
        return True
    for item in [*facts.turnover_requirements, *facts.project_requirements, *facts.team_requirements, *facts.licenses]:
        lowered = str(item or "").casefold().replace("ё", "е")
        if not lowered:
            continue
        if any(
            token in lowered
            for token in (
                "python",
                "react",
                "flutter",
                "vue",
                "angular",
                "postgres",
                "oracle",
                "1с",
                "java",
                "kotlin",
                "swift",
                "docker",
                "kubernetes",
                "devops",
                "qa",
                "тестиров",
                "аналитик",
                "архитектор",
                "электротехническ",
                "лаборатор",
                "аттестован",
                "фстэк",
                "фсб",
                "сро",
                "сертификат",
                "iso",
            )
        ):
            return True
        if any(char.isdigit() for char in str(item)):
            return True
    return False


def _has_structured_requirement_context(facts: ExtractedFacts) -> bool:
    req = facts.participant_requirements
    return bool(
        req.requires_sro
        or req.min_turnover_rub is not None
        or req.requires_analog_projects
        or req.requires_existing_system_modification_experience
        or req.required_roles
        or req.licenses_or_certs
        or req.other_requirements
    )


def _has_inferable_stack_context(facts: ExtractedFacts) -> bool:
    procurement = str(facts.procurement_type or "").casefold().replace("ё", "е")
    if "обучение / образовательные услуги" in procurement:
        return True
    signals = [item.casefold().replace("ё", "е") for item in facts.triage_signals]
    if any(
        token in signal
        for signal in signals
        for token in (
            "промышленная автоматизация / электроучет",
            "асутп/scada/plc",
            "аутсорсинг персонала",
        )
    ):
        return True
    req_roles = [item.casefold().replace("ё", "е") for item in facts.participant_requirements.required_roles]
    return any(role in req_roles for role in ("backend (python)", "frontend (react)", "mobile (flutter)"))
