from __future__ import annotations

import re

from tender_agent.analysis_types import ExtractedFacts


def build_canonical_summary_points(facts: ExtractedFacts, decision: str) -> list[str]:
    if decision == "Не брать":
        return [
            f"1. Стек: {_build_stack(facts)}",
            f"2. Требования к контрагенту: {_build_requirements(facts)}",
        ]
    return [
        f"1. Риски: {_build_risks(facts)}",
        f"2. Стек: {_build_stack(facts)}",
        f"3. Требования к контрагенту: {_build_requirements(facts)}",
        f"4. Документация: {_build_docs(facts)}",
        f"5. Оплата и обеспечение: {_build_payment(facts)}",
    ]


def _build_risks(facts: ExtractedFacts) -> str:
    non_profile_map = {
        "обучение / образовательные услуги": "непрофильный тендер: обучение",
        "сайт / маркетинг / контент": "непрофильный тендер: маркетинг/контент",
        "поддержка / сопровождение": "непрофильный тендер: поддержка/сопровождение",
        "поставка оборудования / лицензий": "непрофильный тендер: поставка/лицензии",
    }
    if facts.procurement_type in non_profile_map:
        return non_profile_map[facts.procurement_type]

    items: list[str] = []
    for signal in facts.triage_signals:
        mapped = _map_risk_signal(signal)
        if mapped and mapped not in items:
            items.append(mapped)
    if (
        "аутсорсинг персонала, не проектная разработка" in facts.triage_signals
        and not facts.stack
    ):
        items.append(
            "нужны специалисты: backend (Python), frontend (React), mobile (Flutter), QA, DevOps, аналитик/BA"
        )
    if not facts.payment:
        items.append("в доступных документах не раскрыты условия оплаты")
    return "; ".join(items[:3]) if items else "существенные риски не выявлены"


def _build_stack(facts: ExtractedFacts) -> str:
    stack = _dedupe(facts.stack)
    inferred = _infer_stack_from_context(facts)
    if inferred:
        stack = _dedupe(inferred + stack)
    if not stack:
        return "явный стек не извлечён"
    return ", ".join(stack[:5])


def _build_requirements(facts: ExtractedFacts) -> str:
    structured = _build_structured_requirements(facts)
    if structured:
        return structured
    items: list[str] = []
    items.extend(_clean_requirement_fragments(facts.turnover_requirements, limit=2))
    items.extend(_clean_requirement_fragments(facts.project_requirements, limit=3))
    items.extend(_clean_requirement_fragments(facts.team_requirements, limit=2))
    items.extend(_dedupe(facts.licenses)[:2])
    items = _dedupe(items)
    if items:
        return "; ".join(items[:4])
    return "не указано"


def _build_structured_requirements(facts: ExtractedFacts) -> str:
    req = facts.participant_requirements
    items: list[str] = []
    if req.requires_sro:
        items.append("СРО")
    if req.min_turnover_rub:
        items.append(f"оборот от {req.min_turnover_rub:,} руб.".replace(",", " "))
    elif req.turnover_texts:
        items.extend(_clean_requirement_fragments(req.turnover_texts, limit=1))
    if req.requires_existing_system_modification_experience:
        items.append("опыт доработки/модификации существующих систем")
    if req.experience_texts:
        items.extend(_clean_requirement_fragments(req.experience_texts, limit=2))
    elif req.requires_analog_projects:
        items.append("релевантный опыт")
    if req.required_roles:
        role_items = list(req.required_roles[:3])
        if "электротехническая лаборатория" in role_items:
            role_items = [
                "наличие электротехнической лаборатории" if item == "электротехническая лаборатория" else item
                for item in role_items
            ]
        items.append(", ".join(role_items))
    elif req.requires_team_documents and facts.team_requirements:
        items.extend(_clean_requirement_fragments(facts.team_requirements, limit=1))
    if req.other_requirements:
        items.extend(_dedupe(req.other_requirements)[:1])
    if req.licenses_or_certs:
        normalized = [item for item in req.licenses_or_certs if item != "СРО"]
        if normalized:
            items.extend(_dedupe(normalized)[:2])
    items = _dedupe(items)
    return "; ".join(items[:4]) if items else ""


def _infer_stack_from_context(facts: ExtractedFacts) -> list[str]:
    inferred: list[str] = []
    signals = [item.casefold().replace("ё", "е") for item in facts.triage_signals]
    procurement = str(facts.procurement_type or "").casefold().replace("ё", "е")
    req_roles = [item.casefold().replace("ё", "е") for item in facts.participant_requirements.required_roles]

    if any("промышленная автоматизация / электроучет" in signal for signal in signals):
        inferred.extend(["АСКУЭ", "промышленная автоматизация"])
    if any("асутп/scada/plc" in signal for signal in signals):
        inferred.extend(["SCADA", "PLC"])
    if "обучение / образовательные услуги" in procurement:
        inferred.append("не разработка ПО / обучение")
    if any("аутсорсинг персонала" in signal for signal in signals):
        if any("backend (python)" in role for role in req_roles) and "Python" not in inferred:
            inferred.append("Python")
        if any("frontend (react)" in role for role in req_roles) and "React" not in inferred:
            inferred.append("React")
        if any("mobile (flutter)" in role for role in req_roles) and "Flutter" not in inferred:
            inferred.append("Flutter")
    return _dedupe(inferred)


def _build_docs(facts: ExtractedFacts) -> str:
    effort_score = 1
    required_items: list[str] = list(facts.submission_requirements or ["заявка"])

    if facts.document_roles.get("нмцк"):
        effort_score += 1
    if facts.project_requirements:
        effort_score += 1
    if facts.turnover_requirements:
        effort_score += 1
    if facts.team_requirements:
        effort_score += 1
    if facts.licenses:
        effort_score += 2
    if facts.document_roles.get("требования_к_участнику") or has_bid_submission_signals(facts):
        effort_score += 1
    if _has_security_or_compliance_burden(facts):
        effort_score += 1
        required_items.append("подтверждение ИБ/соответствия требованиям")

    effort_label = _submission_effort_label(effort_score)
    required_items = _dedupe(required_items)
    if effort_label == "требует проверки":
        return "объём заявки требует проверки; минимум: заявка"
    if required_items:
        return f"объём заявки {effort_label}; вероятно нужны: {', '.join(required_items[:4])}"
    return f"объём заявки {effort_label}; состав документов требует проверки"


def _build_payment(facts: ExtractedFacts) -> str:
    if not facts.payment:
        return "в доступных документах не раскрыты условия оплаты и обеспечения"
    normalized = " ; ".join(item.lower() for item in facts.payment if item)
    parts: list[str] = []
    if "по факту выполн" in normalized:
        parts.append("постоплата по факту")
    if "предоплат" in normalized or "аванс" in normalized:
        parts.append("есть условия аванса/предоплаты")
    if "30 календарных д" in normalized:
        parts.append("оплата 30 календарных дней")
    elif "30 рабочих д" in normalized:
        parts.append("оплата 30 рабочих дней")
    elif "7 рабочих д" in normalized:
        parts.append("оплата 7 рабочих дней")
    if "банковск" in normalized or "обеспечени" in normalized:
        parts.append("есть требования к обеспечению")
    parts = _dedupe(parts)
    if parts:
        return "; ".join(parts)
    return "условия оплаты и обеспечения требуют проверки"


def _license_submission_item(facts: ExtractedFacts) -> str:
    normalized = {item.casefold().replace("ё", "е") for item in facts.licenses}
    if "сро" in normalized:
        return "подтверждение лицензий/СРО"
    if normalized:
        return "подтверждение лицензий/сертификатов"
    return "подтверждение лицензий/СРО"


def _map_risk_signal(signal: str) -> str:
    if "legacy/существующая ИС / модификация системы" in signal:
        return "доработка существующей ИС"
    if "подключение к существующей ИС" in signal:
        return "подключение к существующей ИС (интеграция)"
    if "непрофильный тип закупки: строительство/монтаж" in signal:
        return "строительно-монтажный контур"
    if "enterprise/коробочный контур" in signal:
        return "коробочный/enterprise-контур"
    if "тяжёлый ИБ/регуляторный контур" in signal:
        return "жёсткий ИБ-контур"
    if "непрофильный тип закупки: промышленная автоматизация / электроучет" in signal:
        return "промышленная автоматизация / электроучет"
    if "аутсорсинг персонала" in signal:
        return "предоставление специалистов / почасовая модель"
    if "низкий приоритет: тендер на сайт / веб-тематику" in signal:
        return "низкий приоритет по бюджету и типу web-проекта"
    if signal.startswith("непрофильный тип закупки: "):
        return signal.replace("непрофильный тип закупки: ", "")
    if "стек вне core-профиля" in signal:
        return "стек вне core-профиля Flaton"
    return signal.strip()


def _missing_key_docs(facts: ExtractedFacts) -> bool:
    return not facts.document_roles.get("извещение") or not facts.document_roles.get("договор")


def has_bid_submission_signals(facts: ExtractedFacts) -> bool:
    return bool(
        facts.document_roles.get("извещение")
        or facts.document_roles.get("требования_к_участнику")
        or facts.project_requirements
        or facts.turnover_requirements
        or facts.team_requirements
        or facts.licenses
        or facts.participant_requirements.submission_docs
    )


def _has_security_or_compliance_burden(facts: ExtractedFacts) -> bool:
    joined = " ".join(
        facts.triage_signals + facts.licenses + facts.participant_requirements.licenses_or_certs
    ).casefold().replace("ё", "е")
    return "иб" in joined or "фстэк" in joined or "фсб" in joined or "регулятор" in joined


def _submission_effort_label(score: int) -> str:
    if score >= 6:
        return "высокий"
    if score >= 3:
        return "средний"
    return "требует проверки"


def _dedupe(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned:
            continue
        key = cleaned.casefold().replace("ё", "е")
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _clean_requirement_fragments(items: list[str], *, limit: int) -> list[str]:
    cleaned: list[str] = []
    for item in items[:limit]:
        normalized = " ".join(str(item).split())
        lowered = normalized.casefold().replace("ё", "е")
        specific_experience = _normalize_specific_experience_requirement(normalized, lowered)
        if specific_experience:
            cleaned.append(specific_experience)
            continue
        if _should_keep_specific_requirement(normalized, lowered):
            cleaned.append(normalized)
            continue
        if "фстэк" in lowered or "фсб" in lowered:
            cleaned.append(normalized)
            continue
        if "сертификат" in lowered:
            cleaned.append(normalized)
            continue
        if "референс" in lowered:
            cleaned.append("референс-лист")
        elif "аналогич" in lowered or "опыт" in lowered:
            cleaned.append("релевантный опыт")
        elif "электротехническ" in lowered and "лаборатор" in lowered:
            cleaned.append("электротехническая лаборатория")
        elif "аттестован" in lowered and "персонал" in lowered:
            cleaned.append("аттестованный персонал")
        elif "оборот" in lowered or "выручк" in lowered:
            cleaned.append(normalized)
        elif "команд" in lowered or "персонал" in lowered or "штат" in lowered:
            cleaned.append("требования к команде")
        else:
            cleaned.append(normalized)
    return cleaned


def _normalize_specific_experience_requirement(normalized: str, lowered: str) -> str:
    if "опыт проведения исследований в категории" not in lowered:
        return ""
    text = re.sub(
        r"(?i)\s*участники закупки должны.*$",
        "",
        normalized,
    ).strip(" ;,.")
    text = re.sub(
        r"(?i)^опыт проведения исследований в категории\s+",
        "опыт исследований в категории ",
        text,
    )
    return text


def _should_keep_specific_requirement(normalized: str, lowered: str) -> bool:
    specific_tokens = (
        "python",
        "react",
        "flutter",
        "vue",
        "angular",
        "django",
        "postgresql",
        "postgres",
        "oracle",
        "1с",
        "java",
        "kotlin",
        "swift",
        "docker",
        "kubernetes",
        "аналитик",
        "тестиров",
        "qa",
        "devops",
        "архитектор",
        "тимлид",
        "инженер",
        "геосервис",
        "бронирован",
        "электротехническ",
        "лаборатор",
        "аттестован",
        "сро",
        "фстэк",
        "фсб",
        "сертификат",
        "iso",
    )
    if any(token in lowered for token in specific_tokens):
        return True
    if any(char.isdigit() for char in normalized):
        return True
    return False
