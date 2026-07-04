from __future__ import annotations

from tender_agent.analysis_types import AnalysisPayload, ExtractedFacts


def derive_reason_codes(payload: AnalysisPayload, facts: ExtractedFacts) -> list[str]:
    codes: list[str] = []

    if payload.error_type == "network_error" or payload.decision == "Техсбой LLM":
        codes.append("TECH_LLM_NETWORK")
    if payload.decision == "Не брать":
        codes.append("DECISION_REJECT")
    elif payload.decision == "Уточнить":
        codes.append("DECISION_CLARIFY")
    elif payload.decision == "Брать":
        codes.append("DECISION_GO")

    signal_map = {
        "непрофильный тип закупки: промышленная автоматизация / электроучет": "NON_PROFILE_INDUSTRIAL",
        "непрофильный тип закупки: АСУТП/SCADA/PLC": "NON_PROFILE_AUTOMATION",
        "непрофильный тип закупки: строительство/монтаж": "NON_PROFILE_CONSTRUCTION",
        "аутсорсинг персонала, не проектная разработка": "OUTSTAFF_LIKE",
        "legacy/существующая ИС / модификация системы": "LEGACY_MODIFICATION",
        "подключение к существующей ИС (интеграция)": "INTEGRATION_ONLY",
        "enterprise/коробочный контур": "ENTERPRISE_BOXED",
        "тяжёлый ИБ/регуляторный контур": "INFOSEC_HEAVY",
        "стек вне core-профиля Flaton": "STACK_NON_CORE",
    }
    for signal in facts.triage_signals:
        code = signal_map.get(signal)
        if code:
            codes.append(code)
        if signal.startswith("сильное совпадение стека:"):
            codes.append("STACK_CORE_MATCH")
        if signal.startswith("низкий приоритет: тендер на сайт / веб-тематику"):
            codes.append("LOW_PRIORITY_WEBSITE")
        if signal.startswith("непрофильный тип закупки: обучение"):
            codes.append("NON_PROFILE_TRAINING")

    if not facts.payment:
        codes.append("PAYMENT_MISSING")
    if not facts.submission_requirements:
        codes.append("SUBMISSION_REQUIREMENTS_MISSING")
    if not facts.stack:
        codes.append("STACK_MISSING")
    if facts.participant_requirements.requires_sro:
        codes.append("REQUIRES_SRO")
    if facts.participant_requirements.min_turnover_rub:
        codes.append("REQUIRES_TURNOVER")
    if facts.participant_requirements.requires_analog_projects:
        codes.append("REQUIRES_ANALOG_PROJECTS")
    if facts.participant_requirements.requires_existing_system_modification_experience:
        codes.append("REQUIRES_LEGACY_EXPERIENCE")
    if facts.participant_requirements.required_roles:
        codes.append("REQUIRES_SPECIFIC_ROLES")
    if facts.participant_requirements.licenses_or_certs:
        codes.append("REQUIRES_LICENSES_OR_CERTS")
    if facts.document_roles.get("договор"):
        codes.append("CONTRACT_PRESENT")
    if facts.document_roles.get("тз"):
        codes.append("TZ_PRESENT")
    if facts.quality_flags:
        codes.append("QUALITY_FLAGS_PRESENT")
        for flag in facts.quality_flags:
            normalized = str(flag).strip().upper()
            if normalized:
                codes.append(f"QFLAG_{normalized}")
    if payload.llm_findings:
        if any(item.verified for item in payload.llm_findings):
            codes.append("LLM_FINDINGS_VERIFIED")
        if any(not item.verified for item in payload.llm_findings):
            codes.append("LLM_FINDINGS_UNVERIFIED")

    return _dedupe(codes)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = str(value or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result
