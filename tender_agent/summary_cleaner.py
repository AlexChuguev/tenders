from __future__ import annotations

import re

from tender_agent.analysis_types import ExtractedFacts


def sanitize_summary_points(points: list[str], facts: ExtractedFacts, decision: str) -> list[str]:
    cleaned: list[str] = []
    for index, point in enumerate(points, start=1):
        text = strip_leading_numbering(point).strip()
        text = normalize_summary_wording(text, index)
        text = remove_neutral_phrases(text, index)
        text = compress_repeated_clauses(text)
        if _is_label_only(text):
            text = ""
        if not text:
            text = fallback_summary_text(index, facts, decision)
        cleaned.append(f"{index}. {text}")
    while len(cleaned) < 5:
        idx = len(cleaned) + 1
        cleaned.append(f"{idx}. {fallback_summary_text(idx, facts, decision)}")
    return cleaned[:5]


def normalize_summary_wording(text: str, index: int) -> str:
    normalized = text
    replacements = [
        (r"\bвысокая\s+полнота\s+требований\s+по\s+иб\b", "жёсткий ИБ-контур"),
        (r"\bвысокий\s+уровень\s+требований\s+по\s+иб\b", "жёсткий ИБ-контур"),
        (r"\bвысокий\s+объ[её]м\s+иб\s+требований\b", "жёсткий ИБ-контур"),
        (r"\bвысокая\s+комплексность\s+иб\b", "жёсткий ИБ-контур"),
        (r"\bтребовани\w*\s+по\s+иб\b", "жёсткий ИБ-контур"),
        (r"\bдетальн\w*\s+ж[её]стк\w*\s+иб-контур\b", "жёсткий ИБ-контур"),
        (r"\bтребование\s+сро\b", "требуется СРО"),
        (r"\bчленство\s+в\s+сро\b", "требуется СРО"),
        (r"\bобязательн\w*\s+сро\b", "требуется СРО"),
        (r"\bсро\s+обязательн\w*\b", "требуется СРО"),
        (r"\bтребуется\s+наличие\s+сро\b", "требуется СРО"),
        (r"\bналичие\s+сро\b", "требуется СРО"),
        (r"\bусловия\s+уточняются\s+в\s+приложени[ия]\s*№?\s*1\b", "условия в Приложении №1"),
        (r"\bдетали\s+в\s+приложени[ия]\s*№?\s*1\s*\(?(?:не\s+предоставлен\w*|не\s+раскрыт\w*\s+в\s+явном\s+виде)\)?\b", "нужно проверять по Приложению №1"),
        (r"\bдетали\s+оплаты/обеспечения\s+не\s+раскрыты\b", "детали оплаты и обеспечения в доступных документах не раскрыты"),
        (r"\bтребования\s+по\s+опыту\s+и\s+финансам\s+не\s+указаны\s+в\s+явном\s+виде\b", "требования по опыту и финансам требуют проверки"),
        (r"\bнеясны\s+отдельные\s+детали\s+оплаты\b", "нужна проверка условий оплаты"),
        (r"\bнет\s+цены/договора\b", "в доступных документах нет условий договора и оплаты"),
        (r"\bнет\s+явных\s+условий\s+оплаты\b", "в доступных документах нет условий оплаты"),
        (r"\bусловия\s+не\s+указаны\b", "в доступных документах не раскрыты"),
        (r"\bне\s+раскрыты\s+финусловия\b", "ключевые финусловия вынесены в Приложение №1"),
        (r"\bдругие\s+требования\s+не\s+раскрыты\b", "требования по опыту и финансам требуют проверки"),
        (r"\bнмцк\s+не\s+указан[ао]?\b", "НМЦК нужно проверять по Приложению №1"),
        (r"\bотсутствуют\s+сведения\s+о\s+порядке\s+оплаты\s+и\s+банковской\s+гарантии\b", "в доступных документах не раскрыты условия оплаты и обеспечения"),
        (r"\bнет\s+явных\s+условий\s+оплаты\s+и\s+требований\s+к\s+обеспечению\b", "в доступных документах не раскрыты условия оплаты и обеспечения"),
        (r"\bпорядок\s+и\s+сроки\s+не\s+указаны\b", "в доступных документах не раскрыты условия оплаты"),
        (r"\bподробн\w*\s+требовани\w*\s+по\s+обороту/кейсам\s+не\s+обнаружен\w*\b", "требования по опыту и финансам требуют проверки"),
        (r"\bтребования\s+по\s+обороту,\s*кейсам\s+не\s+указаны\b", "требования по опыту и финансам требуют проверки"),
        (r"\bпо\s+обороту\s+и\s+кейсам\s+не\s+раскрыт\w*\b", "требования по опыту и финансам требуют проверки"),
        (r"\bиные\s+критерии\s+не\s+раскрыты\b", "требования по опыту и финансам требуют проверки"),
        (r"\bполный\s+перечень\s+не\s+указан\b", "полный перечень документов не раскрыт"),
        (r"\bподробн\w*\s+пакет\b", "часть требований вынесена в Приложение №1"),
        (r"\bусловий\s+оплаты\s+и\s+гарантий\s+нет\b", "в доступных документах не раскрыты условия оплаты и обеспечения"),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    if index == 2:
        normalized = re.sub(
            r"\bpython/react/flutter\s+не\s+указаны\b",
            "прямого совпадения с Python/React/Flutter нет",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bpython,\s*react,\s*flutter\s+явно\s+не\s+указаны\b",
            "прямого совпадения с Python/React/Flutter нет",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bpython\s+и\s+flutter\s+не\s+указаны\b",
            "прямого совпадения с Python/Flutter нет",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bне\s+указано\s+в\s+документах\b",
            "явный стек не извлечён",
            normalized,
            flags=re.IGNORECASE,
        )
    if index == 4:
        normalized = re.sub(
            r"\bполный\s+пакет\s+по\s+Приложению\s+№1,\s*объ[её]м\s+высокий\b",
            "полный состав заявки вынесен в Приложение №1",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bобъ[её]мный\s+пакет,\s*список\s+не\s+полон,\s*основная\s+часть\s+в\s+Приложении\s+№1\b",
            "полный состав заявки вынесен в Приложение №1",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bвозможн\w*,\s*подробн\w*\s+пакет\b",
            "объём документов требует уточнения по полному комплекту",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bдетали\s+в\s+приложени[ия]\s*№?\s*1\b",
            "часть требований вынесена в Приложение №1",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bполный\s+перечень\s+документов\s+не\s+раскрыт\b",
            "часть требований вынесена в недоступные приложения",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bзначительн\w*\s*\(?(?:средний/высокий|средний или высокий)\)?\s*объ[её]м\b",
            "объём документов высокий",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"часть\s+требований\s+вынесена\s+в\s+Приложение\s+№1,\s*нужно\s+проверять\s+по\s+Приложению\s+№1\)?",
            "часть требований вынесена в Приложение №1",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bне\s+указано\s+в\s+документах\b",
            "комплект документов требует проверки",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bпакет\s+документов\s+не\s+указан\b",
            "комплект документов требует проверки",
            normalized,
            flags=re.IGNORECASE,
        )
    if index == 5:
        normalized = re.sub(
            r"в\s+доступных\s+документах\s+не\s+раскрыты,\s*нужно\s+проверять\s+по\s+Приложению\s+№1\)?",
            "в доступных документах не раскрыты; нужно проверять по Приложению №1",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bне\s+указано\s+в\s+документах\b",
            "в доступных документах не раскрыты условия оплаты и обеспечения",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bне\s+указаны?\s+условия\s+оплаты\b",
            "в доступных документах не раскрыты условия оплаты",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bне\s+указаны?\s+условия\s+оплаты\s+и\s+обеспечения\b",
            "в доступных документах не раскрыты условия оплаты и обеспечения",
            normalized,
            flags=re.IGNORECASE,
        )
    if index == 3:
        normalized = re.sub(
            r"\bне\s+указано\s+в\s+документах\b",
            "требования к контрагенту требуют проверки",
            normalized,
            flags=re.IGNORECASE,
        )
        normalized = re.sub(
            r"\bне\s+указаны?\s+требования\s+по\s+обороту,\s*не\s+указаны?\s+требования\s+к\s+аналогичным\s+проектам\b",
            "требования по опыту и финансам требуют проверки",
            normalized,
            flags=re.IGNORECASE,
        )
    normalized = re.sub(r"\b(?:возможно|вероятно|скорее всего|по всей видимости)\b[, ]*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\b(?:явно|в\s+явном\s+виде)\b[, ]*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s{2,}", " ", normalized).strip(" ,;.-)")
    return normalized


def strip_leading_numbering(value: str) -> str:
    cleaned = value.strip()
    while True:
        updated = re.sub(r"^\s*\d+\s*[\.\)]\s*", "", cleaned)
        if updated == cleaned:
            return cleaned
        cleaned = updated.strip()


def remove_neutral_phrases(text: str, index: int) -> str:
    normalized = text
    patterns = [
        r"\bтребовани[яй]\s+по\s+обороту\s+отсутств\w*\b",
        r"\bтребовани[яй]\s+по\s+обороту\s+нет\b",
        r"\bданн\w*\s+по\s+обороту\s+и\s+кейсам\s+нет\b",
        r"\bнет\s+требовани[йя]\s+по\s+обороту\s+и\s+кейсам\b",
        r"\bиных\s+ограничени[йя]\s+по\s+обороту\s+и\s+опыту\s+нет\b",
        r"\bограничени[йя]\s+по\s+обороту\s+и\s+опыту\s+нет\b",
        r"\bтребования?\s+по\s+опыту\s+не\s+указан\w*\b",
        r"\bдругие\s+технологии\s+не\s+указан\w*\b",
        r"\bбанковск\w*\s+гаранти\w*\s+не\s+(?:требуетс\w*|предусмотрен\w*)\b",
        r"\bобеспечени[еия]\s+заяв[а-я]+\s+не\s+требуетс\w*\b",
        r"\bобеспечени\w*\s+не\s+указан\w*\b",
        r"\bобеспечени\w*\s+не\s+требуетс\w*\b",
        r"\bтребовани\w*\s+к\s+обеспечени\w*\s+нет\b",
        r"\bнет\s+требовани[йя]\s+к\s+квалификац\w*\s+или\s+лиценз\w*\b",
        r"\bлицензи[ияй]/сро\s+не\s+запрошен\w*\b",
        r"\bне\s+обнаружен[ыо]?\s+специальн\w*\s+требовани\w*\b",
    ]
    if index == 3:
        patterns.append(r"\bнет\s+требовани[йя]\s+по\s+обороту\b")
    for pattern in patterns:
        normalized = re.sub(pattern, "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*;\s*;", "; ", normalized)
    normalized = re.sub(r"\s{2,}", " ", normalized)
    normalized = normalized.strip(" ,;.-")
    return normalized


def compress_repeated_clauses(text: str) -> str:
    parts = [part.strip(" ,;.-") for part in re.split(r"[;|]", text) if part.strip(" ,;.-")]
    deduped = []
    seen = set()
    for part in parts:
        key = _dedupe_key(part)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(part)
    return "; ".join(deduped).strip()


def fallback_summary_text(index: int, facts: ExtractedFacts, decision: str) -> str:
    if index == 1:
        if facts.triage_signals:
            return facts.triage_signals[0]
        return "ключевые риски требуют ручной проверки"
    if index == 2:
        if facts.procurement_type in {"обучение / образовательные услуги", "сайт / маркетинг / контент", "поддержка / сопровождение"}:
            return "Стек: не разработка ПО / стек несущественен"
        return f"Стек: {', '.join(facts.stack)}" if facts.stack else "Стек: не указано в документах"
    if index == 3:
        reqs = (
            facts.participant_requirements.turnover_texts[:1]
            + facts.participant_requirements.experience_texts[:1]
            + facts.participant_requirements.required_roles[:1]
            + facts.participant_requirements.licenses_or_certs[:1]
            + facts.turnover_requirements[:1]
            + facts.project_requirements[:1]
            + facts.licenses[:1]
        )
        if reqs:
            return f"Требования к контрагенту: {'; '.join(reqs)}"
        return "Требования к контрагенту: существенные требования не выявлены"
    if index == 4:
        return f"Документация: полнота {facts.completeness_label}"
    return "Оплата и обеспечение: условия требуют уточнения"


def _ru_normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold().replace("ё", "е"))


def _dedupe_key(value: str) -> str:
    stripped = re.sub(r"^[^:]{1,40}:\s*", "", value.strip())
    return _ru_normalize(stripped)


def _is_label_only(value: str) -> bool:
    normalized = value.strip()
    return bool(
        re.fullmatch(
            r"(риски|стек|требования к контрагенту|документация|оплата и обеспечение)\s*:?",
            normalized,
            flags=re.IGNORECASE,
        )
    )
