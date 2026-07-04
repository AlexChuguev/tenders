from __future__ import annotations

import re

from tender_agent.analysis_types import AnalysisPayload, ExtractedFacts
from tender_agent.llm_findings import merge_verified_findings_into_summary
from tender_agent.policy import TriagePolicy, load_triage_policy
from tender_agent.quality_gates import apply_quality_gates
from tender_agent.reason_codes import derive_reason_codes
from tender_agent.summary_builder import build_canonical_summary_points


def build_priority_hint(
    tender_title: str,
    tender_price_rub: float | None,
    policy: TriagePolicy | None = None,
) -> str:
    policy = policy or load_triage_policy()
    if tender_price_rub is None:
        return ""
    normalized = _ru_normalize(tender_title)
    if tender_price_rub < policy.priority_hint.site_budget_threshold_rub and any(
        token in normalized for token in policy.priority_hint.site_tokens
    ):
        if not any(token in normalized for token in policy.priority_hint.strong_product_tokens):
            return policy.priority_hint.low_priority_message
    return ""


def build_triage_signals(
    tender_title: str,
    tender_price_rub: float | None,
    procurement_type: str,
    stack: list[str],
    licenses: list[str],
    lower_text: str,
    policy: TriagePolicy | None = None,
) -> list[str]:
    policy = policy or load_triage_policy()
    signals: list[str] = []
    priority_hint = build_priority_hint(
        tender_title=tender_title,
        tender_price_rub=tender_price_rub,
        policy=policy,
    )
    if priority_hint:
        signals.append(priority_hint)
    if procurement_type in policy.non_profile_procurement_types:
        signals.append(f"непрофильный тип закупки: {procurement_type}")

    hard_enterprise_tokens = [token for token in policy.enterprise_hard_tokens if token in lower_text]
    soft_enterprise_tokens = [token for token in policy.enterprise_soft_tokens if token in lower_text]
    title_lower = _ru_normalize(tender_title)
    product_context_text = f"{title_lower} {lower_text}"
    development_context = any(token in product_context_text for token in policy.development_context_tokens)
    hard_enterprise_tokens = [
        token
        for token in hard_enterprise_tokens
        if not _is_integration_only_enterprise_token(token, lower_text, development_context)
    ]
    soft_enterprise_tokens = [
        token
        for token in soft_enterprise_tokens
        if not _is_integration_only_enterprise_token(token, lower_text, development_context)
    ]
    if hard_enterprise_tokens:
        signals.append("enterprise/коробочный контур")
    if len(soft_enterprise_tokens) >= 2 and not development_context:
        if "enterprise/коробочный контур" not in signals:
            signals.append("enterprise/коробочный контур")

    hard_infosec_tokens = [token for token in policy.infosec_hard_tokens if token in lower_text]
    medium_infosec_tokens = [token for token in policy.infosec_medium_tokens if token in lower_text]
    explicit_infosec_licenses = any(token in licenses for token in policy.infosec_license_tokens)
    if (
        development_context
        and procurement_type == "разработка ПО / цифрового продукта"
        and hard_infosec_tokens == ["криптограф"]
        and not explicit_infosec_licenses
    ):
        hard_infosec_tokens = []
    if explicit_infosec_licenses or hard_infosec_tokens:
        signals.append("тяжёлый ИБ/регуляторный контур")
    if len(medium_infosec_tokens) >= 2 and procurement_type != "разработка ПО / цифрового продукта":
        if "тяжёлый ИБ/регуляторный контур" not in signals:
            signals.append("тяжёлый ИБ/регуляторный контур")

    legacy_modification_detected = _detect_legacy_modification(lower_text, policy)
    if legacy_modification_detected:
        signals.append("legacy/существующая ИС / модификация системы")

    if _detect_integration_only(lower_text):
        signals.append("подключение к существующей ИС (интеграция)")

    if any(token in lower_text for token in policy.construction_tokens) and not legacy_modification_detected:
        signals.append("непрофильный тип закупки: строительство/монтаж")

    if (
        any(token in lower_text for token in policy.outstaffing_tokens)
        or any(token in title_lower for token in policy.outstaffing_tokens)
        or _detect_outstaffing_by_rate(lower_text)
    ):
        signals.append("аутсорсинг персонала, не проектная разработка")

    if any(
        token in stack
        for token in {"SCADA", "PLC", "TIA Portal", "CoDeSys", "IEC 61131-3", "LAD", "SCL", "LD", "ST"}
    ):
        signals.append("непрофильный тип закупки: АСУТП/SCADA/PLC")

    if any(token in stack for token in {"АСКУЭ", "электроучет", "промышленная автоматизация"}):
        signals.append("непрофильный тип закупки: промышленная автоматизация / электроучет")

    core_stack_match = [token for token in stack if token in policy.core_stack]
    if len(core_stack_match) >= 2:
        signals.append(f"сильное совпадение стека: {', '.join(core_stack_match)}")

    if (
        stack
        and not any(token in stack for token in policy.core_stack)
        and not _is_integration_only_non_core_stack(stack, lower_text, development_context)
    ):
        if any(token in stack for token in policy.non_core_stack_tokens):
            signals.append("стек вне core-профиля Flaton")
    return signals


def postprocess_payload(
    payload: AnalysisPayload,
    facts: ExtractedFacts,
    policy: TriagePolicy | None = None,
) -> AnalysisPayload:
    policy = policy or load_triage_policy()
    decision = payload.decision
    confidence = derive_confidence(payload, facts, policy=policy)

    if any("непрофильный тип закупки" in signal for signal in facts.triage_signals):
        if facts.procurement_type in policy.non_profile_procurement_types:
            decision = "Не брать"
            confidence = max(confidence, policy.confidence.non_profile_floor)

    if "непрофильный тип закупки: строительство/монтаж" in facts.triage_signals:
        decision = "Не брать"
        confidence = max(confidence, policy.confidence.construction_floor)

    if "непрофильный тип закупки: промышленная автоматизация / электроучет" in facts.triage_signals:
        decision = "Не брать"
        confidence = max(confidence, policy.confidence.construction_floor)

    if "аутсорсинг персонала, не проектная разработка" in facts.triage_signals:
        decision = "Не брать"
        confidence = max(confidence, policy.confidence.outstaffing_floor)

    if "legacy/существующая ИС / модификация системы" in facts.triage_signals:
        if decision in {"Брать", "Уточнить"}:
            decision = "Не брать"
        confidence = max(confidence, policy.confidence.legacy_modification_floor)

    if any("низкий приоритет: тендер на сайт / веб-тематику" in signal for signal in facts.triage_signals):
        if decision == "Брать":
            decision = "Уточнить"
        confidence = min(confidence, policy.confidence.low_priority_cap)

    if "тяжёлый ИБ/регуляторный контур" in facts.triage_signals:
        decision = "Не брать"
        confidence = max(confidence, policy.confidence.infosec_floor)

    if "enterprise/коробочный контур" in facts.triage_signals:
        if decision in {"Брать", "Уточнить"}:
            decision = "Не брать"
        confidence = max(confidence, policy.confidence.enterprise_floor)

    if decision == "Не брать" and not _has_hard_rejection_basis(facts, policy):
        decision = "Уточнить"
        confidence = min(confidence, 70)

    if (
        decision == "Не брать"
        and len(facts.key_files) <= 1
        and (
            facts.completeness_label == "низкая"
            or (facts.completeness_label == "средняя" and not facts.triage_signals)
        )
    ):
        decision = "Уточнить"
        confidence = min(confidence, 60)

    if any("сильное совпадение стека:" in signal for signal in facts.triage_signals):
        if decision == "Уточнить":
            confidence = max(confidence, policy.confidence.strong_stack_floor)

    # Do not allow high confidence when key commercial terms are absent
    # from the available document set.
    if decision != "Не брать" and not facts.document_roles.get("договор") and not facts.payment:
        confidence = min(confidence, 70)

    output = AnalysisPayload(
        decision=decision,
        confidence_percent=max(0, min(100, confidence)),
        summary_points=build_canonical_summary_points(facts, decision),
        analysis_markdown=payload.analysis_markdown,
        completeness_label=facts.completeness_label,
        facts=facts,
        llm_raw_text=payload.llm_raw_text,
        error_type=payload.error_type,
        extraction_report=payload.extraction_report,
        llm_findings=payload.llm_findings,
    )
    output = apply_quality_gates(output, facts)
    output.summary_points = merge_verified_findings_into_summary(output.summary_points, output.llm_findings)
    facts.reason_codes = derive_reason_codes(output, facts)
    return output


def derive_confidence(
    payload: AnalysisPayload,
    facts: ExtractedFacts,
    policy: TriagePolicy | None = None,
) -> int:
    policy = policy or load_triage_policy()
    score = policy.confidence.base_score
    if facts.procurement_type != "не удалось определить":
        score += policy.confidence.procurement_type_bonus
    if facts.document_roles.get("тз"):
        score += policy.confidence.tz_bonus
    if facts.document_roles.get("извещение"):
        score += policy.confidence.notice_bonus
    if facts.document_roles.get("требования_к_участнику") or facts.project_requirements or facts.turnover_requirements:
        score += policy.confidence.participant_requirements_bonus
    if facts.document_roles.get("договор") or facts.payment:
        score += policy.confidence.contract_or_payment_bonus
    if facts.stack:
        score += policy.confidence.stack_bonus
    if facts.completeness_label == "высокая":
        score += policy.confidence.completeness_high_bonus
    elif facts.completeness_label == "низкая":
        score -= policy.confidence.completeness_low_penalty
    if facts.triage_signals:
        score += policy.confidence.triage_bonus
    try:
        llm_conf = int(payload.confidence_percent)
    except Exception:
        llm_conf = 0
    if llm_conf and policy.confidence.llm_blend:
        score = round((score + llm_conf) / 2)
    return max(policy.confidence.min_score, min(policy.confidence.max_score, score))


def _ru_normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("ё", "е"))


def _is_integration_only_enterprise_token(token: str, lower_text: str, development_context: bool) -> bool:
    if not development_context:
        return False
    if token not in {"1с", "crm", "erp", "mdm", "mes", "wms", "datareon", "esb"}:
        return False
    integration_markers = (
        "интеграц",
        "интерфейс",
        "api",
        "обмен данн",
        "взаимодейств",
        "шина данн",
        "rest",
    )
    for match in re.finditer(re.escape(token), lower_text):
        start = max(0, match.start() - 120)
        end = min(len(lower_text), match.end() + 120)
        window = lower_text[start:end]
        if any(marker in window for marker in integration_markers):
            continue
        return False
    return False


def _is_integration_only_non_core_stack(stack: list[str], lower_text: str, development_context: bool) -> bool:
    if not development_context:
        return False
    normalized_stack = set(stack)
    if "1С" not in normalized_stack:
        return False
    if normalized_stack <= {"1С", "API", "REST API", "React", "Python", "PostgreSQL"}:
        return True
    if normalized_stack not in ({"1С"}, {"1С", "API"}, {"1С", "REST API"}, {"1С", "API", "REST API"}):
        return False
    return _is_integration_only_enterprise_token("1с", lower_text, development_context)


def _detect_legacy_modification(lower_text: str, policy: TriagePolicy) -> bool:
    has_modification = any(token in lower_text for token in policy.legacy_modification_tokens)
    if not has_modification:
        return False
    has_system_context = any(token in lower_text for token in policy.legacy_system_tokens)
    return has_system_context


def _detect_integration_only(lower_text: str) -> bool:
    connect_tokens = ("подключени", "подключить", "интеграц", "интеграцион")
    dev_tokens = ("разработ", "создан", "модификац", "доработк", "внедрен")
    if not any(token in lower_text for token in connect_tokens):
        return False
    capability_patterns = (
        r"иметь[^.\n]{0,80}интеграц",
        r"продвинут[а-я ]{0,30}интеграц",
        r"интеграц[а-я ]{0,20}с онлайн-панель",
        r"интеграц[а-я ]{0,20}[cс]\s*figma",
    )
    system_pattern = re.compile(
        r"\bис\b|информационн[а-я ]{0,30}систем|существующ[а-я ]{0,40}систем|корпоративн[а-я ]{0,30}систем"
    )
    for token in connect_tokens:
        for match in re.finditer(token, lower_text):
            window = lower_text[max(0, match.start() - 180) : match.end() + 220]
            if any(re.search(pattern, window) for pattern in capability_patterns):
                continue
            if not system_pattern.search(window):
                continue
            if any(dev_token in window for dev_token in dev_tokens):
                continue
            return True
    return False


def _detect_outstaffing_by_rate(lower_text: str) -> bool:
    hourly_tokens = (
        "человеко-час",
        "чел.-час",
        "ч/ч",
        "часовая ставка",
        "ставка в час",
        "почасов",
        "rate card",
        "time and materials",
        "t&m",
        "time&material",
        "tm",
    )
    role_tokens = (
        "разработчик",
        "программист",
        "аналитик",
        "тестировщик",
        "qa",
        "devops",
        "архитектор",
        "инженер",
        "дизайнер",
        "frontend",
        "backend",
        "fullstack",
        "специалист",
    )
    if not any(token in lower_text for token in hourly_tokens):
        return False
    if not any(token in lower_text for token in role_tokens):
        return False
    return True


def _has_hard_rejection_basis(facts: ExtractedFacts, policy: TriagePolicy) -> bool:
    hard_signals = {
        "непрофильный тип закупки: строительство/монтаж",
        "аутсорсинг персонала, не проектная разработка",
        "legacy/существующая ИС / модификация системы",
        "тяжёлый ИБ/регуляторный контур",
        "enterprise/коробочный контур",
    }
    if any(signal in hard_signals for signal in facts.triage_signals):
        return True
    if facts.procurement_type in policy.non_profile_procurement_types:
        return True
    return False
